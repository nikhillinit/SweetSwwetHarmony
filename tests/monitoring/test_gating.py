"""Tests for gating rules (v2.4)."""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from monitoring.gating import (
    GatingEngine,
    GatingConfig,
    GatingDecision,
    SeverityResult,
)
from monitoring.models import Watch, Snapshot, Diff, SeverityComponents


@pytest.fixture
def default_config():
    """Default gating configuration."""
    return GatingConfig(
        alert_threshold=0.30,
        profile_update_threshold=0.60,
        critical_threshold=0.90,
        low_sev_cooldown_threshold=5,
        cooldown_hours=24,
        post_alert_cooldown_minutes=60,
        weight_content_delta=0.30,
        weight_redirect_change=0.25,
        weight_state_change=0.35,
        weight_semantic_drift=0.10,
    )


@pytest.fixture
def gating_engine(default_config):
    """Gating engine with default config."""
    return GatingEngine(default_config)


@pytest.fixture
def sample_watch():
    """Sample watch for testing."""
    return Watch(
        id=1,
        canonical_key="domain:acme.ai",
        url="https://acme.ai",
        watch_type="website",
        interval_seconds=86400,
        active=True,
        consecutive_failures=0,
        consecutive_low_sev_hits=0,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def old_snapshot():
    """Previous snapshot for comparison."""
    return Snapshot(
        id=1,
        watch_id=1,
        fetched_at=datetime.now(timezone.utc) - timedelta(days=1),
        status_code=200,
        requested_url="https://acme.ai",
        final_url="https://acme.ai",
        final_host="acme.ai",
        page_state="live",
        content_hash="abc123",
        hasher_version="v1",
        text_length=5000,
    )


@pytest.fixture
def new_snapshot():
    """New snapshot with changes."""
    return Snapshot(
        id=2,
        watch_id=1,
        fetched_at=datetime.now(timezone.utc),
        status_code=200,
        requested_url="https://acme.ai",
        final_url="https://acme.ai",
        final_host="acme.ai",
        page_state="live",
        content_hash="def456",
        hasher_version="v1",
        text_length=5500,
    )


class TestInstantTriggers:
    """Test instant trigger detection."""

    def test_domain_change_triggers(self, gating_engine, old_snapshot, new_snapshot):
        """Domain change should trigger instant severity 1.0."""
        # Need to simulate redirect for domain change detection
        new_snapshot.final_url = "https://different-domain.com/page"
        new_snapshot.final_host = "different-domain.com"

        result = gating_engine.check_instant_triggers(old_snapshot, new_snapshot)

        assert result is not None
        trigger, severity = result
        assert trigger == "domain_change"
        assert severity == 1.0

    def test_gone_page_triggers(self, gating_engine, old_snapshot, new_snapshot):
        """Page going to 404 should trigger instant severity 0.95."""
        new_snapshot.status_code = 404

        result = gating_engine.check_instant_triggers(old_snapshot, new_snapshot)

        assert result is not None
        trigger, severity = result
        assert trigger == "gone"
        assert severity == 0.95

    def test_gone_410_triggers(self, gating_engine, old_snapshot, new_snapshot):
        """Page going to 410 should trigger instant severity 0.95."""
        new_snapshot.status_code = 410

        result = gating_engine.check_instant_triggers(old_snapshot, new_snapshot)

        assert result is not None
        trigger, severity = result
        assert trigger == "gone"
        assert severity == 0.95

    def test_parked_page_triggers(self, gating_engine, old_snapshot, new_snapshot):
        """Page going to 'parked' state should trigger instant severity 0.90."""
        new_snapshot.page_state = "parked"

        result = gating_engine.check_instant_triggers(old_snapshot, new_snapshot)

        assert result is not None
        trigger, severity = result
        assert trigger == "parked_detected"
        assert severity == 0.90

    def test_ssl_downgrade_triggers(self, gating_engine, old_snapshot, new_snapshot):
        """HTTPS to HTTP should trigger instant severity 0.85."""
        old_snapshot.final_url = "https://acme.ai"
        new_snapshot.final_url = "http://acme.ai"

        result = gating_engine.check_instant_triggers(old_snapshot, new_snapshot)

        assert result is not None
        trigger, severity = result
        assert trigger == "ssl_downgrade"
        assert severity == 0.85

    def test_no_instant_trigger_for_normal_change(self, gating_engine, old_snapshot, new_snapshot):
        """Normal content change should not trigger instant severity."""
        result = gating_engine.check_instant_triggers(old_snapshot, new_snapshot)
        assert result is None

    def test_no_instant_trigger_without_old_snapshot(self, gating_engine, new_snapshot):
        """No instant trigger when there's no previous snapshot."""
        result = gating_engine.check_instant_triggers(None, new_snapshot)
        assert result is None


class TestSeverityCalculation:
    """Test severity score calculation."""

    def test_severity_from_content_delta(self, gating_engine, old_snapshot, new_snapshot):
        """Content delta should contribute to severity."""
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.0,
            severity_components=SeverityComponents(
                content_delta=0.8,
                redirect=0.0,
                state_change=0.0,
                semantic_drift=0.0,
            ),
        )

        result = gating_engine.calculate_severity(diff, old_snapshot, new_snapshot)

        # 0.8 * 0.30 = 0.24
        assert 0.20 <= result.score <= 0.30

    def test_severity_from_state_change(self, gating_engine, old_snapshot, new_snapshot):
        """State change should have highest weight contribution."""
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.0,
            severity_components=SeverityComponents(
                content_delta=0.0,
                redirect=0.0,
                state_change=1.0,
                semantic_drift=0.0,
            ),
            has_state_change=True,
        )

        result = gating_engine.calculate_severity(diff, old_snapshot, new_snapshot)

        # 1.0 * 0.35 = 0.35
        assert result.score == pytest.approx(0.35, abs=0.01)

    def test_combined_severity(self, gating_engine, old_snapshot, new_snapshot):
        """Combined changes should add up."""
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.0,
            severity_components=SeverityComponents(
                content_delta=1.0,
                redirect=0.0,
                state_change=0.0,
                semantic_drift=1.0,
            ),
        )

        result = gating_engine.calculate_severity(diff, old_snapshot, new_snapshot)

        # 1.0 * 0.30 + 1.0 * 0.10 = 0.40
        assert result.score == pytest.approx(0.40, abs=0.02)

    def test_instant_trigger_overrides_calculated(self, gating_engine, old_snapshot, new_snapshot):
        """Instant trigger severity should override lower calculated severity."""
        new_snapshot.status_code = 404  # Gone trigger

        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.0,
            severity_components=SeverityComponents(),
        )

        result = gating_engine.calculate_severity(diff, old_snapshot, new_snapshot)

        assert result.score == 0.95
        assert result.instant_trigger == "gone"

    def test_severity_result_includes_components(self, gating_engine, old_snapshot, new_snapshot):
        """Severity result should include component breakdown."""
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.0,
            severity_components=SeverityComponents(content_delta=0.5),
        )

        result = gating_engine.calculate_severity(diff, old_snapshot, new_snapshot)

        assert "content_delta" in result.components


