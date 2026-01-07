"""Tests for EntityResolver - orchestrates asset-to-lead resolution."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from consumer.entity_resolver import (
    EntityResolver,
    ResolverConfig,
    ResolutionCandidate,
)
from storage.entity_resolution import ResolutionMethod
from storage.source_asset_store import SourceAsset


class TestEntityResolver:
    """Test suite for EntityResolver."""

    @pytest.mark.asyncio
    async def test_resolve_by_domain_match(self):
        """Should resolve asset to lead via domain match."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="github_repo",
            external_id="acme/app",
            raw_payload={
                "homepage": "https://acme.io",
                "description": "Acme app",
            },
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        # Should find domain-based candidate
        domain_candidates = [
            c for c in candidates if c.method == ResolutionMethod.DOMAIN_MATCH
        ]
        assert len(domain_candidates) >= 1
        assert domain_candidates[0].lead_canonical_key == "domain:acme.io"
        assert domain_candidates[0].confidence >= 0.8

    @pytest.mark.asyncio
    async def test_resolve_by_github_org(self):
        """Should resolve asset to lead via GitHub org match."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="github_repo",
            external_id="startup-inc/product",
            raw_payload={
                "owner": {"login": "startup-inc"},
                "description": "Product",
            },
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        # Should find org-based candidate
        org_candidates = [
            c for c in candidates if c.method == ResolutionMethod.ORG_MATCH
        ]
        assert len(org_candidates) >= 1
        assert org_candidates[0].lead_canonical_key == "github_org:startup-inc"

    @pytest.mark.asyncio
    async def test_no_candidates_for_minimal_asset(self):
        """Asset with no identifiable info should return empty candidates."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="github_repo",
            external_id="user/random-repo",
            raw_payload={"description": "Just some code"},
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        # May have low-confidence heuristic candidate, but no strong matches
        strong_candidates = [c for c in candidates if c.confidence >= 0.7]
        assert len(strong_candidates) == 0

    @pytest.mark.asyncio
    async def test_multiple_candidates_sorted_by_confidence(self):
        """Multiple candidates should be sorted by confidence."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="github_repo",
            external_id="acme-corp/product",
            raw_payload={
                "homepage": "https://acme-corp.com",
                "owner": {"login": "acme-corp"},
                "description": "Acme Corp product",
            },
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        # Should have multiple candidates, sorted by confidence
        assert len(candidates) >= 2
        for i in range(len(candidates) - 1):
            assert candidates[i].confidence >= candidates[i + 1].confidence

    @pytest.mark.asyncio
    async def test_best_candidate_selection(self):
        """Should select best candidate based on confidence and method priority."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="github_repo",
            external_id="startup/app",
            raw_payload={
                "homepage": "https://startup.io",
                "owner": {"login": "startup"},
                "description": "Startup app",
            },
            fetched_at=datetime.utcnow(),
        )

        best = await resolver.get_best_candidate(asset)

        assert best is not None
        # Domain match should be preferred (highest confidence)
        assert best.method == ResolutionMethod.DOMAIN_MATCH
        assert best.lead_canonical_key == "domain:startup.io"

    @pytest.mark.asyncio
    async def test_extract_domain_from_various_formats(self):
        """Should extract domain from various URL formats."""
        resolver = EntityResolver(ResolverConfig())

        test_cases = [
            ("https://example.com", "example.com"),
            ("http://www.example.com/", "example.com"),
            ("https://subdomain.example.com/path", "subdomain.example.com"),
            ("example.com", "example.com"),
        ]

        for url, expected in test_cases:
            domain = resolver._extract_domain(url)
            assert domain == expected, f"Failed for {url}"

    @pytest.mark.asyncio
    async def test_skip_github_pages_domains(self):
        """Should skip github.io domains as they're not company domains."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="github_repo",
            external_id="user/project",
            raw_payload={
                "homepage": "https://user.github.io/project",
                "description": "A project",
            },
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        # Should not have domain match for github.io
        domain_candidates = [
            c for c in candidates if c.method == ResolutionMethod.DOMAIN_MATCH
        ]
        assert len(domain_candidates) == 0

    @pytest.mark.asyncio
    async def test_product_hunt_resolution(self):
        """Should resolve Product Hunt assets."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="product_hunt",
            external_id="ph_12345",
            raw_payload={
                "name": "Awesome Product",
                "website": "https://awesome-product.com",
                "tagline": "The best product ever",
            },
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        # Should find domain-based candidate from website
        domain_candidates = [
            c for c in candidates if c.method == ResolutionMethod.DOMAIN_MATCH
        ]
        assert len(domain_candidates) >= 1
        assert domain_candidates[0].lead_canonical_key == "domain:awesome-product.com"

    @pytest.mark.asyncio
    async def test_hacker_news_resolution(self):
        """Should resolve Hacker News assets with URL."""
        resolver = EntityResolver(ResolverConfig())

        asset = SourceAsset(
            id=1,
            source_type="hacker_news",
            external_id="hn_99999",
            raw_payload={
                "title": "Show HN: Our new startup",
                "url": "https://mystartup.com",
            },
            fetched_at=datetime.utcnow(),
        )

        candidates = await resolver.find_candidates(asset)

        domain_candidates = [
            c for c in candidates if c.method == ResolutionMethod.DOMAIN_MATCH
        ]
        assert len(domain_candidates) >= 1
        assert domain_candidates[0].lead_canonical_key == "domain:mystartup.com"
