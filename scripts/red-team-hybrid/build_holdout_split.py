#!/usr/bin/env python3
"""
build_holdout_split.py -- deterministic hold-out cohort split for Track C.

Implements REQ REC-02 from .planning/REQUIREMENTS.md and the Track C
hold-out split design from
`docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` section 3.

Algorithm (verbatim from 05-holdout-cohort-design.md section 3):

    h = sha256(f"{seed}:{episode_id}")
    bucket = int.from_bytes(h[:8], "big") / (1 << 64)
    split = "holdout" if bucket < holdout_fraction else "train"

Properties:
- Deterministic: same (seed, episode_id) -> same split forever
- Stable: adding new episodes does NOT reshuffle existing assignments
- Order-independent: assignment depends only on episode_id, not on
  insertion order
- Reproducible: re-running this script against the same signals.db
  produces a byte-identical episodes_v1.csv (excluding the labelled_at
  column which records the script-run timestamp)

Phase 1 proxy note
------------------
Phase 1 has no real episodes yet -- episodes come from the Phase 2+
analyst labelling cycle. This script ships a **signal-candidate-keyed
proxy** where each operational-collector signal in signals.db becomes
a pseudo-episode with:
    episode_id    = str(signals.id)
    canonical_key = signals.canonical_key
    split         = assign_split(episode_id, seed=20260406, holdout_fraction=0.3)
    outcome_label = "PENDING_PROXY"   (no real outcome yet)
    labelled_at   = script run timestamp (ISO 8601)

Phase 2 MUST re-run this script (or a successor) against real episodes
once the Track B labelling cycle produces outcome-bearing rows. The
header comment in episodes_v1.csv records this explicitly.

Usage
-----
    python scripts/red-team-hybrid/build_holdout_split.py
    python scripts/red-team-hybrid/build_holdout_split.py --seed 20260406
    python scripts/red-team-hybrid/build_holdout_split.py --holdout-fraction 0.3
    python scripts/red-team-hybrid/build_holdout_split.py --db /path/to/signals.db
    python scripts/red-team-hybrid/build_holdout_split.py --out data/shadow/holdout_split/episodes_v1.csv
    python scripts/red-team-hybrid/build_holdout_split.py --json   # emit summary JSON to stdout

Exit codes
----------
    0  success, CSV written
    1  expected failure (e.g., zero candidate rows in signals.db)
    2  operational error (DB unreadable, write failure, etc.)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.db_paths import InTreeDatabaseError
from utils.db_path_helper import resolve_db_path_env

# REC-02 constants. Changes require updating .planning/REQUIREMENTS.md
# and docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md.
DEFAULT_SEED: int = 20260406
DEFAULT_HOLDOUT_FRACTION: float = 0.3
DEFAULT_OUT_PATH: str = "data/shadow/holdout_split/episodes_v1.csv"
DEFAULT_OPERATIONAL_COLLECTORS: tuple[str, ...] = (
    "hacker_news",
    "arxiv",
    "rss_feeds",
)


def assign_split(
    episode_id: str,
    seed: int = DEFAULT_SEED,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> str:
    """Deterministic split per 05-holdout-cohort-design.md section 3."""
    h = hashlib.sha256(f"{seed}:{episode_id}".encode()).digest()
    bucket = int.from_bytes(h[:8], "big") / (1 << 64)
    return "holdout" if bucket < holdout_fraction else "train"


def query_candidates(
    db_path: Path,
    operational: tuple[str, ...] = DEFAULT_OPERATIONAL_COLLECTORS,
) -> list[dict[str, Any]]:
    """
    Return candidate rows for the proxy split.

    Reads signals.db in read-only mode. Filters to operational collectors
    only (same set as freshness_watchdog.py DEFAULT_OPERATIONAL_COLLECTORS).
    Orders by signal_id ASCENDING so the output CSV is order-stable
    across runs.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"signals DB not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(operational))
        cur.execute(
            f"SELECT id, canonical_key, source_api "
            f"FROM signals "
            f"WHERE source_api IN ({placeholders}) "
            f"ORDER BY id ASC",
            operational,
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {"id": row[0], "canonical_key": row[1], "source_api": row[2]}
        for row in rows
    ]


def build_split_rows(
    candidates: list[dict[str, Any]],
    seed: int,
    holdout_fraction: float,
    labelled_at: str,
) -> list[dict[str, str]]:
    """Apply assign_split() to every candidate and build the CSV row dicts."""
    rows: list[dict[str, str]] = []
    for cand in candidates:
        episode_id = str(cand["id"])
        split = assign_split(episode_id, seed, holdout_fraction)
        rows.append({
            "episode_id": episode_id,
            "canonical_key": cand["canonical_key"],
            "split": split,
            "outcome_label": "PENDING_PROXY",
            "labelled_at": labelled_at,
        })
    return rows


def write_csv(out_path: Path, rows: list[dict[str, str]]) -> None:
    """Write the split CSV with a header comment documenting the proxy."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header_comment = (
        "# Phase 1 Track C hold-out split -- proxy using signal IDs as episode_ids.\n"
        "# Generated by scripts/red-team-hybrid/build_holdout_split.py (REC-02).\n"
        "# Algorithm: sha256(seed:episode_id), first 8 bytes, holdout if < 0.3.\n"
        "# Phase 2 MUST re-run against real episodes once Track B produces them.\n"
        "# See docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md section 3.\n"
    )
    with out_path.open("w", encoding="utf-8", newline="") as f:
        f.write(header_comment)
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "episode_id",
                "canonical_key",
                "split",
                "outcome_label",
                "labelled_at",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Count holdout vs train for reporting."""
    holdout = sum(1 for r in rows if r["split"] == "holdout")
    train = sum(1 for r in rows if r["split"] == "train")
    return {
        "total_rows": len(rows),
        "holdout": holdout,
        "train": train,
        "holdout_fraction_actual": (holdout / len(rows)) if rows else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic hold-out split for Track C (REC-02)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to signals database (default: canonical DISCOVERY_DB_PATH)",
    )
    parser.add_argument("--out", type=Path, default=Path(DEFAULT_OUT_PATH))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--json", action="store_true", help="Emit JSON summary to stdout")
    args = parser.parse_args(argv)
    try:
        args.db = Path(resolve_db_path_env(args.db))
    except InTreeDatabaseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        candidates = query_candidates(args.db)
    except (sqlite3.Error, FileNotFoundError) as e:
        print(f"ERROR: failed to query signals.db: {e}", file=sys.stderr)
        return 2

    if not candidates:
        print("ERROR: zero candidates from operational collectors", file=sys.stderr)
        return 1

    labelled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = build_split_rows(candidates, args.seed, args.holdout_fraction, labelled_at)

    try:
        write_csv(args.out, rows)
    except OSError as e:
        print(f"ERROR: failed to write {args.out}: {e}", file=sys.stderr)
        return 2

    summary = summarize(rows)
    summary["out"] = str(args.out)
    summary["seed"] = args.seed
    summary["holdout_fraction_target"] = args.holdout_fraction

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Wrote {summary['total_rows']} rows to {args.out}")
        print(f"  holdout: {summary['holdout']} ({summary['holdout_fraction_actual']:.3f})")
        print(f"  train:   {summary['train']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
