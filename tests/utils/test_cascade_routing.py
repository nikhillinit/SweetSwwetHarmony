"""Phase 1.5+: Cascade routing phase gate and mode wiring tests.

Tests:
- Phase gate enforcement (ADR-4)
- Cascade routing enablement on RuntimeControls
- Unsafe flag matrix
- BtC vs BTC context qualification
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from utils.runtime_controls import RuntimeControls


# ===========================================================================
# Phase 1.5: Cascade routing enablement on RuntimeControls
# ===========================================================================

class TestCascadeRoutingEnablement:
    """Test cascade_routing_enablement field on RuntimeControls."""

    def test_cascade_field_exists(self, monkeypatch):
        """RuntimeControls should have cascade_routing_enablement field."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env(
            cascade_routing_enablement="disabled",
        )
        assert hasattr(controls, "cascade_routing_enablement")
        assert controls.cascade_routing_enablement == "disabled"

    def test_cascade_defaults_to_disabled(self, monkeypatch):
        """cascade_routing_enablement should default to 'disabled'."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env()
        assert controls.cascade_routing_enablement == "disabled"

    def test_cascade_from_env(self, monkeypatch):
        """CASCADE_ROUTING_ENABLEMENT env var should be read."""
        monkeypatch.setenv("CASCADE_ROUTING_ENABLEMENT", "shadow")
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env()
        assert controls.cascade_routing_enablement == "shadow"

    def test_cascade_kwarg_overrides_env(self, monkeypatch):
        """Explicit kwarg should override env var."""
        monkeypatch.setenv("CASCADE_ROUTING_ENABLEMENT", "live")
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env(
            cascade_routing_enablement="disabled",
        )
        assert controls.cascade_routing_enablement == "disabled"

    def test_cascade_invalid_value_raises(self, monkeypatch):
        """Invalid cascade value should raise ValueError."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        with pytest.raises(ValueError, match="cascade_routing_enablement"):
            RuntimeControls.from_env(
                cascade_routing_enablement="turbo",
            )

    def test_is_cascade_active(self, monkeypatch, tmp_path):
        """is_cascade_active should be True for shadow and live."""
        import yaml

        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)

        # Create a passed gate file for live mode test
        gates_file = tmp_path / "phase_gates.yaml"
        gates_file.write_text(yaml.dump({
            "web3_ambiguity_gate": {
                "status": "passed",
                "passed_at": "2026-03-01T12:00:00Z",
                "blocking_for": ["CASCADE_ROUTING_ENABLEMENT=live"],
            }
        }))

        for mode, expected in [("disabled", False), ("shadow", True), ("live", True)]:
            controls = RuntimeControls.from_env(
                cascade_routing_enablement=mode,
                phase_gates_path=str(gates_file),
            )
            assert controls.is_cascade_active == expected, f"mode={mode}"

    def test_is_cascade_shadow(self, monkeypatch, tmp_path):
        """is_cascade_shadow should be True only for shadow."""
        import yaml

        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)

        gates_file = tmp_path / "phase_gates.yaml"
        gates_file.write_text(yaml.dump({
            "web3_ambiguity_gate": {
                "status": "passed",
                "passed_at": "2026-03-01T12:00:00Z",
                "blocking_for": ["CASCADE_ROUTING_ENABLEMENT=live"],
            }
        }))

        for mode, expected in [("disabled", False), ("shadow", True), ("live", False)]:
            controls = RuntimeControls.from_env(
                cascade_routing_enablement=mode,
                phase_gates_path=str(gates_file),
            )
            assert controls.is_cascade_shadow == expected, f"mode={mode}"


# ===========================================================================
# Phase 1.5: Phase Gate Enforcement
# ===========================================================================

class TestPhaseGateEnforcement:
    """Test that cascade=live is blocked when web3 gate is not passed."""

    def test_cascade_live_blocked_by_unpassed_gate(self, monkeypatch):
        """cascade=live + gate pending → downgraded to disabled."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        # Gate is pending by default in phase_gates.yaml
        controls = RuntimeControls.from_env(
            cascade_routing_enablement="live",
        )
        # Should be downgraded to disabled because gate not passed
        assert controls.cascade_routing_enablement == "disabled"

    def test_cascade_live_allowed_after_gate_pass(self, monkeypatch, tmp_path):
        """cascade=live + gate passed → live allowed."""
        import yaml

        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)

        # Create a phase gates file with passed gate
        gates_file = tmp_path / "phase_gates.yaml"
        gates_file.write_text(yaml.dump({
            "web3_ambiguity_gate": {
                "status": "passed",
                "passed_at": "2026-03-01T12:00:00Z",
                "blocking_for": ["CASCADE_ROUTING_ENABLEMENT=live"],
            }
        }))

        controls = RuntimeControls.from_env(
            cascade_routing_enablement="live",
            phase_gates_path=str(gates_file),
        )
        assert controls.cascade_routing_enablement == "live"

    def test_cascade_shadow_not_blocked_by_gate(self, monkeypatch):
        """cascade=shadow is NOT blocked by phase gate (only live is blocked)."""
        monkeypatch.delenv("CASCADE_ROUTING_ENABLEMENT", raising=False)
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env(
            cascade_routing_enablement="shadow",
        )
        assert controls.cascade_routing_enablement == "shadow"


# ===========================================================================
# Phase 1.5: Unsafe Flag Matrix
# ===========================================================================

class TestUnsafeFlagMatrix:
    """Test unsafe flag combinations are fail-closed."""

    def test_invalid_cascade_env_defaults_disabled(self, monkeypatch):
        """Invalid CASCADE_ROUTING_ENABLEMENT env → default to disabled."""
        monkeypatch.setenv("CASCADE_ROUTING_ENABLEMENT", "invalid_value")
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("ML_ENABLEMENT", raising=False)
        controls = RuntimeControls.from_env()
        assert controls.cascade_routing_enablement == "disabled"


# ===========================================================================
# Phase 1.5: Token Context Qualification
# ===========================================================================

class TestTokenContextQualification:
    """Test BtC/B2C collision handling (context-qualified tokens)."""

    def test_btc_vs_b2c_no_false_veto(self):
        """'b2c' and 'btc' should not collide — b2c is consumer, not crypto."""
        from utils.thesis_matcher import ThesisMatcher, HARD_REJECT_KEYWORDS

        matcher = ThesisMatcher()
        # b2c should NOT be a hard reject
        assert "b2c" not in HARD_REJECT_KEYWORDS
        # The text should score positively for consumer signal
        fit = matcher.score("b2c payments platform for consumers")
        # Should NOT have crypto hard rejects
        assert not fit.trace.matched_hard_rejects or "b2c" not in fit.trace.matched_hard_rejects

    def test_btc_in_crypto_context_rejected(self):
        """'btc' in crypto context should be detected by web3 detector."""
        from utils.web3_detector import Web3Detector

        detector = Web3Detector()
        result = detector.detect("BTC trading platform for crypto investors")
        assert result.is_crypto

    def test_b2c_ecommerce_not_crypto(self):
        """'B2C e-commerce platform' should not trigger crypto veto."""
        from utils.web3_detector import Web3Detector

        detector = Web3Detector()
        result = detector.detect("B2C e-commerce platform for direct to consumer brands")
        assert not result.is_crypto
