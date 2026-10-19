"""Account/session isolation and immutable original-message metadata."""
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock
from virtualme.models import SessionKey, MessageEnvelope, ReplyMode

class MessageIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = importlib.import_module('virtualme.memory.session')
        self.store = importlib.import_module('virtualme.memory.store')
        self.retrieval = importlib.import_module('virtualme.memory.retrieval')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'memory.jsonl'
        for module, name, value in ((self.store, 'LONG_TERM_MEMORY_FILE', str(self.path)),
                                    (self.session, '_history_cache', __import__('collections').defaultdict(lambda: __import__('collections').deque(maxlen=20)))):
            setting = patch.object(module, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def test_same_numeric_id_is_isolated_by_type_and_account(self):
        sessions = [SessionKey('1', 'group', 42), SessionKey('1', 'private', 42), SessionKey('2', 'group', 42)]
        for index, key in enumerate(sessions):
            self.session.record_message(MessageEnvelope(key, '42', 'test', str(index), 10, f'unique {index}'))
        for index, key in enumerate(sessions):
            history = self.session.get_history_str(key)
            self.assertIn(f'unique {index}', history)
            for other in range(3):
                if other != index:
                    self.assertNotIn(f'unique {other}', history)

    def test_original_text_and_reply_identity_survive_prompt_cleaning(self):
        message = MessageEnvelope(SessionKey('1', 'group', 7), '42', 'user', '9001', 10,
                                  '操作说明', ({'type': 'text', 'data': {'text': '操作说明'}},), '8999', raw_text=' 原始操作说明 ')
        self.session.record_message(message)
        row, = list(self.store.iter_memory_records())
        self.assertEqual(row['raw_content'], ' 原始操作说明 ')
        self.assertEqual(row['message_id'], '9001')
        self.assertEqual(row['reply_to'], '8999')
        self.assertEqual(row['content'], self.session.sanitize_reply('操作说明'))

    async def test_scoped_recall_does_not_mix_private_other_accounts_or_legacy_records(self):
        rows = [dict(memory_id=str(i), group_id=42, embedding_model='test', embedding=[1., 0.], content='memory', **scope)
                for i, scope in enumerate(({}, {'account_id': '1', 'session_type': 'private'},
                                            {'account_id': '2', 'session_type': 'group'},
                                            {'account_id': '1', 'session_type': 'group'}))]
        with patch.object(self.retrieval, 'iter_vector_records', return_value=iter(rows)), patch.object(
            self.retrieval, 'EMBEDDING_MODEL', 'test'):
            result = await self.retrieval.search_vector_memory('query', SessionKey('1', 'group', 42),
                                                              embedding_func=AsyncMock(return_value=[1., 0.]))
        self.assertEqual([item['memory_id'] for item in result], ['3'])


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def deliver(self, mode, *, synthesize, send, on_reply_sent):
        from virtualme.adapters.qq.delivery import deliver_reply
        return await deliver_reply(None, None, 'answer', mode, send=send, on_reply_sent=on_reply_sent,
                                   synthesize=synthesize, send_sticker=AsyncMock(return_value=False))

    async def test_failed_optional_audio_keeps_sent_text_and_single_memory_commit(self):
        send, record = AsyncMock(return_value={'message_id': 1}), Mock()
        with patch('virtualme.adapters.qq.delivery.random.random', return_value=0):
            result = await self.deliver(ReplyMode.TEXT_AND_OPTIONAL_VOICE,
                                       synthesize=AsyncMock(side_effect=OSError('audio offline')),
                                       send=send, on_reply_sent=record)
        self.assertTrue(result.text_sent)
        self.assertFalse(result.voice_sent)
        self.assertIn('voice', result.errors)
        send.assert_awaited_once_with('answer')
        record.assert_called_once_with('answer')

    async def test_voice_failure_falls_back_to_text(self):
        send, record = AsyncMock(), Mock()
        result = await self.deliver(ReplyMode.VOICE, synthesize=AsyncMock(side_effect=OSError('offline')),
                                    send=send, on_reply_sent=record)
        self.assertTrue(result.text_sent)
        send.assert_awaited_once_with('answer')
        record.assert_called_once()

    async def test_sent_voice_is_not_resent_when_memory_write_fails(self):
        send = AsyncMock()
        with self.assertRaisesRegex(OSError, 'memory failed'):
            await self.deliver(ReplyMode.VOICE, synthesize=AsyncMock(return_value='audio'), send=send,
                               on_reply_sent=Mock(side_effect=OSError('memory failed')))
        self.assertEqual(send.await_count, 1)
