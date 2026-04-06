#!/usr/bin/env python3
"""
Infer TP/FP labels from Notion status events.

Workflow:
1) Run backfill_notion_status_events.py regularly (daily/hourly).
2) Then run this script to label signals based on the first outcome event after push.

Usage:
  python scripts/quality/backfill_quality_outcomes.py --db signals.db --days-to-count 30
"""
from __future__ import annotations

import argparse
import json

from ops.quality.db import quality_conn
from ops.quality.outcomes import backfill_outcomes_from_events
from utils.db_path_helper import resolve_db_path_env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=resolve_db_path_env())
    ap.add_argument("--days-to-count", type=int, default=30)
    ap.add_argument("--since-days", type=int, default=None)
    ap.add_argument("--override-manual", action="store_true", default=False)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        stats = backfill_outcomes_from_events(
            conn,
            days_to_count=args.days_to_count,
            since_days=args.since_days,
            override_manual=bool(args.override_manual),
        )
        print(json.dumps(stats.__dict__, indent=2))


if __name__ == "__main__":
    main()
