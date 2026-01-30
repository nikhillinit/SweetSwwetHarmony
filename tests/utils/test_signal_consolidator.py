"""Tests for signal consolidation logic."""

import pytest
from datetime import datetime, timezone
from utils.signal_consolidator import ConsolidatedSignal, ConflictFlag


class TestConsolidatedSignalDataclass:
    """Test the ConsolidatedSignal dataclass."""

    def test_consolidated_signal_required_fields(self):
        """ConsolidatedSignal requires canonical_key and company_name."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2, 3],
            signal_types=["github_spike", "incorporation"],
            source_apis=["github", "sec_edgar"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        assert consolidated.canonical_key == "domain:acme.ai"
        assert consolidated.company_name == "Acme Inc"
        assert consolidated.contributing_signal_ids == [1, 2, 3]
        assert len(consolidated.signal_types) == 2

    def test_consolidated_signal_has_conflict_flags(self):
        """ConsolidatedSignal can have conflict flags."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            conflict_flags=[
                ConflictFlag(
                    field="company_name",
                    values=["Acme Inc", "ACME Corporation"],
                    severity="warning",
                )
            ],
        )

        assert len(consolidated.conflict_flags) == 1
        assert consolidated.conflict_flags[0].field == "company_name"
        assert consolidated.has_conflicts is True


class TestSourcePriority:
    """Test source priority for field selection."""

    def test_source_priority_order(self):
        """Companies House has highest priority for company_name."""
        from utils.signal_consolidator import SOURCE_PRIORITY

        assert SOURCE_PRIORITY["companies_house"] < SOURCE_PRIORITY["github"]
        assert SOURCE_PRIORITY["sec_edgar"] < SOURCE_PRIORITY["product_hunt"]
        assert SOURCE_PRIORITY["crunchbase"] < SOURCE_PRIORITY["domain_whois"]

    def test_select_company_name_prefers_companies_house(self):
        """Should prefer company_name from Companies House over GitHub."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="acme-ai",  # GitHub style name
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme AI Limited",  # Official name
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        # Should pick Companies House name despite GitHub having higher confidence
        assert result.company_name == "Acme AI Limited"

    def test_select_company_name_falls_back_to_lower_priority(self):
        """Should fall back to lower priority if higher is missing."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="acme-ai",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.company_name == "acme-ai"


class TestConflictDetection:
    """Test conflict detection during consolidation."""

    def test_detects_different_company_names(self):
        """Should flag when signals have different company names."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme AI",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="ACME Corporation",  # Different name!
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.has_conflicts is True
        assert len(result.conflict_flags) == 1
        assert result.conflict_flags[0].field == "company_name"
        assert "Acme AI" in result.conflict_flags[0].values
        assert "ACME Corporation" in result.conflict_flags[0].values

    def test_no_conflict_for_same_company_name(self):
        """Should not flag when all signals have same company name."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",  # Same name
                confidence=0.7,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.has_conflicts is False
        assert len(result.conflict_flags) == 0

    def test_ignores_none_and_empty_company_names(self):
        """Should not treat None/empty as conflicts."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="domain_registration",
                source_api="domain_whois",
                canonical_key="domain:acme.ai",
                company_name=None,  # No name from WHOIS
                confidence=0.5,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert result.has_conflicts is False


class TestDescriptionAggregation:
    """Test description aggregation from raw_data."""

    def test_aggregates_descriptions_from_raw_data(self):
        """Should collect descriptions from raw_data of all signals."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"description": "AI-powered automation tool"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="product_hunt_launch",
                source_api="product_hunt",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"tagline": "Automate your workflow with AI"},
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.descriptions) == 2
        assert "AI-powered automation tool" in result.descriptions
        assert "Automate your workflow with AI" in result.descriptions

    def test_deduplicates_identical_descriptions(self):
        """Should not include duplicate descriptions."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"description": "AI tool"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="github_activity",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.6,
                raw_data={"description": "AI tool"},  # Same
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.descriptions) == 1
        assert result.descriptions[0] == "AI tool"

    def test_handles_missing_descriptions(self):
        """Should handle signals without descriptions gracefully."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)

        signals = [
            StoredSignal(
                id=1,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"form_d": "D-123"},  # No description
                detected_at=now,
                created_at=now,
            ),
        ]

        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)

        assert len(result.descriptions) == 0


