"""Tests for Task 3.10: Triage detail CLI + Sim column in triage list.

Verifies:
- Triage list shows Sim column header
- Sim column shows em-dash when no vectorizer available
- Triage detail shows company info, functional schema, and intelligence
- Triage detail handles missing signal gracefully
- Triage detail shows "no schema" for signals without functional schema
"""

import json
import os
import pickle
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


# ── Helpers ──────────────────────────────────────────────────────────────


async def _create_test_db(db_path: str, include_intelligence: bool = False) -> None:
    """Create a minimal test DB with functional_schemas and optionally intelligence tables."""
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA foreign_keys = OFF")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT,
            company_name TEXT,
            confidence REAL DEFAULT 0.5,
            raw_data TEXT DEFAULT '{}',
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            company_id TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS signal_processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            notion_page_id TEXT,
            processed_at TEXT,
            metadata TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS functional_schemas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            problem_solved_text TEXT,
            customer_archetype TEXT,
            approach_text TEXT,
            schema_confidence REAL,
            is_advisory BOOLEAN NOT NULL DEFAULT 0,
            evidence_signal_ids TEXT,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            superseded_by INTEGER,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(company_id, schema_version)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS thesis_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            category TEXT,
            rationale TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)

    # Signal 1: FoodCo — has schema
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, company_id)
        VALUES (1, 'funding', 'sec_edgar', 'domain:food.co', 'FoodCo', 0.82,
                '{"description": "Meal kit startup for families"}', '2026-01-15T00:00:00Z', 'comp-food')
    """)
    # Signal 2: TechCo — no schema
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, company_id)
        VALUES (2, 'launch', 'github', 'domain:tech.co', 'TechCo', 0.55,
                '{"description": "Some tech product"}', '2026-01-16T00:00:00Z', 'comp-tech')
    """)

    for sid in [1, 2]:
        await db.execute(
            "INSERT INTO signal_processing (signal_id, status) VALUES (?, 'pending')",
            (sid,),
        )

    # FoodCo schema
    await db.execute("""
        INSERT INTO functional_schemas
            (company_id, schema_version, problem_solved_text, customer_archetype,
             approach_text, schema_confidence, is_advisory, evidence_signal_ids, is_active)
        VALUES ('comp-food', 1, 'Healthy meal delivery for families', 'parents',
                'DTC subscription model', 0.85, 0, '[1]', 1)
    """)

    # Thesis classification for FoodCo
    await db.execute("""
        INSERT INTO thesis_classifications (signal_id, category, rationale)
        VALUES (1, 'Consumer CPG', 'Meal kit delivery fits CPG thesis')
    """)

    if include_intelligence:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS precedents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                canonical_key TEXT NOT NULL,
                company_id TEXT,
                human_label TEXT NOT NULL,
                corpus_text TEXT NOT NULL,
                tfidf_vector BLOB,
                similarity_text_hash TEXT,
                signal_created_at TEXT,
                vectorizer_version TEXT NOT NULL,
                label_reason TEXT,
                source_api TEXT,
                confidence REAL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                company_name TEXT DEFAULT ''
            )
        """)
        await db.execute("""
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
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
        """)

    await db.commit()
    await db.close()


async def _get_mock_store(db_path: str):
    """Create a mock SignalStore with real DB connection."""
    mock_store = MagicMock()
    real_db = await aiosqlite.connect(db_path)
    mock_store._db = real_db
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()
    return mock_store, real_db


# ── Triage List — Sim Column Tests ───────────────────────────────────────


class TestTriageListSimColumn:

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_triage_sim.db")

    def _make_args(self, db_path, **kwargs):
        defaults = dict(
            db_path=db_path,
            status="pending",
            min_confidence=None,
            limit=20,
            compact=True,
            verbose=False,
        )
        defaults.update(kwargs)
        return Namespace(**defaults)

    @pytest.mark.asyncio
    async def test_header_shows_sim_column(self, db_path, capsys):
        """Triage header includes Sim column."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            assert "Sim" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_sim_shows_dash_when_no_vectorizer(self, db_path, capsys):
        """Sim column shows em-dash when no vectorizer is available."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            # Each row should have em-dash for Sim since no vectorizer
            lines = [l for l in output.split("\n") if "FoodCo" in l or "TechCo" in l]
            for line in lines:
                assert "\u2014" in line

        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_existing_columns_still_present(self, db_path, capsys):
        """Existing columns (Problem, Archetype, Conf) still present after adding Sim."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            assert "Problem" in output
            assert "Archetype" in output
            assert "Conf" in output
            assert "Source" in output
        finally:
            await real_db.close()


# ── Triage Detail Tests ──────────────────────────────────────────────────


class TestTriageDetail:

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_triage_detail.db")

    def _make_args(self, db_path, signal_id, **kwargs):
        defaults = dict(db_path=db_path, signal_id=signal_id)
        defaults.update(kwargs)
        return Namespace(**defaults)

    @pytest.mark.asyncio
    async def test_detail_shows_company_info(self, db_path, capsys):
        """Detail view shows company name, confidence, and status."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=1)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            assert "FoodCo" in output
            assert "0.82" in output
            assert "pending" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_detail_shows_functional_schema(self, db_path, capsys):
        """Detail view shows functional schema (problem, archetype, approach)."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=1)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            assert "Healthy meal delivery for families" in output
            assert "parents" in output
            assert "DTC subscription model" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_detail_shows_no_schema(self, db_path, capsys):
        """Detail view shows '[no schema]' for signal without functional schema."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=2)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            assert "[no schema]" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_detail_shows_thesis(self, db_path, capsys):
        """Detail view shows thesis classification when available."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=1)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            assert "Consumer CPG" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_detail_invalid_signal_id(self, db_path, capsys):
        """Detail view shows error for non-existent signal."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=999)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            assert "not found" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_detail_intelligence_not_available(self, db_path, capsys):
        """Detail view handles missing vectorizer gracefully."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=1)
            # Patch load_latest_metadata to return None (no vectorizer)
            with patch("run_pipeline.SignalStore", return_value=mock_store), \
                 patch("intelligence.vectorizer_config.load_latest_metadata", return_value=None):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            # Should show intelligence unavailable message (no vectorizer)
            assert "No vectorizer" in output or "Not available" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_detail_shows_detected_at(self, db_path, capsys):
        """Detail view shows detection date and source."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_detail

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, signal_id=1)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_detail(args)

            output = capsys.readouterr().out
            assert "2026-01-15" in output
            assert "sec_edgar" in output
        finally:
            await real_db.close()
