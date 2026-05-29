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
import hashlib
import logging
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.db_path_helper import add_db_path_args, resolve_db_path, resolve_db_path_env
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLockError

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://localhost:8000/api/v1/health"
PRE_RESTORE_PREFIX = "pre-restore-"
LOCK_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class RestoreBackupResult:
    backup_path: Path
    db_path: Path
    pre_restore_backup: Path
    target_sha256_before: str | None
    target_sha256_after: str
    backup_sha256: str
    integrity_check: str
    schema_version: int | None
    db_ops_ledger_status: str
    lock_path: Path


class RestoreError(DBToolError):
    """Restore failure with partial evidence for DB ops ledger rows."""

    def __init__(
        self,
        message: str,
        *,
        pre_restore_backup: Path | None = None,
        sidecar_state: dict | list | str | None = None,
        integrity_check: str | None = None,
        partial_evidence: dict[str, Any] | None = None,
    ) -> None:
        evidence = {
            "pre_restore_backup": (
                str(pre_restore_backup) if pre_restore_backup else None
            ),
            "sidecar_state": sidecar_state,
            "integrity_check": integrity_check,
        }
        evidence.update(partial_evidence or {})
        super().__init__(
            message,
            partial_evidence=evidence,
        )
        self.pre_restore_backup = pre_restore_backup
        self.sidecar_state = sidecar_state
        self.integrity_check = integrity_check


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
    sidecar_state: dict = {"present": [path.name for path in present]}
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        raise RestoreError(
            "Target DB sidecars are present "
            f"({sidecars}) and could not be checkpointed safely: {exc}. "
            "Stop writers or restore into a fresh target path first.",
            sidecar_state={**sidecar_state, "checkpoint_error": str(exc)},
        ) from exc

    busy = row[0] if row and len(row) >= 1 else 1
    if busy:
        raise RestoreError(
            "Target DB sidecars are present "
            f"({sidecars}) and appear to be owned by an active writer. "
            "Stop writers or restore into a fresh target path first.",
            sidecar_state={**sidecar_state, "wal_checkpoint_busy": busy},
        )

    for path in present:
        if path.exists():
            try:
                path.unlink()
            except OSError as exc:
                raise RestoreError(
                    f"Target DB sidecar {path.name} could not be removed "
                    f"after checkpoint: {exc}",
                    sidecar_state={**sidecar_state, "unlink_error": str(exc)},
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_if_exists(path: Path) -> str | None:
    return _sha256_file(path) if path.exists() else None


def _sqlite_integrity_check(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "missing"
    finally:
        conn.close()


def _restore_ledger_details(
    *,
    backup_path: Path,
    lock_path: Path,
    status: str,
    pre_restore_backup: Path | None = None,
    target_sha256_before: str | None = None,
    target_sha256_after: str | None = None,
    backup_sha256: str | None = None,
    integrity_check: str | None = None,
    schema_version: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = {
        "backup_file": str(backup_path),
        "backup_path": str(backup_path),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "target_sha256_before": target_sha256_before,
        "target_sha256_after": target_sha256_after,
        "backup_sha256": backup_sha256,
        "integrity_check": integrity_check,
        "schema_version": schema_version,
        "db_ops_ledger_status": status,
        "lock_path": str(lock_path),
    }
    details.update(extra or {})
    details["db_ops_ledger_status"] = status
    return details


def restore_backup_with_lock_and_ledger(
    backup_path: str | Path,
    db_path: str | Path | None = None,
    force: bool = False,
    api_url: str = DEFAULT_API_URL,
    *,
    lock_timeout_seconds: int = LOCK_TIMEOUT_SECONDS,
) -> RestoreBackupResult:
    """Restore a backup while owning DB tool lock and DB ops ledger writes."""

    from utils.db_ops_ledger import append_db_ops_ledger
    from utils.db_tool_lock import DBToolLock

    backup = Path(backup_path)
    resolved_db_path = Path(resolve_db_path_env(db_path)).resolve()
    lock = DBToolLock(resolved_db_path, tool_name="restore_db")
    lock_path = lock.lock_path
    target_sha256_before = _sha256_if_exists(resolved_db_path)
    backup_sha256 = _sha256_if_exists(backup)

    if not lock.acquire(timeout_seconds=lock_timeout_seconds):
        holder = lock.get_holder_info()
        details = _restore_ledger_details(
            backup_path=backup,
            lock_path=lock_path,
            status="lock_blocked",
            target_sha256_before=target_sha256_before,
            backup_sha256=backup_sha256,
            extra={"holder": holder},
        )
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=str(resolved_db_path),
            action="restore_backup",
            status="lock_blocked",
            details=details,
        )
        raise RestoreError(
            f"Could not acquire DB tool lock. Holder: {holder}",
            partial_evidence=details,
        )

    try:
        pre_restore = restore_backup(
            backup,
            resolved_db_path,
            force,
            api_url,
            _lock=lock,
        )
        target_sha256_after = _sha256_file(resolved_db_path)
        integrity_check = _sqlite_integrity_check(resolved_db_path)
        schema_version = _get_schema_version(resolved_db_path)
        result = RestoreBackupResult(
            backup_path=backup,
            db_path=resolved_db_path,
            pre_restore_backup=pre_restore,
            target_sha256_before=target_sha256_before,
            target_sha256_after=target_sha256_after,
            backup_sha256=backup_sha256 or _sha256_file(backup),
            integrity_check=integrity_check,
            schema_version=schema_version,
            db_ops_ledger_status="success",
            lock_path=lock_path,
        )
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=str(resolved_db_path),
            action="restore_backup",
            status="success",
            details=_restore_ledger_details(
                backup_path=result.backup_path,
                lock_path=result.lock_path,
                status=result.db_ops_ledger_status,
                pre_restore_backup=result.pre_restore_backup,
                target_sha256_before=result.target_sha256_before,
                target_sha256_after=result.target_sha256_after,
                backup_sha256=result.backup_sha256,
                integrity_check=result.integrity_check,
                schema_version=result.schema_version,
            ),
        )
        return result
    except RestoreError as exc:
        details = _restore_ledger_details(
            backup_path=backup,
            lock_path=lock_path,
            status="error",
            target_sha256_before=target_sha256_before,
            target_sha256_after=_sha256_if_exists(resolved_db_path),
            backup_sha256=backup_sha256,
            extra=exc.partial_evidence,
        )
        details["error"] = str(exc)
        exc.partial_evidence = details
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=str(resolved_db_path),
            action="restore_backup",
            status="error",
            details=details,
        )
        raise
    except Exception as exc:
        details = _restore_ledger_details(
            backup_path=backup,
            lock_path=lock_path,
            status="error",
            target_sha256_before=target_sha256_before,
            target_sha256_after=_sha256_if_exists(resolved_db_path),
            backup_sha256=backup_sha256,
        )
        details["error"] = str(exc)
        append_db_ops_ledger(
            tool_name="restore_db",
            db_path=str(resolved_db_path),
            action="restore_backup",
            status="error",
            details=details,
        )
        raise RestoreError(str(exc), partial_evidence=details) from exc
    finally:
        lock.release()