class TestSocialProofAggregation:
    """Test social proof aggregation from raw_data."""

    def test_aggregates_github_stars(self):
        """Should aggregate stars from GitHub signals."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"stars": 150, "recent_stars": 50},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.social_proof["stars"] == 150
        assert result.social_proof["recent_stars"] == 50

    def test_aggregates_product_hunt_upvotes(self):
        """Should aggregate upvotes from Product Hunt signals."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="product_hunt_launch",
                source_api="product_hunt",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"votes": 200, "upvotes": 180, "comments": 45},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.social_proof["votes"] == 200
        assert result.social_proof["upvotes"] == 180
        assert result.social_proof["comments"] == 45

    def test_sums_social_proof_from_multiple_signals(self):
        """Should sum social proof from multiple signals."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"stars": 100},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="product_hunt_launch",
                source_api="product_hunt",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"upvotes": 50},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.social_proof["stars"] == 100
        assert result.social_proof["upvotes"] == 50


class TestFoundingDateExtraction:
    """Test founding date extraction from raw_data."""

    def test_extracts_founding_date_from_companies_house(self):
        """Should extract founding_date from Companies House signal."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"founding_date": "2023-06-15"},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.founding_date is not None
        assert result.founding_date.year == 2023
        assert result.founding_date.month == 6
        assert result.founding_date.day == 15

    def test_prefers_earliest_founding_date(self):
        """Should pick earliest founding_date when multiple exist."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"founding_date": "2024-01-01"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"founding_date": "2023-06-15"},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.founding_date.year == 2023

    def test_extracts_from_registered_date_field(self):
        """Should also check registered_date field (domain WHOIS)."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="domain_registration",
                source_api="domain_whois",
                canonical_key="domain:acme.ai",
                company_name=None,
                confidence=0.5,
                raw_data={"registered_date": "2022-03-01"},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.founding_date is not None
        assert result.founding_date.year == 2022


class TestWhyNowAggregation:
    """Test why_now aggregation from raw_data."""

    def test_aggregates_why_now_from_raw_data(self):
        """Should collect why_now from raw_data of all signals."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="funding_event",
                source_api="crunchbase",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={"why_now": "Just raised $5M seed round"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="hiring_signal",
                source_api="greenhouse",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"why_now": "Hiring 10 engineers"},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert len(result.why_now_parts) == 2
        assert "Just raised $5M seed round" in result.why_now_parts
        assert "Hiring 10 engineers" in result.why_now_parts

    def test_generates_fallback_why_now(self):
        """Should generate fallback when no explicit why_now in raw_data."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"stars": 100},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert len(result.why_now_parts) >= 1
        assert "github_spike" in result.why_now_parts[0].lower() or "detected" in result.why_now_parts[0].lower()

    def test_deduplicates_identical_why_now(self):
        """Should not include duplicate why_now entries."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="funding_event",
                source_api="crunchbase",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={"why_now": "Just raised $5M"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="funding_event",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"why_now": "Just raised $5M"},  # Same
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert len(result.why_now_parts) == 1
        assert result.why_now_parts[0] == "Just raised $5M"

    def test_ignores_empty_why_now(self):
        """Should ignore empty or whitespace-only why_now values."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="funding_event",
                source_api="crunchbase",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={"why_now": "Valid reason"},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={"why_now": "   "},  # Whitespace only
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=3,
                signal_type="incorporation",
                source_api="sec_edgar",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.7,
                raw_data={"why_now": ""},  # Empty
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert len(result.why_now_parts) == 1
        assert result.why_now_parts[0] == "Valid reason"


