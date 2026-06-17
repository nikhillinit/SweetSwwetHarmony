from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SuspensionStore:
    """Durable file-backed suspension state for api_shape_changed circuit breaker.

    Scratch-DB guard: when HARMONIC_SCRATCH_DB=1, all writes are no-ops and
    is_suspended() always returns False. Diagnostic / dry-run sessions must
    set this env var so they never write suspension state to a non-production path.

    Reset audit trail: every suspend/reset action is appended to the JSON file.
    To reset a suspension: store.reset(collector, reset_by="operator").
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._scratch = os.environ.get("HARMONIC_SCRATCH_DB", "0") == "1"
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"suspensions": {}, "audit": []}

    def _save(self) -> None:
        if self._scratch:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def suspend(self, collector: str, reason: str) -> None:
        if self._scratch:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._data["suspensions"][collector] = {"reason": reason, "suspended_at": now}
        self._data["audit"].append(
            {"action": "suspend", "collector": collector, "reason": reason, "at": now}
        )
        self._save()

    def reset(self, collector: str, reset_by: str = "operator") -> None:
        if self._scratch:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._data["suspensions"].pop(collector, None)
        self._data["audit"].append(
            {"action": "reset", "collector": collector, "reset_by": reset_by, "at": now}
        )
        self._save()

    def is_suspended(self, collector: str) -> bool:
        if self._scratch:
            return False
        return collector in self._data.get("suspensions", {})

    def audit_log(self) -> list[dict[str, Any]]:
        return list(self._data.get("audit", []))
