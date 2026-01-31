"""Founder Surfaces Extractor for Discovery Engine.

Phase E: Extract founder intent signals from GitHub profiles.

Scans:
- Profile README (username/username repo)
- Public gists for intent markers
- User profile for social links and company info

Key Signals:
- has_profile_readme: User has a profile README repo
- profile_intent_markers: Commercial intent phrases in README
- declared_websites: URLs from profile README (non-GitHub)
- gist_count: Number of public gists
- gist_intent_markers: Intent phrases in gist descriptions
- social_links: Twitter, LinkedIn, personal site

Usage:
    from utils.founder_surfaces import FounderSurfaceExtractor

    extractor = FounderSurfaceExtractor(github_token="ghp_xxx")
    surface = await extractor.extract("founder123")

    if surface.has_commercial_intent:
        print(f"Found founder signals: {surface.profile_intent_markers}")
"""

from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# Commercial intent markers for founder detection
# Based on thesis_matcher.py INTENT_PHRASES + founder-specific markers
FOUNDER_INTENT_MARKERS: List[str] = [
    # Waitlist/access markers
    "join waitlist",
    "join our waitlist",
    "request access",
    "get early access",
    "early access",
    "private beta",
    "closed beta",
    # Launch markers
    "coming soon",
    "launching soon",
    "launch soon",
    "launching",
    # Commercial markers
    "pricing",
    "subscribe",
    "subscription",
    "premium",
    "pro tier",
    "enterprise",
    # Founder role markers
    "founder",
    "co-founder",
    "cofounder",
    "ceo",
    "cto",
    "building",
    "i'm building",
    "we're building",
    # Product markers
    "sign up",
    "get started",
    "try it",
    "book a demo",
    "schedule a demo",
]

# Regex pattern for URL extraction
URL_PATTERN = re.compile(
    r'https?://[^\s<>\[\]()"\'\`]+',
    re.IGNORECASE
)

# LinkedIn URL pattern
LINKEDIN_PATTERN = re.compile(
    r'linkedin\.com/in/([a-zA-Z0-9_-]+)',
    re.IGNORECASE
)


