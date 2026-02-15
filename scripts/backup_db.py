"""
Online SQLite backup with rotation.

Uses sqlite3.Connection.backup() for WAL-safe online copies.
Validates backup integrity with PRAGMA integrity_check.
Rotates old backups to keep at most --retain copies.

Usage:
    python scripts/backup_db.py [--db signals.db] [--out-dir backups/] [--retain 7]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB = "signals.db"
DEFAULT_OUT_DIR = "backups"
DEFAULT_RETAIN = 7
BACKUP_PREFIX = "signals-"
BACKUP_SUFFIX = ".db"


def create_backup(
    db_path: str | Path,
    out_dir: str | Path,
    retain: int = DEFAULT_RETAIN,
) -> Path:
    """Create an online SQLite backup with rotation.

    Args:
        db_path: Path to the source database.
        out_dir: Directory to store backups.
        retain: Maximum number of backup copies to keep.

    Returns:
        Path to the created backup file.

    Raises:
        FileNotFoundError: If source DB does not exist.
        RuntimeError: If backup integrity check fails.
    """
    db_path = Path(db_path)
    out_dir = Path(out_dir)

    if not db_path.exists():
        raise FileNotFoundError(f"Source database not found: {db_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_name = f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"
    backup_path = out_dir / backup_name

    logger.info("Starting backup: %s -> %s", db_path, backup_path)

    # Online backup using sqlite3.Connection.backup()
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Validate backup integrity
    check_conn = sqlite3.connect(str(backup_path))
    try:
        result = check_conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            raise RuntimeError(
                f"Backup integrity check failed: {result[0]}"
            )
    finally:
        check_conn.close()

    logger.info("Backup created and verified: %s", backup_path)

    # Rotate old backups
    _rotate_backups(out_dir, retain)

    return backup_path


def _rotate_backups(out_dir: Path, retain: int) -> list[Path]:
    """Remove oldest backups exceeding retention limit.

    Returns list of removed files.
    """
    backups = sorted(
        out_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"),
        key=lambda p: p.name,
    )

    removed: list[Path] = []
    while len(backups) > retain:
        oldest = backups.pop(0)
        oldest.unlink()
        removed.append(oldest)
        logger.info("Rotated old backup: %s", oldest)

    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Online SQLite backup with rotation"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Source database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"Backup output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=DEFAULT_RETAIN,
        help=f"Number of backups to retain (default: {DEFAULT_RETAIN})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        backup_path = create_backup(args.db, args.out_dir, args.retain)
        print(f"Backup created: {backup_path}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
