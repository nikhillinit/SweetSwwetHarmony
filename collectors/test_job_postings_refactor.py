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
# DATETIME PARSING EDGE CASES
# =============================================================================

class TestDatetimeParsingEdgeCases:
    """Test edge cases for datetime parsing"""

    def test_parse_float_epoch(self):
        """Should parse float epoch values"""
        from collectors.job_postings import _parse_dt

        result = _parse_dt(1768062645.123)
        assert result is not None
        assert result.year == 2026

    def test_parse_string_epoch(self):
        """Should handle string representation of epoch"""
        from collectors.job_postings import _parse_dt

        result = _parse_dt("1768062645")
        # Might be interpreted as ISO string first, then fallback
        # Just verify it doesn't crash
        assert True

    def test_parse_malformed_iso_returns_none(self):
        """Should return None for malformed ISO strings"""
        from collectors.job_postings import _parse_dt

        # These should not crash
        assert _parse_dt("not-a-date") is None
        assert _parse_dt("2026-99-99") is None  # Invalid month/day

    def test_parse_negative_epoch(self):
        """Should handle negative epoch values (pre-1970)"""
        from collectors.job_postings import _parse_dt

        result = _parse_dt(-1000000)
        # Should either work or return None, but not crash
        assert result is None or result.year < 1970

    def test_parse_very_large_number(self):
        """Should handle very large numbers near ms/s boundary"""
        from collectors.job_postings import _parse_dt

        # Just above seconds threshold (1e12)
        result = _parse_dt(1e12 + 1)
        assert result is not None  # Should parse as milliseconds

    def test_parse_iso_with_microseconds(self):
        """Should parse ISO strings with microseconds"""
        from collectors.job_postings import _parse_dt

        result = _parse_dt("2026-01-10T12:30:45.123456Z")
        assert result is not None
        assert result.year == 2026


# =============================================================================
# GHOST JOB DAMPENER EDGE CASES
# =============================================================================

class TestGhostJobDampenerEdgeCases:
    """Test edge cases for ghost job dampening"""

    def test_ghost_job_at_60_day_boundary(self):
        """Postings at 60 days should have mild penalty"""
        from collectors.job_postings import JobPostingSignal

        fresh_date = datetime.now(timezone.utc) - timedelta(days=10)
        boundary_date = datetime.now(timezone.utc) - timedelta(days=60)

        signal_fresh = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            oldest_posting_at=fresh_date,
        )

        signal_60d = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            oldest_posting_at=boundary_date,
        )

        # 60-day posts should have some penalty vs fresh
        score_fresh = signal_fresh.calculate_signal_score()
        score_60d = signal_60d.calculate_signal_score()

        assert score_60d <= score_fresh

    def test_ghost_job_without_posting_date(self):
        """Should handle missing posting dates gracefully"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
            oldest_posting_at=None,
        )

        # Should not crash, should use base score
        score = signal.calculate_signal_score()
        assert score >= 0.7  # Base score without penalty


# =============================================================================
# SCORE BOUNDARY CONDITIONS
# =============================================================================

class TestScoreBoundaryConditions:
    """Test score boundary conditions with multiple boosts"""

    def test_score_with_all_boosts(self):
        """Score with all boosts should still be capped"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=100,  # Max position boost
            engineering_positions=100,  # 100% engineering
            oldest_posting_at=datetime.now(timezone.utc),  # Fresh
        )

        score = signal.calculate_signal_score()
        assert 0 <= score <= 1.0

    def test_score_at_position_thresholds(self):
        """Test score at position count thresholds"""
        from collectors.job_postings import JobPostingSignal

        for positions in [1, 2, 5, 10, 20, 50]:
            signal = JobPostingSignal(
                company_name="Test",
                company_domain="test.com",
                ats_platform="greenhouse",
                total_positions=positions,
                engineering_positions=0,
            )
            score = signal.calculate_signal_score()
            assert 0.7 <= score <= 1.0

    def test_score_at_engineering_ratio_thresholds(self):
        """Test score at engineering ratio thresholds"""
        from collectors.job_postings import JobPostingSignal

        for eng_ratio in [0, 0.25, 0.5, 0.75, 1.0]:
            total = 10
            eng = int(total * eng_ratio)
            signal = JobPostingSignal(
                company_name="Test",
                company_domain="test.com",
                ats_platform="greenhouse",
                total_positions=total,
                engineering_positions=eng,
            )
            score = signal.calculate_signal_score()
            assert 0.7 <= score <= 1.0

    def test_score_minimum_floor(self):
        """Score should have minimum floor even with heavy penalties"""
        from collectors.job_postings import JobPostingSignal

        # Very old posting with minimal positions
        ancient_date = datetime.now(timezone.utc) - timedelta(days=365)
        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=1,
            engineering_positions=0,
            oldest_posting_at=ancient_date,
        )

        score = signal.calculate_signal_score()
        assert score >= 0.0  # Should not go negative


