"""
Comprehensive tests for ArXiv collector.

Tests:
1. API integration with mocked responses
2. Error handling (API failures, malformed data, network errors)
3. Canonical key generation
4. Confidence score calculation
5. Signal type assignment
6. Full integration flow

Run:
    python -m pytest collectors/test_arxiv_enhanced.py -v
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta, timezone


# =============================================================================
# BASE INTEGRATION TESTS (from existing)
# =============================================================================

class TestArxivCollectorBaseIntegration:
    """Test ArxivCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """ArxivCollector should inherit from BaseCollector"""
        from collectors.arxiv import ArxivCollector
        from collectors.base import BaseCollector

        assert issubclass(ArxivCollector, BaseCollector)

    def test_has_collect_signals_method(self):
        """ArxivCollector should have _collect_signals method"""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI"])

        assert hasattr(collector, '_collect_signals')
        assert callable(collector._collect_signals)

    def test_has_rate_limiter(self):
        """ArxivCollector should have rate limiter"""
        from collectors.arxiv import ArxivCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = ArxivCollector(categories=["cs.AI"])

        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    def test_has_retry_config(self):
        """ArxivCollector should have retry_config"""
        from collectors.arxiv import ArxivCollector
        from collectors.retry_strategy import RetryConfig

        collector = ArxivCollector(categories=["cs.AI"])

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)

    def test_accepts_store_parameter(self):
        """ArxivCollector should accept store parameter"""
        from collectors.arxiv import ArxivCollector
        from unittest.mock import MagicMock

        mock_store = MagicMock()
        collector = ArxivCollector(categories=["cs.AI"], store=mock_store)

        assert collector.store is mock_store

    def test_accepts_categories_parameter(self):
        """ArxivCollector should accept categories parameter"""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI", "cs.LG"])

        assert collector.categories == ["cs.AI", "cs.LG"]

    @pytest.mark.asyncio
    async def test_fetch_papers_uses_retry_wrapper(self):
        """ArxivCollector should use _fetch_with_retry for API calls"""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI"])

        # Mock _fetch_with_retry to verify it's called
        mock_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"></feed>"""

        with patch.object(collector, '_fetch_with_retry', new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = mock_response
            async with collector:
                await collector._fetch_papers()

            # Verify _fetch_with_retry was called (which includes retry logic)
            mock_retry.assert_called_once()


class TestArxivPaper:
    """Test ArxivPaper dataclass"""

    def test_paper_dataclass_exists(self):
        """ArxivPaper dataclass should exist"""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.12345",
            title="A Novel Approach to AI",
            abstract="This paper presents...",
            authors=["John Doe", "Jane Smith"],
            categories=["cs.AI", "cs.LG"],
            published_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.12345",
        )

        assert paper.title == "A Novel Approach to AI"
        assert len(paper.authors) == 2

    def test_signal_score_calculation(self):
        """calculate_signal_score should return valid score"""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.12345",
            title="A Novel Approach to AI",
            abstract="This paper presents...",
            authors=["John Doe"],
            categories=["cs.AI"],
            published_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.12345",
        )

        score = paper.calculate_signal_score()

        # Score should be between 0 and 1
        assert 0 <= score <= 1
        # ArXiv papers are weak signals
        assert score <= 0.6

    def test_to_signal_method(self):
        """to_signal should return Signal object"""
        from collectors.arxiv import ArxivPaper
        from verification.verification_gate_v2 import Signal

        paper = ArxivPaper(
            arxiv_id="2301.12345",
            title="A Novel Approach to AI",
            abstract="This paper presents...",
            authors=["John Doe"],
            categories=["cs.AI"],
            published_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.12345",
        )

        result = paper.to_signal()

        assert isinstance(result, Signal)
        assert result.signal_type == "research_paper"


# =============================================================================
# COMPREHENSIVE API TESTS
# =============================================================================

class TestArxivAPIIntegration:
    """Test ArXiv API integration with mocked responses."""

    @pytest.mark.asyncio
    async def test_fetch_papers_success(self):
        """Should successfully fetch papers from API."""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI"], max_results=10)

        recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

        mock_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
        <id>http://arxiv.org/abs/2301.12345</id>
        <published>{recent_date}</published>
        <updated>{recent_date}</updated>
        <title>Test AI Paper</title>
        <summary>This is a test abstract about AI research.</summary>
        <author><name>John Doe</name></author>
        <author><name>Jane Smith</name></author>
        <category term="cs.AI"/>
        <link href="https://arxiv.org/pdf/2301.12345" title="pdf"/>
    </entry>
