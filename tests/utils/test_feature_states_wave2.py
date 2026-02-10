"""Tests for Wave 2 shadow_entity_resolution feature flag in FeatureRegistry.

Validates:
- Default state is OFF for shadow_entity_resolution
- Environment variable overrides (shadow, active)
- is_enabled behavior (OFF = not enabled, SHADOW = enabled)
- Feature list includes shadow_entity_resolution
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from utils.feature_states import (
    DEFAULT_FEATURES,
    FeatureConfig,
    FeatureRegistry,
    FeatureState,
)


# =============================================================================
# DEFAULT STATE TESTS
# =============================================================================

class TestShadowEntityResolutionDefaults:
    """Tests for shadow_entity_resolution default configuration."""

    def test_shadow_entity_resolution_default_off(self):
        """shadow_entity_resolution should default to OFF."""
        reg = FeatureRegistry()
        assert reg.get_state("shadow_entity_resolution") == FeatureState.OFF

    def test_shadow_entity_resolution_in_default_features(self):
        """shadow_entity_resolution should be registered in DEFAULT_FEATURES."""
        assert "shadow_entity_resolution" in DEFAULT_FEATURES
        config = DEFAULT_FEATURES["shadow_entity_resolution"]
        assert config.state == FeatureState.OFF
        assert config.name == "shadow_entity_resolution"

    def test_shadow_entity_resolution_has_description(self):
        """shadow_entity_resolution should have a non-empty description."""
        config = DEFAULT_FEATURES["shadow_entity_resolution"]
        assert config.description
        assert len(config.description) > 10


# =============================================================================
# ENVIRONMENT OVERRIDE TESTS
# =============================================================================

class TestShadowEntityResolutionEnvOverride:
    """Tests for environment variable overrides on shadow_entity_resolution."""

    def test_shadow_entity_resolution_env_override_shadow(self, monkeypatch):
        """FEATURE_SHADOW_ENTITY_RESOLUTION=shadow should set state to SHADOW."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "shadow")
        reg = FeatureRegistry()
        assert reg.get_state("shadow_entity_resolution") == FeatureState.SHADOW

    def test_shadow_entity_resolution_env_override_active(self, monkeypatch):
        """FEATURE_SHADOW_ENTITY_RESOLUTION=active should set state to ACTIVE."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "active")
        reg = FeatureRegistry()
        assert reg.get_state("shadow_entity_resolution") == FeatureState.ACTIVE

    def test_shadow_entity_resolution_env_override_off(self, monkeypatch):
        """FEATURE_SHADOW_ENTITY_RESOLUTION=off should keep state OFF."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "off")
        reg = FeatureRegistry()
        assert reg.get_state("shadow_entity_resolution") == FeatureState.OFF

    def test_shadow_entity_resolution_env_override_invalid(self, monkeypatch):
        """Invalid env value should fall back to default OFF."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "bogus")
        reg = FeatureRegistry()
        assert reg.get_state("shadow_entity_resolution") == FeatureState.OFF


# =============================================================================
# IS_ENABLED / IS_ACTIVE / IS_SHADOW TESTS
# =============================================================================

class TestShadowEntityResolutionEnabled:
    """Tests for is_enabled, is_active, is_shadow on shadow_entity_resolution."""

    def test_is_enabled_shadow_entity_resolution_off(self):
        """OFF state should not be enabled (not computed)."""
        reg = FeatureRegistry()
        assert reg.is_enabled("shadow_entity_resolution") is False

    def test_is_enabled_when_shadow(self, monkeypatch):
        """SHADOW state should be enabled (computed + logged)."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "shadow")
        reg = FeatureRegistry()
        assert reg.is_enabled("shadow_entity_resolution") is True

    def test_is_enabled_when_active(self, monkeypatch):
        """ACTIVE state should be enabled (computed + affects output)."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "active")
        reg = FeatureRegistry()
        assert reg.is_enabled("shadow_entity_resolution") is True

    def test_is_shadow_when_shadow(self, monkeypatch):
        """is_shadow should return True only for SHADOW state."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "shadow")
        reg = FeatureRegistry()
        assert reg.is_shadow("shadow_entity_resolution") is True

    def test_is_active_when_active(self, monkeypatch):
        """is_active should return True only for ACTIVE state."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "active")
        reg = FeatureRegistry()
        assert reg.is_active("shadow_entity_resolution") is True

    def test_is_active_when_shadow(self, monkeypatch):
        """is_active should return False when in SHADOW state."""
        monkeypatch.setenv("FEATURE_SHADOW_ENTITY_RESOLUTION", "shadow")
        reg = FeatureRegistry()
        assert reg.is_active("shadow_entity_resolution") is False


# =============================================================================
# FEATURE LIST TESTS
# =============================================================================

class TestFeatureListIncludesShadowEntity:
    """Tests for feature list including shadow_entity_resolution."""

    def test_feature_list_includes_shadow_entity_resolution(self):
        """list_features() should include shadow_entity_resolution."""
        reg = FeatureRegistry()
        features = reg.list_features()
        assert "shadow_entity_resolution" in features

    def test_feature_list_off_filter_includes_shadow_entity(self):
        """list_features(state=OFF) should include shadow_entity_resolution."""
        reg = FeatureRegistry()
        off_features = reg.list_features(state=FeatureState.OFF)
        assert "shadow_entity_resolution" in off_features

    def test_feature_list_shadow_filter_excludes_default(self):
        """list_features(state=SHADOW) should not include shadow_entity_resolution by default."""
        reg = FeatureRegistry()
        shadow_features = reg.list_features(state=FeatureState.SHADOW)
        assert "shadow_entity_resolution" not in shadow_features

    def test_feature_config_retrievable(self):
        """get_config should return FeatureConfig for shadow_entity_resolution."""
        reg = FeatureRegistry()
        config = reg.get_config("shadow_entity_resolution")
        assert config is not None
        assert isinstance(config, FeatureConfig)
        assert config.name == "shadow_entity_resolution"
        assert config.state == FeatureState.OFF


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
