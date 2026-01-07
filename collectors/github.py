"""
GitHub Signal Collector for Discovery Engine

Finds repositories with recent star/fork spikes indicating developer tools gaining traction.

Focus areas:
- AI Infrastructure (LLM frameworks, vector DBs, inference engines)
- Developer tools (APIs, SDKs, DevOps)
- Machine Learning (training, serving, deployment)

Strategy:
1. Search for trending repos by stars/recency
2. Filter by relevant topics (ai, ml, llm, infrastructure, developer-tools)
3. Identify the company/org behind the repo
4. Calculate spike metrics (growth rate, velocity)
5. Build canonical keys for deduplication
6. Return signals compatible with verification_gate_v2
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse


class TopicMode(str, Enum):
    """Topic mode for GitHub collector filtering."""
    TECH = "tech"  # AI/ML/Developer tools (default)
    CONSUMER = "consumer"  # Consumer thesis categories

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal, VerificationStatus
from utils.canonical_keys import (
    build_canonical_key,
    build_canonical_key_candidates,
    normalize_domain,
    normalize_github_org,
    normalize_github_repo,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Relevant topics for filtering (aligned with Press On Ventures thesis)
RELEVANT_TOPICS = {
    # AI Infrastructure
    "ai", "artificial-intelligence", "machine-learning", "ml", "llm",
    "large-language-models", "mlops", "ai-infrastructure",
    "vector-database", "embeddings", "transformers",

    # Developer Tools
    "developer-tools", "devtools", "api", "sdk", "cli",
    "framework", "library", "infrastructure",

    # Specific tech areas
    "python", "typescript", "rust", "go", "javascript",
    "docker", "kubernetes", "serverless", "edge-computing",

    # Data/ML
    "data-engineering", "data-science", "deep-learning",
    "neural-networks", "pytorch", "tensorflow",
}

# Consumer thesis topics (Press On Ventures fund focus)
CONSUMER_TOPICS = {
    # Consumer CPG
    "food-delivery", "meal-kit", "grocery", "food-tech", "foodtech",
    "beverage", "snacks", "beauty", "skincare", "personal-care",
    "household", "subscription-box",

    # Consumer Health Tech
    "fitness-app", "fitness", "wellness", "mental-health", "health-tech",
    "healthtech", "supplements", "wearables", "nutrition", "meditation",

    # Travel & Hospitality
    "travel-booking", "travel", "hospitality", "restaurant", "experiences",
    "travel-tech", "traveltech", "booking", "hotels",

    # Consumer Marketplaces
    "marketplace", "consumer", "d2c", "dtc", "direct-to-consumer",
    "ecommerce", "retail-tech", "retailtech",
}

# Minimum thresholds
MIN_STARS = 100
MIN_RECENT_STARS = 20  # Stars gained in lookback period
MIN_GROWTH_RATE = 0.1  # 10% growth in lookback period

# GitHub API rate limits
GITHUB_RATE_LIMIT_DELAY = 1.0  # seconds between requests
GITHUB_MAX_RETRIES = 3


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class RepoMetrics:
    """Metrics for a GitHub repository"""
    repo_full_name: str  # org/repo
    org: str
    repo: str
    description: Optional[str]

    # Current stats
    stars: int
    forks: int
    watchers: int
    open_issues: int

    # Metadata
    language: Optional[str]
    topics: List[str]
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime

    # URLs
    html_url: str
    homepage: Optional[str]

    # Spike metrics (calculated)
    recent_stars: int = 0
    growth_rate: float = 0.0
    velocity_stars_per_day: float = 0.0

    # Company identification
    owner_type: str = "Unknown"  # User, Organization
    owner_company: Optional[str] = None
    owner_bio: Optional[str] = None
    owner_website: Optional[str] = None
    owner_email: Optional[str] = None

    # Raw data for provenance
    raw_repo_data: Dict[str, Any] = field(default_factory=dict)
    raw_owner_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_org_owned(self) -> bool:
        return self.owner_type == "Organization"

    @property
    def is_relevant(self) -> bool:
        """Check if repo matches our focus areas"""
        repo_topics = {t.lower() for t in self.topics}
        return bool(repo_topics & RELEVANT_TOPICS)

    @property
    def age_days(self) -> int:
        """Days since repo creation"""
        return (datetime.now(timezone.utc) - self.created_at).days

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for logging/serialization"""
        return {
            "repo": self.repo_full_name,
            "org": self.org,
            "stars": self.stars,
            "recent_stars": self.recent_stars,
            "growth_rate": round(self.growth_rate, 3),
            "velocity": round(self.velocity_stars_per_day, 2),
            "language": self.language,
            "topics": self.topics,
            "owner_type": self.owner_type,
            "owner_company": self.owner_company,
            "owner_website": self.owner_website,
            "is_relevant": self.is_relevant,
            "age_days": self.age_days,
        }


