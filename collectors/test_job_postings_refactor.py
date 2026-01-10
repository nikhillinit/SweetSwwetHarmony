"""
Tests for JobPostingsCollector.

Covers:
- BaseCollector integration (inheritance, parameters)
- Deterministic serialization (to_dict stability)
- ATS platform detection (Greenhouse, Lever, Ashby, Workable)
- Signal scoring (ghost job dampener)
- Canonical key generation
- Change detection patterns
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# =============================================================================
# BASE COLLECTOR INTEGRATION TESTS
# =============================================================================

class TestJobPostingsInheritsBaseCollector:
    """Test JobPostingsCollector properly inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """JobPostingsCollector should inherit from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.base import BaseCollector

        assert issubclass(JobPostingsCollector, BaseCollector)

    def test_has_api_name_for_rate_limiting(self):
        """JobPostingsCollector should set api_name for rate limiting"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=["example.com"])
        assert collector.api_name == "job_postings"

    def test_has_retry_config(self):
        """JobPostingsCollector should have retry_config from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.retry_strategy import RetryConfig

        collector = JobPostingsCollector(domains=["example.com"])
        assert hasattr(collector, "retry_config")
        assert isinstance(collector.retry_config, RetryConfig)

    def test_accepts_asset_store_parameter(self):
        """JobPostingsCollector should accept asset_store parameter"""
        from collectors.job_postings import JobPostingsCollector

        mock_asset_store = MagicMock()
        collector = JobPostingsCollector(
            domains=["example.com"],
            asset_store=mock_asset_store,
        )
        assert collector.asset_store is mock_asset_store

    def test_accepts_store_parameter(self):
        """JobPostingsCollector should accept store parameter"""
        from collectors.job_postings import JobPostingsCollector

        mock_store = MagicMock()
        collector = JobPostingsCollector(
            domains=["example.com"],
            store=mock_store,
        )
        assert collector.store is mock_store

    def test_has_rate_limiter(self):
        """JobPostingsCollector should have rate_limiter from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=["example.com"])
        assert hasattr(collector, "rate_limiter")


# =============================================================================
# DETERMINISTIC SERIALIZATION TESTS
# =============================================================================

class TestJobPostingSignalSerialization:
    """Test JobPostingSignal.to_dict() produces deterministic output"""

    def test_to_dict_sorts_lists(self):
        """to_dict should sort all lists for deterministic output"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test Co",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            sample_titles=["Zebra Role", "Alpha Role", "Beta Role"],
            departments=["Sales", "Engineering", "Product"],
            locations=["NYC", "Austin", "London"],
        )

        result = signal.to_dict()

        # All lists should be sorted
        assert result["sample_titles"] == ["Alpha Role", "Beta Role", "Zebra Role"]
        assert result["departments"] == ["Engineering", "Product", "Sales"]
        assert result["locations"] == ["Austin", "London", "NYC"]

    def test_to_dict_normalizes_domain(self):
        """to_dict should normalize domain (lowercase, no www)"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="WWW.EXAMPLE.COM",
            ats_platform="lever",
            total_positions=1,
            engineering_positions=0,
        )

        result = signal.to_dict()
        assert result["company_domain"] == "example.com"

    def test_to_dict_datetime_iso_format(self):
        """to_dict should serialize datetimes as ISO format strings"""
        from collectors.job_postings import JobPostingSignal

        dt = datetime(2026, 1, 10, 12, 30, 45, tzinfo=timezone.utc)
        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="ashby",
            total_positions=1,
            engineering_positions=0,
            oldest_posting_at=dt,
            newest_posting_at=dt,
        )

        result = signal.to_dict()
        assert result["oldest_posting_at"] == "2026-01-10T12:30:45+00:00"
        assert result["newest_posting_at"] == "2026-01-10T12:30:45+00:00"

    def test_to_dict_deterministic_hash(self):
        """Same input should always produce same hash"""
        from collectors.job_postings import JobPostingSignal

        signal1 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
            sample_titles=["C", "A", "B"],
            locations=["Z", "Y", "X"],
        )

        signal2 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
            sample_titles=["B", "C", "A"],  # Different order
            locations=["X", "Z", "Y"],  # Different order
        )

        dict1 = signal1.to_dict()
        dict2 = signal2.to_dict()

        hash1 = hashlib.sha256(json.dumps(dict1, sort_keys=True).encode()).hexdigest()
        hash2 = hashlib.sha256(json.dumps(dict2, sort_keys=True).encode()).hexdigest()

        assert hash1 == hash2

    def test_to_dict_includes_oldest_posting_age(self):
        """to_dict should include oldest_posting_age_days for ghost job detection"""
        from collectors.job_postings import JobPostingSignal

        old_date = datetime.now(timezone.utc) - timedelta(days=100)
        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="workable",
            total_positions=1,
            engineering_positions=0,
            oldest_posting_at=old_date,
        )

        result = signal.to_dict()
        assert "oldest_posting_age_days" in result
        assert result["oldest_posting_age_days"] >= 99  # Allow for test timing


