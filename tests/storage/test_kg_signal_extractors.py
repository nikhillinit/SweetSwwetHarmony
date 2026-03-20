"""Tests for storage/kg_signal_extractors.py — per-source extraction, merging, normalization.

Covers:
  - Each per-source extractor with typical raw_data
  - normalize_location with US states, countries, jurisdictions, edge cases
  - _sic_to_sector with known codes, prefix fallback, and unknown codes
  - _topics_to_sectors and _cb_categories_to_sectors mappings
  - merge_attrs priority ordering, union of collections, empty list
  - extract_signal dispatch including unknown source_api
  - _safe_json with string, dict, None inputs
  - Edge cases: empty raw_data, missing fields
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.kg_signal_extractors import (
    EXTRACTOR_REGISTRY,
    SOURCE_PRIORITY,
    ExtractedAttrs,
    _cb_categories_to_sectors,
    _safe_json,
    _sic_to_sector,
    _topics_to_sectors,
    extract_arxiv,
    extract_companies_house,
    extract_crunchbase,
    extract_domain_whois,
    extract_github,
    extract_hacker_news,
    extract_job_postings,
    extract_linkedin,
    extract_news,
    extract_product_hunt,
    extract_sec_edgar,
    extract_signal,
    merge_attrs,
    normalize_location,
)


# ---------------------------------------------------------------------------
# _safe_json
# ---------------------------------------------------------------------------


class TestSafeJson:
    def test_dict_passthrough(self):
        d = {"foo": "bar"}
        assert _safe_json(d) == d

    def test_json_string_parsed(self):
        assert _safe_json('{"a": 1}') == {"a": 1}

    def test_invalid_json_string_returns_empty(self):
        assert _safe_json("not json at all") == {}

    def test_none_returns_empty(self):
        assert _safe_json(None) == {}

    def test_integer_returns_empty(self):
        assert _safe_json(42) == {}


# ---------------------------------------------------------------------------
# normalize_location
# ---------------------------------------------------------------------------


class TestNormalizeLocation:
    def test_us_state_abbreviation(self):
        assert normalize_location("CA") == "us-california"
        assert normalize_location("NY") == "us-new-york"
        assert normalize_location("TX") == "us-texas"

    def test_us_state_case_insensitive(self):
        # normalize_location uppercases before checking _US_STATE_ABBREVS
        assert normalize_location("ca") == "us-california"
        assert normalize_location("ny") == "us-new-york"

    def test_dc(self):
        assert normalize_location("DC") == "us-dc"

    def test_country_united_states(self):
        assert normalize_location("United States") == "us"
        assert normalize_location("usa") == "us"
        assert normalize_location("U.S.") == "us"

    def test_country_united_kingdom(self):
        assert normalize_location("United Kingdom") == "uk"
        assert normalize_location("uk") == "uk"
        assert normalize_location("gb") == "uk"
        assert normalize_location("Great Britain") == "uk"

    def test_uk_regions(self):
        assert normalize_location("England") == "uk-england"
        assert normalize_location("Scotland") == "uk-scotland"
        assert normalize_location("Wales") == "uk-wales"

    def test_jurisdiction_normalize(self):
        assert normalize_location("england-wales") == "uk-england"
        assert normalize_location("northern-ireland") == "uk-northern-ireland"

    def test_empty_string_returns_none(self):
        assert normalize_location("") is None

    def test_none_returns_none(self):
        assert normalize_location(None) is None

    def test_whitespace_only_returns_none(self):
        assert normalize_location("   ") is None

    def test_unknown_city_becomes_slug(self):
        assert normalize_location("San Francisco") == "san-francisco"
        assert normalize_location("New Delhi, India") == "new-delhi-india"


# ---------------------------------------------------------------------------
# _sic_to_sector
# ---------------------------------------------------------------------------


class TestSicToSector:
    def test_exact_4_digit_match(self):
        assert _sic_to_sector("2086") == "cpg"  # bottled beverages
        assert _sic_to_sector("8011") == "health_tech"
        assert _sic_to_sector("7011") == "travel"
        assert _sic_to_sector("5961") == "marketplace"

    def test_2_digit_prefix_fallback(self):
        # "2099" not in map directly, but prefix "20" maps to cpg
        assert _sic_to_sector("2099") == "cpg"
        # "8099" -> prefix "80" -> health_tech
        assert _sic_to_sector("8099") == "health_tech"

    def test_unknown_code_returns_none(self):
        assert _sic_to_sector("9999") is None
        assert _sic_to_sector("1234") is None

    def test_empty_string_returns_none(self):
        assert _sic_to_sector("") is None

    def test_none_returns_none(self):
        assert _sic_to_sector(None) is None

    def test_whitespace_stripped(self):
        assert _sic_to_sector("  2086  ") == "cpg"


# ---------------------------------------------------------------------------
# _topics_to_sectors / _cb_categories_to_sectors
# ---------------------------------------------------------------------------


class TestTopicsToSectors:
    def test_single_topic_match(self):
        assert _topics_to_sectors(["food"]) == ["cpg"]

    def test_multiple_topics_deduped(self):
        assert _topics_to_sectors(["food", "beverage", "beauty"]) == ["cpg"]

    def test_multiple_sectors(self):
        result = _topics_to_sectors(["food", "fitness", "marketplace"])
        assert result == ["cpg", "health_tech", "marketplace"]

    def test_no_match(self):
        assert _topics_to_sectors(["python", "rust"]) == []

    def test_empty_list(self):
        assert _topics_to_sectors([]) == []


class TestCbCategoriesToSectors:
    def test_single_category(self):
        assert _cb_categories_to_sectors(["Food and Beverage"]) == ["cpg"]

    def test_mixed_categories(self):
        result = _cb_categories_to_sectors(["Beauty", "Travel", "Consumer"])
        assert result == ["cpg", "marketplace", "travel"]

    def test_no_match(self):
        assert _cb_categories_to_sectors(["Enterprise SaaS"]) == []


# ---------------------------------------------------------------------------
# Per-source extractors
# ---------------------------------------------------------------------------


class TestExtractGithub:
    def test_typical_repo(self):
        raw = {
            "company_name": "FreshBowl",
            "homepage": "https://freshbowl.co",
            "description": "Meal kit delivery",
            "topics": ["food", "marketplace"],
        }
        attrs = extract_github(raw, "github")
        assert attrs.company_name == "FreshBowl"
        assert attrs.domain == "https://freshbowl.co"
        assert attrs.description == "Meal kit delivery"
        assert attrs.sectors == ["cpg", "marketplace"]
        assert attrs.priority == SOURCE_PRIORITY["github"]
        assert attrs.source_api == "github"

    def test_owner_login_fallback(self):
        raw = {"owner": {"login": "acme-labs"}, "topics": []}
        attrs = extract_github(raw, "github")
        assert attrs.company_name == "acme-labs"

    def test_empty_raw(self):
        attrs = extract_github({}, "github")
        assert attrs.company_name is None
        assert attrs.sectors == []


class TestExtractSecEdgar:
    def test_form_d_filing(self):
        raw = {
            "company_name": "Acme Corp",
            "domain": "acme.com",
            "formType": "D",
            "sic_code": "2086",
            "state": "CA",
        }
        attrs = extract_sec_edgar(raw, "sec_edgar")
        assert attrs.company_name == "Acme Corp"
        assert attrs.domain == "acme.com"
        assert attrs.stage == "Pre-Seed"
        assert attrs.sectors == ["cpg"]
        assert attrs.locations == ["us-california"]
        assert attrs.priority == SOURCE_PRIORITY["sec_edgar"]

    def test_country_fallback_when_no_state(self):
        raw = {"company_name": "UK Co", "country": "United Kingdom"}
        attrs = extract_sec_edgar(raw, "sec_edgar")
        assert attrs.locations == ["uk"]


class TestExtractCompaniesHouse:
    def test_active_company(self):
        raw = {
            "company_name": "BrewCo Ltd",
            "company_status": "active",
            "sic_codes": ["2082", "5812"],
            "jurisdiction": "england-wales",
        }
        attrs = extract_companies_house(raw, "companies_house")
        assert attrs.company_name == "BrewCo Ltd"
        assert attrs.stage == "Pre-Seed"
        assert "cpg" in attrs.sectors
        assert "travel" in attrs.sectors
        assert attrs.locations == ["uk-england"]
        assert attrs.priority == SOURCE_PRIORITY["companies_house"]

    def test_empty_sic_codes(self):
        raw = {"title": "SomeCo", "sic_codes": []}
        attrs = extract_companies_house(raw, "companies_house")
        assert attrs.company_name == "SomeCo"
        assert attrs.sectors == []


class TestExtractCrunchbase:
    def test_full_profile(self):
        raw = {
            "name": "GlowUp",
            "homepage_url": "https://glowup.co",
            "short_description": "DTC beauty brand",
            "last_funding_type": "seed",
            "num_employees_enum": 25,
            "founded_on": "2024-03-15",
            "categories": ["Beauty", "E-Commerce"],
            "location": "San Francisco",
            "country_code": "us",
        }
        attrs = extract_crunchbase(raw, "crunchbase")
        assert attrs.company_name == "GlowUp"
        assert attrs.domain == "https://glowup.co"
        assert attrs.description == "DTC beauty brand"
        assert attrs.stage == "Seed"
        assert attrs.employees == 25
        assert attrs.founded_year == 2024
        assert "cpg" in attrs.sectors
        assert "marketplace" in attrs.sectors
        assert "san-francisco" in attrs.locations
        assert "us" in attrs.locations
        assert attrs.priority == SOURCE_PRIORITY["crunchbase"]

    def test_categories_as_comma_string(self):
        raw = {"categories": "Food, Fitness"}
        attrs = extract_crunchbase(raw, "crunchbase")
        assert "cpg" in attrs.sectors
        assert "health_tech" in attrs.sectors

    def test_stage_mapping_pre_seed(self):
        raw = {"last_funding_type": "angel"}
        attrs = extract_crunchbase(raw, "crunchbase")
        assert attrs.stage == "Pre-Seed"

    def test_stage_mapping_series_a(self):
        raw = {"last_funding_type": "series_a"}
        attrs = extract_crunchbase(raw, "crunchbase")
        assert attrs.stage == "Series A"

    def test_unknown_stage_passthrough(self):
        raw = {"last_funding_type": "series_c"}
        attrs = extract_crunchbase(raw, "crunchbase")
        assert attrs.stage == "series_c"


class TestExtractHackerNews:
    def test_typical(self):
        raw = {
            "company_name": "ShowHN Startup",
            "url_domain": "showhnt.io",
            "title": "Show HN: A new thing",
        }
        attrs = extract_hacker_news(raw, "hacker_news")
        assert attrs.company_name == "ShowHN Startup"
        assert attrs.domain == "showhnt.io"
        assert attrs.claims == {"title": "Show HN: A new thing"}
        assert attrs.priority == SOURCE_PRIORITY["hacker_news"]


class TestExtractJobPostings:
    def test_with_locations_list(self):
        raw = {
            "company": "FitTrack",
            "domain": "fittrack.io",
            "locations": ["NY", "CA"],
        }
        attrs = extract_job_postings(raw, "job_postings")
        assert attrs.company_name == "FitTrack"
        assert attrs.domain == "fittrack.io"
        assert "us-new-york" in attrs.locations
        assert "us-california" in attrs.locations

    def test_with_location_string_fallback(self):
        raw = {"company_name": "Acme", "location": "TX"}
        attrs = extract_job_postings(raw, "job_postings")
        assert attrs.locations == ["us-texas"]

    def test_locations_string_single(self):
        raw = {"company_name": "Co", "locations": "FL"}
        attrs = extract_job_postings(raw, "job_postings")
        assert attrs.locations == ["us-florida"]


class TestExtractNews:
    def test_typical(self):
        raw = {
            "company_name": "NewsTarget",
            "promoted_domain": "newstarget.co",
            "title": "Startup raises $5M",
        }
        attrs = extract_news(raw, "news_api")
        assert attrs.company_name == "NewsTarget"
        assert attrs.domain == "newstarget.co"
        assert attrs.priority == SOURCE_PRIORITY["news_api"]

    def test_rss_feeds_uses_same_extractor(self):
        attrs = extract_news({"company_name": "RSSCo"}, "rss_feeds")
        assert attrs.priority == SOURCE_PRIORITY["rss_feeds"]


class TestExtractDomainWhois:
    def test_typical(self):
        raw = {
            "domain_name": "example.com",
            "registrant_org": "Example Inc",
            "registrant_country": "United States",
        }
        attrs = extract_domain_whois(raw, "domain_whois")
        assert attrs.domain == "example.com"
        assert attrs.company_name == "Example Inc"
        assert attrs.locations == ["us"]
        assert attrs.priority == SOURCE_PRIORITY["domain_whois"]


class TestExtractProductHunt:
    def test_typical(self):
        raw = {
            "name": "LaunchPad",
            "website": "https://launchpad.dev",
            "tagline": "Ship faster",
        }
        attrs = extract_product_hunt(raw, "product_hunt")
        assert attrs.company_name == "LaunchPad"
        assert attrs.domain == "https://launchpad.dev"
        assert attrs.description == "Ship faster"
        assert attrs.priority == SOURCE_PRIORITY["product_hunt"]


class TestExtractArxiv:
    def test_typical(self):
        raw = {"company_name": "DeepNutrition", "title": "Nutritional AI"}
        attrs = extract_arxiv(raw, "arxiv")
        assert attrs.company_name == "DeepNutrition"
        assert attrs.claims == {"title": "Nutritional AI"}
        assert attrs.priority == SOURCE_PRIORITY["arxiv"]

    def test_empty(self):
        attrs = extract_arxiv({}, "arxiv")
        assert attrs.company_name is None


class TestExtractLinkedin:
    def test_full_profile(self):
        raw = {
            "name": "WellnessCo",
            "website": "https://wellnessco.com",
            "description": "Corporate wellness",
            "employee_count": 50,
            "headquarters": "NY",
            "industry": "Health & Wellness",
        }
        attrs = extract_linkedin(raw, "linkedin")
        assert attrs.company_name == "WellnessCo"
        assert attrs.domain == "https://wellnessco.com"
        assert attrs.description == "Corporate wellness"
        assert attrs.employees == 50
        assert attrs.locations == ["us-new-york"]
        assert attrs.priority == SOURCE_PRIORITY["linkedin"]


# ---------------------------------------------------------------------------
# extract_signal (dispatch + JSON parsing)
# ---------------------------------------------------------------------------


class TestExtractSignal:
    def test_dispatch_to_correct_extractor(self):
        raw = {"company_name": "TestCo", "domain": "test.co"}
        attrs = extract_signal("hacker_news", raw)
        assert attrs.company_name == "TestCo"
        assert attrs.source_api == "hacker_news"

    def test_json_string_input(self):
        raw_str = json.dumps({"company_name": "JSONCo", "domain": "json.co"})
        attrs = extract_signal("hacker_news", raw_str)
        assert attrs.company_name == "JSONCo"

    def test_unknown_source_returns_empty_attrs(self):
        attrs = extract_signal("totally_unknown", {"company_name": "X"})
        assert attrs.source_api == "totally_unknown"
        assert attrs.priority == 0
        assert attrs.company_name is None

    def test_none_raw_data(self):
        attrs = extract_signal("github", None)
        assert attrs.company_name is None
        assert attrs.sectors == []


# ---------------------------------------------------------------------------
# merge_attrs
# ---------------------------------------------------------------------------


class TestMergeAttrs:
    def test_empty_list_returns_empty_dict(self):
        assert merge_attrs([]) == {}

    def test_single_source(self):
        a = ExtractedAttrs(
            company_name="Acme",
            domain="acme.co",
            source_api="crunchbase",
            priority=100,
            sectors=["cpg"],
            locations=["us"],
        )
        merged = merge_attrs([a])
        assert merged["company_name"] == "Acme"
        assert merged["domain"] == "acme.co"
        assert merged["sectors"] == ["cpg"]
        assert merged["locations"] == ["us"]

    def test_higher_priority_wins_for_scalars(self):
        low = ExtractedAttrs(
            company_name="GH Name",
            domain="gh.dev",
            description="from github",
            source_api="github",
            priority=30,
        )
        high = ExtractedAttrs(
            company_name="CB Name",
            domain="cb.co",
            description="from crunchbase",
            source_api="crunchbase",
            priority=100,
        )
        merged = merge_attrs([low, high])
        assert merged["company_name"] == "CB Name"
        assert merged["domain"] == "cb.co"
        assert merged["description"] == "from crunchbase"

    def test_lower_fills_missing_scalars(self):
        high = ExtractedAttrs(
            company_name="HighCo",
            source_api="crunchbase",
            priority=100,
        )
        low = ExtractedAttrs(
            company_name="LowCo",
            domain="low.co",
            description="low desc",
            stage="Seed",
            employees=10,
            founded_year=2023,
            source_api="github",
            priority=30,
        )
        merged = merge_attrs([high, low])
        assert merged["company_name"] == "HighCo"
        assert merged["domain"] == "low.co"
        assert merged["description"] == "low desc"
        assert merged["stage"] == "Seed"
        assert merged["employees"] == 10
        assert merged["founded_year"] == 2023

    def test_sectors_and_locations_are_unioned(self):
        a = ExtractedAttrs(
            source_api="crunchbase", priority=100,
            sectors=["cpg"], locations=["us"],
        )
        b = ExtractedAttrs(
            source_api="github", priority=30,
            sectors=["marketplace", "cpg"], locations=["uk", "us"],
        )
        merged = merge_attrs([a, b])
        assert merged["sectors"] == ["cpg", "marketplace"]
        assert merged["locations"] == ["uk", "us"]

    def test_claims_attributed_by_source(self):
        a = ExtractedAttrs(
            source_api="sec_edgar", priority=90,
            claims={"sic_code": "2086"},
        )
        b = ExtractedAttrs(
            source_api="github", priority=30,
            claims={"topics": ["food"]},
        )
        merged = merge_attrs([a, b])
        assert "sec_edgar" in merged["claims"]
        assert "github" in merged["claims"]
        assert merged["claims"]["sec_edgar"] == {"sic_code": "2086"}


# ---------------------------------------------------------------------------
# Registry / priority sanity checks
# ---------------------------------------------------------------------------


class TestRegistryIntegrity:
    def test_all_priority_sources_have_extractors(self):
        for source in SOURCE_PRIORITY:
            assert source in EXTRACTOR_REGISTRY, (
                f"SOURCE_PRIORITY has '{source}' but EXTRACTOR_REGISTRY does not"
            )

    def test_all_extractors_have_priorities(self):
        for source in EXTRACTOR_REGISTRY:
            assert source in SOURCE_PRIORITY, (
                f"EXTRACTOR_REGISTRY has '{source}' but SOURCE_PRIORITY does not"
            )

    def test_crunchbase_is_highest_priority(self):
        assert SOURCE_PRIORITY["crunchbase"] == max(SOURCE_PRIORITY.values())
