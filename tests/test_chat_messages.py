"""Chat regressions using real NoneBot dispatch and isolated local data."""

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot
from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.exception import StopPropagation
from nonebot.message import _check_matcher, _run_matcher, handle_event


class ChatMessageTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        environment = patch.dict(os.environ, {
            'DASHSCOPE_API_KEY': 'test-key',
            'QWEN_VL_API_KEY': '',
            'ADMIN_UID': '42',
            'WHITE_LIST_FILE': str(root / 'groups.json'),
            'LONG_TERM_MEMORY_FILE': str(root / 'memory.jsonl'),
            'VECTOR_MEMORY_FILE': str(root / 'vectors.jsonl'),
            'USAGE_TELEMETRY_FILE': str(root / 'usage.jsonl'),
            'TTS_TELEMETRY_FILE': str(root / 'tts.jsonl'),
            'VECTOR_MEMORY_ENABLED': 'false',
            'VECTOR_MEMORY_AUTO_INDEX_ENABLED': 'false',
            'PROACTIVE_CHAT_ENABLED': 'false',
        })
        environment.start()
        cls.addClassCleanup(environment.stop)
        previous = Path.cwd()
        try:
            # Startup must not read the real .env or create a real sticker file.
            os.chdir(root)
            nonebot.init()
            with patch('openai.AsyncOpenAI'):
                cls.chat = importlib.import_module('plugins.pardo_chat')
                cls.memory = importlib.import_module('plugins.memory')
                cls.store = importlib.import_module('plugins.memory_store')
                cls.monitor = importlib.import_module('plugins.proactive_monitor')
                cls.stickers = importlib.import_module('plugins.sticker_service')
        finally:
            os.chdir(previous)
        cls.bot = Bot(Adapter(nonebot.get_driver()), '1')

    def setUp(self):
        self.memory._history_cache.clear()
        self.monitor.last_active_time.clear()
        self.chat.active_groups.clear()
        self.chat.active_groups.add(123)
        Path(self.chat.WHITE_LIST_FILE).write_text('[123]', encoding='utf-8')
        self.store.memory_file_path().unlink(missing_ok=True)

    def event(self, text, *, private=False, user_id=42, to_me=False, subtype=None):
        data = dict(
            time=1, self_id=1, post_type='message', user_id=user_id,
            message_id=4, raw_message=text, font=0, to_me=to_me,
            message=[dict(type='text', data=dict(text=text))],
            original_message=[dict(type='text', data=dict(text=text))],
            sender=dict(user_id=user_id, nickname='测试用户'),
        )
        if private:
            return PrivateMessageEvent(**data, message_type='private', sub_type=subtype or 'friend')
        return GroupMessageEvent(**data, group_id=123, message_type='group', sub_type='normal')

    async def run_private(self, event):
        with self.assertRaises(StopPropagation):
            await _run_matcher(self.chat.private_chat, self.bot, event, {})

    async def test_private_matcher_rejects_group_and_accepts_private_subtypes(self):
        self.assertFalse(await _check_matcher(self.chat.private_chat, self.bot, self.event('普通群消息'), {}))
        for subtype in ('friend', 'group', 'other'):
            with self.subTest(subtype=subtype):
                self.assertTrue(await _check_matcher(
                    self.chat.private_chat, self.bot,
                    self.event('私聊', private=True, subtype=subtype), {},
                ))

    async def test_bot_and_sticker_groups_refresh_and_preserve_last_valid_state(self):
        path = Path(self.chat.WHITE_LIST_FILE)
        self.assertTrue(self.chat.is_group_active(123))
        path.write_text('[456]', encoding='utf-8')
        self.assertFalse(self.chat.is_group_active(123))
        self.assertEqual(self.chat.get_active_group_ids(), {456})
        self.assertEqual(self.stickers.load_active_groups(), {456})
        path.write_text('invalid json', encoding='utf-8')
        with patch.object(self.chat.logger, 'exception'), patch.object(self.stickers.logger, 'exception'):
            self.assertEqual(self.chat.get_active_group_ids(), {456})
            self.assertEqual(self.stickers.load_active_groups(), {456})

    async def test_activation_write_failure_is_reported_without_enabling_group(self):
        event = self.event(self.chat.ACTIVATE_COMMAND)
        event.group_id = 456
        with patch.object(self.chat, 'update_active_groups', side_effect=OSError('disk failed')), patch.object(
            Bot, 'send', new_callable=AsyncMock,
        ) as send, patch.object(self.chat.logger, 'exception'):
            await _run_matcher(self.chat.mimic_chat, self.bot, event, {})
        self.assertIn('启用失败', str(send.await_args.kwargs['message']))
        self.assertFalse(self.chat.is_group_active(456))
        self.assertEqual(Path(self.chat.WHITE_LIST_FILE).read_text(encoding='utf-8'), '[123]')

    async def test_group_dispatch_reaches_activity_and_sticker_listeners(self):
        with patch.object(self.monitor, 'PROACTIVE_CHAT_ENABLED', True), patch.object(
            self.stickers, 'should_collect_from_event', return_value=False,
        ) as collect, patch.object(self.chat, 'generate_pardo_reply', new_callable=AsyncMock) as generate:
            await handle_event(self.bot, self.event('普通群消息'))
        self.assertIn(123, self.monitor.last_active_time)
        collect.assert_called_once()
        generate.assert_not_awaited()
        self.assertIn('普通群消息', self.memory.get_history_str(123))

    async def test_corrupt_proactive_usage_skips_sending_without_stopping_monitor(self):
        self.monitor.mark_group_active(123)
        with patch.object(self.monitor, 'PROACTIVE_SILENCE_MINUTES', -1), patch.object(
            self.monitor, 'proactive_probability', return_value=1.0,
        ), patch.object(self.monitor, 'get_proactive_daily_count', side_effect=ValueError('invalid count')), patch.object(
            self.monitor, 'get_bot',
        ) as get_bot, patch.object(self.monitor.logger, 'exception') as error:
            await self.monitor.check_silence()
        get_bot.assert_not_called()
        error.assert_called_once()

    async def test_private_history_contains_both_sides_without_duplicate_records(self):
        histories = []

        async def reply(session_id, text):
            histories.append(self.memory.get_history_str(session_id))
            return '机器人回答'

        with patch.object(self.chat, 'generate_pardo_reply', side_effect=reply), patch.object(
            Bot, 'send', new_callable=AsyncMock,
        ) as send:
            await self.run_private(self.event('第一轮问题', private=True))
            await self.run_private(self.event('第二轮追问', private=True))
        self.assertIn('第一轮问题', histories[0])
        for text in ('第一轮问题', '机器人回答', '第二轮追问'):
            self.assertIn(text, histories[1])
        records = list(self.store.iter_memory_records())
        self.assertEqual([r['role'] for r in records], ['user', 'bot', 'user', 'bot'])
        self.assertEqual(send.await_count, 2)
        self.assertNotIn('第一轮问题', self.memory.get_history_str(123))

    async def test_unauthorized_or_empty_private_messages_do_not_enter_memory(self):
        with patch.object(self.chat, 'generate_pardo_reply', new_callable=AsyncMock) as generate, patch.object(
            Bot, 'send', new_callable=AsyncMock,
        ) as send:
            await self.run_private(self.event('无权限', private=True, user_id=99))
            await self.run_private(self.event('   ', private=True))
        generate.assert_not_awaited()
        send.assert_not_awaited()
        self.assertEqual(list(self.store.iter_memory_records() or []), [])
        self.assertFalse(self.memory._history_cache)

    async def test_private_input_is_sanitized_in_both_memory_stores(self):
        with patch.object(self.chat, 'generate_pardo_reply', new_callable=AsyncMock, return_value='回答'), patch.object(
            Bot, 'send', new_callable=AsyncMock,
        ):
            await self.run_private(self.event('操作说明', private=True))
        expected = self.memory.sanitize_reply('操作说明')
        self.assertIn(expected, self.memory.get_history_str(42))
        self.assertEqual(next(self.store.iter_memory_records())['content'], expected)

    async def test_sticker_match_also_delivers_and_records_answer(self):
        with patch.object(self.chat, 'generate_pardo_reply', new_callable=AsyncMock, return_value='完整回答'), patch.object(
            self.chat, 'smart_send', new_callable=AsyncMock, return_value=True,
        ) as sticker, patch.object(Bot, 'send', new_callable=AsyncMock) as send:
            await _run_matcher(self.chat.mimic_chat, self.bot, self.event('帕朵，帮我回答问题'), {})
        sticker.assert_awaited_once()
        send.assert_awaited_once()
        self.assertEqual(str(send.await_args.kwargs['message']), '完整回答')
        self.assertEqual([r['content'] for r in self.store.iter_memory_records()], ['完整回答'])

    async def test_no_sticker_keeps_text_and_voice_fallback_delivery(self):
        for text in ('帕朵，帮我回答问题', '帕朵，语音回答'):
            with self.subTest(text=text):
                self.setUp()
                with patch.object(self.chat, 'generate_pardo_reply', new_callable=AsyncMock, return_value='完整回答'), patch.object(
                    self.chat, 'smart_send', new_callable=AsyncMock, return_value=False,
                ), patch.object(self.chat, 'get_tts_audio', new_callable=AsyncMock, return_value=None), patch.object(
                    Bot, 'send', new_callable=AsyncMock,
                ) as send:
                    await _run_matcher(self.chat.mimic_chat, self.bot, self.event(text), {})
                send.assert_awaited_once()
                self.assertEqual(str(send.await_args.kwargs['message']), '完整回答')
                self.assertEqual([r['content'] for r in self.store.iter_memory_records()], ['完整回答'])

    async def test_failed_text_delivery_is_not_recorded_as_sent(self):
        with patch.object(self.chat, 'generate_pardo_reply', new_callable=AsyncMock, return_value='完整回答'), patch.object(
            self.chat, 'smart_send', new_callable=AsyncMock, return_value=True,
        ), patch.object(Bot, 'send', new_callable=AsyncMock, side_effect=RuntimeError('delivery failed')), patch.object(
            self.chat.logger, 'exception',
        ) as error:
            await _run_matcher(self.chat.mimic_chat, self.bot, self.event('帕朵，帮我回答问题'), {})
        error.assert_called_once()
        self.assertEqual(list(self.store.iter_memory_records() or []), [])
        self.assertFalse(self.memory._history_cache)


if __name__ == '__main__':
    unittest.main()
