"""Shared fixtures for quality ops tests."""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storage.signal_store import SignalStore


def _utc_iso(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _insert_signal(conn: sqlite3.Connection, **overrides) -> int:
    """Insert a minimal valid signal row and return its id."""
    defaults = {
        "signal_type": "test_signal",
        "source_api": "test_source",
        "canonical_key": f"domain:test{id(overrides)}.com",
        "company_name": "Test Company",
        "confidence": 0.75,
        "raw_data": json.dumps({"description": "A test company", "url": "https://test.com"}),
        "detected_at": _utc_iso(1),
        "created_at": _utc_iso(0),
    }
    defaults.update(overrides)
    cur = conn.execute(
        """
        INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, created_at)
        VALUES (:signal_type, :source_api, :canonical_key, :company_name,
                :confidence, :raw_data, :detected_at, :created_at)
        """,
        defaults,
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def quality_db(tmp_path):
    """Create a fresh DB with all migrations (including 25) + quality tables.

    Yields (db_path, store) where store is the SignalStore instance.
    FK constraints are ON.
    """
    db_path = tmp_path / "quality_test.db"

    store = SignalStore(str(db_path))
    asyncio.get_event_loop().run_until_complete(store.initialize())

    # Also ensure quality tables via the sync helper (idempotent)
    from ops.quality.db import quality_conn

    with quality_conn(str(db_path)) as conn:
        pass  # quality_conn runs ensure_quality_tables on enter

    yield str(db_path), store

    asyncio.get_event_loop().run_until_complete(store.close())


@pytest.fixture
def quality_db_with_signals(quality_db):
    """quality_db + 5 sample signals inserted.

    Yields (db_path, store, signal_ids).
    """
    db_path, store = quality_db
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    signal_ids = []
    sources = ["github", "sec_edgar", "hacker_news", "job_postings", "news_api"]
    for i, src in enumerate(sources):
        sid = _insert_signal(
            conn,
            signal_type=f"type_{i}",
            source_api=src,
            canonical_key=f"domain:company{i}.com",
            company_name=f"Company {i}",
            confidence=0.5 + i * 0.1,
            raw_data=json.dumps({
                "description": f"Description for company {i}",
                "url": f"https://company{i}.com",
                "domain": f"company{i}.com",
            }),
            detected_at=_utc_iso(i),
        )
        signal_ids.append(sid)

    conn.close()
    yield db_path, store, signal_ids


@pytest.fixture
def sample_signal_data():
    """Return a valid signal dict suitable for insertion."""
    return {
        "signal_type": "github_spike",
        "source_api": "github",
        "canonical_key": "domain:sample.ai",
        "company_name": "Sample AI",
        "confidence": 0.85,
        "raw_data": json.dumps({
            "description": "An AI-powered consumer health app",
            "url": "https://sample.ai",
            "domain": "sample.ai",
            "title": "Sample AI",
        }),
        "detected_at": _utc_iso(2),
        "created_at": _utc_iso(0),
    }
