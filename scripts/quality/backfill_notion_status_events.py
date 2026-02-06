#!/usr/bin/env python3
"""
Backfill/record Notion status events by running sync_suppression and diffing suppression_cache.

This is the "event-sourced" foundation for downstream outcome labeling.

Usage:
  python scripts/quality/backfill_notion_status_events.py --db signals.db

Requires Notion env vars (same as pipeline sync_suppression):
  - NOTION_TOKEN
  - NOTION_DB_ID
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from ops.quality.db import quality_conn
from ops.quality.status_events import sync_and_capture_status_events


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--baseline-new-keys", action="store_true", default=True)
    ap.add_argument("--no-baseline-new-keys", action="store_false", dest="baseline_new_keys")
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        stats = asyncio.run(
            sync_and_capture_status_events(
                conn,
                db_path=args.db,
                baseline_new_keys=bool(args.baseline_new_keys),
            )
        )
        print(json.dumps(stats.__dict__, indent=2))


if __name__ == "__main__":
    main()
