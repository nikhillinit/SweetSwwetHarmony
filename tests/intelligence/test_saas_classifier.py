"""Tests for SaaS classifier and thesis configuration."""
from __future__ import annotations

import pytest
from pathlib import Path


class TestSaaSThesisConfig:
    """Tests for SaaS thesis YAML configuration."""

    def test_saas_thesis_file_exists(self):
        """SaaS thesis config file should exist and load correctly."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        assert config is not None
        assert config.vertical == "saas"

    def test_saas_has_gtm_motion_signals(self):
        """SaaS thesis config should have GTM motion signals."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        signal_categories = list(config.positive_signals.keys())
        assert "gtm_motion" in signal_categories

    def test_saas_has_vertical_focus_signals(self):
        """SaaS thesis config should have vertical focus signals."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        signal_categories = list(config.positive_signals.keys())
        assert "vertical_focus" in signal_categories

    def test_saas_has_target_market_signals(self):
        """SaaS thesis config should have target market signals."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        signal_categories = list(config.positive_signals.keys())
        assert "target_market" in signal_categories

    def test_saas_gtm_motion_has_plg_keywords(self):
        """SaaS GTM motion signals should include PLG keywords."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        gtm_signals = config.positive_signals.get("gtm_motion", [])
        assert any("product-led" in s.lower() or "plg" in s.lower() for s in gtm_signals)

    def test_saas_has_scoring_weights(self):
        """SaaS thesis config should have scoring weights."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        assert len(config.scoring_weights) > 0
        # Weights should sum to approximately 1.0
        total_weight = sum(config.scoring_weights.values())
        assert 0.9 <= total_weight <= 1.1

    def test_saas_has_negative_signals(self):
        """SaaS thesis config should have negative signals."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        assert len(config.negative_signals) > 0

    def test_saas_has_stage_filters(self):
        """SaaS thesis config should have stage filters."""
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("saas")
        assert "included" in config.stage_filters
        assert "excluded" in config.stage_filters
