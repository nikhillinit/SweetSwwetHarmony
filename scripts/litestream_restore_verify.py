"""Restore a Litestream replica to a temp DB and verify SQLite invariants."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str] | None]


class RestoreVerificationError(RuntimeError):
    """Raised when the restore command or restored database is unsafe."""


def run_litestream_command(
    command: Sequence[str],
    *,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str] | None:
    command_list = [str(part) for part in command]
    if any(part == "verify" for part in command_list[1:]):
        raise RestoreVerificationError(
            "the upstream verify subcommand is not supported; restore to a "
            "temp DB and validate SQLite invariants instead."
        )

    if runner is not None:
        result = runner(command_list)
    else:
        result = subprocess.run(
            command_list,
            capture_output=True,
            check=False,
            text=True,
        )

    if result is not None and result.returncode != 0:
        raise RestoreVerificationError(
            "litestream restore failed: "
            f"{result.stderr.strip() or result.stdout.strip() or result.returncode}"
        )
    return result


def _fetch_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.OperationalError as exc:
        raise RestoreVerificationError(
            f"schema_migrations check failed: {exc}"
        ) from exc
    if row is None or row[0] is None:
        raise RestoreVerificationError("schema_migrations has no applied version")
    return int(row[0])


def _fetch_signal_count(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) FROM signals").fetchone()
    except sqlite3.OperationalError as exc:
        raise RestoreVerificationError(f"signals table check failed: {exc}") from exc
    return int(row[0])


def verify_restored_database(
    db_path: str | Path,
    *,
    min_signals: int,
    expected_schema_version: int | None = None,
) -> dict[str, Any]:
    restored_path = Path(db_path)
    if not restored_path.exists():
        raise RestoreVerificationError(f"restored database not found: {restored_path}")

    try:
        conn = sqlite3.connect(str(restored_path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity_check = str(row[0]) if row else "missing"
            if integrity_check != "ok":
                raise RestoreVerificationError(
                    f"integrity check failed: {integrity_check}"
                )

            schema_version = _fetch_schema_version(conn)
            if (
                expected_schema_version is not None
                and schema_version != expected_schema_version
            ):
                raise RestoreVerificationError(
                    "schema version mismatch: "
                    f"restored={schema_version}, expected={expected_schema_version}"
                )

            signal_count = _fetch_signal_count(conn)
            if signal_count < min_signals:
                raise RestoreVerificationError(
                    "signal lower bound failed: "
                    f"restored={signal_count}, minimum={min_signals}"
                )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise RestoreVerificationError(f"integrity check failed: {exc}") from exc

    return {
        "db_path": str(restored_path),
        "integrity_check": "ok",
        "schema_version": schema_version,
        "signal_count": signal_count,
        "min_signals": min_signals,
    }


def restore_and_verify(
    *,
    replica_url: str,
    restore_path: str | Path,
    min_signals: int,
    expected_schema_version: int | None = None,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    destination = Path(restore_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    run_litestream_command(
        [
            "litestream",
            "restore",
            "-if-replica-exists",
            "-o",
            str(destination),
            replica_url,
        ],
        runner=runner,
    )
    return verify_restored_database(
        destination,
        min_signals=min_signals,
        expected_schema_version=expected_schema_version,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore a Litestream replica to a temp DB and verify it."
    )
    parser.add_argument("--replica-url", required=True, help="Litestream replica URL.")
    parser.add_argument(
        "--restore-path",
        required=True,
        help="Temporary SQLite path to restore into.",
    )
    parser.add_argument(
        "--min-signals",
        type=int,
        default=1,
        help="Minimum acceptable restored signals row count.",
    )
    parser.add_argument(
        "--expected-schema-version",
        type=int,
        default=None,
        help="Optional exact schema_migrations max(version) to require.",
    )
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Optional path for JSON restore verification summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = restore_and_verify(
            replica_url=args.replica_url,
            restore_path=args.restore_path,
            min_signals=args.min_signals,
            expected_schema_version=args.expected_schema_version,
        )
    except RestoreVerificationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.summary_out:
        summary_path = Path(args.summary_out)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
