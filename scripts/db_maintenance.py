#!/usr/bin/env python3
"""
Database Maintenance Script for CI/CD

Provides utilities for SQLite database maintenance in GitHub Actions:
- Integrity checking
- WAL checkpoint
- Vacuum (optional, for size reduction)

Usage:
    python scripts/db_maintenance.py --db-path signals.db --integrity-check
    python scripts/db_maintenance.py --db-path signals.db --checkpoint
    python scripts/db_maintenance.py --db-path signals.db --vacuum
    python scripts/db_maintenance.py --db-path signals.db --all
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def check_integrity(db_path: str) -> bool:
    """
    Run SQLite integrity check.

    Returns:
        True if integrity check passes
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA integrity_check;")
        result = cursor.fetchone()[0]
        conn.close()

        if result == "ok":
            print(f"[OK] Integrity check passed: {db_path}")
            return True
        else:
            print(f"[ERROR] Integrity check failed: {result}")
            return False
    except Exception as e:
        print(f"[ERROR] Integrity check error: {e}")
        return False


def checkpoint_wal(db_path: str) -> bool:
    """
    Checkpoint WAL journal to main database file.

    Returns:
        True if checkpoint succeeded or no WAL present
    """
    wal_path = Path(db_path + "-wal")

    if not wal_path.exists():
        print(f"[OK] No WAL file present: {db_path}")
        return True

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        result = cursor.fetchone()
        conn.close()

        # Result is (busy, log, checkpointed)
        busy, log, checkpointed = result
        print(f"[OK] WAL checkpoint: busy={busy}, log={log}, checkpointed={checkpointed}")

        # Check if WAL was fully checkpointed
        if wal_path.exists() and wal_path.stat().st_size > 0:
            print(f"[WARN] WAL file still has content after checkpoint")
            return True  # Not a failure, just informational

        return True
    except Exception as e:
        print(f"[ERROR] WAL checkpoint error: {e}")
        return False


def vacuum_db(db_path: str) -> bool:
    """
    Vacuum database to reclaim space.

    Note: VACUUM can be slow on large databases. Use sparingly.

    Returns:
        True if vacuum succeeded
    """
    try:
        original_size = Path(db_path).stat().st_size

        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM;")
        conn.close()

        new_size = Path(db_path).stat().st_size
        saved = original_size - new_size

        print(f"[OK] Vacuum complete: {original_size:,} -> {new_size:,} bytes (saved {saved:,})")
        return True
    except Exception as e:
        print(f"[ERROR] Vacuum error: {e}")
        return False


def get_db_stats(db_path: str) -> dict:
    """Get database statistics."""
    stats = {}

    try:
        conn = sqlite3.connect(db_path)

        # Page count and size
        cursor = conn.execute("PRAGMA page_count;")
        stats["page_count"] = cursor.fetchone()[0]

        cursor = conn.execute("PRAGMA page_size;")
        stats["page_size"] = cursor.fetchone()[0]

        stats["total_size"] = stats["page_count"] * stats["page_size"]

        # Journal mode
        cursor = conn.execute("PRAGMA journal_mode;")
        stats["journal_mode"] = cursor.fetchone()[0]

        # Table counts (optional, may fail on some tables)
        tables = ["signals", "watches", "snapshots", "diffs", "monitoring_alerts"]
        for table in tables:
            try:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                stats[f"count_{table}"] = cursor.fetchone()[0]
            except:
                pass

        conn.close()
    except Exception as e:
        print(f"[WARN] Error getting stats: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Database maintenance utilities for CI/CD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        type=str,
        required=True,
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--integrity-check",
        action="store_true",
        help="Run PRAGMA integrity_check",
    )
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="Checkpoint WAL to main database",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Vacuum database to reclaim space",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show database statistics",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run integrity check, checkpoint, and show stats",
    )

    args = parser.parse_args()

    # Check if database exists
    if not Path(args.db_path).exists():
        print(f"[ERROR] Database not found: {args.db_path}")
        sys.exit(1)

    all_ok = True

    # Run requested operations
    if args.all or args.stats:
        print(f"\n=== Database Stats: {args.db_path} ===")
        stats = get_db_stats(args.db_path)
        for key, value in stats.items():
            if isinstance(value, int) and key != "journal_mode":
                print(f"  {key}: {value:,}")
            else:
                print(f"  {key}: {value}")

    if args.all or args.integrity_check:
        print(f"\n=== Integrity Check ===")
        if not check_integrity(args.db_path):
            all_ok = False

    if args.all or args.checkpoint:
        print(f"\n=== WAL Checkpoint ===")
        if not checkpoint_wal(args.db_path):
            all_ok = False

    if args.vacuum:
        print(f"\n=== Vacuum ===")
        if not vacuum_db(args.db_path):
            all_ok = False

    # Exit with appropriate code
    if not all_ok:
        print("\n[WARN] Some operations failed")
        sys.exit(1)
    else:
        print("\n[OK] All operations completed successfully")
        sys.exit(0)


if __name__ == "__main__":
    main()
