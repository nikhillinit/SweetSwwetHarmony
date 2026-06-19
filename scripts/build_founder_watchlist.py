"""Build the founder watchlist for the shadow GH negative-space collector.

Phase 0 task `p0.6` (red-team v2). This is a hard prerequisite for `p0.9`
(shadow GitHub negative-space collector). Without a bounded founder list,
gh-negative-space would issue unbounded GitHub API calls and bust the
GITHUB_TOKEN rate budget within hours.

Output: data/shadow/founder_watchlist.csv
Columns:
  - founder_id           (founders.id, or 'manual_<n>' for manual seeds)
  - full_name
  - github_username      (nullable)
  - linkedin_url         (nullable)
  - source               ('promoted_company' | 'historical_notion' | 'manual_seed')
  - associated_company_id (nullable)
  - added_at             (ISO 8601)

Cap: 500 founders maximum (sized to fit comfortably under the GITHUB_TOKEN
5000 req/hr limit even at 10 polls per founder per hour).

Safety contract:
  - Reads from signals.db ONLY through the ShadowSidecar (immutable URI mode).
  - Writes ONLY to data/shadow/founder_watchlist.csv. Never writes to
    signals.db, the founders table, or production state.

Fallback contract:
  - If linkedin_founder_enrichment is empty AND no promoted companies have
    associated founders, the script falls back to the manual seed list at
    `scripts/data/founder_watchlist_manual_seed.csv` (if it exists).
  - If neither source produces any founders, the script writes an empty CSV
    with the header row only and exits 2 with a clear message. The downstream
    gh-negative-space collector treats an empty file as a hard stop.

Usage:
    python -m scripts.build_founder_watchlist [--limit 500] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from analytics.shadow_sidecar import (
    DEFAULT_SHADOW_ROOT,
    ReadMode,
    ShadowSidecar,
    ShadowSidecarConfig,
)
from utils.db_path_helper import resolve_db_path_env

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = DEFAULT_SHADOW_ROOT / "founder_watchlist.csv"
DEFAULT_LIMIT = 500
MANUAL_SEED_PATH = Path("scripts") / "data" / "founder_watchlist_manual_seed.csv"

CSV_HEADER = [
    "founder_id",
    "full_name",
    "github_username",
    "linkedin_url",
    "source",
    "associated_company_id",
    "added_at",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _iter_founders_from_promoted_companies(conn) -> Iterator[dict]:
    """Read founders linked to promoted company files via founder_signals."""
    if not _table_exists(conn, "founders"):
        logger.info("founders table does not exist; skipping promoted-companies branch")
        return
    if not _table_exists(conn, "founder_signals"):
        return
    if not _table_exists(conn, "company_files"):
        return

    sql = """
        SELECT DISTINCT
            f.id AS founder_id,
            f.name AS full_name,
            f.github_username,
            f.linkedin_url,
            cf.company_id AS associated_company_id
        FROM founders f
        JOIN founder_signals fs ON fs.founder_id = f.id
        JOIN signals s ON s.id = fs.signal_id
        JOIN company_files cf ON cf.canonical_key = s.canonical_key
        WHERE cf.status = 'promoted'
        ORDER BY f.founder_score DESC, f.id ASC
    """
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as exc:
        logger.warning("promoted-companies founder query failed: %s", exc)
        return

    for r in rows:
        yield {
            "founder_id": str(r["founder_id"]),
            "full_name": r["full_name"] or "",
            "github_username": r["github_username"] or "",
            "linkedin_url": r["linkedin_url"] or "",
            "source": "promoted_company",
            "associated_company_id": r["associated_company_id"] or "",
        }


def _iter_founders_from_historical_notion(conn) -> Iterator[dict]:
    """Read founders from suppression cache (Notion mirror) of tracked companies.

    Reuses the existing suppression_cache table; no Notion API calls.
    """
    if not _table_exists(conn, "founders"):
        return
    if not _table_exists(conn, "suppression_cache"):
        return

    tracked_statuses = (
        "Tracking",
        "Dilligence",  # NB: matches Notion typo per .claude/rules/invariants.md
        "Initial Meeting / Call",
        "Committed",
        "Funded",
    )
    placeholders = ",".join("?" for _ in tracked_statuses)
    sql = f"""
        SELECT DISTINCT
            f.id AS founder_id,
            f.name AS full_name,
            f.github_username,
            f.linkedin_url,
            sc.canonical_key AS associated_company_id
        FROM founders f
        JOIN founder_signals fs ON fs.founder_id = f.id
        JOIN signals s ON s.id = fs.signal_id
        JOIN suppression_cache sc ON sc.canonical_key = s.canonical_key
        WHERE sc.status IN ({placeholders})
        ORDER BY f.founder_score DESC, f.id ASC
    """
    try:
        rows = conn.execute(sql, tracked_statuses).fetchall()
    except Exception as exc:
        logger.warning("historical-notion founder query failed: %s", exc)
        return

    for r in rows:
        yield {
            "founder_id": str(r["founder_id"]),
            "full_name": r["full_name"] or "",
            "github_username": r["github_username"] or "",
            "linkedin_url": r["linkedin_url"] or "",
            "source": "historical_notion",
            "associated_company_id": r["associated_company_id"] or "",
        }


def _iter_manual_seed(seed_path: Path) -> Iterator[dict]:
    """Read manual seed CSV (if it exists). Same column shape as output."""
    if not seed_path.exists():
        logger.info("Manual seed not present at %s; skipping", seed_path)
        return
    with seed_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            yield {
                "founder_id": row.get("founder_id") or f"manual_{i + 1}",
                "full_name": row.get("full_name", ""),
                "github_username": row.get("github_username", ""),
                "linkedin_url": row.get("linkedin_url", ""),
                "source": "manual_seed",
                "associated_company_id": row.get("associated_company_id", ""),
            }


def _dedupe(rows: Iterator[dict]) -> List[dict]:
    """Dedupe by github_username if present, else by linkedin_url, else by id."""
    seen: set = set()
    out: List[dict] = []
    for r in rows:
        key = (
            r.get("github_username")
            or r.get("linkedin_url")
            or r.get("founder_id")
        )
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def build_watchlist(
    *,
    output: Path = DEFAULT_OUTPUT,
    limit: int = DEFAULT_LIMIT,
    production_db: Optional[Path] = None,
    seed_path: Path = MANUAL_SEED_PATH,
    dry_run: bool = False,
) -> int:
    """Build the founder watchlist CSV. Returns the number of rows written."""
    production_db = Path(resolve_db_path_env()) if production_db is None else production_db
    cfg = ShadowSidecarConfig(
        production_db=production_db,
        read_mode=ReadMode.IMMUTABLE_URI,
        register_dbtool_lock=False,  # read-only, lock not needed for one-shot script
    )

    collected: List[dict] = []
    if production_db.exists():
        with ShadowSidecar(cfg) as sidecar:
            with sidecar.production_read_connection() as conn:
                collected.extend(_iter_founders_from_promoted_companies(conn))
                collected.extend(_iter_founders_from_historical_notion(conn))
    else:
        logger.warning(
            "Production DB not found at %s — falling back to manual seed only",
            production_db,
        )

    if not collected:
        logger.info("No founders from production DB; trying manual seed")
        collected.extend(_iter_manual_seed(seed_path))

    deduped = _dedupe(iter(collected))
    capped = deduped[:limit]
    added_at = _utcnow_iso()

    logger.info(
        "Founder watchlist: collected=%d deduped=%d capped=%d limit=%d",
        len(collected),
        len(deduped),
        len(capped),
        limit,
    )

    if dry_run:
        logger.info("--dry-run set; not writing output")
        return len(capped)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in capped:
            row["added_at"] = added_at
            writer.writerow({k: row.get(k, "") for k in CSV_HEADER})

    return len(capped)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of founders to include (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--production-db",
        type=Path,
        default=None,
        help="Production DB path (default: DISCOVERY_DB_PATH)",
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=MANUAL_SEED_PATH,
        help=f"Manual seed CSV path (default: {MANUAL_SEED_PATH})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write output")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable INFO logging"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    written = build_watchlist(
        output=args.output,
        limit=args.limit,
        production_db=args.production_db,
        seed_path=args.seed,
        dry_run=args.dry_run,
    )

    if written == 0:
        print(
            "ERROR: No founders found. The shadow GH negative-space collector "
            "must NOT run with an empty watchlist (would issue unbounded GitHub "
            "API calls). Either:\n"
            f"  1. Populate {args.seed} with a manual seed list, or\n"
            "  2. Wait until the founders table has rows joined to promoted "
            "company_files.",
            file=sys.stderr,
        )
        return 2

    print(f"Wrote {written} founders to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
