"""Tests for thesis classification storage."""
import pytest
from datetime import datetime
from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


class TestThesisClassificationSchema:
    """Test thesis_classifications table exists after migration."""

    @pytest.fixture
    async def store(self, tmp_path):
        """Create a fresh store for each test."""
        db_path = str(tmp_path / "test_thesis.db")
        store = SignalStore(db_path)
        await store.initialize()
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_schema_version_is_5(self, store):
        """Schema version should be 5 after migration."""
        assert CURRENT_SCHEMA_VERSION == 5

    @pytest.mark.asyncio
    async def test_thesis_classifications_table_exists(self, store):
        """thesis_classifications table should exist."""
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_classifications'"
        )
        result = await cursor.fetchone()
        assert result is not None
        assert result[0] == "thesis_classifications"

    @pytest.mark.asyncio
    async def test_thesis_classifications_columns(self, store):
        """thesis_classifications should have all required columns."""
        cursor = await store._db.execute("PRAGMA table_info(thesis_classifications)")
        columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}

        required = {
            "id", "signal_id", "canonical_key",
            "thesis_match", "thesis_fit_score", "category",
            "keyword_score", "keyword_category", "negative_keywords",
            "stage_estimate", "confidence", "rationale", "key_signals",
            "prompt_version", "model", "input_tokens", "output_tokens",
            "latency_ms", "classified_at", "competitor_flag", "competitor_match"
        }
        assert required.issubset(column_names)
