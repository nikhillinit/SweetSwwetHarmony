"""
Tests for ArXiv collector.

Basic coverage for BaseCollector integration and dataclasses.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone


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

        mock_store = MagicMock()
        collector = ArxivCollector(categories=["cs.AI"], store=mock_store)

        assert collector.store is mock_store

    def test_accepts_categories_parameter(self):
        """ArxivCollector should accept categories parameter"""
        from collectors.arxiv import ArxivCollector

        collector = ArxivCollector(categories=["cs.AI", "cs.LG"])

        assert collector.categories == ["cs.AI", "cs.LG"]


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


class TestThesisCategories:
    """Test thesis-relevant categories"""

    def test_thesis_categories_exist(self):
        """THESIS_CATEGORIES should be defined"""
        from collectors.arxiv import THESIS_CATEGORIES

        assert isinstance(THESIS_CATEGORIES, dict)
        assert "cs.AI" in THESIS_CATEGORIES
        assert "cs.LG" in THESIS_CATEGORIES
