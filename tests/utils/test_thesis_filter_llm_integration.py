"""Tests for ThesisFilter.classify() LLM integration path.

Validates full LLM-to-routing-to-path_code chain, malformed LLM payloads,
threshold boundary semantics, and Phase 6 field population.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consumer.thesis_filter.llm_classifier import LLMClassifier
from utils.thesis_filter import (
    ThesisFilter,
    ThesisFilterConfig,
    RoutingDecision,
    Web3ReasonCode,
    DomainBlacklistReasonCode,
)


@pytest.fixture
def config():
    """Default config with known thresholds for assertions."""
    return ThesisFilterConfig(
        hold_threshold=0.3,
        skip_llm_if_keyword_below=0.2,
    )


@pytest.fixture
def filter_instance(config):
    return ThesisFilter(config)


def _mock_llm_result(
    thesis_fit_score=0.7,
    category="consumer_cpg",
    rationale="Consumer fit",
    thesis_match=True,
    classification_status="success",
    **overrides,
):
    """Build a MagicMock LLM result."""
    m = MagicMock()
    m.thesis_fit_score = thesis_fit_score
    m.category = category
    m.rationale = rationale
    m.thesis_match = thesis_match
    m.classification_status = classification_status
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


def _install_mock_llm(filter_instance, return_value=None, side_effect=None):
    """Install a mock LLM classifier on the filter instance."""
    mock_llm = AsyncMock()
    if side_effect is not None:
        mock_llm.classify.side_effect = side_effect
    else:
        mock_llm.classify.return_value = return_value
    filter_instance._llm_classifier = mock_llm
    return mock_llm


# Text that reliably scores >= 0.2 on keywords so LLM is invoked
_CONSUMER_TEXT = "Healthy meal kit delivery startup for consumer wellness"
# Text that scores < 0.2 so LLM is skipped
_LOW_TEXT = "xyz random text nothing here"


# ── Core LLM routing chain ──────────────────────────────────────────────────

class TestLLMRoutingChain:
    """Tests for LLM result → routing decision chain."""

    @pytest.mark.asyncio
    async def test_llm_excluded_rejected(self, filter_instance):
        """LLM category=excluded → REJECTED."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(category="excluded", thesis_fit_score=0.1),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_llm_low_score_held(self, filter_instance):
        """LLM score < hold_threshold (0.3) → HELD."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score=0.2, category="other"),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.HELD

    @pytest.mark.asyncio
    async def test_llm_high_score_qualified(self, filter_instance):
        """LLM score >= hold_threshold → QUALIFIED."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score=0.5, category="consumer_cpg"),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_llm_ambiguous_distribution_score_held(self, filter_instance):
        """Score in 0.20-0.29 (ambiguous distribution) → HELD."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(
                thesis_fit_score=0.22,
                category="consumer_health_tech",
                thesis_match=False,
            ),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.HELD

    @pytest.mark.asyncio
    async def test_llm_below_ambiguous_range_held(self, filter_instance):
        """Score below 0.20 still routes to HELD (below hold_threshold 0.3)."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(
                thesis_fit_score=0.15,
                category="consumer_health_tech",
            ),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.HELD

    @pytest.mark.asyncio
    async def test_llm_ad_supported_business_payer_stays_qualified(self, filter_instance):
        """Ad-supported consumer product with paying_customer=business is not demoted."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(
                thesis_fit_score=0.75,
                category="consumer_health_tech",
                paying_customer="business",
            ),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_llm_score_none_fallback(self, filter_instance):
        """LLM score=None → llm_skipped=True, routing from keywords."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score=None),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.llm_skipped is True

    @pytest.mark.asyncio
    async def test_llm_exception_fallback(self, filter_instance):
        """LLM raises RuntimeError → llm_skipped=True, routing from keywords."""
        _install_mock_llm(
            filter_instance,
            side_effect=RuntimeError("LLM service down"),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.llm_skipped is True
        assert result.llm_classification_status == "error_api"

    @pytest.mark.asyncio
    async def test_thesis_filter_falls_back_when_real_classifier_parse_returns_error_parse(
        self,
        filter_instance,
        monkeypatch,
    ):
        """Real classifier parse failures should still trigger keyword-only fallback."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")
        mock_response = MagicMock()
        mock_response.text = "not-json"
        mock_response.usage_metadata = None

        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            filter_instance._llm_classifier = classifier
            result = await filter_instance.classify(_CONSUMER_TEXT)

        assert result.llm_classification_status == "error_parse"
        assert result.llm_skipped is True
        assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_thesis_filter_uses_real_classifier_parsed_structured_fields(
        self,
        filter_instance,
        monkeypatch,
    ):
        """Real classifier success payloads should propagate parsed structured fields into ThesisFilter."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test_key")
        classifier = LLMClassifier(api_key="test_key")
        mock_response = MagicMock()
        mock_response.text = """
        {
          "thesis_match": true,
          "thesis_fit_score": 0.76,
          "category": "consumer_marketplace",
          "stage_estimate": "seed",
          "confidence": "high",
          "company_name": "LessonLane",
          "rationale": "Consumer marketplace for parents booking local activities.",
          "key_signals": ["parents", "marketplace"],
          "primary_end_user": "individual_consumer",
          "paying_customer": "individual_consumer",
          "sells_to_or_operates_in": "operates_in_industry_for_consumers"
        }
        """
        mock_response.usage_metadata = None

        with patch.object(classifier, "_call_gemini_api", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = mock_response
            filter_instance._llm_classifier = classifier
            result = await filter_instance.classify(_CONSUMER_TEXT)

        assert result.llm_skipped is False
        assert result.llm_classification_status == "success"
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.llm_primary_end_user == "individual_consumer"
        assert result.llm_paying_customer == "individual_consumer"
        assert result.llm_sells_to_or_operates_in == "operates_in_industry_for_consumers"

    @pytest.mark.asyncio
    async def test_operational_failure_fallback(self, filter_instance):
        """LLM returns operational failure payload → llm_skipped=True."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(
                thesis_fit_score=0.0,
                category="excluded",
                rationale="gemini unavailable",
                classification_status="error_api",
            ),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        # Operational failure detected → fallback to keywords → llm_skipped
        assert result.llm_skipped is True
        assert result.llm_classification_status == "error_api"


# ── Malformed LLM payloads ──────────────────────────────────────────────────

class TestMalformedLLMPayloads:
    """Tests for graceful handling of broken LLM responses."""

    @pytest.mark.asyncio
    async def test_missing_category_attr(self, filter_instance):
        """LLM result missing category attr → no crash, QUALIFIED (score >= threshold)."""
        m = _mock_llm_result(thesis_fit_score=0.7, category="consumer_cpg")
        del m.category  # Remove the attribute
        _install_mock_llm(filter_instance, return_value=m)
        result = await filter_instance.classify(_CONSUMER_TEXT)
        # score 0.7 >= hold_threshold 0.3 → QUALIFIED; category None won't match "excluded"
        assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_missing_score_attr(self, filter_instance):
        """LLM result missing thesis_fit_score attr → falls back to keywords."""
        m = _mock_llm_result(thesis_fit_score=0.5, category="consumer_cpg")
        del m.thesis_fit_score  # Remove the attribute
        _install_mock_llm(filter_instance, return_value=m)
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.llm_skipped is True

    @pytest.mark.asyncio
    async def test_string_score(self, filter_instance):
        """String score "0.7" → coerced to float, routes as 0.7."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score="0.7"),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        # float("0.7") = 0.7 >= 0.3 → QUALIFIED
        assert result.routing == RoutingDecision.QUALIFIED
        assert result.llm_skipped is False

    @pytest.mark.asyncio
    async def test_unknown_category(self, filter_instance):
        """Unknown category with good score → QUALIFIED (only 'excluded' triggers reject)."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(
                thesis_fit_score=0.5, category="alien_tech"
            ),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.QUALIFIED


# ── Threshold boundary semantics ────────────────────────────────────────────

class TestThresholdBoundaries:
    """Explicit boundary value tests for threshold semantics."""

    @pytest.mark.asyncio
    async def test_keyword_at_skip_llm_threshold_runs_llm(self, filter_instance):
        """Keyword score exactly at skip_llm_if_keyword_below (0.2) → LLM called.

        Semantics: `score < 0.2` skips LLM; `score == 0.2` runs LLM.
        """
        mock_llm = _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score=0.5),
        )
        # Use consumer text that will score >= 0.2 to trigger LLM
        result = await filter_instance.classify(_CONSUMER_TEXT)
        mock_llm.classify.assert_called_once()
        assert result.llm_skipped is False

    @pytest.mark.asyncio
    async def test_llm_score_at_hold_threshold_qualified(self, filter_instance):
        """LLM score exactly at hold_threshold (0.3) → QUALIFIED.

        Semantics: `score < threshold` is HELD; `score >= threshold` is QUALIFIED.
        """
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score=0.3, category="consumer_cpg"),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)
        assert result.routing == RoutingDecision.QUALIFIED


# ── Phase 6 fields in LLM path ──────────────────────────────────────────────

class TestPhase6FieldsInLLMPath:
    """Phase 6 observability fields must be populated in the LLM success path."""

    @pytest.mark.asyncio
    async def test_phase6_fields_in_llm_path(self, filter_instance):
        """LLM success path populates web3_reason_code, cascade_config_snapshot, domain_blacklist_reason_code."""
        _install_mock_llm(
            filter_instance,
            return_value=_mock_llm_result(thesis_fit_score=0.5),
        )
        result = await filter_instance.classify(_CONSUMER_TEXT)

        assert result.web3_reason_code is not None
        assert isinstance(result.web3_reason_code, Web3ReasonCode)

        assert result.cascade_config_snapshot is not None
        assert isinstance(result.cascade_config_snapshot, dict)

        assert result.domain_blacklist_reason_code is not None
        assert isinstance(result.domain_blacklist_reason_code, DomainBlacklistReasonCode)
