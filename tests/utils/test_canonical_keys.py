"""
Comprehensive tests for canonical_keys.py

Tests cover:
- All normalizer functions (domain, companies_house, crunchbase, pitchbook, github)
- Canonical key builders (build_canonical_key, build_canonical_key_candidates)
- Convenience wrappers (canonical_key_from_external_refs, canonical_key_from_signal)
- CanonicalKeyResult dataclass properties
- Key strength helpers (is_strong_key, get_key_strength_score)
- Edge cases and boundary conditions
"""

import pytest
from utils.canonical_keys import (
    # Normalizers
    normalize_domain,
    normalize_companies_house_number,
    normalize_crunchbase_id,
    normalize_pitchbook_id,
    normalize_github_org,
    normalize_github_repo,
    _slug,
    # Builders
    build_canonical_key,
    build_canonical_key_candidates,
    # Convenience wrappers
    CanonicalKeyResult,
    canonical_key_from_external_refs,
    canonical_key_from_signal,
    # Strength helpers
    is_strong_key,
    get_key_strength_score,
    STRONG_KEY_PREFIXES,
    WEAK_KEY_PREFIXES,
)


# =============================================================================
# TEST: _slug helper function
# =============================================================================

class TestSlugHelper:
    """Tests for the _slug internal helper function"""

    def test_slug_basic_lowercase(self):
        """Should lowercase input"""
        assert _slug("Hello") == "hello"
        assert _slug("UPPERCASE") == "uppercase"

    def test_slug_removes_special_chars(self):
        """Should replace non-alphanumeric with hyphens"""
        assert _slug("hello world") == "hello-world"
        assert _slug("hello.world") == "hello-world"
        assert _slug("hello_world") == "hello-world"

    def test_slug_collapses_multiple_separators(self):
        """Should collapse multiple separators into single hyphen"""
        assert _slug("hello   world") == "hello-world"
        assert _slug("hello...world") == "hello-world"
        assert _slug("hello___world") == "hello-world"

    def test_slug_strips_leading_trailing_hyphens(self):
        """Should strip leading and trailing hyphens"""
        assert _slug("-hello-") == "hello"
        assert _slug("--hello--") == "hello"
        assert _slug("...hello...") == "hello"

    def test_slug_empty_input(self):
        """Should handle empty and None input"""
        assert _slug("") == ""
        assert _slug(None) == ""
        assert _slug("   ") == ""

    def test_slug_only_special_chars(self):
        """Should return empty string for only special chars"""
        assert _slug("...") == ""
        assert _slug("___") == ""
        assert _slug("@#$%") == ""

    def test_slug_preserves_numbers(self):
        """Should preserve numbers"""
        assert _slug("hello123") == "hello123"
        assert _slug("123hello") == "123hello"
        assert _slug("hello 123 world") == "hello-123-world"

    def test_slug_unicode_characters(self):
        """Should handle unicode by removing non-ascii"""
        assert _slug("héllo") == "h-llo"
        assert _slug("日本語") == ""


# =============================================================================
# TEST: normalize_domain
# =============================================================================

