"""Tests for enrichment boost calculation."""

import pytest
from datetime import datetime, timezone, timedelta
from dataclasses import fields

from utils.signal_consolidator import ConsolidatedSignal


class TestEnrichmentBoostDataclass:
    """Test the EnrichmentBoost dataclass."""

    def test_enrichment_boost_has_required_fields(self):
        """EnrichmentBoost should have company_age_boost, social_proof_boost, total_boost."""
        from utils.enrichment_boost import EnrichmentBoost

        boost = EnrichmentBoost(
            company_age_boost=0.03,
            social_proof_boost=0.02,
            total_boost=0.05,
            company_age_days=730,
            social_proof_score=1500,
        )

        assert boost.company_age_boost == 0.03
        assert boost.social_proof_boost == 0.02
        assert boost.total_boost == 0.05
        assert boost.company_age_days == 730
        assert boost.social_proof_score == 1500

    def test_enrichment_boost_to_dict(self):
        """EnrichmentBoost.to_dict() should return a dictionary with all fields."""
        from utils.enrichment_boost import EnrichmentBoost

        boost = EnrichmentBoost(
            company_age_boost=0.03,
            social_proof_boost=0.02,
            total_boost=0.05,
            company_age_days=730,
            social_proof_score=1500,
        )

        result = boost.to_dict()

        assert isinstance(result, dict)
        assert result["company_age_boost"] == 0.03
        assert result["social_proof_boost"] == 0.02
        assert result["total_boost"] == 0.05
        assert result["company_age_days"] == 730
        assert result["social_proof_score"] == 1500


class TestEnrichmentConfigDataclass:
    """Test the EnrichmentConfig dataclass."""

    def test_enrichment_config_has_default_values(self):
        """EnrichmentConfig should have sensible defaults."""
        from utils.enrichment_boost import EnrichmentConfig

        config = EnrichmentConfig()

        assert config.age_high_threshold_days == 730  # 2 years
        assert config.age_medium_threshold_days == 365  # 1 year
        assert config.age_high_boost == 0.03
        assert config.age_medium_boost == 0.02
        assert config.stars_high_threshold == 1000
        assert config.stars_medium_threshold == 500
        assert config.upvotes_high_threshold == 200
        assert config.upvotes_medium_threshold == 100
        assert config.social_high_boost == 0.02
        assert config.social_medium_boost == 0.01
        assert config.max_total_boost == 0.05

    def test_enrichment_config_can_be_customized(self):
        """EnrichmentConfig can override defaults."""
        from utils.enrichment_boost import EnrichmentConfig

        config = EnrichmentConfig(
            age_high_threshold_days=1000,
            age_high_boost=0.05,
            max_total_boost=0.10,
        )

        assert config.age_high_threshold_days == 1000
        assert config.age_high_boost == 0.05
        assert config.max_total_boost == 0.10
        # Other values remain default
        assert config.age_medium_threshold_days == 365


class TestCompanyAgeBoost:
    """Test company age boost calculations."""

    def _make_consolidated_signal(
        self, founding_date: datetime = None, social_proof: dict = None
    ) -> ConsolidatedSignal:
        """Helper to create ConsolidatedSignal for tests."""
        now = datetime.now(timezone.utc)
        return ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding_date,
            social_proof=social_proof or {},
        )

    def test_company_over_2_years_gets_max_boost(self):
        """Company >= 730 days old should get max age boost (0.03)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=730)  # Exactly 2 years
        consolidated = self._make_consolidated_signal(founding_date=founding_date)

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.company_age_boost == 0.03
        assert result.company_age_days >= 730

    def test_company_over_3_years_gets_max_boost(self):
        """Company > 730 days old should still get max age boost (0.03)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=1095)  # 3 years
        consolidated = self._make_consolidated_signal(founding_date=founding_date)

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.company_age_boost == 0.03

    def test_company_over_1_year_gets_medium_boost(self):
        """Company >= 365 days and < 730 days should get medium boost (0.02)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=400)  # Between 1 and 2 years
        consolidated = self._make_consolidated_signal(founding_date=founding_date)

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.company_age_boost == 0.02
        assert 365 <= result.company_age_days < 730

    def test_company_exactly_1_year_gets_medium_boost(self):
        """Company exactly 365 days old should get medium boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=365)
        consolidated = self._make_consolidated_signal(founding_date=founding_date)

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.company_age_boost == 0.02

    def test_company_under_1_year_gets_no_boost(self):
        """Company < 365 days old should get no age boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=200)  # Less than 1 year
        consolidated = self._make_consolidated_signal(founding_date=founding_date)

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.company_age_boost == 0.0
        assert result.company_age_days < 365

    def test_no_founding_date_gets_no_boost(self):
        """No founding date should result in no age boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(founding_date=None)

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.company_age_boost == 0.0
        assert result.company_age_days == 0


