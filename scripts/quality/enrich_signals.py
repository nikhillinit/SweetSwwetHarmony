#!/usr/bin/env python3
"""
Run placeholder enrichments for a list of signals (best-effort).

Usage:
  python scripts/quality/enrich_signals.py --db signals.db 123 456
"""
from __future__ import annotations

import argparse
import json
import os

from ops.quality.db import quality_conn
from ops.quality.enrichment import enrich_signals_best_effort


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("signal_ids", nargs="+", type=int)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        results = enrich_signals_best_effort(conn, signal_ids=args.signal_ids)

    print(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    main()
