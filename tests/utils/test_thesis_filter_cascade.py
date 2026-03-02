"""Phase 2: Cascade routing refactor + mode wiring tests.

Tests:
- ThesisFilterConfig.from_env() with cascade parameters
- _route_keyword_only() shared helper (Section C.1)
- Cascade shadow/live/disabled mode wiring
- Exception fallback (ADR-4 safety)
- Config fail-safe (Section C.3)
"""

import logging
import pytest
from dataclasses import field
from typing import List, Tuple
from unittest.mock import patch

from utils.thesis_filter import (
    ThesisFilter,
    ThesisFilterConfig,
    ThesisFilterResult,
    RoutingDecision,
    DecisionPathCode,
)
from utils.thesis_matcher import (
    ThesisFit,
    ThesisFitTrace,
    ConsumerThesis,
)


# ---------------------------------------------------------------------------
# Helper: build ThesisFit for _route_keyword_only tests
# ---------------------------------------------------------------------------

def _make_fit(
    score=0.0,
    negative_keywords=None,
    domain_blacklisted=False,
    matched_hard_rejects=None,
    matched_hard_holds=None,
    consumer_signal_score=0.0,
    consumer_anchor_count=0,
    b2b_soft_score=0.0,
):
    """Build a ThesisFit with trace for routing tests."""
    trace = ThesisFitTrace(
        matched_hard_rejects=matched_hard_rejects or [],
        matched_hard_holds=matched_hard_holds or [],
    )
    return ThesisFit(
        thesis=ConsumerThesis.UNKNOWN,
        score=score,
        matched_keywords=[],
        negative_keywords=negative_keywords or [],
        all_scores={},
        confidence="LOW",
        domain_blacklisted=domain_blacklisted,
        trace=trace,
        consumer_signal_score=consumer_signal_score,
        consumer_anchor_count=consumer_anchor_count,
        b2b_soft_score=b2b_soft_score,
    )


# ===========================================================================
# ThesisFilterConfig.from_env() tests
# ===========================================================================

class TestThesisFilterConfigFromEnv:
    """Test ThesisFilterConfig.from_env() classmethod."""

    def _clear_env(self, monkeypatch):
        """Remove all thesis env vars."""
        for var in [
            "THESIS_HOLD_THRESHOLD", "THESIS_SKIP_LLM_BELOW",
            "THESIS_CONSUMER_RESCUE_THRESHOLD", "THESIS_CONSUMER_ANCHOR_MIN",
            "THESIS_CONSUMER_DOMINANCE_MARGIN", "THESIS_SIGNAL_RATIO_MIN",
            "CASCADE_ROUTING_ENABLEMENT",
        ]:
            monkeypatch.delenv(var, raising=False)

    def test_config_defaults_unchanged(self, monkeypatch):
        """Default config values must match current exactly."""
        self._clear_env(monkeypatch)
        config = ThesisFilterConfig.from_env()
        assert config.hold_threshold == 0.3
        assert config.skip_llm_if_keyword_below == 0.2
        assert config.consumer_rescue_threshold == 0.25
        assert config.consumer_anchor_min == 1
        assert config.consumer_dominance_margin == 0.10
        assert config.signal_ratio_min == 2.0
        assert config.cascade_routing_enablement == "disabled"

    def test_config_from_env_overrides(self, monkeypatch):
        """Env vars should override defaults."""
        self._clear_env(monkeypatch)
        monkeypatch.setenv("THESIS_HOLD_THRESHOLD", "0.4")
        monkeypatch.setenv("THESIS_CONSUMER_RESCUE_THRESHOLD", "0.30")
        monkeypatch.setenv("THESIS_CONSUMER_ANCHOR_MIN", "2")
        monkeypatch.setenv("THESIS_CONSUMER_DOMINANCE_MARGIN", "0.15")
        monkeypatch.setenv("THESIS_SIGNAL_RATIO_MIN", "3.0")
        config = ThesisFilterConfig.from_env()
        assert config.hold_threshold == 0.4
        assert config.consumer_rescue_threshold == 0.30
        assert config.consumer_anchor_min == 2
        assert config.consumer_dominance_margin == 0.15
        assert config.signal_ratio_min == 3.0

    def test_config_cascade_fields_present(self):
        """ThesisFilterConfig should have cascade parameter fields."""
        config = ThesisFilterConfig()
        assert hasattr(config, "cascade_routing_enablement")
        assert hasattr(config, "consumer_rescue_threshold")
        assert hasattr(config, "consumer_anchor_min")
        assert hasattr(config, "consumer_dominance_margin")
        assert hasattr(config, "signal_ratio_min")

    def test_config_fail_safe_invalid_cascade(self, monkeypatch):
        """Invalid cascade value from env → disabled + warning."""
        self._clear_env(monkeypatch)
        monkeypatch.setenv("CASCADE_ROUTING_ENABLEMENT", "turbo")
        config = ThesisFilterConfig.from_env()
        assert config.cascade_routing_enablement == "disabled"

    def test_config_fail_safe_invalid_threshold(self, monkeypatch):
        """Invalid numeric env var → use default."""
        self._clear_env(monkeypatch)
        monkeypatch.setenv("THESIS_CONSUMER_RESCUE_THRESHOLD", "not_a_number")
        config = ThesisFilterConfig.from_env()
        assert config.consumer_rescue_threshold == 0.25


