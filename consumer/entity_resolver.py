"""
EntityResolver: Orchestrates asset-to-lead resolution strategies.

This module implements multiple resolution strategies to link SourceAssets
to Leads (companies):

1. DOMAIN_MATCH: Extract domain from homepage/website URL
2. ORG_MATCH: Use GitHub org as canonical key
3. NAME_SIMILARITY: Fuzzy match company name (future)
4. HEURISTIC: Algorithmic guess based on patterns

Each strategy produces ResolutionCandidates with confidence scores.
The best candidate is selected based on confidence and method priority.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

from storage.entity_resolution import ResolutionMethod
from storage.source_asset_store import SourceAsset

logger = logging.getLogger(__name__)


# Domains to skip (not company domains)
SKIP_DOMAINS = {
    "github.io",
    "github.com",
    "gitlab.io",
    "gitlab.com",
    "bitbucket.org",
    "herokuapp.com",
    "netlify.app",
    "vercel.app",
    "pages.dev",
    "web.app",
    "firebaseapp.com",
}


@dataclass
class ResolverConfig:
    """Configuration for EntityResolver."""
    # Confidence thresholds
    domain_match_confidence: float = 0.9
    org_match_confidence: float = 0.75
    name_match_confidence: float = 0.6
    heuristic_confidence: float = 0.4

    # Enable/disable strategies
    enable_domain_match: bool = True
    enable_org_match: bool = True
    enable_name_similarity: bool = False  # Not yet implemented
    enable_heuristic: bool = True


@dataclass
class ResolutionCandidate:
    """A candidate resolution for an asset."""
    lead_canonical_key: str  # e.g., "domain:acme.com" or "github_org:acme"
    confidence: float  # 0.0-1.0
    method: ResolutionMethod
    reason: str  # Human-readable explanation
    metadata: Dict[str, Any] = field(default_factory=dict)


class EntityResolver:
    """
    Orchestrates asset-to-lead resolution.

    Usage:
        resolver = EntityResolver(ResolverConfig())

        # Get all candidates
        candidates = await resolver.find_candidates(asset)

        # Get best candidate
        best = await resolver.get_best_candidate(asset)
    """

    def __init__(self, config: Optional[ResolverConfig] = None):
        """
        Initialize EntityResolver.

        Args:
            config: Resolver configuration. Uses defaults if not provided.
        """
        self.config = config or ResolverConfig()

    async def find_candidates(
        self,
        asset: SourceAsset,
    ) -> List[ResolutionCandidate]:
        """
        Find all resolution candidates for an asset.

        Runs all enabled resolution strategies and returns candidates
        sorted by confidence (highest first).

        Args:
            asset: SourceAsset to resolve.

        Returns:
            List of ResolutionCandidates, sorted by confidence descending.
        """
        candidates: List[ResolutionCandidate] = []

        # Strategy 1: Domain match
        if self.config.enable_domain_match:
            domain_candidate = self._resolve_by_domain(asset)
            if domain_candidate:
                candidates.append(domain_candidate)

        # Strategy 2: GitHub org match
        if self.config.enable_org_match:
            org_candidate = self._resolve_by_org(asset)
            if org_candidate:
                candidates.append(org_candidate)

        # Strategy 3: Heuristic (fallback)
        if self.config.enable_heuristic:
            heuristic_candidate = self._resolve_by_heuristic(asset)
            if heuristic_candidate:
                candidates.append(heuristic_candidate)

        # Sort by confidence descending
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        return candidates

    async def get_best_candidate(
        self,
        asset: SourceAsset,
        min_confidence: float = 0.0,
    ) -> Optional[ResolutionCandidate]:
        """
        Get the best resolution candidate for an asset.

        Args:
            asset: SourceAsset to resolve.
            min_confidence: Minimum confidence threshold.

        Returns:
            Best ResolutionCandidate, or None if no candidates meet threshold.
        """
        candidates = await self.find_candidates(asset)

        if not candidates:
            return None

        # Filter by confidence
        valid_candidates = [c for c in candidates if c.confidence >= min_confidence]

        if not valid_candidates:
            return None

        return valid_candidates[0]

    def _resolve_by_domain(
        self,
        asset: SourceAsset,
    ) -> Optional[ResolutionCandidate]:
        """
        Resolve asset to lead via domain/homepage URL.

        Extracts domain from homepage, website, or url fields.
        """
        payload = asset.raw_payload

        # Try various URL fields based on source type
        url = None
        if asset.source_type == "github_repo":
            url = payload.get("homepage")
        elif asset.source_type == "product_hunt":
            url = payload.get("website")
        elif asset.source_type == "hacker_news":
            url = payload.get("url")
        else:
            # Generic fallback
            url = payload.get("homepage") or payload.get("website") or payload.get("url")

        if not url:
            return None

        domain = self._extract_domain(url)
        if not domain:
            return None

        # Skip known non-company domains
        if self._should_skip_domain(domain):
            logger.debug(f"Skipping domain {domain} (not a company domain)")
            return None

        return ResolutionCandidate(
            lead_canonical_key=f"domain:{domain}",
            confidence=self.config.domain_match_confidence,
            method=ResolutionMethod.DOMAIN_MATCH,
            reason=f"Domain extracted from URL: {url}",
            metadata={"source_url": url, "domain": domain},
        )

    def _resolve_by_org(
        self,
        asset: SourceAsset,
    ) -> Optional[ResolutionCandidate]:
        """
        Resolve asset to lead via GitHub organization.

        Uses the org from external_id (owner/repo) or owner field.
        """
        if asset.source_type != "github_repo":
            return None

        payload = asset.raw_payload
        org = None

        # Try owner field first
        owner = payload.get("owner")
        if isinstance(owner, dict):
            org = owner.get("login")
        elif isinstance(owner, str):
            org = owner

        # Fallback to external_id
        if not org and "/" in asset.external_id:
            org = asset.external_id.split("/")[0]

        if not org:
            return None

        # Skip personal repos (lowercase, short names often personal)
        # This is a heuristic - could be improved
        if len(org) < 3 or org.lower() == org:
            # Still include but with lower confidence
            return ResolutionCandidate(
                lead_canonical_key=f"github_org:{org}",
                confidence=self.config.org_match_confidence * 0.7,
                method=ResolutionMethod.ORG_MATCH,
                reason=f"GitHub org (possibly personal): {org}",
                metadata={"org": org, "possibly_personal": True},
            )

        return ResolutionCandidate(
            lead_canonical_key=f"github_org:{org}",
            confidence=self.config.org_match_confidence,
            method=ResolutionMethod.ORG_MATCH,
            reason=f"GitHub organization: {org}",
            metadata={"org": org},
        )

    def _resolve_by_heuristic(
        self,
        asset: SourceAsset,
    ) -> Optional[ResolutionCandidate]:
        """
        Resolve asset using heuristic rules.

        Fallback when domain and org matching fail.
        """
        payload = asset.raw_payload

        # Try to extract company name from various fields
        name = None
        source = None

        if asset.source_type == "github_repo":
            # Use repo name as last resort
            if "/" in asset.external_id:
                name = asset.external_id.split("/")[-1]
                source = "repo_name"
        elif asset.source_type == "product_hunt":
            name = payload.get("name")
            source = "product_name"
        elif asset.source_type == "hacker_news":
            # Try to extract from title
            title = payload.get("title", "")
            if title.startswith("Show HN:"):
                name = title.replace("Show HN:", "").strip().split()[0]
                source = "hn_title"

        if not name:
            return None

        # Normalize name
        normalized = self._normalize_name(name)
        if not normalized or len(normalized) < 2:
            return None

        return ResolutionCandidate(
            lead_canonical_key=f"name:{normalized}",
            confidence=self.config.heuristic_confidence,
            method=ResolutionMethod.HEURISTIC,
            reason=f"Name extracted from {source}: {name}",
            metadata={"name": name, "normalized": normalized, "source": source},
        )

    def _extract_domain(self, url: str) -> Optional[str]:
        """
        Extract domain from URL.

        Handles various formats and normalizes the result.
        """
        if not url:
            return None

        url = url.strip()

        # Add scheme if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path.split("/")[0]

            # Remove www prefix
            if domain.startswith("www."):
                domain = domain[4:]

            # Remove port if present
            domain = domain.split(":")[0]

            return domain.lower() if domain else None
        except Exception:
            return None

    def _should_skip_domain(self, domain: str) -> bool:
        """Check if domain should be skipped (not a company domain)."""
        domain = domain.lower()

        # Check exact match
        if domain in SKIP_DOMAINS:
            return True

        # Check suffix match (e.g., user.github.io)
        for skip in SKIP_DOMAINS:
            if domain.endswith("." + skip) or domain.endswith(skip):
                return True

        return False

    def _normalize_name(self, name: str) -> str:
        """Normalize company name for matching."""
        # Lowercase
        name = name.lower()

        # Remove common suffixes
        for suffix in ["inc", "llc", "ltd", "corp", "co", "io", "app"]:
            name = re.sub(rf"\s*{suffix}\.?$", "", name)

        # Remove non-alphanumeric (keep spaces)
        name = re.sub(r"[^a-z0-9\s]", "", name)

        # Collapse whitespace
        name = " ".join(name.split())

        return name.strip()
