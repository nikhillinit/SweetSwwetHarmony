"""
Tests for USPTO patent collector.

Basic coverage for BaseCollector integration and dataclasses.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone


class TestUSPTOCollectorBaseIntegration:
    """Test USPTOCollector inherits from BaseCollector"""

    def test_inherits_from_base_collector(self):
        """USPTOCollector should inherit from BaseCollector"""
        from collectors.uspto import USPTOCollector
        from collectors.base import BaseCollector

        assert issubclass(USPTOCollector, BaseCollector)

    def test_has_collect_signals_method(self):
        """USPTOCollector should have _collect_signals method"""
        from collectors.uspto import USPTOCollector

        collector = USPTOCollector(keywords=["machine learning"])

        assert hasattr(collector, '_collect_signals')
        assert callable(collector._collect_signals)

    def test_has_rate_limiter(self):
        """USPTOCollector should have rate limiter"""
        from collectors.uspto import USPTOCollector
        from utils.rate_limiter import AsyncRateLimiter

        collector = USPTOCollector(keywords=["machine learning"])

        assert hasattr(collector, 'rate_limiter')
        assert isinstance(collector.rate_limiter, AsyncRateLimiter)

    def test_has_retry_config(self):
        """USPTOCollector should have retry_config"""
        from collectors.uspto import USPTOCollector
        from collectors.retry_strategy import RetryConfig

        collector = USPTOCollector(keywords=["machine learning"])

        assert hasattr(collector, 'retry_config')
        assert isinstance(collector.retry_config, RetryConfig)

    def test_accepts_store_parameter(self):
        """USPTOCollector should accept store parameter"""
        from collectors.uspto import USPTOCollector

        mock_store = MagicMock()
        collector = USPTOCollector(keywords=["ml"], store=mock_store)

        assert collector.store is mock_store

    def test_accepts_keywords_parameter(self):
        """USPTOCollector should accept keywords parameter"""
        from collectors.uspto import USPTOCollector

        collector = USPTOCollector(keywords=["neural network", "deep learning"])

        assert collector.keywords == ["neural network", "deep learning"]


class TestPatentFiling:
    """Test PatentFiling dataclass"""

    def test_patent_dataclass_exists(self):
        """PatentFiling dataclass should exist"""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="US12345678",
            patent_number="12345678",
            title="Machine Learning System",
            abstract="A system for machine learning...",
            filing_date=datetime.now(timezone.utc),
            grant_date=None,
            inventors=[{"name": "John Doe"}],
            assignees=[{"name": "Tech Corp"}],
        )

        assert patent.title == "Machine Learning System"
        assert len(patent.inventors) == 1

    def test_signal_score_base(self):
        """calculate_signal_score should return valid base score"""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="US12345678",
            patent_number="12345678",
            title="Machine Learning System",
            abstract="A system for machine learning...",
            filing_date=datetime.now(timezone.utc),
            grant_date=None,
            inventors=[],
            assignees=[],
        )

        score = patent.calculate_signal_score()

        # Base score for patents is 0.4
        assert 0.4 <= score <= 1.0

    def test_signal_score_with_grant(self):
        """calculate_signal_score should boost for granted patents"""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="US12345678",
            patent_number="12345678",
            title="Machine Learning System",
            abstract="A system for machine learning...",
            filing_date=datetime.now(timezone.utc),
            grant_date=datetime.now(timezone.utc),  # Granted!
            inventors=[],
            assignees=[],
        )

        score = patent.calculate_signal_score()

        # Should get grant boost (+0.1)
        assert score >= 0.5

    def test_to_signal_method(self):
        """to_signal should return Signal object"""
        from collectors.uspto import PatentFiling
        from verification.verification_gate_v2 import Signal

        patent = PatentFiling(
            patent_id="US12345678",
            patent_number="12345678",
            title="Machine Learning System",
            abstract="A system for machine learning...",
            filing_date=datetime.now(timezone.utc),
            grant_date=None,
            inventors=[{"name": "John Doe"}],
            assignees=[{"name": "Tech Corp"}],
        )

        result = patent.to_signal()

        assert isinstance(result, Signal)
        assert result.signal_type == "patent_filing"


class TestPatentsViewAPI:
    """Test PatentsView API configuration"""

    def test_api_url_defined(self):
        """PATENTSVIEW_API should be defined"""
        from collectors.uspto import PATENTSVIEW_API

        assert "patentsview.org" in PATENTSVIEW_API
