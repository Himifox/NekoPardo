"""Budget regressions with synthetic clients and temporary telemetry only."""

import ast
import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        config = types.ModuleType('plugins.config')
        for filename in ('budget_guard.py', 'usage_telemetry.py', 'tts.py', 'vector_memory.py'):
            for node in ast.walk(ast.parse((ROOT / 'plugins' / filename).read_text(encoding='utf-8'))):
                if isinstance(node, ast.ImportFrom) and node.module == 'plugins.config':
                    for alias in node.names:
                        setattr(config, alias.name, 0)
        config.USAGE_TELEMETRY_FILE = str(self.root / 'usage.jsonl')
        config.TTS_TELEMETRY_FILE = str(self.root / 'tts.jsonl')
        config.EMBEDDING_API_KEY = 'synthetic-key'
        config.EMBEDDING_BASE_URL = 'https://example.invalid'
        config.EMBEDDING_MODEL = 'synthetic-model'
        store = types.ModuleType('plugins.memory_store')
        store.iter_memory_records = lambda: iter(())
        modules = patch.dict(sys.modules, {'plugins.config': config, 'plugins.memory_store': store})
        modules.start()
        self.addCleanup(modules.stop)
        self.guard = load_module('plugins.budget_guard', 'plugins/budget_guard.py')
        self.usage = load_module('plugins.usage_telemetry', 'plugins/usage_telemetry.py')
        self.tts = load_module('plugins.tts', 'plugins/tts.py')
        self.vector = load_module('plugins.vector_memory', 'plugins/vector_memory.py')

    def response(self, tokens=10, usage=True):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='reply'))],
            usage={'prompt_tokens': tokens - 2, 'completion_tokens': 2, 'total_tokens': tokens} if usage else None,
        )

    def client(self, create):
        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))

    async def chat(self, create, **kwargs):
        return await self.usage.request_chat_completion(self.client(create), model='test-model',
                                                       messages=[{'role': 'user', 'content': 'input'}],
                                                       max_tokens=5, **kwargs)

    def events(self, path=None):
        target = path or self.usage.telemetry_path()
        return [json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()] if target.exists() else []

    def admin_chat(self, create):
        # Execute the actual handler without importing admin startup or .env.
        tree = ast.parse((ROOT / 'admin_web.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == '_chat_test_locked')
        namespace = dict(ChatTestIn=types.SimpleNamespace, _chat_history=[],
                         _extract_system_setting=lambda: 'system', _chat_client=self.client(create),
                         MODEL_NAME='test-model', sanitize_reply=lambda text: text,
                         _soften_message=lambda text: text + ' softened',
                         request_chat_completion=self.usage.request_chat_completion,
                         BudgetExceededError=self.guard.BudgetExceededError, HTTPException=RuntimeError)
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'admin_web.py', 'exec'), namespace)
        return namespace, types.SimpleNamespace(message='input', reset=False, max_tokens=50, temperature=1.0)

    async def test_admin_chat_uses_shared_budget_and_records_usage(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 70
        create = AsyncMock(return_value=self.response(tokens=70))
        namespace, payload = self.admin_chat(create)
        self.assertEqual(await namespace['_chat_test_locked'](payload), {'reply': 'reply'})
        result = await namespace['_chat_test_locked'](payload)
        self.assertIn('预算', result['reply'])
        self.assertEqual(create.await_count, 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(namespace['_chat_history']), 2)

    async def test_admin_softened_retry_records_each_api_attempt_once(self):
        create = AsyncMock(side_effect=[RuntimeError('data_inspection_failed'), self.response()])
        namespace, payload = self.admin_chat(create)
        self.assertEqual(await namespace['_chat_test_locked'](payload), {'reply': 'reply', 'softened': True})
        self.assertEqual([event['success'] for event in self.events()], [False, True])
        self.assertEqual(namespace['_chat_history'][0]['content'], 'input softened')

    async def test_admin_cancellation_rolls_back_pending_history(self):
        namespace, payload = self.admin_chat(AsyncMock(side_effect=asyncio.CancelledError))
        with self.assertRaises(asyncio.CancelledError):
            await namespace['_chat_test_locked'](payload)
        self.assertEqual(namespace['_chat_history'], [])
        self.assertFalse(self.guard._RESERVATIONS)

    async def test_concurrent_chat_calls_cannot_spend_same_remaining_budget(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 10

        async def create(**_):
            await asyncio.sleep(0.03)
            return self.response()

        create = AsyncMock(side_effect=create)
        results = await asyncio.gather(self.chat(create), self.chat(create), return_exceptions=True)
        self.assertEqual(create.await_count, 1)
        self.assertEqual(sum(isinstance(result, self.guard.BudgetExceededError) for result in results), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertFalse(self.guard._RESERVATIONS)

    async def test_unlimited_chat_requests_remain_concurrent(self):
        running = 0
        both_started = asyncio.Event()

        async def create(**_):
            nonlocal running
            running += 1
            if running == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 1)
            return self.response()

        await asyncio.gather(self.chat(create), self.chat(create))
        self.assertEqual(len(self.events()), 2)

    async def test_separate_processes_share_chat_budget(self):
        script = r'''
import asyncio, sys, types
sys.path.insert(0, sys.argv[1])
config = types.ModuleType('plugins.config')
for name in ('DAILY_EMBEDDING_TOKEN_LIMIT', 'DAILY_TTS_CHAR_LIMIT', 'PROACTIVE_DAILY_LIMIT',
             'CHAT_INPUT_PRICE_PER_1K_TOKENS', 'CHAT_OUTPUT_PRICE_PER_1K_TOKENS', 'EMBEDDING_PRICE_PER_1K_TOKENS'):
    setattr(config, name, 0)
config.DAILY_CHAT_TOKEN_LIMIT = 10
config.USAGE_TELEMETRY_FILE = sys.argv[2]
sys.modules['plugins.config'] = config
from plugins.usage_telemetry import request_chat_completion
from plugins.budget_guard import BudgetExceededError
async def create(**kwargs):
    await asyncio.sleep(0.2)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='reply'))],
                                 usage={'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10})
async def main():
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    try:
        await request_chat_completion(client, model='test-model', messages=[{'role': 'user', 'content': 'input'}], max_tokens=5)
        print('success')
    except BudgetExceededError:
        print('denied')
asyncio.run(main())
'''
        processes = await asyncio.gather(*[
            asyncio.create_subprocess_exec(sys.executable, '-c', script, str(ROOT), str(self.usage.telemetry_path()),
                                           stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            for _ in range(2)
        ])
        outputs = await asyncio.gather(*(process.communicate() for process in processes))
        for process, (_, error) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, error.decode(errors='replace'))
        self.assertEqual(sorted(output.decode().strip() for output, _ in outputs), ['denied', 'success'])
        self.assertEqual(len(self.events()), 1)

    async def test_cancellation_releases_active_reservation_and_file_lock(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 10
        started = asyncio.Event()

        async def create(**_):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.chat(create))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.guard._RESERVATIONS)
        await self.chat(AsyncMock(return_value=self.response()))
        self.assertEqual(len(self.events()), 1)

    async def test_failed_request_does_not_spend_budget_or_record_success_twice(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 10
        with self.assertRaisesRegex(RuntimeError, 'platform failure'):
            await self.chat(AsyncMock(side_effect=RuntimeError('platform failure')))
        await self.chat(AsyncMock(return_value=self.response()))
        self.assertEqual([event['success'] for event in self.events()], [False, True])
        self.assertFalse(self.guard._RESERVATIONS)

    async def test_missing_provider_usage_records_estimate_once(self):
        await self.chat(AsyncMock(return_value=self.response(usage=False)))
        event, = self.events()
        self.assertTrue(event['estimated_tokens'])
        self.assertEqual(event['total_tokens'], len('input') + len('reply'))

    async def test_midnight_uses_current_day_and_releases_old_reservations(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 10
        used = {'2026-10-10': 10, '2026-10-11': 0}

        def summary(*, day):
            return {'today_by_kind': {'chat_completion': {'total_tokens': used[day]}}}

        with patch.object(self.guard, '_today', return_value='2026-10-10'):
            self.assertIsNone(self.guard.reserve_budget('chat_completion', 10, summary))
        with patch.object(self.guard, '_today', return_value='2026-10-11'):
            reservation = self.guard.reserve_budget('chat_completion', 10, summary)
            self.assertIsNotNone(reservation)
            with reservation:
                reservation.settle(10, lambda: used.update({'2026-10-11': 10}))
            self.assertIsNone(self.guard.reserve_budget('chat_completion', 1, summary))
        self.assertFalse(self.guard._RESERVATIONS)

    async def test_inflight_request_crossing_midnight_settles_on_completion_day_once(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 10
        used = {'2026-10-10': 0, '2026-10-11': 0}
        summary = lambda *, day: {'today_by_kind': {'chat_completion': {'total_tokens': used[day]}}}
        with patch.object(self.guard, '_today', return_value='2026-10-10'):
            reservation = self.guard.reserve_budget('chat_completion', 10, summary)
        with reservation, patch.object(self.guard, '_today', return_value='2026-10-11'):
            self.assertIsNone(self.guard.reserve_budget('chat_completion', 1, summary))
            record = lambda: used.update({'2026-10-11': used['2026-10-11'] + 10})
            reservation.settle(10, record)
            reservation.settle(10, record)
        self.assertEqual(used, {'2026-10-10': 0, '2026-10-11': 10})
        self.assertFalse(self.guard._RESERVATIONS)

    async def test_successful_paid_call_with_failed_telemetry_is_not_retried_or_forgotten(self):
        self.guard.DAILY_CHAT_TOKEN_LIMIT = 10
        create = AsyncMock(return_value=self.response())
        with patch.object(self.usage, 'record_model_usage', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                await self.chat(create)
        with self.assertRaises(self.guard.BudgetExceededError):
            await self.chat(create)
        self.assertEqual(create.await_count, 1)
        self.assertFalse(self.guard._RESERVATIONS)

    async def test_tts_fallback_settles_once_and_rejects_next_request(self):
        self.guard.DAILY_TTS_CHAR_LIMIT = 5
        with patch.object(self.tts, '_provider_chain', return_value=['doubao', 'gsv']), patch.object(
            self.tts, 'get_doubao_audio', new_callable=AsyncMock, return_value=None,
        ), patch.object(self.tts, 'get_sovits_audio', new_callable=AsyncMock, return_value='audio') as synth:
            self.assertEqual(await self.tts.get_tts_audio('12345'), 'audio')
            self.assertIsNone(await self.tts.get_tts_audio('12345'))
        self.assertEqual(synth.await_count, 1)
        self.assertEqual([event['status'] for event in self.events(self.tts._telemetry_path())],
                         ['failed', 'success', 'failed'])
        self.assertEqual(self.tts.get_tts_usage_summary()['chars'], 5)

    async def test_embedding_rejection_does_not_invoke_client_or_record_failed_api_call(self):
        self.guard.DAILY_EMBEDDING_TOKEN_LIMIT = 3
        with patch.object(self.vector, 'AsyncOpenAI') as client:
            with self.assertRaises(self.guard.BudgetExceededError):
                await self.vector.embed_text('1234')
        client.assert_not_called()
        self.assertEqual(self.events(), [])

    async def test_embedding_success_records_provider_usage_and_closes_client(self):
        response = types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[1.0, 2.0])],
                                         usage={'total_tokens': 2, 'prompt_tokens': 2})
        client = AsyncMock()
        client.__aenter__.return_value.embeddings.create.return_value = response
        with patch.object(self.vector, 'AsyncOpenAI', return_value=client):
            self.assertEqual(await self.vector.embed_text('ab'), [1.0, 2.0])
        client.__aexit__.assert_awaited_once()
        event, = self.events()
        self.assertEqual((event['kind'], event['total_tokens'], event['success']), ('embedding', 2, True))


if __name__ == '__main__':
    unittest.main()
