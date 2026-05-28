#!/usr/bin/env python3
"""
mine_track_b_candidates.py — DB-mined Track B labelling substrate seed.

Implements REQ REC-01 from .planning/REQUIREMENTS.md and the per-signal
candidate schema locked by CONTEXT.md D-05.

Strategy (per CONTEXT.md D-04):
    Surface 30 candidate signals from signals.db, stratified into three
    buckets to span the classifier's decision surface:
        Bucket 1: TP-likely (10 rows)  — qualified category, score >= 0.7
        Bucket 2: FP-likely (10 rows)  — excluded category, score < 0.3
        Bucket 3: Ambiguous (10 rows)  — unclassified or mid-score 0.3-0.7

The latest classification per signal is used (signals are re-classified
multiple times in this DB; ROW_NUMBER OVER PARTITION BY signal_id
ORDER BY classified_at DESC selects the most recent).

Per CONTEXT.md D-06, analyst confirmation flows through the existing
`quality-label` skill:
    python -m ops.cli quality label <signal_id> <TP|FP|UNSURE> --reason "..."
No new labelling UI is built.

Per CONTEXT.md D-07, this cohort is known-biased (drawn from what the
engine already surfaced). Track B is the SECONDARY canary; a true
random-sampled cohort is a Phase 3+ deliverable.

Schema migration (per RESEARCH.md):
    The pre-existing data/shadow/track_b_episodes.csv had a Phase 0
    EPISODE-level schema (episode_id, canonical_key, episode_start, ...).
    This script OVERWRITES with the D-05 per-signal-candidate schema.
    The header comment in the output documents the migration.

Usage
-----
    python scripts/red-team-hybrid/mine_track_b_candidates.py
    python scripts/red-team-hybrid/mine_track_b_candidates.py --json
    python scripts/red-team-hybrid/mine_track_b_candidates.py --db /path/to/signals.db
    python scripts/red-team-hybrid/mine_track_b_candidates.py --out data/shadow/track_b_episodes.csv

Exit codes
----------
    0  success, CSV written with rows from all 3 buckets
    1  expected failure (zero rows in any bucket)
    2  operational error (DB unreadable, etc.)
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH: str = "signals.db"
DEFAULT_OUT_PATH: str = "data/shadow/track_b_episodes.csv"
DEFAULT_OPERATIONAL_COLLECTORS: tuple[str, ...] = (
    "hacker_news",
    "arxiv",
    "rss_feeds",
)
BUCKET_SIZE: int = 10  # 10 per bucket x 3 buckets = 30 candidates per D-04

# D-05 schema columns
CSV_COLUMNS: tuple[str, ...] = (
    "signal_id",
    "source_api",
    "canonical_key",
    "company_name",
    "confidence_from_classifier",
    "thesis_category",
    "claude_pre_label",
    "pre_label_rationale",
    "analyst_label",
    "labeled_at",
    "labeler_id",
)

# Deterministic ordering substitute for ORDER BY RANDOM().
# substr(canonical_key || 'seed20260408', 1, 16) gives a stable
# canonical-key-pseudo-random ordering — same DB state produces the
# same 30 candidates every run.
DETERMINISTIC_ORDER_EXPR = "substr(canonical_key || 'seed20260408', 1, 16)"


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"signals DB not found: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def query_bucket_1_tp_likely(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Qualified-category, score >= 0.7. Up to 10 rows."""
    sql = f"""
    WITH latest_class AS (
        SELECT signal_id, category, thesis_fit_score,
               ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC) AS rn
        FROM thesis_classifications
    )
    SELECT s.id, s.source_api, s.canonical_key, s.company_name, s.confidence,
           lc.category, lc.thesis_fit_score
    FROM signals s
    JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
    WHERE s.source_api IN ('hacker_news','arxiv','rss_feeds')
      AND lc.category NOT IN ('excluded','other')
      AND lc.thesis_fit_score >= 0.7
    ORDER BY {DETERMINISTIC_ORDER_EXPR}
    LIMIT {BUCKET_SIZE}
    """
    rows = conn.execute(sql).fetchall()
    return [
        {
            "id": r[0], "source_api": r[1], "canonical_key": r[2],
            "company_name": r[3], "confidence": r[4],
            "category": r[5], "thesis_fit_score": r[6],
        }
        for r in rows
    ]


def query_bucket_2_fp_likely(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Excluded category, score < 0.3. Up to 10 rows."""
    sql = f"""
    WITH latest_class AS (
        SELECT signal_id, category, thesis_fit_score,
               ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC) AS rn
        FROM thesis_classifications
    )
    SELECT s.id, s.source_api, s.canonical_key, s.company_name, s.confidence,
           lc.category, lc.thesis_fit_score
    FROM signals s
    JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
    WHERE lc.category = 'excluded' AND lc.thesis_fit_score < 0.3
      AND s.source_api IN ('hacker_news','arxiv','rss_feeds')
    ORDER BY {DETERMINISTIC_ORDER_EXPR}
    LIMIT {BUCKET_SIZE}
    """
    rows = conn.execute(sql).fetchall()
    return [
        {
            "id": r[0], "source_api": r[1], "canonical_key": r[2],
            "company_name": r[3], "confidence": r[4],
            "category": r[5], "thesis_fit_score": r[6],
        }
        for r in rows
    ]


def query_bucket_3_ambiguous(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Unclassified or mid-score 0.3-0.7. Up to 10 rows."""
    sql = f"""
    WITH latest_class AS (
        SELECT signal_id, category, thesis_fit_score,
               ROW_NUMBER() OVER (PARTITION BY signal_id ORDER BY classified_at DESC) AS rn
        FROM thesis_classifications
    )
    SELECT s.id, s.source_api, s.canonical_key, s.company_name, s.confidence,
           COALESCE(lc.category, 'unclassified') AS category,
           lc.thesis_fit_score
    FROM signals s
    LEFT JOIN latest_class lc ON s.id = lc.signal_id AND lc.rn = 1
    WHERE s.source_api IN ('hacker_news','arxiv','rss_feeds')
      AND (lc.thesis_fit_score IS NULL
           OR (lc.thesis_fit_score >= 0.3 AND lc.thesis_fit_score < 0.7))
    ORDER BY {DETERMINISTIC_ORDER_EXPR}
    LIMIT {BUCKET_SIZE}
    """
    rows = conn.execute(sql).fetchall()
    return [
        {
            "id": r[0], "source_api": r[1], "canonical_key": r[2],
            "company_name": r[3], "confidence": r[4],
            "category": r[5], "thesis_fit_score": r[6],
        }
        for r in rows
    ]


