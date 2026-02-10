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
    canary_run_id: int
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
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
    created_at: str


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