def restore_backup(
    backup_path: str | Path,
    db_path: str | Path | None = None,
    force: bool = False,
    api_url: str = DEFAULT_API_URL,
    *,
    _lock: Any | None = None,
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
            raise RestoreError(
                f"Backup integrity check failed: {exc}",
                integrity_check=str(exc),
            ) from exc
        if result[0] != "ok":
            raise RestoreError(
                f"Backup integrity check failed: {result[0]}",
                integrity_check=result[0],
            )
    finally:
        check_conn.close()

    # API reachability guard
    if not force:
        if _check_api_reachable(api_url):
            raise RestoreError(
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
    _assert_restore_lock_healthy(_lock)
    shutil.copy2(str(backup_path), str(db_path))

    # Post-restore integrity check
    post_conn = sqlite3.connect(str(db_path))
    try:
        result = post_conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            raise RestoreError(
                f"Post-restore integrity check failed: {result[0]}",
                pre_restore_backup=pre_restore_path,
                integrity_check=result[0],
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


def _assert_restore_lock_healthy(lock: Any | None) -> None:
    if lock is None:
        return
    try:
        lock.assert_healthy()
    except DBToolLockError as exc:
        heartbeat_error = lock.heartbeat_error()
        raise RestoreError(
            "DB tool lock health lost before target overwrite",
            partial_evidence={
                "lock_path": str(lock.lock_path),
                "heartbeat_error": heartbeat_error or str(exc),
                "health_check_error": str(exc),
            },
        ) from exc


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

    try:
        result = restore_backup_with_lock_and_ledger(
            args.backup_file,
            resolved_db_path,
            args.force,
            args.api_url,
        )
        print(f"Restore complete. Pre-restore backup: {result.pre_restore_backup}")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
