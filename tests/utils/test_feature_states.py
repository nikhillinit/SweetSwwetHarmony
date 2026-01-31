"""Tests for Feature States module (SHADOW experimentation infrastructure).

TDD tests for the ACTIVE/SHADOW/OFF feature state model from founder_intel spec.
"""
import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from utils.feature_states import (
    FeatureState,
    FeatureConfig,
    FeatureRegistry,
    DEFAULT_FEATURES,
)


class TestFeatureStateEnum:
    """Test FeatureState enum values."""

    def test_enum_has_active(self):
        """ACTIVE state affects routing/scoring."""
        assert FeatureState.ACTIVE.value == "active"

    def test_enum_has_shadow(self):
        """SHADOW state is computed + logged, but 0 weight."""
        assert FeatureState.SHADOW.value == "shadow"

    def test_enum_has_off(self):
        """OFF state is not computed."""
        assert FeatureState.OFF.value == "off"

    def test_enum_members_count(self):
        """Only 3 states: ACTIVE, SHADOW, OFF."""
        assert len(FeatureState) == 3


class TestFeatureConfig:
    """Test FeatureConfig dataclass."""

    def test_create_feature_config(self):
        """Can create a feature configuration."""
        config = FeatureConfig(
            name="boilerplate_defense",
            state=FeatureState.SHADOW,
            description="Token-based fingerprinting to filter starter kit noise",
        )
        assert config.name == "boilerplate_defense"
        assert config.state == FeatureState.SHADOW
        assert "fingerprinting" in config.description

    def test_feature_config_has_owner(self):
        """Feature configs can have an owner for accountability."""
        config = FeatureConfig(
            name="team_shape",
            state=FeatureState.SHADOW,
            owner="@nikhi",
            description="2-5 contributor analysis",
        )
        assert config.owner == "@nikhi"

    def test_feature_config_default_owner_is_none(self):
        """Owner defaults to None."""
        config = FeatureConfig(
            name="test_feature",
            state=FeatureState.OFF,
            description="Test",
        )
        assert config.owner is None

    def test_feature_config_to_dict(self):
        """Feature configs can serialize to dict."""
        config = FeatureConfig(
            name="boilerplate_defense",
            state=FeatureState.SHADOW,
            owner="@nikhi",
            description="Noise filter",
        )
        d = config.to_dict()
        assert d["name"] == "boilerplate_defense"
        assert d["state"] == "shadow"
        assert d["owner"] == "@nikhi"
        assert d["description"] == "Noise filter"

    def test_feature_config_from_dict(self):
        """Feature configs can deserialize from dict."""
        d = {
            "name": "team_shape",
            "state": "shadow",
            "owner": None,
            "description": "Contributor analysis",
        }
        config = FeatureConfig.from_dict(d)
        assert config.name == "team_shape"
        assert config.state == FeatureState.SHADOW
        assert config.owner is None


class TestDefaultFeatures:
    """Test default feature configurations from spec."""

    def test_default_features_exist(self):
        """DEFAULT_FEATURES constant is defined."""
        assert DEFAULT_FEATURES is not None
        assert isinstance(DEFAULT_FEATURES, dict)

    def test_boilerplate_defense_default_shadow(self):
        """Boilerplate defense should start in SHADOW mode."""
        assert "boilerplate_defense" in DEFAULT_FEATURES
        assert DEFAULT_FEATURES["boilerplate_defense"].state == FeatureState.SHADOW

    def test_team_shape_default_shadow(self):
        """Team shape metrics should start in SHADOW mode."""
        assert "team_shape" in DEFAULT_FEATURES
        assert DEFAULT_FEATURES["team_shape"].state == FeatureState.SHADOW

    def test_founder_surfaces_default_shadow(self):
        """Founder surface extraction should start in SHADOW mode."""
        assert "founder_surfaces" in DEFAULT_FEATURES
        assert DEFAULT_FEATURES["founder_surfaces"].state == FeatureState.SHADOW

    def test_smart_money_default_off(self):
        """Smart money watchlist should start OFF (needs curation)."""
        assert "smart_money" in DEFAULT_FEATURES
        assert DEFAULT_FEATURES["smart_money"].state == FeatureState.OFF

    def test_stargazer_expansion_default_off(self):
        """Stargazer expansion is killed per spec."""
        assert "stargazer_expansion" in DEFAULT_FEATURES
        assert DEFAULT_FEATURES["stargazer_expansion"].state == FeatureState.OFF


