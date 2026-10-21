"""Crash-safe, inter-process coordination for the PAPER portfolio state.

Only short local read/modify/write sections belong under these locks.  Market
data and Telegram network calls must be completed before entering a lock.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


DEFAULT_LOCK_TIMEOUT_SECONDS = float(os.getenv("PAPER_STATE_LOCK_TIMEOUT_SECONDS", "10"))
LOCK_POLL_SECONDS = 0.02


class LockTimeoutError(TimeoutError):
    """Raised when a state lock cannot be acquired before its deadline."""


class DuplicateMonitorError(RuntimeError):
    """Raised when a second copy of a continuous monitor is started."""


class InterProcessFileLock:
    """Small stdlib-only advisory file lock for Windows and POSIX."""

    def __init__(self, path: Path, *, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS):
        self.path = Path(path)
        self.timeout = max(float(timeout), 0.0)
        self._handle = None

    def _try_lock(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def acquire(self) -> "InterProcessFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+b")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._try_lock()
                return self
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise LockTimeoutError(
                        f"Timed out after {self.timeout:.3f}s waiting for lock {self.path.name}"
                    ) from exc
                time.sleep(min(LOCK_POLL_SECONDS, max(deadline - time.monotonic(), 0.0)))

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "InterProcessFileLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def atomic_write_json(path: Path, data: Any) -> None:
    """Durably serialize JSON to a sibling temp file, then atomically replace."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except BaseException:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise


def load_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default


def portfolio_lock(state_dir: Path, *, timeout: Optional[float] = None) -> InterProcessFileLock:
    return InterProcessFileLock(
        Path(state_dir) / ".paper_portfolio.lock",
        timeout=DEFAULT_LOCK_TIMEOUT_SECONDS if timeout is None else timeout,
    )


@contextmanager
def monitor_instance_guard(
    state_dir: Path,
    monitor_name: str,
    *,
    timeout: float = 0.0,
) -> Iterator[None]:
    lock = InterProcessFileLock(Path(state_dir) / f".{monitor_name}.instance.lock", timeout=timeout)
    try:
        lock.acquire()
    except LockTimeoutError as exc:
        raise DuplicateMonitorError(f"{monitor_name} is already running") from exc
    try:
        yield
    finally:
        lock.release()
