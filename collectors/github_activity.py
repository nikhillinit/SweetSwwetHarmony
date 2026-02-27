"""
GitHub Activity Collector for Discovery Engine

Tracks: repo creation, commit spikes, org creation
API: GitHub REST API v3
Rate: 5,000 req/hr (authenticated)
Cost: FREE

when_to_use: When monitoring founder GitHub accounts for activity signals,
  detecting new repo creation, commit frequency spikes, or organization changes.

This collector is different from the main GitHubCollector (github.py):
- github.py: Discovers trending repos across GitHub
- github_activity.py: Monitors specific users/orgs for activity signals

Phase D: Team Shape Metrics (SHADOW mode)
- Computes contributor patterns to identify real startup teams
- 2-5 core contributors indicates a real startup team
- Logs to shadow_log for correlation analysis before promotion to ACTIVE

Phase E: Founder Surfaces (SHADOW mode)
- Scans profile README (username/username repo) for intent markers
- Scans public gists for commercial intent signals
- Extracts declared websites and social links
- Logs to shadow_log for correlation analysis before promotion to ACTIVE

Usage:
    collector = GitHubActivityCollector(
        usernames=["founder1", "founder2"],
        org_names=["startup-org"],
        include_team_shape=True,  # Enable team shape analysis
        include_founder_surfaces=True,  # Enable founder surface extraction
    )
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlparse

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.provenance import create_provenance, hash_response
from collectors.retry_strategy import RetryConfig
from collectors.source_types import SOURCE_TYPE
from storage.signal_store import SignalStore
from utils.feature_states import FeatureRegistry, FeatureState
from utils.team_shape import TeamShapeAnalyzer, TeamShapeMetrics
from utils.founder_surfaces import FounderSurfaceExtractor, FounderSurface
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
MIN_COMMITS_FOR_SPIKE = 50
MAX_LOOKBACK_DAYS = 180


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class GitHubActivitySignal:
    """GitHub activity signal data"""
    username: str
    signal_type: str  # new_repo, commit_spike, org_created
    repo_name: Optional[str] = None
    repo_url: Optional[str] = None
    created_at: Optional[datetime] = None
    commit_count_30d: int = 0
    website_url: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    stars: int = 0
    forks: int = 0
    raw_data: Dict[str, Any] = field(default_factory=dict)
    founder_surface: Optional[Dict[str, Any]] = None  # Phase E: Founder surface data

    @property
    def age_days(self) -> int:
        if not self.created_at:
            return 0
        return (datetime.now(timezone.utc) - self.created_at).days

    def calculate_signal_score(self, apply_founder_boost: bool = False) -> float:
        """Signal strength based on activity type and recency.

        Args:
            apply_founder_boost: If True and ACTIVE, apply confidence boost from
                                founder surface intent score
        """
        weights = {
            "new_repo": 0.6,
            "commit_spike": 0.7,
            "org_created": 0.8,
        }
        base = weights.get(self.signal_type, 0.5)

        # Recency boost
        if self.age_days <= 7:
            base += 0.1
        elif self.age_days <= 30:
            base += 0.05

        # Website = likely company
        if self.website_url:
            base += 0.1

        # Stars indicate traction
        if self.stars >= 100:
            base += 0.1
        elif self.stars >= 10:
            base += 0.05

        # Phase E: Founder surface boost (only when ACTIVE)
        if apply_founder_boost and self.founder_surface:
            intent_score = self.founder_surface.get("intent_score", 0)
            if intent_score > 0:
                # Up to 0.1 boost based on intent score
                base += min(intent_score * 0.1, 0.1)

        return min(base, 1.0)

    def to_signal(
        self,
        retrieved_at: Optional[datetime] = None,
        apply_founder_boost: bool = False,
    ) -> Signal:
        """Convert to verification gate Signal.

        Args:
            retrieved_at: Optional timestamp for when signal was retrieved
            apply_founder_boost: If True and founder_surfaces is ACTIVE, apply boost
        """
        confidence = self.calculate_signal_score(apply_founder_boost=apply_founder_boost)

        # Build canonical key and candidates
        candidates = []
        domain = ""
        if self.website_url:
            parsed = urlparse(self.website_url)
            domain = parsed.netloc.replace("www.", "").lower()
            if domain:
                canonical_key = f"domain:{domain}"
                candidates.append(f"domain:{domain}")
            else:
                canonical_key = f"github_user:{self.username.lower()}"
        else:
            canonical_key = f"github_user:{self.username.lower()}"
        candidates.append(f"github_user:{self.username.lower()}")

        # Build signal ID
        signal_id_parts = [
            "github",
            self.signal_type,
            self.username,
            self.repo_name or "activity",
        ]
        signal_id = "_".join(signal_id_parts)
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        # Use provided retrieved_at or default to now
        if retrieved_at is None:
            retrieved_at = datetime.now(timezone.utc)

        # Build source URL
        source_url = self.repo_url or f"https://github.com/{self.username}"

        # Create provenance for audit trail
        provenance = create_provenance(
            source_url=source_url,
            response_data=self.raw_data,
            endpoint=f"/users/{self.username}/events",
            query_params=None,
            retrieved_at=retrieved_at,
        )

        # Build raw_data dict
        signal_raw_data = {
            "canonical_key": canonical_key,
            "canonical_key_candidates": candidates,
            "username": self.username,
            "activity_type": self.signal_type,
            "repo_name": self.repo_name,
            "repo_url": self.repo_url,
            "website_url": self.website_url,
            "description": self.description,
            "language": self.language,
            "stars": self.stars,
            "forks": self.forks,
            "age_days": self.age_days,
            "commit_count_30d": self.commit_count_30d,
            **provenance,  # Add provenance block
        }

        # Phase E: Add founder surface if present
        if self.founder_surface:
            signal_raw_data["founder_surface"] = self.founder_surface

        return Signal(
            id=f"github_activity_{signal_hash}",
            signal_type="github_activity",
            confidence=confidence,
            source_api="github",
            source_url=source_url,
            source_response_hash=hash_response(self.raw_data),
            detected_at=self.created_at or retrieved_at,
            retrieved_at=retrieved_at,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["github"],
            raw_data=signal_raw_data,
        )


# =============================================================================
# COLLECTOR
# =============================================================================

class GitHubActivityCollector(BaseCollector):
    """
    Collector for GitHub activity signals.

    Monitors specific users/orgs for:
    - New repository creation
    - Commit activity spikes
    - Organization creation

    Optional Phase D: Team shape analysis (SHADOW mode)
    Optional Phase E: Founder surface extraction (SHADOW mode)

    Usage:
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            org_names=["startup-org"],
            include_founder_surfaces=True,
        )
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        usernames: Optional[List[str]] = None,
        org_names: Optional[List[str]] = None,
        store: Optional[SignalStore] = None,
        retry_config: Optional[RetryConfig] = None,
        github_token: Optional[str] = None,
        lookback_days: int = 90,
        include_team_shape: bool = False,
        include_founder_surfaces: bool = False,
        feature_registry: Optional[FeatureRegistry] = None,
    ):
        """
        Args:
            usernames: List of GitHub usernames to monitor
            org_names: List of GitHub org names to monitor
            store: Optional SignalStore for persistence
            retry_config: Configuration for retry behavior
            github_token: GitHub API token (or set GITHUB_TOKEN env var)
            lookback_days: How far back to look for activity
            include_team_shape: Enable team shape analysis (Phase D, SHADOW mode)
            include_founder_surfaces: Enable founder surface extraction (Phase E, SHADOW)
            feature_registry: Optional FeatureRegistry for feature state checks
        """
        super().__init__(
            store=store,
            collector_name="github_activity",
            retry_config=retry_config,
            api_name="github_activity",  # 5000/hour rate limit
        )
        self.usernames = usernames or []
        self.org_names = org_names or []
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN")
        self.lookback_days = lookback_days
        self.include_team_shape = include_team_shape
        self.include_founder_surfaces = include_founder_surfaces

        # Feature registry for checking ACTIVE/SHADOW/OFF states
        self.feature_registry = feature_registry or FeatureRegistry()

        # Initialize team shape analyzer if enabled
        if include_team_shape:
            self.team_shape_analyzer = TeamShapeAnalyzer()
        else:
            self.team_shape_analyzer = None

        # Initialize founder surface extractor if enabled
        if include_founder_surfaces:
            self._surface_extractor = FounderSurfaceExtractor(
                github_token=self.github_token,
            )
        else:
            self._surface_extractor = None

        # Cache for founder surfaces (avoid re-fetching per repo)
        self._surface_cache: Dict[str, Optional[FounderSurface]] = {}

    # BaseCollector provides __aenter__ and __aexit__ by default
    # We don't need custom async context manager since _http_get() handles HTTP clients

    # =========================================================================
    # Feature State Methods
    # =========================================================================

    def _should_extract_surfaces(self) -> bool:
        """Check if founder surfaces should be extracted.

        Returns True if:
        - include_founder_surfaces is True AND
        - founder_surfaces feature is ACTIVE or SHADOW (not OFF)
        """
        if not self.include_founder_surfaces:
            return False
        return self.feature_registry.is_enabled("founder_surfaces")

    def _is_founder_surfaces_active(self) -> bool:
        """Check if founder surfaces feature is in ACTIVE state (affects output)."""
        return self.feature_registry.is_active("founder_surfaces")

    def _is_founder_surfaces_shadow(self) -> bool:
        """Check if founder surfaces feature is in SHADOW state (log only)."""
        return self.feature_registry.is_shadow("founder_surfaces")

    # =========================================================================
    # Team Shape Methods (Phase D - SHADOW mode)
    # =========================================================================

    async def _fetch_contributor_stats(
        self,
        owner: str,
        repo: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch contributor statistics from GitHub API.

        Uses /repos/{owner}/{repo}/stats/contributors endpoint.
        Note: GitHub may return 202 while computing stats - handle with retry.

        Args:
            owner: Repository owner (username or org)
            repo: Repository name

        Returns:
            List of contributor stats or None if unavailable
        """
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/stats/contributors"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        try:
            result = await self._http_get(url=url, headers=headers)
            # GitHub returns list directly for this endpoint
            if isinstance(result, list):
                return result
            return None
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 202:
                # Stats are being computed - could retry, but for now return None
                logger.debug(f"Stats being computed for {owner}/{repo}")
                return None
            elif e.response.status_code == 404:
                logger.debug(f"Stats not found for {owner}/{repo}")
                return None
            elif e.response.status_code == 403:
                # Rate limit or access denied
                logger.warning(f"Access denied for {owner}/{repo} stats")
                return None
            else:
                logger.warning(f"Error fetching stats for {owner}/{repo}: {e}")
                return None
        except Exception as e:
            logger.error(f"Error fetching contributor stats for {owner}/{repo}: {e}")
            return None

    def _analyze_team_shape(
        self,
        contributor_data: Optional[List[Dict[str, Any]]],
    ) -> TeamShapeMetrics:
        """
        Analyze team shape from contributor data.

        Args:
            contributor_data: List from GitHub stats/contributors API

        Returns:
            TeamShapeMetrics with analysis results
        """
        if not self.team_shape_analyzer:
            self.team_shape_analyzer = TeamShapeAnalyzer()
        return self.team_shape_analyzer.analyze_from_contributor_stats(contributor_data)

    async def _log_team_shape_shadow(
        self,
        canonical_key: str,
        team_shape_metrics: TeamShapeMetrics,
        signal_id: Optional[int] = None,
    ) -> None:
        """
        Log team shape metrics to shadow_log when in SHADOW mode.

        Args:
            canonical_key: Company/entity identifier
            team_shape_metrics: Computed team shape metrics
            signal_id: Optional FK to link to a specific signal
        """
        if not self.store:
            logger.debug("No store configured, skipping shadow log")
            return

        # Only log if team_shape feature is enabled (ACTIVE or SHADOW)
        if not self.feature_registry.is_enabled("team_shape"):
            return

        try:
            await self.store.log_shadow_computation(
                feature_name="team_shape",
                canonical_key=canonical_key,
                computed_value=team_shape_metrics.to_dict(),
                signal_id=signal_id,
            )
            logger.debug(f"Logged team_shape shadow computation for {canonical_key}")
        except Exception as e:
            logger.error(f"Failed to log team_shape shadow computation: {e}")

    # =========================================================================
    # Founder Surfaces Methods (Phase E - SHADOW mode)
    # =========================================================================

    async def _extract_founder_surface(
        self,
        username: str,
    ) -> Optional[FounderSurface]:
        """
        Extract founder surface for a GitHub user.

        Uses cache to avoid re-fetching for multiple repos from same user.

        Args:
            username: GitHub username

        Returns:
            FounderSurface or None if extraction fails
        """
        # Check cache first
        if username in self._surface_cache:
            return self._surface_cache[username]

        if not self._surface_extractor:
            return None

        try:
            surface = await self._surface_extractor.extract(username)
            self._surface_cache[username] = surface
            return surface
        except Exception as e:
            logger.error(f"Error extracting founder surface for {username}: {e}")
            self._surface_cache[username] = None
            return None

    async def _log_founder_surface_shadow(
        self,
        canonical_key: str,
        founder_surface: FounderSurface,
        signal_id: Optional[int] = None,
    ) -> None:
        """
        Log founder surface to shadow_log when in SHADOW mode.

        Only logs if founder_surfaces feature is in SHADOW state.
        Does NOT log when ACTIVE (because it affects output, not shadow).

        Args:
            canonical_key: Company/entity identifier
            founder_surface: Extracted founder surface data
            signal_id: Optional FK to link to a specific signal
        """
        if not self.store:
            logger.debug("No store configured, skipping shadow log")
            return

        # Only log if in SHADOW mode (not ACTIVE, not OFF)
        if not self._is_founder_surfaces_shadow():
            return

        try:
            await self.store.log_shadow_computation(
                feature_name="founder_surfaces",
                canonical_key=canonical_key,
                computed_value=founder_surface.to_dict(),
                signal_id=signal_id,
            )
            logger.debug(f"Logged founder_surfaces shadow computation for {canonical_key}")
        except Exception as e:
            logger.error(f"Failed to log founder_surfaces shadow computation: {e}")

    async def check_user(self, username: str) -> List[GitHubActivitySignal]:
        """
        Check user for recent activity signals.

        Args:
            username: GitHub username to check

        Returns:
            List of activity signals
        """
        signals = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        try:
            # Get user's repos using BaseCollector's _http_get (includes retry + rate limiting)
            url = f"{GITHUB_API_BASE}/users/{username}/repos"
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.github_token:
                headers["Authorization"] = f"token {self.github_token}"

            repos = await self._http_get(
                url=url,
                headers=headers,
                params={"sort": "created", "per_page": 30}
            )

            for repo in repos:
                # Skip forks
                if repo.get("fork"):
                    continue

                # Parse creation date
                created_str = repo.get("created_at", "")
                if not created_str:
                    continue

                try:
                    created = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    continue

                # Only include recent repos
                if created >= cutoff:
                    signals.append(GitHubActivitySignal(
                        username=username,
                        signal_type="new_repo",
                        repo_name=repo.get("name"),
                        repo_url=repo.get("html_url"),
                        created_at=created,
                        website_url=repo.get("homepage"),
                        description=repo.get("description"),
                        language=repo.get("language"),
                        stars=repo.get("stargazers_count", 0),
                        forks=repo.get("forks_count", 0),
                        raw_data=repo,
                    ))

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"User not found: {username}")
            else:
                logger.warning(
                    f"Error fetching repos for {username}: "
                    f"HTTP {e.response.status_code}"
                )
        except Exception as e:
            logger.error(f"Error checking user {username}: {e}")

        return signals

    async def check_org(self, org_name: str) -> List[GitHubActivitySignal]:
        """
        Check organization for recent activity signals.

        Args:
            org_name: GitHub organization name to check

        Returns:
            List of activity signals
        """
        signals = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

        try:
            # Get org's repos using BaseCollector's _http_get (includes retry + rate limiting)
            url = f"{GITHUB_API_BASE}/orgs/{org_name}/repos"
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.github_token:
                headers["Authorization"] = f"token {self.github_token}"

            repos = await self._http_get(
                url=url,
                headers=headers,
                params={"sort": "created", "per_page": 30}
            )

            for repo in repos:
                # Skip forks
                if repo.get("fork"):
                    continue

                # Parse creation date
                created_str = repo.get("created_at", "")
                if not created_str:
                    continue

                try:
                    created = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    continue

                # Only include recent repos
                if created >= cutoff:
                    owner = repo.get("owner", {})
                    signals.append(GitHubActivitySignal(
                        username=owner.get("login", org_name),
                        signal_type="new_repo",
                        repo_name=repo.get("name"),
                        repo_url=repo.get("html_url"),
                        created_at=created,
                        website_url=repo.get("homepage"),
                        description=repo.get("description"),
                        language=repo.get("language"),
                        stars=repo.get("stargazers_count", 0),
                        forks=repo.get("forks_count", 0),
                        raw_data=repo,
                    ))

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Organization not found: {org_name}")
            else:
                logger.warning(
                    f"Error fetching repos for org {org_name}: "
                    f"HTTP {e.response.status_code}"
                )
        except Exception as e:
            logger.error(f"Error checking org {org_name}: {e}")

        return signals

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect GitHub activity signals from configured users and orgs.

        Implements BaseCollector._collect_signals() abstract method.

        Returns:
            List of Signal objects for activity signals found
        """
        signals: List[Signal] = []
        should_extract_surfaces = self._should_extract_surfaces()
        surfaces_active = self._is_founder_surfaces_active()

        # Check users
        for username in self.usernames:
            try:
                user_signals = await self.check_user(username)

                # Phase E: Extract founder surface once per user (if enabled)
                founder_surface: Optional[FounderSurface] = None
                if should_extract_surfaces and user_signals:
                    founder_surface = await self._extract_founder_surface(username)

                for signal_obj in user_signals:
                    # Save raw data and detect changes
                    if self.asset_store:
                        is_new, changes = await self._save_asset_with_change_detection(
                            source_type=self.SOURCE_TYPE,
                            external_id=username,
                            raw_data=signal_obj.to_dict() if hasattr(signal_obj, 'to_dict') else vars(signal_obj),
                        )

                        # Skip unchanged users
                        if not is_new and not changes:
                            logger.debug(f"Skipping unchanged GitHub user: {username}")
                            continue

                    # Phase E: Add founder surface to signal
                    if founder_surface:
                        signal_obj.founder_surface = founder_surface.to_dict()

                    # Convert to Signal (apply boost only if ACTIVE)
                    signal = signal_obj.to_signal(apply_founder_boost=surfaces_active)
                    signals.append(signal)

                    # Phase E: Log to shadow_log if in SHADOW mode
                    if founder_surface:
                        canonical_key = signal.raw_data.get("canonical_key", f"github_user:{username}")
                        await self._log_founder_surface_shadow(
                            canonical_key=canonical_key,
                            founder_surface=founder_surface,
                        )

                # Rate limiting is handled by BaseCollector._http_get()
            except Exception as e:
                # BaseCollector tracks errors, but we log for debugging
                logger.error(f"Error checking user {username}: {e}")

        # Check orgs
        for org in self.org_names:
            try:
                org_signals = await self.check_org(org)
                for signal_obj in org_signals:
                    # Save raw data and detect changes
                    if self.asset_store:
                        is_new, changes = await self._save_asset_with_change_detection(
                            source_type=self.SOURCE_TYPE,
                            external_id=org,
                            raw_data=signal_obj.to_dict() if hasattr(signal_obj, 'to_dict') else vars(signal_obj),
                        )

                        # Skip unchanged orgs
                        if not is_new and not changes:
                            logger.debug(f"Skipping unchanged GitHub org: {org}")
                            continue

                    signals.append(signal_obj.to_signal())
                # Rate limiting is handled by BaseCollector._http_get()
            except Exception as e:
                # BaseCollector tracks errors, but we log for debugging
                logger.error(f"Error checking org {org}: {e}")

        return signals


# =============================================================================
# CLI / TESTING
# =============================================================================

async def main():
    """CLI entry point for testing"""
    import argparse

    parser = argparse.ArgumentParser(description="GitHub Activity Collector")
    parser.add_argument("--users", type=str, help="Comma-separated usernames")
    parser.add_argument("--orgs", type=str, help="Comma-separated org names")
    parser.add_argument("--lookback-days", type=int, default=90, help="Lookback days")
    parser.add_argument("--founder-surfaces", action="store_true", help="Enable founder surfaces")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    usernames = args.users.split(",") if args.users else []
    org_names = args.orgs.split(",") if args.orgs else []

    if not usernames and not org_names:
        print("Usage: python github_activity.py --users=user1,user2 --orgs=org1,org2")
        return

    collector = GitHubActivityCollector(
        usernames=usernames,
        org_names=org_names,
        lookback_days=args.lookback_days,
        include_founder_surfaces=args.founder_surfaces,
    )
    result = await collector.run(dry_run=True)

    print("\n" + "=" * 60)
    print("GITHUB ACTIVITY COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    if result.error_message:
        print(f"Errors: {result.error_message}")

    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
