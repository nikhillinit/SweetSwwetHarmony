"""Tests for LinkedIn collector."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from collectors.linkedin import (
    LinkedInCollector,
    LinkedInCompany,
    LinkedInJobPosting,
)


class TestLinkedInCompany:
    """Tests for LinkedInCompany data class."""

    def test_calculate_signal_score_base(self):
        """Base score should be 0.5 for any company."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
        )
        score = company.calculate_signal_score()
        assert score >= 0.5

    def test_calculate_signal_score_early_stage_boost(self):
        """Small companies should get a boost."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
            company_size="11-50",
        )
        score = company.calculate_signal_score()
        assert score >= 0.6  # 0.5 base + 0.1 size boost

    def test_calculate_signal_score_recent_founding_boost(self):
        """Recently founded companies should get a boost."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
            founded_year=datetime.now().year - 1,  # 1 year old
        )
        score = company.calculate_signal_score()
        assert score >= 0.65  # 0.5 base + 0.15 recent founding

    def test_calculate_signal_score_consumer_industry_boost(self):
        """Consumer industries should get a boost."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
            industry="Food & Beverages",
        )
        score = company.calculate_signal_score()
        assert score >= 0.6  # 0.5 base + 0.1 industry

    def test_calculate_signal_score_capped_at_1(self):
        """Score should never exceed 1.0."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
            company_size="11-50",
            founded_year=datetime.now().year,
            industry="Food & Beverages",
            follower_count=10000,
        )
        score = company.calculate_signal_score()
        assert score <= 1.0

    def test_to_signal_with_website(self):
        """to_signal should use domain as canonical key when website available."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/acme",
            name="Acme Corp",
            website="https://www.acme.com",
        )
        signal = company.to_signal()

        assert signal.signal_type == "linkedin_company"
        assert signal.source_api == "linkedin"
        assert signal.raw_data["canonical_key"] == "domain:acme.com"
        assert signal.raw_data["company_name"] == "Acme Corp"

    def test_to_signal_without_website(self):
        """to_signal should use LinkedIn slug as canonical key when no website."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/acme-corp",
            name="Acme Corp",
        )
        signal = company.to_signal()

        assert signal.raw_data["canonical_key"] == "linkedin:acme-corp"

    def test_to_signal_includes_metadata(self):
        """to_signal should include relevant metadata."""
        company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
            description="A test company",
            industry="Technology",
            company_size="11-50",
            founded_year=2023,
            follower_count=1000,
        )
        signal = company.to_signal()

        assert signal.raw_data["industry"] == "Technology"
        assert signal.raw_data["company_size"] == "11-50"
        assert signal.raw_data["founded_year"] == 2023
        assert signal.raw_data["follower_count"] == 1000


class TestLinkedInJobPosting:
    """Tests for LinkedInJobPosting data class."""

    def test_to_signal_base_confidence(self):
        """Job posting should have base confidence of 0.65."""
        job = LinkedInJobPosting(
            job_url="https://linkedin.com/jobs/123",
            company_url="https://linkedin.com/company/test",
            company_name="Test Co",
            title="Software Engineer",
        )
        signal = job.to_signal()

        assert signal.confidence == 0.65

    def test_to_signal_leadership_boost(self):
        """Leadership roles should get a confidence boost."""
        job = LinkedInJobPosting(
            job_url="https://linkedin.com/jobs/123",
            company_url="https://linkedin.com/company/test",
            company_name="Test Co",
            title="Co-Founder & CTO",
        )
        signal = job.to_signal()

        assert signal.confidence >= 0.75  # 0.65 base + 0.1 leadership

    def test_to_signal_with_domain(self):
        """to_signal should use provided domain for canonical key."""
        job = LinkedInJobPosting(
            job_url="https://linkedin.com/jobs/123",
            company_url="https://linkedin.com/company/test",
            company_name="Test Co",
            title="Engineer",
        )
        signal = job.to_signal(company_domain="test.com")

        assert signal.raw_data["canonical_key"] == "domain:test.com"

    def test_to_signal_why_now(self):
        """to_signal should include job title in why_now."""
        job = LinkedInJobPosting(
            job_url="https://linkedin.com/jobs/123",
            company_url="https://linkedin.com/company/test",
            company_name="Test Co",
            title="VP of Product",
        )
        signal = job.to_signal()

        assert "VP of Product" in signal.raw_data["why_now"]


