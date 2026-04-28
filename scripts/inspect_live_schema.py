"""Day 1.5 schema probe (read-only).

Validates the live ``signals.db`` against the Phase 2 schema contract at
``.omx/wave6/live_schema_contract.json``. Writes a structured JSON report and
a human-readable Markdown summary to ``.omx/wave6/live_schema_report.{json,md}``.

Exit codes:

* ``0`` — contract satisfied; Day 2 work is unblocked.
* ``2`` — contract violation: at least one required table or column is
  missing from the live database. Inspect the report for details.
* ``3`` — contract or database file could not be read.

The probe is strictly read-only against the database — it issues only
``PRAGMA table_info(...)`` and ``SELECT name FROM sqlite_master`` queries
and never writes, opens for write, or attaches additional databases.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

DEFAULT_CONTRACT_PATH = Path(".omx") / "wave6" / "live_schema_contract.json"
DEFAULT_OUT_DIR = Path(".omx") / "wave6"
DEFAULT_DB_PATH = "signals.db"

EXIT_OK = 0
EXIT_CONTRACT_VIOLATION = 2
EXIT_LOAD_ERROR = 3


def load_contract(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load the schema contract JSON."""
    with Path(path).open("r", encoding="utf-8") as fh:
        contract = json.load(fh)
    if not isinstance(contract, dict):
        raise ValueError(f"Contract at {path} must be a JSON object")
    if "required_tables" not in contract:
        raise ValueError(f"Contract at {path} missing 'required_tables'")
    return contract


def _table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    cursor = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def inspect_database(
    db_path: str | os.PathLike[str],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect the live database against the contract.

    The function issues only schema-introspection queries (``sqlite_master`` /
    ``PRAGMA table_info``) and opens the database read-only via the URI
    ``file:...?mode=ro`` form.
    """
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        return {
            "ok": False,
            "error": "database_not_found",
            "db_path": str(db_path_obj),
            "tables": {},
            "missing_tables": [],
            "missing_columns": {},
        }

    required_tables = contract.get("required_tables", {})
    if not isinstance(required_tables, Mapping):
        raise ValueError("Contract 'required_tables' must be a mapping")

    con = sqlite3.connect(f"file:{db_path_obj}?mode=ro", uri=True)
    try:
        tables: dict[str, dict[str, Any]] = {}
        missing_tables: list[str] = []
        missing_columns: dict[str, list[str]] = {}

        for table_name, spec in required_tables.items():
            required_cols = list((spec or {}).get("required_columns", []))
            if not _table_exists(con, table_name):
                missing_tables.append(table_name)
                tables[table_name] = {
                    "status": "missing_table",
                    "required_columns": required_cols,
                    "actual_columns": [],
                    "missing_columns": list(required_cols),
                }
                continue

            actual = _table_columns(con, table_name)
            actual_set = set(actual)
            missing = [c for c in required_cols if c not in actual_set]
            if missing:
                missing_columns[table_name] = missing
                tables[table_name] = {
                    "status": "missing_columns",
                    "required_columns": required_cols,
                    "actual_columns": actual,
                    "missing_columns": missing,
                }
            else:
                tables[table_name] = {
                    "status": "ok",
                    "required_columns": required_cols,
                    "actual_columns": actual,
                    "missing_columns": [],
                }
    finally:
        con.close()

    ok = not missing_tables and not missing_columns
    return {
        "ok": ok,
        "db_path": str(db_path_obj),
        "tables": tables,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


def render_markdown_report(
    report: Mapping[str, Any],
    *,
    db_path: str | os.PathLike[str],
    contract_path: str | os.PathLike[str],
    contract_version: Optional[int] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Render a Markdown summary of the report."""
    lines: list[str] = []
    verdict = "PASS" if report.get("ok") else "FAIL"
    lines.append(f"# Live Schema Probe — {verdict}")
    lines.append("")
    if generated_at:
        lines.append(f"- **Generated at:** {generated_at}")
    lines.append(f"- **Database:** `{db_path}`")
    lines.append(f"- **Contract:** `{contract_path}`")
    if contract_version is not None:
        lines.append(f"- **Contract version:** {contract_version}")
    lines.append(f"- **Verdict:** {'✅ OK' if report.get('ok') else '❌ Contract violation'}")
    lines.append("")

    if report.get("error"):
        lines.append(f"**Error:** `{report['error']}`")
        lines.append("")

    lines.append("## Table-by-table status")
    lines.append("")
    lines.append("| Table | Status | Missing columns |")
    lines.append("|---|---|---|")
    for name in sorted(report.get("tables", {})):
        info = report["tables"][name]
        status = info.get("status", "unknown")
        missing = ", ".join(info.get("missing_columns", [])) or "—"
        marker = (
            "✅" if status == "ok"
            else ("❌" if status in {"missing_table", "missing_columns"} else "⚠️")
        )
        lines.append(f"| `{name}` | {marker} {status} | {missing} |")

    if report.get("missing_tables"):
        lines.append("")
        lines.append("## Missing tables")
        for t in report["missing_tables"]:
            lines.append(f"- `{t}`")
    if report.get("missing_columns"):
        lines.append("")
        lines.append("## Missing columns")
        for t, cols in sorted(report["missing_columns"].items()):
            lines.append(f"- `{t}`: {', '.join(cols)}")

    return "\n".join(lines) + "\n"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="inspect-live-schema",
        description=(
            "Day 1.5 schema probe. Validates signals.db against the Phase 2 "
            "schema contract and writes JSON + Markdown reports."
        ),
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DISCOVERY_DB_PATH", DEFAULT_DB_PATH),
        help="Path to signals.db (default: $DISCOVERY_DB_PATH or signals.db).",
    )
    parser.add_argument(
        "--contract",
        default=str(DEFAULT_CONTRACT_PATH),
        help="Path to schema contract JSON.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Directory to write live_schema_report.{json,md}.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    contract_path = Path(args.contract)
    out_dir = Path(args.out_dir)

    try:
        contract = load_contract(contract_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"failed to load contract {contract_path}: {exc}\n")
        return EXIT_LOAD_ERROR

    report = inspect_database(args.db, contract)
    contract_version = contract.get("version")
    generated_at = _utc_now_iso()

    payload = {
        "schema_version": 1,
        "contract_version": contract_version,
        "generated_at": generated_at,
        "db_path": str(Path(args.db)),
        "contract_path": str(contract_path),
        **report,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    json_out = out_dir / "live_schema_report.json"
    md_out = out_dir / "live_schema_report.md"
    json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_out.write_text(
        render_markdown_report(
            report,
            db_path=args.db,
            contract_path=contract_path,
            contract_version=contract_version,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    if report.get("error") == "database_not_found":
        sys.stderr.write(f"database not found: {args.db}\n")
        return EXIT_LOAD_ERROR

    if not report.get("ok"):
        return EXIT_CONTRACT_VIOLATION
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
