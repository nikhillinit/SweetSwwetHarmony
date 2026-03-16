"""
Activation Gate -- step-specific readiness checks for progressive feature activation.

Reads existing canary_runs and canary_drift_alerts tables to determine whether
the system is ready to advance to the next activation step.

Step-specific policy matrix:
  Step 1 (Shadow):   lenient  -- observe-only, no mutations
  Step 2 (Low-risk): moderate -- some writes (thin files, drift monitoring)
  Step 3 (Write):    strict   -- manual Notion push, triage, hunter promote
  Step 4 (Batch):    strict   -- batch commit, entity merges
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Step-specific policy thresholds
STEP_POLICY = {
    1: {
        "max_canary_age_hours": 48,
        "block_on_degraded": False,
        "block_on_no_canary": False,
        "block_on_warning_alerts": False,
        "warn_on_warning_alerts": False,
        "required_spc_metrics": [],
        "optional_spc_metrics": [],
    },
    2: {
        "max_canary_age_hours": 48,
        "block_on_degraded": True,
        "block_on_no_canary": False,
        "block_on_warning_alerts": False,
        "warn_on_warning_alerts": True,
        "required_spc_metrics": [],
        "optional_spc_metrics": [],
    },
    3: {
        "max_canary_age_hours": 24,
        "block_on_degraded": True,
        "block_on_no_canary": True,
        "block_on_warning_alerts": False,
        "warn_on_warning_alerts": True,
        "required_spc_metrics": ["collector_volume", "overall_fp_rate"],
        "optional_spc_metrics": ["confidence_calibration_ece", "quarantine_regret"],
    },
    4: {
        "max_canary_age_hours": 24,
        "block_on_degraded": True,
        "block_on_no_canary": True,
        "block_on_warning_alerts": True,
        "warn_on_warning_alerts": False,
        "required_spc_metrics": ["collector_volume", "overall_fp_rate"],
        "optional_spc_metrics": ["confidence_calibration_ece", "quarantine_regret", "publish_fp_rate"],
    },
}


@dataclass
class ActivationGateResult:
    verdict: str  # "ready" | "warn" | "blocked"
    step: int
    reasons: list[str] = field(default_factory=list)
    canary_verdict: Optional[str] = None
    canary_pass_rate: Optional[float] = None
    canary_run_age_hours: Optional[float] = None
    open_critical_alerts: int = 0
    open_warning_alerts: int = 0
    drift_coverage: dict[str, str] = field(default_factory=dict)
    checked_at: str = ""

    @property
    def can_proceed(self) -> bool:
        return self.verdict in ("ready", "warn")

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "step": self.step,
            "reasons": self.reasons,
            "can_proceed": self.can_proceed,
            "canary": {
                "verdict": self.canary_verdict,
                "pass_rate": self.canary_pass_rate,
                "run_age_hours": self.canary_run_age_hours,
            },
            "alerts": {
                "open_critical": self.open_critical_alerts,
                "open_warning": self.open_warning_alerts,
            },
            "drift_coverage": self.drift_coverage,
            "checked_at": self.checked_at,
        }


def _resolve_spc_metrics(policy: dict) -> tuple[list[str], list[str]]:
    """Resolve SPC metric lists from env overrides or policy defaults.

    Validates metric names against VALID_SPC_METRICS.  If the env override
    yields an empty required list (all-invalid), falls back to policy defaults.
    """
    from monitoring.spc_monitor import VALID_SPC_METRICS

    policy_required = list(policy.get("required_spc_metrics", []))
    policy_optional = list(policy.get("optional_spc_metrics", []))

    env_req = os.environ.get("SPC_REQUIRED_METRICS", "").strip()
    env_opt = os.environ.get("SPC_OPTIONAL_METRICS", "").strip()

    if env_req:
        parsed = [m.strip().lower() for m in env_req.split(",") if m.strip()]
        valid = [m for m in parsed if m in VALID_SPC_METRICS]
        invalid = [m for m in parsed if m not in VALID_SPC_METRICS]
        if invalid:
            logger.warning(
                "SPC_REQUIRED_METRICS contains invalid names %s (valid: %s)",
                invalid, sorted(VALID_SPC_METRICS),
            )
        if valid:
            policy_required = valid
        else:
            logger.warning(
                "SPC_REQUIRED_METRICS resolved empty after validation — "
                "falling back to policy defaults: %s",
                policy.get("required_spc_metrics", []),
            )

    if env_opt:
        parsed = [m.strip().lower() for m in env_opt.split(",") if m.strip()]
        valid = [m for m in parsed if m in VALID_SPC_METRICS]
        invalid = [m for m in parsed if m not in VALID_SPC_METRICS]
        if invalid:
            logger.warning(
                "SPC_OPTIONAL_METRICS contains invalid names %s (valid: %s)",
                invalid, sorted(VALID_SPC_METRICS),
            )
        if valid:
            policy_optional = valid

    return policy_required, policy_optional


def _evaluate_spc_coverage(db_path, metrics: list[str]) -> dict[str, str]:
    """Sync SPC coverage check — runs in a thread via asyncio.to_thread."""
    import sqlite3 as _sqlite3
    from monitoring.spc_monitor import SPCMonitor

    conn = _sqlite3.connect(str(db_path), timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        spc = SPCMonitor()
        result = {}
        for metric in metrics:
            try:
                limits = spc.compute_control_limits(
                    conn, metric, segment_type="overall", segment_key="",
                )
                result[metric] = "ok" if limits is not None else "insufficient_data"
            except Exception:
                result[metric] = "error"
        return result
    finally:
        conn.close()


async def check_activation_readiness(store, step: int = 1) -> ActivationGateResult:
    """Check whether the system is ready for the given activation step.

    Args:
        store: SignalStore instance with initialized DB.
        step: Activation step (1-4). Raises ValueError for invalid step.

    Returns:
        ActivationGateResult with verdict, reasons, and diagnostic data.
    """
    if step not in STEP_POLICY:
        raise ValueError(f"Invalid step {step}: must be 1-4")

    policy = STEP_POLICY[step]
    now = datetime.now(timezone.utc)
    reasons: list[str] = []
    verdict = "ready"

    # --- Query latest canary run ---
    db = store._db
    cursor = await db.execute(
        """SELECT verdict, pass_rate, created_at
           FROM canary_runs
           ORDER BY created_at DESC, id DESC
           LIMIT 1"""
    )
    canary_row = await cursor.fetchone()

    canary_verdict: Optional[str] = None
    canary_pass_rate: Optional[float] = None
    canary_age_hours: Optional[float] = None

    if canary_row is None:
        # No canary data
        if policy["block_on_no_canary"]:
            verdict = "blocked"
            reasons.append("No canary data available (required for step >= 3)")
        else:
            if verdict != "blocked":
                verdict = "warn"
            reasons.append("No canary data available")
    else:
        canary_verdict = canary_row[0]
        canary_pass_rate = canary_row[1]
        created_at_str = canary_row[2]

        # Parse canary age
        try:
            created_at = datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            canary_age_hours = (now - created_at).total_seconds() / 3600
        except (ValueError, TypeError):
            canary_age_hours = None

        # Check canary verdict
        if canary_verdict == "fail":
            verdict = "blocked"
            reasons.append(f"Canary verdict is 'fail' (pass_rate={canary_pass_rate})")
        elif canary_verdict == "degraded":
            if policy["block_on_degraded"]:
                verdict = "blocked"
                reasons.append(f"Canary verdict is 'degraded' (blocked at step >= 2)")
            else:
                if verdict != "blocked":
                    verdict = "warn"
                reasons.append(f"Canary verdict is 'degraded' (pass_rate={canary_pass_rate})")

        # Check canary staleness
        if canary_age_hours is not None:
            max_age = policy["max_canary_age_hours"]
            if canary_age_hours > max_age:
                if step >= 3:
                    verdict = "blocked"
                    reasons.append(
                        f"Canary run is {canary_age_hours:.1f}h old "
                        f"(max {max_age}h for step {step})"
                    )
                else:
                    if verdict != "blocked":
                        verdict = "warn"
                    reasons.append(
                        f"Canary run is {canary_age_hours:.1f}h old "
                        f"(max {max_age}h for step {step})"
                    )

    # --- Query open drift alerts ---
    cursor = await db.execute(
        """SELECT severity, COUNT(*) as cnt
           FROM canary_drift_alerts
           WHERE status = 'open'
           GROUP BY severity"""
    )
    alert_rows = await cursor.fetchall()

    open_critical = 0
    open_warning = 0
    for row in alert_rows:
        if row[0] == "critical":
            open_critical = row[1]
        elif row[0] == "warning":
            open_warning = row[1]

    # Critical alerts block ALL steps
    if open_critical > 0:
        verdict = "blocked"
        reasons.append(f"{open_critical} open critical drift alert(s)")

    # Warning alerts: step-specific policy
    if open_warning > 0:
        if policy["block_on_warning_alerts"]:
            verdict = "blocked"
            reasons.append(f"{open_warning} open warning drift alert(s) (blocked at step {step})")
        elif policy["warn_on_warning_alerts"]:
            if verdict != "blocked":
                verdict = "warn"
            reasons.append(f"{open_warning} open warning drift alert(s)")
        # else: pass (step 1 ignores warning alerts)

    # --- SPC drift coverage check ---
    drift_coverage = {}
    policy_required = policy.get("required_spc_metrics", [])
    policy_optional = policy.get("optional_spc_metrics", [])
    if policy_required or policy_optional:
        required, optional = _resolve_spc_metrics(policy)
        all_metrics = required + optional
        try:
            drift_coverage = await asyncio.to_thread(
                _evaluate_spc_coverage, store.db_path, all_metrics,
            )
        except Exception as exc:
            logger.error("SPC coverage evaluation failed: %s", exc)
            drift_coverage = {m: "error" for m in all_metrics}

        req_gaps = [m for m in required if drift_coverage.get(m) != "ok"]
        if req_gaps:
            msg = f"{len(req_gaps)} required drift monitor(s) not ready: {', '.join(req_gaps)}"
            if step >= 4:
                verdict = "blocked"
                reasons.append(f"{msg} (blocked at step {step})")
            else:
                if verdict != "blocked":
                    verdict = "warn"
                reasons.append(msg)

        opt_gaps = [m for m in optional if drift_coverage.get(m) != "ok"]
        if opt_gaps:
            logger.info("Optional drift monitors not ready: %s", opt_gaps)

    result = ActivationGateResult(
        verdict=verdict,
        step=step,
        reasons=reasons,
        canary_verdict=canary_verdict,
        canary_pass_rate=canary_pass_rate,
        canary_run_age_hours=round(canary_age_hours, 1) if canary_age_hours is not None else None,
        open_critical_alerts=open_critical,
        open_warning_alerts=open_warning,
        drift_coverage=drift_coverage,
        checked_at=now.isoformat(),
    )

    logger.info(
        "activation_gate_check step=%d verdict=%s reasons=%s",
        step, result.verdict, result.reasons,
    )
    return result
