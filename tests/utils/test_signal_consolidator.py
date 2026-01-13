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
