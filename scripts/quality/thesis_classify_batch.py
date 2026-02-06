#!/usr/bin/env python3
"""
Batch classify recent signals missing thesis_classifications rows.

Usage:
  python scripts/quality/thesis_classify_batch.py --db signals.db --days 30 --limit 200
"""
from __future__ import annotations

import argparse
import json
import os

from ops.quality.db import quality_conn
from ops.quality.thesis import batch_classify_missing_thesis


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--model", default="gemini-2.0-flash")
    ap.add_argument("--prompt-version", default="quality-ops-v1")
    ap.add_argument("--stop-on-error", action="store_true", default=False)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        summary = batch_classify_missing_thesis(
            conn,
            days=args.days,
            limit=args.limit,
            model=args.model,
            prompt_version=args.prompt_version,
            stop_on_error=bool(args.stop_on_error),
        )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