# =============================================================================
# GITHUB COLLECTOR
# =============================================================================

class GitHubCollector(BaseCollector):
    """
    Collects GitHub spike signals for early-stage startups.

    Usage:
        collector = GitHubCollector(store=signal_store, github_token=os.getenv("GITHUB_TOKEN"))
        result = await collector.run(dry_run=False)
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        github_token: Optional[str] = None,
        lookback_days: int = 30,
        max_repos: int = 100,
        topic_mode: TopicMode = TopicMode.TECH,
        star_change_threshold: float = 0.10,
    ):
        """
        Args:
            store: Optional SignalStore instance for persistence
            github_token: GitHub API token (or set GITHUB_TOKEN env var)
            lookback_days: How far back to look for star spikes
            max_repos: Maximum repos to analyze per run
            topic_mode: TopicMode.TECH (default) or TopicMode.CONSUMER
            star_change_threshold: Minimum percentage change in stars to detect (default 10%)
        """
        super().__init__(store=store, collector_name="github")

        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        if not self.github_token:
            raise ValueError("GitHub token required (set GITHUB_TOKEN env var)")

        self.lookback_days = lookback_days
        self.max_repos = max_repos
        self.topic_mode = topic_mode
        self.star_change_threshold = star_change_threshold

        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Rate limiting
        self._request_count = 0
        self._last_request_time = datetime.now(timezone.utc)

        # Cache for org lookups
        self._org_cache: Dict[str, Dict[str, Any]] = {}

    async def __aenter__(self):
        """Async context manager entry"""
        self.client = httpx.AsyncClient(timeout=30.0)
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.client:
            await self.client.aclose()
        return await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from GitHub trending repositories.

        Returns:
            List of Signal objects
        """
        # Step 1: Search for trending repos
        logger.info("Searching for trending repositories...")
        repos = await self._search_trending_repos()
        logger.info(f"Found {len(repos)} candidate repositories")

        # Step 2: Enrich with metrics and owner data
        logger.info("Enriching repository data...")
        enriched_repos: List[RepoMetrics] = []
        for repo_data in repos[:self.max_repos]:
            try:
                metrics = await self._enrich_repo_metrics(repo_data)
                if metrics.is_relevant:
                    enriched_repos.append(metrics)
            except Exception as e:
                logger.warning(f"Failed to enrich {repo_data.get('full_name')}: {e}")
                # Continue with next repo - don't fail entire batch

        logger.info(f"Enriched {len(enriched_repos)} relevant repositories")

        # Step 3: Filter for spikes
        logger.info("Filtering for star spikes...")
        spiking_repos = self._filter_for_spikes(enriched_repos)
        logger.info(f"Found {len(spiking_repos)} repositories with spikes")

        # Step 4: Convert to signals
        logger.info("Converting to signals...")
        signals = self._convert_to_signals(spiking_repos)

        # Step 5: Log samples
        if signals:
            logger.info(f"Sample signals:")
            for sig in signals[:3]:
                logger.info(f"  - {sig.raw_data.get('repo_full_name')}: "
                          f"{sig.raw_data.get('stars')} stars "
                          f"(+{sig.raw_data.get('recent_stars')} recent)")

        return signals

    @retry(
        stop=stop_after_attempt(GITHUB_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    async def _github_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make a rate-limited request to GitHub API.

        Handles:
        - Rate limiting (both proactive and reactive)
        - Retries on 5xx errors
        - Response validation
        """
        # Proactive rate limiting
        await self._rate_limit()

        url = f"{self.base_url}{endpoint}"

        logger.debug(f"GitHub API: {method} {endpoint}")
        response = await self.client.request(
            method,
            url,
            headers=self.headers,
            **kwargs
        )

        # Check for rate limit headers
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining and int(remaining) < 10:
            logger.warning(f"GitHub rate limit low: {remaining} remaining")

        # Handle rate limit exceeded
        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset_time = response.headers.get("X-RateLimit-Reset")
            if reset_time:
                wait_seconds = int(reset_time) - int(datetime.now(timezone.utc).timestamp())
                logger.warning(f"Rate limit exceeded. Waiting {wait_seconds}s")
                await asyncio.sleep(min(wait_seconds, 60))

        response.raise_for_status()

        self._request_count += 1
        self._last_request_time = datetime.now(timezone.utc)

        return response.json()

    async def _rate_limit(self):
        """Proactive rate limiting between requests"""
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_request_time).total_seconds()

        if elapsed < GITHUB_RATE_LIMIT_DELAY:
            await asyncio.sleep(GITHUB_RATE_LIMIT_DELAY - elapsed)

    async def _search_trending_repos(self) -> List[Dict[str, Any]]:
        """
        Search for trending repositories using GitHub Search API.

        Strategy:
        - Search for repos with stars > MIN_STARS
        - Pushed recently (within lookback window)
        - Sort by stars (most popular first)
        - Filter by language and topics
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")

        # Build search query
        # Example: "stars:>100 pushed:>2024-01-01 topic:ai OR topic:ml"
        query_parts = [
            f"stars:>{MIN_STARS}",
            f"pushed:>{cutoff_str}",
        ]

        # Add topic filters (GitHub search supports OR)
        topic_filters = ["ai", "ml", "llm", "developer-tools", "infrastructure"]
        topic_query = " OR ".join(f"topic:{t}" for t in topic_filters)
        query = " ".join(query_parts) + f" {topic_query}"

        logger.info(f"GitHub search query: {query}")

        # Search (paginated)
        all_repos: List[Dict[str, Any]] = []
        per_page = 100
        max_pages = 3  # GitHub limits to 1000 results, but we'll cap lower

        for page in range(1, max_pages + 1):
            try:
                result = await self._github_request(
                    "GET",
                    "/search/repositories",
                    params={
                        "q": query,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": per_page,
                        "page": page,
                    }
                )

                items = result.get("items", [])
                all_repos.extend(items)

                logger.info(f"Page {page}: {len(items)} repos (total: {len(all_repos)})")

                # Stop if we got fewer than per_page (last page)
                if len(items) < per_page:
                    break

                # Stop if we hit our max
                if len(all_repos) >= self.max_repos:
                    break

            except Exception as e:
                logger.warning(f"Search page {page} failed: {e}")
                break

        return all_repos

    async def _enrich_repo_metrics(self, repo_data: Dict[str, Any]) -> RepoMetrics:
        """
        Enrich repository data with owner information and calculated metrics.

        Steps:
        1. Parse basic repo data
        2. Fetch owner (user/org) details
        3. Calculate spike metrics
        4. Build canonical keys
        """
        # Parse basic repo data
        full_name = repo_data["full_name"]
        org, repo = full_name.split("/", 1)

        # Parse timestamps
        created_at = datetime.fromisoformat(repo_data["created_at"].replace("Z", "+00:00"))
        updated_at = datetime.fromisoformat(repo_data["updated_at"].replace("Z", "+00:00"))
        pushed_at = datetime.fromisoformat(repo_data["pushed_at"].replace("Z", "+00:00"))

        # Get owner details
        owner_data = await self._get_owner_details(repo_data["owner"]["login"])

        # Extract company info from owner
        owner_type = owner_data.get("type", "Unknown")
        owner_company = owner_data.get("company")
        owner_bio = owner_data.get("bio")
        owner_website = owner_data.get("blog")
        owner_email = owner_data.get("email")

        # Create metrics object
        metrics = RepoMetrics(
            repo_full_name=full_name,
            org=org,
            repo=repo,
            description=repo_data.get("description"),
            stars=repo_data["stargazers_count"],
            forks=repo_data["forks_count"],
            watchers=repo_data["watchers_count"],
            open_issues=repo_data["open_issues_count"],
            language=repo_data.get("language"),
            topics=repo_data.get("topics", []),
            created_at=created_at,
            updated_at=updated_at,
            pushed_at=pushed_at,
            html_url=repo_data["html_url"],
            homepage=repo_data.get("homepage"),
            owner_type=owner_type,
            owner_company=owner_company,
            owner_bio=owner_bio,
            owner_website=owner_website,
            owner_email=owner_email,
            raw_repo_data=repo_data,
            raw_owner_data=owner_data,
        )

        # Calculate spike metrics
        # Note: GitHub API doesn't give historical stars, so we estimate based on age
        # For more accurate spike detection, you'd need to track stars over time
        # or use a service like GH Archive

        # Rough estimate: assume linear growth and recent activity indicates spike
        age_days = max(metrics.age_days, 1)
        avg_stars_per_day = metrics.stars / age_days

        # Check if recently pushed (indicates active development)
        days_since_push = (datetime.now(timezone.utc) - pushed_at).days

        if days_since_push < self.lookback_days:
            # Assume higher recent activity
            # This is a heuristic - in production, use historical data
            estimated_recent_stars = int(avg_stars_per_day * self.lookback_days * 1.5)
            metrics.recent_stars = min(estimated_recent_stars, metrics.stars)
            metrics.growth_rate = metrics.recent_stars / max(metrics.stars, 1)
            metrics.velocity_stars_per_day = metrics.recent_stars / self.lookback_days
        else:
            # Minimal recent activity
            metrics.recent_stars = 0
            metrics.growth_rate = 0.0
            metrics.velocity_stars_per_day = 0.0

        return metrics

    async def _get_owner_details(self, login: str) -> Dict[str, Any]:
        """
        Fetch owner (user or org) details from GitHub.

        Cached to avoid repeated lookups.
        """
        if login in self._org_cache:
            return self._org_cache[login]

        # Determine if user or org
        try:
            # Try org endpoint first
            owner_data = await self._github_request("GET", f"/orgs/{login}")
            owner_data["type"] = "Organization"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Not an org, try user
                owner_data = await self._github_request("GET", f"/users/{login}")
                owner_data["type"] = "User"
            else:
                raise

        self._org_cache[login] = owner_data
        return owner_data

    def is_topic_relevant(self, repo: RepoMetrics) -> bool:
        """
        Check if repository topics match the current topic mode.

        Args:
            repo: RepoMetrics to check.

        Returns:
            True if repo matches current topic_mode topics.
        """
        repo_topics = {t.lower() for t in repo.topics}

        if self.topic_mode == TopicMode.CONSUMER:
            return bool(repo_topics & CONSUMER_TOPICS)
        else:
            return bool(repo_topics & RELEVANT_TOPICS)

    def _filter_for_spikes(self, repos: List[RepoMetrics]) -> List[RepoMetrics]:
        """
        Filter repositories to only those showing spike signals.

        Criteria:
        - Recent stars > MIN_RECENT_STARS
        - Growth rate > MIN_GROWTH_RATE
        - Relevant topics (based on topic_mode)
        """
        spiking = []

        for repo in repos:
            if (repo.recent_stars >= MIN_RECENT_STARS and
                repo.growth_rate >= MIN_GROWTH_RATE and
                self.is_topic_relevant(repo)):
                spiking.append(repo)

        # Sort by recent stars (strongest signals first)
        spiking.sort(key=lambda r: r.recent_stars, reverse=True)

        return spiking

    def _convert_to_signals(self, repos: List[RepoMetrics]) -> List[Signal]:
        """
        Convert RepoMetrics to Signal objects compatible with verification_gate_v2.

        Each signal includes:
        - Signal type: "github_spike"
        - Confidence score based on spike strength
        - Provenance (source URL, response hash)
        - Canonical keys for deduplication
        """
        signals = []

        for repo in repos:
            # Calculate confidence based on spike metrics
            # Higher confidence for:
            # - More recent stars
            # - Higher growth rate
            # - Organization-owned (vs individual)
            # - Has website/company info

            confidence = 0.5  # Base confidence

            # Boost for strong spike
            if repo.recent_stars > 100:
                confidence += 0.2
            elif repo.recent_stars > 50:
                confidence += 0.1

            # Boost for high growth rate
            if repo.growth_rate > 0.5:  # 50%+ growth
                confidence += 0.15
            elif repo.growth_rate > 0.25:  # 25%+ growth
                confidence += 0.1

            # Boost for organization ownership
            if repo.is_org_owned:
                confidence += 0.1

            # Boost for having website/company info
            if repo.owner_website or repo.owner_company:
                confidence += 0.05

            # Cap at 0.95 (never 100% confident from single source)
            confidence = min(confidence, 0.95)

            # Build canonical keys
            canonical_key_candidates = build_canonical_key_candidates(
                domain_or_website=repo.owner_website or repo.homepage or "",
                github_org=repo.org if repo.is_org_owned else "",
                github_repo=repo.repo_full_name,
                fallback_company_name=repo.owner_company or repo.org,
                fallback_region="",
            )

            canonical_key = canonical_key_candidates[0] if canonical_key_candidates else ""

            # Create signal
            signal_id = f"github_spike_{hashlib.sha256(repo.repo_full_name.encode()).hexdigest()[:12]}"

            signal = Signal(
                id=signal_id,
                signal_type="github_spike",
                confidence=confidence,
                source_api="github",
                source_url=repo.html_url,
                source_response_hash=hashlib.sha256(
                    str(repo.raw_repo_data).encode()
                ).hexdigest(),
                detected_at=datetime.now(timezone.utc),
                verified_by_sources=["github"],
                verification_status=VerificationStatus.SINGLE_SOURCE,
                raw_data={
                    # Core identifiers
                    "repo_full_name": repo.repo_full_name,
                    "github_org": repo.org,
                    "github_repo": repo.repo_full_name,
                    "canonical_key": canonical_key,
                    "canonical_key_candidates": canonical_key_candidates,

                    # Metrics
                    "stars": repo.stars,
                    "recent_stars": repo.recent_stars,
                    "growth_rate": repo.growth_rate,
                    "velocity_stars_per_day": repo.velocity_stars_per_day,
                    "forks": repo.forks,
                    "watchers": repo.watchers,
                    "open_issues": repo.open_issues,

                    # Metadata
                    "description": repo.description,
                    "language": repo.language,
                    "topics": repo.topics,
                    "created_at": repo.created_at.isoformat(),
                    "updated_at": repo.updated_at.isoformat(),
                    "pushed_at": repo.pushed_at.isoformat(),
                    "age_days": repo.age_days,

                    # Company info
                    "owner_type": repo.owner_type,
                    "owner_company": repo.owner_company,
                    "owner_bio": repo.owner_bio,
                    "owner_website": repo.owner_website,
                    "owner_email": repo.owner_email,

                    # URLs
                    "html_url": repo.html_url,
                    "homepage": repo.homepage,

                    # Why now / thesis fit
                    "why_now": self._generate_why_now(repo),
                    "thesis_fit": self._assess_thesis_fit(repo),
                }
            )

            signals.append(signal)

        return signals

    def _generate_why_now(self, repo: RepoMetrics) -> str:
        """
        Generate a "Why Now" narrative for the signal.

        Explains why this is an interesting signal right now.
        """
        parts = []

        if repo.recent_stars > 100:
            parts.append(f"Rapid adoption: +{repo.recent_stars} stars in {self.lookback_days} days")
        elif repo.recent_stars > 50:
            parts.append(f"Growing traction: +{repo.recent_stars} stars recently")

        if repo.growth_rate > 0.5:
            parts.append(f"{int(repo.growth_rate * 100)}% growth rate")

        if repo.age_days < 90:
            parts.append(f"New project ({repo.age_days} days old)")

        if repo.is_org_owned and repo.owner_company:
            parts.append(f"Backed by {repo.owner_company}")

        return "; ".join(parts) if parts else "Recent developer interest"

    def _assess_thesis_fit(self, repo: RepoMetrics) -> str:
        """
        Assess how well this signal fits Press On Ventures thesis.

        Returns:
            Thesis category based on topic_mode:
            - TECH: "AI Infrastructure", "Developer Tools", "Other"
            - CONSUMER: "Consumer CPG", "Consumer Health Tech", "Travel & Hospitality",
                        "Consumer Marketplaces", "Other"
        """
        repo_topics = {t.lower() for t in repo.topics}
        description = (repo.description or "").lower()

        # Consumer thesis categories (when in CONSUMER mode)
        if self.topic_mode == TopicMode.CONSUMER:
            # Consumer CPG
            cpg_keywords = {
                "food-delivery", "meal-kit", "grocery", "food-tech", "foodtech",
                "beverage", "snacks", "beauty", "skincare", "personal-care",
                "household", "subscription-box"
            }
            if repo_topics & cpg_keywords or any(k in description for k in ["meal kit", "food delivery", "grocery", "beauty", "skincare"]):
                return "Consumer CPG"

            # Consumer Health Tech
            health_keywords = {
                "fitness-app", "fitness", "wellness", "mental-health", "health-tech",
                "healthtech", "supplements", "wearables", "nutrition", "meditation"
            }
            if repo_topics & health_keywords or any(k in description for k in ["fitness", "wellness", "mental health", "health"]):
                return "Consumer Health Tech"

            # Travel & Hospitality
            travel_keywords = {
                "travel-booking", "travel", "hospitality", "restaurant", "experiences",
                "travel-tech", "traveltech", "booking", "hotels"
            }
            if repo_topics & travel_keywords or any(k in description for k in ["travel", "hospitality", "restaurant", "booking"]):
                return "Travel & Hospitality"

            # Consumer Marketplaces
            marketplace_keywords = {
                "marketplace", "consumer", "d2c", "dtc", "direct-to-consumer",
                "ecommerce", "retail-tech", "retailtech"
            }
            if repo_topics & marketplace_keywords or any(k in description for k in ["marketplace", "d2c", "dtc", "ecommerce"]):
                return "Consumer Marketplaces"

            return "Other"

        # Tech thesis categories (default TECH mode)
        # AI Infrastructure
        ai_infra_keywords = {"ai", "llm", "ml", "machine-learning", "embeddings", "vector-database"}
        if repo_topics & ai_infra_keywords or any(k in description for k in ["llm", "language model", "embeddings"]):
            return "AI Infrastructure"

        # Developer Tools
        devtools_keywords = {"developer-tools", "api", "sdk", "cli", "framework", "devops"}
        if repo_topics & devtools_keywords or any(k in description for k in ["developer", "api", "sdk"]):
            return "Developer Tools"

        # Default
        return "Other"

    async def compute_delta(
        self,
        current_repos: List[Dict[str, Any]],
        asset_store: "SourceAssetStore",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Compute delta between current repos and previous snapshot.

        Enables idempotent daily runs by detecting:
        - New repos (not in previous snapshot)
        - Changed repos (significant star increase)
        - Unchanged repos (minor or no changes)

        Args:
            current_repos: List of current repo data dicts from API.
            asset_store: SourceAssetStore instance for snapshot storage.

        Returns:
            Dict with keys:
            - "new": List of new repo dicts
            - "changed": List of repo dicts with significant changes
            - "unchanged": List of repo dicts with minor/no changes
        """
        from storage.source_asset_store import SourceAsset

        delta: Dict[str, List[Dict[str, Any]]] = {
            "new": [],
            "changed": [],
            "unchanged": [],
        }

        for repo in current_repos:
            full_name = repo.get("full_name", "")
            current_stars = repo.get("stargazers_count", 0)

            # Get previous snapshot
            previous = await asset_store.get_latest_snapshot(
                source_type="github_repo",
                external_id=full_name,
            )

            change_detected = False
            if previous is None:
                # New repo - not in previous snapshot
                delta["new"].append(repo)
                change_detected = True
            else:
                # Existing repo - check for significant change
                previous_stars = previous.get("stargazers_count", 0)

                if previous_stars > 0:
                    change_rate = (current_stars - previous_stars) / previous_stars
                else:
                    change_rate = 1.0 if current_stars > 0 else 0.0

                if change_rate >= self.star_change_threshold:
                    delta["changed"].append(repo)
                    change_detected = True
                else:
                    delta["unchanged"].append(repo)

            # Save current snapshot
            await asset_store.save_asset(SourceAsset(
                source_type="github_repo",
                external_id=full_name,
                raw_payload=repo,
                fetched_at=datetime.now(timezone.utc),
                change_detected=change_detected,
            ))

        logger.info(
            f"Delta computed: {len(delta['new'])} new, "
            f"{len(delta['changed'])} changed, "
            f"{len(delta['unchanged'])} unchanged"
        )

        return delta


# =============================================================================
# CLI / TESTING
# =============================================================================

async def main():
    """CLI entry point for testing"""
    import argparse

    parser = argparse.ArgumentParser(description="GitHub Signal Collector")
    parser.add_argument("--dry-run", action="store_true", help="Don't persist signals")
    parser.add_argument("--lookback-days", type=int, default=30, help="Days to look back")
    parser.add_argument("--max-repos", type=int, default=100, help="Max repos to analyze")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    collector = GitHubCollector(
        lookback_days=args.lookback_days,
        max_repos=args.max_repos,
    )

    result = await collector.run(dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print("GITHUB COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"Signals new: {result.signals_new}")
    print(f"Dry run: {result.dry_run}")
    if result.error_message:
        print(f"Errors: {result.error_message}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
