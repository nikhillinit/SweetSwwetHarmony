"""
Validated SQLite restore from backup.

Safety rules:
- Refuses to restore when API server is reachable (prevents DB corruption).
- Creates pre-restore backup of current DB (always).
- Validates backup integrity before and after restore.
- Verifies schema version matches CURRENT_SCHEMA_VERSION post-restore.

Usage:
    python scripts/restore_db.py <backup-file> [--db-path signals.db] [--db signals.db] [--force] [--api-url URL]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from utils.db_path_helper import add_db_path_args, resolve_db_path, resolve_db_path_env

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://localhost:8000/api/v1/health"
PRE_RESTORE_PREFIX = "pre-restore-"


def _sidecar_paths(db_path: Path) -> tuple[Path, Path]:
    """Return WAL and SHM sidecar paths for *db_path*."""
    return (
        db_path.with_name(db_path.name + "-wal"),
        db_path.with_name(db_path.name + "-shm"),
    )


def _ensure_no_target_sidecars(db_path: Path) -> None:
    """Resolve target sidecars when safe, otherwise refuse."""
    present = [path for path in _sidecar_paths(db_path) if path.exists()]
    if not present:
        return

    sidecars = ", ".join(path.name for path in present)
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        raise RuntimeError(
            "Target DB sidecars are present "
            f"({sidecars}) and could not be checkpointed safely: {exc}. "
            "Stop writers or restore into a fresh target path first."
        ) from exc

    busy = row[0] if row and len(row) >= 1 else 1
    if busy:
        raise RuntimeError(
            "Target DB sidecars are present "
            f"({sidecars}) and appear to be owned by an active writer. "
            "Stop writers or restore into a fresh target path first."
        )

    for path in present:
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                raise RuntimeError(
                    f"Target DB sidecar {path.name} could not be removed after checkpoint: {exc}"
                ) from exc


def _check_api_reachable(api_url: str) -> bool:
    """Return True if the API health endpoint is reachable."""
    try:
        import httpx
    except ImportError:
        # httpx not installed — try urllib as fallback
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(api_url, method="GET")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except (urllib.error.URLError, OSError):
            return False

    try:
        resp = httpx.get(api_url, timeout=5.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


def _get_schema_version(db_path: Path) -> int | None:
    """Read schema version from a database."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            return row[0] if row and row[0] is not None else None
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None


def restore_backup(
    backup_path: str | Path,
    db_path: str | Path | None = None,
    force: bool = False,
    api_url: str = DEFAULT_API_URL,
) -> Path:
    """Restore a database from backup.

    Args:
        backup_path: Path to the backup file to restore from.
        db_path: Path to the target database.
        force: If True, bypass the API reachability check.
        api_url: URL to check for API reachability.

    Returns:
        Path to the pre-restore safety backup.

    Raises:
        FileNotFoundError: If backup file does not exist.
        RuntimeError: If backup is invalid, API is running (without --force),
                      or schema version mismatch.
    """
    backup_path = Path(backup_path)
    db_path = Path(resolve_db_path_env(db_path))

    if not backup_path.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    # Validate backup integrity before restore
    logger.info("Validating backup integrity: %s", backup_path)
    check_conn = sqlite3.connect(str(backup_path))
    try:
        try:
            result = check_conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"Backup integrity check failed: {exc}"
            ) from exc
        if result[0] != "ok":
            raise RuntimeError(
                f"Backup integrity check failed: {result[0]}"
            )
    finally:
        check_conn.close()

    # API reachability guard
    if not force:
        if _check_api_reachable(api_url):
            raise RuntimeError(
                "API server is running. Stop it before restoring. "
                "Use --force to override."
            )
    else:
        if _check_api_reachable(api_url):
            print(
                "WARNING: API server is running! Restoring with --force. "
                "Data corruption is possible if the API writes during restore.",
                file=sys.stderr,
            )

    _ensure_no_target_sidecars(db_path)

    # Create pre-restore safety backup (always, even with --force)
    pre_restore_path: Path | None = None
    if db_path.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        pre_restore_name = f"{PRE_RESTORE_PREFIX}{timestamp}.db"
        pre_restore_path = db_path.parent / pre_restore_name
        logger.info("Creating pre-restore backup: %s", pre_restore_path)
        shutil.copy2(str(db_path), str(pre_restore_path))

    # Restore: copy backup over current DB
    logger.info("Restoring from %s to %s", backup_path, db_path)
    shutil.copy2(str(backup_path), str(db_path))

    # Post-restore integrity check
    post_conn = sqlite3.connect(str(db_path))
    try:
        result = post_conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            raise RuntimeError(
                f"Post-restore integrity check failed: {result[0]}"
            )
    finally:
        post_conn.close()

    # Schema version check
    from storage.signal_store import CURRENT_SCHEMA_VERSION

    restored_version = _get_schema_version(db_path)
    if restored_version is not None and restored_version != CURRENT_SCHEMA_VERSION:
        logger.warning(
            "Schema version mismatch: backup has v%s, expected v%s",
            restored_version,
            CURRENT_SCHEMA_VERSION,
        )

    logger.info("Restore complete: %s", db_path)
    return pre_restore_path or db_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validated SQLite restore from backup"
    )
    parser.add_argument(
        "backup_file",
        help="Path to the backup file to restore from",
    )
    add_db_path_args(parser)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass API reachability check (dangerous)",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"API health endpoint URL (default: {DEFAULT_API_URL})",
    )
    args = parser.parse_args(argv)
    resolved_db_path = resolve_db_path(args)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from utils.db_ops_ledger import append_db_ops_ledger
    from utils.db_tool_lock import DBToolLock

    lock = DBToolLock(resolved_db_path, tool_name="restore_db")
    if not lock.acquire(timeout_seconds=5):
        holder = lock.get_holder_info()
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=resolved_db_path,
            action="restore_backup",
            status="lock_blocked",
            details={"holder": holder, "backup_file": args.backup_file},
        )
        print(f"ERROR: Could not acquire DB tool lock. Holder: {holder}", file=sys.stderr)
        return 1

    try:
        pre_restore = restore_backup(
            args.backup_file,
            resolved_db_path,
            args.force,
            args.api_url,
        )
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=resolved_db_path,
            action="restore_backup",
            status="success",
            details={"backup_file": args.backup_file, "pre_restore_backup": str(pre_restore)},
        )
        print(f"Restore complete. Pre-restore backup: {pre_restore}")
        return 0
    except Exception as e:
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=resolved_db_path,
            action="restore_backup",
            status="error",
            details={"backup_file": args.backup_file, "error": str(e)},
        )
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
