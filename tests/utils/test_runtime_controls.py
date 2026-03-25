"""Tests for RuntimeControls module (v2 policy configuration).

Tests for env var parsing, membership validation, invariant enforcement,
and legacy kwarg mapping for the Negative Keyword Policy v2 runtime controls.
"""
from __future__ import annotations

import logging
import pytest

from utils.runtime_controls import (
    RuntimeControls,
    VALID_LOADER_MODES,
    VALID_ENABLEMENTS,
    _normalize_string,
    _parse_bool_env,
)


class TestMembershipValidation:
    """Test membership validation rejects invalid explicit values."""

    def test_invalid_v2_enablement_raises_value_error(self):
        """v2_enablement='invalid' should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            RuntimeControls.from_env(v2_enablement="invalid")

        assert "Invalid v2_enablement" in str(exc_info.value)
        assert "'invalid'" in str(exc_info.value)
        assert "disabled" in str(exc_info.value)

    def test_invalid_policy_loader_mode_raises_value_error(self):
        """policy_loader_mode='invalid' should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            RuntimeControls.from_env(policy_loader_mode="invalid")

        assert "Invalid policy_loader_mode" in str(exc_info.value)
        assert "'invalid'" in str(exc_info.value)
        assert "permissive" in str(exc_info.value)

    def test_dataclass_post_init_validates_loader_mode(self):
        """Direct dataclass construction with invalid loader_mode raises."""
        with pytest.raises(ValueError) as exc_info:
            RuntimeControls(
                policy_loader_mode="unknown",
                v2_enablement="disabled",
                v2_execution_enabled=False,
            )

        assert "Invalid policy_loader_mode" in str(exc_info.value)

    def test_dataclass_post_init_validates_enablement(self):
        """Direct dataclass construction with invalid enablement raises."""
        with pytest.raises(ValueError) as exc_info:
            RuntimeControls(
                policy_loader_mode="permissive",
                v2_enablement="unknown",
                v2_execution_enabled=False,
            )

        assert "Invalid v2_enablement" in str(exc_info.value)


class TestEnvEmptyWhitespaceTreatedAsUnset:
    """Test env empty/whitespace treated as unset."""

    def test_empty_v2_enablement_env_uses_default(self, monkeypatch):
        """V2_ENABLEMENT='' should use default 'disabled'."""
        monkeypatch.setenv("V2_ENABLEMENT", "")

        controls = RuntimeControls.from_env()

        assert controls.v2_enablement == "disabled"

    def test_whitespace_v2_enablement_env_uses_default(self, monkeypatch):
        """V2_ENABLEMENT='  ' should use default 'disabled'."""
        monkeypatch.setenv("V2_ENABLEMENT", "  ")

        controls = RuntimeControls.from_env()

        assert controls.v2_enablement == "disabled"

    def test_empty_policy_loader_mode_env_uses_derived(self, monkeypatch):
        """POLICY_LOADER_MODE='' with disabled enablement uses 'permissive'."""
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.setenv("POLICY_LOADER_MODE", "")

        controls = RuntimeControls.from_env()

        assert controls.policy_loader_mode == "permissive"

    def test_whitespace_policy_loader_mode_env_uses_derived(self, monkeypatch):
        """POLICY_LOADER_MODE='   ' with shadow enablement uses 'strict'."""
        monkeypatch.setenv("POLICY_LOADER_MODE", "   ")
        monkeypatch.setenv("V2_ENABLEMENT", "shadow")

        controls = RuntimeControls.from_env()

        assert controls.policy_loader_mode == "strict"