</feed>'''

        async with collector:
            with patch.object(collector, "_fetch_with_retry", new_callable=AsyncMock) as mock_retry:
                mock_retry.return_value = mock_xml.encode()
                papers = await collector._fetch_papers()

                assert len(papers) == 1
                assert papers[0].title == "Test AI Paper"
                assert papers[0].arxiv_id == "2301.12345"
                assert len(papers[0].authors) == 2

    @pytest.mark.asyncio
    async def test_fetch_papers_filters_old_papers(self):
        """Should filter out papers older than lookback_days."""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI"], lookback_days=30, max_results=10)

        # Paper from 60 days ago (should be filtered)
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

        mock_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
        <id>http://arxiv.org/abs/2301.12345</id>
        <published>{old_date}</published>
        <updated>{old_date}</updated>
        <title>Old Paper</title>
        <summary>This paper is too old.</summary>
        <author><name>John Doe</name></author>
        <category term="cs.AI"/>
        <link href="https://arxiv.org/pdf/2301.12345" title="pdf"/>
    </entry>
</feed>'''

        async with collector:
            with patch.object(collector, "_fetch_with_retry", new_callable=AsyncMock) as mock_retry:
                mock_retry.return_value = mock_xml.encode()
                papers = await collector._fetch_papers()

                # Old paper should be filtered out
                assert len(papers) == 0

    @pytest.mark.asyncio
    async def test_fetch_papers_api_error(self):
        """Should handle API errors gracefully."""
        from collectors.arxiv import ArxivCollector
        import httpx

        collector = ArxivCollector(categories=["cs.AI"])

        async with collector:
            with patch.object(collector, "_fetch_with_retry", new_callable=AsyncMock) as mock_retry:
                # Simulate API error by raising HTTPStatusError
                mock_retry.side_effect = httpx.HTTPStatusError(
                    "Server Error", request=Mock(), response=Mock(status_code=500)
                )
                papers = await collector._fetch_papers()

                # Should return empty list on error
                assert papers == []

    @pytest.mark.asyncio
    async def test_fetch_papers_xml_parse_error(self):
        """Should handle malformed XML responses."""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI"])

        async with collector:
            with patch.object(collector, "_fetch_with_retry", new_callable=AsyncMock) as mock_retry:
                mock_retry.return_value = b"<invalid xml>"
                papers = await collector._fetch_papers()

                # Should handle parse error gracefully
                assert papers == []

    @pytest.mark.asyncio
    async def test_fetch_papers_network_error(self):
        """Should handle network errors."""
        from collectors.arxiv import ArxivCollector
        import httpx

        collector = ArxivCollector(categories=["cs.AI"])

        async with collector:
            with patch.object(collector, "_fetch_with_retry", new_callable=AsyncMock) as mock_retry:
                mock_retry.side_effect = httpx.NetworkError("Connection failed")
                papers = await collector._fetch_papers()

                assert papers == []


class TestConfidenceScoring:
    """Test confidence score calculation."""

    def test_base_score_for_paper(self):
        """Should have base score of 0.3 for any paper."""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.01",
            title="Basic Paper",
            abstract="Basic research",
            authors=["John Doe"],
            categories=["cs.AI"],
            published_at=datetime.now(timezone.utc) - timedelta(days=100),
            updated_at=datetime.now(timezone.utc) - timedelta(days=100),
            pdf_url="https://arxiv.org/pdf/2301.01",
        )

        score = paper.calculate_signal_score()

        assert score >= 0.3
        assert score < 0.4  # No boosts

    def test_score_boosts_for_recent_paper(self):
        """Should boost score for recent papers."""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.02",
            title="Recent Paper",
            abstract="New research",
            authors=["John Doe"],
            categories=["cs.AI"],
            published_at=datetime.now(timezone.utc) - timedelta(days=15),  # Recent
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.02",
        )

        score = paper.calculate_signal_score()

        # Base 0.3 + recent boost 0.1 + AI category 0.05 = 0.45
        assert score >= 0.4

    def test_score_boosts_for_multi_category(self):
        """Should boost score for papers with multiple categories."""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.03",
            title="Multi-Category Paper",
            abstract="Cross-disciplinary research",
            authors=["John Doe"],
            categories=["cs.AI", "cs.LG", "stat.ML"],  # 3+ categories
            published_at=datetime.now(timezone.utc) - timedelta(days=20),
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.03",
        )

        score = paper.calculate_signal_score()

        # Should have multi-category boost
        assert score >= 0.35


class TestCanonicalKeyGeneration:
    """Test canonical key generation."""

    def test_canonical_key_from_first_author(self):
        """Should use first author for canonical key."""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.12345",
            title="Test Paper",
            abstract="Test",
            authors=["John Smith", "Jane Doe"],
            categories=["cs.AI"],
            published_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.12345",
        )

        signal = paper.to_signal()

        assert "canonical_key" in signal.raw_data
        assert "arxiv_author:" in signal.raw_data["canonical_key"]
        assert "johnsmith" in signal.raw_data["canonical_key"]


class TestSignalTypeAssignment:
    """Test signal type assignment."""

    def test_signal_type_is_research_paper(self):
        """Should assign correct signal type."""
        from collectors.arxiv import ArxivPaper

        paper = ArxivPaper(
            arxiv_id="2301.12345",
            title="Test",
            abstract="Test",
            authors=["John Doe"],
            categories=["cs.AI"],
            published_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            pdf_url="https://arxiv.org/pdf/2301.12345",
        )

        signal = paper.to_signal()

        assert signal.signal_type == "research_paper"
        assert signal.source_api == "arxiv"


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_full_collection_flow():
    """Test full collection flow from API to signals."""
    from collectors.arxiv import ArxivCollector

    collector = ArxivCollector(categories=["cs.AI"], max_results=10)

    recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

    mock_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
        <id>http://arxiv.org/abs/2401.12345</id>
        <published>{recent_date}</published>
        <updated>{recent_date}</updated>
        <title>Novel AI Architecture</title>
        <summary>We propose a new neural network architecture for language models.</summary>
        <author><name>Alice Johnson</name></author>
        <author><name>Bob Williams</name></author>
        <category term="cs.AI"/>
        <category term="cs.LG"/>
        <link href="https://arxiv.org/pdf/2401.12345" title="pdf"/>
    </entry>
</feed>'''

    async with collector:
        with patch.object(collector, "_fetch_with_retry", new_callable=AsyncMock) as mock_retry:
            mock_retry.return_value = mock_xml.encode()
            signals = await collector._collect_signals()

            assert len(signals) == 1
            assert signals[0].signal_type == "research_paper"
            assert signals[0].raw_data["arxiv_id"] == "2401.12345"
            assert signals[0].confidence > 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