class TestAlertDecisions:
    """Test alert threshold decisions."""

    def test_alert_above_threshold(self, gating_engine, sample_watch, default_config):
        """Severity above threshold should trigger alert."""
        severity = 0.5
        now = datetime.now(timezone.utc)

        should_alert = gating_engine.should_alert(sample_watch, severity, now)

        assert should_alert is True

    def test_no_alert_below_threshold(self, gating_engine, sample_watch, default_config):
        """Severity below threshold should not trigger alert."""
        severity = 0.2
        now = datetime.now(timezone.utc)

        should_alert = gating_engine.should_alert(sample_watch, severity, now)

        assert should_alert is False

    def test_alert_at_exact_threshold(self, gating_engine, sample_watch, default_config):
        """Severity at exact threshold should trigger alert."""
        severity = 0.30
        now = datetime.now(timezone.utc)

        should_alert = gating_engine.should_alert(sample_watch, severity, now)

        assert should_alert is True

    def test_alert_cooldown_blocks(self, gating_engine, sample_watch, default_config):
        """Watch in cooldown should not alert."""
        severity = 0.5
        now = datetime.now(timezone.utc)
        sample_watch.cooldown_until = now + timedelta(hours=1)

        should_alert = gating_engine.should_alert(sample_watch, severity, now)

        assert should_alert is False

    def test_critical_bypasses_cooldown(self, gating_engine, sample_watch, default_config):
        """Critical severity should bypass cooldown."""
        severity = 0.95
        now = datetime.now(timezone.utc)
        sample_watch.cooldown_until = now + timedelta(hours=1)

        should_alert = gating_engine.should_alert(sample_watch, severity, now)

        assert should_alert is True

    def test_alert_after_cooldown_expires(self, gating_engine, sample_watch, default_config):
        """Alert should be allowed after cooldown expires."""
        severity = 0.5
        now = datetime.now(timezone.utc)
        sample_watch.cooldown_until = now - timedelta(minutes=1)

        should_alert = gating_engine.should_alert(sample_watch, severity, now)

        assert should_alert is True


class TestProfileUpdateDecisions:
    """Test profile update threshold decisions."""

    def test_profile_update_above_threshold(self, gating_engine, default_config):
        """Severity above profile update threshold should trigger."""
        severity = 0.7
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.7,
            severity_components=SeverityComponents(),
            has_state_change=True,
            diff_summary={"length_change": 500},
        )

        should_update = gating_engine.should_enqueue_profile_update(severity, diff)

        assert should_update is True

    def test_no_profile_update_below_threshold(self, gating_engine, default_config):
        """Severity below profile update threshold should not trigger."""
        severity = 0.5
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.5,
            severity_components=SeverityComponents(),
        )

        should_update = gating_engine.should_enqueue_profile_update(severity, diff)

        assert should_update is False


