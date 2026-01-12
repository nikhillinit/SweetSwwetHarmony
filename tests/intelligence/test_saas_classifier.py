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


class TestSaaSClassifier:
    """Tests for SaaS classifier."""

    def test_classifier_initialization(self):
        """SaaSClassifier should initialize correctly."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        assert classifier is not None
        assert classifier.thesis_config is not None

    def test_classify_returns_result(self):
        """classify() should return a SaaSClassificationResult."""
        from intelligence.saas_classifier import SaaSClassifier, SaaSClassificationResult
        classifier = SaaSClassifier()
        result = classifier.classify(
            company_name="TestCo",
            description="A B2B SaaS platform",
            signals={}
        )
        assert isinstance(result, SaaSClassificationResult)

    def test_classify_vertical_saas_high_score(self):
        """Vertical SaaS with PLG should score highly."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        result = classifier.classify(
            company_name="ConstructionFlow",
            description="Vertical SaaS platform for construction project management with product-led growth",
            signals={"source": "g2crowd", "funding": "series_a"}
        )
        assert result.fit_score >= 7
        assert result.category == "vertical_saas"

    def test_classify_developer_tools(self):
        """Developer tools platform should classify correctly."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        result = classifier.classify(
            company_name="DevAPI",
            description="API platform for developers with freemium model",
            signals={}
        )
        assert result.fit_score >= 5
        assert "api" in result.reasoning.lower() or "developer" in result.reasoning.lower()

    def test_classify_enterprise_software(self):
        """Enterprise software should classify correctly."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        result = classifier.classify(
            company_name="EnterpriseCo",
            description="Enterprise workflow automation for Fortune 500 companies",
            signals={"funding": "series_b"}
        )
        assert result.category in ["enterprise_saas", "workflow_automation"]

    def test_gtm_motion_affects_score(self):
        """GTM motion should affect the fit score."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        plg_result = classifier.classify(
            company_name="PLGCo",
            description="Product-led growth SaaS with freemium self-serve model",
            signals={}
        )
        sales_result = classifier.classify(
            company_name="SalesCo",
            description="Enterprise sales-led software solution",
            signals={}
        )
        # PLG should score higher or similar for Press On thesis
        assert plg_result.fit_score >= sales_result.fit_score - 2

    def test_result_has_all_fields(self):
        """Classification result should have all required fields."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        result = classifier.classify(
            company_name="TestCo",
            description="A SaaS platform",
            signals={}
        )
        assert hasattr(result, 'fit_score')
        assert hasattr(result, 'category')
        assert hasattr(result, 'reasoning')
        assert hasattr(result, 'gtm_motion')
        assert hasattr(result, 'target_market')
        assert hasattr(result, 'matched_rules')
        assert hasattr(result, 'confidence')

    def test_negative_signals_reduce_score(self):
        """Negative signals should reduce the fit score."""
        from intelligence.saas_classifier import SaaSClassifier
        classifier = SaaSClassifier()
        result = classifier.classify(
            company_name="LegacyCo",
            description="Legacy software consulting heavy services business on-premise only",
            signals={}
        )
        assert result.fit_score <= 5
