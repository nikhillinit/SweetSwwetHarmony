"""Filesystem advisory lock for destructive DB tooling.

This is a thin generalized wrapper extracted from MonitorLock semantics so
non-monitoring DB tools can coordinate around a shared lock contract without
reusing monitoring-specific naming.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils.advisory_file_lock import AdvisoryFileLock, AdvisoryFileLockHealthError

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
        self._lock = AdvisoryFileLock(
            self.lock_path,
            ttl_seconds=ttl_seconds,
            context={
                "kind": "db-tool",
                "toolName": tool_name,
                "dbPath": str(self.db_path),
            },
            legacy_metadata={
                "tool_name": tool_name,
                "db_path": str(self.db_path),
            },
        )

    def acquire(self, timeout_seconds: int = 30) -> bool:
        acquired = self._lock.acquire(timeout_seconds=timeout_seconds)
        if acquired:
            logger.info("Acquired DB tool lock: %s", self.lock_path)
            return True
        holder = self.get_holder_info()
        logger.warning("Failed to acquire DB tool lock. Holder: %s", holder)
        return False

    def release(self) -> None:
        if self._lock.release():
            logger.info("Released DB tool lock: %s", self.lock_path)

    def force_break(self) -> bool:
        return self._lock.force_break()

    def get_holder_info(self) -> dict[str, Any] | None:
        return self._lock.get_holder_info()

    def is_locked(self) -> bool:
        return self._lock.is_locked()

    def assert_healthy(self) -> None:
        try:
            self._lock.assert_healthy()
        except AdvisoryFileLockHealthError as exc:
            raise DBToolLockError(str(exc)) from exc

    def is_healthy(self) -> bool:
        return self._lock.is_healthy()

    def heartbeat_error(self) -> str | None:
        return self._lock.heartbeat_error()

    def __enter__(self) -> "DBToolLock":
        if not self.acquire():
            raise DBToolLockError(f"Could not acquire DB tool lock. Holder: {self.get_holder_info()}")
        return self

    def __exit__(self, *args) -> None:
        self.release()
