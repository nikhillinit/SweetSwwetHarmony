"""Tests for consumer thesis configuration and classifier."""
from __future__ import annotations

import pytest
from pathlib import Path


class TestConsumerThesisConfig:
    """Tests for consumer thesis YAML configuration."""

    def test_consumer_thesis_file_exists(self):
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("consumer")
        assert config is not None
        assert config.vertical == "consumer"

    def test_consumer_has_brand_positioning_rule(self):
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("consumer")
        positive_signals = config.positive_signals
        assert "brand_positioning" in positive_signals

    def test_consumer_has_channel_strategy_rule(self):
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("consumer")
        positive_signals = config.positive_signals
        assert "channel_strategy" in positive_signals

    def test_consumer_has_category_rule(self):
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("consumer")
        positive_signals = config.positive_signals
        assert "product_category" in positive_signals

    def test_consumer_brand_positioning_has_keywords(self):
        from intelligence.thesis_config import load_thesis_config
        config = load_thesis_config("consumer")
        brand_keywords = config.positive_signals.get("brand_positioning", [])
        assert "premium" in brand_keywords or "luxury" in brand_keywords
