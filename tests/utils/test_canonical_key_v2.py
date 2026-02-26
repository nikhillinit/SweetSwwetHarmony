"""Tests for utils/canonical_key_v2.py.

Verifies:
- Domain extraction from company_url
- Multi-level TLD handling (.co.uk, .com.au)
- Publisher domain exclusion
- Plausible name heuristic
- Synthetic unlinked_buzz for public_buzz
- No synthetic for non-public_buzz
- Never raises on malformed input
"""

import pytest

from utils.canonical_key_v2 import (
    build_canonical_key_v2,
    extract_target_domain,
    is_plausible_company_name,
)


class TestExtractTargetDomain:
    """Domain extraction from raw signal data."""

    def test_extracts_domain_from_company_url(self):
        raw = {"company_url": "https://www.acme.ai/about"}
        assert extract_target_domain(raw) == "acme.ai"

    def test_extracts_from_company_domain(self):
        raw = {"company_domain": "example.com"}
        assert extract_target_domain(raw) == "example.com"

    def test_extracts_from_website(self):
        raw = {"website": "https://startup.io"}
        assert extract_target_domain(raw) == "startup.io"

    def test_extracts_from_homepage_url(self):
        raw = {"homepage_url": "https://myapp.co/home"}
        assert extract_target_domain(raw) == "myapp.co"

    def test_multi_level_tld_co_uk(self):
        raw = {"company_url": "https://britishstartup.co.uk/"}
        assert extract_target_domain(raw) == "britishstartup.co.uk"

    def test_multi_level_tld_com_au(self):
        raw = {"company_url": "https://ozstartup.com.au/products"}
        assert extract_target_domain(raw) == "ozstartup.com.au"

    def test_publisher_domain_excluded(self):
        """techcrunch.com is a publisher, not a target company."""
        raw = {"company_url": "https://techcrunch.com/2024/01/startup-funding"}
        assert extract_target_domain(raw) is None

    def test_github_domain_excluded(self):
        raw = {"company_url": "https://github.com/some-org"}
        assert extract_target_domain(raw) is None

    def test_empty_raw_data(self):
        assert extract_target_domain({}) is None

    def test_none_values(self):
        raw = {"company_url": None, "website": None}
        assert extract_target_domain(raw) is None


class TestIsPlausibleCompanyName:
    """Plausible company name heuristic per Appendix A."""

    def test_normal_company_name(self):
        assert is_plausible_company_name("Acme Inc") is True

    def test_single_word_real_name(self):
        assert is_plausible_company_name("Stripe") is True

    def test_single_word_stopword(self):
        assert is_plausible_company_name("the") is False

    def test_too_short(self):
        assert is_plausible_company_name("A") is False

    def test_too_long(self):
        assert is_plausible_company_name("A" * 61) is False

    def test_no_letters(self):
        assert is_plausible_company_name("123456") is False

    def test_too_many_tokens(self):
        assert is_plausible_company_name("one two three four five six seven") is False

    def test_six_tokens_ok(self):
        assert is_plausible_company_name("The Big Red Company of Today") is True

    def test_empty_string(self):
        assert is_plausible_company_name("") is False

    def test_none(self):
        assert is_plausible_company_name(None) is False

    def test_publisher_domain_rejected(self):
        assert is_plausible_company_name("techcrunch.com") is False


class TestBuildCanonicalKeyV2:
    """Full build_canonical_key_v2 integration tests."""

    def test_domain_from_company_url(self):
        key, key_type, reasons = build_canonical_key_v2(
            raw_data={"company_url": "https://acme.ai"},
            source_api="github",
            signal_type="github_spike",
            canonical_key="name_loc:acme",
        )
        assert key == "domain:acme.ai"
        assert key_type == "domain"

    def test_name_loc_from_company_name(self):
        key, key_type, reasons = build_canonical_key_v2(
            raw_data={"company_name": "Cool Startup"},
            source_api="job_postings",
            signal_type="hiring_signal",
            canonical_key="name_loc:cool-startup",
        )
        assert key is not None
        assert key_type in ("name_loc", "domain")

    def test_reuses_existing_name_loc_key(self):
        """When no domain and no company_name, reuse existing name_loc canonical_key."""
        key, key_type, reasons = build_canonical_key_v2(
            raw_data={},
            source_api="job_postings",
            signal_type="hiring_signal",
            canonical_key="name_loc:cool-startup|us",
        )
        assert key == "name_loc:cool-startup|us"
        assert key_type == "name_loc"

    def test_synthetic_unlinked_buzz_for_public_buzz(self):
        key, key_type, reasons = build_canonical_key_v2(
            raw_data={},  # No domain, no company_name
            source_api="hacker_news",
            signal_type="hacker_news_mention",
            canonical_key="hn:12345",
        )
        assert key is not None
        assert key.startswith("name_loc:unlinked_buzz_")
        assert key_type == "unlinked_buzz"

    def test_no_synthetic_for_non_public_buzz(self):
        """Non-public_buzz signals without domain/name return None."""
        key, key_type, reasons = build_canonical_key_v2(
            raw_data={},
            source_api="sec_edgar",
            signal_type="funding_event",
            canonical_key="sec:12345",
        )
        # Should try to reuse canonical_key (not name_loc:), no domain, regulatory family
        # So should return None since no name_loc to reuse and not public_buzz
        assert key is None
        assert key_type is None

    def test_never_raises_on_malformed_input(self):
        """Should never raise, even with garbage input."""
        key, key_type, reasons = build_canonical_key_v2(
            raw_data=None,
            source_api=None,
            signal_type=None,
            canonical_key=None,
        )
        # Should not raise
        assert isinstance(reasons, list)

    def test_json_string_raw_data(self):
        """raw_data as JSON string should be parsed."""
        import json
        raw = json.dumps({"company_url": "https://acme.ai"})
        key, key_type, reasons = build_canonical_key_v2(
            raw_data=raw,
            source_api="github",
            signal_type="github_spike",
            canonical_key="name_loc:acme",
        )
        assert key == "domain:acme.ai"
        assert key_type == "domain"