class TestNormalizeDomain:
    """Tests for domain normalization"""

    def test_full_url_with_https(self):
        """Should extract domain from full HTTPS URL"""
        assert normalize_domain("https://www.example.com/path?q=1") == "example.com"
        assert normalize_domain("https://example.com/") == "example.com"

    def test_full_url_with_http(self):
        """Should extract domain from HTTP URL"""
        assert normalize_domain("http://example.com") == "example.com"
        assert normalize_domain("http://EXAMPLE.COM") == "example.com"

    def test_domain_only_input(self):
        """Should handle domain-only input (no scheme)"""
        assert normalize_domain("example.com") == "example.com"
        assert normalize_domain("example.com/") == "example.com"
        assert normalize_domain("example.com/path") == "example.com"

    def test_strips_www_prefix(self):
        """Should strip www. prefix"""
        assert normalize_domain("www.example.com") == "example.com"
        assert normalize_domain("https://www.example.com") == "example.com"

    def test_lowercases_domain(self):
        """Should lowercase domain"""
        assert normalize_domain("EXAMPLE.COM") == "example.com"
        assert normalize_domain("Example.Com") == "example.com"

    def test_strips_port(self):
        """Should strip port number"""
        assert normalize_domain("example.com:8080") == "example.com"
        assert normalize_domain("https://example.com:443/path") == "example.com"

    def test_strips_auth(self):
        """Should strip auth credentials"""
        assert normalize_domain("user:pass@example.com") == "example.com"
        assert normalize_domain("https://user:pass@example.com") == "example.com"

    def test_strips_leading_dots(self):
        """Should strip leading dots"""
        assert normalize_domain(".example.com") == "example.com"
        assert normalize_domain("..example.com") == "example.com"

    def test_empty_input(self):
        """Should return empty string for empty input"""
        assert normalize_domain("") == ""
        assert normalize_domain("   ") == ""
        assert normalize_domain(None) == ""

    def test_no_dot_returns_empty(self):
        """Should return empty if no dot (not a valid domain)"""
        assert normalize_domain("localhost") == ""
        assert normalize_domain("example") == ""

    def test_multi_level_tlds(self):
        """Should handle multi-level TLDs correctly"""
        # Note: This implementation does NOT strip multi-level TLDs to root
        # It preserves the full domain as given
        assert normalize_domain("example.co.uk") == "example.co.uk"
        assert normalize_domain("www.example.co.uk") == "example.co.uk"
        assert normalize_domain("sub.example.co.uk") == "sub.example.co.uk"

    def test_subdomains_preserved(self):
        """Should preserve subdomains (except www)"""
        assert normalize_domain("api.example.com") == "api.example.com"
        assert normalize_domain("mail.example.com") == "mail.example.com"

    def test_ip_addresses(self):
        """Should handle IP addresses (technically valid)"""
        # IPs have dots, so they pass the basic check
        assert normalize_domain("192.168.1.1") == "192.168.1.1"

    def test_complex_urls(self):
        """Should handle complex URLs with query params and fragments"""
        assert normalize_domain("https://www.example.com/path?foo=bar&baz=qux#section") == "example.com"
        assert normalize_domain("https://example.com?q=1") == "example.com"


# =============================================================================
# TEST: normalize_companies_house_number
# =============================================================================

class TestNormalizeCompaniesHouseNumber:
    """Tests for UK Companies House number normalization"""

    def test_standard_numeric(self):
        """Should handle standard numeric company numbers"""
        assert normalize_companies_house_number("12345678") == "12345678"
        assert normalize_companies_house_number("  12345678  ") == "12345678"

    def test_scottish_prefix(self):
        """Should lowercase Scottish company prefixes"""
        assert normalize_companies_house_number("SC123456") == "sc123456"
        assert normalize_companies_house_number("sc123456") == "sc123456"

    def test_northern_ireland_prefix(self):
        """Should lowercase and strip dashes from NI numbers"""
        assert normalize_companies_house_number("NI123456") == "ni123456"
        assert normalize_companies_house_number("NI-123456") == "ni123456"
        assert normalize_companies_house_number("NI-123-456") == "ni123456"

    def test_other_prefixes(self):
        """Should handle other valid prefixes"""
        # OC = LLP, FC = Foreign Company, etc.
        assert normalize_companies_house_number("OC123456") == "oc123456"
        assert normalize_companies_house_number("FC123456") == "fc123456"

    def test_strips_non_alphanumeric(self):
        """Should strip all non-alphanumeric characters"""
        assert normalize_companies_house_number("12.345.678") == "12345678"
        assert normalize_companies_house_number("12/345/678") == "12345678"
        assert normalize_companies_house_number("12_345_678") == "12345678"

    def test_empty_input(self):
        """Should return empty string for empty input"""
        assert normalize_companies_house_number("") == ""
        assert normalize_companies_house_number("   ") == ""
        assert normalize_companies_house_number(None) == ""

    def test_whitespace_only_special_chars(self):
        """Should return empty for whitespace-only or special-char-only input"""
        assert normalize_companies_house_number("---") == ""
        assert normalize_companies_house_number("...") == ""


# =============================================================================
# TEST: normalize_crunchbase_id
# =============================================================================

