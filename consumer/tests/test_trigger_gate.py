"""Tests for TriggerGate deterministic signal filtering."""
import pytest
from consumer.trigger_gate import TriggerGate, TriggerResult, ChangeType


class TestTriggerGate:
    """Test suite for TriggerGate."""

    def test_description_change_over_threshold_triggers(self):
        """Description change >20% should trigger classification."""
        gate = TriggerGate(description_threshold=0.2)

        old = {"description": "A fitness tracking app for consumers"}
        new = {"description": "Enterprise wellness platform for HR teams with analytics dashboard"}

        result = gate.should_classify(old, new)

        assert result.should_trigger is True
        assert ChangeType.DESCRIPTION_CHANGE in result.change_types
        assert result.trigger_reason is not None

    def test_minor_description_change_skipped(self):
        """Description change <20% should NOT trigger."""
        gate = TriggerGate(description_threshold=0.2)

        old = {"description": "A fitness tracking app"}
        new = {"description": "A fitness tracking application"}  # Minor change

        result = gate.should_classify(old, new)

        assert result.should_trigger is False

    def test_domain_change_triggers(self):
        """Domain/homepage change should trigger."""
        gate = TriggerGate()

        old = {"description": "Fitness app", "homepage": "https://fitapp.com"}
        new = {"description": "Fitness app", "homepage": "https://enterprise-wellness.io"}

        result = gate.should_classify(old, new)

        assert result.should_trigger is True
        assert ChangeType.DOMAIN_CHANGE in result.change_types

    def test_pivot_keyword_triggers(self):
        """New pivot keywords should trigger."""
        gate = TriggerGate()

        old = {"description": "Consumer fitness tracking"}
        new = {"description": "Enterprise fitness platform with API"}

        result = gate.should_classify(old, new)

        assert result.should_trigger is True
        assert ChangeType.KEYWORD_SWAP in result.change_types

    def test_empty_old_snapshot_no_trigger(self):
        """First observation should not trigger (no baseline)."""
        gate = TriggerGate()

        old = {}
        new = {"description": "A great new startup"}

        result = gate.should_classify(old, new)

        # First observation = no comparison possible
        assert result.should_trigger is False

    def test_identical_snapshots_no_trigger(self):
        """Identical data should not trigger."""
        gate = TriggerGate()

        snapshot = {"description": "Fitness app", "homepage": "https://fit.com"}

        result = gate.should_classify(snapshot, snapshot)

        assert result.should_trigger is False

    def test_multiple_changes_aggregated(self):
        """Multiple change types should all be captured."""
        gate = TriggerGate()

        old = {
            "description": "Consumer mobile app",
            "homepage": "https://consumer.app"
        }
        new = {
            "description": "Enterprise B2B SaaS platform with API integrations",
            "homepage": "https://enterprise-platform.io"
        }

        result = gate.should_classify(old, new)

        assert result.should_trigger is True
        assert len(result.change_types) >= 2
        assert result.change_magnitude > 0

    def test_none_description_handled(self):
        """None descriptions should be handled gracefully."""
        gate = TriggerGate()

        old = {"description": None}
        new = {"description": "New description"}

        result = gate.should_classify(old, new)

        # No baseline description = no trigger
        assert result.should_trigger is False

    def test_custom_pivot_keywords(self):
        """Custom pivot keywords should be used."""
        gate = TriggerGate(pivot_keywords=["shutdown", "acquired", "deprecated"])

        old = {"description": "Active startup product"}
        new = {"description": "This product has been deprecated"}

        result = gate.should_classify(old, new)

        assert result.should_trigger is True
        assert ChangeType.KEYWORD_SWAP in result.change_types

    def test_change_magnitude_reflects_severity(self):
        """Change magnitude should reflect how significant the change is."""
        gate = TriggerGate()

        # Domain change = high magnitude
        old = {"description": "App", "homepage": "https://a.com"}
        new = {"description": "App", "homepage": "https://b.com"}

        result = gate.should_classify(old, new)

        assert result.change_magnitude >= 0.8  # Domain changes are severe