class TestWeightedConfidence:
    """Test weighted confidence calculation."""

    def test_weights_confidence_by_source_priority(self):
        """Higher priority sources should have more weight in confidence."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",  # Low priority (7)
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="companies_house",  # High priority (1)
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.6,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        # Weighted average should be closer to 0.6 than simple average of 0.75
        # Simple average = (0.9 + 0.6) / 2 = 0.75
        # With weighting, Companies House signal should pull it down
        assert result.aggregated_confidence < 0.75
        assert result.aggregated_confidence > 0.6

    def test_single_signal_uses_own_confidence(self):
        """Single signal should use its own confidence."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        assert result.aggregated_confidence == 0.85

    def test_unknown_source_uses_default_priority(self):
        """Unknown sources should use default priority (low weight)."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="some_signal",
                source_api="unknown_source",  # Default priority (99)
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="incorporation",
                source_api="sec_edgar",  # Priority 2
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.5,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        # SEC EDGAR has high weight (priority 2 -> weight 9)
        # Unknown source has low weight (clamped priority 10 -> weight 1)
        # Weighted avg should be closer to 0.5 (SEC EDGAR)
        assert result.aggregated_confidence < 0.7  # Simple average would be 0.7

    def test_equal_priority_sources_equal_weight(self):
        """Sources with equal priority should contribute equally."""
        from storage.signal_store import StoredSignal
        from utils.signal_consolidator import SignalConsolidator

        now = datetime.now(timezone.utc)
        signals = [
            StoredSignal(
                id=1,
                signal_type="github_spike",
                source_api="github",  # Priority 7
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.8,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
            StoredSignal(
                id=2,
                signal_type="github_activity",
                source_api="github",  # Priority 7 (same)
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.6,
                raw_data={},
                detected_at=now,
                created_at=now,
            ),
        ]
        consolidator = SignalConsolidator()
        result = consolidator.consolidate(signals)
        # Equal weights means simple average: (0.8 + 0.6) / 2 = 0.7
        assert result.aggregated_confidence == pytest.approx(0.7, abs=0.001)


# =============================================================================
# PHASE G EXTENSIONS
# =============================================================================


class TestGetPrimaryDescription:
    """Test get_primary_description safe accessor (Phase G)."""

    def test_returns_first_description(self):
        """Should return first description from list."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            descriptions=["First description", "Second description"],
        )

        assert consolidated.get_primary_description() == "First description"

    def test_returns_empty_string_for_empty_list(self):
        """Should return empty string when descriptions list is empty."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            descriptions=[],
        )

        assert consolidated.get_primary_description() == ""

    def test_handles_none_in_list(self):
        """Should handle None as first element gracefully."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            descriptions=[None, "Second"],
        )

        # Should handle None gracefully
        result = consolidated.get_primary_description()
        assert result == "" or result == "Second"


