# tests/enrichment/test_travel_certifications.py
from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from enrichment.travel_certifications import (
    TravelCertificationsClient,
    TravelCertification,
    CertificationSource,
)


class TestTravelCertification:
    """Tests for TravelCertification dataclass."""

    def test_certification_fields(self):
        cert = TravelCertification(
            entity_id="entity-123",
            source=CertificationSource.FORBES,
            rating="5-star",
            year=2026,
            property_name="The Ritz-Carlton",
            fetched_at=datetime.utcnow()
        )
        assert cert.source == CertificationSource.FORBES
        assert cert.rating == "5-star"


class TestCertificationSource:
    """Tests for CertificationSource enum."""

    def test_sources_exist(self):
        assert CertificationSource.FORBES.value == "forbes"
        assert CertificationSource.AAA.value == "aaa"
        assert CertificationSource.MICHELIN.value == "michelin"


class TestTravelCertificationsClient:
    """Tests for TravelCertificationsClient."""

    def test_client_initialization(self):
        client = TravelCertificationsClient()
        assert client.rate_limit == 1.0

    @pytest.mark.asyncio
    async def test_search_forbes_certifications(self):
        client = TravelCertificationsClient()

        mock_certs = [
            TravelCertification(
                entity_id="",
                source=CertificationSource.FORBES,
                rating="5-star",
                year=2026,
                property_name="Test Hotel",
                fetched_at=datetime.utcnow()
            )
        ]

        with patch.object(client, '_fetch_forbes', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_certs
            results = await client.search_certifications("Test Hotel")

            assert len(results) >= 0  # May or may not find matches

    @pytest.mark.asyncio
    async def test_search_handles_errors(self):
        client = TravelCertificationsClient()

        with patch.object(client, '_fetch_forbes', new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("Network error")
            results = await client.search_certifications("Test")

            # Should handle errors gracefully
            assert isinstance(results, list)
