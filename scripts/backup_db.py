"""
Online SQLite backup with rotation.

Uses sqlite3.Connection.backup() for WAL-safe online copies.
Validates backup integrity with PRAGMA integrity_check.
Rotates old backups to keep at most --retain copies.

Usage:
    python scripts/backup_db.py [--db-path signals.db] [--out-dir backups/] [--retain 7]
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from utils.db_path_helper import add_db_path_args, resolve_db_path

logger = logging.getLogger(__name__)

DEFAULT_OUT_DIR = "backups"
DEFAULT_RETAIN = 7
BACKUP_PREFIX = "signals-"
BACKUP_SUFFIX = ".db"
LOCK_TIMEOUT_SECONDS = 5


class BackupError(RuntimeError):
    """Backup failure with partial evidence for DB ops ledger rows."""

    def __init__(
        self,
        message: str,
        *,
        backup_path: Path | None = None,
        integrity_check: str | None = None,
    ) -> None:
        super().__init__(message)
        self.backup_path = backup_path
        self.integrity_check = integrity_check


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

    if retain < 1:
        raise ValueError("retain must be at least 1")

    if not db_path.exists():
        raise FileNotFoundError(f"Source database not found: {db_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_name = f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"
    backup_path = out_dir / backup_name

    logger.info("Starting backup: %s -> %s", db_path, backup_path)

    # Online backup using sqlite3.Connection.backup()
    try:
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(backup_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        backup_path.unlink(missing_ok=True)
        raise BackupError(
            f"Backup copy failed: {exc}",
            backup_path=backup_path,
        ) from exc

    # Validate backup integrity
    check_conn = sqlite3.connect(str(backup_path))
    try:
        try:
            result = check_conn.execute("PRAGMA integrity_check").fetchone()
            integrity_result = result[0] if result else "no_result"
        except sqlite3.DatabaseError as exc:
            integrity_result = str(exc)
            backup_path.unlink(missing_ok=True)
            raise BackupError(
                f"Backup integrity check failed: {exc}",
                backup_path=backup_path,
                integrity_check=integrity_result,
            ) from exc

        if integrity_result != "ok":
            backup_path.unlink(missing_ok=True)
            raise BackupError(
                f"Backup integrity check failed: {integrity_result}",
                backup_path=backup_path,
                integrity_check=integrity_result,
            )
    finally:
        check_conn.close()

    logger.info("Backup created and verified: %s", backup_path)

    # Rotate old backups
    try:
        _rotate_backups(out_dir, retain)
    except OSError as exc:
        raise BackupError(
            f"Backup rotation failed: {exc}",
            backup_path=backup_path,
            integrity_check="ok",
        ) from exc

    return backup_path


def _rotate_backups(out_dir: Path, retain: int) -> list[Path]:
    """Remove oldest backups exceeding retention limit.

    Returns list of removed files.
    """
    if retain < 1:
        raise ValueError("retain must be at least 1")

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


def _path_for_ledger(path: Path | None) -> str | None:
    return str(path.resolve()) if path else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Online SQLite backup with rotation"
    )
    add_db_path_args(parser)
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
    resolved_db_path = resolve_db_path(args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from utils.db_ops_ledger import append_db_ops_ledger
    from utils.db_tool_lock import DBToolLock

    if args.retain < 1:
        append_db_ops_ledger(
            tool_name="backup_db",
            db_path=resolved_db_path,
            action="create_backup",
            status="error",
            details={
                "out_dir": args.out_dir,
                "retain": args.retain,
                "error": "retain must be at least 1",
            },
        )
        print("ERROR: retain must be at least 1", file=sys.stderr)
        return 1

    lock = DBToolLock(resolved_db_path, tool_name="backup_db")
    if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
        holder = lock.get_holder_info()
        append_db_ops_ledger(
            tool_name="backup_db",
            db_path=resolved_db_path,
            action="create_backup",
            status="lock_blocked",
            details={
                "holder": holder,
                "out_dir": args.out_dir,
                "retain": args.retain,
            },
        )
        print(f"ERROR: Could not acquire DB tool lock. Holder: {holder}", file=sys.stderr)
        return 1

    try:
        backup_path = create_backup(resolved_db_path, args.out_dir, args.retain)
        retained_count = len(
            list(Path(args.out_dir).glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"))
        )
        append_db_ops_ledger(
            tool_name="backup_db",
            db_path=resolved_db_path,
            action="create_backup",
            status="success",
            details={
                "backup_path": _path_for_ledger(backup_path),
                "out_dir": args.out_dir,
                "retain": args.retain,
                "retained_count": retained_count,
                "integrity_check": "ok",
            },
        )
        print(f"Backup created: {backup_path}")
        return 0
    except Exception as e:
        backup_path = (
            _path_for_ledger(e.backup_path)
            if isinstance(e, BackupError)
            else None
        )
        integrity_check = e.integrity_check if isinstance(e, BackupError) else None
        append_db_ops_ledger(
            tool_name="backup_db",
            db_path=resolved_db_path,
            action="create_backup",
            status="error",
            details={
                "backup_path": backup_path,
                "out_dir": args.out_dir,
                "retain": args.retain,
                "integrity_check": integrity_check,
                "error": str(e),
            },
        )
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
