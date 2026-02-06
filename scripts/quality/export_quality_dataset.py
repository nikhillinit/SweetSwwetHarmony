#!/usr/bin/env python3
"""
Export labeled signals to a CSV/JSONL dataset for offline evaluation.

Usage:
  python scripts/quality/export_quality_dataset.py --db signals.db --days 90 --format csv --out /tmp/quality.csv
"""
from __future__ import annotations

import argparse
import json
import os

from ops.quality.db import quality_conn
from ops.quality.export import export_dataset_csv, export_dataset_jsonl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--format", choices=["csv", "jsonl"], default="csv")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        if args.format == "csv":
            n = export_dataset_csv(conn, out_path=args.out, days=args.days)
        else:
            n = export_dataset_jsonl(conn, out_path=args.out, days=args.days)

    print(json.dumps({"exported": n, "out": args.out, "format": args.format}, indent=2))


if __name__ == "__main__":
    main()
