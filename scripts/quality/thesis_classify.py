#!/usr/bin/env python3
"""
Run keyword + LLM thesis classification for a single signal and store result.

Usage:
  python scripts/quality/thesis_classify.py --db signals.db 123 --model gemini-3.5-flash
"""
from __future__ import annotations

import argparse
import json

from ops.quality.db import quality_conn
from ops.quality.thesis import classify_signal_llm
from utils.db_path_helper import resolve_db_path_env
from utils.thesis_llm_model import DEFAULT_THESIS_LLM_MODEL, THESIS_LLM_MODEL_ENV


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    model_help = f"Defaults to ${THESIS_LLM_MODEL_ENV} or {DEFAULT_THESIS_LLM_MODEL}"
    ap.add_argument("--model", default=None, help=model_help)
    ap.add_argument("--prompt-version", default="quality-ops-v1")
    ap.add_argument("signal_id", type=int)
    args = ap.parse_args()
    args.db = resolve_db_path_env(args.db)

    with quality_conn(args.db) as conn:
        r = classify_signal_llm(conn, signal_id=args.signal_id, model=args.model, prompt_version=args.prompt_version)

    print(json.dumps(r.__dict__, indent=2))


if __name__ == "__main__":
    main()
