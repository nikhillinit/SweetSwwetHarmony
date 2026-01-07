"""Tests for Crunchbase collector."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from collectors.crunchbase import (
    CrunchbaseCollector,
    CrunchbaseCompany,
    FundingRound,
)


class TestCrunchbaseCompany:
    """Tests for CrunchbaseCompany data class."""

    def test_calculate_signal_score_base(self):
        """Base score should be 0.5 for any company."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
        )
        score = company.calculate_signal_score()
        assert score >= 0.5

    def test_calculate_signal_score_recent_funding_boost(self):
        """Recent funding should significantly boost score."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            last_funding_at=datetime.now(timezone.utc) - timedelta(days=15),
        )
        score = company.calculate_signal_score()
        assert score >= 0.75  # 0.5 base + 0.25 recent funding

    def test_calculate_signal_score_seed_stage_boost(self):
        """Seed stage should get a boost."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            last_funding_type="seed",
        )
        score = company.calculate_signal_score()
        assert score >= 0.6  # 0.5 base + 0.1 seed

    def test_calculate_signal_score_small_team_boost(self):
        """Small teams should get a boost."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            num_employees_enum="c_11_50",
        )
        score = company.calculate_signal_score()
        assert score >= 0.6  # 0.5 base + 0.1 small team

    def test_calculate_signal_score_consumer_category_boost(self):
        """Consumer categories should get a boost."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            categories=["Food and Beverage", "Consumer Goods"],
        )
        score = company.calculate_signal_score()
        assert score >= 0.6  # 0.5 base + 0.1 consumer

    def test_calculate_signal_score_capped_at_1(self):
        """Score should never exceed 1.0."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            last_funding_at=datetime.now(timezone.utc) - timedelta(days=5),
            last_funding_type="seed",
            num_employees_enum="c_1_10",
            categories=["Consumer"],
        )
        score = company.calculate_signal_score()
        assert score <= 1.0

    def test_to_signal_with_website(self):
        """to_signal should use domain as canonical key when website available."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Acme Corp",
            permalink="acme-corp",
            website_url="https://www.acme.com",
        )
        signal = company.to_signal()

        assert signal.signal_type == "crunchbase_company"
        assert signal.source_api == "crunchbase"
        assert signal.raw_data["canonical_key"] == "domain:acme.com"
        assert signal.raw_data["company_name"] == "Acme Corp"

    def test_to_signal_without_website(self):
        """to_signal should use permalink as canonical key when no website."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Acme Corp",
            permalink="acme-corp",
        )
        signal = company.to_signal()

        assert signal.raw_data["canonical_key"] == "crunchbase:acme-corp"

    def test_to_signal_funding_type(self):
        """to_signal should return crunchbase_funding for recent funding."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Acme Corp",
            permalink="acme-corp",
            last_funding_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        signal = company.to_signal()

        assert signal.signal_type == "crunchbase_funding"

    def test_to_signal_includes_metadata(self):
        """to_signal should include relevant metadata."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            short_description="A great company",
            founded_on=datetime(2023, 1, 1, tzinfo=timezone.utc),
            num_employees_enum="c_11_50",
            total_funding_usd=1_500_000,
            last_funding_type="seed",
            categories=["Consumer"],
        )
        signal = company.to_signal()

        assert signal.raw_data["total_funding_usd"] == 1_500_000
        assert signal.raw_data["last_funding_type"] == "seed"
        assert signal.raw_data["num_employees"] == "c_11_50"

    def test_build_why_now_with_recent_funding(self):
        """_build_why_now should mention recent funding."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            last_funding_at=datetime.now(timezone.utc) - timedelta(days=10),
            last_funding_type="seed",
            total_funding_usd=2_000_000,
        )
        why_now = company._build_why_now()

        assert "Seed" in why_now
        assert "$2.0M" in why_now

    def test_build_why_now_with_early_team(self):
        """_build_why_now should mention small team."""
        company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
            num_employees_enum="c_1_10",
        )
        why_now = company._build_why_now()

        assert "1-10" in why_now