class TestBooleanParsing:
    """Test boolean parsing for V2_EXECUTION_ENABLED."""

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_truthy_values_parse_to_true(self, monkeypatch, value):
        """V2_EXECUTION_ENABLED truthy values (true/1/yes/on) parse to True."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", value)

        controls = RuntimeControls.from_env()

        assert controls.v2_execution_enabled is True

    @pytest.mark.parametrize("value", ["TRUE", "True", "YES", "Yes", "ON", "On"])
    def test_truthy_values_case_insensitive(self, monkeypatch, value):
        """Truthy values are case-insensitive."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", value)

        controls = RuntimeControls.from_env()

        assert controls.v2_execution_enabled is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off"])
    def test_falsy_values_parse_to_false(self, monkeypatch, value):
        """V2_EXECUTION_ENABLED falsy values (false/0/no/off) parse to False."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", value)

        controls = RuntimeControls.from_env()

        assert controls.v2_execution_enabled is False

    @pytest.mark.parametrize("value", ["FALSE", "False", "NO", "No", "OFF", "Off"])
    def test_falsy_values_case_insensitive(self, monkeypatch, value):
        """Falsy values are case-insensitive."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", value)

        controls = RuntimeControls.from_env()

        assert controls.v2_execution_enabled is False

    def test_unrecognized_value_logs_warning_and_derives_default(self, monkeypatch, caplog):
        """V2_EXECUTION_ENABLED='maybe' (unrecognized) logs WARNING, derives default."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", "maybe")
        monkeypatch.setenv("V2_ENABLEMENT", "disabled")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        # Should derive from enablement (disabled -> False)
        assert controls.v2_execution_enabled is False

        # Should log warning
        assert "Unrecognized boolean value" in caplog.text
        assert "maybe" in caplog.text

    def test_unrecognized_value_derives_true_for_shadow(self, monkeypatch, caplog):
        """Unrecognized V2_EXECUTION_ENABLED with shadow enablement derives True."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", "uncertain")
        monkeypatch.setenv("V2_ENABLEMENT", "shadow")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        # Should derive from enablement (shadow -> True)
        assert controls.v2_execution_enabled is True
        assert "Unrecognized boolean value" in caplog.text


class TestLegacyInvariantTrap:
    """Test legacy enable_v2_policy kwarg mapping."""

    def test_enable_v2_policy_true_maps_to_shadow(self):
        """enable_v2_policy=True maps to enablement='shadow'."""
        controls = RuntimeControls.from_env(enable_v2_policy=True)

        assert controls.v2_enablement == "shadow"

    def test_enable_v2_policy_true_derives_strict_loader_mode(self):
        """enable_v2_policy=True derives loader_mode='strict'."""
        controls = RuntimeControls.from_env(enable_v2_policy=True)

        assert controls.policy_loader_mode == "strict"

    def test_enable_v2_policy_false_maps_to_disabled(self):
        """enable_v2_policy=False maps to enablement='disabled'."""
        controls = RuntimeControls.from_env(enable_v2_policy=False)

        assert controls.v2_enablement == "disabled"

    def test_enable_v2_policy_false_derives_permissive_loader_mode(self):
        """enable_v2_policy=False derives loader_mode='permissive'."""
        controls = RuntimeControls.from_env(enable_v2_policy=False)

        assert controls.policy_loader_mode == "permissive"


class TestEnvConflictCorrection:
    """Test env conflict correction with WARNING and auto-correct."""

    def test_shadow_with_permissive_auto_corrects_to_strict(self, monkeypatch, caplog):
        """V2_ENABLEMENT='shadow' + POLICY_LOADER_MODE='permissive' auto-corrects to strict."""
        monkeypatch.setenv("V2_ENABLEMENT", "shadow")
        monkeypatch.setenv("POLICY_LOADER_MODE", "permissive")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        # Should auto-correct to strict
        assert controls.policy_loader_mode == "strict"

        # Should log warning
        assert "Invariant violation" in caplog.text
        assert "shadow" in caplog.text
        assert "strict" in caplog.text

    def test_live_with_permissive_auto_corrects_to_strict(self, monkeypatch, caplog):
        """V2_ENABLEMENT='live' + POLICY_LOADER_MODE='permissive' auto-corrects to strict."""
        monkeypatch.setenv("V2_ENABLEMENT", "live")
        monkeypatch.setenv("POLICY_LOADER_MODE", "permissive")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        # Should auto-correct to strict
        assert controls.policy_loader_mode == "strict"
        assert "Invariant violation" in caplog.text

    def test_live_with_execution_disabled_auto_corrects(self, monkeypatch, caplog):
        """V2_ENABLEMENT='live' + V2_EXECUTION_ENABLED='false' auto-corrects to True."""
        monkeypatch.setenv("V2_ENABLEMENT", "live")
        monkeypatch.setenv("V2_EXECUTION_ENABLED", "false")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        # Should auto-correct to True
        assert controls.v2_execution_enabled is True
        assert "Invariant violation" in caplog.text
        assert "live" in caplog.text


