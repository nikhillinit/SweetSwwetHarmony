"""Snapshot helpers for proving process dry-run database immutability."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TableSnapshot:
    row_count: int
    content_hash: str


@dataclass(frozen=True)
class CompareDryRunResult:
    lane: str
    command: str
    command_returncode: int
    changed_tables: list[str]
    new_tables: list[str]
    removed_tables: list[str]
    before: Dict[str, TableSnapshot]
    after: Dict[str, TableSnapshot]
    stdout: str
    stderr: str


_BASELINE_ENV: Dict[str, str] = {
    "WARMUP_SUPPRESSION_CACHE": "false",
    "USE_GATING": "false",
    "USE_ENTITIES": "false",
    "USE_ASSET_STORE": "false",
    "USE_FOUNDER_SCORING": "false",
    "USE_VELOCITY_TRACKING": "false",
    "USE_THESIS_FILTER": "true",
    "USE_COMPETITOR_DETECTION": "false",
    "ENABLE_EXIT_PREDICTOR": "false",
    "ENABLE_INVESTOR_MATCHING": "false",
    "USE_PHASE_G_IDENTITY_RESOLUTION": "false",
    "USE_SHADOW_ENTITY_RESOLUTION": "false",
    "USE_CLAIM_FACTS": "false",
    "ENABLE_FUNCTIONAL_SCHEMA": "false",
    "USE_THIN_FILES": "false",
    "LLM_THESIS_MODE": "off",
}

_LANE_ENV_OVERRIDES: Dict[str, Dict[str, str]] = {
    "baseline": {},
    "claim_facts": {"USE_CLAIM_FACTS": "true"},
    "entities": {"USE_ENTITIES": "true"},
    "phase_g_identity_resolution": {"USE_PHASE_G_IDENTITY_RESOLUTION": "true"},
    "shadow_entity_resolution": {
        "USE_PHASE_G_IDENTITY_RESOLUTION": "true",
        "USE_SHADOW_ENTITY_RESOLUTION": "true",
    },
    "exit_predictor": {"ENABLE_EXIT_PREDICTOR": "true"},
    "investor_matching": {"ENABLE_INVESTOR_MATCHING": "true"},
    "founder_scoring": {"USE_FOUNDER_SCORING": "true"},
    "functional_schema": {"ENABLE_FUNCTIONAL_SCHEMA": "true"},
    "combined_high_risk": {
        "USE_ENTITIES": "true",
        "USE_FOUNDER_SCORING": "true",
        "ENABLE_EXIT_PREDICTOR": "true",
        "ENABLE_INVESTOR_MATCHING": "true",
        "USE_PHASE_G_IDENTITY_RESOLUTION": "true",
        "USE_SHADOW_ENTITY_RESOLUTION": "true",
        "USE_CLAIM_FACTS": "true",
        "ENABLE_FUNCTIONAL_SCHEMA": "true",
    },
}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if isinstance(value, sqlite3.Row):
        return {key: _normalize_value(value[key]) for key in value.keys()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(val) for key, val in sorted(value.items())}
    return value


def snapshot_tables(db_path: str | Path) -> Dict[str, TableSnapshot]:
    """Capture row counts and deterministic content hashes for user tables."""
    snapshots: Dict[str, TableSnapshot] = {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        for row in rows:
            table_name = row["name"]
            table_rows = conn.execute(f'SELECT * FROM "{table_name}"').fetchall()
            normalized_rows = [
                json.dumps(_normalize_value(record), sort_keys=True, separators=(",", ":"))
                for record in table_rows
            ]
            normalized_rows.sort()
            digest = hashlib.sha256("\n".join(normalized_rows).encode("utf-8")).hexdigest()
            snapshots[table_name] = TableSnapshot(
                row_count=len(table_rows),
                content_hash=digest,
            )
    finally:
        conn.close()
    return snapshots


def _build_env(lane: str | None) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(_BASELINE_ENV)
    if lane:
        env.update(_LANE_ENV_OVERRIDES.get(lane, {}))
    return env


def compare_dry_run(
    *,
    db_path: str | Path,
    command: str,
    lane: str | None = None,
) -> CompareDryRunResult:
    """Run a process dry-run command and compare all persistent tables."""
    before = snapshot_tables(db_path)
    completed = subprocess.run(
        command,
        shell=True,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        env=_build_env(lane),
    )
    after = snapshot_tables(db_path)

    before_tables = set(before)
    after_tables = set(after)
    new_tables = sorted(after_tables - before_tables)
    removed_tables = sorted(before_tables - after_tables)

    changed_tables = sorted(
        table
        for table in before_tables & after_tables
        if before[table] != after[table]
    )
    changed_tables.extend(new_tables)
    changed_tables.extend(removed_tables)

    return CompareDryRunResult(
        lane=lane or "baseline",
        command=command,
        command_returncode=completed.returncode,
        changed_tables=sorted(changed_tables),
        new_tables=new_tables,
        removed_tables=removed_tables,
        before=before,
        after=after,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _cmd_compare_dry_run(args: argparse.Namespace) -> int:
    result = compare_dry_run(
        db_path=args.db_path,
        command=args.command,
        lane=args.lane,
    )
    print(json.dumps(
        {
            "lane": result.lane,
            "command": result.command,
            "command_returncode": result.command_returncode,
            "changed_tables": result.changed_tables,
            "new_tables": result.new_tables,
            "removed_tables": result.removed_tables,
            "before": {key: asdict(value) for key, value in result.before.items()},
            "after": {key: asdict(value) for key, value in result.after.items()},
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
        indent=2,
        sort_keys=True,
    ))
    if result.command_returncode != 0:
        return result.command_returncode
    return 1 if result.changed_tables else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    compare_parser = subparsers.add_parser(
        "compare-dry-run",
        help="Run a dry-run command and diff all persistent user tables.",
    )
    compare_parser.add_argument("--db-path", required=True, help="SQLite DB path to snapshot.")
    compare_parser.add_argument("--command", required=True, help="Command to execute.")
    compare_parser.add_argument(
        "--lane",
        default="baseline",
        help="Feature lane name from the approved dry-run readonly plan.",
    )
    compare_parser.set_defaults(handler=_cmd_compare_dry_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