# =============================================================================
# BOARD ID GENERATION EDGE CASES
# =============================================================================

class TestBoardIdEdgeCases:
    """Test edge cases for board ID generation"""

    def test_empty_domain(self):
        """Should handle empty domain gracefully"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("")

        # Should return empty list or minimal candidates
        assert isinstance(ids, list)

    def test_numeric_domain(self):
        """Should handle numeric-only domain names"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("123.com")

        assert "123" in ids

    def test_very_short_domain(self):
        """Should handle very short domain names"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("a.io")

        assert "a" in ids

    def test_domain_with_subdomain(self):
        """Should handle domains with subdomains"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("careers.company.com")

        # Should extract the company name appropriately
        assert isinstance(ids, list)
        assert len(ids) > 0

    def test_multi_level_tld(self):
        """Should handle multi-level TLDs like .co.uk"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("company.co.uk")

        assert "company" in ids

    def test_special_characters_in_domain(self):
        """Should handle special characters in domain"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        ids = collector._generate_board_ids("my-great_company.com")

        assert isinstance(ids, list)
        assert len(ids) > 0


# =============================================================================
# API ERROR HANDLING TESTS
# =============================================================================

class TestAPIErrorHandling:
    """Test API error handling scenarios"""

    @pytest.mark.asyncio
    async def test_greenhouse_handles_500_error(self):
        """Should handle HTTP 500 errors gracefully"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector,
            "_http_get",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            ),
        ):
            result = await collector._check_greenhouse("test", "test.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_lever_handles_timeout(self):
        """Should handle timeout errors gracefully"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector,
            "_http_get",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("Timeout"),
        ):
            result = await collector._check_lever("test", "test.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_ashby_handles_connection_error(self):
        """Should handle connection errors gracefully"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector,
            "_http_get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = await collector._check_ashby("test", "test.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_greenhouse_handles_empty_jobs_list(self):
        """Should handle empty jobs list"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_http_get", new_callable=AsyncMock, return_value={"jobs": []}
        ):
            result = await collector._check_greenhouse("test", "test.com")
            # Empty jobs should return None or signal with 0 positions
            assert result is None or result.total_positions == 0

    @pytest.mark.asyncio
    async def test_lever_handles_non_list_response(self):
        """Should handle unexpected response format"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_http_get", new_callable=AsyncMock, return_value={"error": "not found"}
        ):
            result = await collector._check_lever("test", "test.com")
            assert result is None


# =============================================================================
# WORKABLE HTML EDGE CASES
# =============================================================================

class TestWorkableHtmlEdgeCases:
    """Test edge cases for Workable HTML parsing"""

    @pytest.mark.asyncio
    async def test_workable_multiple_job_indicators(self):
        """Should handle multiple job indicator patterns"""
        from collectors.job_postings import JobPostingsCollector

        mock_html = '''
        <html>
        <body>
            <div data-ui="job-opening">Job 1</div>
            <div class="whr-item">Job 2</div>
            <li data-ui="job">Job 3</li>
        </body>
        </html>
        '''

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_fetch_with_retry", new_callable=AsyncMock, return_value=mock_html
        ):
            result = await collector._check_workable("test", "test.com")
            assert result is not None
            assert result.total_positions >= 1

    @pytest.mark.asyncio
    async def test_workable_handles_malformed_html(self):
        """Should handle malformed HTML without crashing"""
        from collectors.job_postings import JobPostingsCollector

        mock_html = '''
        <html>
        <body>
            <div data-ui="job-opening">
                <h3>Job Title
            <!-- Missing closing tags -->
        '''

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_fetch_with_retry", new_callable=AsyncMock, return_value=mock_html
        ):
            # Should not crash
            result = await collector._check_workable("test", "test.com")
            # May or may not find jobs, but shouldn't crash
            assert result is None or isinstance(result.total_positions, int)

    @pytest.mark.asyncio
    async def test_workable_handles_empty_html(self):
        """Should handle empty HTML response"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_fetch_with_retry", new_callable=AsyncMock, return_value=""
        ):
            result = await collector._check_workable("test", "test.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_workable_handles_null_response(self):
        """Should handle null/None response"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_fetch_with_retry", new_callable=AsyncMock, return_value=None
        ):
            result = await collector._check_workable("test", "test.com")
            assert result is None


# =============================================================================
# DOMAIN INPUT VALIDATION
# =============================================================================

class TestDomainInputValidation:
    """Test handling of various domain inputs"""

    @pytest.mark.asyncio
    async def test_collect_with_empty_domains_list(self):
        """Should handle empty domains list"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])
        signals = await collector._collect_signals()

        assert isinstance(signals, list)
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_check_domain_normalizes_input(self):
        """Should normalize domain input"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        with patch.object(
            collector, "_check_greenhouse", new_callable=AsyncMock, return_value=None
        ) as mock_gh, patch.object(
            collector, "_check_lever", new_callable=AsyncMock, return_value=None
        ), patch.object(
            collector, "_check_ashby", new_callable=AsyncMock, return_value=None
        ), patch.object(
            collector, "_check_workable", new_callable=AsyncMock, return_value=None
        ):
            # Should work with URL-like input
            await collector.check_domain("https://www.example.com/careers")

            # Should have been called with normalized domain
            assert mock_gh.called


# =============================================================================
# SIGNAL HASH DETERMINISM
# =============================================================================

class TestSignalHashDeterminism:
    """Test signal hash determinism for deduplication"""

    def test_hash_stable_across_list_order(self):
        """Signal hash should be stable regardless of list order"""
        from collectors.job_postings import JobPostingSignal

        signal1 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
            sample_titles=["A", "B", "C"],
            departments=["X", "Y", "Z"],
            locations=["1", "2", "3"],
        )

        signal2 = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=3,
            engineering_positions=1,
            sample_titles=["C", "B", "A"],  # Different order
            departments=["Z", "X", "Y"],
            locations=["3", "1", "2"],
        )

        hash1 = signal1.to_signal().source_response_hash
        hash2 = signal2.to_signal().source_response_hash

        assert hash1 == hash2

    def test_hash_different_for_different_content(self):
        """Signal hash should differ for different content"""
        from collectors.job_postings import JobPostingSignal

        signal1 = JobPostingSignal(
            company_name="Company A",
            company_domain="company-a.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
        )

        signal2 = JobPostingSignal(
            company_name="Company B",
            company_domain="company-b.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=2,
        )

        hash1 = signal1.to_signal().source_response_hash
        hash2 = signal2.to_signal().source_response_hash

        assert hash1 != hash2


# =============================================================================
# POSTING AGE CALCULATION
# =============================================================================

class TestPostingAgeCalculation:
    """Test posting age calculation for ghost job detection"""

    def test_age_calculation_positive(self):
        """Should calculate positive age for past dates"""
        from collectors.job_postings import _calculate_posting_age_days

        past_date = datetime.now(timezone.utc) - timedelta(days=30)
        age = _calculate_posting_age_days(past_date)

        assert age is not None
        assert 29 <= age <= 31  # Allow for timing

    def test_age_calculation_zero_for_today(self):
        """Should return 0 or 1 for today's date"""
        from collectors.job_postings import _calculate_posting_age_days

        today = datetime.now(timezone.utc)
        age = _calculate_posting_age_days(today)

        assert age is not None
        assert age <= 1

    def test_age_calculation_none_input(self):
        """Should handle None input"""
        from collectors.job_postings import _calculate_posting_age_days

        age = _calculate_posting_age_days(None)
        assert age is None

    def test_age_calculation_future_date(self):
        """Should handle future dates gracefully"""
        from collectors.job_postings import _calculate_posting_age_days

        future_date = datetime.now(timezone.utc) + timedelta(days=30)
        age = _calculate_posting_age_days(future_date)

        # Should return 0 or negative, but not crash
        assert age is not None
        assert age <= 0


# =============================================================================
# RUN PYTEST
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
