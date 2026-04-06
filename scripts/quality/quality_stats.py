#!/usr/bin/env python3
"""
Compute quality stats (overall + by source_api) from labeled signals.

Usage:
  python scripts/quality/quality_stats.py --db signals.db --days 30
"""
from __future__ import annotations

import argparse
import json
from ops.quality.db import quality_conn
from ops.quality.stats import get_overall_stats, get_stats_by_source_api
from utils.db_path_helper import resolve_db_path_env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=resolve_db_path_env())
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-labeled", type=int, default=10)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        overall = get_overall_stats(conn, days=args.days)
        by_src = get_stats_by_source_api(conn, days=args.days, min_labeled=args.min_labeled)

    print(json.dumps({"overall": overall}, indent=2))
    print("")
    for s in by_src:
        print(f"- {s.source_api:24s} labeled={s.labeled_signals:5d} fp={s.fp:4d} tp={s.tp:4d} unsure={s.unsure:4d} fp_rate={s.fp_rate:.2%}")


if __name__ == "__main__":
    main()
