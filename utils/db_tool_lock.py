"""Filesystem advisory lock for destructive DB tooling.

This is a thin generalized wrapper extracted from MonitorLock semantics so
non-monitoring DB tools can coordinate around a shared lock contract without
reusing monitoring-specific naming.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DBToolLockError(Exception):
    """Raised when a DB tool lock cannot be acquired."""


class DBToolLock:
    """Filesystem advisory lock for destructive DB tools."""

    DEFAULT_TTL_SECONDS = 3600

    def __init__(
        self,
        db_path: str | Path,
        *,
        tool_name: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.db_path = Path(db_path)
        self.tool_name = tool_name
        self.lock_path = self.db_path.with_suffix(self.db_path.suffix + ".dbtool.lock")
        self.ttl_seconds = ttl_seconds
        self._pid = os.getpid()
        self._acquired = False

    def acquire(self, timeout_seconds: int = 30) -> bool:
        start_time = time.time()
        while True:
            if self._is_stale():
                logger.warning("Breaking stale DB tool lock: %s", self.lock_path)
                self._remove_lock()

            if self._try_create_lock():
                self._acquired = True
                logger.info("Acquired DB tool lock: %s", self.lock_path)
                return True

            if time.time() - start_time >= timeout_seconds:
                holder = self.get_holder_info()
                logger.warning("Failed to acquire DB tool lock. Holder: %s", holder)
                return False

            time.sleep(0.5)

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            holder = self._read_lock()
            if holder and holder.get("pid") == self._pid:
                self._remove_lock()
                logger.info("Released DB tool lock: %s", self.lock_path)
        finally:
            self._acquired = False

    def force_break(self) -> bool:
        if self.lock_path.exists():
            self._remove_lock()
            return True
        return False

    def get_holder_info(self) -> Optional[dict]:
        return self._read_lock()

    def is_locked(self) -> bool:
        return self.lock_path.exists() and not self._is_stale()

    def _try_create_lock(self) -> bool:
        try:
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            lock_info = {
                "pid": self._pid,
                "tool_name": self.tool_name,
                "hostname": os.environ.get("HOSTNAME", os.environ.get("COMPUTERNAME", "unknown")),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "db_path": str(self.db_path),
            }
            os.write(fd, json.dumps(lock_info, indent=2).encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def _read_lock(self) -> Optional[dict]:
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
        acquired_at_str = lock_info.get("acquired_at")
        if not acquired_at_str:
            return True
        try:
            acquired_at = datetime.fromisoformat(acquired_at_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - acquired_at).total_seconds()
            return age_seconds > self.ttl_seconds
        except Exception:
            return True

    def __enter__(self) -> "DBToolLock":
        if not self.acquire():
            raise DBToolLockError(f"Could not acquire DB tool lock. Holder: {self.get_holder_info()}")
        return self

    def __exit__(self, *args) -> None:
        self.release()
