#!/usr/bin/env python3
"""
Generate a keyword-vs-LLM disagreement report.

Usage:
  python scripts/quality/thesis_disagreement_report.py --db signals.db --days 30 --out /tmp/report.md
"""
from __future__ import annotations

import argparse

from ops.quality.db import quality_conn
from ops.quality.thesis import generate_disagreement_report
from utils.db_path_helper import resolve_db_path_env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--keyword-threshold", type=float, default=0.40)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    args.db = resolve_db_path_env(args.db)

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