def to_csv_row(bucket_label: str, source: dict[str, Any]) -> dict[str, str]:
    """Convert a DB row to a D-05 CSV row dict."""
    score_str = (
        f"{source['thesis_fit_score']:.2f}" if source["thesis_fit_score"] is not None else "NULL"
    )
    category = source["category"] or "unclassified"
    if bucket_label == "TP":
        label = "TP"
        rationale = (
            f"Bucket 1 (TP-likely): qualified category, score >= 0.7. "
            f"category={category}, score={score_str}"
        )
    elif bucket_label == "FP":
        label = "FP"
        rationale = (
            f"Bucket 2 (FP-likely): excluded category, score < 0.3. "
            f"category={category}, score={score_str}"
        )
    else:  # UNSURE
        label = "UNSURE"
        rationale = (
            f"Bucket 3 (ambiguous): {('unclassified' if source['thesis_fit_score'] is None else 'mid-score 0.3-0.7')}. "
            f"category={category}, score={score_str}"
        )

    return {
        "signal_id": str(source["id"]),
        "source_api": source["source_api"],
        "canonical_key": source["canonical_key"],
        "company_name": source["company_name"] or "",
        "confidence_from_classifier": f"{source['confidence']:.2f}",
        "thesis_category": category,
        "claude_pre_label": label,
        "pre_label_rationale": rationale,
        "analyst_label": "",
        "labeled_at": "",
        "labeler_id": "",
    }


def write_csv(out_path: Path, rows: list[dict[str, str]], generated_at: str) -> None:
    """Write CSV with header comment documenting the schema migration."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header_comment = (
        f"# Phase 1 Track B candidate seed (REC-01) — D-05 per-signal schema.\n"
        f"# Generated by scripts/red-team-hybrid/mine_track_b_candidates.py\n"
        f"# Generated at: {generated_at}\n"
        f"# SCHEMA MIGRATION NOTE: this file previously held a Phase 0 EPISODE-level\n"
        f"# header (episode_id,canonical_key,episode_start,...). It is now D-05 per-signal\n"
        f"# (signal_id,source_api,canonical_key,...). Episode-level rollup happens in Phase 2+\n"
        f"# in a separate file. See CONTEXT.md D-05 and 1-RESEARCH.md for the migration record.\n"
        f"# Cohort selection bias: Track B is the SECONDARY canary per CONTEXT.md D-07;\n"
        f"# this cohort is drawn from what the engine already surfaced and is known-biased.\n"
        f"# Random-sampled cohort is a Phase 3+ deliverable.\n"
        f'# Analyst confirmation: python -m ops.cli quality label <signal_id> <TP|FP|UNSURE> --reason "..."\n'
    )
    with out_path.open("w", encoding="utf-8", newline="") as f:
        f.write(header_comment)
        writer = csv.DictWriter(f, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine Track B candidates from signals.db (REC-01).")
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    parser.add_argument("--out", type=Path, default=Path(DEFAULT_OUT_PATH))
    parser.add_argument("--json", action="store_true", help="Emit JSON summary to stdout")
    args = parser.parse_args(argv)

    try:
        conn = _ro_conn(args.db)
    except (sqlite3.Error, FileNotFoundError) as e:
        print(f"ERROR: failed to open signals.db: {e}", file=sys.stderr)
        return 2

    try:
        b1 = query_bucket_1_tp_likely(conn)
        b2 = query_bucket_2_fp_likely(conn)
        b3 = query_bucket_3_ambiguous(conn)
    except sqlite3.Error as e:
        print(f"ERROR: query failed: {e}", file=sys.stderr)
        conn.close()
        return 2
    finally:
        conn.close()

    rows = (
        [to_csv_row("TP", r) for r in b1]
        + [to_csv_row("FP", r) for r in b2]
        + [to_csv_row("UNSURE", r) for r in b3]
    )

    if not rows:
        print("ERROR: zero candidates from all buckets — check signals.db state", file=sys.stderr)
        return 1

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        write_csv(args.out, rows, generated_at)
    except OSError as e:
        print(f"ERROR: failed to write {args.out}: {e}", file=sys.stderr)
        return 2

    summary = {
        "out": str(args.out),
        "total_rows": len(rows),
        "bucket_1_tp_likely": len(b1),
        "bucket_2_fp_likely": len(b2),
        "bucket_3_ambiguous": len(b3),
        "generated_at": generated_at,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Wrote {summary['total_rows']} rows to {args.out}")
        print(f"  Bucket 1 (TP-likely):  {summary['bucket_1_tp_likely']}")
        print(f"  Bucket 2 (FP-likely):  {summary['bucket_2_fp_likely']}")
        print(f"  Bucket 3 (ambiguous):  {summary['bucket_3_ambiguous']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
