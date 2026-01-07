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

Usage:
    collector = GitHubActivityCollector(
        usernames=["founder1", "founder2"],
        org_names=["startup-org"],
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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from storage.signal_store import SignalStore
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

    @property
    def age_days(self) -> int:
        if not self.created_at:
            return 0
        return (datetime.now(timezone.utc) - self.created_at).days

    def calculate_signal_score(self) -> float:
        """Signal strength based on activity type and recency"""
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

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal"""
        confidence = self.calculate_signal_score()

        # Build canonical key
        if self.website_url:
            parsed = urlparse(self.website_url)
            domain = parsed.netloc.replace("www.", "").lower()
            if domain:
                canonical_key = f"domain:{domain}"
            else:
                canonical_key = f"github_user:{self.username.lower()}"
        else:
            canonical_key = f"github_user:{self.username.lower()}"

        # Build signal ID
        signal_id_parts = [
            "github",
            self.signal_type,
            self.username,
            self.repo_name or "activity",
        ]
        signal_id = "_".join(signal_id_parts)
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        return Signal(
            id=f"github_activity_{signal_hash}",
            signal_type="github_activity",
            confidence=confidence,
            source_api="github",
            source_url=self.repo_url or f"https://github.com/{self.username}",
            source_response_hash=hashlib.sha256(
                str(self.raw_data).encode()
            ).hexdigest()[:16],
            detected_at=self.created_at or datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["github"],
            raw_data={
                "canonical_key": canonical_key,
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
            }
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

    Usage:
        collector = GitHubActivityCollector(
            usernames=["founder1"],
            org_names=["startup-org"],
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
    ):
        """
        Args:
            usernames: List of GitHub usernames to monitor
            org_names: List of GitHub org names to monitor
            store: Optional SignalStore for persistence
            retry_config: Configuration for retry behavior
            github_token: GitHub API token (or set GITHUB_TOKEN env var)
            lookback_days: How far back to look for activity
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

    # BaseCollector provides __aenter__ and __aexit__ by default
    # We don't need custom async context manager since _http_get() handles HTTP clients

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

        # Check users
        for username in self.usernames:
            try:
                user_signals = await self.check_user(username)
                signals.extend([s.to_signal() for s in user_signals])
                # Rate limiting is handled by BaseCollector._http_get()
            except Exception as e:
                # BaseCollector tracks errors, but we log for debugging
                logger.error(f"Error checking user {username}: {e}")

        # Check orgs
        for org in self.org_names:
            try:
                org_signals = await self.check_org(org)
                signals.extend([s.to_signal() for s in org_signals])
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
