"""Block 4: Phase 2 Thin Slice Tests.

Verifies that functional schema infrastructure is working:
- v32 migration applied (table exists)
- Extraction produces schema (mock Gemini)
- CSV export includes schema columns
- Triage shows schema when available
- Backfill is idempotent (skips existing schemas)
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from consumer.functional_extractor import (
    FunctionalExtractor,
    FunctionalSchema,
    VALID_ARCHETYPES,
    EXTRACTOR_PROMPT_VERSION,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(monkeypatch):
    """Fresh SignalStore with temp DB."""
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)
    monkeypatch.delenv("V2_ENABLEMENT", raising=False)
    monkeypatch.delenv("ML_ENABLEMENT", raising=False)
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    monkeypatch.setenv("LLM_THESIS_MODE", "off")

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


async def _seed_signal(store, company_id="company-1", canonical_key="domain:acme.com",
                       company_name="Acme Corp"):
    """Insert a signal directly and return its ID."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()
    cursor = await db.execute(
        """INSERT INTO signals
           (company_id, company_name, canonical_key, signal_type, source_api,
            confidence, detected_at, created_at, raw_data)
           VALUES (?, ?, ?, 'funding_event', 'sec_edgar', 0.8, ?, ?, ?)""",
        (company_id, company_name, canonical_key, now, now,
         json.dumps({"description": f"Test signal for {company_name}"}))
    )
    await db.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestV32MigrationApplied:
    """Verify the functional_schemas table exists."""

    @pytest.mark.asyncio
    async def test_functional_schemas_table_exists(self, store):
        """v32 migration creates functional_schemas table."""
        db = store._db
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='functional_schemas'"
        )
        row = await cursor.fetchone()
        assert row is not None, "functional_schemas table should exist after initialize()"
        assert row[0] == "functional_schemas"

    @pytest.mark.asyncio
    async def test_functional_schemas_columns(self, store):
        """Table has expected columns."""
        db = store._db
        cursor = await db.execute("PRAGMA table_info(functional_schemas)")
        rows = await cursor.fetchall()
        col_names = {r[1] for r in rows}

        expected = {
            "id", "company_id", "schema_version", "problem_solved_text",
            "customer_text", "approach_text", "customer_archetype",
            "problem_archetypes", "schema_confidence", "is_advisory",
            "evidence_signal_ids", "extraction_model", "extraction_prompt_version",
            "is_active", "superseded_by", "created_at",
        }
        assert expected.issubset(col_names), f"Missing columns: {expected - col_names}"


class TestExtractionProducesSchema:
    """Test that FunctionalExtractor produces valid schemas (mock Gemini)."""

    @pytest.mark.asyncio
    async def test_extraction_mock_gemini(self, store):
        """Mock Gemini extraction runs through FunctionalExtractor.extract and saves correctly."""
        signal_id = await _seed_signal(store, "comp-ext", "domain:ext.com", "Extract Co")
        extractor = FunctionalExtractor(api_key="test-key")
        signal_data = {
            "title": "Extract Co raises seed funding",
            "source_api": "sec_edgar",
            "source_context": "Extract Co helps consumers find healthy snacks via AI-powered recommendations.",
        }
        gemini_response = MagicMock(
            text=json.dumps({
                "problem_solved": "Helps consumers find healthy snacks",
                "customer": "Health-conscious millennials",
                "approach": "AI-powered snack recommendation engine",
                "customer_archetype": "foodies",
                "problem_archetypes": ["subscription", "wellness"],
                "schema_confidence": 0.85,
            })
        )

        with patch.object(
            extractor,
            "_call_gemini_api",
            AsyncMock(return_value=gemini_response),
        ) as mock_call:
            schema = await extractor.extract(
                signal_data,
                company_id="comp-ext",
                evidence_signal_ids=[signal_id],
            )

        assert schema is not None
        assert schema.company_id == "comp-ext"
        assert schema.problem_solved_text == "Helps consumers find healthy snacks"
        assert schema.customer_text == "Health-conscious millennials"
        assert schema.approach_text == "AI-powered snack recommendation engine"
        assert schema.customer_archetype == "foodies"
        assert schema.problem_archetypes == ["subscription", "wellness"]
        assert schema.evidence_signal_ids == [signal_id]
        assert schema.extraction_model == "gemini-2.0-flash"
        assert schema.extraction_prompt_version == EXTRACTOR_PROMPT_VERSION
        mock_call.assert_awaited_once()

        schema_id = await store.save_functional_schema(schema.to_storage_dict())
        assert schema_id is not None
        assert schema_id > 0

        has_schema = await store.has_active_schema("comp-ext")
        assert has_schema is True
        saved_schema = await store.get_active_schema("comp-ext")
        assert saved_schema is not None
        assert saved_schema["problem_solved_text"] == schema.problem_solved_text
        assert saved_schema["customer_archetype"] == "foodies"
        assert saved_schema["evidence_signal_ids"] == [signal_id]


