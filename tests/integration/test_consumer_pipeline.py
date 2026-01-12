"""Integration tests for consumer pipeline."""
from __future__ import annotations

import pytest
from intelligence.domain_router import DomainRouter, Domain
from intelligence.consumer_classifier import ConsumerClassifier


class TestConsumerDomainRouting:
    """Integration tests for consumer domain routing."""

    def test_dtc_brand_routes_to_consumer(self):
        router = DomainRouter()
        result = router.detect_domain("Direct-to-consumer wellness brand")
        assert result.primary_domain == Domain.CONSUMER

    def test_marketplace_routes_to_consumer(self):
        router = DomainRouter()
        result = router.detect_domain("Two-sided marketplace for artisan goods")
        assert result.primary_domain == Domain.CONSUMER

    def test_premium_brand_routes_to_consumer(self):
        router = DomainRouter()
        result = router.detect_domain("Premium luxury skincare brand")
        assert result.primary_domain == Domain.CONSUMER


class TestConsumerClassification:
    """Integration tests for consumer classification."""

    def test_dtc_brand_classified_correctly(self):
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="PremiumBev",
            description="Premium DTC beverage brand for health-conscious millennials",
            signals={}
        )
        assert result.sub_vertical == "premium_consumer"
        assert result.fit_score >= 5

    def test_marketplace_classified_correctly(self):
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="LocalMarket",
            description="Two-sided marketplace connecting local artisans with buyers",
            signals={}
        )
        assert result.sub_vertical == "consumer_platforms"
        assert "marketplace" in result.category

    def test_community_commerce_classified_correctly(self):
        classifier = ConsumerClassifier()
        result = classifier.classify(
            company_name="CreatorShop",
            description="Community commerce platform for creator economy",
            signals={}
        )
        assert result.sub_vertical == "consumer_platforms"


class TestConsumerFullPipeline:
    """Integration tests for full consumer pipeline."""

    def test_signal_to_classification_flow_dtc(self):
        router = DomainRouter()
        classifier = ConsumerClassifier()

        signal_content = "Premium DTC wellness brand launching innovative supplements"

        domain_result = router.detect_domain(signal_content)
        assert domain_result.primary_domain == Domain.CONSUMER

        class_result = classifier.classify(
            company_name="WellnessCo",
            description=signal_content,
            signals={}
        )
        assert class_result.sub_vertical == "premium_consumer"
        assert class_result.fit_score >= 4

    def test_signal_to_classification_flow_marketplace(self):
        router = DomainRouter()
        classifier = ConsumerClassifier()

        signal_content = "Community-driven marketplace for sustainable fashion"

        domain_result = router.detect_domain(signal_content)
        assert domain_result.primary_domain == Domain.CONSUMER

        class_result = classifier.classify(
            company_name="SustainMarket",
            description=signal_content,
            signals={}
        )
        assert class_result.sub_vertical == "consumer_platforms"

    def test_cpg_startup_full_flow(self):
        router = DomainRouter()
        classifier = ConsumerClassifier()

        signal_content = "CPG startup disrupting the beverage industry with artisan craft drinks"

        domain_result = router.detect_domain(signal_content)
        assert domain_result.primary_domain == Domain.CONSUMER
        assert domain_result.confidence >= 0.7

        class_result = classifier.classify(
            company_name="CraftDrinks",
            description=signal_content,
            signals={}
        )
        assert class_result.category == "consumer_beverage"
        assert class_result.brand_positioning == "premium"
