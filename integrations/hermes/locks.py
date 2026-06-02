from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.advisory_file_lock import AdvisoryFileLock, AdvisoryFileLockHealthError


class HermesLockError(Exception):
    """Raised when the Hermes advisory lock cannot be acquired."""


_CANONICAL_LOCK_ORDER = (
    "signals.db",
    "hermes-config",
    "governance",
    "collector-promotion",
    "incident-response",
    "notion-outbox",
    "shadow-entity-evaluator",
    "suppression-cache",
)
_LOCK_ORDER_RANK = {
    lock_name: index for index, lock_name in enumerate(_CANONICAL_LOCK_ORDER)
}


def canonical_lock_order(lock_names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            lock_names,
            key=lambda lock_name: (
                _LOCK_ORDER_RANK.get(lock_name, len(_CANONICAL_LOCK_ORDER)),
                lock_name,
            ),
        )
    )


def assert_canonical_lock_order(
    lock_names: tuple[str, ...],
    *,
    task_name: str,
) -> None:
    expected = canonical_lock_order(lock_names)
    if lock_names == expected:
        return
    raise HermesLockError(
        f"{task_name} required_locks must follow canonical lock order: "
        f"{', '.join(expected)}; got {', '.join(lock_names)}"
    )


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
        self._lock = AdvisoryFileLock(
            self.lock_path,
            ttl_seconds=ttl_seconds,
            context={
                "kind": "hermes",
                "mode": mode,
                "runId": run_id,
            },
            legacy_metadata={
                "mode": mode,
                "runId": run_id,
            },
        )
        self._pid = os.getpid()

    def acquire(self, timeout_seconds: int = 0) -> bool:
        return self._lock.acquire(timeout_seconds=timeout_seconds)

    def release(self) -> None:
        self._lock.release()

    def force_unlock(self, reason: str) -> bool:
        if not reason.strip():
            raise ValueError("force unlock requires a reason")
        if not self.lock_path.exists():
            return False
        holder = self.get_holder_info()
        self._append_force_unlock_audit(reason, holder)
        return self._lock.force_break()

    def get_holder_info(self) -> dict[str, Any] | None:
        return self._lock.get_holder_info()

    def is_locked(self) -> bool:
        return self._lock.is_locked()

    def assert_healthy(self) -> None:
        try:
            self._lock.assert_healthy()
        except AdvisoryFileLockHealthError as exc:
            raise HermesLockError(str(exc)) from exc

    def is_healthy(self) -> bool:
        return self._lock.is_healthy()

    def heartbeat_error(self) -> str | None:
        return self._lock.heartbeat_error()

    def _append_force_unlock_audit(
        self,
        reason: str,
        holder: dict[str, Any] | None,
    ) -> None:
        audit_path = self.lock_path.parent / "forced_unlocks.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "pid": self._pid,
            "lockHolderInfoSnapshot": holder,
        }
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def __enter__(self) -> "HermesLock":
        if not self.acquire(timeout_seconds=0):
            holder = self.get_holder_info()
            raise HermesLockError(f"Could not acquire Hermes lock. Holder: {holder}")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()
