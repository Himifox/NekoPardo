"""Admin regressions; all settings and records use temporary files or stubs."""

import ast
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


class AdminWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        stubs = {}
        for name in ('plugins.config', 'plugins.memory_store', 'plugins.tts',
                     'plugins.usage_telemetry', 'plugins.vector_memory', 'plugins.memory',
                     'plugins.budget_guard',
                     'start', 'openai'):
            stubs[name] = types.ModuleType(name)
        stubs['plugins.config'].API_KEY = 'isolated-test-key'
        stubs['plugins.config'].BASE_URL = 'https://example.invalid'
        stubs['plugins.config'].MODEL_NAME = 'test-model'
        stubs['plugins.config'].WHITE_LIST_FILE = 'groups.json'
        stubs['plugins.budget_guard'].BudgetExceededError = type('BudgetExceededError', (RuntimeError,), {})
        stubs['plugins.usage_telemetry'].request_chat_completion = AsyncMock()
        exports = {
            'plugins.memory_store': ('count_memory_records', 'iter_memory_records'),
            'plugins.tts': ('get_tts_usage_summary',),
            'plugins.usage_telemetry': ('get_model_usage_summary', 'telemetry_path'),
            'plugins.vector_memory': ('get_vector_memory_stats', 'build_vector_memory_index'),
            'plugins.memory': ('sanitize_reply',),
            'start': ('load_dotenv',),
            'openai': ('AsyncOpenAI',),
        }
        for module, names in exports.items():
            for name in names:
                setattr(stubs[module], name, MagicMock())
        spec = importlib.util.spec_from_file_location('isolated_admin_web', root / 'admin_web.py')
        cls.admin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {**stubs, spec.name: cls.admin}):
            spec.loader.exec_module(cls.admin)
        cls.persona_path = cls.admin.PLUGIN_FILE

    def setUp(self):
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
        path = Path(__file__).resolve().parents[1] / 'virtualme' / 'chat' / 'persona.py'
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

    @unittest.skipUnless(shutil.which('node'), 'Node.js required for frontend rendering check')
    def test_memory_metadata_and_content_are_html_escaped(self):
        html = self.admin._HTML
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
