"""Tests for Founder Surfaces Extractor.

TDD tests for Phase E: Founder surface extraction in SHADOW mode.
Scans GitHub profile READMEs and gists for founder intent signals.
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from utils.founder_surfaces import (
    FounderSurface,
    FounderSurfaceExtractor,
    FOUNDER_INTENT_MARKERS,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def extractor():
    """Create a FounderSurfaceExtractor instance."""
    return FounderSurfaceExtractor()


@pytest.fixture
def extractor_with_token():
    """Create an extractor with a GitHub token."""
    return FounderSurfaceExtractor(github_token="test_token_123")


@pytest.fixture
def sample_user_profile():
    """Sample GitHub user profile API response."""
    return {
        "login": "founder123",
        "name": "Jane Founder",
        "bio": "Building the future of fitness. CEO @fittech. Join our waitlist!",
        "company": "FitTech Inc",
        "blog": "https://fittech.io",
        "twitter_username": "janefounder",
        "location": "San Francisco, CA",
        "public_repos": 25,
        "public_gists": 5,
        "created_at": "2020-01-15T00:00:00Z",
        "html_url": "https://github.com/founder123",
    }


@pytest.fixture
def sample_profile_readme():
    """Sample profile README content."""
    return """
# Hi, I'm Jane!

I'm the **CEO and co-founder** of [FitTech](https://fittech.io).

## What I'm building

A fitness platform that helps you achieve your goals.