# =============================================================================
# SIGNAL SCORING TESTS
# =============================================================================

class TestJobPostingSignalScoring:
    """Test signal confidence scoring logic"""

    def test_base_score_is_high(self):
        """Hiring signal base score should be 0.7"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=1,
            engineering_positions=0,
        )

        score = signal.calculate_signal_score()
        assert score >= 0.7

    def test_boost_for_many_positions(self):
        """Score should increase with position count"""
        from collectors.job_postings import JobPostingSignal

        signal_1 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=1,
            engineering_positions=0,
        )

        signal_10 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=10,
            engineering_positions=0,
        )

        assert signal_10.calculate_signal_score() > signal_1.calculate_signal_score()

    def test_boost_for_engineering_heavy(self):
        """Score should increase for engineering-heavy hiring"""
        from collectors.job_postings import JobPostingSignal

        signal_sales = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=10,
            engineering_positions=0,
        )

        signal_eng = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=10,
            engineering_positions=8,  # 80% engineering
        )

        assert signal_eng.calculate_signal_score() > signal_sales.calculate_signal_score()

    def test_ghost_job_dampener_90_days(self):
        """Postings older than 90 days should be penalized"""
        from collectors.job_postings import JobPostingSignal

        recent_date = datetime.now(timezone.utc) - timedelta(days=30)
        old_date = datetime.now(timezone.utc) - timedelta(days=100)

        signal_recent = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            oldest_posting_at=recent_date,
        )

        signal_old = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            oldest_posting_at=old_date,
        )

        assert signal_old.calculate_signal_score() < signal_recent.calculate_signal_score()

    def test_ghost_job_dampener_180_days_severe(self):
        """Postings older than 180 days should have severe penalty"""
        from collectors.job_postings import JobPostingSignal

        ancient_date = datetime.now(timezone.utc) - timedelta(days=200)

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            oldest_posting_at=ancient_date,
        )

        score = signal.calculate_signal_score()
        # Should be significantly below the base score of 0.7
        assert score < 0.6

    def test_score_capped_at_1(self):
        """Score should never exceed 1.0"""
        from collectors.job_postings import JobPostingSignal

        # Maximize all boosts
        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=100,
            engineering_positions=100,
            oldest_posting_at=datetime.now(timezone.utc),
        )

        score = signal.calculate_signal_score()
        assert score <= 1.0


# =============================================================================
# CANONICAL KEY TESTS
# =============================================================================

class TestCanonicalKeyGeneration:
    """Test canonical key generation for entity resolution"""

    def test_to_signal_includes_canonical_key(self):
        """Signal should include canonical_key in raw_data"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test Co",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
        )

        result = signal.to_signal()
        assert "canonical_key" in result.raw_data
        assert result.raw_data["canonical_key"] == "domain:test.com"

    def test_to_signal_includes_canonical_key_candidates(self):
        """Signal should include canonical_key_candidates list"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test Company",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
            raw_snapshot={"board_id": "testcompany"},
        )

        result = signal.to_signal()
        assert "canonical_key_candidates" in result.raw_data
        candidates = result.raw_data["canonical_key_candidates"]

        assert "domain:test.com" in candidates
        assert "company_name:test company" in candidates
        assert "ats_board:greenhouse:testcompany" in candidates


# =============================================================================
# BOARD ID GENERATION TESTS
# =============================================================================

class TestBoardIdGeneration:
    """Test board ID candidate generation from domains"""

    def test_basic_domain(self):
        """Should extract base name from simple domain"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("example.com")

        assert "example" in ids
        assert "examplehq" in ids
        assert "example-careers" in ids

    def test_hyphenated_domain(self):
        """Should handle hyphenated names"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("jacob-bar.com")

        assert "jacob-bar" in ids
        assert "jacobbar" in ids
        assert "jacob" in ids

    def test_underscored_domain(self):
        """Should handle underscored names"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("my_company.com")

        assert "my_company" in ids
        assert "mycompany" in ids
        assert "my" in ids

    def test_deduplicates_candidates(self):
        """Should not return duplicate board IDs"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("test.com")

        assert len(ids) == len(set(ids))


# =============================================================================
# DATETIME PARSING TESTS
# =============================================================================

class TestDatetimeParsing:
    """Test datetime parsing from various ATS formats"""

    def test_parse_iso_string(self):
        """Should parse ISO 8601 strings"""
        from collectors.job_postings import _parse_dt

        result = _parse_dt("2026-01-10T12:30:45Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 10

    def test_parse_iso_with_offset(self):
        """Should parse ISO strings with timezone offset"""
        from collectors.job_postings import _parse_dt

        result = _parse_dt("2026-01-10T12:30:45+00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_parse_epoch_seconds(self):
        """Should parse epoch seconds"""
        from collectors.job_postings import _parse_dt

        # 2026-01-10 12:30:45 UTC
        epoch = 1768062645
        result = _parse_dt(epoch)

        assert result is not None
        assert result.year == 2026

    def test_parse_epoch_milliseconds(self):
        """Should parse epoch milliseconds"""
        from collectors.job_postings import _parse_dt

        epoch_ms = 1768062645000
        result = _parse_dt(epoch_ms)

        assert result is not None
        assert result.year == 2026

    def test_parse_none_returns_none(self):
        """Should return None for None input"""
        from collectors.job_postings import _parse_dt

        assert _parse_dt(None) is None

    def test_parse_empty_string_returns_none(self):
        """Should return None for empty string"""
        from collectors.job_postings import _parse_dt

        assert _parse_dt("") is None
        assert _parse_dt("   ") is None


# =============================================================================
# COLLECTOR INTEGRATION TESTS
# =============================================================================

class TestJobPostingsCollectorIntegration:
    """Test full collector flow with mocked HTTP responses"""

    @pytest.mark.asyncio
    async def test_collect_signals_returns_list(self):
        """_collect_signals should return list of Signal objects"""
        from collectors.job_postings import JobPostingsCollector

        with patch.object(
            JobPostingsCollector, "check_domain", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = None

            collector = JobPostingsCollector(domains=["example.com"])
            signals = await collector._collect_signals()

            assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_check_domain_tries_all_platforms(self):
        """check_domain should try all ATS platforms"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_check_greenhouse", new_callable=AsyncMock, return_value=None
        ) as mock_gh, patch.object(
            collector, "_check_ashby", new_callable=AsyncMock, return_value=None
        ) as mock_ashby, patch.object(
            collector, "_check_lever", new_callable=AsyncMock, return_value=None
        ) as mock_lever, patch.object(
            collector, "_check_workable", new_callable=AsyncMock, return_value=None
        ) as mock_workable:

            await collector.check_domain("example.com")

            # Should have tried all platforms
            assert mock_gh.called
            assert mock_ashby.called
            assert mock_lever.called
            assert mock_workable.called

    @pytest.mark.asyncio
    async def test_check_domain_returns_first_hit(self):
        """check_domain should return first successful platform"""
        from collectors.job_postings import JobPostingsCollector, JobPostingSignal

        collector = JobPostingsCollector(domains=[])

        mock_signal = JobPostingSignal(
            company_name="Test",
            company_domain="example.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
        )

        with patch.object(
            collector, "_check_greenhouse", new_callable=AsyncMock, return_value=mock_signal
        ):
            result = await collector.check_domain("example.com")

            assert result is not None
            assert result.ats_platform == "greenhouse"