class TestPrecedence:
    """Test precedence order: explicit kwarg > legacy kwarg > env var > default."""

    def test_explicit_kwarg_beats_env_var(self, monkeypatch):
        """Explicit v2_enablement kwarg beats env var."""
        monkeypatch.setenv("V2_ENABLEMENT", "live")

        controls = RuntimeControls.from_env(v2_enablement="shadow")

        assert controls.v2_enablement == "shadow"

    def test_explicit_loader_mode_beats_env_var(self, monkeypatch):
        """Explicit policy_loader_mode kwarg beats env var."""
        monkeypatch.setenv("POLICY_LOADER_MODE", "permissive")

        # Note: shadow requires strict, so use disabled to test explicit permissive
        controls = RuntimeControls.from_env(
            v2_enablement="disabled",
            policy_loader_mode="permissive",
        )

        assert controls.policy_loader_mode == "permissive"

    def test_legacy_kwarg_beats_env_var_when_modern_not_provided(self, monkeypatch):
        """Legacy enable_v2_policy beats env var when modern v2_enablement not provided."""
        monkeypatch.setenv("V2_ENABLEMENT", "live")

        controls = RuntimeControls.from_env(enable_v2_policy=True)

        # Legacy maps True -> shadow, which should override env "live"
        assert controls.v2_enablement == "shadow"

    def test_modern_kwarg_beats_legacy_kwarg(self):
        """Modern v2_enablement kwarg beats legacy enable_v2_policy."""
        controls = RuntimeControls.from_env(
            v2_enablement="live",
            enable_v2_policy=False,  # Would map to disabled
        )

        # Modern kwarg wins
        assert controls.v2_enablement == "live"

    def test_explicit_execution_beats_env_var(self, monkeypatch):
        """Explicit v2_execution_enabled kwarg beats env var."""
        monkeypatch.setenv("V2_EXECUTION_ENABLED", "false")

        controls = RuntimeControls.from_env(v2_execution_enabled=True)

        assert controls.v2_execution_enabled is True


class TestDefaultDerivation:
    """Test default value derivation."""

    def test_no_args_no_env_defaults_to_disabled_permissive(self, monkeypatch):
        """No args/env -> enablement='disabled', loader_mode='permissive'."""
        # Ensure no env vars are set
        monkeypatch.delenv("V2_ENABLEMENT", raising=False)
        monkeypatch.delenv("POLICY_LOADER_MODE", raising=False)
        monkeypatch.delenv("V2_EXECUTION_ENABLED", raising=False)

        controls = RuntimeControls.from_env()

        assert controls.v2_enablement == "disabled"
        assert controls.policy_loader_mode == "permissive"
        assert controls.v2_execution_enabled is False

    def test_shadow_enablement_derives_strict_loader_mode(self, monkeypatch):
        """enablement='shadow' -> loader_mode='strict' (derived)."""
        monkeypatch.delenv("POLICY_LOADER_MODE", raising=False)

        controls = RuntimeControls.from_env(v2_enablement="shadow")

        assert controls.policy_loader_mode == "strict"

    def test_live_enablement_derives_strict_loader_mode(self, monkeypatch):
        """enablement='live' -> loader_mode='strict' (derived)."""
        monkeypatch.delenv("POLICY_LOADER_MODE", raising=False)

        controls = RuntimeControls.from_env(v2_enablement="live")

        assert controls.policy_loader_mode == "strict"

    def test_disabled_enablement_derives_permissive_loader_mode(self, monkeypatch):
        """enablement='disabled' -> loader_mode='permissive' (derived)."""
        monkeypatch.delenv("POLICY_LOADER_MODE", raising=False)

        controls = RuntimeControls.from_env(v2_enablement="disabled")

        assert controls.policy_loader_mode == "permissive"

    def test_shadow_enablement_derives_execution_enabled_true(self, monkeypatch):
        """enablement='shadow' -> v2_execution_enabled=True (derived)."""
        monkeypatch.delenv("V2_EXECUTION_ENABLED", raising=False)

        controls = RuntimeControls.from_env(v2_enablement="shadow")

        assert controls.v2_execution_enabled is True

    def test_disabled_enablement_derives_execution_enabled_false(self, monkeypatch):
        """enablement='disabled' -> v2_execution_enabled=False (derived)."""
        monkeypatch.delenv("V2_EXECUTION_ENABLED", raising=False)

        controls = RuntimeControls.from_env(v2_enablement="disabled")

        assert controls.v2_execution_enabled is False


