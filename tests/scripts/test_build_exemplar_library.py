"""Tests for scripts/build_exemplar_library.py — Phase 3 Task 3.6."""

from __future__ import annotations

import os
import pickle
import sqlite3
from pathlib import Path

import pytest

from scripts.build_exemplar_library import (
    build_exemplar_library,
    _make_exemplar_key,
    _make_description,
    _infer_category,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap_db(db_path: str) -> None:
    """Create minimal schema for exemplar library tests."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY,
            canonical_key TEXT,
            company_id TEXT,
            company_name TEXT,
            raw_data TEXT DEFAULT '{}',
            source_api TEXT,
            confidence REAL DEFAULT 0.5,
            signal_type TEXT DEFAULT 'test',
            detected_at TEXT DEFAULT '2026-01-01',
            created_at TEXT DEFAULT '2026-01-01T00:00:00Z'
        );
        CREATE TABLE IF NOT EXISTS signal_quality_metrics (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER,
            human_label TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS thesis_classifications (
            id INTEGER PRIMARY KEY,
            signal_id INTEGER,
            category TEXT,
            rationale TEXT
        );
        CREATE TABLE IF NOT EXISTS thesis_exemplars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exemplar_key TEXT NOT NULL,
            canonical_key TEXT,
            company_name TEXT,
            human_label TEXT NOT NULL DEFAULT 'TP',
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            corpus_text TEXT NOT NULL,
            tfidf_vector BLOB,
            vectorizer_version TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'auto',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(exemplar_key, vectorizer_version)
        );
    """)
    conn.commit()
    conn.close()


def _insert_signal(conn, signal_id, name, label, raw_data=None, category=None):
    """Insert a signal + label + optional thesis classification."""
    conn.execute(
        "INSERT INTO signals (id, canonical_key, company_id, company_name, raw_data, source_api) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (signal_id, f"domain:{name.lower()}.com", f"cid_{signal_id}", name,
         raw_data or '{"description": "' + name + ' is a consumer product"}', "github"),
    )
    conn.execute(
        "INSERT INTO signal_quality_metrics (signal_id, human_label, notes) VALUES (?, ?, ?)",
        (signal_id, label, f"Labeled {label}"),
    )
    if category:
        conn.execute(
            "INSERT INTO thesis_classifications (signal_id, category, rationale) VALUES (?, ?, ?)",
            (signal_id, category, "auto"),
        )
    conn.commit()


class _FakeStore:
    """Minimal async store wrapper around sqlite3 for tests."""
    def __init__(self, db_path):
        import aiosqlite
        self._db_path = db_path
        self._db = None

    async def initialize(self):
        import aiosqlite
        self._db = await aiosqlite.connect(self._db_path)

    async def close(self):
        if self._db:
            await self._db.close()


