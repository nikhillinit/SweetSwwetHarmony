"""
Comprehensive tests for USPTO patent collector.

Tests:
1. API integration with mocked responses
2. Error handling
3. Canonical key generation
4. Confidence score calculation
5. Signal type assignment

Run:
    python -m pytest collectors/test_uspto_enhanced.py -v
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta, timezone


# =============================================================================
# BASE INTEGRATION TESTS
# =============================================================================

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

    def test_accepts_keywords_parameter(self):
        """USPTOCollector should accept keywords parameter"""
        from collectors.uspto import USPTOCollector

        collector = USPTOCollector(keywords=["neural network", "deep learning"])

        assert collector.keywords == ["neural network", "deep learning"]


# =============================================================================
# COMPREHENSIVE API TESTS
# =============================================================================

class TestUSPTOAPIIntegration:
    """Test USPTO API integration with mocked responses."""

    @pytest.mark.asyncio
    async def test_fetch_patents_success(self):
        """Should successfully fetch patents from API."""
        from collectors.uspto import USPTOCollector

        collector = USPTOCollector(keywords=["ai"], max_results=10)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json = Mock(return_value={
            "patents": [
                {
                    "patent_id": "11234567",
                    "patent_number": "11234567",
                    "patent_title": "Machine Learning System",
                    "patent_abstract": "A novel system for machine learning...",
                    "patent_date": "2024-01-15",
                    "patent_num_cited_by_us_patents": 5,
                }
            ]
        })

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                patents = await collector._fetch_patents()

                assert len(patents) == 1
                assert patents[0].title == "Machine Learning System"
                assert patents[0].patent_number == "11234567"

    @pytest.mark.asyncio
    async def test_fetch_patents_api_error(self):
        """Should handle API errors gracefully."""
        from collectors.uspto import USPTOCollector

        collector = USPTOCollector(keywords=["ai"])

        mock_response = Mock()
        mock_response.status_code = 500

        async with collector:
            with patch.object(collector.client, "post", return_value=mock_response):
                patents = await collector._fetch_patents()

                # Should return empty list on error
                assert patents == []

    @pytest.mark.asyncio
    async def test_fetch_patents_network_error(self):
        """Should handle network errors."""
        from collectors.uspto import USPTOCollector
        import httpx

        collector = USPTOCollector(keywords=["ai"])

        async with collector:
            with patch.object(collector.client, "post", side_effect=httpx.NetworkError("Connection failed")):
                patents = await collector._fetch_patents()

                assert patents == []


class TestConfidenceScoring:
    """Test confidence score calculation."""

    def test_base_score_for_filing(self):
        """Should have base score of 0.4 for patent filing."""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="11234567",
            patent_number="11234567",
            title="Test Patent",
            abstract="A test patent",
            filing_date=datetime.now(timezone.utc),
            grant_date=None,
            inventors=[],
            assignees=[],
        )

        score = patent.calculate_signal_score()

        assert score >= 0.4
        assert score < 0.5  # No boosts

    def test_score_boost_for_granted_patent(self):
        """Should boost score for granted patents."""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="11234567",
            patent_number="11234567",
            title="Granted Patent",
            abstract="A granted patent",
            filing_date=datetime.now(timezone.utc) - timedelta(days=200),
            grant_date=datetime.now(timezone.utc),  # Granted
            inventors=[],
            assignees=[],
        )

        score = patent.calculate_signal_score()

        # Base 0.4 + granted 0.1 = 0.5
        assert score >= 0.5

    def test_score_boost_for_citations(self):
        """Should boost score for highly cited patents."""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="11234567",
            patent_number="11234567",
            title="Cited Patent",
            abstract="A highly cited patent",
            filing_date=datetime.now(timezone.utc),
            grant_date=datetime.now(timezone.utc),
            inventors=[],
            assignees=[],
            citations_count=25,  # High citations
        )

        score = patent.calculate_signal_score()

        # Base 0.4 + granted 0.1 + citations 0.1 = 0.6
        assert score >= 0.6


class TestCanonicalKeyGeneration:
    """Test canonical key generation."""

    def test_canonical_key_from_assignee(self):
        """Should use assignee for canonical key."""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="11234567",
            patent_number="11234567",
            title="Test",
            abstract="Test",
            filing_date=datetime.now(timezone.utc),
            grant_date=None,
            inventors=[],
            assignees=[{"organization": "Acme Corp", "name_first": "", "name_last": "", "type": ""}],
        )

        signal = patent.to_signal()

        assert "canonical_key" in signal.raw_data
        assert "patent_assignee:" in signal.raw_data["canonical_key"]
        assert "acme" in signal.raw_data["canonical_key"].lower()


class TestSignalTypeAssignment:
    """Test signal type assignment."""

    def test_signal_type_is_patent_filing(self):
        """Should assign correct signal type."""
        from collectors.uspto import PatentFiling

        patent = PatentFiling(
            patent_id="11234567",
            patent_number="11234567",
            title="Test",
            abstract="Test",
            filing_date=datetime.now(timezone.utc),
            grant_date=None,
            inventors=[{"name": "John Doe"}],
            assignees=[{"name": "Acme Corp"}],
        )

        signal = patent.to_signal()

        assert signal.signal_type == "patent_filing"
        assert signal.source_api == "uspto"


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

@pytest.mark.asyncio
async def test_full_collection_flow():
    """Test full collection flow from API to signals."""
    from collectors.uspto import USPTOCollector

    collector = USPTOCollector(keywords=["ai"], max_results=10)

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={
        "patents": [
            {
                "patent_id": "11234567",
                "patent_number": "11234567",
                "patent_title": "AI Neural Network System",
                "patent_abstract": "A novel neural network architecture for AI applications.",
                "patent_date": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d"),
                "patent_num_cited_by_us_patents": 10,
                "inventors": [
                    {"inventor_first_name": "John", "inventor_last_name": "Doe"}
                ],
                "assignees": [
                    {"assignee_organization": "Tech Corp"}
                ],
            }
        ]
    })

    async with collector:
        with patch.object(collector.client, "post", return_value=mock_response):
            signals = await collector._collect_signals()

            assert len(signals) == 1
            assert signals[0].signal_type == "patent_filing"
            assert signals[0].raw_data["patent_number"] == "11234567"
            assert signals[0].confidence >= 0.4


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
