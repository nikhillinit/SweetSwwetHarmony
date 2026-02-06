#!/usr/bin/env python3
"""
Detect FP patterns from labeled signals and write a JSON report.

Usage:
  python scripts/quality/fp_pattern_detector.py --db signals.db --days 30 --out /tmp/patterns.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ops.quality.db import quality_conn
from ops.quality.patterns import PatternConfig, detect_patterns


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-count", type=int, default=10)
    ap.add_argument("--fp-rate-threshold", type=float, default=0.70)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = PatternConfig(days=args.days, min_count=args.min_count, fp_rate_threshold=args.fp_rate_threshold)

    with quality_conn(args.db) as conn:
        patterns = detect_patterns(conn, config=cfg)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"config": cfg.__dict__, "patterns": patterns}, indent=2), encoding="utf-8")

    print(json.dumps({"patterns": len(patterns), "out": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()