class TestNormalizeCrunchbaseId:
    """Tests for Crunchbase ID normalization"""

    def test_basic_lowercase(self):
        """Should lowercase Crunchbase IDs"""
        assert normalize_crunchbase_id("Anthropic") == "anthropic"
        assert normalize_crunchbase_id("OPENAI") == "openai"

    def test_slug_ids(self):
        """Should preserve slug format"""
        assert normalize_crunchbase_id("anthropic-ai") == "anthropic-ai"
        assert normalize_crunchbase_id("open-ai") == "open-ai"

    def test_strips_whitespace(self):
        """Should strip leading/trailing whitespace"""
        assert normalize_crunchbase_id("  anthropic  ") == "anthropic"

    def test_empty_input(self):
        """Should handle empty input"""
        assert normalize_crunchbase_id("") == ""
        assert normalize_crunchbase_id(None) == ""


# =============================================================================
# TEST: normalize_pitchbook_id
# =============================================================================

class TestNormalizePitchbookId:
    """Tests for PitchBook ID normalization"""

    def test_basic_lowercase(self):
        """Should lowercase PitchBook IDs"""
        assert normalize_pitchbook_id("ABC123") == "abc123"

    def test_strips_whitespace(self):
        """Should strip whitespace"""
        assert normalize_pitchbook_id("  abc123  ") == "abc123"

    def test_empty_input(self):
        """Should handle empty input"""
        assert normalize_pitchbook_id("") == ""
        assert normalize_pitchbook_id(None) == ""


# =============================================================================
# TEST: normalize_github_org
# =============================================================================

class TestNormalizeGithubOrg:
    """Tests for GitHub organization name normalization"""

    def test_basic_lowercase(self):
        """Should lowercase org names"""
        assert normalize_github_org("Anthropic") == "anthropic"
        assert normalize_github_org("OpenAI") == "openai"

    def test_preserves_hyphens(self):
        """Should preserve hyphens via slug"""
        assert normalize_github_org("Anthropic-AI") == "anthropic-ai"

    def test_strips_whitespace(self):
        """Should strip whitespace"""
        assert normalize_github_org("  OpenAI  ") == "openai"

    def test_special_chars_to_hyphen(self):
        """Should convert special chars to hyphens"""
        assert normalize_github_org("open_ai") == "open-ai"
        assert normalize_github_org("open.ai") == "open-ai"

    def test_empty_input(self):
        """Should handle empty input"""
        assert normalize_github_org("") == ""
        assert normalize_github_org(None) == ""


# =============================================================================
# TEST: normalize_github_repo
# =============================================================================

class TestNormalizeGithubRepo:
    """Tests for GitHub repo normalization"""

    def test_simple_org_repo_format(self):
        """Should normalize simple org/repo format"""
        assert normalize_github_repo("Anthropic/claude") == "anthropic/claude"
        assert normalize_github_repo("OpenAI/gpt-4") == "openai/gpt-4"

    def test_full_github_url(self):
        """Should extract org/repo from full GitHub URL"""
        assert normalize_github_repo("https://github.com/OpenAI/gpt-4") == "openai/gpt-4"
        assert normalize_github_repo("http://github.com/Anthropic/claude") == "anthropic/claude"

    def test_github_url_without_scheme(self):
        """Should handle github.com URL without scheme"""
        assert normalize_github_repo("github.com/OpenAI/gpt-4") == "openai/gpt-4"

    def test_github_url_with_trailing_path(self):
        """Should extract org/repo even with trailing path"""
        assert normalize_github_repo("https://github.com/OpenAI/gpt-4/tree/main") == "openai/gpt-4"
        assert normalize_github_repo("https://github.com/OpenAI/gpt-4/blob/main/README.md") == "openai/gpt-4"

    def test_github_url_with_git_extension(self):
        """Should handle .git extension in repo name"""
        # The slug function will convert .git to just the repo name
        result = normalize_github_repo("https://github.com/OpenAI/gpt-4.git")
        assert result == "openai/gpt-4-git"  # .git becomes -git via slug

    def test_missing_repo(self):
        """Should return empty if repo is missing"""
        assert normalize_github_repo("Anthropic") == ""
        assert normalize_github_repo("Anthropic/") == ""
        assert normalize_github_repo("https://github.com/Anthropic") == ""

    def test_missing_org(self):
        """Should return empty if org is missing"""
        assert normalize_github_repo("/claude") == ""

    def test_empty_input(self):
        """Should handle empty input"""
        assert normalize_github_repo("") == ""
        assert normalize_github_repo(None) == ""

    def test_extra_slashes(self):
        """Should handle extra slashes"""
        assert normalize_github_repo("Anthropic//claude") == "anthropic/claude"


