from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from integrations.hermes.config import PROJECT_ROOT

GATE_FAILURE_EXIT = 4


def emit(ok: bool, detail: str, evidence: dict[str, Any] | None = None) -> int:
    print(
        json.dumps(
            {
                "ok": ok,
                "detail": detail,
                "evidence": evidence or {},
            },
            indent=2,
        )
    )
    return 0 if ok else GATE_FAILURE_EXIT


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_project_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def latest_existing(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)
