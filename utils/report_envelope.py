"""Standard report envelope for v6.6.2 CLI commands.

All canary-phase commands write JSON reports using this envelope format.
Atomic write via tempfile + rename to prevent partial files on crash.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def create_report(
    command: str,
    ok: bool,
    db_path: str,
    started_at: datetime,
    metrics: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a standard report envelope.

    Returns a dict with the report contents.
    """
    return {
        "ok": ok,
        "command": command,
        "version": "v6.6.2",
        "db_path": str(db_path),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def write_report(report: Dict[str, Any], path: str) -> None:
    """Atomically write a JSON report to disk.

    Creates parent directories if needed. Uses tempfile + rename
    for crash safety.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        suffix=".tmp",
        prefix=".report_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # Atomic rename (same filesystem)
        os.replace(tmp_path, str(target))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
