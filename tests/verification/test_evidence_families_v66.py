"""Tests for verification/evidence_families.py.

Verifies:
- All 24 known signal types are mapped
- Unknown types return "unknown" (never "public_buzz")
- Source API overrides work correctly
"""

import pytest

from verification.evidence_families import get_family, VALID_FAMILIES


# Exhaustive list of known signal types from all 16 collectors
KNOWN_SIGNAL_TYPES = [
    ("github_spike", "github", "developer"),
    ("github_activity", "github_activity", "developer"),
    ("new_repo", "github_activity", "developer"),
    ("commit_spike", "github_activity", "developer"),
    ("org_created", "github_activity", "developer"),
    ("funding_event", "sec_edgar", "regulatory"),
    ("incorporation", "companies_house", "regulatory"),
    ("incorporation", "opencorporates", "regulatory"),
    ("patent_filing", "uspto", "regulatory"),
    ("crunchbase_funding", "crunchbase", "regulatory"),
    ("crunchbase_company", "crunchbase", "web_presence"),
    ("domain_registration", "domain_whois", "web_presence"),
    ("linkedin_company", "linkedin", "web_presence"),
    ("hiring_signal", "job_postings", "hiring"),
    ("linkedin_job_posting", "linkedin", "hiring"),
    ("product_hunt_launch", "product_hunt", "public_buzz"),
    ("hacker_news_mention", "hacker_news", "public_buzz"),
    ("news_mention", "news_api", "public_buzz"),
    ("news_mention", "rss_feeds", "public_buzz"),
    ("funding_announcement", "news_api", "public_buzz"),
    ("product_launch", "news_api", "public_buzz"),
    ("press_release", "rss_feeds", "public_buzz"),
    ("research_paper", "arxiv", "developer"),
    ("funding_news", "discord", "public_buzz"),
    ("feedback_request", "discord", "public_buzz"),
    ("hunter_discovery", "active_hunter", "public_buzz"),
]


class TestAllCollectorSignalTypesMapped:
    """Every known signal type returns a valid non-unknown family."""

    @pytest.mark.parametrize("signal_type,source_api,expected", KNOWN_SIGNAL_TYPES)
    def test_mapped(self, signal_type, source_api, expected):
        result = get_family(signal_type, source_api)
        assert result == expected, (
            f"get_family({signal_type!r}, {source_api!r}) = {result!r}, expected {expected!r}"
        )
        assert result in VALID_FAMILIES


class TestUnknownNeverReturnsPublicBuzz:
    """Invariant #4: unknown signal types return 'unknown', never 'public_buzz'."""

    @pytest.mark.parametrize("signal_type", [
        "totally_bogus",
        "new_signal_type_2027",
        "",
        "funding_whatever",
    ])
    def test_unknown_returns_unknown(self, signal_type):
        result = get_family(signal_type, "unknown_source")
        assert result == "unknown", f"Expected 'unknown' for {signal_type!r}, got {result!r}"
        assert result != "public_buzz"


class TestSourceApiOverrides:
    """Source API overrides for ambiguous signal types."""

    def test_funding_announcement_sec_edgar_is_regulatory(self):
        assert get_family("funding_announcement", "sec_edgar") == "regulatory"

    def test_funding_announcement_news_api_is_public_buzz(self):
        assert get_family("funding_announcement", "news_api") == "public_buzz"

    def test_funding_event_news_api_is_public_buzz(self):
        assert get_family("funding_event", "news_api") == "public_buzz"

    def test_funding_event_sec_edgar_is_regulatory(self):
        """Without override, funding_event default is 'regulatory'."""
        assert get_family("funding_event", "sec_edgar") == "regulatory"

    def test_funding_event_rss_feeds_is_public_buzz(self):
        assert get_family("funding_event", "rss_feeds") == "public_buzz"