# =============================================================================
# GREENHOUSE API TESTS
# =============================================================================

class TestGreenhouseIntegration:
    """Test Greenhouse API integration"""

    @pytest.mark.asyncio
    async def test_greenhouse_parses_jobs(self):
        """Should parse Greenhouse API response correctly"""
        from collectors.job_postings import JobPostingsCollector

        mock_response = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Senior Software Engineer",
                    "location": {"name": "San Francisco, CA"},
                    "departments": [{"name": "Engineering"}],
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/123",
                    "updated_at": "2026-01-05T10:00:00Z",
                },
                {
                    "id": 124,
                    "title": "Product Manager",
                    "location": {"name": "Remote"},
                    "departments": [{"name": "Product"}],
                    "absolute_url": "https://boards.greenhouse.io/test/jobs/124",
                    "updated_at": "2026-01-08T10:00:00Z",
                },
            ]
        }

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_http_get", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await collector._check_greenhouse("testcompany", "test.com")

            assert result is not None
            assert result.total_positions == 2
            assert result.engineering_positions == 1
            assert "Senior Software Engineer" in result.sample_titles
            assert "Engineering" in result.departments

    @pytest.mark.asyncio
    async def test_greenhouse_handles_404(self):
        """Should return None for non-existent boards"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector,
            "_http_get",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            ),
        ):
            result = await collector._check_greenhouse("nonexistent", "test.com")
            assert result is None


# =============================================================================
# LEVER API TESTS
# =============================================================================

class TestLeverIntegration:
    """Test Lever API integration"""

    @pytest.mark.asyncio
    async def test_lever_parses_jobs(self):
        """Should parse Lever API response correctly"""
        from collectors.job_postings import JobPostingsCollector

        mock_response = [
            {
                "id": "abc123",
                "text": "Backend Engineer",
                "categories": {"location": "NYC", "department": "Engineering"},
                "hostedUrl": "https://jobs.lever.co/test/abc123",
                "createdAt": 1768000000000,
            },
        ]

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_http_get", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await collector._check_lever("testcompany", "test.com")

            assert result is not None
            assert result.total_positions == 1
            assert result.ats_platform == "lever"


# =============================================================================
# ASHBY API TESTS
# =============================================================================

class TestAshbyIntegration:
    """Test Ashby API integration"""

    @pytest.mark.asyncio
    async def test_ashby_parses_jobs(self):
        """Should parse Ashby API response correctly"""
        from collectors.job_postings import JobPostingsCollector

        mock_response = {
            "jobs": [
                {
                    "id": "job-1",
                    "title": "Full Stack Developer",
                    "location": "Remote",
                    "department": "Engineering",
                    "jobUrl": "https://jobs.ashbyhq.com/test/job-1",
                    "publishedAt": "2026-01-05T10:00:00Z",
                },
            ]
        }

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_http_get", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await collector._check_ashby("testcompany", "test.com")

            assert result is not None
            assert result.ats_platform == "ashby"
            assert result.total_positions == 1


# =============================================================================
# WORKABLE API TESTS
# =============================================================================

class TestWorkableIntegration:
    """Test Workable HTML parsing"""

    @pytest.mark.asyncio
    async def test_workable_detects_jobs(self):
        """Should detect jobs from Workable careers page HTML"""
        from collectors.job_postings import JobPostingsCollector

        mock_html = '''
        <html>
        <body>
            <div data-ui="job-opening">
                <h3 class="job-title">Marketing Manager</h3>
            </div>
            <div data-ui="job-opening">
                <h3 class="job-title">Software Engineer</h3>
            </div>
        </body>
        </html>
        '''

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_fetch_with_retry", new_callable=AsyncMock, return_value=mock_html
        ):
            result = await collector._check_workable("testcompany", "test.com")

            assert result is not None
            assert result.ats_platform == "workable"
            assert result.total_positions >= 2

    @pytest.mark.asyncio
    async def test_workable_returns_none_for_no_jobs(self):
        """Should return None if no job indicators found"""
        from collectors.job_postings import JobPostingsCollector

        mock_html = "<html><body><p>About us page</p></body></html>"

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_fetch_with_retry", new_callable=AsyncMock, return_value=mock_html
        ):
            result = await collector._check_workable("testcompany", "test.com")
            assert result is None


# =============================================================================
# SIGNAL OUTPUT TESTS
# =============================================================================

class TestSignalOutput:
    """Test final Signal object structure"""

    def test_signal_has_required_fields(self):
        """Signal should have all required verification gate fields"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test Co",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            sample_titles=["Engineer", "PM"],
            job_url="https://jobs.greenhouse.io/test",
        )

        result = signal.to_signal()

        assert result.id is not None
        assert result.signal_type == "hiring_signal"
        assert 0 <= result.confidence <= 1
        assert result.source_api == "greenhouse_jobs"
        assert result.source_url == "https://jobs.greenhouse.io/test"
        assert result.source_response_hash is not None
        assert result.verification_status is not None
        assert "canonical_key" in result.raw_data
        assert "canonical_key_candidates" in result.raw_data

    def test_signal_id_is_deterministic(self):
        """Same input should produce same signal ID"""
        from collectors.job_postings import JobPostingSignal

        signal1 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
        )

        signal2 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
        )

        assert signal1.to_signal().id == signal2.to_signal().id


# =============================================================================
# RUN PYTEST
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
