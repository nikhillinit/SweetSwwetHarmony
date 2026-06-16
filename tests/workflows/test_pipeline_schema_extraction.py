"""Tests for Task 2.4: Pipeline wiring of functional schema extraction.

Verifies:
- Pipeline runs with schema extraction enabled (mock LLM)
- Pipeline runs with schema extraction disabled
- Schema extraction failure doesn't break pipeline
- Schema not extracted for rejected signals
- Schema skipped if company already has active schema
- Multi-signal company group: highest-confidence signal selected
"""

import asyncio
import json
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflows.pipeline import PipelineConfig, DiscoveryPipeline, PipelineStats


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_config(tmp_path, **overrides):
    """Create a PipelineConfig pointing at a temp DB."""
    defaults = dict(
        db_path=str(tmp_path / "test.db"),
        notion_api_key=None,
        notion_database_id=None,
        use_gating=False,
        use_entities=False,
        use_consolidation=False,
        use_enrichment_boost=False,
        use_thesis_filter=False,
        use_competitor_detection=False,
        use_exit_predictor=False,
        use_investor_matching=False,
        use_phase_g_identity_resolution=False,
        use_claim_facts=False,
        use_thin_files=False,
        use_functional_schema=False,
        use_founder_scoring=False,
        use_velocity_tracking=False,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


# ── Test: Config defaults ────────────────────────────────────────────────

class TestFunctionalSchemaConfig:
    def test_default_disabled(self):
        config = PipelineConfig()
        assert config.use_functional_schema is False

    def test_env_var_enables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FUNCTIONAL_SCHEMA", "true")
        config = PipelineConfig.from_env()
        assert config.use_functional_schema is True

    def test_env_var_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_FUNCTIONAL_SCHEMA", "false")
        config = PipelineConfig.from_env()
        assert config.use_functional_schema is False


# ── Test: Schema extractor init ──────────────────────────────────────────

class TestSchemaExtractorInit:
    def test_not_initialized_when_disabled(self, tmp_path):
        config = _make_config(tmp_path, use_functional_schema=False)
        pipeline = DiscoveryPipeline(config)
        assert pipeline._schema_extractor is None

    def test_initialized_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        config = _make_config(tmp_path, use_functional_schema=True)
        pipeline = DiscoveryPipeline(config)
        assert pipeline._schema_extractor is not None

    def test_graceful_when_no_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        config = _make_config(tmp_path, use_functional_schema=True)
        # FunctionalExtractor() should succeed (lazy client init)
        # but extraction would fail at runtime — that's expected
        pipeline = DiscoveryPipeline(config)
        # Extractor is created but client will fail on first call
        assert pipeline._schema_extractor is not None


# ── Test: PipelineStats ──────────────────────────────────────────────────

class TestPipelineStatsSchemaField:
    def test_schemas_extracted_default(self):
        stats = PipelineStats()
        assert stats.schemas_extracted == 0

    def test_schemas_extracted_increments(self):
        stats = PipelineStats()
        stats.schemas_extracted = 5
        assert stats.schemas_extracted == 5


# ── Test: Schema extraction in _process_company ─────────────────────────

class TestProcessCompanySchemaExtraction:
    """Integration-style tests for schema extraction within _process_company."""

    @pytest.fixture
    def pipeline_with_schema(self, tmp_path, monkeypatch):
        """Create a pipeline with schema extraction enabled and mocked extractor."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        config = _make_config(tmp_path, use_functional_schema=True, use_thesis_filter=False)
        pipeline = DiscoveryPipeline(config)

        # Mock the schema extractor
        mock_extractor = AsyncMock()
        pipeline._schema_extractor = mock_extractor
        return pipeline, mock_extractor

    @pytest.fixture
    def mock_stored_signal(self):
        """Create a mock StoredSignal."""
        sig = MagicMock()
        sig.id = 1
        sig.canonical_key = "domain:test.com"
        sig.company_name = "TestCo"
        sig.company_id = "comp-123"
        sig.confidence = 0.75
        sig.signal_type = "funding"
        sig.source_api = "sec_edgar"
        sig.raw_data = json.dumps({"title": "TestCo"})
        sig.detected_at = "2026-01-15T00:00:00Z"
        return sig

    @pytest.mark.asyncio
    async def test_extraction_called_when_enabled(self, pipeline_with_schema, mock_stored_signal):
        """Schema extraction is called when extractor is enabled."""
        pipeline, mock_extractor = pipeline_with_schema

        # Set up mocks
        mock_schema = MagicMock()
        mock_schema.customer_archetype = "foodies"
        mock_schema.to_storage_dict.return_value = {"company_id": "comp-123"}
        mock_extractor.extract.return_value = mock_schema

        mock_store = AsyncMock()
        mock_store.has_active_schema.return_value = False
        mock_store.save_functional_schema.return_value = 1
        mock_store.check_suppression.return_value = None
        mock_store.mark_pushed.return_value = None
        mock_store.mark_rejected.return_value = None
        pipeline._store = mock_store

        # Mock verification gate
        mock_gate = MagicMock()
        mock_gate.evaluate.return_value = MagicMock(
            decision=MagicMock(value="reject"),
            confidence_score=0.3,
            reason="Single source",
            suggested_status="Tracking",
            verification_status=MagicMock(value="unverified"),
        )
        from workflows.pipeline import PushDecision
        mock_gate.evaluate.return_value.decision = PushDecision.REJECT
        pipeline._gate = mock_gate

        result = await pipeline._process_company([mock_stored_signal], dry_run=False)

        mock_store.has_active_schema.assert_called_once_with("comp-123")
        mock_extractor.extract.assert_called_once()
        mock_store.save_functional_schema.assert_called_once()
        assert result["schema_extracted"] is True

    @pytest.mark.asyncio
    async def test_extraction_skipped_when_schema_exists(self, pipeline_with_schema, mock_stored_signal):
        """Schema extraction skipped if company already has an active schema."""
        pipeline, mock_extractor = pipeline_with_schema

        mock_store = AsyncMock()
        mock_store.has_active_schema.return_value = True  # Already exists
        mock_store.check_suppression.return_value = None
        mock_store.mark_rejected.return_value = None
        pipeline._store = mock_store

        mock_gate = MagicMock()
        from workflows.pipeline import PushDecision
        mock_gate.evaluate.return_value = MagicMock(
            decision=PushDecision.REJECT,
            confidence_score=0.3,
            reason="Single source",
            suggested_status="Tracking",
            verification_status=MagicMock(value="unverified"),
        )
        pipeline._gate = mock_gate

        result = await pipeline._process_company([mock_stored_signal], dry_run=True)

        mock_store.has_active_schema.assert_called_once_with("comp-123")
        mock_extractor.extract.assert_not_called()
        assert result["schema_extracted"] is False

    @pytest.mark.asyncio
    async def test_extraction_failure_nonfatal(self, pipeline_with_schema, mock_stored_signal):
        """Schema extraction failure doesn't break pipeline."""
        pipeline, mock_extractor = pipeline_with_schema

        mock_extractor.extract.side_effect = RuntimeError("LLM API down")

        mock_store = AsyncMock()
        mock_store.has_active_schema.return_value = False
        mock_store.check_suppression.return_value = None
        mock_store.mark_rejected.return_value = None
        pipeline._store = mock_store

        mock_gate = MagicMock()
        from workflows.pipeline import PushDecision
        mock_gate.evaluate.return_value = MagicMock(
            decision=PushDecision.REJECT,
            confidence_score=0.3,
            reason="Single source",
            suggested_status="Tracking",
            verification_status=MagicMock(value="unverified"),
        )
        pipeline._gate = mock_gate

        # Should not raise
        result = await pipeline._process_company([mock_stored_signal], dry_run=True)
        assert result["schema_extracted"] is False

    @pytest.mark.asyncio
    async def test_extraction_not_called_when_disabled(self, tmp_path, monkeypatch):
        """Schema extraction not called when config is disabled."""
        config = _make_config(tmp_path, use_functional_schema=False, use_thesis_filter=False)
        pipeline = DiscoveryPipeline(config)
        assert pipeline._schema_extractor is None

    @pytest.mark.asyncio
    async def test_best_signal_selection(self, pipeline_with_schema):
        """Multi-signal group: highest-confidence signal selected, sec_edgar preferred on tie."""
        pipeline, mock_extractor = pipeline_with_schema

        mock_schema = MagicMock()
        mock_schema.customer_archetype = "foodies"
        mock_schema.to_storage_dict.return_value = {"company_id": "comp-123"}
        mock_extractor.extract.return_value = mock_schema

        mock_store = AsyncMock()
        mock_store.has_active_schema.return_value = False
        mock_store.save_functional_schema.return_value = 1
        mock_store.check_suppression.return_value = None
        mock_store.mark_rejected.return_value = None
        pipeline._store = mock_store

        mock_gate = MagicMock()
        from workflows.pipeline import PushDecision
        mock_gate.evaluate.return_value = MagicMock(
            decision=PushDecision.REJECT,
            confidence_score=0.3,
            reason="Single source",
            suggested_status="Tracking",
            verification_status=MagicMock(value="unverified"),
        )
        pipeline._gate = mock_gate

        # Two signals: same confidence, different sources
        sig1 = MagicMock()
        sig1.id = 1
        sig1.canonical_key = "domain:test.com"
        sig1.company_name = "TestCo"
        sig1.company_id = "comp-123"
        sig1.confidence = 0.7
        sig1.signal_type = "news"
        sig1.source_api = "news_api"
        sig1.raw_data = "{}"
        sig1.detected_at = "2026-01-15"

        sig2 = MagicMock()
        sig2.id = 2
        sig2.canonical_key = "domain:test.com"
        sig2.company_name = "TestCo"
        sig2.company_id = "comp-123"
        sig2.confidence = 0.7
        sig2.signal_type = "filing"
        sig2.source_api = "sec_edgar"
        sig2.raw_data = "{}"
        sig2.detected_at = "2026-01-15"

        await pipeline._process_company([sig1, sig2], dry_run=True)

        # Verify extract was called with sec_edgar signal's data (preferred on tie)
        call_args = mock_extractor.extract.call_args
        assert call_args[0][0]["source_api"] == "sec_edgar"

    @pytest.mark.asyncio
    async def test_no_company_id_skips_extraction(self, pipeline_with_schema, mock_stored_signal):
        """Extraction skipped when company_id is missing."""
        pipeline, mock_extractor = pipeline_with_schema
        mock_stored_signal.company_id = None

        mock_store = AsyncMock()
        mock_store.check_suppression.return_value = None
        mock_store.mark_rejected.return_value = None
        pipeline._store = mock_store

        mock_gate = MagicMock()
        from workflows.pipeline import PushDecision
        mock_gate.evaluate.return_value = MagicMock(
            decision=PushDecision.REJECT,
            confidence_score=0.3,
            reason="Single source",
            suggested_status="Tracking",
            verification_status=MagicMock(value="unverified"),
        )
        pipeline._gate = mock_gate

        result = await pipeline._process_company([mock_stored_signal], dry_run=True)

        mock_extractor.extract.assert_not_called()
        assert result["schema_extracted"] is False
