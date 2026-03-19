"""Tests for ClaimExtractor.

Covers extraction of ClaimFact objects from ConsolidatedSignal mocks,
including well-formed data, missing fields, malformed JSON raw_data,
unexpected types, empty payloads, and multiple signal sources.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from storage.claim_fact_store import ClaimFact
from tests.fixtures.mocks import make_consolidated_signal
from utils.claim_extractor import (
    ClaimExtractor,
    EXTRACTABLE_PREDICATES,
    FIELD_TO_PREDICATE,
    extract_claims,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DETECTED_AT = datetime(2026, 1, 15, tzinfo=timezone.utc)
_FOUNDING_DATE = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _make_signal(
    company_name="Acme Inc",
    canonical_key="domain:acme.ai",
    source_apis=None,
    raw_data=None,
    signal_ids=None,
    founding_date=None,
    detected_at=None,
):
    """Wrapper around make_consolidated_signal that supplies datetime objects."""
    sig = make_consolidated_signal(
        company_name=company_name,
        canonical_key=canonical_key,
        source_apis=source_apis,
        raw_data=raw_data,
        signal_ids=signal_ids,
        founding_date=founding_date,
        detected_at=detected_at or _DETECTED_AT.isoformat(),
    )
    # ClaimExtractor calls .isoformat() on latest_detected_at,
    # so replace the plain string with a real datetime.
    sig.latest_detected_at = detected_at or _DETECTED_AT
    # make_consolidated_signal uses `source_apis or ["github"]` which treats
    # an explicit empty list as falsy; override when caller passes [].
    if source_apis is not None:
        sig.source_apis = source_apis
    return sig


# ---------------------------------------------------------------------------
# 1. Valid raw-data JSON
# ---------------------------------------------------------------------------

class TestValidRawData:
    """Extract ClaimFact objects from well-formed signals."""

    def test_company_name_extracted(self):
        """Company name on the consolidated signal yields a company_name fact."""
        sig = _make_signal(
            company_name="Acme Inc",
            raw_data={"title": "irrelevant"},
        )
        extractor = ClaimExtractor()
        facts = extractor.extract(sig, entity_id="ent-1", canonical_key="domain:acme.ai")

        name_facts = [f for f in facts if f.predicate == "company_name"]
        assert len(name_facts) == 1
        assert json.loads(name_facts[0].value_json) == "Acme Inc"

    def test_founding_date_extracted(self):
        """founding_date on the signal is extracted as an ISO string."""
        sig = _make_signal(founding_date=_FOUNDING_DATE)
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:acme.ai")

        fd_facts = [f for f in facts if f.predicate == "founding_date"]
        assert len(fd_facts) == 1
        # Value should be JSON-encoded ISO string
        assert _FOUNDING_DATE.isoformat() in fd_facts[0].value_json

    def test_raw_data_fields_extracted(self):
        """Fields in merged_raw_data matching FIELD_TO_PREDICATE are extracted."""
        sig = _make_signal(
            company_name=None,  # avoid duplicate from top-level
            raw_data={
                "location": "San Francisco, CA",
                "industry": "Consumer Health Tech",
                "website": "https://acme.ai",
            },
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:acme.ai")

        predicates = {f.predicate for f in facts}
        assert "location" in predicates
        assert "industry" in predicates
        assert "website" in predicates

    def test_raw_data_variant_field_names(self):
        """Variant field names (e.g. 'hq_city', 'sector') map to correct predicates."""
        sig = _make_signal(
            company_name=None,
            raw_data={
                "hq_city": "London",
                "sector": "CPG",
                "homepage": "https://example.com",
            },
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:example.com")

        predicates = {f.predicate for f in facts}
        assert "location" in predicates
        assert "industry" in predicates
        assert "website" in predicates

    def test_claim_fact_fields_populated(self):
        """Each returned ClaimFact has required fields populated."""
        sig = _make_signal(
            company_name="Acme Inc",
            source_apis=["sec_edgar"],
            raw_data={"location": "NYC"},
            signal_ids=[10, 20],
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:acme.ai")

        for fact in facts:
            assert isinstance(fact, ClaimFact)
            assert fact.entity_id == "ent-1"
            assert fact.source_canonical_key == "domain:acme.ai"
            assert fact.predicate in EXTRACTABLE_PREDICATES or fact.predicate in FIELD_TO_PREDICATE.values()
            assert fact.value_json  # non-empty
            assert fact.valid_from  # non-empty ISO string
            assert fact.observed_at  # non-empty ISO string
            assert fact.supporting_signal_ids == [10, 20]
            assert 1 <= fact.source_tier <= 5
            assert 0.0 <= fact.confidence <= 1.0

    def test_dedup_by_predicate(self):
        """Duplicate predicates from top-level and raw_data are deduplicated."""
        sig = _make_signal(
            company_name="Acme",
            raw_data={"company_name": "Acme Corp"},  # same predicate via raw_data
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:acme.ai")

        name_facts = [f for f in facts if f.predicate == "company_name"]
        assert len(name_facts) == 1, "Should keep only the first (top-level) company_name"


# ---------------------------------------------------------------------------
# 2. Missing fields
# ---------------------------------------------------------------------------

class TestMissingFields:
    """Signals with missing company_name or other fields produce partial claims."""

    def test_no_company_name(self):
        """Missing company_name still produces facts from raw_data."""
        sig = _make_signal(
            company_name=None,
            raw_data={"location": "Austin, TX", "industry": "Travel"},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        predicates = {f.predicate for f in facts}
        assert "company_name" not in predicates
        assert "location" in predicates

    def test_no_founding_date(self):
        """Missing founding_date skips that predicate."""
        sig = _make_signal(founding_date=None, raw_data={})
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        predicates = {f.predicate for f in facts}
        assert "founding_date" not in predicates

    def test_no_company_name_no_founding_date(self):
        """Minimal signal with only raw_data fields."""
        sig = _make_signal(
            company_name=None,
            founding_date=None,
            raw_data={"website": "https://startup.io"},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:startup.io")

        assert len(facts) == 1
        assert facts[0].predicate == "website"


# ---------------------------------------------------------------------------
# 3. Malformed / non-dict raw_data
# ---------------------------------------------------------------------------

class TestMalformedRawData:
    """raw_data that is not a proper dict should not crash the extractor."""

    def test_raw_data_is_empty_dict(self):
        """Empty dict raw_data should still produce top-level facts."""
        sig = _make_signal(company_name="FooCo", raw_data={})
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:foo.co")

        assert len(facts) >= 1
        assert facts[0].predicate == "company_name"

    def test_raw_data_is_empty_string_dict(self):
        """raw_data with empty-string values should skip those fields."""
        sig = _make_signal(
            company_name=None,
            raw_data={"location": "", "industry": "   ", "website": "https://ok.com"},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:ok.com")

        predicates = {f.predicate for f in facts}
        assert "location" not in predicates
        assert "industry" not in predicates
        assert "website" in predicates

    def test_raw_data_empty_list_values_skipped(self):
        """raw_data fields set to empty lists should be skipped."""
        sig = _make_signal(
            company_name=None,
            raw_data={"location": [], "industry": "Health Tech"},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        predicates = {f.predicate for f in facts}
        assert "location" not in predicates
        assert "industry" in predicates


# ---------------------------------------------------------------------------
# 4. Unexpected types in raw_data values
# ---------------------------------------------------------------------------

class TestUnexpectedTypes:
    """Non-standard value types in raw_data are handled gracefully."""

    def test_numeric_value_serialized(self):
        """Numeric values should be JSON-serializable."""
        sig = _make_signal(
            company_name=None,
            raw_data={"funding_raised": 5000000},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        fund_facts = [f for f in facts if f.predicate == "funding_raised"]
        assert len(fund_facts) == 1
        assert json.loads(fund_facts[0].value_json) == 5000000

    def test_list_value_serialized(self):
        """List values should be JSON-serializable."""
        sig = _make_signal(
            company_name=None,
            raw_data={"location": ["San Francisco", "New York"]},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        loc_facts = [f for f in facts if f.predicate == "location"]
        assert len(loc_facts) == 1
        assert json.loads(loc_facts[0].value_json) == ["San Francisco", "New York"]

    def test_nested_dict_value_serialized(self):
        """Nested dict values should be JSON-serializable."""
        sig = _make_signal(
            company_name=None,
            raw_data={"location": {"city": "London", "country": "UK"}},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        loc_facts = [f for f in facts if f.predicate == "location"]
        assert len(loc_facts) == 1
        parsed = json.loads(loc_facts[0].value_json)
        assert parsed["city"] == "London"

    def test_boolean_value_serialized(self):
        """Boolean values should not crash and should serialize."""
        sig = _make_signal(
            company_name=None,
            raw_data={"industry": True},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        ind_facts = [f for f in facts if f.predicate == "industry"]
        assert len(ind_facts) == 1
        assert json.loads(ind_facts[0].value_json) is True


# ---------------------------------------------------------------------------
# 5. Empty payloads
# ---------------------------------------------------------------------------

class TestEmptyPayloads:
    """Completely empty or minimal signals."""

    def test_all_fields_none(self):
        """Signal with no company_name, no founding_date, empty raw_data."""
        sig = _make_signal(
            company_name=None,
            founding_date=None,
            raw_data={},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        assert facts == []

    def test_raw_data_with_unrecognized_keys(self):
        """raw_data with keys not in FIELD_TO_PREDICATE produces no facts."""
        sig = _make_signal(
            company_name=None,
            founding_date=None,
            raw_data={"random_field": "value", "another_field": 42},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        assert facts == []

    def test_raw_data_all_none_values(self):
        """raw_data where every mapped field is None produces no raw_data facts."""
        sig = _make_signal(
            company_name=None,
            founding_date=None,
            raw_data={"location": None, "industry": None, "website": None},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

        assert facts == []


# ---------------------------------------------------------------------------
# 6. Parameterized: multiple signal sources / types
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "source_apis, expected_tier_range",
    [
        (["sec_edgar"], (1, 2)),       # authority 0.90 -> tier 1 or 2
        (["github"], (3, 5)),          # authority 0.55 -> tier 4
        (["domain_whois"], (4, 5)),    # authority 0.45 -> tier 5
        (["crunchbase"], (2, 4)),      # authority 0.75 -> tier 3
        (["companies_house"], (1, 2)), # authority 0.95 -> tier 1
        (["unknown_source"], (3, 5)),  # default authority 0.5 -> tier 4
    ],
    ids=[
        "sec_edgar-high-authority",
        "github-medium-authority",
        "domain_whois-low-authority",
        "crunchbase-curated",
        "companies_house-official",
        "unknown-source-default",
    ],
)
def test_source_tier_mapping(source_apis, expected_tier_range):
    """Source APIs map to the correct authority tier range."""
    sig = _make_signal(
        company_name="TestCo",
        source_apis=source_apis,
        raw_data={},
    )
    facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

    assert len(facts) >= 1
    low, high = expected_tier_range
    for fact in facts:
        assert low <= fact.source_tier <= high, (
            f"source={source_apis}, tier={fact.source_tier} not in [{low}, {high}]"
        )


@pytest.mark.parametrize(
    "raw_data_field, expected_predicate",
    [
        ("company_name", "company_name"),
        ("name", "company_name"),
        ("legal_name", "company_name"),
        ("organization_name", "company_name"),
        ("founding_date", "founding_date"),
        ("incorporation_date", "founding_date"),
        ("founded_on", "founding_date"),
        ("location", "location"),
        ("hq_city", "location"),
        ("headquarters", "location"),
        ("industry", "industry"),
        ("sector", "industry"),
        ("vertical", "industry"),
        ("funding_raised", "funding_raised"),
        ("total_funding", "funding_raised"),
        ("amount_raised", "funding_raised"),
        ("website", "website"),
        ("homepage", "website"),
        ("url", "website"),
    ],
    ids=lambda val: val,
)
def test_field_to_predicate_mapping(raw_data_field, expected_predicate):
    """Each FIELD_TO_PREDICATE variant maps to the correct predicate."""
    sig = _make_signal(
        company_name=None,
        founding_date=None,
        raw_data={raw_data_field: "test-value"},
    )
    facts = ClaimExtractor().extract(sig, "ent-1", "domain:test.com")

    assert len(facts) == 1
    assert facts[0].predicate == expected_predicate


@pytest.mark.parametrize(
    "predicate, expected_confidence",
    [
        ("company_name", 0.8),
        ("website", 0.8),
        ("founding_date", 0.8),
        ("location", 0.6),
        ("industry", 0.6),
        ("funding_raised", 0.5),
    ],
)
def test_field_confidence_mapping(predicate, expected_confidence):
    """Each predicate gets the correct confidence score from _field_confidence."""
    extractor = ClaimExtractor()
    assert extractor._field_confidence(predicate) == expected_confidence


# ---------------------------------------------------------------------------
# Multi-source averaging
# ---------------------------------------------------------------------------

class TestMultiSourceAveraging:
    """Tier calculation when multiple source APIs contribute."""

    def test_mixed_sources_averaged(self):
        """Multiple sources produce an averaged tier."""
        sig = _make_signal(
            company_name="MultiCo",
            source_apis=["sec_edgar", "github"],  # 0.90 + 0.55 = 1.45 / 2 = 0.725 -> tier 3
            raw_data={},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:multi.co")

        assert len(facts) >= 1
        # Average authority 0.725 -> tier 3 (>= 0.65)
        assert facts[0].source_tier == 3

    def test_empty_sources_lowest_tier(self):
        """Empty source_apis list gives tier 5 (lowest)."""
        sig = _make_signal(
            company_name="NoSourceCo",
            source_apis=[],
            raw_data={},
        )
        facts = ClaimExtractor().extract(sig, "ent-1", "domain:nosource.com")

        assert len(facts) >= 1
        assert facts[0].source_tier == 5


# ---------------------------------------------------------------------------
# extract_batch
# ---------------------------------------------------------------------------

class TestExtractBatch:
    """Test batch extraction across multiple signals."""

    def test_batch_extracts_multiple_entities(self):
        """extract_batch returns facts keyed by entity_id."""
        sig1 = _make_signal(company_name="AlphaCo", canonical_key="domain:alpha.com")
        sig2 = _make_signal(company_name="BetaCo", canonical_key="domain:beta.com")

        entity_map = {
            "domain:alpha.com": "ent-alpha",
            "domain:beta.com": "ent-beta",
        }

        extractor = ClaimExtractor()
        result = extractor.extract_batch([sig1, sig2], entity_map)

        assert "ent-alpha" in result
        assert "ent-beta" in result
        assert any(f.predicate == "company_name" for f in result["ent-alpha"])
        assert any(f.predicate == "company_name" for f in result["ent-beta"])

    def test_batch_skips_unmapped_entities(self):
        """Signals without a matching entity_id in the map are skipped."""
        sig = _make_signal(company_name="Orphan", canonical_key="domain:orphan.com")
        entity_map = {"domain:other.com": "ent-other"}

        extractor = ClaimExtractor()
        result = extractor.extract_batch([sig], entity_map)

        assert result == {}

    def test_batch_skips_empty_facts(self):
        """Signals that produce zero facts are not included in the result."""
        sig = _make_signal(
            company_name=None,
            founding_date=None,
            canonical_key="domain:empty.com",
            raw_data={},
        )
        entity_map = {"domain:empty.com": "ent-empty"}

        extractor = ClaimExtractor()
        result = extractor.extract_batch([sig], entity_map)

        assert "ent-empty" not in result


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

class TestConvenienceFunction:
    """Test the extract_claims top-level function."""

    def test_extract_claims_matches_extractor(self):
        """extract_claims() produces the same result as ClaimExtractor().extract()."""
        sig = _make_signal(
            company_name="ConvCo",
            raw_data={"location": "NYC"},
        )
        expected = ClaimExtractor().extract(sig, "ent-1", "domain:conv.co")
        actual = extract_claims(sig, "ent-1", "domain:conv.co")

        # Same number of facts with same predicates
        assert len(actual) == len(expected)
        assert {f.predicate for f in actual} == {f.predicate for f in expected}


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
