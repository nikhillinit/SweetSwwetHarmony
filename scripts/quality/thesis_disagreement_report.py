#!/usr/bin/env python3
"""
Generate a keyword-vs-LLM disagreement report.

Usage:
  python scripts/quality/thesis_disagreement_report.py --db signals.db --days 30 --out /tmp/report.md
"""
from __future__ import annotations

import argparse
import os

from ops.quality.db import quality_conn
from ops.quality.thesis import generate_disagreement_report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--keyword-threshold", type=float, default=0.40)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        report = generate_disagreement_report(
            conn,
            days=args.days,
            keyword_threshold=args.keyword_threshold,
            out_path=args.out,
        )

    if args.out:
        print(f"Wrote: {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
