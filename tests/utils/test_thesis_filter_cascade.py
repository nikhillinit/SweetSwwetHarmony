"""Phase 2: Cascade routing refactor + mode wiring tests.

Tests:
- ThesisFilterConfig.from_env() with cascade parameters
- _route_keyword_only() shared helper (Section C.1)
- Cascade shadow/live/disabled mode wiring
- Exception fallback (ADR-4 safety)
- Config fail-safe (Section C.3)
"""

import json
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
    Web3ReasonCode,
    DomainBlacklistReasonCode,
    CascadeExceptionCode,
    CascadeResolution,
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


# ===========================================================================
# NaN/Inf safety tests (v12.2 requirement)
# ===========================================================================

class TestNaNInfSafety:
    """Verify cascade routing handles malformed scores without crashing."""

    def test_nan_consumer_signal_no_crash(self):
        """NaN consumer_signal_score → deterministic HOLD_DEFAULT, no exception."""
        config = ThesisFilterConfig(cascade_routing_enablement="live")
        f = ThesisFilter(config)

        fit = _make_fit(
            score=0.1,
            consumer_signal_score=float("nan"),
            consumer_anchor_count=2,
            b2b_soft_score=0.10,
        )
        routing, code = f._route_keyword_only(fit, cascade_enabled=True)
        # NaN comparisons are False → rescue conditions fail → HOLD_DEFAULT
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_DEFAULT

    def test_inf_b2b_soft_score_no_crash(self):
        """Inf b2b_soft_score → deterministic result, no exception."""
        config = ThesisFilterConfig(cascade_routing_enablement="live")
        f = ThesisFilter(config)

        fit = _make_fit(
            score=0.1,
            consumer_signal_score=0.40,
            consumer_anchor_count=2,
            b2b_soft_score=float("inf"),
        )
        routing, code = f._route_keyword_only(fit, cascade_enabled=True)
        # inf B2B → dominance margin negative, ratio near 0 → guard block
        assert routing == RoutingDecision.HELD
        assert code == DecisionPathCode.HOLD_B2B_GUARD_BLOCK


# ===========================================================================
# Phase 6: Observability & trace contract hardening tests
# ===========================================================================