**Join our waitlist**: [fittech.io/waitlist](https://fittech.io/waitlist)

Coming soon: Mobile app!

## Connect with me
- Twitter: [@janefounder](https://twitter.com/janefounder)
- LinkedIn: [linkedin.com/in/janefounder](https://linkedin.com/in/janefounder)
- Website: [janedoe.com](https://janedoe.com)
"""


@pytest.fixture
def sample_gists():
    """Sample public gists API response."""
    return [
        {
            "id": "gist1",
            "description": "Pricing calculator for FitTech premium tier",
            "created_at": "2024-01-10T10:00:00Z",
            "updated_at": "2024-01-10T10:00:00Z",
            "public": True,
            "html_url": "https://gist.github.com/founder123/gist1",
            "files": {
                "pricing.py": {"filename": "pricing.py", "size": 1500}
            },
        },
        {
            "id": "gist2",
            "description": "Demo script for investors",
            "created_at": "2024-01-05T10:00:00Z",
            "updated_at": "2024-01-05T10:00:00Z",
            "public": True,
            "html_url": "https://gist.github.com/founder123/gist2",
            "files": {
                "demo.md": {"filename": "demo.md", "size": 800}
            },
        },
        {
            "id": "gist3",
            "description": "Private beta signup automation",
            "created_at": "2024-01-01T10:00:00Z",
            "updated_at": "2024-01-01T10:00:00Z",
            "public": True,
            "html_url": "https://gist.github.com/founder123/gist3",
            "files": {
                "signup.sh": {"filename": "signup.sh", "size": 500}
            },
        },
    ]


# =============================================================================
# FOUNDER SURFACE DATACLASS TESTS
# =============================================================================

class TestFounderSurface:
    """Tests for FounderSurface dataclass."""

    def test_create_minimal_surface(self):
        """Can create a minimal founder surface."""
        surface = FounderSurface(
            username="testuser",
            has_profile_readme=False,
            gist_count=0,
        )
        assert surface.username == "testuser"
        assert surface.has_profile_readme is False
        assert surface.gist_count == 0

    def test_create_full_surface(self):
        """Can create a fully populated founder surface."""
        surface = FounderSurface(
            username="founder123",
            has_profile_readme=True,
            profile_readme_content="# Hello",
            profile_intent_markers=["join waitlist", "coming soon"],
            declared_websites=["https://fittech.io"],
            gist_count=5,
            recent_gists=[{"id": "g1", "description": "test"}],
            gist_intent_markers=["pricing"],
            social_links={"twitter": "janefounder", "linkedin": "janefounder"},
            bio="Building FitTech",
            company="FitTech Inc",
        )
        assert surface.has_profile_readme is True
        assert len(surface.profile_intent_markers) == 2
        assert len(surface.declared_websites) == 1
        assert surface.gist_count == 5

    def test_to_dict(self):
        """FounderSurface can serialize to dict."""
        surface = FounderSurface(
            username="founder123",
            has_profile_readme=True,
            gist_count=3,
            profile_intent_markers=["waitlist"],
        )
        d = surface.to_dict()
        assert d["username"] == "founder123"
        assert d["has_profile_readme"] is True
        assert d["gist_count"] == 3
        assert "waitlist" in d["profile_intent_markers"]

    def test_has_commercial_intent_true(self):
        """has_commercial_intent returns True when intent markers found."""
        surface = FounderSurface(
            username="founder123",
            has_profile_readme=True,
            gist_count=0,
            profile_intent_markers=["join waitlist"],
        )
        assert surface.has_commercial_intent is True

    def test_has_commercial_intent_false(self):
        """has_commercial_intent returns False with no markers."""
        surface = FounderSurface(
            username="hobbyist",
            has_profile_readme=True,
            gist_count=0,
            profile_intent_markers=[],
            gist_intent_markers=[],
        )
        assert surface.has_commercial_intent is False

    def test_has_commercial_intent_from_gists(self):
        """has_commercial_intent considers gist markers too."""
        surface = FounderSurface(
            username="founder123",
            has_profile_readme=False,
            gist_count=1,
            profile_intent_markers=[],
            gist_intent_markers=["pricing"],
        )
        assert surface.has_commercial_intent is True

    def test_intent_score_calculation(self):
        """intent_score aggregates markers."""
        surface = FounderSurface(
            username="founder123",
            has_profile_readme=True,
            gist_count=2,
            profile_intent_markers=["join waitlist", "coming soon"],
            gist_intent_markers=["pricing"],
        )
        # 3 markers should give non-zero score
        assert surface.intent_score > 0

    def test_intent_score_zero_no_markers(self):
        """intent_score is 0 with no markers."""
        surface = FounderSurface(
            username="hobbyist",
            has_profile_readme=True,
            gist_count=0,
        )
        assert surface.intent_score == 0


# =============================================================================
# INTENT MARKERS TESTS
# =============================================================================

class TestIntentMarkers:
    """Tests for intent marker definitions."""

    def test_intent_markers_exist(self):
        """FOUNDER_INTENT_MARKERS constant is defined."""
        assert FOUNDER_INTENT_MARKERS is not None
        assert len(FOUNDER_INTENT_MARKERS) > 0

    def test_waitlist_markers_present(self):
        """Waitlist-related markers are present."""
        markers_lower = [m.lower() for m in FOUNDER_INTENT_MARKERS]
        assert any("waitlist" in m for m in markers_lower)

    def test_pricing_markers_present(self):
        """Pricing-related markers are present."""
        markers_lower = [m.lower() for m in FOUNDER_INTENT_MARKERS]
        assert any("pricing" in m for m in markers_lower)

    def test_beta_markers_present(self):
        """Beta-related markers are present."""
        markers_lower = [m.lower() for m in FOUNDER_INTENT_MARKERS]
        assert any("beta" in m for m in markers_lower)

    def test_founder_role_markers_present(self):
        """Founder role markers are present."""
        markers_lower = [m.lower() for m in FOUNDER_INTENT_MARKERS]
        assert any("founder" in m or "ceo" in m for m in markers_lower)


# =============================================================================
# EXTRACTOR INITIALIZATION TESTS
# =============================================================================

class TestFounderSurfaceExtractorInit:
    """Tests for extractor initialization."""

    def test_init_without_token(self):
        """Extractor initializes without GitHub token."""
        with patch.dict("os.environ", {}, clear=True):
            # Remove GITHUB_TOKEN if set
            os.environ.pop("GITHUB_TOKEN", None)
            extractor = FounderSurfaceExtractor()
            assert extractor._github_token is None

    def test_init_with_token(self):
        """Extractor initializes with GitHub token."""
        extractor = FounderSurfaceExtractor(github_token="test_token")
        assert extractor._github_token == "test_token"

    def test_init_from_env_token(self):
        """Extractor picks up GITHUB_TOKEN from environment."""
        with patch.dict("os.environ", {"GITHUB_TOKEN": "env_token_123"}):
            extractor = FounderSurfaceExtractor()
            assert extractor._github_token == "env_token_123"

    def test_init_explicit_token_overrides_env(self):
        """Explicit token overrides environment variable."""
        with patch.dict("os.environ", {"GITHUB_TOKEN": "env_token"}):
            extractor = FounderSurfaceExtractor(github_token="explicit_token")
            assert extractor._github_token == "explicit_token"


# =============================================================================
# INTENT EXTRACTION TESTS
# =============================================================================

class TestIntentExtraction:
    """Tests for _extract_intent_markers method."""

    def test_extract_waitlist_marker(self, extractor):
        """Detects 'join waitlist' intent marker."""
        text = "Join our waitlist at example.com"
        markers = extractor._extract_intent_markers(text)
        assert any("waitlist" in m.lower() for m in markers)

    def test_extract_pricing_marker(self, extractor):
        """Detects 'pricing' intent marker."""
        text = "Check out our pricing page for details"
        markers = extractor._extract_intent_markers(text)
        assert any("pricing" in m.lower() for m in markers)

    def test_extract_beta_marker(self, extractor):
        """Detects 'private beta' intent marker."""
        text = "We're in private beta, request access now"
        markers = extractor._extract_intent_markers(text)
        assert any("beta" in m.lower() for m in markers)

    def test_extract_coming_soon_marker(self, extractor):
        """Detects 'coming soon' intent marker."""
        text = "New features coming soon!"
        markers = extractor._extract_intent_markers(text)
        assert any("coming soon" in m.lower() for m in markers)

    def test_extract_founder_marker(self, extractor):
        """Detects founder role markers."""
        text = "I'm the CEO and co-founder of TechCorp"
        markers = extractor._extract_intent_markers(text)
        assert len(markers) > 0

    def test_extract_building_marker(self, extractor):
        """Detects 'building' intent marker."""
        text = "Currently building a fitness app"
        markers = extractor._extract_intent_markers(text)
        assert any("building" in m.lower() for m in markers)

    def test_extract_multiple_markers(self, extractor):
        """Extracts multiple markers from text."""
        text = """
        I'm the founder of FitApp. Join our waitlist!
        We're in private beta and launching soon.
        Check our pricing page.
        """
        markers = extractor._extract_intent_markers(text)
        assert len(markers) >= 3

    def test_no_markers_in_plain_text(self, extractor):
        """Returns empty list for text without markers."""
        text = "I like to code Python in my spare time."
        markers = extractor._extract_intent_markers(text)
        assert markers == []

    def test_case_insensitive_extraction(self, extractor):
        """Marker extraction is case-insensitive."""
        text = "JOIN OUR WAITLIST NOW!"
        markers = extractor._extract_intent_markers(text)
        assert len(markers) > 0


# =============================================================================
# URL EXTRACTION TESTS
# =============================================================================

class TestURLExtraction:
    """Tests for _extract_urls method."""

    def test_extract_https_url(self, extractor):
        """Extracts HTTPS URLs."""
        text = "Check out https://example.com for more"
        urls = extractor._extract_urls(text)
        assert "https://example.com" in urls

    def test_extract_http_url(self, extractor):
        """Extracts HTTP URLs."""
        text = "Visit http://example.com"
        urls = extractor._extract_urls(text)
        assert "http://example.com" in urls

    def test_extract_multiple_urls(self, extractor):
        """Extracts multiple URLs."""
        text = """
        Website: https://fittech.io
        Blog: https://blog.fittech.io
        Demo: https://demo.fittech.io
        """
        urls = extractor._extract_urls(text)
        assert len(urls) == 3

    def test_extract_url_with_path(self, extractor):
        """Extracts URLs with paths."""
        text = "See https://example.com/path/to/page"
        urls = extractor._extract_urls(text)
        assert any("/path/to/page" in u for u in urls)

    def test_no_urls_in_plain_text(self, extractor):
        """Returns empty list for text without URLs."""
        text = "Just some plain text without links"
        urls = extractor._extract_urls(text)
        assert urls == []

    def test_deduplicates_urls(self, extractor):
        """Removes duplicate URLs."""
        text = """
        Link: https://example.com
        Same link: https://example.com
        """
        urls = extractor._extract_urls(text)
        assert urls.count("https://example.com") == 1

    def test_filters_github_urls(self, extractor):
        """Filters out GitHub URLs (not declared websites)."""
        text = """
        My project: https://github.com/user/repo
        My site: https://mycompany.com
        """
        urls = extractor._extract_urls(text)
        assert not any("github.com" in u for u in urls)
        assert any("mycompany.com" in u for u in urls)


# =============================================================================
# SOCIAL LINKS EXTRACTION TESTS
# =============================================================================

class TestSocialLinksExtraction:
    """Tests for _extract_social_links method."""

    def test_extract_twitter_username(self, extractor, sample_user_profile):
        """Extracts Twitter username from profile."""
        links = extractor._extract_social_links(sample_user_profile, None)
        assert links.get("twitter") == "janefounder"

    def test_extract_website_from_blog(self, extractor, sample_user_profile):
        """Extracts website from blog field."""
        links = extractor._extract_social_links(sample_user_profile, None)
        assert links.get("website") == "https://fittech.io"

    def test_extract_linkedin_from_readme(self, extractor, sample_user_profile, sample_profile_readme):
        """Extracts LinkedIn from profile README."""
        links = extractor._extract_social_links(sample_user_profile, sample_profile_readme)
        assert "linkedin" in links
        assert "janefounder" in links["linkedin"]

    def test_no_social_links(self, extractor):
        """Returns empty dict for profile without social links."""
        profile = {"login": "anon", "public_repos": 1}
        links = extractor._extract_social_links(profile, None)
        assert links == {} or all(v is None for v in links.values())


# =============================================================================
# PROFILE README FETCH TESTS
# =============================================================================

class TestProfileReadmeFetch:
    """Tests for _fetch_profile_readme method."""

    @pytest.mark.asyncio
    async def test_fetch_readme_success(self, extractor_with_token):
        """Successfully fetches profile README."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": "IyBIZWxsbywgV29ybGQh",  # Base64 for "# Hello, World!"
            "encoding": "base64",
        }

        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response.json.return_value
            content = await extractor_with_token._fetch_profile_readme("founder123")
            assert content is not None
            assert "Hello" in content

    @pytest.mark.asyncio
    async def test_fetch_readme_not_found(self, extractor_with_token):
        """Returns None when profile README doesn't exist."""
        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("404 Not Found")
            content = await extractor_with_token._fetch_profile_readme("noreadme")
            assert content is None

    @pytest.mark.asyncio
    async def test_fetch_readme_handles_error(self, extractor_with_token):
        """Gracefully handles API errors."""
        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("Rate limit exceeded")
            content = await extractor_with_token._fetch_profile_readme("ratelimited")
            assert content is None


# =============================================================================
# GIST FETCH TESTS
# =============================================================================

class TestGistFetch:
    """Tests for _fetch_gists method."""

    @pytest.mark.asyncio
    async def test_fetch_gists_success(self, extractor_with_token, sample_gists):
        """Successfully fetches user gists."""
        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = sample_gists
            gists = await extractor_with_token._fetch_gists("founder123", limit=10)
            assert len(gists) == 3
            assert gists[0]["id"] == "gist1"

    @pytest.mark.asyncio
    async def test_fetch_gists_limit(self, extractor_with_token, sample_gists):
        """Respects gist limit parameter."""
        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = sample_gists[:2]
            gists = await extractor_with_token._fetch_gists("founder123", limit=2)
            assert len(gists) <= 2

    @pytest.mark.asyncio
    async def test_fetch_gists_empty(self, extractor_with_token):
        """Returns empty list for user with no gists."""
        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            gists = await extractor_with_token._fetch_gists("nogists")
            assert gists == []

    @pytest.mark.asyncio
    async def test_fetch_gists_handles_error(self, extractor_with_token):
        """Gracefully handles API errors."""
        with patch.object(extractor_with_token, '_http_get', new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("API error")
            gists = await extractor_with_token._fetch_gists("erroruser")
            assert gists == []


# =============================================================================
# FULL EXTRACTION TESTS
# =============================================================================

class TestFullExtraction:
    """Tests for the full extract() method."""

    @pytest.mark.asyncio
    async def test_extract_full_profile(
        self, extractor_with_token, sample_user_profile, sample_profile_readme, sample_gists
    ):
        """Extracts complete founder surface from profile."""
        with patch.object(extractor_with_token, '_fetch_user_profile', new_callable=AsyncMock) as mock_profile, \
             patch.object(extractor_with_token, '_fetch_profile_readme', new_callable=AsyncMock) as mock_readme, \
             patch.object(extractor_with_token, '_fetch_gists', new_callable=AsyncMock) as mock_gists:

            mock_profile.return_value = sample_user_profile
            mock_readme.return_value = sample_profile_readme
            mock_gists.return_value = sample_gists

            surface = await extractor_with_token.extract("founder123")

            assert surface.username == "founder123"
            assert surface.has_profile_readme is True
            assert surface.gist_count == 5  # From profile
            assert len(surface.recent_gists) == 3
            assert surface.company == "FitTech Inc"
            assert surface.has_commercial_intent is True

    @pytest.mark.asyncio
    async def test_extract_minimal_profile(self, extractor_with_token):
        """Handles users with minimal profile."""
        minimal_profile = {
            "login": "minimaluser",
            "public_repos": 1,
            "public_gists": 0,
        }

        with patch.object(extractor_with_token, '_fetch_user_profile', new_callable=AsyncMock) as mock_profile, \
             patch.object(extractor_with_token, '_fetch_profile_readme', new_callable=AsyncMock) as mock_readme, \
             patch.object(extractor_with_token, '_fetch_gists', new_callable=AsyncMock) as mock_gists:

            mock_profile.return_value = minimal_profile
            mock_readme.return_value = None
            mock_gists.return_value = []

            surface = await extractor_with_token.extract("minimaluser")

            assert surface.username == "minimaluser"
            assert surface.has_profile_readme is False
            assert surface.gist_count == 0
            assert surface.has_commercial_intent is False

    @pytest.mark.asyncio
    async def test_extract_user_not_found(self, extractor_with_token):
        """Returns None for non-existent user."""
        with patch.object(extractor_with_token, '_fetch_user_profile', new_callable=AsyncMock) as mock_profile:
            mock_profile.return_value = None

            surface = await extractor_with_token.extract("nonexistent")
            assert surface is None

    @pytest.mark.asyncio
    async def test_extract_without_token(self, extractor):
        """Works without token (lower rate limits)."""
        profile = {
            "login": "publicuser",
            "public_repos": 10,
            "public_gists": 2,
        }

        with patch.object(extractor, '_fetch_user_profile', new_callable=AsyncMock) as mock_profile, \
             patch.object(extractor, '_fetch_profile_readme', new_callable=AsyncMock) as mock_readme, \
             patch.object(extractor, '_fetch_gists', new_callable=AsyncMock) as mock_gists:

            mock_profile.return_value = profile
            mock_readme.return_value = None
            mock_gists.return_value = []

            surface = await extractor.extract("publicuser")
            assert surface.username == "publicuser"


# =============================================================================
# GIST INTENT MARKER TESTS
# =============================================================================

class TestGistIntentMarkers:
    """Tests for extracting intent markers from gists."""

    def test_extract_from_gist_descriptions(self, extractor, sample_gists):
        """Extracts intent markers from gist descriptions."""
        markers = extractor._extract_gist_intent_markers(sample_gists)
        # "pricing" and "private beta" should be found
        assert len(markers) >= 1

    def test_no_markers_in_technical_gists(self, extractor):
        """No markers from purely technical gist descriptions."""
        technical_gists = [
            {"id": "1", "description": "Python utility functions"},
            {"id": "2", "description": "Bash script for backup"},
        ]
        markers = extractor._extract_gist_intent_markers(technical_gists)
        assert markers == []


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_extract_urls_handles_none(self, extractor):
        """_extract_urls handles None input."""
        urls = extractor._extract_urls(None)
        assert urls == []

    def test_extract_markers_handles_none(self, extractor):
        """_extract_intent_markers handles None input."""
        markers = extractor._extract_intent_markers(None)
        assert markers == []

    def test_extract_urls_handles_empty(self, extractor):
        """_extract_urls handles empty string."""
        urls = extractor._extract_urls("")
        assert urls == []

    def test_extract_markers_handles_empty(self, extractor):
        """_extract_intent_markers handles empty string."""
        markers = extractor._extract_intent_markers("")
        assert markers == []

    @pytest.mark.asyncio
    async def test_extract_handles_timeout(self, extractor_with_token):
        """extract() handles timeout gracefully."""
        with patch.object(extractor_with_token, '_fetch_user_profile', new_callable=AsyncMock) as mock_profile:
            mock_profile.side_effect = TimeoutError("Request timeout")

            surface = await extractor_with_token.extract("timeoutuser")
            assert surface is None
