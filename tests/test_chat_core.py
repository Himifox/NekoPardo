"""Shared chat behavior, isolated from live data and external APIs."""

import asyncio
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class ChatCoreTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        environment = patch.dict(os.environ, {
            'DASHSCOPE_API_KEY': 'isolated-test-key',
            'LONG_TERM_MEMORY_FILE': str(root / 'memory.jsonl'),
            'VECTOR_MEMORY_FILE': str(root / 'vectors.jsonl'),
            'USAGE_TELEMETRY_FILE': str(root / 'usage.jsonl'),
            'VECTOR_MEMORY_ENABLED': 'false',
            'VECTOR_MEMORY_AUTO_INDEX_ENABLED': 'false',
        })
        environment.start()
        cls.addClassCleanup(environment.stop)
        before = dict(sys.modules)

        def restore_modules():
            for name in list(sys.modules):
                if name.startswith(('plugins.', 'virtualme.')) and name not in before:
                    del sys.modules[name]

        cls.addClassCleanup(restore_modules)
        # All file/SDK entry points used below are replaced before execution.
        cls.context = importlib.import_module('virtualme.chat.context')
        cls.service = importlib.import_module('virtualme.chat.service')
        cls.policy = importlib.import_module('virtualme.chat.text_policy')
        cls.session = importlib.import_module('virtualme.memory.session')

    async def test_context_preserves_prompt_layout_and_recall(self):
        c = self.context
        with patch.object(c, 'load_target_history', return_value=['样本甲', '样本乙']) as history, patch.object(
            c.random, 'sample', return_value=['样本乙', '样本甲'],
        ) as sample, patch.object(c, 'load_history_for_group', return_value='近期消息'), patch.object(
            c, 'SYSTEM_SETTING', '人设',
        ), patch.object(c, 'VECTOR_MEMORY_ENABLED', True), patch.object(
            c, 'search_vector_memory', new_callable=AsyncMock, return_value=[{'content': '记忆'}],
        ) as search, patch.object(c, 'format_recalled_memories', return_value='长期消息'):
            result = await c.build_system_content(123, '问题')
        self.assertEqual(result, '人设\n\n【当前群聊历史】\n近期消息\n\n'
                         '【相关长期记忆】\n长期消息\n\n'
                         '【用户的个人历史消息（仅供参考）】\n样本乙\n样本甲\n\n'
                         '接下来请你用帕朵的口吻回复老板的话，保持语气和人设的一致性！')
        history.assert_called_once_with(c.HISTORY_FILE_PATH, c.TARGET_UID)
        sample.assert_called_once_with(['样本甲', '样本乙'], 2)
        search.assert_awaited_once_with('问题', 123)

    async def test_recall_disabled_empty_or_failed_keeps_existing_fallback(self):
        c = self.context
        for enabled, error in ((False, None), (True, None), (True, RuntimeError('offline'))):
            with self.subTest(enabled=enabled, error=error), patch.object(
                c, 'load_target_history', return_value=[],
            ), patch.object(c, 'load_history_for_group', return_value='群聊历史记录为空。'), patch.object(
                c, 'VECTOR_MEMORY_ENABLED', enabled,
            ), patch.object(c, 'search_vector_memory', new_callable=AsyncMock,
                            return_value=[], side_effect=error) as recall, patch.object(c.logger, 'exception'):
                result = await c.build_system_content(9, '问题')
            self.assertNotIn('【相关长期记忆】', result)
            self.assertIn('【当前群聊历史】\n群聊历史记录为空。', result)
            self.assertEqual(recall.await_count, int(enabled))

    async def test_reply_preserves_sdk_parameters_and_does_not_record_delivery(self):
        s = self.service
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='  操作说明  '))])
        for overrides in ({}, {'temperature': .95, 'max_tokens': 80,
                               'frequency_penalty': .1, 'presence_penalty': .2}):
            with self.subTest(overrides=overrides), patch.object(
                s, 'build_system_content', new_callable=AsyncMock, return_value='完整上下文',
            ) as build, patch.object(s, 'get_client', return_value='client'), patch.object(
                s, 'request_chat_completion', new_callable=AsyncMock, return_value=response,
            ) as request, patch.object(s, 'save_bot_reply') as save, patch.object(
                s, 'schedule_vector_memory_auto_index',
            ) as index:
                reply = await s.generate_pardo_reply(123, '原始输入', **overrides)
            self.assertEqual(reply, self.policy.sanitize_reply('操作说明'))
            build.assert_awaited_once_with(123, '原始输入')
            expected = dict(temperature=0.85, max_tokens=100, frequency_penalty=.3, presence_penalty=.7)
            expected.update(overrides)
            request.assert_awaited_once_with(
                'client', model=s.MODEL_NAME,
                messages=[{'role': 'system', 'content': '完整上下文'}, {'role': 'user', 'content': '原始输入'}],
                stop=['用户:', 'User:'], **expected,
            )
            save.assert_not_called()
            index.assert_not_called()

    async def test_budget_reply_and_unexpected_errors_preserved(self):
        s = self.service
        with patch.object(s, 'build_system_content', new_callable=AsyncMock, return_value='上下文'), patch.object(
            s, 'get_client', return_value='client',
        ), patch.object(s, 'request_chat_completion', new_callable=AsyncMock) as request:
            request.side_effect = s.BudgetExceededError()
            self.assertEqual(await s.generate_pardo_reply(1, '问题'),
                             '老板，今天聊天预算见底啦，咱先省点小钱，晚点再聊哦。')
            request.side_effect = RuntimeError('offline')
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                await s.generate_pardo_reply(1, '问题')
            request.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await s.generate_pardo_reply(1, '问题')

    async def test_lazy_client_reuse_and_shutdown(self):
        s = self.service
        client = SimpleNamespace(close=AsyncMock())
        with patch.object(s, '_client', None), patch.object(s, 'AsyncOpenAI', return_value=client) as factory:
            self.assertIs(s.get_client(), client)
            self.assertIs(s.get_client(), client)
            factory.assert_called_once_with(api_key=s.API_KEY, base_url=s.BASE_URL)
            await s.close_client()
            await s.close_client()
            client.close.assert_awaited_once()
            self.assertIsNone(s._client)

    async def test_input_and_delivered_reply_keep_memory_rules(self):
        m = self.session
        with patch.object(m, '_history_cache', {}), patch.object(m, 'append_short_term_memory') as append, patch.object(
            m, 'save_memory_entry',
        ) as persist:
            m.record_incoming(42, '昵称', '操作说明', user_id=42, timestamp=10)
        cleaned = self.policy.sanitize_reply('操作说明')
        append.assert_called_once_with(42, '昵称', cleaned)
        persist.assert_called_once_with(content=cleaned, role='user', group_id=42,
                                        sender='昵称', user_id=42, timestamp=10)
        calls = Mock()
        with patch.object(self.service, 'save_bot_reply', calls.save), patch.object(
            self.service, 'schedule_vector_memory_auto_index', calls.index,
        ):
            self.service.record_delivered_reply(42, '回答')
        self.assertEqual([call[0] for call in calls.mock_calls], ['save', 'index'])
        calls.save.assert_called_once_with(42, '回答')

    async def test_tts_text_preserves_action_removal_and_empty_fallback(self):
        for original, expected in (('（摇尾巴）你好[动作]！', '你好！'), ('【动作】', '喵！'),
                                   (' 普通正文 ', '普通正文'), ('', '喵！')):
            with self.subTest(original=original):
                self.assertEqual(self.policy.to_tts_text(original), expected)


class CoreImportTests(unittest.TestCase):
    def test_import_needs_no_nonebot_and_creates_no_clients_or_files(self):
        root = Path(__file__).resolve().parents[1]
        script = '''
import sys
from pathlib import Path
from unittest.mock import patch
class BlockFramework:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "nonebot" or fullname.startswith("nonebot."):
            raise AssertionError("Core imported NoneBot: " + fullname)
sys.meta_path.insert(0, BlockFramework())
with patch("openai.AsyncOpenAI", side_effect=AssertionError("Client created on import")):
    import virtualme.chat.service
    import virtualme.chat.access
    import virtualme.memory.session
assert not list(Path.cwd().iterdir()), "Import wrote files"
assert "plugins.pardo_chat" not in sys.modules
assert "plugins.memory" not in sys.modules
'''
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONPATH=str(root), DASHSCOPE_API_KEY='isolated-key',
                       WHITE_LIST_FILE=str(Path(directory) / 'groups.json'))
            result = subprocess.run([sys.executable, '-c', script], cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
