#!/usr/bin/env python3
"""Record a manual or external DB operation in the repo-local DB-ops ledger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_path_helper import resolve_db_path_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="Path to SQLite database")
    parser.add_argument("--tool-name", default="manual_external", help="Operator-supplied tool or actor name")
    parser.add_argument("--action", required=True, help="Short action name, e.g. manual_checkpoint")
    parser.add_argument("--status", default="noted", help="Status label to record")
    parser.add_argument("--note", required=True, help="Human-readable note for the operation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = resolve_db_path_env(args.db_path)
    path = append_db_ops_ledger(
        tool_name=args.tool_name,
        db_path=db_path,
        action=args.action,
        status=args.status,
        details={"note": args.note, "source": "db_ops_note"},
    )
    print(f"Recorded DB ops note in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
