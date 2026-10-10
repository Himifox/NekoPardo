"""Disk-state and process-lock regression tests without application imports."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.group_access import read_active_groups, update_active_groups


class GroupAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'groups.json'
        self.project = Path(__file__).resolve().parents[1]

    def process(self, script, *args):
        process = subprocess.Popen([sys.executable, '-c', script, *map(str, args)], cwd=self.project,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        return process

    def finish(self, process):
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, stdout + stderr)

    def test_multiple_processes_do_not_lose_whitelist_updates(self):
        script = ('import sys; from plugins.group_access import update_active_groups; '
                  '[update_active_groups(sys.argv[1], add=(int(sys.argv[2])+i,)) for i in range(15)]')
        processes = [self.process(script, self.path, start) for start in (1, 16, 31, 46)]
        for process in processes:
            self.finish(process)
        self.assertEqual(read_active_groups(self.path), set(range(1, 61)))

    def test_update_waits_for_another_process_lock(self):
        ready, release, complete = (self.root / name for name in ('ready', 'release', 'complete'))
        holder_script = '''import sys, time
from pathlib import Path
from plugins.file_lock import file_lock
with file_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).touch()
    deadline = time.monotonic() + 10
    while not Path(sys.argv[3]).exists():
        if time.monotonic() > deadline:
            raise TimeoutError('test release signal missing')
        time.sleep(.01)
'''
        holder = self.process(holder_script, self.path.with_name('groups.json.lock'), ready, release)
        deadline = time.monotonic() + 10
        while not ready.exists():
            self.assertIsNone(holder.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        writer = self.process('import sys; from pathlib import Path; '
                              'from plugins.group_access import update_active_groups; '
                              'update_active_groups(sys.argv[1], add=(123,)); Path(sys.argv[2]).touch()',
                              self.path, complete)
        time.sleep(.25)
        self.assertFalse(complete.exists())
        release.touch()
        self.finish(holder)
        self.finish(writer)
        self.assertTrue(complete.exists())
        self.assertEqual(read_active_groups(self.path), {123})

    def test_failed_replace_and_invalid_data_preserve_original(self):
        original = '[1, 2]'
        self.path.write_text(original, encoding='utf-8')
        with patch('plugins.group_access.os.replace', side_effect=OSError('unwritable')):
            with self.assertRaises(OSError):
                update_active_groups(self.path, add=(3,))
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)
        self.assertEqual({p.name for p in self.root.iterdir()}, {'groups.json', 'groups.json.lock'})
        for malformed in ('{}', '[true]', '[1.5]', '[-1]', 'partial'):
            self.path.write_text(malformed, encoding='utf-8')
            with self.assertRaises(ValueError):
                update_active_groups(self.path, add=(3,))
            self.assertEqual(self.path.read_text(encoding='utf-8'), malformed)

    def test_addition_does_not_resurrect_a_deleted_group(self):
        update_active_groups(self.path, add=(1, 2))
        update_active_groups(self.path, remove=(1,))
        update_active_groups(self.path, add=(3,))
        self.assertEqual(read_active_groups(self.path), {2, 3})


if __name__ == '__main__':
    unittest.main()