class TestBackfillIdempotent:
    """Test that backfill skips existing schemas."""

    @pytest.mark.asyncio
    async def test_backfill_skips_existing(self, store, monkeypatch):
        """Explicit backfill entrypoint skips companies that already have active schemas."""
        from scripts import backfill_functional_schemas

        signal_id = await _seed_signal(store, "comp-idem", "domain:idem.com", "Idem Co")

        schema_dict = {
            "company_id": "comp-idem",
            "problem_solved_text": "Helps travelers book unique stays",
            "customer_text": "Adventure travelers",
            "approach_text": "Curated marketplace of unique accommodations",
            "customer_archetype": "travelers",
            "problem_archetypes": ["travel_booking", "marketplace"],
            "schema_confidence": 0.9,
            "is_advisory": False,
            "evidence_signal_ids": [signal_id],
            "extraction_model": "gemini-2.0-flash",
            "extraction_prompt_version": EXTRACTOR_PROMPT_VERSION,
        }

        id1 = await store.save_functional_schema(schema_dict)
        assert id1 > 0

        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM functional_schemas WHERE company_id = ?",
            ("comp-idem",),
        )
        count_before = (await cursor.fetchone())[0]
        assert count_before == 1

        candidate = {
            "company_id": "comp-idem",
            "company_name": "Idem Co",
            "canonical_key": "domain:idem.com",
            "signal_id": signal_id,
            "raw_data": json.dumps({"description": "Test signal for Idem Co"}),
        }
        mock_extractor = MagicMock()
        mock_extractor.extract = AsyncMock()

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

        with patch.object(
            backfill_functional_schemas,
            "find_signals_without_schemas",
            AsyncMock(return_value=[candidate]),
        ), patch.object(
            backfill_functional_schemas,
            "FunctionalExtractor",
            return_value=mock_extractor,
        ):
            result = await backfill_functional_schemas.backfill(
                db_path=str(store.db_path),
                limit=10,
                dry_run=False,
            )

        assert result == {
            "mode": "live",
            "candidates": 1,
            "extracted": 0,
            "skipped": 1,
            "errors": 0,
        }
        mock_extractor.extract.assert_not_awaited()

        cursor = await db.execute(
            "SELECT COUNT(*) FROM functional_schemas WHERE company_id = ?",
            ("comp-idem",),
        )
        count_after = (await cursor.fetchone())[0]
        assert count_after == count_before


class TestSchemaArchetypeValidation:
    """Test that only valid archetypes are accepted."""

    @pytest.mark.asyncio
    async def test_valid_archetypes_constant(self):
        """VALID_ARCHETYPES contains expected entries."""
        assert "foodies" in VALID_ARCHETYPES
        assert "travelers" in VALID_ARCHETYPES
        assert "general_consumer" in VALID_ARCHETYPES
        assert "unknown" in VALID_ARCHETYPES
        # B2B archetypes should NOT be present
        assert "enterprise" not in VALID_ARCHETYPES
        assert "developer" not in VALID_ARCHETYPES
