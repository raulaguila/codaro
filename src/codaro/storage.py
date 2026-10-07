"""Private bounded reads paired with the atomic storage writer."""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path


def private_read(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    directory = None
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Armazenamento não pode usar links simbólicos.")
    try:
        if os.name == "posix":
            directory = os.open(path.parent, flags | os.O_DIRECTORY)
            descriptor = os.open(path.name, flags, dir_fd=directory)
        else:
            descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Armazenamento deve ser regular, sem links.")
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Armazenamento excede o limite.")
        return data
    finally:
        if directory is not None:
            os.close(directory)


def private_json(path: Path, maximum: int):
    try:
        return json.loads(private_read(path, maximum))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("Armazenamento JSON inválido.") from exc


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def thread_lock(lock):
    if not lock.acquire(timeout=5):
        raise ValueError("Armazenamento em uso por outra sessão; tente novamente.")
    try:
        yield
    finally:
        lock.release()


@contextmanager
def private_lock(path: Path):
    """Serialize archive updates, including other POSIX processes, without following links."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path), threading.RLock())
    with thread_lock(lock):
        if os.name != "posix":
            yield
            return
        import fcntl

        root = os.open(path.parent.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory = descriptor = None
        try:
            try:
                os.mkdir(path.parent.name, 0o700, dir_fd=root)
            except FileExistsError:
                pass
            directory = os.open(
                path.parent.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
            )
            descriptor = os.open(
                path.name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Lock deve ser regular, sem links.")
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError(
                            "Memória em uso por outra sessão; tente novamente."
                        ) from None
                    time.sleep(0.05)
            yield
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
            os.close(root)
