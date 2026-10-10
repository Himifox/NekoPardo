"""Advisory file locks shared by the bot and its local admin process."""

import asyncio
import errno
import os
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def _lock_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as file:
        if os.name == "nt" and file.seek(0, os.SEEK_END) == 0:
            file.write(b"\0")
            file.flush()
        yield file


def _try_lock(file) -> bool:
    try:
        if os.name == "nt":
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            return False
        raise
    return True


def _unlock(file) -> None:
    if os.name == "nt":
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path):
    """For short synchronous disk updates; never hold across network calls."""
    with _lock_file(Path(path)) as file:
        while not _try_lock(file):
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(file)


@asynccontextmanager
async def async_file_lock(path: Path):
    with _lock_file(Path(path)) as file:
        while not _try_lock(file):
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            _unlock(file)
