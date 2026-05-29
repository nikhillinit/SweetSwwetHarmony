"""Shared atomic advisory file lock primitive."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _LockState:
    exists: bool
    metadata: dict[str, Any] | None
    raw: str | None
    mtime: float | None

    @property
    def malformed(self) -> bool:
        return self.exists and self.metadata is None


class AdvisoryFileLock:
    """Filesystem advisory lock with owner-token release and serialized reclaim."""

    DEFAULT_TTL_SECONDS = 3600
    DEFAULT_BREAK_LOCK_TTL_SECONDS = 30
    DEFAULT_MALFORMED_GRACE_SECONDS = 30

    def __init__(
        self,
        lock_path: str | Path,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        context: dict[str, Any] | None = None,
        legacy_metadata: dict[str, Any] | None = None,
        break_lock_ttl_seconds: int = DEFAULT_BREAK_LOCK_TTL_SECONDS,
        malformed_grace_seconds: int = DEFAULT_MALFORMED_GRACE_SECONDS,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.break_lock_path = self.lock_path.with_name(f"{self.lock_path.name}.break")
        self.ttl_seconds = ttl_seconds
        self.context = dict(context or {})
        self.legacy_metadata = dict(legacy_metadata or {})
        self.break_lock_ttl_seconds = break_lock_ttl_seconds
        self.malformed_grace_seconds = malformed_grace_seconds
        self.owner_token = str(uuid.uuid4())
        self._break_owner_token = str(uuid.uuid4())
        self._pid = os.getpid()
        self._acquired = False

    def acquire(self, timeout_seconds: int = 0) -> bool:
        start = time.monotonic()
        while True:
            if self._try_create_target_lock():
                self._acquired = True
                return True

            state = self._read_state(self.lock_path)
            if self._target_can_be_reclaimed(state) and self._break_observed_target(state):
                continue

            if time.monotonic() - start >= timeout_seconds:
                return False

            time.sleep(0.5)

    def release(self) -> bool:
        if not self._acquired:
            return False
        released = False
        try:
            state = self._read_state(self.lock_path)
            if state.metadata and state.metadata.get("ownerToken") == self.owner_token:
                self.lock_path.unlink(missing_ok=True)
                released = True
        finally:
            self._acquired = False
        return released

    def force_break(self) -> bool:
        if not self.lock_path.exists():
            return False
        self.lock_path.unlink(missing_ok=True)
        self._acquired = False
        return True

    def get_holder_info(self) -> dict[str, Any] | None:
        return self._read_state(self.lock_path).metadata

    def is_locked(self) -> bool:
        state = self._read_state(self.lock_path)
        return state.exists and not self._target_can_be_reclaimed(state)

    def refresh_metadata(
        self,
        *,
        context: dict[str, Any] | None = None,
        legacy_metadata: dict[str, Any] | None = None,
    ) -> bool:
        if not self._acquired:
            return False
        state = self._read_state(self.lock_path)
        if not state.metadata or state.metadata.get("ownerToken") != self.owner_token:
            self._acquired = False
            return False

        if context is not None:
            self.context = dict(context)
        if legacy_metadata is not None:
            self.legacy_metadata = dict(legacy_metadata)
        metadata = self._metadata(acquired_at=str(state.metadata.get("acquiredAt") or self._now()))
        self._write_metadata_atomic(self.lock_path, metadata)
        return True

    def _try_create_target_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            return False

        os.close(fd)
        try:
            self._write_metadata_atomic(self.lock_path, self._metadata())
        except Exception:
            self.lock_path.unlink(missing_ok=True)
            raise
        return True

    def _target_can_be_reclaimed(self, state: _LockState) -> bool:
        if not state.exists:
            return False
        if state.malformed:
            return self._state_age_seconds(state) > self.malformed_grace_seconds
        if not state.metadata:
            return False
        return self._metadata_is_stale(state.metadata)

    def _break_observed_target(self, observed_state: _LockState) -> bool:
        if not self._acquire_break_lock():
            return False
        try:
            current_state = self._read_state(self.lock_path)
            if not self._same_observed_target(observed_state, current_state):
                return False
            if not self._target_can_be_reclaimed(current_state):
                return False
            self.lock_path.unlink(missing_ok=True)
            return True
        finally:
            self._release_break_lock()

    def _acquire_break_lock(self) -> bool:
        while True:
            if self._try_create_break_lock():
                return True

            state = self._read_state(self.break_lock_path)
            if not self._break_lock_is_stale(state):
                return False
            if not self._unlink_if_same(self.break_lock_path, state):
                return False

    def _try_create_break_lock(self) -> bool:
        self.break_lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(
                str(self.break_lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError:
            return False

        os.close(fd)
        try:
            self._write_metadata_atomic(
                self.break_lock_path,
                {
                    "ownerToken": self._break_owner_token,
                    "pid": self._pid,
                    "hostname": _hostname(),
                    "acquiredAt": self._now(),
                    "heartbeatAt": self._now(),
                    "ttlSeconds": self.break_lock_ttl_seconds,
                    "context": {"kind": "advisory-break-lock", "target": str(self.lock_path)},
                },
            )
        except Exception:
            self.break_lock_path.unlink(missing_ok=True)
            raise
        return True

    def _release_break_lock(self) -> None:
        state = self._read_state(self.break_lock_path)
        if state.metadata and state.metadata.get("ownerToken") == self._break_owner_token:
            self.break_lock_path.unlink(missing_ok=True)

    def _break_lock_is_stale(self, state: _LockState) -> bool:
        if not state.exists:
            return True
        if state.malformed:
            return self._state_age_seconds(state) > self.break_lock_ttl_seconds
        if not state.metadata:
            return False
        ttl_seconds = _coerce_ttl_seconds(
            state.metadata.get("ttlSeconds"),
            self.break_lock_ttl_seconds,
        )
        return self._metadata_age_seconds(state.metadata) > ttl_seconds

    def _metadata_is_stale(self, metadata: dict[str, Any]) -> bool:
        ttl_seconds = _coerce_ttl_seconds(metadata.get("ttlSeconds"), self.ttl_seconds)
        return self._metadata_age_seconds(metadata) > ttl_seconds

    def _metadata_age_seconds(self, metadata: dict[str, Any]) -> float:
        observed_at = _metadata_observed_at(metadata)
        if observed_at is None:
            return float("inf")
        return (datetime.now(timezone.utc) - observed_at).total_seconds()

    def _state_age_seconds(self, state: _LockState) -> float:
        if state.mtime is None:
            return 0.0
        return max(0.0, time.time() - state.mtime)

    def _same_observed_target(self, observed_state: _LockState, current_state: _LockState) -> bool:
        if not observed_state.exists or not current_state.exists:
            return observed_state.exists == current_state.exists
        observed_token = (
            observed_state.metadata.get("ownerToken")
            if observed_state.metadata
            else None
        )
        if observed_token:
            return bool(
                current_state.metadata
                and current_state.metadata.get("ownerToken") == observed_token
            )
        return current_state.raw == observed_state.raw

    def _unlink_if_same(self, path: Path, observed_state: _LockState) -> bool:
        current_state = self._read_state(path)
        if not self._same_observed_target(observed_state, current_state):
            return False
        path.unlink(missing_ok=True)
        return True

    def _metadata(self, *, acquired_at: str | None = None) -> dict[str, Any]:
        now = self._now()
        acquired = acquired_at or now
        metadata: dict[str, Any] = {
            "ownerToken": self.owner_token,
            "pid": self._pid,
            "hostname": _hostname(),
            "acquiredAt": acquired,
            "heartbeatAt": now,
            "ttlSeconds": self.ttl_seconds,
            "context": dict(self.context),
        }
        metadata.update(self.legacy_metadata)
        metadata["acquired_at"] = acquired
        return metadata

    def _write_metadata_atomic(self, path: Path, metadata: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8")
        fd = os.open(str(tmp_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        try:
            os.replace(tmp_path, path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _read_state(self, path: Path) -> _LockState:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return _LockState(False, None, None, None)

        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return _LockState(True, None, None, stat.st_mtime)
        if not isinstance(payload, dict):
            return _LockState(True, None, raw, stat.st_mtime)
        return _LockState(True, payload, raw, stat.st_mtime)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


def _metadata_observed_at(metadata: dict[str, Any]) -> datetime | None:
    for key in ("heartbeatAt", "acquiredAt", "acquired_at"):
        value = metadata.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _coerce_ttl_seconds(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hostname() -> str:
    return os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or socket.gethostname()