class TestPhase6Observability:
    """Phase 6: Typed reason codes, counterfactual capture, config snapshots."""

    # --- Core contract tests ---

    @pytest.mark.asyncio
    async def test_web3_clean_reason_on_non_crypto(self):
        """#1: classify consumer text → web3_reason_code == CLEAN."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("organic meal kit delivery for families", skip_llm=True)
        assert result.web3_reason_code == Web3ReasonCode.CLEAN

    @pytest.mark.asyncio
    async def test_web3_unambiguous_reason_on_crypto_veto(self):
        """#2: classify unambiguous crypto → web3_reason_code == UNAMBIGUOUS_CRYPTO."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("blockchain nft marketplace", skip_llm=True)
        assert result.web3_reason_code == Web3ReasonCode.UNAMBIGUOUS_CRYPTO
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_web3_ambiguous_with_context_reason(self):
        """#3: classify ambiguous term with crypto context → AMBIGUOUS_WITH_CONTEXT.

        "crypto" ∈ CRYPTO_CONTEXT (line 73 of web3_detector.py),
        ∉ UNAMBIGUOUS_CRYPTO (lines 37-44);
        "token" ∈ AMBIGUOUS_TERMS (line 48).

        Detection path: no unambiguous hit → "token" found as ambiguous →
        no rescue phrase match → "crypto" co-occurs within window → is_crypto=True.
        """
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify(
            "token governance crypto rewards platform", skip_llm=True,
        )
        assert result.web3_reason_code == Web3ReasonCode.AMBIGUOUS_WITH_CONTEXT
        assert result.routing == RoutingDecision.REJECTED

    @pytest.mark.asyncio
    async def test_domain_blacklist_reason_code_via_classify(self):
        """#4: domain_blacklisted=True → DOMAIN_ON_BLACKLIST reason code."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)

        # Mock ThesisMatcher.score to return blacklisted fit
        original_score = f._keyword_matcher.score

        def mock_score(text, company_name=None, domain_name=None):
            fit = original_score(text, company_name, domain_name=domain_name)
            # Force domain_blacklisted
            object.__setattr__(fit, "domain_blacklisted", True)
            return fit

        f._keyword_matcher.score = mock_score

        result = await f.classify("some consumer startup text", skip_llm=True)
        assert result.domain_blacklist_reason_code == DomainBlacklistReasonCode.DOMAIN_ON_BLACKLIST

    @pytest.mark.asyncio
    async def test_counterfactual_path_code_shadow_populated(self):
        """#5: shadow mode → counterfactual_path_code is not None."""
        config = ThesisFilterConfig(cascade_routing_enablement="shadow")
        f = ThesisFilter(config)
        result = await f.classify(
            "direct to consumer subscription meal kit delivery brand",
            skip_llm=True,
        )
        assert result.counterfactual_path_code is not None
        assert isinstance(result.counterfactual_path_code, DecisionPathCode)

    @pytest.mark.asyncio
    async def test_counterfactual_path_code_disabled_none(self):
        """#6: disabled mode → counterfactual_path_code is None."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("some random startup text", skip_llm=True)
        assert result.counterfactual_path_code is None

    @pytest.mark.asyncio
    async def test_counterfactual_none_and_exception_code_on_shadow_failure(self):
        """#7: shadow mode + cascade exception → exception code set, counterfactual None."""
        config = ThesisFilterConfig(cascade_routing_enablement="shadow")
        f = ThesisFilter(config)

        original_route = f._route_keyword_only

        def exploding_route(fit, *, cascade_enabled=False):
            if cascade_enabled:
                raise RuntimeError("cascade bug")
            return original_route(fit, cascade_enabled=False)

        f._route_keyword_only = exploding_route

        result = await f.classify("random text for testing", skip_llm=True)
        assert result.counterfactual_path_code is None
        assert result.cascade_exception_code == CascadeExceptionCode.SHADOW_ROUTE_EXCEPTION

    @pytest.mark.asyncio
    async def test_config_snapshot_contains_required_keys(self):
        """#8: snapshot has all 11 compact keys."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("organic meal kit delivery", skip_llm=True)

        snapshot = result.cascade_config_snapshot
        assert snapshot is not None
        required_keys = {
            "cascade_routing_enablement",
            "cascade_mode_used",
            "hold_threshold",
            "skip_llm_if_keyword_below",
            "consumer_rescue_threshold",
            "consumer_anchor_min",
            "consumer_dominance_margin",
            "signal_ratio_min",
            "consumer_lexicon_sha256",
            "b2b_lexicon_sha256",
            "negative_policy_sha256",
        }
        assert required_keys.issubset(set(snapshot.keys()))

    def test_resolve_cascade_returns_dataclass(self):
        """#9: _resolve_cascade_routing returns CascadeResolution dataclass."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        fit = _make_fit(score=0.1)
        resolution = f._resolve_cascade_routing(fit)
        assert isinstance(resolution, CascadeResolution)
        assert hasattr(resolution, "decision")
        assert hasattr(resolution, "path_code")
        assert hasattr(resolution, "counterfactual_path_code")
        assert hasattr(resolution, "cascade_exception_code")

    # --- Invariant tests (P0) ---

    @pytest.mark.asyncio
    async def test_web3_reason_code_always_set_after_classify(self):
        """#10: web3_reason_code is always non-None after classify."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)

        texts = [
            "blockchain nft marketplace",       # crypto
            "organic meal kit delivery",         # consumer
            "generic text about nothing much",   # neutral
        ]
        for text in texts:
            result = await f.classify(text, skip_llm=True)
            assert result.web3_reason_code is not None, (
                f"web3_reason_code was None for text: {text!r}"
            )

    @pytest.mark.asyncio
    async def test_counterfactual_never_synthetic_error(self):
        """#11: counterfactual_path_code is None or DecisionPathCode, never a bare string."""
        for mode in ("disabled", "shadow", "live"):
            config = ThesisFilterConfig(cascade_routing_enablement=mode)
            f = ThesisFilter(config)
            result = await f.classify("meal kit delivery startup", skip_llm=True)
            cpc = result.counterfactual_path_code
            assert cpc is None or isinstance(cpc, DecisionPathCode), (
                f"mode={mode}: counterfactual_path_code={cpc!r} is not None or DecisionPathCode"
            )

    @pytest.mark.asyncio
    async def test_snapshot_hashes_always_present(self):
        """#12: snapshot contains all 3 sha256 keys and they are non-None strings."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("consumer brand startup", skip_llm=True)

        snapshot = result.cascade_config_snapshot
        assert snapshot is not None
        for key in ("consumer_lexicon_sha256", "b2b_lexicon_sha256", "negative_policy_sha256"):
            assert key in snapshot, f"Missing {key} in snapshot"
            assert isinstance(snapshot[key], str), f"{key} is not a string"
            assert len(snapshot[key]) > 0, f"{key} is empty"

    @pytest.mark.asyncio
    async def test_shadow_snapshot_has_extended_keys(self):
        """#13: shadow mode snapshot has extended keys."""
        config = ThesisFilterConfig(cascade_routing_enablement="shadow")
        f = ThesisFilter(config)
        result = await f.classify("consumer food delivery startup", skip_llm=True)

        snapshot = result.cascade_config_snapshot
        assert snapshot is not None
        extended_keys = {
            "keyword_high_threshold",
            "keyword_low_threshold",
            "high_boost",
            "low_penalty",
            "negative_keyword_penalty",
        }
        assert extended_keys.issubset(set(snapshot.keys())), (
            f"Missing extended keys: {extended_keys - set(snapshot.keys())}"
        )

    # (#14 DROPPED per R2 — replaced by #9 structural assertion)

    @pytest.mark.asyncio
    async def test_domain_blacklist_reason_none_when_not_evaluated_early_web3_veto(self):
        """#15: early web3 veto → domain_blacklist_reason_code is None (not evaluated)."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("blockchain nft marketplace", skip_llm=True)
        assert result.routing == RoutingDecision.REJECTED
        assert result.decision_path_code == DecisionPathCode.VETO_WEB3
        assert result.domain_blacklist_reason_code is None

    @pytest.mark.asyncio
    async def test_to_dict_json_serializable(self):
        """#16: to_dict() is JSON-serializable with all new enum fields populated."""
        config = ThesisFilterConfig(cascade_routing_enablement="shadow")
        f = ThesisFilter(config)
        result = await f.classify(
            "direct to consumer subscription meal kit delivery brand",
            skip_llm=True,
        )
        d = result.to_dict()
        # Must not raise
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        # web3_reason_code always present in dict (R4)
        assert "web3_reason_code" in d

    # --- P1 tests ---

    @pytest.mark.asyncio
    async def test_snapshot_reproducible(self):
        """#17: same text + config → identical snapshot dicts."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        r1 = await f.classify("organic meal kit delivery", skip_llm=True)
        r2 = await f.classify("organic meal kit delivery", skip_llm=True)
        assert r1.cascade_config_snapshot == r2.cascade_config_snapshot

    @pytest.mark.asyncio
    async def test_domain_blacklist_clean_when_not_blacklisted(self):
        """#18: normal text → domain_blacklist_reason_code == CLEAN."""
        config = ThesisFilterConfig(cascade_routing_enablement="disabled")
        f = ThesisFilter(config)
        result = await f.classify("organic meal kit delivery", skip_llm=True)
        assert result.domain_blacklist_reason_code == DomainBlacklistReasonCode.CLEAN
