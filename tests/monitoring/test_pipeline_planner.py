"""
Tests for Pipeline Planner

Tests auto-selection of presets based on page type classification.
Follows TDD - tests written first.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from monitoring.page_type_classifier import PageType, PageClassification
from monitoring.content_pipeline.config import WatchConfig, ExtractorConfig, TransportConfig
from monitoring.content_pipeline.presets import PresetRegistry


class TestPageTypePresetMapping:
    """Test the page type to preset name mapping."""

    def test_pricing_maps_to_pricing_table_v1(self):
        """Pricing page type should map to pricing_table_v1 preset."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.PRICING] == "pricing_table_v1"

    def test_news_maps_to_blog_post_v1(self):
        """News page type should map to blog_post_v1 preset."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.NEWS] == "blog_post_v1"

    def test_careers_maps_to_default(self):
        """Careers page type should map to default (no special preset yet)."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.CAREERS] == "default"

    def test_product_maps_to_default(self):
        """Product page type should map to default preset."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.PRODUCT] == "default"

    def test_terms_maps_to_default(self):
        """Terms page type should map to default preset."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.TERMS] == "default"

    def test_landing_maps_to_default(self):
        """Landing page type should map to default preset."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.LANDING] == "default"

    def test_unknown_maps_to_default(self):
        """Unknown page type should map to default preset."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        assert PAGE_TYPE_PRESETS[PageType.UNKNOWN] == "default"

    def test_all_page_types_have_mapping(self):
        """All page types should have a mapping defined."""
        from monitoring.content_pipeline.planner import PAGE_TYPE_PRESETS

        for page_type in PageType:
            assert page_type in PAGE_TYPE_PRESETS, f"Missing mapping for {page_type}"


class TestPipelinePlannerExplicitConfig:
    """Test PipelinePlanner with explicit config_json provided."""

    def test_explicit_config_used_directly(self):
        """When config_json is provided, it should be used directly."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        config_json = json.dumps({
            "preset": "custom_preset",
            "extractor": {"preset": "article", "selectors": ["article"]},
            "transport": {"initial": "playwright"},
        })

        result = planner.plan("https://example.com/pricing", config_json=config_json)

        assert isinstance(result, WatchConfig)
        assert result.preset == "custom_preset"
        assert result.extractor.preset == "article"
        assert result.transport.initial == "playwright"

    def test_explicit_config_not_marked_auto_selected(self):
        """Explicit config should not have auto_selected metadata."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        config_json = json.dumps({"preset": "spa"})

        result = planner.plan("https://example.com", config_json=config_json)

        # metadata should indicate NOT auto-selected
        assert result.metadata.get("auto_selected") is False

    def test_invalid_config_json_falls_back_to_auto(self):
        """Invalid config_json should fall back to auto-selection."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        invalid_json = "not valid json {"

        result = planner.plan("https://example.com/pricing", config_json=invalid_json)

        # Should auto-select based on URL
        assert result.metadata.get("auto_selected") is True
        assert result.preset == "pricing_table_v1"


class TestPipelinePlannerAutoSelection:
    """Test PipelinePlanner auto-selection when no config_json is provided."""

    def test_pricing_url_selects_pricing_preset(self):
        """Pricing URL should auto-select pricing_table_v1 preset."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing")

        assert isinstance(result, WatchConfig)
        assert result.preset == "pricing_table_v1"
        assert result.metadata.get("auto_selected") is True
        assert result.metadata.get("page_type") == "pricing"
        assert result.metadata.get("preset_name") == "pricing_table_v1"

    def test_careers_url_selects_default_preset(self):
        """Careers URL should auto-select default preset."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/careers")

        assert result.preset == "default"
        assert result.metadata.get("page_type") == "careers"
        assert result.metadata.get("preset_name") == "default"

    def test_blog_url_selects_blog_preset(self):
        """Blog/news URL should auto-select blog_post_v1 preset."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/blog/announcement")

        assert result.preset == "blog_post_v1"
        assert result.metadata.get("page_type") == "news"

    def test_unknown_url_selects_default_preset(self):
        """Unknown URL should auto-select default preset."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/random-page-xyz")

        assert result.preset == "default"
        assert result.metadata.get("page_type") == "unknown"

    def test_homepage_selects_default_preset(self):
        """Homepage should auto-select default preset."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/")

        assert result.preset == "default"
        assert result.metadata.get("page_type") == "landing"


