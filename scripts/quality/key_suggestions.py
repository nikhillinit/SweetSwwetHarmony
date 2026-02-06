#!/usr/bin/env python3
"""
Suggest strengthening weak name_loc canonical keys by extracting domains from raw_data.

Usage:
  python scripts/quality/key_suggestions.py --db signals.db --min-signals 5 --out /tmp/key_suggestions.md
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ops.quality.db import quality_conn
from ops.quality.keys import suggest_key_strengthening, suggestions_to_markdown


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--min-signals", type=int, default=5)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--fp-only", action="store_true", default=False)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        suggestions = suggest_key_strengthening(conn, min_signals=args.min_signals, limit=args.limit, fp_only=bool(args.fp_only))
        md = suggestions_to_markdown(suggestions)

    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(json.dumps({"out": args.out, "suggestions": len(suggestions)}, indent=2))
    else:
        print(md)


if __name__ == "__main__":
    main()
