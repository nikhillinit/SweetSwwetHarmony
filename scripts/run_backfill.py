"""One-shot script: backfill NULL company_id values in the target signals DB."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="Path to SQLite database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mappings without mutating the DB",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply the backfill to the target DB",
    )
    return parser


async def run(db_path: str | None, *, dry_run: bool, apply_changes: bool) -> int:
    from utils.db_ops_ledger import append_db_ops_ledger
    from storage.migrations.backfill_v28_identity import backfill_company_ids
    from storage.signal_store import SignalStore
    from utils.db_path_helper import resolve_db_path_env
    from utils.db_tool_lock import DBToolLock

    if not dry_run and not apply_changes:
        resolved_db_path = resolve_db_path_env(db_path)
        append_db_ops_ledger(
            tool_name="run_backfill",
            db_path=resolved_db_path,
            action="backfill_company_ids",
            status="refused",
            details={"reason": "missing_yes_flag", "dry_run": dry_run},
        )
        print("Refusing to mutate company_id values without --yes.", file=sys.stderr)
        return 2

    resolved_db_path = resolve_db_path_env(db_path)
    lock = None
    if not dry_run:
        lock = DBToolLock(resolved_db_path, tool_name="run_backfill")
        if not lock.acquire(timeout_seconds=5):
            holder = lock.get_holder_info()
            append_db_ops_ledger(
                tool_name="run_backfill",
                db_path=resolved_db_path,
                action="backfill_company_ids",
                status="lock_blocked",
                details={"holder": holder},
            )
            print(f"Refusing to mutate company_id values because DB tool lock is held: {holder}", file=sys.stderr)
            return 2
    store = SignalStore(resolved_db_path)
    await store.initialize()
    try:
        await store._db.execute("PRAGMA busy_timeout=5000")
        if not dry_run:
            await store._db.execute("BEGIN IMMEDIATE")
            await store._db.rollback()

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id IS NULL"
        )
        null_count = (await cursor.fetchone())[0]
        print(f"NULL company_id count: {null_count}")

        if null_count == 0:
            print("No NULLs to fix - already clean!")
            return 0

        cursor = await store._db.execute(
            "SELECT id, canonical_key FROM signals WHERE company_id IS NULL"
        )
        rows = await cursor.fetchall()
        for row in rows:
            print(f"  Signal {row[0]}: {row[1]}")

        result = await backfill_company_ids(store, dry_run=dry_run)
        mode = result["mode"]
        before = result["null_count_before"]
        after = result.get("null_count_after", before)
        new = result["newly_generated"]
        merged = result["merge_resolved"]
        print(f"Backfill complete: mode={mode}, null_before={before}, null_after={after}")
        print(f"  Newly generated: {new}, merge_resolved: {merged}")
        if not dry_run:
            append_db_ops_ledger(
                tool_name="run_backfill",
                db_path=resolved_db_path,
                action="backfill_company_ids",
                status="success",
                details={"null_count_before": before, "null_count_after": after},
            )
        return 0
    finally:
        await store.close()
        if lock is not None:
            lock.release()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(
        run(
            args.db_path,
            dry_run=args.dry_run,
            apply_changes=args.yes,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
