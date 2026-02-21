"""
Tests for Phase A: Warm Intro Shadow Rollout.

Covers:
1. WarmIntroIndicator model validation + privacy boundary
2. ProspectPayload warm_intro_indicators field
3. Outbox worker mode branching (off/shadow/live)
4. Backward compat: payloads without warm_intro_indicators
5. Privacy: no attribution field leaks through
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connectors.notion_connector_v2 import (
    ProspectPayload,
    WarmIntroIndicator,
    InvestmentStage,
)
from workflows.notion_outbox_worker import NotionOutboxWorker


# =============================================================================
# WARM INTRO INDICATOR MODEL
# =============================================================================


class TestWarmIntroIndicator:
    """WarmIntroIndicator model validation and privacy."""

    def test_valid_indicator(self):
        ind = WarmIntroIndicator(
            investor_domain="sequoia.com",
            score_bucket="high",
            badge="Active LP",
            source_kind="notion_lp",
        )
        assert ind.investor_domain == "sequoia.com"
        assert ind.score_bucket == "high"
        assert ind.badge == "Active LP"
        assert ind.source_kind == "notion_lp"

    def test_gmail_source(self):
        ind = WarmIntroIndicator(
            investor_domain="a16z.com",
            score_bucket="medium",
            source_kind="gmail",
        )
        assert ind.source_kind == "gmail"
        assert ind.badge == ""  # Default

    def test_low_score_bucket(self):
        ind = WarmIntroIndicator(
            investor_domain="test.com",
            score_bucket="low",
            source_kind="gmail",
        )
        assert ind.score_bucket == "low"

    def test_invalid_score_bucket_rejected(self):
        with pytest.raises(Exception):  # Pydantic validation error
            WarmIntroIndicator(
                investor_domain="test.com",
                score_bucket="very_high",
                source_kind="gmail",
            )

    def test_invalid_source_kind_rejected(self):
        with pytest.raises(Exception):
            WarmIntroIndicator(
                investor_domain="test.com",
                score_bucket="high",
                source_kind="both",  # "both" is NOT in the enum
            )

    def test_extra_fields_forbidden(self):
        """Extra fields are rejected (extra='forbid' on the model)."""
        with pytest.raises(Exception):
            WarmIntroIndicator(
                investor_domain="test.com",
                score_bucket="high",
                source_kind="gmail",
                attribution="via John Smith",  # Privacy violation — forbidden
            )

    def test_no_attribution_field(self):
        """WarmIntroIndicator has no attribution field — privacy boundary."""
        fields = WarmIntroIndicator.model_fields
        assert "attribution" not in fields
        assert "email" not in fields
        assert "name" not in fields

    def test_serialization_roundtrip(self):
        ind = WarmIntroIndicator(
            investor_domain="greylock.com",
            score_bucket="high",
            badge="LP Committed",
            source_kind="notion_lp",
        )
        dumped = ind.model_dump(mode="json")
        restored = WarmIntroIndicator.model_validate(dumped)
        assert restored == ind


# =============================================================================
# PROSPECT PAYLOAD WITH INDICATORS
# =============================================================================


class TestProspectPayloadWarmIntro:
    """ProspectPayload warm_intro_indicators integration."""

    def test_default_empty_list(self):
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="k",
            stage=InvestmentStage.SEED,
        )
        assert payload.warm_intro_indicators == []

    def test_with_indicators(self):
        indicators = [
            WarmIntroIndicator(
                investor_domain="seq.com",
                score_bucket="high",
                source_kind="notion_lp",
            ),
        ]
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="k",
            stage=InvestmentStage.SEED,
            warm_intro_indicators=indicators,
        )
        assert len(payload.warm_intro_indicators) == 1
        assert payload.warm_intro_indicators[0].investor_domain == "seq.com"

    def test_none_normalized_to_empty(self):
        data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Seed",
            "warm_intro_indicators": None,
        }
        payload = ProspectPayload.model_validate(data)
        assert payload.warm_intro_indicators == []

    def test_serialization_preserves_indicators(self):
        indicators = [
            WarmIntroIndicator(
                investor_domain="a16z.com",
                score_bucket="medium",
                badge="Active",
                source_kind="gmail",
            ),
        ]
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="domain:test.com",
            stage=InvestmentStage.PRE_SEED,
            warm_intro_indicators=indicators,
        )
        dumped = payload.model_dump(mode="json")
        restored = ProspectPayload.model_validate(dumped)
        assert len(restored.warm_intro_indicators) == 1
        assert restored.warm_intro_indicators[0].investor_domain == "a16z.com"

    def test_legacy_payload_without_indicators(self):
        """Old outbox entries without warm_intro_indicators still deserialize."""
        legacy_data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Pre-Seed",
            "signal_types": ["funding"],
        }
        payload = ProspectPayload.model_validate(legacy_data)
        assert payload.warm_intro_indicators == []


# =============================================================================
# OUTBOX WORKER MODE BRANCHING
# =============================================================================


class TestOutboxWorkerWarmIntroModes:
    """Outbox worker applies correct mode for warm intro indicators."""

    def _make_payload(self, indicators=None) -> ProspectPayload:
        return ProspectPayload(
            discovery_id="d",
            company_name="Test Corp",
            canonical_key="domain:test.com",
            stage=InvestmentStage.SEED,
            warm_intro_indicators=indicators or [],
        )

    def _make_worker(self, mode: str = "off") -> NotionOutboxWorker:
        mock_store = AsyncMock()
        mock_notion = MagicMock()
        return NotionOutboxWorker(
            signal_store=mock_store,
            notion_connector=mock_notion,
            warm_intro_notion_mode=mode,
        )

    @pytest.mark.asyncio
    async def test_off_mode_strips_indicators(self):
        """In 'off' mode, indicators are stripped before push."""
        worker = self._make_worker("off")
        indicators = [
            WarmIntroIndicator(
                investor_domain="seq.com",
                score_bucket="high",
                source_kind="notion_lp",
            ),
        ]
        payload = self._make_payload(indicators)

        await worker._apply_warm_intro_mode(payload, outbox_id=1)

        assert payload.warm_intro_indicators == []

    @pytest.mark.asyncio
    async def test_shadow_mode_logs_then_strips(self):
        """In 'shadow' mode, indicators are logged to shadow_log then stripped."""
        worker = self._make_worker("shadow")
        indicators = [
            WarmIntroIndicator(
                investor_domain="a16z.com",
                score_bucket="medium",
                badge="Active",
                source_kind="gmail",
            ),
        ]
        payload = self._make_payload(indicators)

        await worker._apply_warm_intro_mode(payload, outbox_id=42)

        # Indicators should be stripped
        assert payload.warm_intro_indicators == []

        # Shadow log should be called
        worker.store.log_shadow_computation.assert_called_once()
        call_args = worker.store.log_shadow_computation.call_args
        assert call_args.kwargs["feature_name"] == "warm_intro_indicators"
        assert call_args.kwargs["canonical_key"] == "domain:test.com"

    @pytest.mark.asyncio
    async def test_live_mode_retains_indicators(self):
        """In 'live' mode, indicators are retained in the payload."""
        worker = self._make_worker("live")
        indicators = [
            WarmIntroIndicator(
                investor_domain="greylock.com",
                score_bucket="high",
                badge="LP",
                source_kind="notion_lp",
            ),
        ]
        payload = self._make_payload(indicators)

        await worker._apply_warm_intro_mode(payload, outbox_id=1)

        assert len(payload.warm_intro_indicators) == 1
        assert payload.warm_intro_indicators[0].investor_domain == "greylock.com"

    @pytest.mark.asyncio
    async def test_empty_indicators_noop(self):
        """No-op when payload has no indicators (any mode)."""
        for mode in ("off", "shadow", "live"):
            worker = self._make_worker(mode)
            payload = self._make_payload([])

            await worker._apply_warm_intro_mode(payload, outbox_id=1)

            assert payload.warm_intro_indicators == []

    @pytest.mark.asyncio
    async def test_shadow_mode_swallows_log_failure(self):
        """Shadow mode continues even if shadow_log write fails."""
        worker = self._make_worker("shadow")
        worker.store.log_shadow_computation = AsyncMock(side_effect=Exception("DB error"))

        indicators = [
            WarmIntroIndicator(
                investor_domain="test.com",
                score_bucket="low",
                source_kind="gmail",
            ),
        ]
        payload = self._make_payload(indicators)

        # Should not raise
        await worker._apply_warm_intro_mode(payload, outbox_id=1)

        # Indicators still stripped even on log failure
        assert payload.warm_intro_indicators == []

    @pytest.mark.asyncio
    async def test_shadow_logged_data_matches_model(self):
        """Shadow-logged data matches the WarmIntroIndicator schema."""
        worker = self._make_worker("shadow")
        indicators = [
            WarmIntroIndicator(
                investor_domain="a16z.com",
                score_bucket="high",
                badge="Active LP",
                source_kind="gmail",
            ),
            WarmIntroIndicator(
                investor_domain="greylock.com",
                score_bucket="medium",
                source_kind="notion_lp",
            ),
        ]
        payload = self._make_payload(indicators)

        await worker._apply_warm_intro_mode(payload, outbox_id=99)

        call_args = worker.store.log_shadow_computation.call_args
        logged_value = call_args.kwargs["computed_value"]

        assert len(logged_value) == 2
        assert logged_value[0]["investor_domain"] == "a16z.com"
        assert logged_value[0]["score_bucket"] == "high"
        assert logged_value[1]["investor_domain"] == "greylock.com"

        # Verify no attribution leaked
        for entry in logged_value:
            assert "attribution" not in entry
            assert "email" not in entry


# =============================================================================
# PRIVACY BOUNDARY
# =============================================================================


class TestPrivacyBoundary:
    """Verify no PII/attribution crosses the Notion boundary."""

    def test_indicator_has_only_bounded_fields(self):
        """WarmIntroIndicator only allows known bounded fields."""
        allowed = {"investor_domain", "score_bucket", "badge", "source_kind"}
        actual = set(WarmIntroIndicator.model_fields.keys())
        assert actual == allowed

    def test_no_free_text_attribution_in_serialized_payload(self):
        """Serialized payload with indicators has no attribution field."""
        indicators = [
            WarmIntroIndicator(
                investor_domain="test.com",
                score_bucket="high",
                source_kind="gmail",
            ),
        ]
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="k",
            stage=InvestmentStage.SEED,
            warm_intro_indicators=indicators,
        )
        dumped = payload.model_dump(mode="json")
        dumped_str = json.dumps(dumped)

        # No "attribution" key anywhere in the serialized output
        assert "attribution" not in dumped_str
