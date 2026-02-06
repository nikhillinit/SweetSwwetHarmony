#!/usr/bin/env python3
"""
Run keyword + LLM thesis classification for a single signal and store result.

Usage:
  python scripts/quality/thesis_classify.py --db signals.db 123 --model gemini-2.0-flash
"""
from __future__ import annotations

import argparse
import json
import os

from ops.quality.db import quality_conn
from ops.quality.thesis import classify_signal_llm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DISCOVERY_DB_PATH", "signals.db"))
    ap.add_argument("--model", default="gemini-2.0-flash")
    ap.add_argument("--prompt-version", default="quality-ops-v1")
    ap.add_argument("signal_id", type=int)
    args = ap.parse_args()

    with quality_conn(args.db) as conn:
        r = classify_signal_llm(conn, signal_id=args.signal_id, model=args.model, prompt_version=args.prompt_version)

    print(json.dumps(r.__dict__, indent=2))


if __name__ == "__main__":
    main()