class TestLinkedInCollector:
    """Tests for LinkedInCollector."""

    def test_collector_requires_api_key(self):
        """Collector should warn when no API key provided."""
        # Clear env var if set
        with patch.dict("os.environ", {"PROXYCURL_API_KEY": ""}, clear=True):
            collector = LinkedInCollector()
            assert collector.api_key is None or collector.api_key == ""

    def test_collector_accepts_api_key_param(self):
        """Collector should accept API key as parameter."""
        collector = LinkedInCollector(api_key="test_key")
        assert collector.api_key == "test_key"

    def test_collector_reads_api_key_from_env(self):
        """Collector should read API key from environment."""
        with patch.dict("os.environ", {"PROXYCURL_API_KEY": "env_key"}):
            collector = LinkedInCollector()
            assert collector.api_key == "env_key"

    def test_collector_name(self):
        """Collector should have correct name."""
        collector = LinkedInCollector()
        assert collector.collector_name == "linkedin"

    @pytest.mark.asyncio
    async def test_collect_signals_without_api_key_returns_empty(self):
        """collect_signals should return empty list without API key."""
        with patch.dict("os.environ", {"PROXYCURL_API_KEY": ""}, clear=True):
            collector = LinkedInCollector()
            async with collector:
                signals = await collector._collect_signals()
            assert signals == []

    @pytest.mark.asyncio
    async def test_collect_signals_fetches_company_urls(self):
        """collect_signals should fetch companies by URL."""
        collector = LinkedInCollector(
            api_key="test_key",
            company_urls=["https://linkedin.com/company/test"],
        )

        mock_company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
        )

        with patch.object(collector, "_fetch_company", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_company

            async with collector:
                signals = await collector._collect_signals()

            mock_fetch.assert_called_once_with("https://linkedin.com/company/test")
            assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_collect_signals_resolves_domains(self):
        """collect_signals should resolve companies by domain."""
        collector = LinkedInCollector(
            api_key="test_key",
            company_domains=["acme.com"],
        )

        mock_company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/acme",
            name="Acme Corp",
            website="https://acme.com",
        )

        with patch.object(
            collector, "_resolve_company_by_domain", new_callable=AsyncMock
        ) as mock_resolve:
            mock_resolve.return_value = mock_company

            async with collector:
                signals = await collector._collect_signals()

            mock_resolve.assert_called_once_with("acme.com")
            assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_collect_signals_handles_fetch_errors(self):
        """collect_signals should handle errors gracefully."""
        collector = LinkedInCollector(
            api_key="test_key",
            company_urls=["https://linkedin.com/company/test"],
        )

        with patch.object(collector, "_fetch_company", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("API error")

            async with collector:
                signals = await collector._collect_signals()

            # Should return empty but not raise
            assert signals == []

    @pytest.mark.asyncio
    async def test_run_returns_collector_result(self):
        """run() should return CollectorResult."""
        collector = LinkedInCollector(
            api_key="test_key",
            company_urls=["https://linkedin.com/company/test"],
        )

        mock_company = LinkedInCompany(
            linkedin_url="https://linkedin.com/company/test",
            name="Test Co",
        )

        with patch.object(collector, "_fetch_company", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_company

            result = await collector.run(dry_run=True)

            assert result.collector == "linkedin"
            assert result.signals_found == 1

    def test_parse_company(self):
        """_parse_company should parse Proxycurl response correctly."""
        collector = LinkedInCollector()
        data = {
            "name": "Test Company",
            "description": "A great company",
            "website": "https://test.com",
            "industry": "Technology",
            "company_size": "11-50",
            "company_size_on_linkedin": 25,
            "founded_year": 2022,
            "specialties": ["AI", "ML"],
            "locations": [{"city": "SF"}],
            "follower_count": 500,
        }

        company = collector._parse_company(data, "https://linkedin.com/company/test")

        assert company.name == "Test Company"
        assert company.description == "A great company"
        assert company.website == "https://test.com"
        assert company.industry == "Technology"
        assert company.company_size == "11-50"
        assert company.founded_year == 2022
        assert company.follower_count == 500
