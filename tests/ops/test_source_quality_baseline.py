# tests/ops/test_source_quality_baseline.py
"""Verify quality stats CLI supports --source-api filtering."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest


def make_quality_db(tmp_path: Path) -> str:
    """Create a minimal signals.db with quality labels for two sources.

    Uses signal_quality_metrics (the view/table that get_stats_by_source_api
    actually joins against) rather than quality_labels directly.
    min_labeled default is 10 (decided=TP+FP), so we insert enough rows to
    exceed that threshold for at least one source.
    """
    db_path = str(tmp_path / "signals.db")
    con = sqlite3.connect(db_path)
    # Build enough rows so github hits min_labeled=10 (decided >= 10)
    # and hacker_news stays below threshold (decided=1) — but we lower
    # min_labeled=1 in tests so both sources appear.
    signals_rows = ",\n".join(
        f"({i}, 'github', 'domain:gh-{i}.test', 'GH {i}', 0.8, datetime('now', '-30 days'))"
        for i in range(1, 13)  # 12 github rows
    )
    hn_row = "(13, 'hacker_news', 'domain:hn-a.test', 'HN A', 0.6, datetime('now', '-29 days'))"
    all_signals = signals_rows + ",\n" + hn_row

    sp_rows = ",".join(f"({i},{i},'processed')" for i in range(1, 14))

    # 6 TP + 6 FP for github (fp_rate=0.5), 1 FP for hacker_news
    github_labels = ",".join(
        f"({i}, {i}, 'TP')" if i <= 6 else f"({i}, {i}, 'FP')"
        for i in range(1, 13)
    )
    hn_label = "(13, 13, 'FP')"
    all_labels = github_labels + ",\n" + hn_label

    con.executescript(f"""
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            source_api TEXT,
            canonical_key TEXT UNIQUE,
            company_name TEXT,
            confidence REAL,
            detected_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE signal_processing (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER UNIQUE,
            status TEXT DEFAULT 'pending',
            processed_at TEXT,
            notion_page_id TEXT,
            error_message TEXT
        );
        CREATE TABLE signal_quality_metrics (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER UNIQUE,
            human_label TEXT,
            label_source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO signals (id, source_api, canonical_key, company_name, confidence, detected_at)
        VALUES {all_signals};
        INSERT INTO signal_processing (id, signal_id, status) VALUES {sp_rows};
        INSERT INTO signal_quality_metrics (id, signal_id, human_label) VALUES {all_labels};
    """)
    con.commit()
    con.close()
    return db_path


def test_stats_by_source_api_returns_per_source_breakdown(tmp_path):
    from ops.quality.stats import get_stats_by_source_api
    db_path = make_quality_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    # min_labeled=1 so hacker_news (decided=1) also appears
    stats = get_stats_by_source_api(con, days=90, min_labeled=1)
    con.close()
    sources = {s.source_api for s in stats}
    assert "github" in sources
    assert "hacker_news" in sources


def test_github_fp_rate_is_0_5(tmp_path):
    from ops.quality.stats import get_stats_by_source_api
    db_path = make_quality_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    # github has 12 decided signals — well above default min_labeled=10
    stats = get_stats_by_source_api(con, days=90, min_labeled=10)
    con.close()
    gh = next(s for s in stats if s.source_api == "github")
    # 6 TP + 6 FP → fp_rate = 0.5
    assert abs(gh.fp_rate - 0.5) < 0.01, f"Expected fp_rate ≈ 0.5, got {gh.fp_rate}"


def test_stats_cli_accepts_source_api_arg(tmp_path, monkeypatch):
    """The quality stats argparse subcommand must accept --source-api."""
    import argparse
    from ops.quality_cli import register_quality_commands
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register_quality_commands(sub)
    # Must parse without error
    args = parser.parse_args(["quality", "--db", "signals.db", "stats", "--source-api", "github"])
    assert args.source_api == "github"