# =============================================================================
# TEST: build_canonical_key
# =============================================================================

class TestBuildCanonicalKey:
    """Tests for build_canonical_key function"""

    def test_domain_takes_priority(self):
        """Domain should take priority over all other identifiers"""
        key = build_canonical_key(
            domain_or_website="https://acme.ai",
            companies_house_number="12345678",
            crunchbase_id="acme",
            github_org="acme-ai"
        )
        assert key == "domain:acme.ai"

    def test_companies_house_second_priority(self):
        """Companies House should be second priority after domain"""
        key = build_canonical_key(
            companies_house_number="12345678",
            crunchbase_id="acme",
            github_org="acme-ai"
        )
        assert key == "companies_house:12345678"

    def test_crunchbase_third_priority(self):
        """Crunchbase should be third priority"""
        key = build_canonical_key(
            crunchbase_id="acme",
            pitchbook_id="pb123",
            github_org="acme-ai"
        )
        assert key == "crunchbase:acme"

    def test_pitchbook_fourth_priority(self):
        """PitchBook should be fourth priority"""
        key = build_canonical_key(
            pitchbook_id="pb123",
            github_org="acme-ai"
        )
        assert key == "pitchbook:pb123"

    def test_github_org_fifth_priority(self):
        """GitHub org should be fifth priority"""
        key = build_canonical_key(
            github_org="acme-ai",
            github_repo="acme/product"
        )
        assert key == "github_org:acme-ai"

    def test_github_repo_sixth_priority(self):
        """GitHub repo should be sixth priority"""
        key = build_canonical_key(
            github_repo="acme/product"
        )
        assert key == "github_repo:acme/product"

    def test_name_loc_fallback(self):
        """name_loc should be last resort fallback"""
        key = build_canonical_key(
            fallback_company_name="Acme Inc",
            fallback_region="US-CA"
        )
        assert key == "name_loc:acme-inc|us-ca"

    def test_name_loc_without_region(self):
        """name_loc should work without region"""
        key = build_canonical_key(fallback_company_name="Acme Inc")
        assert key == "name_loc:acme-inc"

    def test_empty_returns_empty(self):
        """Should return empty string if no valid identifiers"""
        key = build_canonical_key()
        assert key == ""

    def test_empty_strings_ignored(self):
        """Empty string values should be ignored"""
        key = build_canonical_key(
            domain_or_website="",
            companies_house_number="12345678"
        )
        assert key == "companies_house:12345678"


# =============================================================================
# TEST: build_canonical_key_candidates
# =============================================================================

class TestBuildCanonicalKeyCandidates:
    """Tests for build_canonical_key_candidates function"""

    def test_returns_all_candidates_in_order(self):
        """Should return all valid candidates in priority order"""
        candidates = build_canonical_key_candidates(
            domain_or_website="https://acme.ai",
            companies_house_number="12345678",
            github_org="acme-ai"
        )
        assert candidates == [
            "domain:acme.ai",
            "companies_house:12345678",
            "github_org:acme-ai"
        ]

    def test_deduplicates_candidates(self):
        """Should deduplicate identical candidates"""
        # If the same key would be generated twice, it should appear only once
        candidates = build_canonical_key_candidates(
            domain_or_website="acme.ai",
            crunchbase_id="acme"
        )
        # Each should appear once
        assert candidates.count("domain:acme.ai") == 1
        assert candidates.count("crunchbase:acme") == 1

    def test_empty_values_excluded(self):
        """Empty values should not generate candidates"""
        candidates = build_canonical_key_candidates(
            domain_or_website="",
            companies_house_number="12345678"
        )
        assert candidates == ["companies_house:12345678"]

    def test_full_candidate_list(self):
        """Should include all identifier types when provided"""
        candidates = build_canonical_key_candidates(
            domain_or_website="acme.ai",
            companies_house_number="12345678",
            crunchbase_id="acme-cb",
            pitchbook_id="acme-pb",
            github_org="acme",
            github_repo="acme/product",
            fallback_company_name="Acme Inc",
            fallback_region="US"
        )
        assert len(candidates) == 7
        assert candidates[0] == "domain:acme.ai"
        assert candidates[1] == "companies_house:12345678"
        assert candidates[2] == "crunchbase:acme-cb"
        assert candidates[3] == "pitchbook:acme-pb"
        assert candidates[4] == "github_org:acme"
        assert candidates[5] == "github_repo:acme/product"
        assert candidates[6] == "name_loc:acme-inc|us"

    def test_empty_returns_empty_list(self):
        """Should return empty list if no valid identifiers"""
        candidates = build_canonical_key_candidates()
        assert candidates == []


