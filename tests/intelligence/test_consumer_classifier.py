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


class TestConsumerClassifier:
    """Tests for consumer classifier."""

    def test_classifier_initialization(self):
        from intelligence.consumer_classifier import ConsumerClassifier
        classifier = ConsumerClassifier()
        assert classifier is not None
        assert classifier.thesis_config is not None

    def test_classify_dtc_brand_high_score(self):
        from intelligence.consumer_classifier import ConsumerClassifier
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="PremiumBevCo",
            description="Premium DTC beverage brand for health-conscious millennials",
            signals={"source": "producthunt"}
        )
        assert result.fit_score >= 5  # Good score for matching multiple rules
        assert result.sub_vertical == "premium_consumer"

    def test_classify_marketplace(self):
        from intelligence.consumer_classifier import ConsumerClassifier
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="LocalMarket",
            description="Two-sided marketplace for local artisan goods",
            signals={}
        )
        assert result.sub_vertical == "consumer_platforms"
        assert "marketplace" in result.category

    def test_classify_community_commerce(self):
        from intelligence.consumer_classifier import ConsumerClassifier
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="CreatorShop",
            description="Community commerce platform for creator economy",
            signals={}
        )
        assert result.fit_score >= 1  # At least detected as consumer platform
        assert result.sub_vertical == "consumer_platforms"

    def test_brand_positioning_affects_score(self):
        from intelligence.consumer_classifier import ConsumerClassifier
        classifier = ConsumerClassifier()
        premium_result = classifier.classify(
            company_name="LuxuryBrand",
            description="Premium luxury skincare for affluent consumers",
            signals={}
        )
        mass_result = classifier.classify(
            company_name="ValueBrand",
            description="Affordable everyday products for everyone",
            signals={}
        )
        assert premium_result.fit_score >= mass_result.fit_score

    def test_dtc_channel_boosts_score(self):
        from intelligence.consumer_classifier import ConsumerClassifier
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="DTCBrand",
            description="Direct-to-consumer wellness brand with DTC-first strategy",
            signals={}
        )
        assert result.channel_strategy == "dtc"
        assert result.fit_score >= 3  # Matches channel and category