@dataclass
class FounderSurface:
    """Extracted founder surface data from GitHub profile.

    Attributes:
        username: GitHub username
        has_profile_readme: Whether user has username/username repo with README
        profile_readme_content: Raw README content (if exists)
        profile_intent_markers: Commercial intent phrases found in README
        declared_websites: Non-GitHub URLs found in profile README
        gist_count: Number of public gists
        recent_gists: Recent gist metadata (id, description, created_at)
        gist_intent_markers: Intent phrases found in gist descriptions
        social_links: Social media links (twitter, linkedin, website)
        bio: User bio from profile
        company: Company from profile
    """
    username: str
    has_profile_readme: bool
    gist_count: int = 0
    profile_readme_content: Optional[str] = None
    profile_intent_markers: List[str] = field(default_factory=list)
    declared_websites: List[str] = field(default_factory=list)
    recent_gists: List[Dict[str, Any]] = field(default_factory=list)
    gist_intent_markers: List[str] = field(default_factory=list)
    social_links: Dict[str, str] = field(default_factory=dict)
    bio: Optional[str] = None
    company: Optional[str] = None

    @property
    def has_commercial_intent(self) -> bool:
        """Returns True if any commercial intent markers were found."""
        return bool(self.profile_intent_markers or self.gist_intent_markers)

    @property
    def intent_score(self) -> float:
        """Calculate aggregate intent score from markers.

        Returns:
            Score from 0.0 to 1.0 based on marker count
        """
        total_markers = len(self.profile_intent_markers) + len(self.gist_intent_markers)
        if total_markers == 0:
            return 0.0
        # Cap at 5 markers for max score
        return min(total_markers / 5.0, 1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage/logging."""
        return {
            "username": self.username,
            "has_profile_readme": self.has_profile_readme,
            "profile_readme_length": len(self.profile_readme_content) if self.profile_readme_content else 0,
            "profile_intent_markers": self.profile_intent_markers,
            "declared_websites": self.declared_websites,
            "gist_count": self.gist_count,
            "recent_gists_count": len(self.recent_gists),
            "gist_intent_markers": self.gist_intent_markers,
            "social_links": self.social_links,
            "bio": self.bio,
            "company": self.company,
            "has_commercial_intent": self.has_commercial_intent,
            "intent_score": self.intent_score,
        }


class FounderSurfaceExtractor:
    """Extracts founder surface signals from GitHub profiles.

    Fetches:
    1. User profile (bio, company, social links)
    2. Profile README (username/username repo)
    3. Public gists

    Extracts intent markers and declared websites for founder detection.
    """

    def __init__(
        self,
        github_token: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """Initialize the extractor.

        Args:
            github_token: GitHub API token (or uses GITHUB_TOKEN env var)
            timeout: HTTP request timeout in seconds
        """
        self._github_token = github_token or os.environ.get("GITHUB_TOKEN")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for GitHub API requests."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._github_token:
            headers["Authorization"] = f"token {self._github_token}"
        return headers

    async def _http_get(self, url: str) -> Any:
        """Make GET request to GitHub API.

        Args:
            url: Full URL to fetch

        Returns:
            Parsed JSON response

        Raises:
            Exception: On HTTP errors
        """
        client = await self._get_client()
        response = await client.get(url, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    async def _fetch_user_profile(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch user profile from GitHub API.

        Args:
            username: GitHub username

        Returns:
            User profile dict or None if not found
        """
        try:
            url = f"{GITHUB_API_BASE}/users/{username}"
            return await self._http_get(url)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"User not found: {username}")
            else:
                logger.warning(f"Error fetching profile for {username}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching profile for {username}: {e}")
            return None

    async def _fetch_profile_readme(self, username: str) -> Optional[str]:
        """Fetch profile README content from username/username repo.

        Args:
            username: GitHub username

        Returns:
            README content as string, or None if not found
        """
        try:
            url = f"{GITHUB_API_BASE}/repos/{username}/{username}/readme"
            data = await self._http_get(url)

            # Decode base64 content
            content = data.get("content", "")
            encoding = data.get("encoding", "base64")

            if encoding == "base64" and content:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                return decoded

            return content if content else None

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"No profile README for {username}")
            else:
                logger.warning(f"Error fetching README for {username}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching profile README for {username}: {e}")
            return None

    async def _fetch_gists(self, username: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch public gists for a user.

        Args:
            username: GitHub username
            limit: Maximum number of gists to fetch

        Returns:
            List of gist metadata dicts
        """
        try:
            url = f"{GITHUB_API_BASE}/users/{username}/gists"
            params = f"?per_page={limit}"
            gists = await self._http_get(url + params)
            return gists[:limit] if isinstance(gists, list) else []
        except Exception as e:
            logger.debug(f"Error fetching gists for {username}: {e}")
            return []

    def _extract_intent_markers(self, text: Optional[str]) -> List[str]:
        """Extract commercial intent markers from text.

        Args:
            text: Text to scan for intent markers

        Returns:
            List of matched intent markers
        """
        if not text:
            return []

        text_lower = text.lower()
        found_markers = []

        for marker in FOUNDER_INTENT_MARKERS:
            marker_lower = marker.lower()
            # Use word boundary matching for multi-word markers
            if " " in marker_lower:
                if marker_lower in text_lower:
                    found_markers.append(marker)
            else:
                # For single words, use word boundary regex
                pattern = r'\b' + re.escape(marker_lower) + r'\b'
                if re.search(pattern, text_lower):
                    found_markers.append(marker)

        return found_markers

    def _extract_urls(self, text: Optional[str]) -> List[str]:
        """Extract non-GitHub URLs from text.

        Args:
            text: Text to scan for URLs

        Returns:
            List of unique non-GitHub URLs
        """
        if not text:
            return []

        urls = URL_PATTERN.findall(text)

        # Filter out GitHub URLs and deduplicate
        filtered_urls = []
        seen = set()
        for url in urls:
            # Clean URL (remove trailing punctuation)
            url = url.rstrip('.,;:!?)]\'"')

            # Skip GitHub URLs
            if "github.com" in url.lower() or "githubusercontent.com" in url.lower():
                continue

            if url not in seen:
                seen.add(url)
                filtered_urls.append(url)

        return filtered_urls

    def _extract_social_links(
        self,
        profile: Dict[str, Any],
        readme_content: Optional[str],
    ) -> Dict[str, str]:
        """Extract social links from profile and README.

        Args:
            profile: GitHub user profile dict
            readme_content: Profile README content

        Returns:
            Dict of social platform -> username/URL
        """
        links: Dict[str, str] = {}

        # From profile
        if profile.get("twitter_username"):
            links["twitter"] = profile["twitter_username"]

        if profile.get("blog"):
            links["website"] = profile["blog"]

        # From README - look for LinkedIn
        if readme_content:
            linkedin_match = LINKEDIN_PATTERN.search(readme_content)
            if linkedin_match:
                links["linkedin"] = linkedin_match.group(1)

        return links

    def _extract_gist_intent_markers(self, gists: List[Dict[str, Any]]) -> List[str]:
        """Extract intent markers from gist descriptions.

        Args:
            gists: List of gist metadata dicts

        Returns:
            List of intent markers found in gist descriptions
        """
        all_markers = []
        seen = set()

        for gist in gists:
            description = gist.get("description") or ""
            markers = self._extract_intent_markers(description)
            for marker in markers:
                if marker not in seen:
                    seen.add(marker)
                    all_markers.append(marker)

        return all_markers

    async def extract(self, username: str) -> Optional[FounderSurface]:
        """Extract founder surface data for a GitHub user.

        Args:
            username: GitHub username

        Returns:
            FounderSurface with extracted data, or None if user not found
        """
        try:
            # Fetch user profile
            profile = await self._fetch_user_profile(username)
            if not profile:
                return None

            # Fetch profile README
            readme_content = await self._fetch_profile_readme(username)

            # Fetch gists
            gists = await self._fetch_gists(username, limit=10)

            # Extract data
            profile_markers = self._extract_intent_markers(readme_content)
            bio_markers = self._extract_intent_markers(profile.get("bio"))

            # Combine profile and bio markers
            all_profile_markers = list(set(profile_markers + bio_markers))

            declared_websites = self._extract_urls(readme_content)
            social_links = self._extract_social_links(profile, readme_content)
            gist_markers = self._extract_gist_intent_markers(gists)

            # Build recent gists summary
            recent_gists = [
                {
                    "id": g.get("id"),
                    "description": g.get("description"),
                    "created_at": g.get("created_at"),
                }
                for g in gists
            ]

            return FounderSurface(
                username=username,
                has_profile_readme=readme_content is not None,
                profile_readme_content=readme_content,
                profile_intent_markers=all_profile_markers,
                declared_websites=declared_websites,
                gist_count=profile.get("public_gists", 0),
                recent_gists=recent_gists,
                gist_intent_markers=gist_markers,
                social_links=social_links,
                bio=profile.get("bio"),
                company=profile.get("company"),
            )

        except TimeoutError as e:
            logger.warning(f"Timeout extracting founder surface for {username}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error extracting founder surface for {username}: {e}")
            return None

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
