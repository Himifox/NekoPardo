"""The advisory lock must serialize separate local processes."""

import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from virtualme.storage.files import async_file_lock


class FileLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_child_process_waits_until_parent_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared.lock'
            marker = Path(directory) / 'acquired.txt'
            script = '''
import asyncio, sys
from pathlib import Path
from virtualme.storage.files import async_file_lock
async def main():
    print('ready', flush=True)
    async with async_file_lock(Path(sys.argv[1])):
        Path(sys.argv[2]).write_text('acquired')
asyncio.run(main())
'''
            process = None
            try:
                async with async_file_lock(path):
                    process = subprocess.Popen(
                        [sys.executable, '-c', script, str(path), str(marker)],
                        cwd=Path(__file__).resolve().parents[2],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    )
                    ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10)
                    self.assertEqual(ready.strip(), 'ready')
                    await asyncio.sleep(0.2)
                    self.assertIsNone(process.poll())
                    self.assertFalse(marker.exists())
                _, stderr = await asyncio.wait_for(asyncio.to_thread(process.communicate), 10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertTrue(marker.exists())
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