def _create_vectorizer(vectorizer_dir, version):
    """Train and save a minimal vectorizer."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    import joblib

    vectorizer = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), min_df=1)
    vectorizer.fit([
        "healthy snacks organic food consumer",
        "fitness wellness app health tech",
        "enterprise saas b2b developer tools",
    ])
    os.makedirs(vectorizer_dir, exist_ok=True)
    path = os.path.join(vectorizer_dir, f"case_law_{version}.joblib")
    joblib.dump(vectorizer, path)
    return path


# ---------------------------------------------------------------------------
# Unit tests — helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_make_exemplar_key_from_canonical(self):
        sig = {"canonical_key": "domain:acme.com", "company_name": "Acme", "signal_id": 1}
        key = _make_exemplar_key(sig)
        assert key.startswith("auto_")
        assert len(key) == 17  # "auto_" + 12 hex chars

    def test_make_exemplar_key_stable(self):
        sig = {"canonical_key": "domain:acme.com", "company_name": "Acme", "signal_id": 1}
        assert _make_exemplar_key(sig) == _make_exemplar_key(sig)

    def test_make_description(self):
        sig = {"company_name": "SnackCo", "label_reason": "CPG fit", "source_api": "github"}
        desc = _make_description(sig)
        assert "SnackCo" in desc
        assert "CPG fit" in desc
        assert "github" in desc

    def test_infer_category_from_thesis(self):
        assert _infer_category({"thesis_category": "consumer_cpg"}) == "consumer_cpg"

    def test_infer_category_default(self):
        assert _infer_category({"thesis_category": None}) == "general"
        assert _infer_category({}) == "general"


# ---------------------------------------------------------------------------
# Integration tests — build_exemplar_library
# ---------------------------------------------------------------------------

class TestBuildExemplarLibrary:
    @pytest.fixture
    def setup_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _bootstrap_db(db_path)
        vectorizer_dir = str(tmp_path / "vectorizers")
        _create_vectorizer(vectorizer_dir, "v1.0.0")
        return db_path, vectorizer_dir

    @pytest.mark.asyncio
    async def test_builds_exemplars_from_tp(self, setup_db):
        db_path, vectorizer_dir = setup_db
        conn = sqlite3.connect(db_path)
        _insert_signal(conn, 1, "SnackCo", "TP", category="consumer_cpg")
        _insert_signal(conn, 2, "FitApp", "TP", category="health_tech")
        _insert_signal(conn, 3, "B2BTool", "FP")  # Should be excluded
        conn.close()

        store = _FakeStore(db_path)
        await store.initialize()
        try:
            result = await build_exemplar_library(
                store, version="v1.0.0", vectorizer_dir=vectorizer_dir,
            )
        finally:
            await store.close()

        assert result["exemplar_count"] == 2
        assert "consumer_cpg" in result["categories"]
        assert "health_tech" in result["categories"]

        # Verify rows in DB
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT * FROM thesis_exemplars").fetchall()
        conn.close()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_dry_run_no_write(self, setup_db):
        db_path, vectorizer_dir = setup_db
        conn = sqlite3.connect(db_path)
        _insert_signal(conn, 1, "SnackCo", "TP")
        conn.close()

        store = _FakeStore(db_path)
        await store.initialize()
        try:
            result = await build_exemplar_library(
                store, version="v1.0.0", dry_run=True, vectorizer_dir=vectorizer_dir,
            )
        finally:
            await store.close()

        assert result["exemplar_count"] == 1

        # DB should still be empty
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT * FROM thesis_exemplars").fetchall()
        conn.close()
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_empty_tp_returns_zero(self, setup_db):
        db_path, vectorizer_dir = setup_db
        conn = sqlite3.connect(db_path)
        _insert_signal(conn, 1, "B2BTool", "FP")  # Only FP
        conn.close()

        store = _FakeStore(db_path)
        await store.initialize()
        try:
            result = await build_exemplar_library(
                store, version="v1.0.0", vectorizer_dir=vectorizer_dir,
            )
        finally:
            await store.close()

        assert result["exemplar_count"] == 0

    @pytest.mark.asyncio
    async def test_missing_vectorizer_returns_error(self, setup_db):
        db_path, _ = setup_db
        store = _FakeStore(db_path)
        await store.initialize()
        try:
            result = await build_exemplar_library(
                store, version="v9.9.9", vectorizer_dir=str(Path(db_path).parent / "missing"),
            )
        finally:
            await store.close()

        assert result.get("error")
        assert result["exemplar_count"] == 0

    @pytest.mark.asyncio
    async def test_prunes_old_versions(self, setup_db):
        db_path, vectorizer_dir = setup_db
        conn = sqlite3.connect(db_path)
        _insert_signal(conn, 1, "SnackCo", "TP")
        # Pre-insert old-version exemplar
        conn.execute(
            "INSERT INTO thesis_exemplars (exemplar_key, category, description, corpus_text, "
            "tfidf_vector, vectorizer_version, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("old_key", "old", "old desc", "old text", b"old_vec", "v0.9.0", "auto"),
        )
        conn.commit()
        conn.close()

        store = _FakeStore(db_path)
        await store.initialize()
        try:
            result = await build_exemplar_library(
                store, version="v1.0.0", vectorizer_dir=vectorizer_dir,
            )
        finally:
            await store.close()

        assert result.get("pruned", 0) >= 1

        # Only new version should remain
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT vectorizer_version FROM thesis_exemplars").fetchall()
        conn.close()
        assert all(r[0] == "v1.0.0" for r in rows)

    @pytest.mark.asyncio
    async def test_exemplar_vectors_are_valid(self, setup_db):
        db_path, vectorizer_dir = setup_db
        conn = sqlite3.connect(db_path)
        _insert_signal(conn, 1, "SnackCo", "TP")
        conn.close()

        store = _FakeStore(db_path)
        await store.initialize()
        try:
            await build_exemplar_library(
                store, version="v1.0.0", vectorizer_dir=vectorizer_dir,
            )
        finally:
            await store.close()

        # Verify vector is deserializable scipy sparse
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT tfidf_vector FROM thesis_exemplars").fetchone()
        conn.close()
        vec = pickle.loads(row[0])
        assert hasattr(vec, "toarray")  # scipy sparse matrix
