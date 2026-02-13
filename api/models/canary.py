"""Canary API data transfer objects.

DTOs for canary runs, drift alerts, and trigger requests.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class CanaryRunDTO(BaseModel):
    """Canary run summary for API consumers."""

    id: int
    run_id: str
    golden_set_size: int
    golden_set_hash: str
    golden_set_version: Optional[str] = None
    total_scored: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    pass_rate: Optional[float] = None
    verdict: str
    drift_threshold: Optional[float] = None
    pass_rate_threshold: Optional[float] = None
    duration_ms: Optional[float] = None
    baseline_status: str = Field(
        default="matched",
        description="'matched', 'no_baseline', or 'incompatible'",
    )
    baseline_message: Optional[str] = None
    created_at: str


class DriftAlertDTO(BaseModel):
    """Drift alert for API consumers."""

    id: int
    canary_run_id: Optional[int] = None
    alert_type: str
    severity: str
    signal_id: Optional[int] = None
    canonical_key: Optional[str] = None
    metric_name: str
    expected_value: Optional[float] = None
    actual_value: Optional[float] = None
    delta: Optional[float] = None
    message: str
    status: str
    drift_category: Optional[str] = None
    signature_key: Optional[str] = None
    occurrence_count: int = 1
    last_seen_at: Optional[str] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None
    snoozed_until: Optional[str] = None
    snooze_count: int = 0
    created_at: str


class AlertAckRequest(BaseModel):
    """Request to acknowledge an alert."""
    reason: str = Field(..., min_length=1)


class AlertSnoozeRequest(BaseModel):
    """Request to snooze an alert."""
    hours: int = Field(..., ge=1, le=168)
    reason: Optional[str] = None


class AlertResolveRequest(BaseModel):
    """Request to resolve an alert."""
    resolution: str = Field(..., min_length=1)


class AlertStatsDTO(BaseModel):
    """Drift alert statistics."""
    open: int = 0
    acknowledged: int = 0
    snoozed: int = 0
    resolved: int = 0
    mtta_mean_seconds: Optional[float] = None
    mtta_p50_seconds: Optional[float] = None
    mtta_p95_seconds: Optional[float] = None


class SPCCheckRequest(BaseModel):
    """Request for SPC check."""
    metrics: Optional[list[str]] = None


class CanaryTriggerRequest(BaseModel):
    """Request to trigger a canary run."""

    drift_threshold: Optional[float] = Field(
        default=None,
        description="Override drift threshold (default: 0.15)",
    )
    pass_rate_threshold: Optional[float] = Field(
        default=None,
        description="Override pass rate threshold (default: 0.80)",
    )


class CanaryStatusDTO(BaseModel):
    """Current canary status summary."""

    latest_verdict: Optional[str] = None
    latest_pass_rate: Optional[float] = None
    latest_run_at: Optional[str] = None
    total_runs: int = 0
    open_alerts: int = 0
