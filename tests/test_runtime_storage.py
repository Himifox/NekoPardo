"""Storage regressions using synthetic events and temporary files only."""

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot


class RuntimeStorageTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path.cwd()
            try:
                os.chdir(temporary)
                nonebot.init()
                cls.stickers = importlib.import_module('plugins.sticker_service')
                cls.proactive = importlib.import_module('plugins.proactive_usage')
            finally:
                os.chdir(previous)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.collection = self.root / 'stickers.json'
        self.collection.write_text('{}', encoding='utf-8')
        self.usage = self.root / 'proactive.json'
        for module, name, value in (
            (self.stickers, 'COLLECTION_JSON', str(self.collection)),
            (self.stickers, 'COLLECTION_DIR', str(self.root / 'images')),
            (self.proactive, 'PROACTIVE_USAGE_FILE', str(self.usage)),
        ):
            context = patch.object(module, name, value)
            context.start()
            self.addCleanup(context.stop)

    def event(self, identifier, url):
        segment = SimpleNamespace(type='image', data={'md5': identifier, 'url': url})
        return SimpleNamespace(get_message=lambda: [segment])

    async def test_concurrent_recognition_preserves_both_new_stickers(self):
        ready = asyncio.Event()
        calls = 0

        async def recognize(url):
            nonlocal calls
            calls += 1
            if calls == 2:
                ready.set()
            await ready.wait()
            return '关键词：开心'

        with patch.object(self.stickers, 'should_collect_from_event', return_value=True), patch.object(
            self.stickers, 'qwen_recognize_sticker', side_effect=recognize,
        ), patch.object(self.stickers, 'download_sticker_image', new_callable=AsyncMock):
            await asyncio.wait_for(asyncio.gather(
                self.stickers._(None, self.event('a', 'https://example.invalid/a')),
                self.stickers._(None, self.event('b', 'https://example.invalid/b')),
            ), timeout=5)
        self.assertEqual(set(await self.stickers.load_collection()), {'a', 'b'})

    async def test_recognition_does_not_overwrite_concurrent_url_update(self):
        self.collection.write_text(json.dumps({'old': {'meaning': '旧含义', 'url': 'old-url'}}), encoding='utf-8')
        started, release = asyncio.Event(), asyncio.Event()

        async def recognize(url):
            started.set()
            await release.wait()
            return '新含义'

        with patch.object(self.stickers, 'should_collect_from_event', return_value=True), patch.object(
            self.stickers, 'STICKER_RECOGNIZE_EXISTING', False,
        ), patch.object(self.stickers, 'qwen_recognize_sticker', side_effect=recognize), patch.object(
            self.stickers, 'download_sticker_image', new_callable=AsyncMock,
        ):
            task = asyncio.create_task(self.stickers._(None, self.event('new', 'new-url')))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                await self.stickers._(None, self.event('old', 'updated-url'))
            finally:
                release.set()
                await asyncio.wait_for(task, timeout=5)
        collection = await self.stickers.load_collection()
        self.assertEqual(collection['old'], {'meaning': '旧含义', 'url': 'updated-url'})
        self.assertEqual(collection['new']['meaning'], '新含义')

    async def test_collection_atomic_failure_preserves_previous_file(self):
        with patch.object(self.stickers.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                await self.stickers.save_collection({'new': 'value'})
        self.assertEqual(await self.stickers.load_collection(), {})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    async def test_malformed_collection_is_not_replaced(self):
        self.collection.write_text('{', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            await self.stickers.update_collection_entry('new', 'url', 'meaning')
        self.assertEqual(self.collection.read_text(encoding='utf-8'), '{')

    async def test_proactive_counter_survives_module_reload_and_resets_next_day(self):
        with patch.object(self.proactive, '_today', return_value='2026-10-10'):
            self.assertEqual(self.proactive.increment_proactive_daily_count(), 1)
            self.assertEqual(self.proactive.increment_proactive_daily_count(), 2)
        # A reload discards process state, just as a restart would.
        importlib.reload(self.proactive)
        with patch.object(self.proactive, 'PROACTIVE_USAGE_FILE', str(self.usage)), patch.object(
            self.proactive, '_today', return_value='2026-10-10',
        ):
            self.assertEqual(self.proactive.get_proactive_daily_count(), 2)
        with patch.object(self.proactive, 'PROACTIVE_USAGE_FILE', str(self.usage)), patch.object(
            self.proactive, '_today', return_value='2026-10-11',
        ):
            self.assertEqual(self.proactive.get_proactive_daily_count(), 0)
            self.assertEqual(self.proactive.increment_proactive_daily_count(), 1)

    async def test_concurrent_proactive_increments_are_not_lost(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: self.proactive.increment_proactive_daily_count(), range(20)))
        self.assertEqual(set(values), set(range(1, 21)))
        self.assertEqual(self.proactive.get_proactive_daily_count(), 20)

    async def test_corrupt_usage_is_not_reset_or_overwritten(self):
        for text in ('{', '{"date":"2026-10-10","count":-1}', '[]'):
            with self.subTest(text=text):
                self.usage.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.proactive.increment_proactive_daily_count()
                self.assertEqual(self.usage.read_text(encoding='utf-8'), text)

    async def test_usage_atomic_failure_preserves_previous_count(self):
        self.proactive.increment_proactive_daily_count()
        with patch.object(self.proactive.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.proactive.increment_proactive_daily_count()
        self.assertEqual(self.proactive.get_proactive_daily_count(), 1)
        self.assertEqual(list(self.root.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
