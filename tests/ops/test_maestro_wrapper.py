"""
Tests for OpsLayerMaestro wrapper.

Tests:
- JSON extraction from markdown-wrapped responses
- Context size decision logging
- Text-only routing triggers Kimi selection
- Decision record creation
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pydantic import BaseModel

from ops.integrations.maestro_wrapper import (
    OpsLayerMaestro,
    ContextSizeDecision,
    StructuredOutputMeta,
    VersionedOutput,
)


# =============================================================================
# TEST FIXTURES
# =============================================================================

class SampleOutput(BaseModel):
    """Sample Pydantic model for testing structured output."""
    confidence: float
    category: str


@pytest.fixture
def mock_maestro():
    """Create a mock Maestro instance."""
    maestro = MagicMock()
    maestro.kimi_mode = MagicMock()
    maestro._estimate_context_size = MagicMock(return_value=(2, 5000))
    maestro._should_use_kimi = MagicMock(return_value=False)
    return maestro


@pytest.fixture
def ops_maestro(mock_maestro):
    """Create OpsLayerMaestro with mock Maestro."""
    return OpsLayerMaestro(maestro=mock_maestro)


# =============================================================================
# JSON EXTRACTION TESTS
# =============================================================================

class TestJsonExtraction:
    """Tests for _extract_json() method."""

    def test_extract_json_from_markdown_block(self, ops_maestro):
        """Verify JSON extraction from markdown-wrapped response."""
        dirty_response = '''Here is the analysis:
```json
{"confidence": 0.85, "category": "CPG"}
```
Let me know if you need more details.'''

        cleaned = ops_maestro._extract_json(dirty_response)
        assert cleaned == '{"confidence": 0.85, "category": "CPG"}'

    def test_extract_json_from_markdown_without_json_tag(self, ops_maestro):
        """Verify JSON extraction from ``` block without json tag."""
        dirty_response = '''```
{"confidence": 0.9, "category": "Health Tech"}
```'''

        cleaned = ops_maestro._extract_json(dirty_response)
        assert cleaned == '{"confidence": 0.9, "category": "Health Tech"}'

    def test_extract_json_raw(self, ops_maestro):
        """Verify raw JSON passthrough."""
        raw_json = '{"confidence": 0.75, "category": "Marketplace"}'
        cleaned = ops_maestro._extract_json(raw_json)
        assert cleaned == raw_json

    def test_extract_json_with_surrounding_text(self, ops_maestro):
        """Verify JSON extraction when surrounded by text."""
        response = '''Based on my analysis, here is the result:
{"confidence": 0.65, "category": "Travel"}
I hope this helps!'''

        cleaned = ops_maestro._extract_json(response)
        # Should find the JSON object
        assert '"confidence": 0.65' in cleaned
        assert '"category": "Travel"' in cleaned

    def test_extract_json_preserves_whitespace_in_json(self, ops_maestro):
        """Verify internal JSON whitespace is preserved."""
        raw_json = '{"confidence": 0.85,\n  "category": "CPG"}'
        cleaned = ops_maestro._extract_json(raw_json)
        assert cleaned == raw_json


# =============================================================================
# DECISION RECORD TESTS
# =============================================================================

class TestDecisionRecord:
    """Tests for decision record creation."""

    def test_create_decision_record_below_threshold(self, ops_maestro, mock_maestro):
        """Verify decision record for below-threshold context."""
        mock_maestro._estimate_context_size.return_value = (2, 5000)

        decision = ops_maestro._create_decision_record(
            task="Test task",
            context_files=["file1.py", "file2.py"],
            context_text=None,
        )

        assert decision.file_count == 2
        assert decision.estimated_tokens == 5000
        assert decision.chosen_backend == "Codex"
        assert "below thresholds" in decision.reason
        assert decision.task_summary == "Test task"

    def test_create_decision_record_above_token_threshold(self, ops_maestro, mock_maestro):
        """Verify decision record for above-token-threshold context."""
        mock_maestro._estimate_context_size.return_value = (3, 25000)

        decision = ops_maestro._create_decision_record(
            task="Large context task",
            context_files=["file1.py", "file2.py", "file3.py"],
            context_text="x" * 80000,  # ~20K tokens
        )

        assert decision.chosen_backend == "Kimi"
        assert "tokens >= 20K" in decision.reason

    def test_create_decision_record_above_file_threshold(self, ops_maestro, mock_maestro):
        """Verify decision record for above-file-threshold context."""
        mock_maestro._estimate_context_size.return_value = (6, 10000)

        decision = ops_maestro._create_decision_record(
            task="Many files task",
            context_files=["f1.py", "f2.py", "f3.py", "f4.py", "f5.py", "f6.py"],
            context_text=None,
        )

        assert decision.chosen_backend == "Kimi"
        assert "files >= 5" in decision.reason

    def test_create_decision_record_forced_kimi(self, mock_maestro):
        """Verify decision record when force_kimi=True."""
        ops_maestro = OpsLayerMaestro(maestro=mock_maestro, force_kimi=True)
        mock_maestro._estimate_context_size.return_value = (1, 1000)

        decision = ops_maestro._create_decision_record(
            task="Forced Kimi task",
            context_files=["small.py"],
            context_text=None,
        )

        assert decision.chosen_backend == "Kimi"
        assert "mode_override=ALWAYS" in decision.reason

    def test_decision_record_truncates_long_task(self, ops_maestro, mock_maestro):
        """Verify task summary is truncated to 100 chars."""
        mock_maestro._estimate_context_size.return_value = (1, 1000)
        long_task = "x" * 200

        decision = ops_maestro._create_decision_record(
            task=long_task,
            context_files=None,
            context_text=None,
        )

        assert len(decision.task_summary) == 100


# =============================================================================
# TEXT-ONLY ROUTING TESTS
# =============================================================================

class TestTextOnlyRouting:
    """Tests for text-only context routing."""

    def test_text_only_triggers_kimi_in_maestro(self):
        """Verify context_text alone triggers Kimi in Maestro._estimate_context_size."""
        from integrations.maestro import Maestro, KimiMode

        maestro = Maestro(kimi_mode=KimiMode.AUTO)

        # 25K tokens of text, no files
        large_text = "x" * 100_000  # ~25K tokens at 4 chars/token

        file_count, est_tokens = maestro._estimate_context_size(
            context_files=None,
            context_text=large_text,
        )

        assert file_count == 0
        assert est_tokens >= 20_000

    def test_text_only_should_use_kimi(self):
        """Verify _should_use_kimi returns True for large text-only context."""
        from integrations.maestro import Maestro, KimiMode

        maestro = Maestro(kimi_mode=KimiMode.AUTO)
        large_text = "x" * 100_000  # ~25K tokens

        should_use = maestro._should_use_kimi(
            context_files=None,
            context_text=large_text,
        )

        assert should_use is True

    def test_small_text_uses_codex(self):
        """Verify small text-only context uses Codex."""
        from integrations.maestro import Maestro, KimiMode

        maestro = Maestro(kimi_mode=KimiMode.AUTO)
        small_text = "x" * 1000  # ~250 tokens

        should_use = maestro._should_use_kimi(
            context_files=None,
            context_text=small_text,
        )

        assert should_use is False


# =============================================================================
# VERSIONED OUTPUT TESTS
# =============================================================================

class TestVersionedOutput:
    """Tests for VersionedOutput wrapper."""

    def test_versioned_output_structure(self):
        """Verify VersionedOutput structure."""
        data = SampleOutput(confidence=0.9, category="CPG")
        meta = StructuredOutputMeta(
            schema_version="1.0.0",
            producer_model="Kimi",
        )
        output = VersionedOutput(data=data, meta=meta)

        assert output.data.confidence == 0.9
        assert output.data.category == "CPG"
        assert output.meta.schema_version == "1.0.0"
        assert output.meta.producer_model == "Kimi"
        assert output.meta.raw_response is None

    def test_versioned_output_with_raw_response(self):
        """Verify VersionedOutput includes raw response when requested."""
        data = SampleOutput(confidence=0.85, category="Health Tech")
        meta = StructuredOutputMeta(
            schema_version="1.0.0",
            producer_model="Codex",
            raw_response='{"confidence": 0.85, "category": "Health Tech"}',
        )
        output = VersionedOutput(data=data, meta=meta)

        assert output.meta.raw_response is not None
        assert "0.85" in output.meta.raw_response


# =============================================================================
# INTEGRATION-STYLE TESTS (mocked)
# =============================================================================

class TestAnalyzeWithSchema:
    """Tests for analyze_with_schema method (mocked)."""

    @pytest.mark.asyncio
    async def test_analyze_with_schema_success(self, ops_maestro, mock_maestro):
        """Verify successful schema-validated analysis."""
        # Mock the forensic_collaborate response
        mock_result = MagicMock()
        mock_iteration = MagicMock()
        mock_iteration.codex_response = '{"confidence": 0.9, "category": "CPG"}'
        mock_result.iterations = [mock_iteration]

        mock_maestro.forensic_collaborate = AsyncMock(return_value=mock_result)

        with patch.object(ops_maestro, '_log_decision'):
            result = await ops_maestro.analyze_with_schema(
                task="Test analysis",
                context="Test context",
                output_schema=SampleOutput,
            )

        assert isinstance(result, VersionedOutput)
        assert result.data.confidence == 0.9
        assert result.data.category == "CPG"
        assert result.meta.schema_version == "1.0.0"

    @pytest.mark.asyncio
    async def test_analyze_with_schema_markdown_response(self, ops_maestro, mock_maestro):
        """Verify handling of markdown-wrapped response."""
        mock_result = MagicMock()
        mock_iteration = MagicMock()
        mock_iteration.codex_response = '''```json
{"confidence": 0.85, "category": "Health Tech"}
```'''
        mock_result.iterations = [mock_iteration]

        mock_maestro.forensic_collaborate = AsyncMock(return_value=mock_result)

        with patch.object(ops_maestro, '_log_decision'):
            result = await ops_maestro.analyze_with_schema(
                task="Markdown test",
                context="Test context",
                output_schema=SampleOutput,
            )

        assert result.data.confidence == 0.85
        assert result.data.category == "Health Tech"

    @pytest.mark.asyncio
    async def test_analyze_with_schema_retry_on_failure(self, ops_maestro, mock_maestro):
        """Verify retry logic on validation failure."""
        # First response is invalid, second is valid
        mock_result_bad = MagicMock()
        mock_iteration_bad = MagicMock()
        mock_iteration_bad.codex_response = 'not valid json'
        mock_result_bad.iterations = [mock_iteration_bad]

        mock_result_good = MagicMock()
        mock_iteration_good = MagicMock()
        mock_iteration_good.codex_response = '{"confidence": 0.8, "category": "Travel"}'
        mock_result_good.iterations = [mock_iteration_good]

        mock_maestro.forensic_collaborate = AsyncMock(
            side_effect=[mock_result_bad, mock_result_good]
        )

        with patch.object(ops_maestro, '_log_decision'):
            result = await ops_maestro.analyze_with_schema(
                task="Retry test",
                context="Test context",
                output_schema=SampleOutput,
            )

        assert result.data.confidence == 0.8
        assert result.data.category == "Travel"
        # Should have been called twice (initial + retry)
        assert mock_maestro.forensic_collaborate.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_with_schema_failure_after_retries(self, ops_maestro, mock_maestro):
        """Verify ValueError raised after all retries fail."""
        mock_result = MagicMock()
        mock_iteration = MagicMock()
        mock_iteration.codex_response = 'invalid json forever'
        mock_result.iterations = [mock_iteration]

        mock_maestro.forensic_collaborate = AsyncMock(return_value=mock_result)

        with patch.object(ops_maestro, '_log_decision'):
            with pytest.raises(ValueError) as exc_info:
                await ops_maestro.analyze_with_schema(
                    task="Failure test",
                    context="Test context",
                    output_schema=SampleOutput,
                )

        assert "Failed to parse structured output" in str(exc_info.value)