class TestPipelinePlannerPresetLoading:
    """Test that PipelinePlanner correctly loads presets."""

    def test_loads_preset_config_for_pricing(self):
        """Should load full preset configuration for pricing pages."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing")

        # pricing_table_v1 has specific selectors
        assert result.extractor.selectors is not None
        assert "#pricing" in result.extractor.selectors or ".pricing" in result.extractor.selectors

    def test_loads_preset_transport_config(self):
        """Should load transport config from preset."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing")

        # pricing_table_v1 has on_403: curl_cffi in the yaml
        assert result.transport.initial == "httpx"
        # on_403 varies by preset config

    def test_uses_custom_preset_registry(self):
        """Should use custom PresetRegistry if provided."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        custom_registry = PresetRegistry(presets_path=Path("/nonexistent/path.yaml"))
        custom_registry._presets = {
            "default": {
                "description": "Custom default",
                "extractor": {"preset": "custom_extractor"},
                "transport": {"initial": "custom_transport"},
            },
            "pricing_table_v1": {
                "description": "Custom pricing",
                "extractor": {"preset": "custom_pricing"},
                "transport": {"initial": "playwright"},
            },
        }
        custom_registry._loaded = True

        planner = PipelinePlanner(preset_registry=custom_registry)
        result = planner.plan("https://startup.io/pricing")

        assert result.extractor.preset == "custom_pricing"
        assert result.transport.initial == "playwright"


class TestPipelinePlannerMetadata:
    """Test metadata in returned WatchConfig."""

    def test_auto_selected_metadata_true_when_auto(self):
        """auto_selected should be True when config was auto-selected."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing")

        assert result.metadata["auto_selected"] is True

    def test_auto_selected_metadata_false_when_explicit(self):
        """auto_selected should be False when config was explicit."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        config_json = json.dumps({"preset": "spa"})
        result = planner.plan("https://startup.io/pricing", config_json=config_json)

        assert result.metadata["auto_selected"] is False

    def test_page_type_in_metadata(self):
        """page_type should be in metadata for auto-selected configs."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/careers")

        assert result.metadata["page_type"] == "careers"

    def test_preset_name_in_metadata(self):
        """preset_name should be in metadata for auto-selected configs."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/blog")

        assert result.metadata["preset_name"] == "blog_post_v1"

    def test_confidence_in_metadata(self):
        """Classification confidence should be in metadata."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing")

        assert "confidence" in result.metadata
        assert isinstance(result.metadata["confidence"], float)
        assert 0.0 <= result.metadata["confidence"] <= 1.0


class TestPipelinePlannerEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_url(self):
        """Empty URL should return default config."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("")

        assert isinstance(result, WatchConfig)
        # Empty URL maps to landing page
        assert result.metadata.get("page_type") in ["landing", "unknown"]

    def test_none_config_json(self):
        """None config_json should trigger auto-selection."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing", config_json=None)

        assert result.metadata["auto_selected"] is True
        assert result.preset == "pricing_table_v1"

    def test_empty_config_json(self):
        """Empty string config_json should trigger auto-selection."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing", config_json="")

        assert result.metadata["auto_selected"] is True

    def test_whitespace_only_config_json(self):
        """Whitespace-only config_json should trigger auto-selection."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://startup.io/pricing", config_json="   ")

        assert result.metadata["auto_selected"] is True

    def test_preset_not_found_falls_back_to_default_data(self):
        """If mapped preset doesn't exist, should use default preset data but keep preset name."""
        from monitoring.content_pipeline.planner import PipelinePlanner, PAGE_TYPE_PRESETS

        # Temporarily modify mapping to use non-existent preset
        original = PAGE_TYPE_PRESETS[PageType.PRICING]
        PAGE_TYPE_PRESETS[PageType.PRICING] = "nonexistent_preset_xyz"

        try:
            planner = PipelinePlanner()
            result = planner.plan("https://startup.io/pricing")

            # Preset name reflects the mapping, but data comes from default
            # (PresetRegistry.get() returns default data with a warning)
            assert result.preset == "nonexistent_preset_xyz"
            assert result.metadata["preset_name"] == "nonexistent_preset_xyz"
            # The actual config data should come from "default" preset
            # which has selectors like ["main", "article", "body"]
            assert result.extractor.preset == "default"
        finally:
            PAGE_TYPE_PRESETS[PageType.PRICING] = original


class TestPipelinePlannerIntegration:
    """Integration tests for full planning flow."""

    def test_full_planning_flow_pricing_page(self):
        """Full flow: URL -> classification -> preset -> config."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://acme.com/pricing")

        # Verify complete config structure
        assert isinstance(result, WatchConfig)
        assert isinstance(result.extractor, ExtractorConfig)
        assert isinstance(result.transport, TransportConfig)

        # Verify pricing-specific settings
        assert result.preset == "pricing_table_v1"
        assert result.metadata["auto_selected"] is True
        assert result.metadata["page_type"] == "pricing"

    def test_full_planning_flow_with_explicit_config(self):
        """Full flow with explicit config overriding auto-selection."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        explicit_config = json.dumps({
            "preset": "spa_hydration_v1",
            "extractor": {
                "preset": "spa",
                "selectors": ["#__NEXT_DATA__"],
                "fallback_on_empty": False,
            },
            "transport": {
                "initial": "playwright",
            },
        })

        # URL says pricing, but explicit config overrides
        result = planner.plan("https://acme.com/pricing", config_json=explicit_config)

        assert result.preset == "spa_hydration_v1"
        assert result.extractor.preset == "spa"
        assert result.transport.initial == "playwright"
        assert result.metadata["auto_selected"] is False

    def test_plan_multiple_urls_different_presets(self):
        """Different URLs should get different presets."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()

        pricing_config = planner.plan("https://acme.com/pricing")
        blog_config = planner.plan("https://acme.com/blog/announcement")
        careers_config = planner.plan("https://acme.com/careers")

        assert pricing_config.preset == "pricing_table_v1"
        assert blog_config.preset == "blog_post_v1"
        assert careers_config.preset == "default"

    def test_consistent_results_for_same_url(self):
        """Same URL should produce consistent results."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()

        result1 = planner.plan("https://acme.com/pricing")
        result2 = planner.plan("https://acme.com/pricing")

        assert result1.preset == result2.preset
        assert result1.metadata == result2.metadata


class TestWatchConfigMetadataExtension:
    """Test that WatchConfig properly supports metadata field."""

    def test_watchconfig_has_metadata_attribute(self):
        """WatchConfig should have a metadata attribute."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://example.com")

        assert hasattr(result, "metadata")
        assert isinstance(result.metadata, dict)

    def test_metadata_preserved_in_to_dict(self):
        """Metadata should be preserved in to_dict output."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://example.com/pricing")

        result_dict = result.to_dict()
        assert "metadata" in result_dict
        assert result_dict["metadata"]["auto_selected"] is True

    def test_metadata_preserved_in_to_json(self):
        """Metadata should be preserved in JSON serialization."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        planner = PipelinePlanner()
        result = planner.plan("https://example.com/pricing")

        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert "metadata" in parsed
        assert parsed["metadata"]["auto_selected"] is True
