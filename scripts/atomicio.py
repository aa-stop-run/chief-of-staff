"""Writing the Chief's state files safely when more than one process may do it (the dashboard server, `chief.py` in a terminal,
a worker running `proposals.py`): a temporary file that no other writer shares, an atomic replace that copes with Windows
briefly refusing it, and a small lock file for read-modify-write sequences."""
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

REPLACE_TRIES, REPLACE_WAIT_S = 20, 0.05     # Windows refuses a replace while another process has the target open
LOCK_WAIT_S, LOCK_STALE_S = 10.0, 30.0       # how long to wait for the lock; a lock older than this was left by a dead process


def write_text(path, text: str, newline: str | None = "\n") -> None:
    """Replace `path` with `text` atomically. Readers see the old file or the new one, never half of it. Line endings are LF on
    every system unless the caller asks otherwise, so a state file is the same bytes wherever it was written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8", newline=newline)
    for attempt in range(REPLACE_TRIES):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(REPLACE_WAIT_S)


@contextmanager
def locked(path, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S):
    """Hold `<path>.lock` while reading, changing and writing `path`, so two processes do not lose each other's change.
    A lock left behind by a process that died is taken over after `stale_s`. Raises TimeoutError if it cannot be had."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue                      # it went away between the two calls: try again
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock.name} is held by another process") from None
            time.sleep(0.05)
        except PermissionError:               # Windows: the lock is being deleted by its owner right now
            if time.monotonic() > deadline:
                raise TimeoutError(f"{lock.name} is held by another process") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