class TestGatingDecision:
    """Test complete gating decision."""

    def test_decision_returns_all_fields(self, gating_engine, sample_watch):
        """Decision should contain all required fields."""
        severity = 0.7
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.7,
            severity_components=SeverityComponents(),
            has_state_change=True,
            diff_summary={"length_change": 100},
        )
        now = datetime.now(timezone.utc)

        decision = gating_engine.decide(sample_watch, severity, diff, now)

        assert isinstance(decision, GatingDecision)
        assert hasattr(decision, 'should_alert')
        assert hasattr(decision, 'should_enqueue_profile_update')
        assert hasattr(decision, 'severity_score')
        assert hasattr(decision, 'reason')

    def test_decision_high_severity(self, gating_engine, sample_watch):
        """High severity should trigger both alert and profile update."""
        severity = 0.8
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.8,
            severity_components=SeverityComponents(),
            has_state_change=True,
            diff_summary={"length_change": 500},
        )
        now = datetime.now(timezone.utc)

        decision = gating_engine.decide(sample_watch, severity, diff, now)

        assert decision.should_alert is True
        assert decision.should_enqueue_profile_update is True
        assert decision.reason == "high_severity"

    def test_decision_low_severity(self, gating_engine, sample_watch):
        """Low severity should not trigger anything."""
        severity = 0.1
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.1,
            severity_components=SeverityComponents(),
        )
        now = datetime.now(timezone.utc)

        decision = gating_engine.decide(sample_watch, severity, diff, now)

        assert decision.should_alert is False
        assert decision.should_enqueue_profile_update is False
        assert decision.reason == "low_severity"

    def test_decision_medium_severity(self, gating_engine, sample_watch):
        """Medium severity should alert but not profile update."""
        severity = 0.45
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.45,
            severity_components=SeverityComponents(),
        )
        now = datetime.now(timezone.utc)

        decision = gating_engine.decide(sample_watch, severity, diff, now)

        assert decision.should_alert is True
        assert decision.should_enqueue_profile_update is False
        assert decision.reason == "medium_severity"

    def test_decision_critical_severity(self, gating_engine, sample_watch):
        """Critical severity should have special reason."""
        severity = 0.95
        diff = Diff(
            old_snapshot_id=1,
            new_snapshot_id=2,
            severity_score=0.95,
            severity_components=SeverityComponents(),
            has_state_change=True,
            diff_summary={"length_change": 1000},
        )
        now = datetime.now(timezone.utc)

        decision = gating_engine.decide(sample_watch, severity, diff, now)

        assert decision.should_alert is True
        assert decision.reason == "critical_severity"


class TestCooldownStateCalculation:
    """Test cooldown state updates."""

    def test_low_sev_increments_counter(self, gating_engine, sample_watch):
        """Low severity should increment consecutive counter."""
        severity = 0.1
        now = datetime.now(timezone.utc)

        state = gating_engine.calculate_new_cooldown_state(sample_watch, severity, alerted=False, now=now)

        assert state["consecutive_low_sev_hits"] == 1

    def test_low_sev_threshold_triggers_cooldown(self, gating_engine, sample_watch):
        """Reaching low_sev threshold should trigger extended cooldown."""
        severity = 0.1
        now = datetime.now(timezone.utc)
        sample_watch.consecutive_low_sev_hits = 4  # One more triggers threshold of 5

        state = gating_engine.calculate_new_cooldown_state(sample_watch, severity, alerted=False, now=now)

        assert state["cooldown_until"] is not None
        assert state["cooldown_until"] > now
        assert state["consecutive_low_sev_hits"] == 0  # Reset after cooldown

    def test_medium_sev_resets_counter(self, gating_engine, sample_watch):
        """Medium+ severity should reset low_sev counter."""
        severity = 0.5
        now = datetime.now(timezone.utc)
        sample_watch.consecutive_low_sev_hits = 3

        state = gating_engine.calculate_new_cooldown_state(sample_watch, severity, alerted=True, now=now)

        assert state["consecutive_low_sev_hits"] == 0

    def test_alert_sets_post_alert_cooldown(self, gating_engine, sample_watch):
        """Alerting should set post-alert cooldown."""
        severity = 0.5
        now = datetime.now(timezone.utc)

        state = gating_engine.calculate_new_cooldown_state(sample_watch, severity, alerted=True, now=now)

        assert state["cooldown_until"] is not None
        expected_cooldown = now + timedelta(minutes=60)
        assert abs((state["cooldown_until"] - expected_cooldown).total_seconds()) < 1
