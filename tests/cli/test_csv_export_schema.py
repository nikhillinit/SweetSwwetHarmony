"""Tests for Task 2.9: CSV export with functional schema columns.

Verifies:
- CSV export includes new columns (problem_solved, customer_archetype, schema_confidence, thesis_category, thesis_rationale)
- Signals without schema have empty values (not NULL text)
- Signals with schema show correct values
- Advisory schema shows * suffix on archetype
- Column count matches header count
"""

import csv
import os
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


# ── Helpers ──────────────────────────────────────────────────────────────

async def _create_test_db(db_path: str) -> None:
    """Create a minimal test DB with required tables and test data."""
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
            customer_text TEXT,
            approach_text TEXT,
            customer_archetype TEXT,
            problem_archetypes TEXT,
            schema_confidence REAL,
            is_advisory BOOLEAN NOT NULL DEFAULT 0,
            evidence_signal_ids TEXT,
            extraction_model TEXT,
            extraction_prompt_version TEXT,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            superseded_by INTEGER,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE(company_id, schema_version)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS thesis_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            canonical_key TEXT,
            keyword_score REAL,
            keyword_category TEXT,
            negative_keywords TEXT,
            thesis_match BOOLEAN,
            thesis_fit_score REAL,
            category TEXT,
            stage_estimate TEXT,
            confidence REAL,
            rationale TEXT,
            key_signals TEXT,
            prompt_version TEXT,
            model TEXT,
            classified_at TEXT,
            competitor_flag BOOLEAN DEFAULT 0,
            competitor_match TEXT
        )
    """)

    # Insert test signals
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, detected_at, company_id)
        VALUES (1, 'funding', 'sec_edgar', 'domain:food.co', 'FoodCo', 0.8, '2026-01-15T00:00:00Z', 'comp-food')
    """)
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, detected_at, company_id)
        VALUES (2, 'launch', 'github', 'domain:tech.co', 'TechCo', 0.6, '2026-01-16T00:00:00Z', 'comp-tech')
    """)
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name, confidence, detected_at, company_id)
        VALUES (3, 'news', 'news_api', 'domain:adv.co', 'AdvisoryCo', 0.5, '2026-01-17T00:00:00Z', 'comp-adv')
    """)

    # Functional schema for FoodCo (non-advisory)
    await db.execute("""
        INSERT INTO functional_schemas
            (company_id, schema_version, problem_solved_text, customer_archetype,
             schema_confidence, is_advisory, is_active)
        VALUES ('comp-food', 1, 'Healthy meal delivery for families', 'parents', 0.85, 0, 1)
    """)

    # Functional schema for AdvisoryCo (advisory — low confidence)
    await db.execute("""
        INSERT INTO functional_schemas
            (company_id, schema_version, problem_solved_text, customer_archetype,
             schema_confidence, is_advisory, is_active)
        VALUES ('comp-adv', 1, 'Some advisory product', 'foodies', 0.35, 1, 1)
    """)

    # Thesis classification for FoodCo
    await db.execute("""
        INSERT INTO thesis_classifications
            (signal_id, canonical_key, category, rationale, classified_at)
        VALUES (1, 'domain:food.co', 'consumer_cpg', 'DTC food delivery', '2026-01-15T00:00:00Z')
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


# ── Tests ────────────────────────────────────────────────────────────────

EXPECTED_COLUMNS = [
    "signal_id", "company_name", "canonical_key", "confidence",
    "signal_type", "source_api", "detected_at", "status", "company_id",
    "problem_solved", "customer_archetype", "schema_confidence",
    "thesis_category", "thesis_rationale",
]


class TestCSVExportSchema:
    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_export.db")

    @pytest.fixture
    def output_path(self, tmp_path):
        return str(tmp_path / "export.csv")

    @pytest.mark.asyncio
    async def test_csv_has_new_columns(self, db_path, output_path):
        """CSV export includes all new columns in header."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)

            assert header == EXPECTED_COLUMNS
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_signal_with_schema_shows_values(self, db_path, output_path):
        """Signal with functional schema shows correct values."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            food_row = next(r for r in rows if r["company_name"] == "FoodCo")
            assert food_row["problem_solved"] == "Healthy meal delivery for families"
            assert food_row["customer_archetype"] == "parents"
            assert food_row["schema_confidence"] == "0.85"
            assert food_row["thesis_category"] == "consumer_cpg"
            assert food_row["thesis_rationale"] == "DTC food delivery"
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_signal_without_schema_has_empty_values(self, db_path, output_path):
        """Signal without functional schema has empty string values."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            tech_row = next(r for r in rows if r["company_name"] == "TechCo")
            assert tech_row["problem_solved"] == ""
            assert tech_row["customer_archetype"] == ""
            assert tech_row["schema_confidence"] == ""
            assert tech_row["thesis_category"] == ""
            assert tech_row["thesis_rationale"] == ""
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_advisory_schema_has_star_suffix(self, db_path, output_path):
        """Advisory schema shows * suffix on customer_archetype."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            adv_row = next(r for r in rows if r["company_name"] == "AdvisoryCo")
            assert adv_row["customer_archetype"] == "foodies*"
            assert adv_row["schema_confidence"] == "0.35"
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_column_count_matches_header(self, db_path, output_path):
        """Every row has the same number of columns as the header."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                for i, row in enumerate(reader):
                    assert len(row) == len(header), f"Row {i} has {len(row)} cols, header has {len(header)}"
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_non_advisory_schema_no_star(self, db_path, output_path):
        """Non-advisory schema does NOT have * suffix."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            food_row = next(r for r in rows if r["company_name"] == "FoodCo")
            assert food_row["customer_archetype"] == "parents"
            assert "*" not in food_row["customer_archetype"]
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_three_rows_exported(self, db_path, output_path):
        """All three test signals are exported."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_export_queue

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = Namespace(db_path=db_path, status=None, min_confidence=None, days=None, out=output_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_export_queue(args)

            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Header + 3 data rows
            assert len(rows) == 4
        finally:
            await real_db.close()
