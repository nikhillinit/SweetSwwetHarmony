#!/usr/bin/env python3
"""
extract_founder_candidates.py — per-collector founder name extractor for Track E.

Implements REQ REC-04 from .planning/REQUIREMENTS.md per CONTEXT.md D-08
and D-27.

Per-collector handlers (D-27):
    arxiv:        parse authors[] from raw_data JSON; filter to papers
                  whose title or abstract contains a company-pattern
                  token (Inc, Labs, AI, Corp, Studio, ...)
    hacker_news:  parse story_text for "I'm <Name>" / "I am <Name>" /
                  "<Name> here" patterns indicating a self-identified
                  founder. The `author` field is an HN handle, not a
                  real name, so we cannot use it directly.
    news_api:     parse title + description for capitalized phrases
                  near "founder", "CEO", "co-founder"
    rss_feeds:    SKIPPED per D-27 (low-signal raw_data)

Hard constraint (CONTEXT.md D-11):
    NO LinkedIn scraping. Founder names come ONLY from arxiv author
    lists, HN self-introductions, news_api article text.

Output:
    Writes to a candidates buffer file (default: artifacts/rec-04/
    founder_candidates_raw.csv) with the populator's 5-column schema
    plus a `source_collector` column for traceability. Task 2 of plan
    01-09 merges this into scripts/data/founder_watchlist_manual_seed.csv
    and runs the populator.

founder_id convention:
    claude_001..claude_NNN for rows from this script. The existing
    populator emits source=manual_seed for all seed-file rows; the
    founder_id prefix is the post-hoc marker for "Claude vs analyst".

Usage
-----
    python scripts/red-team-hybrid/extract_founder_candidates.py
    python scripts/red-team-hybrid/extract_founder_candidates.py --json
    python scripts/red-team-hybrid/extract_founder_candidates.py --max-per-collector 30
    python scripts/red-team-hybrid/extract_founder_candidates.py --out artifacts/rec-04/founder_candidates_raw.csv

Exit codes
----------
    0  success, candidates written
    1  expected failure (zero candidates from any handler)
    2  operational error
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH: str = "signals.db"
DEFAULT_OUT_PATH: str = "artifacts/rec-04/founder_candidates_raw.csv"
DEFAULT_MAX_PER_COLLECTOR: int = 40
OPERATIONAL_COLLECTORS: tuple[str, ...] = ("arxiv", "hacker_news", "news_api")

# Output columns: populator's 5-column schema + source_collector for traceability
OUTPUT_COLUMNS: tuple[str, ...] = (
    "founder_id",
    "full_name",
    "github_username",
    "linkedin_url",
    "associated_company_id",
    "source_collector",
    "extraction_evidence",
)

# Company-pattern tokens for arxiv filter (D-27)
ARXIV_COMPANY_TOKENS = re.compile(
    r"\b(Inc|Inc\.|LLC|Labs|Lab|AI|Corp|Corporation|Studio|Studios|"
    r"Technologies|Technology|Health|Bio|Diagnostics|Therapeutics|"
    r"Co\.|Company|Group|Capital|Ventures)\b"
)

# HN self-intro patterns (D-27)
HN_SELF_INTRO = re.compile(
    r"\b(?:I[' ]?m|I am|This is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
    r"(?:\s*[,.\-]|,?\s+(?:and\s+)?(?:I|the|a|co-?founder|founder|CEO))",
    re.IGNORECASE | re.MULTILINE,
)

# news_api capitalized-phrase-near-founder pattern (D-27)
# Match a 2-3 word capitalized name near founder/CEO/co-founder
NEWS_FOUNDER = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})"
    r"(?:\s*[,.\-]\s*|\s+is\s+|\s+the\s+|\s+,\s*)"
    r"(?:co-?founder|founder|CEO|chief executive)",
    re.IGNORECASE,
)


def _ro_conn(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"signals DB not found: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _safe_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def extract_arxiv(conn: sqlite3.Connection, max_rows: int) -> list[dict[str, str]]:
    """Extract founder candidates from arxiv signals."""
    rows = conn.execute(
        "SELECT id, raw_data FROM signals WHERE source_api = 'arxiv'"
    ).fetchall()
    candidates: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for sid, raw_text in rows:
        data = _safe_json(raw_text)
        if not data:
            continue
        title = (data.get("title") or "")
        abstract = (data.get("abstract") or "")
        haystack = f"{title}\n{abstract}"
        if not ARXIV_COMPANY_TOKENS.search(haystack):
            continue
        authors = data.get("authors") or []
        if not isinstance(authors, list):
            continue
        for author in authors:
            if not isinstance(author, str):
                continue
            name = author.strip()
            if not name or len(name.split()) < 2 or len(name) > 80:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            candidates.append({
                "full_name": name,
                "source_collector": "arxiv",
                "extraction_evidence": f"signal_id={sid} title-token-match",
            })
            if len(candidates) >= max_rows:
                return candidates
    return candidates


def extract_hacker_news(conn: sqlite3.Connection, max_rows: int) -> list[dict[str, str]]:
    """Extract founder candidates from hacker_news Show HN posts."""
    rows = conn.execute(
        "SELECT id, raw_data FROM signals WHERE source_api = 'hacker_news'"
    ).fetchall()
    candidates: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for sid, raw_text in rows:
        data = _safe_json(raw_text)
        if not data:
            continue
        story_text = data.get("story_text") or ""
        title = data.get("title") or ""
        haystack = f"{title}\n{story_text}"
        for match in HN_SELF_INTRO.finditer(haystack):
            name = match.group(1).strip()
            if not name or len(name.split()) < 2 or len(name) > 80:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            candidates.append({
                "full_name": name,
                "source_collector": "hacker_news",
                "extraction_evidence": f"signal_id={sid} self-intro-pattern",
            })
            if len(candidates) >= max_rows:
                return candidates
    return candidates


def extract_news_api(conn: sqlite3.Connection, max_rows: int) -> list[dict[str, str]]:
    """Extract founder candidates from news_api article text."""
    rows = conn.execute(
        "SELECT id, raw_data FROM signals WHERE source_api = 'news_api'"
    ).fetchall()
    candidates: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for sid, raw_text in rows:
        data = _safe_json(raw_text)
        if not data:
            continue
        title = data.get("title") or ""
        description = data.get("description") or ""
        haystack = f"{title}\n{description}"
        for match in NEWS_FOUNDER.finditer(haystack):
            name = match.group(1).strip()
            if not name or len(name.split()) < 2 or len(name) > 80:
                continue
            if name in seen_names:
                continue
            seen_names.add(name)
            candidates.append({
                "full_name": name,
                "source_collector": "news_api",
                "extraction_evidence": f"signal_id={sid} founder-near-name",
            })
            if len(candidates) >= max_rows:
                return candidates
    return candidates


def assign_founder_ids(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    """Stamp claude_NNN founder_id and zero out unused columns per populator schema."""
    result: list[dict[str, str]] = []
    for i, c in enumerate(candidates, start=1):
        result.append({
            "founder_id": f"claude_{i:03d}",
            "full_name": c["full_name"],
            "github_username": "",
            "linkedin_url": "",  # HARD CONSTRAINT D-11: never populated
            "associated_company_id": "",
            "source_collector": c["source_collector"],
            "extraction_evidence": c["extraction_evidence"],
        })
    return result


def write_candidates(out_path: Path, rows: list[dict[str, str]]) -> None:
    """Write the candidates buffer file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        f.write("# Phase 1 Track E founder candidates buffer (REC-04).\n")
        f.write("# Generated by scripts/red-team-hybrid/extract_founder_candidates.py\n")
        f.write("# Per CONTEXT.md D-11: NO LinkedIn scraping. linkedin_url is always empty.\n")
        f.write("# Task 2 of plan 01-09 merges this into scripts/data/founder_watchlist_manual_seed.csv\n")
        f.write("# and runs scripts/build_founder_watchlist.py to regenerate data/shadow/founder_watchlist.csv.\n")
        writer = csv.DictWriter(f, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract founder candidates from signals.db (REC-04).")
    parser.add_argument("--db", type=Path, default=Path(DEFAULT_DB_PATH))
    parser.add_argument("--out", type=Path, default=Path(DEFAULT_OUT_PATH))
    parser.add_argument("--max-per-collector", type=int, default=DEFAULT_MAX_PER_COLLECTOR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        conn = _ro_conn(args.db)
    except (sqlite3.Error, FileNotFoundError) as e:
        print(f"ERROR: failed to open signals.db: {e}", file=sys.stderr)
        return 2

    try:
        arxiv_c = extract_arxiv(conn, args.max_per_collector)
        hn_c = extract_hacker_news(conn, args.max_per_collector)
        news_c = extract_news_api(conn, args.max_per_collector)
    except sqlite3.Error as e:
        print(f"ERROR: query failed: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    merged = arxiv_c + hn_c + news_c
    # Dedupe across collectors on full_name (case-insensitive)
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for c in merged:
        key = c["full_name"].lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    with_ids = assign_founder_ids(deduped)
    try:
        write_candidates(args.out, with_ids)
    except OSError as e:
        print(f"ERROR: failed to write {args.out}: {e}", file=sys.stderr)
        return 2

    summary = {
        "out": str(args.out),
        "total_rows": len(with_ids),
        "arxiv": len(arxiv_c),
        "hacker_news": len(hn_c),
        "news_api": len(news_c),
        "deduped": len(merged) - len(deduped),
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Wrote {summary['total_rows']} unique founder candidates to {args.out}")
        print(f"  arxiv:       {summary['arxiv']}")
        print(f"  hacker_news: {summary['hacker_news']}")
        print(f"  news_api:    {summary['news_api']}")
        print(f"  deduped:     {summary['deduped']}")

    if summary["total_rows"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