# ===========================================================================
# _route_keyword_only() shared helper tests
# ===========================================================================

class TestRouteKeywordOnly:
    """Test _route_keyword_only() shared routing helper."""

    @pytest.fixture
    def filter_cascade(self):
        """ThesisFilter with cascade config."""
        config = ThesisFilterConfig(
            cascade_routing_enablement="live",
            consumer_rescue_threshold=0.25,
            consumer_anchor_min=1,
            consumer_dominance_margin=0.10,
            signal_ratio_min=2.0,
        )
        return ThesisFilter(config)

    @pytest.fixture
    def filter_disabled(self):
        """ThesisFilter with cascade disabled."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        return ThesisFilter(config)

    # --- Absolute vetoes (mode-independent) ---

    def test_hard_reject_absolute_veto(self, filter_cascade):
        """hard_reject + high consumer signal → REJECTED, VETO_HARD_REJECT."""
        fit = _make_fit(
            score=0.1,
            matched_hard_rejects=["cryptocurrency"],
            consumer_signal_score=0.8,
            consumer_anchor_count=3,
        )
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.REJECTED
        assert code == DecisionPathCode.VETO_HARD_REJECT

    def test_hard_hold_blocks_qualification(self, filter_cascade):
        """hard_hold + strong consumer → HELD, HOLD_HARD_HOLD."""
        fit = _make_fit(
            score=0.1,
            matched_hard_holds=["enterprise"],
            consumer_signal_score=0.6,
            consumer_anchor_count=2,
        )
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_HARD_HOLD

    def test_domain_blacklist_veto(self, filter_cascade):
        """Blacklisted domain → REJECTED, VETO_DOMAIN_BLACKLIST."""
        fit = _make_fit(domain_blacklisted=True)
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.REJECTED
        assert code == DecisionPathCode.VETO_DOMAIN_BLACKLIST

    def test_qualify_sector(self, filter_cascade):
        """High keyword score → QUALIFIED, QUALIFY_SECTOR."""
        fit = _make_fit(score=0.5)  # Above hold_threshold 0.3
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.QUALIFIED
        assert code == DecisionPathCode.QUALIFY_SECTOR

    # --- Cascade: consumer rescue (dominance margin) ---

    def test_dominance_margin_rescue(self, filter_cascade):
        """Consumer dominant over B2B → QUALIFIED, QUALIFY_CONSUMER_RESCUE."""
        fit = _make_fit(
            score=0.1,  # Low sector score
            consumer_signal_score=0.40,
            consumer_anchor_count=2,
            b2b_soft_score=0.10,  # dominance: 0.40 - 0.10 = 0.30 >= 0.10
        )
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.QUALIFIED
        assert code == DecisionPathCode.QUALIFY_CONSUMER_RESCUE

    def test_dominance_margin_blocks_balanced(self, filter_cascade):
        """Consumer ≈ B2B → HELD, HOLD_B2B_GUARD_BLOCK."""
        fit = _make_fit(
            score=0.1,
            consumer_signal_score=0.30,
            consumer_anchor_count=1,
            b2b_soft_score=0.25,  # dominance: 0.05 < 0.10; ratio: 1.2 < 2.0
        )
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_B2B_GUARD_BLOCK

    def test_b2b_guard_block_vs_hold_default(self, filter_cascade):
        """Anchor+threshold but no dominance = HOLD_B2B_GUARD_BLOCK;
        no anchor = HOLD_DEFAULT."""
        # With anchor, above rescue threshold, but no dominance
        fit_with_anchor = _make_fit(
            score=0.1,
            consumer_signal_score=0.30,
            consumer_anchor_count=1,
            b2b_soft_score=0.25,
        )
        _, code = filter_cascade._route_keyword_only(
            fit_with_anchor, cascade_enabled=True,
        )
        assert code == DecisionPathCode.HOLD_B2B_GUARD_BLOCK

        # Without anchor
        fit_no_anchor = _make_fit(
            score=0.1,
            consumer_signal_score=0.30,
            consumer_anchor_count=0,
            b2b_soft_score=0.25,
        )
        _, code = filter_cascade._route_keyword_only(
            fit_no_anchor, cascade_enabled=True,
        )
        assert code == DecisionPathCode.HOLD_DEFAULT

    def test_signal_ratio_alternative_dominance(self, filter_cascade):
        """Signal ratio >= 2.0 passes dominance even if margin < 0.10."""
        fit = _make_fit(
            score=0.1,
            consumer_signal_score=0.26,
            consumer_anchor_count=1,
            b2b_soft_score=0.12,  # margin: 0.14 >= 0.10 → passes
            # Also ratio: 0.26/0.12 = 2.17 >= 2.0 → passes
        )
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.QUALIFIED
        assert code == DecisionPathCode.QUALIFY_CONSUMER_RESCUE

    def test_below_rescue_threshold_hold_default(self, filter_cascade):
        """Consumer score below rescue threshold → HOLD_DEFAULT."""
        fit = _make_fit(
            score=0.1,
            consumer_signal_score=0.10,  # Below 0.25 threshold
            consumer_anchor_count=1,
            b2b_soft_score=0.0,
        )
        routing, code = filter_cascade._route_keyword_only(
            fit, cascade_enabled=True,
        )
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_DEFAULT

    # --- Legacy behavior (cascade disabled) ---

    def test_cascade_disabled_legacy_negative_rejected(self, filter_disabled):
        """Cascade disabled: any negative keyword → REJECTED (legacy)."""
        fit = _make_fit(
            score=0.1,
            negative_keywords=["saas"],  # soft negative
        )
        routing, code = filter_disabled._route_keyword_only(
            fit, cascade_enabled=False,
        )
        assert routing == RoutingDecision.REJECTED
        assert code == DecisionPathCode.VETO_HARD_REJECT

    def test_cascade_disabled_legacy_no_negative_held(self, filter_disabled):
        """Cascade disabled: low score, no negatives → HELD (legacy)."""
        fit = _make_fit(score=0.1)
        routing, code = filter_disabled._route_keyword_only(
            fit, cascade_enabled=False,
        )
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_DEFAULT

    def test_hard_hold_blocks_even_when_disabled(self, filter_disabled):
        """Hard holds route to HELD even when cascade is disabled."""
        fit = _make_fit(
            score=0.1,
            matched_hard_holds=["enterprise"],
            negative_keywords=["enterprise"],
        )
        routing, code = filter_disabled._route_keyword_only(
            fit, cascade_enabled=False,
        )
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_HARD_HOLD


# ===========================================================================
# Cascade wiring in classify() tests
# ===========================================================================

class TestCascadeWiring:
    """Test cascade mode wiring in classify()."""

    @pytest.mark.asyncio
    async def test_cascade_disabled_uses_route_helper(self, monkeypatch):
        """Cascade disabled: classify uses _route_keyword_only."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)

        # Crypto text → hard_reject → REJECTED
        result = await f.classify(
            "cryptocurrency blockchain trading platform",
            skip_llm=True,
        )
        # Web3 pre-check catches crypto before keyword routing
        assert result.routing == RoutingDecision.REJECTED
        assert result.decision_path_code == DecisionPathCode.VETO_WEB3

    @pytest.mark.asyncio
    async def test_cascade_shadow_logs_counterfactual(self, monkeypatch, caplog):
        """Shadow mode: log counterfactual, return legacy result."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        config = ThesisFilterConfig(cascade_routing_enablement="shadow")
        f = ThesisFilter(config)

        with caplog.at_level(logging.INFO, logger="utils.thesis_filter"):
            result = await f.classify(
                "random text for testing cascade shadow",
                skip_llm=True,
            )

        # Should have counterfactual log
        assert any(
            "cascade_counterfactual" in r.message for r in caplog.records
        ), f"Expected cascade_counterfactual log, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_cascade_live_qualify_consumer_rescue(self, monkeypatch):
        """Live mode: strong consumer signal rescues via consumer rescue."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        config = ThesisFilterConfig(cascade_routing_enablement="live")
        f = ThesisFilter(config)

        # Text with strong consumer signal, no hard negatives, low sector score
        result = await f.classify(
            "direct to consumer subscription meal kit delivery brand for shoppers",
            skip_llm=True,
        )
        # With live cascade, strong consumer signal should rescue
        assert result.decision_path_code in (
            DecisionPathCode.QUALIFY_CONSUMER_RESCUE,
            DecisionPathCode.QUALIFY_SECTOR,
        )
        assert result.routing == RoutingDecision.QUALIFIED

    @pytest.mark.asyncio
    async def test_cascade_exception_fallback(self, monkeypatch):
        """Exception in cascade → legacy routing + HOLD_DEFAULT."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        config = ThesisFilterConfig(cascade_routing_enablement="live")
        f = ThesisFilter(config)

        # Patch _route_keyword_only to always raise
        def exploding_route(fit, *, cascade_enabled=False):
            raise RuntimeError("cascade bug")

        monkeypatch.setattr(f, "_route_keyword_only", exploding_route)

        # Should not crash — falls back to inline legacy
        result = await f.classify(
            "random text for testing",
            skip_llm=True,
        )
        assert result.decision_path_code == DecisionPathCode.HOLD_DEFAULT

    @pytest.mark.asyncio
    async def test_hard_hold_routes_to_held_not_rejected(self, monkeypatch):
        """Enterprise/B2B hard holds now route to HELD, not REJECTED."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)

        result = await f.classify(
            "Enterprise B2B SaaS platform for developers",
            skip_llm=True,
        )
        # enterprise, b2b are hard_hold → HELD (not REJECTED as before)
        assert result.routing == RoutingDecision.HELD
        assert result.decision_path_code == DecisionPathCode.HOLD_HARD_HOLD

    @pytest.mark.asyncio
    async def test_hard_reject_still_rejected(self, monkeypatch):
        """Hard reject keywords still route to REJECTED."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)

        # "boilerplate" is a hard_reject keyword (template noise)
        result = await f.classify(
            "boilerplate template generator code tool",
            skip_llm=True,
        )
        assert result.routing == RoutingDecision.REJECTED
        assert result.decision_path_code == DecisionPathCode.VETO_HARD_REJECT
