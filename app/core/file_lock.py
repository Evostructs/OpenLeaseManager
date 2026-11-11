from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_file_lock(path: Path):
    """Cross-platform exclusive file lock used to serialize migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        if lock_file.tell() == 0 and path.stat().st_size == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        try:
            import fcntl

            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield lock_file
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        except ImportError:
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield lock_file
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
