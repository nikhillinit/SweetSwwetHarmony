"""
Tests for utils/dns_promotion.py — DNS Phase 2 config accessor functions.

TDD RED phase: these tests define the public API contract for the
dns_promotion config module. All values are read via accessor functions
(no frozen globals at import time).
"""

from __future__ import annotations

import os

import pytest

from utils.dns_promotion import (
    is_dns_probe_enabled,
    is_dns_promote_enabled,
    get_dns_confidence_penalty,
    get_guardrail_override,
    get_effective_penalty_and_source,
)


# =============================================================================
# is_dns_probe_enabled()
# =============================================================================


class TestIsDnsProbeEnabled:
    """DNS_PROBE_ENABLED env var controls probe gating."""

    def test_default_is_false(self, monkeypatch):
        monkeypatch.delenv("DNS_PROBE_ENABLED", raising=False)
        assert is_dns_probe_enabled() is False

    def test_true_when_set(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_ENABLED", "true")
        assert is_dns_probe_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_ENABLED", "True")
        assert is_dns_probe_enabled() is True

    def test_false_when_explicit(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_ENABLED", "false")
        assert is_dns_probe_enabled() is False


# =============================================================================
# is_dns_promote_enabled()
# =============================================================================


class TestIsDnsPromoteEnabled:
    """DNS_PROBE_PROMOTE_ENABLED env var controls promotion gating."""

    def test_default_is_false(self, monkeypatch):
        monkeypatch.delenv("DNS_PROBE_PROMOTE_ENABLED", raising=False)
        assert is_dns_promote_enabled() is False

    def test_true_when_set(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "true")
        assert is_dns_promote_enabled() is True

    def test_false_when_explicit(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_PROMOTE_ENABLED", "false")
        assert is_dns_promote_enabled() is False


# =============================================================================
# get_dns_confidence_penalty()
# =============================================================================


class TestGetDnsConfidencePenalty:
    """DNS_PROBE_CONFIDENCE_PENALTY with deprecated DNS_PROMOTION_PENALTY alias."""

    def test_default_is_003(self, monkeypatch):
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)
        assert get_dns_confidence_penalty() == 0.03

    def test_canonical_var_overrides(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_CONFIDENCE_PENALTY", "0.05")
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)
        assert get_dns_confidence_penalty() == 0.05

    def test_deprecated_alias_used_when_canonical_absent(self, monkeypatch):
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.setenv("DNS_PROMOTION_PENALTY", "0.07")
        assert get_dns_confidence_penalty() == 0.07

    def test_canonical_wins_over_deprecated(self, monkeypatch):
        """When both set, canonical var wins."""
        monkeypatch.setenv("DNS_PROBE_CONFIDENCE_PENALTY", "0.04")
        monkeypatch.setenv("DNS_PROMOTION_PENALTY", "0.09")
        assert get_dns_confidence_penalty() == 0.04

    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_CONFIDENCE_PENALTY", "not_a_number")
        assert get_dns_confidence_penalty() == 0.03


# =============================================================================
# get_guardrail_override()
# =============================================================================


class TestGetGuardrailOverride:
    """DNS_PHASE2_GUARDRAILS_OVERRIDE and _REASON."""

    def test_default_no_override(self, monkeypatch):
        monkeypatch.delenv("DNS_PHASE2_GUARDRAILS_OVERRIDE", raising=False)
        monkeypatch.delenv("DNS_PHASE2_GUARDRAILS_OVERRIDE_REASON", raising=False)
        flag, reason = get_guardrail_override()
        assert flag is False
        assert reason == ""

    def test_override_enabled_with_reason(self, monkeypatch):
        monkeypatch.setenv("DNS_PHASE2_GUARDRAILS_OVERRIDE", "true")
        monkeypatch.setenv("DNS_PHASE2_GUARDRAILS_OVERRIDE_REASON", "manual approval by nikhi")
        flag, reason = get_guardrail_override()
        assert flag is True
        assert reason == "manual approval by nikhi"

    def test_override_enabled_without_reason(self, monkeypatch):
        monkeypatch.setenv("DNS_PHASE2_GUARDRAILS_OVERRIDE", "true")
        monkeypatch.delenv("DNS_PHASE2_GUARDRAILS_OVERRIDE_REASON", raising=False)
        flag, reason = get_guardrail_override()
        assert flag is True
        assert reason == ""


# =============================================================================
# get_effective_penalty_and_source()
# =============================================================================


class TestGetEffectivePenaltyAndSource:
    """Returns (value, source_label) for audit/logging."""

    def test_default_source(self, monkeypatch):
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)
        value, source = get_effective_penalty_and_source()
        assert value == 0.03
        assert source == "default"

    def test_canonical_source(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_CONFIDENCE_PENALTY", "0.05")
        monkeypatch.delenv("DNS_PROMOTION_PENALTY", raising=False)
        value, source = get_effective_penalty_and_source()
        assert value == 0.05
        assert source == "DNS_PROBE_CONFIDENCE_PENALTY"

    def test_deprecated_source(self, monkeypatch):
        monkeypatch.delenv("DNS_PROBE_CONFIDENCE_PENALTY", raising=False)
        monkeypatch.setenv("DNS_PROMOTION_PENALTY", "0.07")
        value, source = get_effective_penalty_and_source()
        assert value == 0.07
        assert source == "DNS_PROMOTION_PENALTY"

    def test_both_set_reports_canonical(self, monkeypatch):
        monkeypatch.setenv("DNS_PROBE_CONFIDENCE_PENALTY", "0.04")
        monkeypatch.setenv("DNS_PROMOTION_PENALTY", "0.09")
        value, source = get_effective_penalty_and_source()
        assert value == 0.04
        assert source == "DNS_PROBE_CONFIDENCE_PENALTY"
