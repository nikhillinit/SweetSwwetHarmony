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
