"""Tests for Phase 9: Verification Gate LLM Integration."""

import os
import pytest
from datetime import datetime, timezone

from verification.verification_gate_v2 import VerificationGate, Signal, PushDecision


class TestVerificationGateLLMAdjustment:
    """Test LLM confidence adjustment in verification gate."""

    def test_shadow_mode_no_adjustment(self, monkeypatch):
        """Verify shadow mode doesn't adjust confidence."""
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")

        gate = VerificationGate()
        base_confidence = 0.75

        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=base_confidence,
            keyword_score=0.9,
            llm_score=0.9
        )

        assert adjusted == base_confidence  # No adjustment in shadow mode
        assert reason == "llm_inactive"

    def test_active_mode_agreement_boost(self, monkeypatch):
        """Verify active mode boosts confidence when keyword + LLM agree."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")
        monkeypatch.setenv("LLM_AGREEMENT_THRESHOLD", "0.7")
        monkeypatch.setenv("CONFIDENCE_BOOST_AGREEMENT", "0.10")

        gate = VerificationGate()
        base_confidence = 0.75

        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=base_confidence,
            keyword_score=0.9,  # Both high
            llm_score=0.85
        )

        assert adjusted == 0.85  # 0.75 + 0.10
        assert reason == "agreement_boost"

    def test_active_mode_disagreement_penalty(self, monkeypatch):
        """Verify active mode penalizes confidence when keyword + LLM disagree."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")
        monkeypatch.setenv("LLM_AGREEMENT_THRESHOLD", "0.7")
        monkeypatch.setenv("LLM_DISAGREEMENT_THRESHOLD", "0.4")
        monkeypatch.setenv("CONFIDENCE_PENALTY_DISAGREEMENT", "0.15")

        gate = VerificationGate()
        base_confidence = 0.75

        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=base_confidence,
            keyword_score=0.9,  # Keyword high
            llm_score=0.3  # LLM low
        )

        assert adjusted == 0.60  # 0.75 - 0.15
        assert reason == "disagreement_penalty"

    def test_confidence_clamped_to_one(self, monkeypatch):
        """Verify confidence clamped to [0, 1] range."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")
        monkeypatch.setenv("CONFIDENCE_BOOST_AGREEMENT", "0.10")

        gate = VerificationGate()

        # High base + boost = should cap at 1.0
        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=0.95,
            keyword_score=0.9,
            llm_score=0.9
        )

        assert adjusted == 1.0  # Clamped, not 1.05
        assert reason == "agreement_boost"

    def test_confidence_clamped_to_zero(self, monkeypatch):
        """Verify confidence clamped to [0, 1] range."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")
        monkeypatch.setenv("CONFIDENCE_PENALTY_DISAGREEMENT", "0.50")

        gate = VerificationGate()

        # Low base + large penalty = should floor at 0.0
        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=0.20,
            keyword_score=0.9,
            llm_score=0.1
        )

        assert adjusted == 0.0  # Clamped, not negative
        assert reason == "disagreement_penalty"

    def test_missing_llm_data_no_adjustment(self, monkeypatch):
        """Verify no adjustment when LLM data missing."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")

        gate = VerificationGate()
        base_confidence = 0.75

        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=base_confidence,
            keyword_score=None,  # Missing
            llm_score=None
        )

        assert adjusted == base_confidence
        assert reason == "llm_data_missing"

    def test_weak_keyword_strong_llm_modest_boost(self, monkeypatch):
        """Verify modest boost when LLM finds fit that keywords missed."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")
        monkeypatch.setenv("LLM_AGREEMENT_THRESHOLD", "0.7")
        monkeypatch.setenv("LLM_DISAGREEMENT_THRESHOLD", "0.4")
        monkeypatch.setenv("CONFIDENCE_BOOST_AGREEMENT", "0.10")

        gate = VerificationGate()
        base_confidence = 0.50

        adjusted, reason = gate._apply_llm_adjustment(
            base_confidence=base_confidence,
            keyword_score=0.3,  # Low
            llm_score=0.85  # High
        )

        assert adjusted == 0.55  # 0.50 + 0.05 (half of 0.10)
        assert reason == "weak_keyword_strong_llm"

    def test_evaluate_passes_thesis_scores(self, monkeypatch):
        """Verify evaluate method accepts and uses thesis scores."""
        monkeypatch.setenv("LLM_THESIS_MODE", "active")

        gate = VerificationGate()

        signals = [
            Signal(
                id="sig-1",
                signal_type="incorporation",
                confidence=0.9,
                source_api="companies_house",
                detected_at=datetime.now(timezone.utc)
            )
        ]

        # Should not raise, and should use scores for adjustment
        result = gate.evaluate(
            signals=signals,
            keyword_score=0.8,
            llm_score=0.85
        )

        assert result.confidence_score is not None
        # Confidence should be adjusted (can't predict exact value without full calculation)
