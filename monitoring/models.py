"""
Data Models for Monitoring Subsystem

Dataclasses representing monitoring entities: Watch, Snapshot, Diff, Alert.
These mirror the database schema from migration 10.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json


@dataclass
class Watch:
    """
    A URL being monitored for changes.

    Maps to the `watches` table.
    """
    id: Optional[int] = None
    canonical_key: str = ""
    url: str = ""
    watch_type: str = "website"  # website, portfolio, linkedin_about
    interval_seconds: int = 86400  # 24 hours

    # Operational state
    last_checked_at: Optional[datetime] = None
    last_snapshot_id: Optional[int] = None
    consecutive_failures: int = 0
    backoff_until: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    consecutive_low_sev_hits: int = 0
    last_low_sev_at: Optional[datetime] = None

    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_due(self, now: Optional[datetime] = None) -> bool:
        """Check if this watch is due for checking."""
        now = now or datetime.now(timezone.utc)

        # Not active
        if not self.active:
            return False

        # In backoff
        if self.backoff_until and now < self.backoff_until:
            return False

        # In cooldown
        if self.cooldown_until and now < self.cooldown_until:
            return False

        # Never checked
        if self.last_checked_at is None:
            return True

        # Check interval
        from datetime import timedelta
        next_check = self.last_checked_at + timedelta(seconds=self.interval_seconds)
        return now >= next_check

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "canonical_key": self.canonical_key,
            "url": self.url,
            "watch_type": self.watch_type,
            "interval_seconds": self.interval_seconds,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "last_snapshot_id": self.last_snapshot_id,
            "consecutive_failures": self.consecutive_failures,
            "backoff_until": self.backoff_until.isoformat() if self.backoff_until else None,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class Snapshot:
    """
    An immutable record of a page fetch.

    Maps to the `snapshots` table.
    """
    id: Optional[int] = None
    watch_id: int = 0
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status_code: Optional[int] = None

    # URL chain
    requested_url: str = ""
    final_url: Optional[str] = None
    final_host: Optional[str] = None

    # Content
    page_state: Optional[str] = None  # live, coming_soon, blocked, error, unknown
    content_hash: str = ""
    text_length: int = 0
    text_content_preview: Optional[str] = None

    # Embedding
    embedding_key: Optional[str] = None

    # Error
    error: Optional[str] = None

    # Metadata
    metadata: Optional[Dict[str, Any]] = None

    @property
    def success(self) -> bool:
        """Check if fetch was successful."""
        return self.status_code == 200 and self.error is None

    @property
    def has_redirect(self) -> bool:
        """Check if there was a redirect to a different host."""
        if not self.final_url or not self.requested_url:
            return False
        from urllib.parse import urlparse
        req_host = urlparse(self.requested_url).netloc.lower()
        final_host = urlparse(self.final_url).netloc.lower()
        return req_host != final_host

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "watch_id": self.watch_id,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "status_code": self.status_code,
            "requested_url": self.requested_url,
            "final_url": self.final_url,
            "final_host": self.final_host,
            "page_state": self.page_state,
            "content_hash": self.content_hash,
            "text_length": self.text_length,
            "embedding_key": self.embedding_key,
            "error": self.error,
            "success": self.success,
            "has_redirect": self.has_redirect,
        }


@dataclass
class SeverityComponents:
    """Components that make up the severity score."""
    content_delta: float = 0.0  # 0-1 based on text length change ratio
    semantic_drift: Optional[float] = None  # 0-1, None if not computable
    state_change: float = 0.0  # 1.0 if page_state changed
    redirect: float = 0.0  # 1.0 if host changed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_delta": self.content_delta,
            "semantic_drift": self.semantic_drift,
            "state_change": self.state_change,
            "redirect": self.redirect,
        }


@dataclass
class Diff:
    """
    Computed difference between two snapshots.

    Maps to the `diffs` table.
    """
    id: Optional[int] = None
    watch_id: int = 0
    old_snapshot_id: Optional[int] = None
    new_snapshot_id: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Severity
    severity_score: float = 0.0
    severity_components: Optional[SeverityComponents] = None
    semantic_drift: Optional[float] = None

    # Flags
    has_redirect: bool = False
    has_state_change: bool = False
    has_text_change: bool = False

    # Summary
    diff_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "watch_id": self.watch_id,
            "old_snapshot_id": self.old_snapshot_id,
            "new_snapshot_id": self.new_snapshot_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "severity_score": self.severity_score,
            "severity_components": self.severity_components.to_dict() if self.severity_components else None,
            "semantic_drift": self.semantic_drift,
            "has_redirect": self.has_redirect,
            "has_state_change": self.has_state_change,
            "has_text_change": self.has_text_change,
            "diff_summary": self.diff_summary,
        }


@dataclass
class MonitoringAlert:
    """
    An alert requiring acknowledgement.

    Maps to the `monitoring_alerts` table.
    """
    id: Optional[int] = None
    watch_id: int = 0
    diff_id: Optional[int] = None
    alert_reason: str = ""  # high_severity, host_changed, status_410, etc.
    severity_score: float = 0.0

    # Acknowledgement
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "watch_id": self.watch_id,
            "diff_id": self.diff_id,
            "alert_reason": self.alert_reason,
            "severity_score": self.severity_score,
            "acknowledged": self.acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "payload": self.payload,
        }


@dataclass
class CanonicalKeyAlias:
    """
    Tracks when a company rebrands or redirects to a new domain.

    Maps to the `canonical_key_aliases` table.
    """
    id: Optional[int] = None
    old_key: str = ""
    new_key: str = ""
    reason: Optional[str] = None  # redirect, rebrand, merge
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "old_key": self.old_key,
            "new_key": self.new_key,
            "reason": self.reason,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }


@dataclass
class MonitoringConfig:
    """
    Configuration for the monitoring subsystem.

    Loaded from the `monitoring_config` table.
    """
    # Severity thresholds
    action_threshold: float = 0.20  # Below this: store diff only
    profile_threshold: float = 0.60  # Above this: trigger profile update
    alert_threshold: float = 0.80  # Above this: create alert

    # Debounce settings
    debounce_window_hours: int = 72
    debounce_count: int = 2

    # Cooldown after profile update
    cooldown_hours: int = 24

    # Semantic drift
    semantic_drift_threshold: float = 0.85

    # Severity weights
    weight_content: float = 0.30
    weight_semantic: float = 0.40
    weight_state: float = 0.15
    weight_redirect: float = 0.15

    # Retention
    max_snapshots_per_watch: int = 10
    max_diff_age_days: int = 90

    @classmethod
    def from_json(cls, json_str: str) -> "MonitoringConfig":
        """Parse from JSON string."""
        data = json.loads(json_str)
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "action_threshold": self.action_threshold,
            "profile_threshold": self.profile_threshold,
            "alert_threshold": self.alert_threshold,
            "debounce_window_hours": self.debounce_window_hours,
            "debounce_count": self.debounce_count,
            "cooldown_hours": self.cooldown_hours,
            "semantic_drift_threshold": self.semantic_drift_threshold,
            "weight_content": self.weight_content,
            "weight_semantic": self.weight_semantic,
            "weight_state": self.weight_state,
            "weight_redirect": self.weight_redirect,
            "max_snapshots_per_watch": self.max_snapshots_per_watch,
            "max_diff_age_days": self.max_diff_age_days,
        })
