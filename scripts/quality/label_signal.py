#!/usr/bin/env python3
"""
Manual labeling helper.

Usage:
  python scripts/quality/label_signal.py --db signals.db 123 FP --reason "spam" --by alice
"""
from __future__ import annotations

import argparse
import json
import os

from ops.quality.db import quality_conn
from ops.quality.labels import label_signal_manual
from utils.db_path_helper import resolve_db_path_env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--by", dest="created_by", default=os.getenv("USER", "human"))
    ap.add_argument("--reason", default=None)
    ap.add_argument("--notes", default=None)
    ap.add_argument("signal_id", type=int)
    ap.add_argument("label", choices=["TP", "FP", "UNSURE", "ADJ"])
    args = ap.parse_args()
    args.db = resolve_db_path_env(args.db)

    with quality_conn(args.db) as conn:
        feedback_id, upsert = label_signal_manual(
            conn,
            signal_id=args.signal_id,
            label=args.label,
            created_by=args.created_by,
            reason=args.reason,
            notes=args.notes,
        )

    print(json.dumps({"feedback_id": feedback_id, "signal_id": upsert.signal_id, "label": upsert.human_label}, indent=2))


if __name__ == "__main__":
    main()
