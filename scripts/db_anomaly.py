from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AnomalyResult:
    ok: bool
    anomaly_type: str | None = None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


class AnomalyChecker:
    def __init__(
        self,
        db_path: Path,
        known_bad_shas_path: Path | None = None,
        watermark_path: Path | None = None,
        output_path: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.known_bad_shas_path = Path(known_bad_shas_path) if known_bad_shas_path else None
        self.watermark_path = Path(watermark_path) if watermark_path else None
        self.output_path = Path(output_path) if output_path else None

    def check(self) -> AnomalyResult:
        sha = self._sha256()
        row_count = self._row_count()
        evidence = {
            "sha256": sha,
            "row_count": row_count,
            "size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.output_path:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(json.dumps(evidence, indent=2))

        if self.known_bad_shas_path and self.known_bad_shas_path.exists():
            bad = json.loads(self.known_bad_shas_path.read_text()).get("shas", [])
            if sha in bad:
                return AnomalyResult(
                    ok=False,
                    anomaly_type="known_bad_sha",
                    detail=f"sha {sha} in known_bad_shas",
                    evidence=evidence,
                )

        if self.watermark_path and self.watermark_path.exists():
            wm = json.loads(self.watermark_path.read_text())
            min_rows = int(wm.get("min_row_count", 0))
            if row_count < min_rows:
                return AnomalyResult(
                    ok=False,
                    anomaly_type="row_count_drop",
                    detail=f"row_count={row_count} < watermark={min_rows}",
                    evidence=evidence,
                )

        return AnomalyResult(ok=True, evidence=evidence)

    def _sha256(self) -> str:
        h = hashlib.sha256()
        if self.db_path.exists():
            h.update(self.db_path.read_bytes())
        return h.hexdigest()

    def _row_count(self) -> int:
        if not self.db_path.exists():
            return 0
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            count = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            con.close()
            return int(count)
        except Exception:
            return 0


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Check signals.db for known anomalies")
    parser.add_argument("db_path")
    parser.add_argument("--known-bad-shas", dest="known_bad_shas_path")
    parser.add_argument("--watermark", dest="watermark_path")
    parser.add_argument("--output", dest="output_path", default=".omx/state/anomaly_manifest.json")
    args = parser.parse_args()

    result = AnomalyChecker(
        db_path=Path(args.db_path),
        known_bad_shas_path=Path(args.known_bad_shas_path) if args.known_bad_shas_path else None,
        watermark_path=Path(args.watermark_path) if args.watermark_path else None,
        output_path=Path(args.output_path),
    ).check()

    print(json.dumps({"ok": result.ok, "anomaly_type": result.anomaly_type, "detail": result.detail}, indent=2))
    sys.exit(0 if result.ok else 1)
