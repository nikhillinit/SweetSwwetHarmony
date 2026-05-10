"""E2E batch publish: approve review_items for batch testing."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.db_tool_errors import DBToolError

ITEMS_TO_APPROVE = [35, 14]  # thinkqurio.com, priceperball.net


class ApproveError(DBToolError):
    """Approve failure carrying review_item_ids that were mid-mutation."""

    def __init__(
        self,
        message: str,
        *,
        review_item_ids: list[int],
        last_processed_id: int | None = None,
        committed: bool = False,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "review_item_ids": list(review_item_ids),
                "last_processed_id": last_processed_id,
                "committed": committed,
            },
        )
        self.review_item_ids = list(review_item_ids)
        self.last_processed_id = last_processed_id
        self.committed = committed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="Path to SQLite database")
    parser.add_argument(
        "--review-item-ids",
        default=",".join(str(item) for item in ITEMS_TO_APPROVE),
        help="Comma-separated review_item IDs to approve",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Apply the approval update to the target DB",
    )
    return parser


def _parse_review_item_ids(raw: str) -> list[int]:
    values = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        values.append(int(token))
    if not values:
        raise ValueError("At least one review item ID is required")
    return values


async def run(
    db_path: str | None,
    *,
    review_item_ids: list[int],
    apply_changes: bool,
) -> int:
    import aiosqlite
    from datetime import datetime, timezone
    from utils.db_ops_ledger import append_db_ops_ledger
    from utils.db_path_helper import resolve_db_path_env
    from utils.db_tool_lock import DBToolLock

    if not apply_changes:
        resolved_db_path = resolve_db_path_env(db_path)
        append_db_ops_ledger(
            tool_name="e2e_batch_approve",
            db_path=resolved_db_path,
            action="approve_review_items",
            status="refused",
            details={"reason": "missing_yes_flag", "review_item_ids": review_item_ids},
        )
        print("Refusing to mutate review_items without --yes.", file=sys.stderr)
        return 2

    resolved_db_path = resolve_db_path_env(db_path)
    lock = DBToolLock(resolved_db_path, tool_name="e2e_batch_approve")
    if not lock.acquire(timeout_seconds=5):
        holder = lock.get_holder_info()
        append_db_ops_ledger(
            tool_name="e2e_batch_approve",
            db_path=resolved_db_path,
            action="approve_review_items",
            status="lock_blocked",
            details={"holder": holder, "review_item_ids": review_item_ids},
        )
        print(f"Refusing to mutate review_items because DB tool lock is held: {holder}", file=sys.stderr)
        return 2

    db = await aiosqlite.connect(resolved_db_path)
    last_processed_id: int | None = None
    committed = False
    try:
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")

            now = datetime.now(timezone.utc).isoformat()
            for review_item_id in review_item_ids:
                last_processed_id = review_item_id
                cursor = await db.execute(
                    "SELECT status, company_id FROM review_items WHERE id = ?",
                    (review_item_id,),
                )
                row = await cursor.fetchone()
                if not row:
                    print(f"  ri.id={review_item_id}: NOT FOUND")
                    continue
                if row[0] != "pending":
                    print(f"  ri.id={review_item_id}: already {row[0]}, skipping")
                    continue

                await db.execute(
                    """
                    UPDATE review_items
                    SET status = 'approved',
                        decided_by = 'e2e-test',
                        decided_at = ?,
                        reason = 'E2E batch publish test'
                    WHERE id = ?
                    """,
                    (now, review_item_id),
                )
                print(f"  ri.id={review_item_id}: pending -> approved (company_id={row[1]})")

            await db.commit()
            committed = True

            placeholders = ",".join("?" for _ in review_item_ids)
            cursor = await db.execute(
                f"SELECT id, status FROM review_items WHERE id IN ({placeholders})",
                tuple(review_item_ids),
            )
            rows = await cursor.fetchall()
            print("\nVerification:")
            for row in rows:
                print(f"  ri.id={row[0]}: {row[1]}")
            append_db_ops_ledger(
                tool_name="e2e_batch_approve",
                db_path=resolved_db_path,
                action="approve_review_items",
                status="success",
                details={"review_item_ids": review_item_ids},
            )
            return 0
        except Exception as e:
            try:
                await db.rollback()
            except Exception:
                pass
            err = ApproveError(
                f"Approve failed: {e}",
                review_item_ids=review_item_ids,
                last_processed_id=last_processed_id,
                committed=committed,
            )
            append_db_ops_ledger(
                tool_name="e2e_batch_approve",
                db_path=resolved_db_path,
                action="approve_review_items",
                status="error",
                details={**err.partial_evidence, "error": str(err)},
            )
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
    finally:
        await db.close()
        lock.release()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        review_item_ids = _parse_review_item_ids(args.review_item_ids)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(
        run(
            args.db_path,
            review_item_ids=review_item_ids,
            apply_changes=args.yes,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
