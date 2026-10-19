"""Architectural boundaries and resource lifetime regressions."""
import ast
import asyncio
from pathlib import Path
import unittest
import os
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]

class ArchitectureTests(unittest.TestCase):
    def test_bot_boot_loads_every_required_adapter_without_connecting(self):
        script = '''
import runpy, sys
import nonebot
runpy.run_path(sys.argv[1], run_name='assembly_smoke')
expected = {'events', 'commands', 'stickers', 'chat', 'proactive', 'startup'}
loaded = {plugin.module_name.rsplit('.', 1)[-1] for plugin in nonebot.get_loaded_plugins()}
assert expected <= loaded, (expected, loaded)
'''
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, PYTHONPATH=str(ROOT),
                               WHITE_LIST_FILE=str(Path(directory) / 'groups.json'),
                               PROACTIVE_CHAT_ENABLED='false')
            result = subprocess.run([sys.executable, '-c', script, str(ROOT / 'bot.py')],
                                    cwd=directory, env=environment, capture_output=True,
                                    text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_services_do_not_import_plugins_or_messaging_framework(self):
        for path in (ROOT / 'virtualme').rglob('*.py'):
            if 'adapters' in path.relative_to(ROOT / 'virtualme').parts:
                continue
            with self.subTest(module=path.relative_to(ROOT)):
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    imports = [node.module or ''] if isinstance(node, ast.ImportFrom) else (
                        [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                    self.assertFalse(any(name.split('.')[0] in ('plugins', 'nonebot', 'fastapi')
                                         or name.startswith('virtualme.adapters') for name in imports), imports)

    def test_qq_plugins_are_explicit_and_packages_have_no_registration(self):
        tree = ast.parse((ROOT / 'bot.py').read_text(encoding='utf-8'))
        self.assertNotIn('load_plugins', [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)])
        for path in (ROOT / 'virtualme/adapters').rglob('__init__.py'):
            self.assertFalse(any(isinstance(node, ast.Call) for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_owned_tasks_before_closing_client(self):
        from virtualme import runtime
        calls = []
        started = asyncio.Event()

        async def worker():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                calls.append('cancelled')

        task = runtime.start_task(worker(), name='shutdown-test')
        await started.wait()
        with patch('virtualme.llm.client.close_client', new_callable=AsyncMock,
                   side_effect=lambda: calls.append('closed')):
            await runtime.shutdown()
        self.assertTrue(task.cancelled())
        self.assertEqual(calls, ['cancelled', 'closed'])
        self.assertFalse(runtime._tasks)

    async def test_failed_task_is_observed_and_removed(self):
        from virtualme import runtime
        async def fail():
            raise ValueError('worker failed')
        with patch.object(runtime.logger, 'error') as log:
            task = runtime.start_task(fail(), name='failing-worker')
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
        log.assert_called_once()
        self.assertNotIn(task, runtime._tasks)
