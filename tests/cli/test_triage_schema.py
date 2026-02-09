"""Tests for Task 2.10: Triage CLI with functional schema columns.

Verifies:
- Triage list shows Problem, Archetype, schema Conf columns
- Advisory schemas show * suffix on archetype
- Signals without schema show placeholder values
- Verbose mode shows approach_text and evidence_signal_ids
"""

import json
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


# ── Helpers ──────────────────────────────────────────────────────────────

async def _create_test_db(db_path: str) -> None:
    """Create a minimal test DB with functional_schemas data."""
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

    # Signal 1: FoodCo — has non-advisory schema
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, company_id)
        VALUES (1, 'funding', 'sec_edgar', 'domain:food.co', 'FoodCo', 0.8,
                '{"description": "Meal kit startup"}', '2026-01-15T00:00:00Z', 'comp-food')
    """)
    # Signal 2: TechCo — no schema
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, company_id)
        VALUES (2, 'launch', 'github', 'domain:tech.co', 'TechCo', 0.6,
                '{"description": "Some tech product"}', '2026-01-16T00:00:00Z', 'comp-tech')
    """)
    # Signal 3: AdvisoryCo — has advisory schema
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, company_id)
        VALUES (3, 'news', 'news_api', 'domain:adv.co', 'AdvisoryCo', 0.5,
                '{"description": "Advisory product"}', '2026-01-17T00:00:00Z', 'comp-adv')
    """)
    # Signal 4: LongProblem — has schema with long problem text
    await db.execute("""
        INSERT INTO signals (id, signal_type, source_api, canonical_key, company_name,
                             confidence, raw_data, detected_at, company_id)
        VALUES (4, 'funding', 'sec_edgar', 'domain:longproblem.co', 'LongProblemCo', 0.7,
                '{"description": "Long problem company"}', '2026-01-18T00:00:00Z', 'comp-long')
    """)

    # All signals are pending
    for sid in [1, 2, 3, 4]:
        await db.execute(
            "INSERT INTO signal_processing (signal_id, status) VALUES (?, 'pending')",
            (sid,),
        )

    # FoodCo schema (non-advisory)
    await db.execute("""
        INSERT INTO functional_schemas
            (company_id, schema_version, problem_solved_text, customer_archetype,
             approach_text, schema_confidence, is_advisory, evidence_signal_ids, is_active)
        VALUES ('comp-food', 1, 'Healthy meal delivery for families', 'parents',
                'DTC subscription model', 0.85, 0, '[1]', 1)
    """)

    # AdvisoryCo schema (advisory)
    await db.execute("""
        INSERT INTO functional_schemas
            (company_id, schema_version, problem_solved_text, customer_archetype,
             approach_text, schema_confidence, is_advisory, evidence_signal_ids, is_active)
        VALUES ('comp-adv', 1, 'Some advisory product', 'foodies',
                'Marketplace approach', 0.35, 1, '[3]', 1)
    """)

    # LongProblemCo schema — problem text longer than 40 chars
    await db.execute("""
        INSERT INTO functional_schemas
            (company_id, schema_version, problem_solved_text, customer_archetype,
             approach_text, schema_confidence, is_advisory, evidence_signal_ids, is_active)
        VALUES ('comp-long', 1,
                'This is a very long problem description that should be truncated in triage output',
                'millennials', 'Mobile app', 0.72, 0, '[4]', 1)
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


class TestTriageSchemaDisplay:

    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_triage.db")

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
    async def test_header_shows_problem_and_archetype(self, db_path, capsys):
        """Triage header includes Problem and Archetype columns."""
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
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_signal_with_schema_shows_problem(self, db_path, capsys):
        """Signal with functional schema shows problem_solved_text."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            assert "Healthy meal delivery for families" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_signal_with_schema_shows_archetype(self, db_path, capsys):
        """Signal with functional schema shows customer_archetype."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            # FoodCo archetype
            assert "parents" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_signal_without_schema_shows_placeholder(self, db_path, capsys):
        """Signal without functional schema shows dash placeholder."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            # TechCo has no schema — should see "[no schema]" or "—"
            lines = [l for l in output.split("\n") if "TechCo" in l]
            assert len(lines) >= 1
            tech_line = lines[0]
            # Should have a dash/placeholder for archetype
            assert "\u2014" in tech_line or "-" in tech_line
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_advisory_archetype_has_star(self, db_path, capsys):
        """Advisory schema shows * suffix on archetype."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            # AdvisoryCo archetype should be "foodies*"
            assert "foodies*" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_non_advisory_archetype_no_star(self, db_path, capsys):
        """Non-advisory schema does NOT have * suffix."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            lines = [l for l in output.split("\n") if "FoodCo" in l]
            assert len(lines) >= 1
            food_line = lines[0]
            assert "parents" in food_line
            assert "parents*" not in food_line
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_long_problem_truncated(self, db_path, capsys):
        """Problem text longer than 40 chars is truncated."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            # Full problem is 81 chars — should be truncated
            assert "This is a very long problem description that should be truncated in triage output" not in output
            # But truncated portion should be present
            assert "This is a very long problem description" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_verbose_shows_approach(self, db_path, capsys):
        """Verbose mode shows approach_text."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, verbose=True)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            assert "DTC subscription model" in output
        finally:
            await real_db.close()

    @pytest.mark.asyncio
    async def test_verbose_shows_evidence_ids(self, db_path, capsys):
        """Verbose mode shows evidence_signal_ids."""
        await _create_test_db(db_path)
        from run_pipeline import cmd_triage_list

        mock_store, real_db = await _get_mock_store(db_path)
        try:
            args = self._make_args(db_path, verbose=True)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd_triage_list(args)

            output = capsys.readouterr().out
            # Evidence IDs should appear in verbose output
            assert "Evidence:" in output or "[1]" in output
        finally:
            await real_db.close()
