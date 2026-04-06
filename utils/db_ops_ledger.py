"""Repo-local JSONL ledger for destructive DB tooling."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_db_ops_ledger_path() -> Path:
    override = os.getenv("DB_OPS_LEDGER_PATH")
    if override:
        return Path(override)
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / ".omx" / "logs" / "db_ops_ledger.jsonl"


def append_db_ops_ledger(
    *,
    tool_name: str,
    db_path: str,
    action: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> Path:
    path = get_db_ops_ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "tool_name": tool_name,
        "db_path": str(Path(db_path).resolve()),
        "action": action,
        "status": status,
        "details": details or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path