class TestNormalizeStringHelper:
    """Test _normalize_string helper function."""

    def test_none_returns_none(self):
        """None input returns None."""
        assert _normalize_string(None) is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert _normalize_string("") is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only string returns None."""
        assert _normalize_string("   ") is None

    def test_strips_and_lowercases(self):
        """Strips whitespace and lowercases."""
        assert _normalize_string("  SHADOW  ") == "shadow"
        assert _normalize_string("\tLIVE\n") == "live"

    def test_mixed_case_normalized(self):
        """Mixed case is normalized to lowercase."""
        assert _normalize_string("ShAdOw") == "shadow"


class TestParseBoolEnvHelper:
    """Test _parse_bool_env helper function."""

    def test_unset_derives_from_enablement_shadow(self):
        """Unset value derives True for shadow/live enablement."""
        result = _parse_bool_env(None, "shadow")
        assert result is True

    def test_unset_derives_from_enablement_disabled(self):
        """Unset value derives False for disabled enablement."""
        result = _parse_bool_env(None, "disabled")
        assert result is False

    def test_empty_derives_from_enablement(self):
        """Empty string derives from enablement."""
        result = _parse_bool_env("", "shadow")
        assert result is True


class TestConvenienceProperties:
    """Test convenience properties on RuntimeControls."""

    def test_is_v2_active_true_for_shadow(self):
        """is_v2_active returns True for shadow mode."""
        controls = RuntimeControls.from_env(v2_enablement="shadow")
        assert controls.is_v2_active is True

    def test_is_v2_active_true_for_live(self):
        """is_v2_active returns True for live mode."""
        controls = RuntimeControls.from_env(v2_enablement="live")
        assert controls.is_v2_active is True

    def test_is_v2_active_false_for_disabled(self):
        """is_v2_active returns False for disabled mode."""
        controls = RuntimeControls.from_env(v2_enablement="disabled")
        assert controls.is_v2_active is False

    def test_is_shadow_mode(self):
        """is_shadow_mode returns True only for shadow."""
        shadow = RuntimeControls.from_env(v2_enablement="shadow")
        live = RuntimeControls.from_env(v2_enablement="live")
        disabled = RuntimeControls.from_env(v2_enablement="disabled")

        assert shadow.is_shadow_mode is True
        assert live.is_shadow_mode is False
        assert disabled.is_shadow_mode is False

    def test_is_live_mode(self):
        """is_live_mode returns True only for live."""
        shadow = RuntimeControls.from_env(v2_enablement="shadow")
        live = RuntimeControls.from_env(v2_enablement="live")
        disabled = RuntimeControls.from_env(v2_enablement="disabled")

        assert shadow.is_live_mode is False
        assert live.is_live_mode is True
        assert disabled.is_live_mode is False


class TestInvalidEnvValueWarnings:
    """Test warnings for invalid env var values (vs. explicit args which raise)."""

    def test_invalid_env_enablement_warns_and_uses_default(self, monkeypatch, caplog):
        """Invalid V2_ENABLEMENT env value warns and uses default."""
        monkeypatch.setenv("V2_ENABLEMENT", "invalid_mode")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        assert controls.v2_enablement == "disabled"
        assert "Invalid V2_ENABLEMENT" in caplog.text

    def test_invalid_env_loader_mode_warns_and_derives(self, monkeypatch, caplog):
        """Invalid POLICY_LOADER_MODE env value warns and derives from enablement."""
        monkeypatch.setenv("POLICY_LOADER_MODE", "invalid_mode")
        monkeypatch.setenv("V2_ENABLEMENT", "shadow")

        with caplog.at_level(logging.WARNING):
            controls = RuntimeControls.from_env()

        # Should derive from enablement (shadow -> strict)
        assert controls.policy_loader_mode == "strict"
        assert "Invalid POLICY_LOADER_MODE" in caplog.text
