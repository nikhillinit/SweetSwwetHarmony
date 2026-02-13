"""Tests for Wave 4 write feature guards.

Coverage:
- All features: disabled → raises FeatureDisabledError; active → passes
- Shadow mode: merge logs but doesn't raise; non-merge features reject shadow
- Invalid env value → warning + disabled default
- is_feature_enabled helper
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from workflows.feature_guards import (
    FeatureDisabledError,
    WriteFeature,
    WriteMode,
    assert_write_enabled,
    get_write_mode,
    is_feature_enabled,
)


class TestGetWriteMode:
    """Test get_write_mode() reads env vars correctly."""

    def test_merge_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MERGE_WRITES_ENABLED", raising=False)
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.DISABLED

    def test_merge_active(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.ACTIVE

    def test_merge_shadow(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "shadow")
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.SHADOW

    def test_bulk_triage_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("BULK_TRIAGE_ENABLED", raising=False)
        assert get_write_mode(WriteFeature.BULK_TRIAGE) == WriteMode.DISABLED

    def test_bulk_triage_active(self, monkeypatch):
        monkeypatch.setenv("BULK_TRIAGE_ENABLED", "active")
        assert get_write_mode(WriteFeature.BULK_TRIAGE) == WriteMode.ACTIVE

    def test_hunter_promote_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HUNTER_PROMOTE_ENABLED", raising=False)
        assert get_write_mode(WriteFeature.HUNTER_PROMOTE) == WriteMode.DISABLED

    def test_hunter_promote_active(self, monkeypatch):
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        assert get_write_mode(WriteFeature.HUNTER_PROMOTE) == WriteMode.ACTIVE

    def test_invalid_value_defaults_disabled(self, monkeypatch, caplog):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "invalid_value")
        with caplog.at_level(logging.WARNING):
            mode = get_write_mode(WriteFeature.MERGE_WRITES)
        assert mode == WriteMode.DISABLED
        assert "Invalid" in caplog.text

    def test_bulk_triage_rejects_shadow(self, monkeypatch, caplog):
        """Bulk triage only supports disabled/active, not shadow."""
        monkeypatch.setenv("BULK_TRIAGE_ENABLED", "shadow")
        with caplog.at_level(logging.WARNING):
            mode = get_write_mode(WriteFeature.BULK_TRIAGE)
        assert mode == WriteMode.DISABLED
        assert "Invalid" in caplog.text

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "Active")
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.ACTIVE

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "  active  ")
        assert get_write_mode(WriteFeature.MERGE_WRITES) == WriteMode.ACTIVE


class TestAssertWriteEnabled:
    """Test assert_write_enabled() guard function."""

    def test_active_passes(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
        mode = assert_write_enabled(WriteFeature.MERGE_WRITES)
        assert mode == WriteMode.ACTIVE

    def test_disabled_raises(self, monkeypatch):
        monkeypatch.delenv("MERGE_WRITES_ENABLED", raising=False)
        with pytest.raises(FeatureDisabledError) as exc_info:
            assert_write_enabled(WriteFeature.MERGE_WRITES)
        err = exc_info.value
        assert err.feature == WriteFeature.MERGE_WRITES
        assert err.env_var == "MERGE_WRITES_ENABLED"
        assert err.current_mode == "disabled"

    def test_shadow_raises_without_allow_shadow(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "shadow")
        with pytest.raises(FeatureDisabledError):
            assert_write_enabled(WriteFeature.MERGE_WRITES, allow_shadow=False)

    def test_shadow_passes_with_allow_shadow(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "shadow")
        mode = assert_write_enabled(WriteFeature.MERGE_WRITES, allow_shadow=True)
        assert mode == WriteMode.SHADOW

    def test_bulk_triage_disabled_raises(self, monkeypatch):
        monkeypatch.delenv("BULK_TRIAGE_ENABLED", raising=False)
        with pytest.raises(FeatureDisabledError) as exc_info:
            assert_write_enabled(WriteFeature.BULK_TRIAGE)
        assert "BULK_TRIAGE_ENABLED" in str(exc_info.value)

    def test_bulk_triage_active_passes(self, monkeypatch):
        monkeypatch.setenv("BULK_TRIAGE_ENABLED", "active")
        mode = assert_write_enabled(WriteFeature.BULK_TRIAGE)
        assert mode == WriteMode.ACTIVE

    def test_hunter_promote_disabled_raises(self, monkeypatch):
        monkeypatch.delenv("HUNTER_PROMOTE_ENABLED", raising=False)
        with pytest.raises(FeatureDisabledError):
            assert_write_enabled(WriteFeature.HUNTER_PROMOTE)

    def test_hunter_promote_active_passes(self, monkeypatch):
        monkeypatch.setenv("HUNTER_PROMOTE_ENABLED", "active")
        mode = assert_write_enabled(WriteFeature.HUNTER_PROMOTE)
        assert mode == WriteMode.ACTIVE


class TestIsFeatureEnabled:
    """Test is_feature_enabled() helper."""

    def test_disabled_returns_false(self, monkeypatch):
        monkeypatch.delenv("MERGE_WRITES_ENABLED", raising=False)
        assert is_feature_enabled(WriteFeature.MERGE_WRITES) is False

    def test_active_returns_true(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "active")
        assert is_feature_enabled(WriteFeature.MERGE_WRITES) is True

    def test_shadow_returns_true(self, monkeypatch):
        monkeypatch.setenv("MERGE_WRITES_ENABLED", "shadow")
        assert is_feature_enabled(WriteFeature.MERGE_WRITES) is True


class TestDriftMonitoring:
    """Test DRIFT_MONITORING feature guard."""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("DRIFT_MONITORING_ENABLED", raising=False)
        assert get_write_mode(WriteFeature.DRIFT_MONITORING) == WriteMode.DISABLED

    def test_active_passes(self, monkeypatch):
        monkeypatch.setenv("DRIFT_MONITORING_ENABLED", "active")
        mode = assert_write_enabled(WriteFeature.DRIFT_MONITORING)
        assert mode == WriteMode.ACTIVE

    def test_disabled_raises(self, monkeypatch):
        monkeypatch.delenv("DRIFT_MONITORING_ENABLED", raising=False)
        with pytest.raises(FeatureDisabledError) as exc_info:
            assert_write_enabled(WriteFeature.DRIFT_MONITORING)
        err = exc_info.value
        assert err.feature == WriteFeature.DRIFT_MONITORING
        assert err.env_var == "DRIFT_MONITORING_ENABLED"
        assert err.current_mode == "disabled"
