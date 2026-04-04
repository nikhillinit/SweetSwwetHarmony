"""Backfill thesis_classifications provenance metadata.

Stamps prompt_version and model on historical rows that predate provenance tracking.

Two segments:
  1. LLM-classified (has rationale) but missing prompt_version → "pre-provenance" + "gemini-2.0-flash"
  2. Keyword-only (no rationale) and missing prompt_version → "keyword-only", model stays null

Usage:
  python scripts/backfill_thesis_provenance.py --dry-run   # Preview changes
  python scripts/backfill_thesis_provenance.py              # Execute backfill
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description="Backfill thesis provenance metadata")
    parser.add_argument("--db", default="signals.db", help="Database path")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    # --- Pre-flight: count affected rows ---
    llm_missing = conn.execute(
        "SELECT COUNT(*) FROM thesis_classifications "
        "WHERE rationale IS NOT NULL AND rationale != '' "
        "AND (prompt_version IS NULL OR prompt_version = '')"
    ).fetchone()[0]

    kw_missing = conn.execute(
        "SELECT COUNT(*) FROM thesis_classifications "
        "WHERE (rationale IS NULL OR rationale = '') "
        "AND (prompt_version IS NULL OR prompt_version = '')"
    ).fetchone()[0]

    already_stamped = conn.execute(
        "SELECT COUNT(*) FROM thesis_classifications "
        "WHERE prompt_version IS NOT NULL AND prompt_version != ''"
    ).fetchone()[0]

    total = conn.execute("SELECT COUNT(*) FROM thesis_classifications").fetchone()[0]

    print(f"Total thesis_classifications rows: {total}")
    print(f"Already stamped: {already_stamped}")
    print(f"LLM-classified missing provenance: {llm_missing}")
    print(f"Keyword-only missing provenance: {kw_missing}")
    print(f"Total to backfill: {llm_missing + kw_missing}")
    print()

    if llm_missing + kw_missing == 0:
        print("Nothing to backfill.")
        conn.close()
        return

    if args.dry_run:
        print("[DRY RUN] Would apply:")
        print(f"  UPDATE {llm_missing} LLM rows: prompt_version='pre-provenance', model='gemini-2.0-flash'")
        print(f"  UPDATE {kw_missing} keyword rows: prompt_version='keyword-only'")
        conn.close()
        return

    # --- Execute backfill ---
    now = datetime.now(timezone.utc).isoformat()

    # Segment 1: LLM-classified rows
    if llm_missing > 0:
        cursor = conn.execute(
            "UPDATE thesis_classifications "
            "SET prompt_version = 'pre-provenance', model = 'gemini-2.0-flash' "
            "WHERE rationale IS NOT NULL AND rationale != '' "
            "AND (prompt_version IS NULL OR prompt_version = '')"
        )
        print(f"Updated {cursor.rowcount} LLM-classified rows -> prompt_version='pre-provenance', model='gemini-2.0-flash'")

    # Segment 2: Keyword-only rows
    if kw_missing > 0:
        cursor = conn.execute(
            "UPDATE thesis_classifications "
            "SET prompt_version = 'keyword-only' "
            "WHERE (rationale IS NULL OR rationale = '') "
            "AND (prompt_version IS NULL OR prompt_version = '')"
        )
        print(f"Updated {cursor.rowcount} keyword-only rows -> prompt_version='keyword-only'")

    conn.commit()

    # --- Verify ---
    remaining = conn.execute(
        "SELECT COUNT(*) FROM thesis_classifications "
        "WHERE prompt_version IS NULL OR prompt_version = ''"
    ).fetchone()[0]

    print()
    print(f"Verification: {remaining} rows still missing prompt_version (should be 0)")

    # Show distribution
    print()
    print("Prompt version distribution after backfill:")
    for pv, cnt in conn.execute(
        "SELECT prompt_version, COUNT(*) FROM thesis_classifications "
        "GROUP BY prompt_version ORDER BY COUNT(*) DESC"
    ):
        print(f"  {pv}: {cnt}")

    conn.close()

    if remaining > 0:
        print(f"\nWARNING: {remaining} rows still missing provenance!")
        sys.exit(1)
    else:
        print("\nBackfill complete. All rows have provenance metadata.")


if __name__ == "__main__":
    main()