class TestFeatureRegistry:
    """Test FeatureRegistry class."""

    def test_create_registry_with_defaults(self):
        """Registry can be created with default features."""
        registry = FeatureRegistry()
        assert registry is not None

    def test_get_state_returns_default(self):
        """get_state returns the default state for known features."""
        registry = FeatureRegistry()
        state = registry.get_state("boilerplate_defense")
        assert state == FeatureState.SHADOW

    def test_get_state_unknown_returns_off(self):
        """get_state returns OFF for unknown features (safe default)."""
        registry = FeatureRegistry()
        state = registry.get_state("unknown_feature_xyz")
        assert state == FeatureState.OFF

    def test_set_state(self):
        """set_state changes feature state."""
        registry = FeatureRegistry()
        registry.set_state("boilerplate_defense", FeatureState.ACTIVE)
        assert registry.get_state("boilerplate_defense") == FeatureState.ACTIVE

    def test_set_state_creates_new_feature(self):
        """set_state can create new features."""
        registry = FeatureRegistry()
        registry.set_state("new_feature", FeatureState.SHADOW, description="New")
        assert registry.get_state("new_feature") == FeatureState.SHADOW

    def test_is_active(self):
        """is_active returns True only for ACTIVE features."""
        registry = FeatureRegistry()
        registry.set_state("active_feature", FeatureState.ACTIVE)
        registry.set_state("shadow_feature", FeatureState.SHADOW)

        assert registry.is_active("active_feature") is True
        assert registry.is_active("shadow_feature") is False
        assert registry.is_active("unknown") is False

    def test_is_shadow(self):
        """is_shadow returns True only for SHADOW features."""
        registry = FeatureRegistry()
        assert registry.is_shadow("boilerplate_defense") is True
        assert registry.is_shadow("smart_money") is False

    def test_is_enabled(self):
        """is_enabled returns True for both ACTIVE and SHADOW (computed)."""
        registry = FeatureRegistry()
        registry.set_state("active_feature", FeatureState.ACTIVE)

        # ACTIVE is enabled (affects output)
        assert registry.is_enabled("active_feature") is True
        # SHADOW is enabled (computed, logged, but 0 weight)
        assert registry.is_enabled("boilerplate_defense") is True
        # OFF is not enabled
        assert registry.is_enabled("smart_money") is False

    def test_list_features(self):
        """list_features returns all registered features."""
        registry = FeatureRegistry()
        features = registry.list_features()
        assert "boilerplate_defense" in features
        assert "team_shape" in features

    def test_list_features_by_state(self):
        """Can filter features by state."""
        registry = FeatureRegistry()
        shadow_features = registry.list_features(state=FeatureState.SHADOW)
        off_features = registry.list_features(state=FeatureState.OFF)

        assert "boilerplate_defense" in shadow_features
        assert "smart_money" in off_features
        assert "smart_money" not in shadow_features

    def test_get_config(self):
        """get_config returns full FeatureConfig."""
        registry = FeatureRegistry()
        config = registry.get_config("boilerplate_defense")
        assert config is not None
        assert config.name == "boilerplate_defense"
        assert config.state == FeatureState.SHADOW

    def test_get_config_unknown_returns_none(self):
        """get_config returns None for unknown features."""
        registry = FeatureRegistry()
        config = registry.get_config("unknown_feature")
        assert config is None


class TestFeatureRegistryPersistence:
    """Test FeatureRegistry config file persistence."""

    def test_save_to_file(self):
        """Registry can save state to JSON file."""
        registry = FeatureRegistry()
        registry.set_state("boilerplate_defense", FeatureState.ACTIVE)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_path = Path(f.name)

        try:
            registry.save_to_file(config_path)
            assert config_path.exists()

            # Verify content
            with open(config_path) as f:
                data = json.load(f)
            assert data["boilerplate_defense"]["state"] == "active"
        finally:
            config_path.unlink(missing_ok=True)

    def test_load_from_file(self):
        """Registry can load state from JSON file."""
        # Create a config file
        config_data = {
            "boilerplate_defense": {
                "name": "boilerplate_defense",
                "state": "active",
                "owner": "@nikhi",
                "description": "Promoted to active",
            },
            "custom_feature": {
                "name": "custom_feature",
                "state": "shadow",
                "owner": None,
                "description": "Custom",
            },
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            config_path = Path(f.name)

        try:
            registry = FeatureRegistry.load_from_file(config_path)
            assert registry.get_state("boilerplate_defense") == FeatureState.ACTIVE
            assert registry.get_state("custom_feature") == FeatureState.SHADOW
        finally:
            config_path.unlink(missing_ok=True)

    def test_load_from_file_merges_defaults(self):
        """Loading from file merges with defaults (doesn't lose features)."""
        # Config file only has one feature
        config_data = {
            "boilerplate_defense": {
                "name": "boilerplate_defense",
                "state": "active",
                "owner": None,
                "description": "Promoted",
            },
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            config_path = Path(f.name)

        try:
            registry = FeatureRegistry.load_from_file(config_path)
            # File value takes precedence
            assert registry.get_state("boilerplate_defense") == FeatureState.ACTIVE
            # Defaults preserved for missing features
            assert registry.get_state("team_shape") == FeatureState.SHADOW
        finally:
            config_path.unlink(missing_ok=True)

    def test_load_from_nonexistent_file_uses_defaults(self):
        """Loading from nonexistent file uses defaults."""
        registry = FeatureRegistry.load_from_file(Path("/nonexistent/path.json"))
        assert registry.get_state("boilerplate_defense") == FeatureState.SHADOW


class TestFeatureRegistryEnvOverrides:
    """Test environment variable overrides for feature states."""

    def test_env_override_active(self):
        """Environment variables can force features to ACTIVE."""
        with patch.dict('os.environ', {'FEATURE_BOILERPLATE_DEFENSE': 'active'}):
            registry = FeatureRegistry()
            assert registry.get_state("boilerplate_defense") == FeatureState.ACTIVE

    def test_env_override_off(self):
        """Environment variables can force features to OFF."""
        with patch.dict('os.environ', {'FEATURE_TEAM_SHAPE': 'off'}):
            registry = FeatureRegistry()
            assert registry.get_state("team_shape") == FeatureState.OFF

    def test_env_override_case_insensitive(self):
        """Environment variable values are case-insensitive."""
        with patch.dict('os.environ', {'FEATURE_BOILERPLATE_DEFENSE': 'ACTIVE'}):
            registry = FeatureRegistry()
            assert registry.get_state("boilerplate_defense") == FeatureState.ACTIVE

    def test_env_override_invalid_ignored(self):
        """Invalid environment variable values are ignored."""
        with patch.dict('os.environ', {'FEATURE_BOILERPLATE_DEFENSE': 'invalid'}):
            registry = FeatureRegistry()
            # Falls back to default
            assert registry.get_state("boilerplate_defense") == FeatureState.SHADOW
