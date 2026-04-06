"""Tests for scripts.build_founder_watchlist — verifies dedupe, cap, fallback."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from scripts.build_founder_watchlist import (
    CSV_HEADER,
    build_watchlist,
)


def _make_signals_db_with_founders(path: Path, n_founders: int = 3) -> None:
    """Build a fake signals.db with founders + promoted company_files joins."""
    conn = sqlite3.connect(str(path))
    try:
        # Minimal signals + company_files schema needed by the queries
        conn.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT,
                source_api TEXT,
                canonical_key TEXT NOT NULL,
                company_name TEXT,
                confidence REAL,
                raw_data TEXT,
                detected_at TEXT,
                created_at TEXT
            );
            CREATE TABLE company_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL UNIQUE,
                company_name TEXT,
                canonical_key TEXT NOT NULL,
                status TEXT NOT NULL,
                source_apis TEXT NOT NULL DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                promoted_at TEXT,
                archived_at TEXT,
                metadata TEXT
            );
            CREATE TABLE founders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT,
                founder_key TEXT,
                name TEXT,
                email TEXT,
                linkedin_url TEXT,
                github_username TEXT,
                twitter_handle TEXT,
                current_title TEXT,
                current_company TEXT,
                bio TEXT,
                location TEXT,
                is_serial_founder BOOLEAN DEFAULT 0,
                is_technical BOOLEAN DEFAULT 0,
                has_faang_experience BOOLEAN DEFAULT 0,
                has_startup_experience BOOLEAN DEFAULT 0,
                has_domain_expertise BOOLEAN DEFAULT 0,
                previous_exits INTEGER DEFAULT 0,
                years_experience INTEGER DEFAULT 0,
                founder_score REAL DEFAULT 0.0,
                score_calculated_at TEXT,
                raw_data TEXT,
                source_api TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE founder_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                founder_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                relationship TEXT DEFAULT 'founder',
                created_at TEXT NOT NULL,
                UNIQUE(founder_id, signal_id)
            );
            CREATE TABLE suppression_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                notion_page_id TEXT NOT NULL,
                status TEXT NOT NULL,
                company_name TEXT,
                cached_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                metadata TEXT
            );
            """
        )
        ts = "2026-04-06T00:00:00Z"
        for i in range(n_founders):
            ck = f"domain:co{i}.ai"
            conn.execute(
                "INSERT INTO signals (signal_type, source_api, canonical_key, "
                "company_name, confidence, raw_data, detected_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("domain_registration", "domain_whois", ck, f"Co{i}", 0.6, "{}", ts, ts),
            )
            sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO company_files (company_id, company_name, canonical_key, "
                "status, source_apis, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"co_{i}", f"Co{i}", ck, "promoted", '["domain_whois"]', ts, ts),
            )
            conn.execute(
                "INSERT INTO founders (name, github_username, linkedin_url, "
                "source_api, first_seen_at, last_updated_at, created_at, founder_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"Founder {i}",
                    f"founder_{i}_gh",
                    f"https://linkedin.com/in/founder_{i}",
                    "linkedin",
                    ts,
                    ts,
                    ts,
                    0.5 + i * 0.1,
                ),
            )
            fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO founder_signals (founder_id, signal_id, created_at) "
                "VALUES (?, ?, ?)",
                (fid, sid, ts),
            )
        conn.commit()
    finally:
        conn.close()


def _read_csv(path: Path) -> list:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_writes_csv_with_expected_header(tmp_path: Path):
    prod = tmp_path / "fake_signals.db"
    _make_signals_db_with_founders(prod, n_founders=2)
    out = tmp_path / "shadow" / "founder_watchlist.csv"

    n = build_watchlist(
        output=out,
        limit=500,
        production_db=prod,
        seed_path=tmp_path / "no_such_seed.csv",
    )
    assert n == 2
    assert out.exists()

    rows = _read_csv(out)
    assert len(rows) == 2
    for r in rows:
        assert set(CSV_HEADER) <= set(r.keys())
        assert r["source"] == "promoted_company"
        assert r["github_username"].startswith("founder_")


def test_dedupe_keeps_one_per_github_username(tmp_path: Path):
    """A founder appearing twice (e.g., once via promoted, once via tracked) is dedup'd."""
    prod = tmp_path / "fake_signals.db"
    _make_signals_db_with_founders(prod, n_founders=1)

    # Add a suppression_cache row for the same canonical_key with Tracking status
    # so the founder appears in BOTH branches
    conn = sqlite3.connect(str(prod))
    try:
        conn.execute(
            "INSERT INTO suppression_cache (canonical_key, notion_page_id, status, "
            "company_name, cached_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "domain:co0.ai",
                "fake_page_id",
                "Tracking",
                "Co0",
                "2026-04-06T00:00:00Z",
                "2026-05-06T00:00:00Z",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    out = tmp_path / "shadow" / "founder_watchlist.csv"
    n = build_watchlist(
        output=out,
        limit=500,
        production_db=prod,
        seed_path=tmp_path / "no_such_seed.csv",
    )
    assert n == 1, "Same founder should be deduped across both source branches"


def test_cap_respects_limit(tmp_path: Path):
    prod = tmp_path / "fake_signals.db"
    _make_signals_db_with_founders(prod, n_founders=10)
    out = tmp_path / "shadow" / "founder_watchlist.csv"
    n = build_watchlist(
        output=out,
        limit=3,
        production_db=prod,
        seed_path=tmp_path / "no_such_seed.csv",
    )
    assert n == 3


def test_falls_back_to_manual_seed_when_db_empty(tmp_path: Path):
    prod = tmp_path / "fake_signals.db"
    _make_signals_db_with_founders(prod, n_founders=0)
    seed = tmp_path / "manual_seed.csv"
    seed.write_text(
        "founder_id,full_name,github_username,linkedin_url,associated_company_id\n"
        "manual_1,Test Founder,test_gh,https://linkedin.com/in/test,\n",
        encoding="utf-8",
    )

    out = tmp_path / "shadow" / "founder_watchlist.csv"
    n = build_watchlist(
        output=out,
        limit=500,
        production_db=prod,
        seed_path=seed,
    )
    assert n == 1
    rows = _read_csv(out)
    assert rows[0]["source"] == "manual_seed"
    assert rows[0]["github_username"] == "test_gh"


def test_returns_zero_when_no_sources_available(tmp_path: Path):
    prod = tmp_path / "fake_signals.db"
    _make_signals_db_with_founders(prod, n_founders=0)
    out = tmp_path / "shadow" / "founder_watchlist.csv"
    n = build_watchlist(
        output=out,
        limit=500,
        production_db=prod,
        seed_path=tmp_path / "no_such_seed.csv",
    )
    assert n == 0


def test_dry_run_does_not_write_file(tmp_path: Path):
    prod = tmp_path / "fake_signals.db"
    _make_signals_db_with_founders(prod, n_founders=2)
    out = tmp_path / "shadow" / "founder_watchlist.csv"
    n = build_watchlist(
        output=out,
        limit=500,
        production_db=prod,
        seed_path=tmp_path / "no_such_seed.csv",
        dry_run=True,
    )
    assert n == 2
    assert not out.exists()
