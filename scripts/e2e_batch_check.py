"""E2E batch publish: list pending review_items with details."""

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
    parser.add_argument("--limit", type=int, default=20, help="Maximum rows to show")
    return parser


async def run(db_path: str | None, *, limit: int = 20) -> int:
    import aiosqlite
    from utils.db_path_helper import resolve_db_path_env

    resolved_db_path = resolve_db_path_env(db_path)
    db = await aiosqlite.connect(resolved_db_path)
    try:
        cursor = await db.execute(
            """
            SELECT ri.id, ri.company_id, cf.company_name, cf.canonical_key,
                   s.confidence, s.source_api
            FROM review_items ri
            JOIN company_files cf ON cf.company_id = ri.company_id
            LEFT JOIN signals s ON s.company_id = ri.company_id
            WHERE ri.status = 'pending'
            ORDER BY s.confidence DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        print("=== Pending review_items (by confidence) ===")
        seen = set()
        for row in rows:
            if row[0] in seen:
                continue
            seen.add(row[0])
            name = (row[2] or "?")[:50]
            key = (row[3] or "?")[:40]
            print(
                f"  ri.id={row[0]:3d} conf={row[4]:.3f} "
                f"src={row[5]:15s} key={key:40s} company={name}"
            )
        return 0
    finally:
        await db.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args.db_path, limit=args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
