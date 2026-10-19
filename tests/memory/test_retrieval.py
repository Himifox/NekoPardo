"""Vector regressions using temporary storage and synthetic embeddings only."""

import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class VectorMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.vector = importlib.import_module('virtualme.memory.retrieval')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'vectors.jsonl'
        patches = patch.multiple(
            self.vector, VECTOR_MEMORY_FILE=str(self.path),
            EMBEDDING_MODEL='new-model', EMBEDDING_API_KEY='',
            VECTOR_MEMORY_MIN_CHARS=1,
        )
        patches.start()
        self.addCleanup(patches.stop)

    def memory(self, identity='one'):
        return dict(id=identity, group_id=123, content='synthetic memory',
                    role='user', sender='test', timestamp=1)

    def write_vectors(self, rows):
        self.path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    async def test_model_change_reindexes_without_deleting_old_vectors(self):
        old = dict(memory_id='one', group_id=123, embedding_model='old-model', embedding=[1., 0.])
        self.write_vectors([old])
        embed = AsyncMock(return_value=[1., 0.])
        with patch.object(self.vector, 'iter_memory_records', return_value=iter([self.memory()])):
            result = await self.vector.build_vector_memory_index(embedding_func=embed)
        self.assertEqual(result['added'], 1)
        rows = list(self.vector.iter_vector_records())
        self.assertEqual([row['embedding_model'] for row in rows], ['old-model', 'new-model'])
        self.assertEqual(self.vector.load_indexed_memory_ids(), {'one'})

    async def test_recall_filters_models_groups_and_duplicate_ids_before_top_k(self):
        base = dict(memory_id='one', group_id=123, content='synthetic', embedding_model='new-model', embedding=[1., 0.])
        self.write_vectors([
            dict(base, memory_id='old', embedding_model='old-model'),
            dict(base, memory_id='other-group', group_id=456),
            base, base, dict(base, memory_id='two'),
        ])
        result = await self.vector.search_vector_memory(
            'query', 123, top_k=2, embedding_func=AsyncMock(return_value=[1., 0.]))
        self.assertEqual([row['memory_id'] for row in result], ['one', 'two'])

    async def test_concurrent_builders_only_embed_each_memory_once(self):
        embed = AsyncMock()

        async def slow_embedding(text):
            await asyncio.sleep(0.02)
            return [1., 0.]

        embed.side_effect = slow_embedding
        with patch.object(self.vector, 'iter_memory_records', side_effect=lambda: iter([self.memory()])):
            results = await asyncio.gather(*[
                self.vector.build_vector_memory_index(embedding_func=embed) for _ in range(2)
            ])
        self.assertEqual(embed.await_count, 1)
        self.assertEqual(sum(result['added'] for result in results), 1)
        self.assertEqual(len(list(self.vector.iter_vector_records())), 1)

    async def test_limit_counts_failed_attempts_and_zero_sends_nothing(self):
        embed = AsyncMock(side_effect=RuntimeError('synthetic API failure'))
        with patch.object(self.vector, 'iter_memory_records', side_effect=lambda: iter([
            self.memory(str(index)) for index in range(5)
        ])), patch.object(self.vector.logger, 'exception'):
            result = await self.vector.build_vector_memory_index(limit=2, embedding_func=embed)
            self.assertEqual(result['failed'], 2)
            self.assertEqual(embed.await_count, 2)
            embed.reset_mock()
            result = await self.vector.build_vector_memory_index(limit=0, embedding_func=embed)
            embed.assert_not_awaited()
            self.assertEqual(result['added'], 0)

    async def test_skipped_records_do_not_consume_attempt_limit(self):
        embed = AsyncMock(return_value=[1., 0.])
        with patch.object(self.vector, 'iter_memory_records', return_value=iter([
            dict(self.memory('empty'), content=''), self.memory('valid'), self.memory('later')
        ])):
            result = await self.vector.build_vector_memory_index(limit=1, embedding_func=embed)
        self.assertEqual((result['skipped'], result['added']), (1, 1))
        embed.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
