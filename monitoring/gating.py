"""
Gating Rules for Monitoring Subsystem

Determines when diffs trigger alerts and/or profile updates.
Implements Spec v2.4 Section 10.7.

Severity thresholds:
- 0 (maintenance): No alert, no profile update
- 0.01-0.29 (low): No alert (but tracked), no profile update
- 0.30-0.59 (medium): Alert, no profile update
- 0.60-0.89 (high): Alert + profile update
- 0.90-1.00 (critical): Alert (instant, bypasses cooldown) + profile update

Instant triggers (bypass severity calculation):
- domain_change: severity 1.0
- gone (404/410 after 2xx): severity 0.95
- parked_detected: severity 0.90
- ssl_downgrade: severity 0.85
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from monitoring.models import Watch, Snapshot, Diff, MonitoringConfig


@dataclass
class GatingConfig:
    """Configuration for gating rules (from config/monitoring.json)."""
    # Severity thresholds
    alert_threshold: float = 0.30
    profile_update_threshold: float = 0.60
    critical_threshold: float = 0.90

    # Cooldown settings
    low_sev_cooldown_threshold: int = 5  # Consecutive low-sev hits before cooldown
    cooldown_hours: int = 24  # Extended cooldown duration
    post_alert_cooldown_minutes: int = 60  # Cooldown after alerting

    # Severity weights
    weight_content_delta: float = 0.30
    weight_redirect_change: float = 0.25
    weight_state_change: float = 0.35
    weight_semantic_drift: float = 0.10


@dataclass
class SeverityResult:
    """Result of severity calculation."""
    score: float
    components: dict
    instant_trigger: Optional[str] = None
    instant_trigger_severity: Optional[float] = None


@dataclass
class GatingDecision:
    """Decision from gating rules."""
    should_alert: bool
    should_enqueue_profile_update: bool
    reason: str
    severity_score: float
    bypassed_cooldown: bool = False


class GatingEngine:
    """
    Applies gating rules to determine actions from diffs.

    Usage:
        engine = GatingEngine(config)

        # Check for instant triggers first
        trigger = engine.check_instant_triggers(old_snapshot, new_snapshot)
        if trigger:
            severity = trigger[1]
        else:
            severity = engine.calculate_severity(diff, config)

        # Determine actions
        decision = engine.decide(watch, severity, diff)

        # Update cooldown state
        engine.update_cooldown_state(watch, severity, decision.should_alert, store)
    """

    def __init__(self, config: Optional[GatingConfig] = None):
        """
        Initialize gating engine.

        Args:
            config: Gating configuration (defaults to GatingConfig())
        """
        self.config = config or GatingConfig()

    def check_instant_triggers(
        self,
        old_snapshot: Optional["Snapshot"],
        new_snapshot: "Snapshot",
    ) -> Optional[Tuple[str, float]]:
        """
        Check for instant triggers that bypass severity calculation.

        Args:
            old_snapshot: Previous snapshot (None if first)
            new_snapshot: New snapshot

        Returns:
            Tuple of (trigger_name, severity) or None
        """
        if old_snapshot is None:
            return None

        # 1. Domain change (host changed) -> severity 1.0
        if new_snapshot.has_redirect:
            old_host = old_snapshot.final_host or ""
            new_host = new_snapshot.final_host or ""
            if old_host and new_host and old_host.lower() != new_host.lower():
                return ("domain_change", 1.0)

        # 2. Gone (404/410 after previous 2xx) -> severity 0.95
        old_status = old_snapshot.status_code or 0
        new_status = new_snapshot.status_code or 0
        if 200 <= old_status < 300 and new_status in (404, 410):
            return ("gone", 0.95)

        # 3. Parked detected -> severity 0.90
        old_state = old_snapshot.page_state or ""
        new_state = new_snapshot.page_state or ""
        if old_state != "parked" and new_state == "parked":
            return ("parked_detected", 0.90)

        # 4. SSL downgrade (HTTPS -> HTTP) -> severity 0.85
        old_url = old_snapshot.final_url or old_snapshot.requested_url or ""
        new_url = new_snapshot.final_url or new_snapshot.requested_url or ""
        if old_url.startswith("https://") and new_url.startswith("http://"):
            return ("ssl_downgrade", 0.85)

        return None

    def calculate_severity(
        self,
        diff: "Diff",
        old_snapshot: Optional["Snapshot"] = None,
        new_snapshot: Optional["Snapshot"] = None,
    ) -> SeverityResult:
        """
        Calculate severity score from diff components.

        Uses weighted combination per Spec v2.4 Section 10.7.2.

        Args:
            diff: The computed diff
            old_snapshot: Previous snapshot (for instant trigger check)
            new_snapshot: New snapshot (for instant trigger check)

        Returns:
            SeverityResult with score and components
        """
        # Check instant triggers first
        instant_trigger = None
        instant_severity = None
        if old_snapshot and new_snapshot:
            trigger = self.check_instant_triggers(old_snapshot, new_snapshot)
            if trigger:
                instant_trigger, instant_severity = trigger

        components = diff.severity_components
        if not components:
            return SeverityResult(
                score=instant_severity or 0.0,
                components={},
                instant_trigger=instant_trigger,
                instant_trigger_severity=instant_severity,
            )

        # Weighted severity calculation
        weights = {
            "content_delta": self.config.weight_content_delta,
            "redirect_change": self.config.weight_redirect_change,
            "state_change": self.config.weight_state_change,
            "semantic_drift": self.config.weight_semantic_drift,
        }

        component_scores = {
            "content_delta": min(1.0, components.content_delta or 0.0) * weights["content_delta"],
            "redirect_change": (1.0 if diff.has_redirect else 0.0) * weights["redirect_change"],
            "state_change": (1.0 if diff.has_state_change else 0.0) * weights["state_change"],
            "semantic_drift": (components.semantic_drift or 0.0) * weights["semantic_drift"],
        }

        calculated_score = min(1.0, sum(component_scores.values()))

        # Use instant trigger severity if higher
        final_score = instant_severity if instant_severity and instant_severity > calculated_score else calculated_score

        return SeverityResult(
            score=final_score,
            components=component_scores,
            instant_trigger=instant_trigger,
            instant_trigger_severity=instant_severity,
        )

    def should_alert(
        self,
        watch: "Watch",
        severity: float,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Determine if an alert should be created.

        Args:
            watch: The watch being checked
            severity: Calculated severity score
            now: Current time (defaults to utcnow)

        Returns:
            True if alert should be created
        """
        now = now or datetime.now(timezone.utc)

        # Critical severity bypasses cooldown
        if severity >= self.config.critical_threshold:
            return True

        # Respect cooldown
        if watch.cooldown_until and now < watch.cooldown_until:
            return False

        # Medium+ severity alerts (outside cooldown)
        return severity >= self.config.alert_threshold

    def should_enqueue_profile_update(
        self,
        severity: float,
        diff: "Diff",
    ) -> bool:
        """
        Determine if a profile update should be enqueued.

        Profile updates are more expensive (LLM calls), so gating is stricter.

        Args:
            severity: Calculated severity score
            diff: The computed diff

        Returns:
            True if profile update should be enqueued
        """
        # Must meet profile update threshold
        if severity < self.config.profile_update_threshold:
            return False

        # Must have meaningful content change (not just redirect)
        text_length_delta = 0
        if diff.diff_summary and "length_change" in diff.diff_summary:
            text_length_delta = abs(diff.diff_summary["length_change"])

        if text_length_delta == 0 and not diff.has_state_change:
            return False

        return True

    def decide(
        self,
        watch: "Watch",
        severity: float,
        diff: "Diff",
        now: Optional[datetime] = None,
    ) -> GatingDecision:
        """
        Make a complete gating decision.

        Args:
            watch: The watch being checked
            severity: Calculated severity score
            diff: The computed diff
            now: Current time

        Returns:
            GatingDecision with all action flags
        """
        now = now or datetime.now(timezone.utc)

        should_alert = self.should_alert(watch, severity, now)
        should_profile = self.should_enqueue_profile_update(severity, diff)

        # Determine reason
        if severity < self.config.alert_threshold:
            reason = "low_severity"
        elif severity >= self.config.critical_threshold:
            reason = "critical_severity"
            bypassed_cooldown = watch.cooldown_until and now < watch.cooldown_until
        else:
            reason = "medium_severity" if severity < self.config.profile_update_threshold else "high_severity"
            bypassed_cooldown = False

        return GatingDecision(
            should_alert=should_alert,
            should_enqueue_profile_update=should_profile,
            reason=reason,
            severity_score=severity,
            bypassed_cooldown=severity >= self.config.critical_threshold and watch.cooldown_until is not None,
        )

    def calculate_new_cooldown_state(
        self,
        watch: "Watch",
        severity: float,
        alerted: bool,
        now: Optional[datetime] = None,
    ) -> dict:
        """
        Calculate updated cooldown state for a watch.

        Args:
            watch: Current watch state
            severity: Severity of this diff
            alerted: Whether an alert was created
            now: Current time

        Returns:
            Dict with updated cooldown fields:
            - cooldown_until: Optional[datetime]
            - consecutive_low_sev_hits: int
            - last_low_sev_at: Optional[datetime]
        """
        now = now or datetime.now(timezone.utc)

        if severity < self.config.alert_threshold:
            # Low severity - track consecutive hits
            new_count = watch.consecutive_low_sev_hits + 1

            # If too many low-sev in short window, enter cooldown
            if new_count >= self.config.low_sev_cooldown_threshold:
                return {
                    "cooldown_until": now + timedelta(hours=self.config.cooldown_hours),
                    "consecutive_low_sev_hits": 0,
                    "last_low_sev_at": None,
                }

            return {
                "cooldown_until": watch.cooldown_until,
                "consecutive_low_sev_hits": new_count,
                "last_low_sev_at": now,
            }

        # Medium+ severity resets low-sev tracking
        result = {
            "consecutive_low_sev_hits": 0,
            "last_low_sev_at": None,
        }

        if alerted:
            # Standard post-alert cooldown
            result["cooldown_until"] = now + timedelta(minutes=self.config.post_alert_cooldown_minutes)
        else:
            result["cooldown_until"] = watch.cooldown_until

        return result


# Convenience functions
def calculate_severity(
    diff: "Diff",
    config: Optional[GatingConfig] = None,
) -> float:
    """Calculate severity score from a diff."""
    engine = GatingEngine(config)
    result = engine.calculate_severity(diff)
    return result.score


def should_alert(
    watch: "Watch",
    severity: float,
    config: Optional[GatingConfig] = None,
) -> bool:
    """Check if an alert should be created."""
    return GatingEngine(config).should_alert(watch, severity)


def should_enqueue_profile_update(
    severity: float,
    diff: "Diff",
    config: Optional[GatingConfig] = None,
) -> bool:
    """Check if a profile update should be enqueued."""
    return GatingEngine(config).should_enqueue_profile_update(severity, diff)
