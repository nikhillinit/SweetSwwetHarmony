from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class HermesLockError(Exception):
    """Raised when the Hermes advisory lock cannot be acquired."""


class HermesLock:
    DEFAULT_TTL_SECONDS = 3600

    def __init__(
        self,
        lock_path: str | Path,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        mode: str = "unknown",
        run_id: str | None = None,
    ):
        self.lock_path = Path(lock_path)
        self.ttl_seconds = ttl_seconds
        self.mode = mode
        self.run_id = run_id
        self._acquired = False
        self._pid = os.getpid()

    def acquire(self, timeout_seconds: int = 0) -> bool:
        start = time.time()
        while True:
            if self._is_stale():
                self._remove_lock()

            if self._try_create_lock():
                self._acquired = True
                return True

            if time.time() - start >= timeout_seconds:
                return False

            time.sleep(0.5)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            holder = self.get_holder_info()
            if holder and holder.get("pid") == self._pid:
                self._remove_lock()
        finally:
            self._acquired = False

    def force_unlock(self, reason: str) -> bool:
        if not reason.strip():
            raise ValueError("force unlock requires a reason")
        if not self.lock_path.exists():
            return False
        self._remove_lock()
        return True

    def get_holder_info(self) -> dict[str, Any] | None:
        return self._read_lock()

    def is_locked(self) -> bool:
        return self.lock_path.exists() and not self._is_stale()

    def _try_create_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            return False

        lock_info = {
            "pid": self._pid,
            "hostname": os.environ.get("HOSTNAME", os.environ.get("COMPUTERNAME", "unknown")),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "runId": self.run_id,
        }
        try:
            os.write(fd, json.dumps(lock_info, indent=2).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _read_lock(self) -> dict[str, Any] | None:
        try:
            if not self.lock_path.exists():
                return None
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _remove_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def _is_stale(self) -> bool:
        lock_info = self._read_lock()
        if not lock_info:
            return False

        acquired_at = lock_info.get("acquired_at")
        if not acquired_at:
            return True

        try:
            acquired = datetime.fromisoformat(str(acquired_at).replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - acquired).total_seconds() > self.ttl_seconds
        except ValueError:
            return True

    def __enter__(self) -> "HermesLock":
        if not self.acquire(timeout_seconds=0):
            holder = self.get_holder_info()
            raise HermesLockError(f"Could not acquire Hermes lock. Holder: {holder}")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