# =============================================================================
# TEST: CanonicalKeyResult dataclass
# =============================================================================

class TestCanonicalKeyResult:
    """Tests for CanonicalKeyResult dataclass"""

    def test_has_strong_key_true_for_domain(self):
        """has_strong_key should be True for domain keys"""
        result = CanonicalKeyResult(
            canonical_key="domain:acme.ai",
            candidates=["domain:acme.ai"]
        )
        assert result.has_strong_key is True

    def test_has_strong_key_true_for_companies_house(self):
        """has_strong_key should be True for companies_house keys"""
        result = CanonicalKeyResult(
            canonical_key="companies_house:12345678",
            candidates=["companies_house:12345678"]
        )
        assert result.has_strong_key is True

    def test_has_strong_key_false_for_name_loc(self):
        """has_strong_key should be False for name_loc keys"""
        result = CanonicalKeyResult(
            canonical_key="name_loc:acme|us",
            candidates=["name_loc:acme|us"]
        )
        assert result.has_strong_key is False

    def test_has_strong_key_true_for_github(self):
        """has_strong_key should be True for github keys (stronger than name_loc)"""
        # Note: has_strong_key returns True for anything NOT name_loc
        # This is different from is_strong_key() which checks STRONG_KEY_PREFIXES
        result = CanonicalKeyResult(
            canonical_key="github_org:acme",
            candidates=["github_org:acme"]
        )
        assert result.has_strong_key is True

    def test_has_strong_key_false_for_empty(self):
        """has_strong_key should be False for empty key"""
        result = CanonicalKeyResult(canonical_key="", candidates=[])
        assert result.has_strong_key is False

    def test_key_type_extracts_prefix(self):
        """key_type should extract the prefix from canonical key"""
        result = CanonicalKeyResult(
            canonical_key="domain:acme.ai",
            candidates=["domain:acme.ai"]
        )
        assert result.key_type == "domain"

    def test_key_type_for_companies_house(self):
        """key_type should work for companies_house"""
        result = CanonicalKeyResult(
            canonical_key="companies_house:12345678",
            candidates=[]
        )
        assert result.key_type == "companies_house"

    def test_key_type_none_for_empty(self):
        """key_type should be None for empty key"""
        result = CanonicalKeyResult(canonical_key="", candidates=[])
        assert result.key_type is None

    def test_frozen_dataclass(self):
        """CanonicalKeyResult should be immutable (frozen)"""
        result = CanonicalKeyResult(
            canonical_key="domain:acme.ai",
            candidates=["domain:acme.ai"]
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            result.canonical_key = "changed"


# =============================================================================
# TEST: canonical_key_from_external_refs
# =============================================================================

class TestCanonicalKeyFromExternalRefs:
    """Tests for canonical_key_from_external_refs function"""

    def test_domain_key_from_website(self):
        """Should extract domain from 'website' key"""
        refs = {"website": "https://www.example.com/product"}
        result = canonical_key_from_external_refs(refs)
        assert result.canonical_key == "domain:example.com"

    def test_domain_key_from_domain(self):
        """Should extract domain from 'domain' key"""
        refs = {"domain": "example.com"}
        result = canonical_key_from_external_refs(refs)
        assert result.canonical_key == "domain:example.com"

    def test_domain_takes_priority_over_website(self):
        """'domain' key should take priority over 'website'"""
        refs = {
            "domain": "primary.com",
            "website": "secondary.com"
        }
        result = canonical_key_from_external_refs(refs)
        assert result.canonical_key == "domain:primary.com"

    def test_all_refs_generate_candidates(self):
        """Should generate candidates from all provided refs"""
        refs = {
            "website": "https://www.example.com",
            "companies_house_number": "SC123456",
            "github_repo": "https://github.com/ExampleLabs/stealth-repo",
        }
        result = canonical_key_from_external_refs(
            refs,
            fallback_company_name="Example Labs",
            fallback_region="UK-Scotland"
        )
        assert result.canonical_key == "domain:example.com"
        assert "companies_house:sc123456" in result.candidates
        assert "github_repo:examplelabs/stealth-repo" in result.candidates
        assert "name_loc:example-labs|uk-scotland" in result.candidates

    def test_empty_refs_with_fallback(self):
        """Should use fallback when refs are empty"""
        result = canonical_key_from_external_refs(
            {},
            fallback_company_name="Stealth Startup",
            fallback_region="US-SF"
        )
        assert result.canonical_key == "name_loc:stealth-startup|us-sf"

    def test_empty_refs_no_fallback(self):
        """Should return empty key when refs empty and no fallback"""
        result = canonical_key_from_external_refs({})
        assert result.canonical_key == ""
        assert result.candidates == []


# =============================================================================
# TEST: canonical_key_from_signal
# =============================================================================

class TestCanonicalKeyFromSignal:
    """Tests for canonical_key_from_signal function"""

    def test_github_spike_signal(self):
        """Should extract github_org from github_spike signal"""
        result = canonical_key_from_signal(
            "github_spike",
            {"github_org": "anthropic", "github_repo": "anthropic/claude"}
        )
        # github_org has higher priority than github_repo
        assert result.canonical_key == "github_org:anthropic"
        assert "github_repo:anthropic/claude" in result.candidates

    def test_incorporation_signal_with_company_number(self):
        """Should extract companies_house from incorporation signal"""
        result = canonical_key_from_signal(
            "incorporation",
            {"company_number": "SC123456", "company_name": "Test Ltd"}
        )
        assert result.canonical_key == "companies_house:sc123456"

    def test_incorporation_signal_with_companies_house_number(self):
        """Should handle alternative field name"""
        result = canonical_key_from_signal(
            "incorporation",
            {"companies_house_number": "12345678"}
        )
        assert result.canonical_key == "companies_house:12345678"

    def test_funding_event_signal(self):
        """Should extract crunchbase_id from funding_event signal"""
        result = canonical_key_from_signal(
            "funding_event",
            {"crunchbase_id": "anthropic-ai", "pitchbook_id": "pb123"}
        )
        assert result.canonical_key == "crunchbase:anthropic-ai"
        assert "pitchbook:pb123" in result.candidates

    def test_domain_registration_signal(self):
        """Should extract domain from domain_registration signal"""
        result = canonical_key_from_signal(
            "domain_registration",
            {"domain": "newstartup.ai"}
        )
        assert result.canonical_key == "domain:newstartup.ai"

    def test_common_domain_field(self):
        """Should extract domain field regardless of signal type"""
        result = canonical_key_from_signal(
            "unknown_signal",
            {"domain": "example.com"}
        )
        assert result.canonical_key == "domain:example.com"

    def test_common_website_field(self):
        """Should extract website field regardless of signal type"""
        result = canonical_key_from_signal(
            "unknown_signal",
            {"website": "https://example.com"}
        )
        assert result.canonical_key == "domain:example.com"

    def test_unknown_signal_type_with_fallback(self):
        """Should use fallback for unknown signal types"""
        result = canonical_key_from_signal(
            "unknown_signal",
            {"random_field": "value"},
            fallback_company_name="Mystery Co",
            fallback_region="Unknown"
        )
        assert result.canonical_key == "name_loc:mystery-co|unknown"

    def test_empty_signal_data(self):
        """Should handle empty signal data"""
        result = canonical_key_from_signal(
            "github_spike",
            {}
        )
        assert result.canonical_key == ""


# =============================================================================
# TEST: is_strong_key
# =============================================================================

class TestIsStrongKey:
    """Tests for is_strong_key function"""

    def test_domain_is_strong(self):
        """Domain keys should be strong"""
        assert is_strong_key("domain:acme.ai") is True

    def test_companies_house_is_strong(self):
        """Companies House keys should be strong"""
        assert is_strong_key("companies_house:12345678") is True

    def test_crunchbase_is_strong(self):
        """Crunchbase keys should be strong"""
        assert is_strong_key("crunchbase:anthropic") is True

    def test_pitchbook_is_strong(self):
        """PitchBook keys should be strong"""
        assert is_strong_key("pitchbook:pb123") is True

    def test_github_org_is_weak(self):
        """GitHub org keys should be weak"""
        assert is_strong_key("github_org:acme") is False

    def test_github_repo_is_weak(self):
        """GitHub repo keys should be weak"""
        assert is_strong_key("github_repo:acme/product") is False

    def test_name_loc_is_weak(self):
        """name_loc keys should be weak"""
        assert is_strong_key("name_loc:acme|us") is False

    def test_empty_is_weak(self):
        """Empty key should return False"""
        assert is_strong_key("") is False

    def test_unknown_prefix_is_weak(self):
        """Unknown prefix should return False"""
        assert is_strong_key("unknown:value") is False


# =============================================================================
# TEST: get_key_strength_score
# =============================================================================

class TestGetKeyStrengthScore:
    """Tests for get_key_strength_score function"""

    def test_domain_score(self):
        """Domain should have highest score (100)"""
        assert get_key_strength_score("domain:acme.ai") == 100

    def test_companies_house_score(self):
        """Companies House should have score 95"""
        assert get_key_strength_score("companies_house:12345678") == 95

    def test_crunchbase_score(self):
        """Crunchbase should have score 80"""
        assert get_key_strength_score("crunchbase:anthropic") == 80

    def test_pitchbook_score(self):
        """PitchBook should have score 75"""
        assert get_key_strength_score("pitchbook:pb123") == 75

    def test_github_org_score(self):
        """GitHub org should have score 50"""
        assert get_key_strength_score("github_org:acme") == 50

    def test_github_repo_score(self):
        """GitHub repo should have score 40"""
        assert get_key_strength_score("github_repo:acme/product") == 40

    def test_name_loc_score(self):
        """name_loc should have lowest score (10)"""
        assert get_key_strength_score("name_loc:acme|us") == 10

    def test_empty_score(self):
        """Empty key should have score 0"""
        assert get_key_strength_score("") == 0

    def test_unknown_prefix_score(self):
        """Unknown prefix should have score 0"""
        assert get_key_strength_score("unknown:value") == 0

    def test_score_ordering(self):
        """Scores should follow priority order"""
        scores = [
            get_key_strength_score("domain:x"),
            get_key_strength_score("companies_house:x"),
            get_key_strength_score("crunchbase:x"),
            get_key_strength_score("github_org:x"),
            get_key_strength_score("github_repo:x"),
            get_key_strength_score("name_loc:x"),
        ]
        # Should be in descending order
        assert scores == sorted(scores, reverse=True)


# =============================================================================
# TEST: Constants
# =============================================================================

class TestConstants:
    """Tests for module constants"""

    def test_strong_key_prefixes(self):
        """STRONG_KEY_PREFIXES should contain expected values"""
        assert "domain" in STRONG_KEY_PREFIXES
        assert "companies_house" in STRONG_KEY_PREFIXES
        assert "crunchbase" in STRONG_KEY_PREFIXES
        assert "pitchbook" in STRONG_KEY_PREFIXES
        assert "github_org" not in STRONG_KEY_PREFIXES
        assert "name_loc" not in STRONG_KEY_PREFIXES

    def test_weak_key_prefixes(self):
        """WEAK_KEY_PREFIXES should contain expected values"""
        assert "github_org" in WEAK_KEY_PREFIXES
        assert "github_repo" in WEAK_KEY_PREFIXES
        assert "name_loc" in WEAK_KEY_PREFIXES
        assert "domain" not in WEAK_KEY_PREFIXES

    def test_strong_weak_disjoint(self):
        """Strong and weak prefixes should be disjoint"""
        assert STRONG_KEY_PREFIXES.isdisjoint(WEAK_KEY_PREFIXES)