class TestSocialProofBoost:
    """Test social proof boost calculations."""

    def _make_consolidated_signal(
        self, founding_date: datetime = None, social_proof: dict = None
    ) -> ConsolidatedSignal:
        """Helper to create ConsolidatedSignal for tests."""
        now = datetime.now(timezone.utc)
        return ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding_date,
            social_proof=social_proof or {},
        )

    def test_high_stars_gets_max_social_boost(self):
        """Stars >= 1000 should get max social boost (0.02)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"stars": 1000})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.02
        assert result.social_proof_score == 1000

    def test_very_high_stars_gets_max_social_boost(self):
        """Stars > 1000 should still get max social boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"stars": 5000})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.02

    def test_medium_stars_gets_medium_social_boost(self):
        """Stars >= 500 and < 1000 should get medium social boost (0.01)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"stars": 600})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.01
        assert result.social_proof_score == 600

    def test_low_stars_gets_no_social_boost(self):
        """Stars < 500 should get no social boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"stars": 200})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.0

    def test_high_upvotes_gets_max_social_boost(self):
        """Upvotes >= 200 should get max social boost (0.02)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"upvotes": 200})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.02

    def test_medium_upvotes_gets_medium_social_boost(self):
        """Upvotes >= 100 and < 200 should get medium social boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"upvotes": 150})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.01

    def test_low_upvotes_gets_no_social_boost(self):
        """Upvotes < 100 should get no social boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={"upvotes": 50})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.0

    def test_stars_and_upvotes_use_best_tier(self):
        """With both stars and upvotes, use the best tier."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        # Medium stars but high upvotes -> should get high boost
        consolidated = self._make_consolidated_signal(
            social_proof={"stars": 600, "upvotes": 250}
        )

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.02  # High tier from upvotes

    def test_no_social_proof_gets_no_boost(self):
        """No social proof should result in no social boost."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(social_proof={})

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        assert result.social_proof_boost == 0.0
        assert result.social_proof_score == 0

    def test_social_proof_score_uses_max_metric(self):
        """Social proof score should be the maximum of stars and upvotes."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        consolidated = self._make_consolidated_signal(
            social_proof={"stars": 800, "upvotes": 150}
        )

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        # Score should be max of 800 and 150
        assert result.social_proof_score == 800


class TestTotalBoostCapping:
    """Test that total boost is capped at max_total_boost."""

    def _make_consolidated_signal(
        self, founding_date: datetime = None, social_proof: dict = None
    ) -> ConsolidatedSignal:
        """Helper to create ConsolidatedSignal for tests."""
        now = datetime.now(timezone.utc)
        return ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.7,
            earliest_detected_at=now,
            latest_detected_at=now,
            founding_date=founding_date,
            social_proof=social_proof or {},
        )

    def test_total_boost_capped_at_max(self):
        """Total boost should be capped at max_total_boost (0.05)."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=800)  # 0.03 boost
        consolidated = self._make_consolidated_signal(
            founding_date=founding_date,
            social_proof={"stars": 1500},  # 0.02 boost
        )

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        # 0.03 + 0.02 = 0.05, exactly at cap
        assert result.company_age_boost == 0.03
        assert result.social_proof_boost == 0.02
        assert result.total_boost == 0.05

    def test_total_boost_respects_cap_when_exceeding(self):
        """Total boost should not exceed max even if sum of boosts would."""
        from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentConfig

        # Custom config with higher individual boosts
        config = EnrichmentConfig(
            age_high_boost=0.04,
            social_high_boost=0.03,
            max_total_boost=0.05,
        )

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=800)  # 0.04 boost with custom config
        consolidated = self._make_consolidated_signal(
            founding_date=founding_date,
            social_proof={"stars": 1500},  # 0.03 boost with custom config
        )

        calculator = EnrichmentBoostCalculator(config=config)
        result = calculator.calculate(consolidated)

        # 0.04 + 0.03 = 0.07, but cap is 0.05
        assert result.total_boost == 0.05

    def test_total_boost_below_cap_is_not_modified(self):
        """Total boost below cap should not be modified."""
        from utils.enrichment_boost import EnrichmentBoostCalculator

        now = datetime.now(timezone.utc)
        founding_date = now - timedelta(days=400)  # 0.02 boost
        consolidated = self._make_consolidated_signal(
            founding_date=founding_date,
            social_proof={"stars": 600},  # 0.01 boost
        )

        calculator = EnrichmentBoostCalculator()
        result = calculator.calculate(consolidated)

        # 0.02 + 0.01 = 0.03, below cap of 0.05
        assert result.company_age_boost == 0.02
        assert result.social_proof_boost == 0.01
        assert result.total_boost == 0.03


class TestEnrichmentBoostCalculatorInit:
    """Test EnrichmentBoostCalculator initialization."""

    def test_calculator_uses_default_config(self):
        """Calculator should use default config if none provided."""
        from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentConfig

        calculator = EnrichmentBoostCalculator()

        # Should have default config values
        assert calculator.config.age_high_threshold_days == 730
        assert calculator.config.max_total_boost == 0.05

    def test_calculator_uses_custom_config(self):
        """Calculator should use custom config if provided."""
        from utils.enrichment_boost import EnrichmentBoostCalculator, EnrichmentConfig

        custom_config = EnrichmentConfig(
            age_high_threshold_days=1000,
            max_total_boost=0.10,
        )

        calculator = EnrichmentBoostCalculator(config=custom_config)

        assert calculator.config.age_high_threshold_days == 1000
        assert calculator.config.max_total_boost == 0.10