class TestCrunchbaseCollector:
    """Tests for CrunchbaseCollector."""

    def test_collector_requires_api_key(self):
        """Collector should warn when no API key provided."""
        with patch.dict("os.environ", {"CRUNCHBASE_API_KEY": ""}, clear=True):
            collector = CrunchbaseCollector()
            assert collector.api_key is None or collector.api_key == ""

    def test_collector_accepts_api_key_param(self):
        """Collector should accept API key as parameter."""
        collector = CrunchbaseCollector(api_key="test_key")
        assert collector.api_key == "test_key"

    def test_collector_reads_api_key_from_env(self):
        """Collector should read API key from environment."""
        with patch.dict("os.environ", {"CRUNCHBASE_API_KEY": "env_key"}):
            collector = CrunchbaseCollector()
            assert collector.api_key == "env_key"

    def test_collector_name(self):
        """Collector should have correct name."""
        collector = CrunchbaseCollector()
        assert collector.collector_name == "crunchbase"

    def test_collector_default_filters(self):
        """Collector should have sensible defaults."""
        collector = CrunchbaseCollector()
        assert collector.lookback_days == 30
        assert "seed" in collector.funding_stages
        assert "pre_seed" in collector.funding_stages
        assert "United States" in collector.locations

    def test_collector_accepts_custom_filters(self):
        """Collector should accept custom filter parameters."""
        collector = CrunchbaseCollector(
            lookback_days=60,
            funding_stages=["series_a"],
            categories=["Consumer"],
            locations=["Canada"],
            max_results=50,
        )
        assert collector.lookback_days == 60
        assert collector.funding_stages == ["series_a"]
        assert collector.categories == ["Consumer"]
        assert collector.locations == ["Canada"]
        assert collector.max_results == 50

    @pytest.mark.asyncio
    async def test_collect_signals_without_api_key_returns_empty(self):
        """collect_signals should return empty list without API key."""
        with patch.dict("os.environ", {"CRUNCHBASE_API_KEY": ""}, clear=True):
            collector = CrunchbaseCollector()
            async with collector:
                signals = await collector._collect_signals()
            assert signals == []

    @pytest.mark.asyncio
    async def test_collect_signals_calls_search(self):
        """collect_signals should search for recently funded companies."""
        collector = CrunchbaseCollector(api_key="test_key")

        mock_company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
        )

        with patch.object(
            collector, "_search_recently_funded", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = [mock_company]

            async with collector:
                signals = await collector._collect_signals()

            mock_search.assert_called_once()
            assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_collect_signals_handles_errors(self):
        """collect_signals should handle errors gracefully."""
        collector = CrunchbaseCollector(api_key="test_key")

        with patch.object(
            collector, "_search_recently_funded", new_callable=AsyncMock
        ) as mock_search:
            mock_search.side_effect = Exception("API error")

            async with collector:
                signals = await collector._collect_signals()

            # Should return empty but not raise
            assert signals == []

    @pytest.mark.asyncio
    async def test_run_returns_collector_result(self):
        """run() should return CollectorResult."""
        collector = CrunchbaseCollector(api_key="test_key")

        mock_company = CrunchbaseCompany(
            uuid="test-uuid",
            name="Test Co",
            permalink="test-co",
        )

        with patch.object(
            collector, "_search_recently_funded", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = [mock_company]

            result = await collector.run(dry_run=True)

            assert result.collector == "crunchbase"
            assert result.signals_found == 1

    def test_parse_company_basic(self):
        """_parse_company should parse basic company data."""
        collector = CrunchbaseCollector()
        entity = {
            "identifier": {
                "uuid": "abc123",
                "value": "Test Company",
                "permalink": "test-company",
            },
            "properties": {
                "short_description": "A test company",
                "website_url": "https://test.com",
                "num_employees_enum": "c_11_50",
            },
        }

        company = collector._parse_company(entity)

        assert company.uuid == "abc123"
        assert company.name == "Test Company"
        assert company.permalink == "test-company"
        assert company.short_description == "A test company"
        assert company.website_url == "https://test.com"

    def test_parse_company_with_funding(self):
        """_parse_company should parse funding data."""
        collector = CrunchbaseCollector()
        entity = {
            "identifier": {
                "uuid": "abc123",
                "value": "Test Company",
                "permalink": "test-company",
            },
            "properties": {
                "funding_total": {"value_usd": 5000000},
                "last_funding_at": "2024-01-15T00:00:00Z",
                "last_funding_type": "series_a",
            },
        }

        company = collector._parse_company(entity)

        assert company.total_funding_usd == 5000000
        assert company.last_funding_type == "series_a"
        assert company.last_funding_at is not None

    def test_parse_company_with_categories(self):
        """_parse_company should parse categories."""
        collector = CrunchbaseCollector()
        entity = {
            "identifier": {
                "uuid": "abc123",
                "value": "Test Company",
                "permalink": "test-company",
            },
            "properties": {
                "categories": [
                    {"value": "Consumer Goods"},
                    {"value": "E-Commerce"},
                ],
            },
        }

        company = collector._parse_company(entity)

        assert "Consumer Goods" in company.categories
        assert "E-Commerce" in company.categories

    def test_parse_company_handles_missing_data(self):
        """_parse_company should handle missing/null data gracefully."""
        collector = CrunchbaseCollector()
        entity = {
            "identifier": {
                "uuid": "abc123",
                "value": "Test Company",
                "permalink": "test-company",
            },
            "properties": {},
        }

        company = collector._parse_company(entity)

        assert company is not None
        assert company.uuid == "abc123"
        assert company.short_description == ""
        assert company.total_funding_usd is None
