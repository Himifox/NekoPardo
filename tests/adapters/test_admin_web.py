"""Admin regressions; all settings and records use temporary files or stubs."""

import ast
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class AdminWebTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        stubs = {}
        for name in ('virtualme.config', 'virtualme.memory.store', 'virtualme.speech.service',
                     'virtualme.telemetry.usage', 'virtualme.memory.retrieval', 'virtualme.adapters.qq.events',
                     'virtualme.telemetry.budget',
                     'virtualme.chat.service',
                     'start', 'openai'):
            stubs[name] = types.ModuleType(name)
        stubs['virtualme.config'].PROJECT_ROOT = root
        for name in ('DAILY_CHAT_TOKEN_LIMIT', 'DAILY_EMBEDDING_TOKEN_LIMIT', 'DAILY_TTS_CHAR_LIMIT', 'PROACTIVE_DAILY_LIMIT'):
            setattr(stubs['virtualme.config'], name, 0)
        stubs['virtualme.config'].API_KEY = 'isolated-test-key'
        stubs['virtualme.config'].BASE_URL = 'https://example.invalid'
        stubs['virtualme.config'].MODEL_NAME = 'test-model'
        stubs['virtualme.config'].WHITE_LIST_FILE = 'groups.json'
        stubs['virtualme.telemetry.budget'].BudgetExceededError = type('BudgetExceededError', (RuntimeError,), {})
        stubs['virtualme.telemetry.usage'].request_chat_completion = AsyncMock()
        stubs['virtualme.chat.service'].generate_reply = AsyncMock()
        stubs['virtualme.chat.service'].close_client = AsyncMock()
        exports = {
            'virtualme.memory.store': ('count_memory_records', 'iter_memory_records'),
            'virtualme.speech.service': ('get_tts_usage_summary',),
            'virtualme.telemetry.usage': ('get_model_usage_summary', 'telemetry_path'),
            'virtualme.memory.retrieval': ('get_vector_memory_stats', 'build_vector_memory_index'),
            'virtualme.adapters.qq.events': ('sanitize_reply',),
            'start': ('load_dotenv',),
            'openai': ('AsyncOpenAI',),
        }
        for module, names in exports.items():
            for name in names:
                setattr(stubs[module], name, MagicMock())
        spec = importlib.util.spec_from_file_location('isolated_admin_web', root / 'virtualme/adapters/web/routes.py')
        cls.admin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {**stubs, spec.name: cls.admin}):
            spec.loader.exec_module(cls.admin)
        cls.persona_path = cls.admin.PLUGIN_FILE

    def setUp(self):
        self.admin._chat_history = []
        self.admin._chat_lock = asyncio.Lock()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.plugin = self.root / 'plugin.py'
        self.source = '# 注释\nOLD_SYSTEM_SETTING = "old"\nSYSTEM_SETTING = """旧人设"""\nanswer = SYSTEM_SETTING\n'
        self.plugin.write_text(self.source, encoding='utf-8')
        for name, value in (('PLUGIN_FILE', self.plugin), ('BASE_DIR', self.root),
                            ('WHITE_LIST_FILE', 'nested/groups.json')):
            context = patch.object(self.admin, name, value, create=True)
            context.start()
            self.addCleanup(context.stop)

    def setting_nodes(self):
        return ast.parse(self.plugin.read_text(encoding='utf-8')).body

    def test_real_persona_source_is_the_shared_python_constant(self):
        path = Path(__file__).resolve().parents[2] / 'virtualme' / 'chat' / 'persona.py'
        self.assertEqual(self.persona_path, path)
        with patch.object(self.admin, 'PLUGIN_FILE', path):
            content = self.admin._extract_system_setting()
        assignment = next(node for node in ast.parse(path.read_text(encoding='utf-8')).body
                          if isinstance(node, ast.Assign) and any(
                              isinstance(target, ast.Name) and target.id == 'SYSTEM_SETTING'
                              for target in node.targets))
        self.assertEqual(content, ast.literal_eval(assignment.value))

    def test_repeated_save_preserves_assignment_and_surrounding_code(self):
        for text in ('新的人设', '包含"引号"和\\路径\n第二行', ''):
            self.assertTrue(self.admin._write_system_setting(text))
            self.assertEqual(self.admin._extract_system_setting(), text)
            nodes = self.setting_nodes()
            self.assertEqual(len(nodes), 3)
            self.assertEqual(nodes[1].targets[0].id, 'SYSTEM_SETTING')
            self.assertEqual(nodes[2].value.id, 'SYSTEM_SETTING')
            self.assertEqual(nodes[0].value.value, 'old')

    def test_injection_payload_remains_string_data(self):
        payload = '"""角色"""\nraise RuntimeError("injected")\n"""尾部"""'
        self.admin.set_setting(self.admin.SystemSettingUpdate(content=payload))
        self.assertEqual(len(self.setting_nodes()), 3)
        self.assertEqual(self.admin._extract_system_setting(), payload[3:-3])

    def test_legacy_preset_delimiters_are_accepted(self):
        self.admin.set_setting(self.admin.SystemSettingUpdate(content='"""预设人设"""'))
        self.assertEqual(self.admin._extract_system_setting(), '预设人设')

    def test_save_preserves_source_line_endings_and_ignores_fake_assignment(self):
        source = '# SYSTEM_SETTING = "fake"\r\nnotes = "SYSTEM_SETTING = other"\r\nSYSTEM_SETTING = "旧人设"\r\nanswer = SYSTEM_SETTING\r\n'
        self.plugin.write_bytes(source.encode('utf-8'))
        self.admin._write_system_setting('更新')
        self.assertEqual(self.plugin.read_bytes(), source.replace('"旧人设"', repr('更新')).encode('utf-8'))

    def test_atomic_failure_keeps_original_and_removes_temporary_file(self):
        with patch.object(self.admin.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.admin._write_system_setting('new')
        self.assertEqual(self.plugin.read_text(encoding='utf-8'), self.source)
        self.assertEqual(list(self.root.iterdir()), [self.plugin])

    def test_custom_group_paths_and_concurrent_updates(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(self.admin._add_active_group, range(1, 21)))
        target = self.root / 'nested/groups.json'
        self.assertEqual(set(json.loads(target.read_text(encoding='utf-8'))), set(range(1, 21)))
        self.assertFalse((self.root / 'active_groups.json').exists())
        self.admin._remove_active_group(10)
        self.assertNotIn(10, self.admin._active_groups())
        absolute = self.root / 'absolute.json'
        with patch.object(self.admin, 'WHITE_LIST_FILE', str(absolute)):
            self.admin._add_active_group(99)
        self.assertEqual(json.loads(absolute.read_text(encoding='utf-8')), [99])

    def test_malformed_group_file_is_not_overwritten(self):
        target = self.admin._groups_path()
        target.parent.mkdir()
        for text in ('{"unexpected":1}', '{', '["invalid-id"]'):
            target.write_text(text, encoding='utf-8')
            with self.assertRaises(self.admin.HTTPException) as error:
                self.admin._add_active_group(99)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(target.read_text(encoding='utf-8'), text)

    async def test_chat_keeps_independent_history_parameters_and_latest_persona(self):
        with patch.object(self.admin, 'generate_reply', new_callable=AsyncMock,
                          side_effect=['回答甲', '回答乙']) as generate:
            first = await self.admin.chat_test(self.admin.ChatTestIn(message=' 第一轮 ', max_tokens=1, temperature=.4))
            self.admin._write_system_setting('更新人设')
            second = await self.admin.chat_test(self.admin.ChatTestIn(message='第二轮', max_tokens=2000, temperature=1.2))
        self.assertEqual(first, {'reply': '回答甲'})
        self.assertEqual(second, {'reply': '回答乙'})
        self.assertEqual(generate.await_args_list[0].args[0], [
            {'role': 'system', 'content': '旧人设'}, {'role': 'user', 'content': '第一轮'},
        ])
        self.assertEqual(generate.await_args_list[1].args[0], [
            {'role': 'system', 'content': '更新人设'}, {'role': 'user', 'content': '第一轮'},
            {'role': 'assistant', 'content': '回答甲'}, {'role': 'user', 'content': '第二轮'},
        ])
        self.assertEqual(generate.await_args_list[0].kwargs,
                         dict(temperature=.4, max_tokens=50, extra_body={'enable_thinking': False}))
        self.assertEqual(generate.await_args_list[1].kwargs,
                         dict(temperature=1.2, max_tokens=1000, extra_body={'enable_thinking': False}))

    async def test_reset_and_empty_input_do_not_call_model(self):
        self.admin._chat_history = [{'role': 'user', 'content': '旧消息'}]
        with patch.object(self.admin, 'generate_reply', new_callable=AsyncMock) as generate:
            self.assertEqual(await self.admin.chat_test(self.admin.ChatTestIn(message='', reset=True)),
                             {'reply': '', 'reset': True})
            with self.assertRaises(self.admin.HTTPException) as error:
                await self.admin.chat_test(self.admin.ChatTestIn(message='  '))
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.admin._chat_history, [])
        generate.assert_not_awaited()

    async def test_failure_budget_and_cancellation_preserve_completed_history(self):
        initial = [{'role': 'user', 'content': '旧问题'}, {'role': 'assistant', 'content': '旧回答'}]
        for error, expected in ((RuntimeError('offline'), '（调用失败：offline）'),
                                (self.admin.BudgetExceededError(), '（今日聊天预算已用完，请调整预算或明天再试）'),
                                (asyncio.CancelledError(), None)):
            with self.subTest(error=error):
                self.admin._chat_history = list(initial)
                with patch.object(self.admin, 'generate_reply', new_callable=AsyncMock, side_effect=error):
                    if expected is None:
                        with self.assertRaises(asyncio.CancelledError):
                            await self.admin.chat_test(self.admin.ChatTestIn(message='新问题'))
                    else:
                        self.assertEqual(await self.admin.chat_test(self.admin.ChatTestIn(message='新问题')),
                                         {'reply': expected})
                self.assertEqual(self.admin._chat_history, initial)

    async def test_existing_retry_policy_and_history_commit_are_preserved(self):
        initial = [{'role': 'user', 'content': '旧问题'}, {'role': 'assistant', 'content': '旧回答'}]
        for retry_error in (None, RuntimeError('blocked again'), self.admin.BudgetExceededError(), asyncio.CancelledError()):
            with self.subTest(retry_error=retry_error):
                self.admin._chat_history = list(initial)
                with patch.object(self.admin, 'generate_reply', new_callable=AsyncMock,
                                  side_effect=[RuntimeError('data_inspection_failed'), retry_error or '重试回答']) as generate:
                    if isinstance(retry_error, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            await self.admin.chat_test(self.admin.ChatTestIn(message='操作说明', temperature=.5))
                    else:
                        result = await self.admin.chat_test(self.admin.ChatTestIn(message='操作说明', temperature=.5))
                        if retry_error is None:
                            self.assertEqual(result, {'reply': '重试回答', 'softened': True})
                        elif isinstance(retry_error, self.admin.BudgetExceededError):
                            self.assertIn('预算', result['reply'])
                        else:
                            self.assertEqual(result, {'reply': '（被平台内容审核拦截，换个说法再试）'})
                retry_messages = generate.await_args_list[1].args[0]
                self.assertEqual(retry_messages[1:-1], initial)
                self.assertEqual(retry_messages[-1], {'role': 'user', 'content': self.admin._soften_message('操作说明')})
                self.assertEqual(generate.await_args_list[1].kwargs['temperature'], 1.0)
                self.assertEqual(self.admin._chat_history,
                                 initial if retry_error else retry_messages[1:] + [{'role': 'assistant', 'content': '重试回答'}])

    async def test_chat_requests_remain_serialized(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        messages = []

        async def generate(history, **parameters):
            messages.append(history)
            entered.set()
            await release.wait()
            return '回答'

        with patch.object(self.admin, 'generate_reply', side_effect=generate):
            first = asyncio.create_task(self.admin.chat_test(self.admin.ChatTestIn(message='第一轮')))
            await asyncio.wait_for(entered.wait(), timeout=2)
            second = asyncio.create_task(self.admin.chat_test(self.admin.ChatTestIn(message='第二轮')))
            await asyncio.sleep(0)
            self.assertEqual(len(messages), 1)
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        self.assertEqual(messages[1][1:3], [
            {'role': 'user', 'content': '第一轮'}, {'role': 'assistant', 'content': '回答'},
        ])

    async def test_lifespan_closes_shared_client_on_shutdown(self):
        with patch('start.load_dotenv'):
            from virtualme.adapters.web import app as application
        with patch.object(application, 'shutdown', new_callable=AsyncMock) as close:
            async with application.lifespan(application.app):
                close.assert_not_awaited()
            close.assert_awaited_once()

    @unittest.skipUnless(shutil.which('node'), 'Node.js required for frontend rendering check')
    def test_memory_metadata_and_content_are_html_escaped(self):
        html = self.admin.index()
        function = html[html.index('async function loadMemory()'):html.index('async function loadGroups()')]
        malicious = '<img src=x onerror=alert(1)>'
        script = 'const elements = new Map(); const $ = id => { if (!elements.has(id)) elements.set(id, {value:""}); return elements.get(id); };\n'
        script += 'const api = async () => (' + json.dumps({'total': 1, 'records': [{
            'role': 'user', 'sender': malicious, 'created_at': malicious, 'content': malicious,
        }]}) + ');\n'
        script += function + '\nloadMemory().then(() => console.log($("memory-box").innerHTML));'
        result = subprocess.run(['node'], input=script, text=True, encoding='utf-8',
                                capture_output=True, check=True)
        self.assertNotIn('<img', result.stdout)
        self.assertEqual(result.stdout.count('&lt;img'), 3)


if __name__ == '__main__':
    unittest.main()
