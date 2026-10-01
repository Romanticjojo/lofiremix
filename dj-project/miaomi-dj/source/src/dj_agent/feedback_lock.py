"""Reentrant session locks shared by browser, CLI and training readers."""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def session_lock(session):
    root = Path(session)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('反馈目录不存在或是链接')
    key = str(root.resolve())
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        path = root/'.feedback.lock'
        if path.is_symlink():
            raise ValueError('反馈锁文件不能是链接')
        with path.open('a+b') as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            _local.held = held
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