class TestToPublic:
    """Test to_public() output method (Phase G)."""

    def test_returns_dict(self):
        """to_public() should return a dictionary."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2],
            signal_types=["github_spike", "incorporation"],
            source_apis=["github", "companies_house"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_public()
        assert isinstance(result, dict)

    def test_includes_canonical_key(self):
        """to_public() should include canonical_key."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_public()
        assert result["canonical_key"] == "domain:acme.ai"

    def test_includes_company_name(self):
        """to_public() should include company_name."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_public()
        assert result["company_name"] == "Acme Inc"

    def test_includes_description_via_safe_accessor(self):
        """to_public() should use get_primary_description()."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            descriptions=["A great company"],
        )

        result = consolidated.to_public()
        assert result["description"] == "A great company"

    def test_includes_confidence(self):
        """to_public() should include aggregated_confidence."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.85,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_public()
        assert result["confidence"] == 0.85

    def test_includes_signal_count(self):
        """to_public() should include signal_count."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2, 3],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_public()
        assert result["signal_count"] == 3

    def test_excludes_audit_fields(self):
        """to_public() should NOT include audit fields."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_public()
        # Should not include internal audit fields
        assert "contributing_signal_ids" not in result
        assert "merged_raw_data" not in result
        assert "raw_signal_bundle" not in result
        assert "field_choices" not in result
        assert "field_candidates" not in result


class TestToAudit:
    """Test to_audit() output method (Phase G)."""

    def test_returns_dict(self):
        """to_audit() should return a dictionary."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_audit()
        assert isinstance(result, dict)

    def test_includes_public_fields(self):
        """to_audit() should include all public fields."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_audit()
        assert "canonical_key" in result
        assert "company_name" in result

    def test_includes_contributing_signal_ids(self):
        """to_audit() should include contributing_signal_ids."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2, 3],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_audit()
        assert result["contributing_signal_ids"] == [1, 2, 3]

    def test_includes_source_apis(self):
        """to_audit() should include source_apis."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike", "incorporation"],
            source_apis=["github", "companies_house"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_audit()
        assert "github" in result["source_apis"]
        assert "companies_house" in result["source_apis"]

    def test_includes_conflict_flags(self):
        """to_audit() should include conflict_flags."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1, 2],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            conflict_flags=[
                ConflictFlag(
                    field="company_name",
                    values=["Acme Inc", "ACME Corp"],
                    severity="warning",
                )
            ],
        )

        result = consolidated.to_audit()
        assert "conflict_flags" in result
        assert len(result["conflict_flags"]) == 1

    def test_includes_policy_version(self):
        """to_audit() should include policy_version."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        result = consolidated.to_audit()
        assert "policy_version" in result


class TestPhaseGAuditFields:
    """Test Phase G audit fields on ConsolidatedSignal."""

    def test_has_policy_version_field(self):
        """ConsolidatedSignal should have policy_version field."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
            policy_version="g_v1.0",
        )

        assert consolidated.policy_version == "g_v1.0"

    def test_has_field_choices_field(self):
        """ConsolidatedSignal should have field_choices field."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        assert hasattr(consolidated, "field_choices")
        assert isinstance(consolidated.field_choices, dict)

    def test_has_field_candidates_field(self):
        """ConsolidatedSignal should have field_candidates field."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        assert hasattr(consolidated, "field_candidates")
        assert isinstance(consolidated.field_candidates, dict)

    def test_has_field_conflicts_field(self):
        """ConsolidatedSignal should have field_conflicts field."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        assert hasattr(consolidated, "field_conflicts")
        assert isinstance(consolidated.field_conflicts, dict)

    def test_has_raw_signal_bundle_field(self):
        """ConsolidatedSignal should have raw_signal_bundle field."""
        now = datetime.now(timezone.utc)

        consolidated = ConsolidatedSignal(
            canonical_key="domain:acme.ai",
            company_name="Acme Inc",
            contributing_signal_ids=[1],
            signal_types=["github_spike"],
            source_apis=["github"],
            aggregated_confidence=0.75,
            earliest_detected_at=now,
            latest_detected_at=now,
        )

        assert hasattr(consolidated, "raw_signal_bundle")
        assert isinstance(consolidated.raw_signal_bundle, list)


class TestMergeScoringIntegration:
    """Tests for Phase G merge scoring in SignalConsolidator."""

    def _make_stored_signal(
        self,
        id: int,
        source_api: str,
        company_name: str,
        confidence: float,
        detected_at: datetime = None,
    ):
        """Helper to create StoredSignal for testing."""
        from storage.signal_store import StoredSignal

        detected_at = detected_at or datetime.now(timezone.utc)
        return StoredSignal(
            id=id,
            signal_type="test_signal",
            source_api=source_api,
            canonical_key="domain:test.com",
            company_name=company_name,
            confidence=confidence,
            raw_data={},
            detected_at=detected_at,
            created_at=detected_at,
        )

    def test_pick_highest_score_selects_by_effective_score(self):
        """pick_highest_score should select candidate with highest effective score."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)

        # Companies House has higher authority than GitHub
        signals = [
            self._make_stored_signal(1, "github", "acme-repo", 0.9, now),
            self._make_stored_signal(2, "companies_house", "Acme Ltd", 0.7, now),
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]
        winner, score = consolidator.pick_highest_score(signals, policy, "company_name")

        # Companies House (authority ~0.95, conf 0.7) should beat GitHub (authority ~0.55, conf 0.9)
        assert winner.company_name == "Acme Ltd"

    def test_pick_highest_score_confidence_breaks_authority_tie(self):
        """Higher confidence wins when authority is equal."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)

        # Same source (same authority), different confidence
        signals = [
            self._make_stored_signal(1, "crunchbase", "Acme Corp", 0.6, now),
            self._make_stored_signal(2, "crunchbase", "Acme Inc", 0.9, now),  # Higher confidence
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]
        winner, score = consolidator.pick_highest_score(signals, policy, "company_name")

        assert winner.company_name == "Acme Inc"

    def test_pick_highest_score_deterministic_tie_breaker_recency(self):
        """More recent signal wins when score and authority tie."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES
        from datetime import timedelta

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(days=10)

        # Same source, same confidence, different time
        signals = [
            self._make_stored_signal(1, "crunchbase", "Acme Corp", 0.8, earlier),
            self._make_stored_signal(2, "crunchbase", "Acme Inc", 0.8, now),  # More recent
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]
        winner, score = consolidator.pick_highest_score(signals, policy, "company_name")

        # More recent wins as tie-breaker
        assert winner.company_name == "Acme Inc"

    def test_pick_highest_score_deterministic_final_fallback(self):
        """Lexical ordering on normalized value ensures determinism."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)

        # Exact same score, authority, recency - use lexical on normalized value
        signals = [
            self._make_stored_signal(1, "crunchbase", "Zebra Inc", 0.8, now),
            self._make_stored_signal(2, "crunchbase", "Alpha Inc", 0.8, now),  # Alpha < Zebra lexically
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]
        winner, score = consolidator.pick_highest_score(signals, policy, "company_name")

        # Alphabetically first normalized value wins as final tie-breaker
        assert winner.company_name == "Alpha Inc"

    def test_pick_highest_score_returns_score(self):
        """pick_highest_score should return the winning score."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)

        signals = [
            self._make_stored_signal(1, "companies_house", "Acme Ltd", 0.9, now),
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]
        winner, score = consolidator.pick_highest_score(signals, policy, "company_name")

        # companies_house authority ~0.95, confidence 0.9 => score ~0.855
        assert 0.8 <= score <= 0.95
        assert winner is not None

    def test_pick_highest_score_empty_signals_raises(self):
        """pick_highest_score should raise on empty signal list."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        policy = FIELD_MERGE_POLICIES["company_name"]

        with pytest.raises(ValueError, match="No signals"):
            consolidator.pick_highest_score([], policy, "company_name")

    def test_pick_highest_score_skips_empty_values(self):
        """pick_highest_score should skip signals with empty field values."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)

        signals = [
            self._make_stored_signal(1, "companies_house", "", 0.9, now),  # Empty
            self._make_stored_signal(2, "github", "Acme Repo", 0.8, now),  # Has value
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]
        winner, score = consolidator.pick_highest_score(signals, policy, "company_name")

        assert winner.company_name == "Acme Repo"

    def test_pick_highest_score_all_empty_raises(self):
        """pick_highest_score should raise if all signals have empty values."""
        from utils.signal_consolidator import SignalConsolidator
        from utils.merge_policy import FIELD_MERGE_POLICIES

        consolidator = SignalConsolidator()
        now = datetime.now(timezone.utc)

        signals = [
            self._make_stored_signal(1, "companies_house", "", 0.9, now),
            self._make_stored_signal(2, "github", None, 0.8, now),
        ]

        policy = FIELD_MERGE_POLICIES["company_name"]

        with pytest.raises(ValueError, match="No candidates"):
            consolidator.pick_highest_score(signals, policy, "company_name")
