"""
Advisory Lock for Monitoring Sweep

Prevents concurrent monitor sweeps on the same database.
Uses a filesystem-based lock with TTL for stale lock detection.

Per Spec v2.4 Section 10.3.
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


class MonitorLockError(Exception):
    """Raised when lock cannot be acquired."""
    pass


class MonitorLock:
    """
    Filesystem-based advisory lock for monitoring sweeps.

    Prevents concurrent sweeps on the same database by creating a lock file.
    Supports TTL-based stale lock detection and force-break for recovery.

    Usage:
        lock = MonitorLock("/path/to/signals.db")

        # Try to acquire lock
        if lock.acquire(timeout_seconds=30):
            try:
                # Do sweep work
                pass
            finally:
                lock.release()
        else:
            print(f"Lock held by: {lock.get_holder_info()}")

        # Or use context manager
        with MonitorLock("/path/to/signals.db") as lock:
            # Do sweep work
            pass
    """

    DEFAULT_TTL_SECONDS = 3600  # 1 hour - sweeps should not take this long

    def __init__(
        self,
        db_path: str | Path,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        """
        Initialize lock.

        Args:
            db_path: Path to the database (lock file will be {db_path}.monitor.lock)
            ttl_seconds: TTL for stale lock detection
        """
        self.db_path = Path(db_path)
        self.lock_path = self.db_path.with_suffix(self.db_path.suffix + ".monitor.lock")
        self.ttl_seconds = ttl_seconds
        self._acquired = False
        self._pid = os.getpid()

    def acquire(self, timeout_seconds: int = 30) -> bool:
        """
        Try to acquire the lock.

        Args:
            timeout_seconds: How long to wait for lock (0 = no wait)

        Returns:
            True if lock acquired, False otherwise
        """
        start_time = time.time()

        while True:
            # Check for stale lock
            if self._is_stale():
                logger.warning(f"Breaking stale lock: {self.lock_path}")
                self._remove_lock()

            # Try to acquire
            if self._try_create_lock():
                self._acquired = True
                logger.info(f"Acquired monitor lock: {self.lock_path}")
                return True

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed >= timeout_seconds:
                holder = self.get_holder_info()
                logger.warning(f"Failed to acquire lock after {elapsed:.1f}s. Holder: {holder}")
                return False

            # Wait and retry
            time.sleep(0.5)

    def release(self) -> None:
        """Release the lock."""
        if not self._acquired:
            return

        try:
            # Only remove if we still own it
            holder = self._read_lock()
            if holder and holder.get("pid") == self._pid:
                self._remove_lock()
                logger.info(f"Released monitor lock: {self.lock_path}")
            else:
                logger.warning(f"Lock not owned by this process, not releasing")
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")
        finally:
            self._acquired = False

    def force_break(self) -> bool:
        """
        Force-break the lock (use with caution).

        Returns:
            True if lock was broken, False if no lock existed
        """
        if self.lock_path.exists():
            holder = self.get_holder_info()
            self._remove_lock()
            logger.warning(f"Force-broke lock. Previous holder: {holder}")
            return True
        return False

    def get_holder_info(self) -> Optional[dict]:
        """
        Get information about the current lock holder.

        Returns:
            Dict with holder info or None if no lock
        """
        return self._read_lock()

    def is_locked(self) -> bool:
        """Check if lock is currently held (and not stale)."""
        if not self.lock_path.exists():
            return False
        return not self._is_stale()

    def _try_create_lock(self) -> bool:
        """Try to atomically create lock file."""
        try:
            # Use O_CREAT | O_EXCL for atomic creation
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )

            # Write lock info
            lock_info = {
                "pid": self._pid,
                "hostname": os.environ.get("HOSTNAME", os.environ.get("COMPUTERNAME", "unknown")),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "db_path": str(self.db_path),
            }

            os.write(fd, json.dumps(lock_info, indent=2).encode())
            os.close(fd)
            return True

        except FileExistsError:
            return False
        except Exception as e:
            logger.error(f"Error creating lock: {e}")
            return False

    def _read_lock(self) -> Optional[dict]:
        """Read lock file contents."""
        try:
            if not self.lock_path.exists():
                return None

            with open(self.lock_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error reading lock: {e}")
            return None

    def _remove_lock(self) -> None:
        """Remove lock file."""
        try:
            self.lock_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Error removing lock: {e}")

    def _is_stale(self) -> bool:
        """Check if existing lock is stale (TTL expired)."""
        lock_info = self._read_lock()
        if not lock_info:
            return False

        acquired_at_str = lock_info.get("acquired_at")
        if not acquired_at_str:
            return True  # Invalid lock

        try:
            acquired_at = datetime.fromisoformat(acquired_at_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - acquired_at).total_seconds()
            return age_seconds > self.ttl_seconds
        except Exception:
            return True  # Invalid timestamp

    def __enter__(self) -> "MonitorLock":
        """Context manager entry."""
        if not self.acquire():
            holder = self.get_holder_info()
            raise MonitorLockError(f"Could not acquire lock. Holder: {holder}")
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.release()


# Convenience function
def acquire_monitor_lock(
    db_path: str | Path,
    timeout_seconds: int = 30,
) -> Optional[MonitorLock]:
    """
    Acquire a monitor lock (convenience function).

    Args:
        db_path: Database path
        timeout_seconds: Lock timeout

    Returns:
        MonitorLock if acquired, None otherwise
    """
    lock = MonitorLock(db_path)
    if lock.acquire(timeout_seconds):
        return lock
    return None
