"""
Tests for job_postings.py refactored to use BaseCollector.

TDD Phase: RED - These tests verify BaseCollector integration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestJobPostingsInheritsBaseCollector:
    """Test JobPostingsCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """JobPostingsCollector should inherit from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.base import BaseCollector

        assert issubclass(JobPostingsCollector, BaseCollector)

    def test_has_api_name_for_rate_limiting(self):
        """JobPostingsCollector should set api_name for rate limiting"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=["example.com"])

        # Should have api_name set for rate limiting
        assert collector.api_name == "job_postings"

    def test_has_retry_config(self):
        """JobPostingsCollector should have retry_config from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.retry_strategy import RetryConfig

        collector = JobPostingsCollector(domains=["example.com"])

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)


class TestJobPostingsCollectSignalsMethod:
    """Test _collect_signals() implementation"""

    @pytest.mark.asyncio
    async def test_collect_signals_returns_list(self):
        """_collect_signals should return list of Signal objects"""
        from collectors.job_postings import JobPostingsCollector
        from verification.verification_gate_v2 import Signal

        with patch.object(
            JobPostingsCollector, 'check_domain', new_callable=AsyncMock
        ) as mock_check:
            # Return a mock signal
            mock_signal = MagicMock()
            mock_signal.to_signal.return_value = Signal(
                id="test_signal",
                signal_type="hiring_signal",
                confidence=0.8,
                source_api="greenhouse_jobs",
                source_url="https://jobs.greenhouse.io/test",
                source_response_hash="abc123",
                raw_data={"canonical_key": "domain:example.com"},
            )
            mock_check.return_value = mock_signal

            collector = JobPostingsCollector(domains=["example.com"])
            signals = await collector._collect_signals()

            assert isinstance(signals, list)
            if signals:  # If there are signals
                assert isinstance(signals[0], Signal)

    @pytest.mark.asyncio
    async def test_run_returns_collector_result(self):
        """run() should return CollectorResult from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector
        from discovery_engine.mcp_server import CollectorResult

        with patch.object(
            JobPostingsCollector, 'check_domain', new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = None  # No jobs found

            collector = JobPostingsCollector(domains=["example.com"])
            result = await collector.run(dry_run=True)

            assert isinstance(result, CollectorResult)
            assert hasattr(result, 'collector')
            assert hasattr(result, 'status')
            assert hasattr(result, 'signals_found')


class TestJobPostingsUsesRetryLogic:
    """Test that API calls use retry logic"""

    @pytest.mark.asyncio
    async def test_uses_fetch_with_retry(self):
        """HTTP calls should use _fetch_with_retry for retries"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.retry_strategy import RetryConfig

        collector = JobPostingsCollector(
            domains=["example.com"],
            retry_config=RetryConfig(max_retries=3, backoff_base=0.01),
        )

        # Verify collector has _fetch_with_retry method from BaseCollector
        assert hasattr(collector, '_fetch_with_retry')
        assert callable(collector._fetch_with_retry)

    @pytest.mark.asyncio
    async def test_retries_on_http_error(self):
        """Should retry on transient HTTP errors"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.retry_strategy import RetryConfig
        import httpx

        attempts = []

        async def mock_request():
            attempts.append(1)
            if len(attempts) < 2:
                request = httpx.Request("GET", "https://api.example.com")
                response = httpx.Response(500, request=request)
                raise httpx.HTTPStatusError("Server Error", request=request, response=response)
            return {"jobs": []}

        collector = JobPostingsCollector(
            domains=["example.com"],
            retry_config=RetryConfig(max_retries=3, backoff_base=0.01, jitter=False),
        )

        # Use the retry helper
        result = await collector._fetch_with_retry(mock_request)

        assert result == {"jobs": []}
        assert len(attempts) == 2  # Initial + 1 retry

    @pytest.mark.asyncio
    async def test_greenhouse_api_uses_retry_on_transient_error(self):
        """_check_greenhouse should retry on transient HTTP errors (500, 502, 503, 504)"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.retry_strategy import RetryConfig
        import httpx

        attempt_count = []

        async def mock_get(*args, **kwargs):
            attempt_count.append(1)
            if len(attempt_count) == 1:
                # First attempt fails with 503
                request = httpx.Request("GET", args[0] if args else kwargs.get('url', 'http://test'))
                response = httpx.Response(503, request=request)
                raise httpx.HTTPStatusError("Service Unavailable", request=request, response=response)
            else:
                # Second attempt succeeds
                class MockResponse:
                    status_code = 200
                    def json(self):
                        return {"jobs": [{"title": "Engineer", "absolute_url": "http://job"}]}
                return MockResponse()

        collector = JobPostingsCollector(
            domains=["test.com"],
            retry_config=RetryConfig(max_retries=3, backoff_base=0.01, jitter=False),
        )

        async with collector:
            with patch.object(collector.client, 'get', side_effect=mock_get):
                result = await collector._check_greenhouse("test", "test.com")

        # Should have retried and succeeded
        assert result is not None
        assert len(attempt_count) == 2

    @pytest.mark.asyncio
    async def test_lever_api_uses_retry_on_transient_error(self):
        """_check_lever should retry on transient HTTP errors (500, 502, 503, 504)"""
        from collectors.job_postings import JobPostingsCollector
        from collectors.retry_strategy import RetryConfig
        import httpx

        attempt_count = []

        async def mock_get(*args, **kwargs):
            attempt_count.append(1)
            if len(attempt_count) == 1:
                # First attempt fails with 502
                request = httpx.Request("GET", args[0] if args else kwargs.get('url', 'http://test'))
                response = httpx.Response(502, request=request)
                raise httpx.HTTPStatusError("Bad Gateway", request=request, response=response)
            else:
                # Second attempt succeeds
                class MockResponse:
                    status_code = 200
                    def json(self):
                        return [{"text": "Developer", "hostedUrl": "http://job"}]
                return MockResponse()

        collector = JobPostingsCollector(
            domains=["test.com"],
            retry_config=RetryConfig(max_retries=3, backoff_base=0.01, jitter=False),
        )

        async with collector:
            with patch.object(collector.client, 'get', side_effect=mock_get):
                result = await collector._check_lever("test", "test.com")

        # Should have retried and succeeded
        assert result is not None
        assert len(attempt_count) == 2


class TestJobPostingsUsesRateLimiter:
    """Test that rate limiting is applied"""

    @pytest.mark.asyncio
    async def test_has_rate_limiter(self):
        """JobPostingsCollector should have rate limiter"""
        from collectors.job_postings import JobPostingsCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = JobPostingsCollector(domains=["example.com"])

        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    @pytest.mark.asyncio
    async def test_rate_limiter_is_unlimited(self):
        """Job postings APIs are unlimited (no rate limiting needed)"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=["example.com"])

        # job_postings should be unlimited (public APIs, no auth)
        assert collector.rate_limiter.rate is None


class TestJobPostingsInitialization:
    """Test collector initialization"""

    def test_accepts_domains_parameter(self):
        """JobPostingsCollector should accept domains in __init__"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=["example.com", "test.com"])

        assert hasattr(collector, 'domains')
        assert collector.domains == ["example.com", "test.com"]

    def test_accepts_store_parameter(self):
        """JobPostingsCollector should accept store from BaseCollector"""
        from collectors.job_postings import JobPostingsCollector
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        collector = JobPostingsCollector(
            domains=["example.com"],
            store=mock_store,
        )

        assert collector.store is mock_store

    def test_collector_name_is_job_postings(self):
        """collector_name should be 'job_postings'"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=["example.com"])

        assert collector.collector_name == "job_postings"


class TestJobPostingsBackwardsCompatibility:
    """Test backwards compatibility with existing usage"""

    @pytest.mark.asyncio
    async def test_check_domain_still_works(self):
        """check_domain() method should still be available"""
        from collectors.job_postings import JobPostingsCollector

        collector = JobPostingsCollector(domains=[])

        assert hasattr(collector, 'check_domain')
        assert callable(collector.check_domain)

    def test_job_posting_signal_dataclass_exists(self):
        """JobPostingSignal dataclass should still exist"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=5,
            engineering_positions=3,
        )

        assert signal.company_name == "Test"
        assert signal.total_positions == 5

    def test_signal_score_calculation(self):
        """calculate_signal_score() should still work"""
        from collectors.job_postings import JobPostingSignal

        signal = JobPostingSignal(
            company_name="Test",
            company_domain="test.com",
            ats_platform="greenhouse",
            total_positions=10,
            engineering_positions=5,
        )

        score = signal.calculate_signal_score()

        # 0.7 base + 0.15 (10+ positions) + 0.1 (50%+ engineering)
        assert score == 0.95
