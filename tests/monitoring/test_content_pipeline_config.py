"""Tests for content pipeline configuration parsing (v2.5).

Comprehensive tests for:
- ExtractorConfig
- TransportConfig
- WatchConfig
- ConfigParser
- PresetRegistry
- Watch.config_dict property
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from monitoring.content_pipeline.config import (
    ExtractorConfig,
    TransportConfig,
    WatchConfig,
    ConfigParser,
)
from monitoring.content_pipeline.presets import (
    PresetRegistry,
    get_preset,
    load_presets,
    DEFAULT_PRESETS_PATH,
)
from monitoring.models import Watch


class TestExtractorConfig:
    """Test ExtractorConfig dataclass."""

    def test_default_values(self):
        """ExtractorConfig should have sensible defaults."""
        config = ExtractorConfig()

        assert config.preset == "default"
        assert config.selectors is None
        assert config.fallback_on_empty is True

    def test_custom_values(self):
        """ExtractorConfig should accept custom values."""
        selectors = ["article", ".content", "#main"]
        config = ExtractorConfig(
            preset="article",
            selectors=selectors,
            fallback_on_empty=False,
        )

        assert config.preset == "article"
        assert config.selectors == selectors
        assert config.fallback_on_empty is False

    def test_to_dict_minimal(self):
        """to_dict should only include non-default values."""
        config = ExtractorConfig()
        result = config.to_dict()

        assert result == {"preset": "default"}
        # Should not include selectors=None or fallback_on_empty=True (default)
        assert "selectors" not in result
        assert "fallback_on_empty" not in result

    def test_to_dict_with_selectors(self):
        """to_dict should include selectors when set."""
        config = ExtractorConfig(selectors=["article", "main"])
        result = config.to_dict()

        assert result["selectors"] == ["article", "main"]

    def test_to_dict_with_fallback_false(self):
        """to_dict should include fallback_on_empty when False."""
        config = ExtractorConfig(fallback_on_empty=False)
        result = config.to_dict()

        assert result["fallback_on_empty"] is False

    def test_from_dict_empty(self):
        """from_dict with empty dict should return defaults."""
        config = ExtractorConfig.from_dict({})

        assert config.preset == "default"
        assert config.selectors is None
        assert config.fallback_on_empty is True

    def test_from_dict_with_values(self):
        """from_dict should restore all values."""
        data = {
            "preset": "pricing",
            "selectors": ["#pricing", ".pricing-table"],
            "fallback_on_empty": False,
        }
        config = ExtractorConfig.from_dict(data)

        assert config.preset == "pricing"
        assert config.selectors == ["#pricing", ".pricing-table"]
        assert config.fallback_on_empty is False

    def test_roundtrip(self):
        """to_dict and from_dict should be reversible."""
        original = ExtractorConfig(
            preset="spa",
            selectors=["#__NEXT_DATA__", "#__NUXT__"],
            fallback_on_empty=False,
        )

        serialized = original.to_dict()
        restored = ExtractorConfig.from_dict(serialized)

        assert restored.preset == original.preset
        assert restored.selectors == original.selectors
        assert restored.fallback_on_empty == original.fallback_on_empty


class TestTransportConfig:
    """Test TransportConfig dataclass."""

    def test_default_values(self):
        """TransportConfig should have sensible defaults."""
        config = TransportConfig()

        assert config.initial == "httpx"
        assert config.on_403 is None
        assert config.on_429 is None
        assert config.on_timeout is None

    def test_custom_values(self):
        """TransportConfig should accept custom values."""
        config = TransportConfig(
            initial="playwright",
            on_403="curl_cffi",
            on_429="exponential_backoff",
            on_timeout="playwright",
        )

        assert config.initial == "playwright"
        assert config.on_403 == "curl_cffi"
        assert config.on_429 == "exponential_backoff"
        assert config.on_timeout == "playwright"

    def test_to_dict_minimal(self):
        """to_dict should only include set values."""
        config = TransportConfig()
        result = config.to_dict()

        assert result == {"initial": "httpx"}
        assert "on_403" not in result
        assert "on_429" not in result
        assert "on_timeout" not in result

    def test_to_dict_with_escalation_rules(self):
        """to_dict should include all escalation rules when set."""
        config = TransportConfig(
            initial="httpx",
            on_403="playwright",
            on_429="exponential_backoff",
        )
        result = config.to_dict()

        assert result["initial"] == "httpx"
        assert result["on_403"] == "playwright"
        assert result["on_429"] == "exponential_backoff"
        assert "on_timeout" not in result

    def test_from_dict_empty(self):
        """from_dict with empty dict should return defaults."""
        config = TransportConfig.from_dict({})

        assert config.initial == "httpx"
        assert config.on_403 is None
        assert config.on_429 is None
        assert config.on_timeout is None

    def test_from_dict_with_values(self):
        """from_dict should restore all values."""
        data = {
            "initial": "playwright",
            "on_403": "curl_cffi",
            "on_429": None,
            "on_timeout": "retry",
        }
        config = TransportConfig.from_dict(data)

        assert config.initial == "playwright"
        assert config.on_403 == "curl_cffi"
        assert config.on_429 is None
        assert config.on_timeout == "retry"

    def test_roundtrip(self):
        """to_dict and from_dict should be reversible."""
        original = TransportConfig(
            initial="playwright",
            on_403="curl_cffi",
            on_429="exponential_backoff",
            on_timeout="retry",
        )

        serialized = original.to_dict()
        restored = TransportConfig.from_dict(serialized)

        assert restored.initial == original.initial
        assert restored.on_403 == original.on_403
        assert restored.on_429 == original.on_429
        assert restored.on_timeout == original.on_timeout


class TestWatchConfig:
    """Test WatchConfig dataclass."""

    def test_default_values(self):
        """WatchConfig should have sensible defaults."""
        config = WatchConfig()

        assert config.preset == "default"
        assert isinstance(config.extractor, ExtractorConfig)
        assert isinstance(config.transport, TransportConfig)
        assert config.extractor.preset == "default"
        assert config.transport.initial == "httpx"

    def test_to_dict(self):
        """to_dict should include all nested configs."""
        config = WatchConfig()
        result = config.to_dict()

        assert result["preset"] == "default"
        assert "extractor" in result
        assert "transport" in result
        assert result["extractor"]["preset"] == "default"
        assert result["transport"]["initial"] == "httpx"

    def test_to_json(self):
        """to_json should produce valid JSON."""
        config = WatchConfig()
        json_str = config.to_json()

        # Should be valid JSON
        parsed = json.loads(json_str)
        assert parsed["preset"] == "default"

    def test_from_dict_empty(self):
        """from_dict with empty dict should return defaults."""
        config = WatchConfig.from_dict({})

        assert config.preset == "default"
        assert config.extractor.preset == "default"
        assert config.transport.initial == "httpx"

    def test_from_dict_with_nested(self):
        """from_dict should handle nested configs."""
        data = {
            "preset": "pricing",
            "extractor": {
                "preset": "pricing",
                "selectors": ["#pricing"],
            },
            "transport": {
                "initial": "httpx",
                "on_403": "playwright",
            },
        }
        config = WatchConfig.from_dict(data)

        assert config.preset == "pricing"
        assert config.extractor.preset == "pricing"
        assert config.extractor.selectors == ["#pricing"]
        assert config.transport.on_403 == "playwright"

    def test_from_json_valid(self):
        """from_json should parse valid JSON."""
        json_str = '{"preset": "spa", "extractor": {"preset": "spa"}}'
        config = WatchConfig.from_json(json_str)

        assert config.preset == "spa"
        assert config.extractor.preset == "spa"

    def test_from_json_invalid_returns_default(self):
        """from_json with invalid JSON should return defaults."""
        json_str = "this is not valid json {"
        config = WatchConfig.from_json(json_str)

        assert config.preset == "default"
        assert config.extractor.preset == "default"

    def test_from_json_none_returns_default(self):
        """from_json with None should return defaults."""
        config = WatchConfig.from_json(None)

        assert config.preset == "default"

    def test_from_json_empty_string_returns_default(self):
        """from_json with empty string should return defaults."""
        config = WatchConfig.from_json("")

        assert config.preset == "default"

    def test_from_json_non_dict_returns_default(self):
        """from_json with non-dict JSON should return defaults."""
        # JSON array
        config = WatchConfig.from_json('["a", "b"]')
        assert config.preset == "default"

        # JSON string
        config = WatchConfig.from_json('"just a string"')
        assert config.preset == "default"

        # JSON number
        config = WatchConfig.from_json("42")
        assert config.preset == "default"

        # JSON null
        config = WatchConfig.from_json("null")
        assert config.preset == "default"

    def test_to_json_from_json_roundtrip(self):
        """to_json and from_json should be reversible."""
        original = WatchConfig(
            preset="article",
            extractor=ExtractorConfig(
                preset="article",
                selectors=["article", ".post"],
                fallback_on_empty=True,
            ),
            transport=TransportConfig(
                initial="httpx",
                on_403="playwright",
            ),
        )

        json_str = original.to_json()
        restored = WatchConfig.from_json(json_str)

        assert restored.preset == original.preset
        assert restored.extractor.preset == original.extractor.preset
        assert restored.extractor.selectors == original.extractor.selectors
        assert restored.transport.on_403 == original.transport.on_403


class TestConfigParser:
    """Test ConfigParser static utility."""

    def test_parse_returns_watch_config(self):
        """parse should return a WatchConfig instance."""
        json_str = '{"preset": "spa"}'
        config = ConfigParser.parse(json_str)

        assert isinstance(config, WatchConfig)
        assert config.preset == "spa"

    def test_parse_with_none(self):
        """parse with None should return defaults."""
        config = ConfigParser.parse(None)

        assert isinstance(config, WatchConfig)
        assert config.preset == "default"

    def test_parse_with_invalid_json(self):
        """parse with invalid JSON should return defaults."""
        config = ConfigParser.parse("not json")

        assert isinstance(config, WatchConfig)
        assert config.preset == "default"

    def test_parse_with_full_config(self):
        """parse should handle complete config JSON."""
        json_str = json.dumps({
            "preset": "pricing_table_v1",
            "extractor": {
                "preset": "pricing",
                "selectors": ["#pricing", ".plans"],
                "fallback_on_empty": True,
            },
            "transport": {
                "initial": "httpx",
                "on_403": "curl_cffi",
            },
        })
        config = ConfigParser.parse(json_str)

        assert config.preset == "pricing_table_v1"
        assert config.extractor.preset == "pricing"
        assert config.extractor.selectors == ["#pricing", ".plans"]
        assert config.transport.on_403 == "curl_cffi"


class TestPresetRegistry:
    """Test PresetRegistry class."""

    def test_load_returns_dict(self):
        """load should return a dictionary of presets."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))
        presets = registry.load()

        assert isinstance(presets, dict)
        assert "default" in presets

    def test_load_sets_loaded_flag(self):
        """load should set _loaded flag after first call."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))

        assert registry._loaded is False
        registry.load()
        assert registry._loaded is True

    def test_get_uses_cached_presets(self):
        """get() should use cached _presets without reloading.

        Note: load() doesn't have early return caching, but get() checks _loaded
        flag and uses _presets directly.
        """
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))

        # Load first
        registry.load()
        assert registry._loaded is True

        # Mutate the internal dict
        registry._presets["test_marker"] = {"cached": True}

        # get() should use cached data (it checks _loaded before calling load())
        # Looking up a key that doesn't require load() means we should still see our marker
        # Actually, get() will return "test_marker" directly since it checks _loaded first
        preset = registry.get("test_marker")

        assert preset["cached"] is True

    def test_get_returns_preset_by_name(self):
        """get should return preset configuration by name."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))
        registry.load()

        preset = registry.get("default")

        assert isinstance(preset, dict)
        assert "extractor" in preset
        assert "transport" in preset

    def test_get_unknown_returns_default(self):
        """get with unknown name should return default preset."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))
        registry.load()

        preset = registry.get("nonexistent_preset_xyz")

        # Should fall back to default
        assert isinstance(preset, dict)
        assert preset == registry.get("default")

    def test_get_auto_loads(self):
        """get should automatically load if not already loaded."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))

        # Don't call load() first
        preset = registry.get("default")

        assert isinstance(preset, dict)
        assert registry._loaded is True

    def test_default_presets_structure(self):
        """Default presets should have required structure."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))
        presets = registry.load()

        # Check default preset structure
        default = presets["default"]
        assert "description" in default
        assert "extractor" in default
        assert "transport" in default
        assert "content_limits" in default

        # Check spa preset
        assert "spa" in presets
        spa = presets["spa"]
        assert spa["transport"]["initial"] == "playwright"


class TestPresetRegistryWithYaml:
    """Test PresetRegistry loading from actual YAML file."""

    @pytest.fixture
    def real_presets_path(self):
        """Get path to real watch_presets.yaml."""
        return DEFAULT_PRESETS_PATH

    def test_load_real_yaml_file(self, real_presets_path):
        """Should load presets from actual YAML file if it exists."""
        if not real_presets_path.exists():
            pytest.skip("watch_presets.yaml not found")

        registry = PresetRegistry(presets_path=real_presets_path)
        presets = registry.load()

        assert isinstance(presets, dict)
        assert "default" in presets

    def test_real_yaml_has_expected_presets(self, real_presets_path):
        """Real YAML file should contain expected preset names."""
        if not real_presets_path.exists():
            pytest.skip("watch_presets.yaml not found")

        registry = PresetRegistry(presets_path=real_presets_path)
        presets = registry.load()

        # These presets should exist in the real file
        expected_presets = ["default", "pricing_table_v1", "blog_post_v1", "spa_hydration_v1"]
        for preset_name in expected_presets:
            assert preset_name in presets, f"Missing preset: {preset_name}"

    def test_get_preset_convenience_function(self, real_presets_path):
        """get_preset convenience function should work."""
        # Reset global registry to use default path
        import monitoring.content_pipeline.presets as presets_module
        presets_module._registry = None

        if not real_presets_path.exists():
            pytest.skip("watch_presets.yaml not found")

        preset = get_preset("default")

        assert isinstance(preset, dict)
        assert "extractor" in preset

    def test_load_presets_convenience_function(self, real_presets_path):
        """load_presets convenience function should work."""
        if not real_presets_path.exists():
            pytest.skip("watch_presets.yaml not found")

        presets = load_presets(real_presets_path)

        assert isinstance(presets, dict)
        assert "default" in presets


class TestPresetRegistryErrorHandling:
    """Test PresetRegistry error handling."""

    def test_invalid_yaml_uses_defaults(self, tmp_path):
        """Invalid YAML should fall back to defaults."""
        invalid_yaml = tmp_path / "invalid.yaml"
        invalid_yaml.write_text("this: is: not: valid: yaml: {{{{")

        registry = PresetRegistry(presets_path=invalid_yaml)
        presets = registry.load()

        # Should still return defaults
        assert "default" in presets

    def test_non_dict_yaml_uses_defaults(self, tmp_path):
        """YAML file with non-dict content should fall back to defaults."""
        non_dict_yaml = tmp_path / "array.yaml"
        non_dict_yaml.write_text("- item1\n- item2\n- item3")

        registry = PresetRegistry(presets_path=non_dict_yaml)
        presets = registry.load()

        # Should still return defaults
        assert "default" in presets

    def test_yaml_import_error_uses_defaults(self, tmp_path):
        """ImportError when loading yaml should fall back to defaults.

        The PresetRegistry handles ImportError by catching it and using defaults.
        This tests that the error handling path works correctly.
        """
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        # Create a valid YAML file (so the path exists check passes)
        valid_yaml = tmp_path / "presets.yaml"
        valid_yaml.write_text("presets:\n  custom: {}\n")

        registry = PresetRegistry(presets_path=valid_yaml)

        # Patch __import__ to simulate yaml not being available
        with patch.object(builtins, "__import__", side_effect=mock_import):
            presets = registry.load()

        # Should fall back to defaults
        assert "default" in presets
        assert registry._loaded is True


class TestWatchConfigDictProperty:
    """Test Watch.config_dict property integration."""

    def test_config_dict_with_none(self):
        """config_dict with None config_json should return default."""
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json=None,
        )

        assert watch.config_dict == {"preset": "default"}

    def test_config_dict_with_valid_json(self):
        """config_dict with valid JSON should parse correctly."""
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json='{"preset": "spa", "custom_field": true}',
        )

        config = watch.config_dict
        assert config["preset"] == "spa"
        assert config["custom_field"] is True

    def test_config_dict_with_invalid_json(self):
        """config_dict with invalid JSON should return default."""
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json="not valid json {{{",
        )

        assert watch.config_dict == {"preset": "default"}

    def test_config_dict_with_non_dict_json(self):
        """config_dict with non-dict JSON should return default."""
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json='["array", "not", "dict"]',
        )

        assert watch.config_dict == {"preset": "default"}

    def test_config_dict_with_complex_config(self):
        """config_dict should handle complex nested config."""
        config_data = {
            "preset": "pricing_table_v1",
            "extractor": {
                "preset": "pricing",
                "selectors": ["#pricing", ".plans"],
            },
            "transport": {
                "initial": "httpx",
                "on_403": "curl_cffi",
            },
        }
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json=json.dumps(config_data),
        )

        config = watch.config_dict
        assert config["preset"] == "pricing_table_v1"
        assert config["extractor"]["selectors"] == ["#pricing", ".plans"]
        assert config["transport"]["on_403"] == "curl_cffi"

    def test_config_dict_appears_in_to_dict(self):
        """config_dict should be included in watch.to_dict() output."""
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json='{"preset": "spa"}',
        )

        result = watch.to_dict()
        assert "config_dict" in result
        assert result["config_dict"]["preset"] == "spa"


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_configparser_to_watchconfig_flow(self):
        """Full flow from JSON string to typed config."""
        json_str = json.dumps({
            "preset": "blog_post_v1",
            "extractor": {
                "preset": "article",
                "selectors": ["article", ".post-content"],
                "fallback_on_empty": True,
            },
            "transport": {
                "initial": "httpx",
                "on_403": "playwright",
            },
        })

        # Parse through ConfigParser
        config = ConfigParser.parse(json_str)

        # Verify typed access
        assert config.preset == "blog_post_v1"
        assert config.extractor.preset == "article"
        assert config.extractor.selectors == ["article", ".post-content"]
        assert config.transport.on_403 == "playwright"

        # Roundtrip back to JSON
        restored_json = config.to_json()
        restored = WatchConfig.from_json(restored_json)
        assert restored.preset == config.preset

    def test_watch_config_dict_with_configparser(self):
        """Watch.config_dict can be used with ConfigParser."""
        watch = Watch(
            id=1,
            canonical_key="domain:example.com",
            url="https://example.com",
            config_json='{"preset": "spa_hydration_v1"}',
        )

        # Get raw dict from watch
        raw_config = watch.config_dict

        # Convert to typed config
        typed_config = WatchConfig.from_dict(raw_config)

        assert typed_config.preset == "spa_hydration_v1"

    def test_preset_registry_provides_complete_config(self):
        """PresetRegistry presets can be used as WatchConfig source."""
        registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))
        preset_data = registry.get("default")

        # Preset data should be usable with WatchConfig.from_dict
        config = WatchConfig.from_dict(preset_data)

        assert config.preset == "default"
        assert config.extractor.fallback_on_empty is True

    def test_full_pipeline_simulation(self):
        """Simulate full config pipeline from watch to typed config."""
        # 1. Create watch with config_json
        watch = Watch(
            id=42,
            canonical_key="domain:startup.io",
            url="https://startup.io/pricing",
            config_json=json.dumps({
                "preset": "pricing_table_v1",
                "extractor": {
                    "preset": "pricing",
                    "selectors": ["#pricing"],
                },
                "transport": {
                    "initial": "httpx",
                    "on_403": "curl_cffi",
                },
            }),
        )

        # 2. Parse config
        config = ConfigParser.parse(watch.config_json)

        # 3. Verify all typed fields
        assert config.preset == "pricing_table_v1"
        assert config.extractor.preset == "pricing"
        assert config.extractor.selectors == ["#pricing"]
        assert config.extractor.fallback_on_empty is True  # default
        assert config.transport.initial == "httpx"
        assert config.transport.on_403 == "curl_cffi"
        assert config.transport.on_429 is None  # default
        assert config.transport.on_timeout is None  # default

        # 4. Serialize back
        serialized = config.to_json()
        restored = WatchConfig.from_json(serialized)
        assert restored.preset == config.preset
