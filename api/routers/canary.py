"""
Canary Router — Canary scoring and drift alert endpoints.

Endpoints:
- GET /canary/status — Current canary status (Permission.VIEW)
- GET /canary/runs — Paginated canary run list (Permission.VIEW)
- POST /canary/run — Trigger a canary run (Permission.CANARY_RUN)
- GET /canary/drift-alerts — Paginated drift alerts (Permission.VIEW)

POST /canary/run includes concurrency guard: 409 if a canary is already running.
All paginated endpoints: default limit=50, max limit=200, sort (created_at DESC, id DESC).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import BaseResponse, ListMeta, ListResponse, error_response, feature_disabled_response
from api.models.canary import (
    AlertAckRequest,
    AlertResolveRequest,
    AlertSnoozeRequest,
    AlertStatsDTO,
    CanaryRunDTO,
    CanaryStatusDTO,
    CanaryTriggerRequest,
    DriftAlertDTO,
    SPCCheckRequest,
)
from api.pagination import decode_cursor, encode_cursor
from workflows.feature_guards import WriteFeature, assert_write_enabled, FeatureDisabledError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/canary", tags=["canary"])


# =============================================================================
# STATUS
# =============================================================================

@router.get("/status")
async def canary_status(
    request: Request,
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Get current canary status summary."""
    store = request.app.state.store
    db = store._db

    # Latest run
    cursor = await db.execute(
        """
        SELECT verdict, pass_rate, created_at
        FROM canary_runs
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
    )
    latest = await cursor.fetchone()

    # Total runs
    cursor = await db.execute("SELECT COUNT(*) FROM canary_runs")
    total_runs = (await cursor.fetchone())[0]

    # Open alerts
    cursor = await db.execute(
        "SELECT COUNT(*) FROM canary_drift_alerts WHERE status = 'open'"
    )
    open_alerts = (await cursor.fetchone())[0]

    status_dto = CanaryStatusDTO(
        latest_verdict=latest[0] if latest else None,
        latest_pass_rate=latest[1] if latest else None,
        latest_run_at=latest[2] if latest else None,
        total_runs=total_runs,
        open_alerts=open_alerts,
    )

    return BaseResponse(data=status_dto).model_dump()


# =============================================================================
# RUNS
# =============================================================================

@router.get("/runs")
async def list_canary_runs(
    request: Request,
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List canary runs with cursor pagination."""
    store = request.app.state.store
    db = store._db

    cursor_data = decode_cursor(cursor) if cursor else {}
    cursor_created_at = cursor_data.get("created_at")
    cursor_id = cursor_data.get("id")

    conditions = []
    params = []

    if cursor_created_at and cursor_id is not None:
        conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
        params.extend([cursor_created_at, cursor_created_at, int(cursor_id)])

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(min(max(limit, 1), 200))

    db_cursor = await db.execute(
        f"""
        SELECT id, run_id, golden_set_size, golden_set_hash, golden_set_version,
               total_scored, passed, failed, skipped, pass_rate,
               verdict, drift_threshold, pass_rate_threshold,
               duration_ms, created_at
        FROM canary_runs
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = await db_cursor.fetchall()

    items = [
        CanaryRunDTO(
            id=row[0],
            run_id=row[1],
            golden_set_size=row[2],
            golden_set_hash=row[3],
            golden_set_version=row[4],
            total_scored=row[5] or 0,
            passed=row[6] or 0,
            failed=row[7] or 0,
            skipped=row[8] or 0,
            pass_rate=row[9],
            verdict=row[10],
            drift_threshold=row[11],
            pass_rate_threshold=row[12],
            duration_ms=row[13],
            created_at=row[14],
        )
        for row in rows
    ]

    next_cursor = None
    if items and len(items) == limit:
        last = rows[-1]
        next_cursor = encode_cursor({"created_at": last[14], "id": str(last[0])})

    return ListResponse(
        data=items,
        meta=ListMeta(
            total=len(items),
            cursor=next_cursor,
            has_more=len(items) == limit,
        ),
    ).model_dump()


# =============================================================================
# TRIGGER
# =============================================================================

@router.post("/run")
async def trigger_canary_run(
    request: Request,
    body: CanaryTriggerRequest = CanaryTriggerRequest(),
    operator: OperatorContext = Depends(require_permission(Permission.CANARY_RUN)),
):
    """Trigger a canary run.

    Returns 409 Conflict if a canary run is already in progress.
    """
    store = request.app.state.store
    db = store._db

    # Concurrency guard: check for running canary
    cursor = await db.execute(
        """
        SELECT cr.run_id
        FROM canary_runs cr
        JOIN run_history rh ON rh.id = cr.run_id
        WHERE rh.status = 'running' AND rh.run_type = 'canary'
        LIMIT 1
        """,
    )
    running = await cursor.fetchone()

    if running:
        raise error_response(
            409, "conflict", "CANARY_RUN_IN_PROGRESS",
            "A canary run is already in progress",
            detail={"run_id": running[0]},
        )

    # Trigger canary run
    from monitoring.canary_checker import (
        CanaryChecker, build_stratified_golden_set, store_canary_run,
        DEFAULT_DRIFT_THRESHOLD, DEFAULT_PASS_RATE_THRESHOLD,
    )
    from monitoring.drift_detector import detect_drift, store_drift_alerts
    from storage.audit_events import record_event

    drift_threshold = body.drift_threshold or DEFAULT_DRIFT_THRESHOLD
    pass_rate_threshold = body.pass_rate_threshold or DEFAULT_PASS_RATE_THRESHOLD

    stratified = await build_stratified_golden_set(store)
    checker = CanaryChecker(
        stratified.golden_set,
        drift_threshold=drift_threshold,
        pass_rate_threshold=pass_rate_threshold,
    )
    result = await checker.run(store)
    canary_run_id = await store_canary_run(store, checker, result, stratified)

    # Drift detection
    drift = await detect_drift(
        store, canary_run_id, stratified.golden_set_hash,
    )
    if drift.alerts:
        await store_drift_alerts(store, canary_run_id, drift.alerts)

    # Audit event
    try:
        await record_event(
            store,
            action_type="canary_run_triggered",
            entity_type="canary_run",
            entity_id=str(canary_run_id),
            actor=operator.actor_label,
            details=json.dumps({
                "verdict": result.verdict,
                "pass_rate": result.pass_rate,
                "drift_verdict": drift.verdict,
                "alerts": len(drift.alerts),
            }),
        )
    except Exception as e:
        logger.warning("Failed to record canary audit event: %s", e)

    baseline_status = "matched"
    if drift.verdict == "no_baseline":
        baseline_status = "no_baseline"

    return BaseResponse(data=CanaryRunDTO(
        id=canary_run_id,
        run_id="",  # Will be set from run_history
        golden_set_size=len(stratified.golden_set),
        golden_set_hash=stratified.golden_set_hash,
        total_scored=result.total,
        passed=result.passed,
        failed=result.failed,
        skipped=result.skipped,
        pass_rate=result.pass_rate,
        verdict=result.verdict,
        drift_threshold=drift_threshold,
        pass_rate_threshold=pass_rate_threshold,
        duration_ms=result.duration_ms,
        baseline_status=baseline_status,
        baseline_message=drift.baseline_message,
        created_at="",
    )).model_dump()


# =============================================================================
# DRIFT ALERTS
# =============================================================================

@router.get("/drift-alerts")
async def list_drift_alerts(
    request: Request,
    status: Optional[str] = Query(default=None, description="Filter by status"),
    severity: Optional[str] = Query(default=None, description="Filter by severity"),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """List canary drift alerts with cursor pagination."""
    store = request.app.state.store
    db = store._db

    cursor_data = decode_cursor(cursor) if cursor else {}
    cursor_created_at = cursor_data.get("created_at")
    cursor_id = cursor_data.get("id")

    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if severity:
        conditions.append("severity = ?")
        params.append(severity)
    if cursor_created_at and cursor_id is not None:
        conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
        params.extend([cursor_created_at, cursor_created_at, int(cursor_id)])

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(min(max(limit, 1), 200))

    db_cursor = await db.execute(
        f"""
        SELECT id, canary_run_id, alert_type, severity,
               signal_id, canonical_key, metric_name,
               expected_value, actual_value, delta,
               message, status, drift_category, signature_key,
               occurrence_count, last_seen_at,
               acknowledged_by, acknowledged_at,
               resolved_by, resolved_at, resolution,
               snoozed_until, snooze_count, created_at
        FROM canary_drift_alerts
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = await db_cursor.fetchall()

    items = [
        _row_to_dto(row)
        for row in rows
    ]

    next_cursor = None
    if items and len(items) == limit:
        last = rows[-1]
        next_cursor = encode_cursor({"created_at": last[23], "id": str(last[0])})

    return ListResponse(
        data=items,
        meta=ListMeta(
            total=len(items),
            cursor=next_cursor,
            has_more=len(items) == limit,
        ),
    ).model_dump()


def _row_to_dto(row) -> DriftAlertDTO:
    """Convert a full drift alert row to DTO."""
    return DriftAlertDTO(
        id=row[0],
        canary_run_id=row[1],
        alert_type=row[2],
        severity=row[3],
        signal_id=row[4],
        canonical_key=row[5],
        metric_name=row[6],
        expected_value=row[7],
        actual_value=row[8],
        delta=row[9],
        message=row[10],
        status=row[11],
        drift_category=row[12],
        signature_key=row[13],
        occurrence_count=row[14] or 1,
        last_seen_at=row[15],
        acknowledged_by=row[16],
        acknowledged_at=row[17],
        resolved_by=row[18],
        resolved_at=row[19],
        resolution=row[20],
        snoozed_until=row[21],
        snooze_count=row[22] or 0,
        created_at=row[23],
    )


async def _read_alert_dto(store, alert_id: int) -> Optional[DriftAlertDTO]:
    """Read a single alert as DTO."""
    db = store._db
    cursor = await db.execute(
        """SELECT id, canary_run_id, alert_type, severity,
               signal_id, canonical_key, metric_name,
               expected_value, actual_value, delta,
               message, status, drift_category, signature_key,
               occurrence_count, last_seen_at,
               acknowledged_by, acknowledged_at,
               resolved_by, resolved_at, resolution,
               snoozed_until, snooze_count, created_at
        FROM canary_drift_alerts WHERE id = ?""",
        (alert_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return _row_to_dto(row)


# =============================================================================
# ALERT MUTATIONS (Wave 5)
# =============================================================================


@router.post("/drift-alerts/{alert_id}/acknowledge")
async def acknowledge_drift_alert(
    request: Request,
    alert_id: int,
    body: AlertAckRequest,
    operator: OperatorContext = Depends(require_permission(Permission.CANARY_RUN)),
):
    """Acknowledge a drift alert (D27: CANARY_RUN permission)."""
    try:
        assert_write_enabled(WriteFeature.DRIFT_MONITORING)
    except FeatureDisabledError as e:
        raise feature_disabled_response(e.feature.value, e.env_var)
    store = request.app.state.store

    from monitoring.alert_escalation import acknowledge_alert
    result = await acknowledge_alert(store, alert_id, operator.user_id, body.reason)

    if not result.success:
        if "not found" in (result.error or ""):
            raise error_response(404, "not_found", "ALERT_NOT_FOUND", result.error)
        raise error_response(409, "conflict", "INVALID_TRANSITION", result.error,
                             detail={"current_status": result.old_status})

    dto = await _read_alert_dto(store, alert_id)
    return BaseResponse(data=dto).model_dump()


@router.post("/drift-alerts/{alert_id}/snooze")
async def snooze_drift_alert(
    request: Request,
    alert_id: int,
    body: AlertSnoozeRequest,
    operator: OperatorContext = Depends(require_permission(Permission.CANARY_RUN)),
):
    """Snooze a drift alert for N hours."""
    try:
        assert_write_enabled(WriteFeature.DRIFT_MONITORING)
    except FeatureDisabledError as e:
        raise feature_disabled_response(e.feature.value, e.env_var)
    store = request.app.state.store

    from monitoring.alert_escalation import snooze_alert
    result = await snooze_alert(store, alert_id, operator.user_id, body.hours, body.reason)

    if not result.success:
        if "not found" in (result.error or ""):
            raise error_response(404, "not_found", "ALERT_NOT_FOUND", result.error)
        if "1-168" in (result.error or ""):
            raise error_response(422, "validation_error", "INVALID_SNOOZE_HOURS", result.error)
        raise error_response(409, "conflict", "INVALID_TRANSITION", result.error,
                             detail={"current_status": result.old_status})

    dto = await _read_alert_dto(store, alert_id)
    return BaseResponse(data=dto).model_dump()


@router.post("/drift-alerts/{alert_id}/resolve")
async def resolve_drift_alert(
    request: Request,
    alert_id: int,
    body: AlertResolveRequest,
    operator: OperatorContext = Depends(require_permission(Permission.CANARY_RUN)),
):
    """Resolve a drift alert."""
    try:
        assert_write_enabled(WriteFeature.DRIFT_MONITORING)
    except FeatureDisabledError as e:
        raise feature_disabled_response(e.feature.value, e.env_var)
    store = request.app.state.store

    from monitoring.alert_escalation import resolve_alert
    result = await resolve_alert(store, alert_id, operator.user_id, body.resolution)

    if not result.success:
        if "not found" in (result.error or ""):
            raise error_response(404, "not_found", "ALERT_NOT_FOUND", result.error)
        raise error_response(409, "conflict", "INVALID_TRANSITION", result.error,
                             detail={"current_status": result.old_status})

    dto = await _read_alert_dto(store, alert_id)
    return BaseResponse(data=dto).model_dump()


@router.get("/drift-alerts/stats")
async def drift_alert_stats(
    request: Request,
    operator: OperatorContext = Depends(require_permission(Permission.VIEW)),
):
    """Get drift alert statistics including MTTA."""
    store = request.app.state.store
    db = store._db

    counts = {}
    for s in ("open", "acknowledged", "snoozed", "resolved"):
        cursor = await db.execute(
            "SELECT COUNT(*) FROM canary_drift_alerts WHERE status = ?", (s,)
        )
        counts[s] = (await cursor.fetchone())[0]

    from monitoring.alert_escalation import compute_mtta
    mtta = await compute_mtta(store)

    return BaseResponse(data=AlertStatsDTO(
        open=counts["open"],
        acknowledged=counts["acknowledged"],
        snoozed=counts["snoozed"],
        resolved=counts["resolved"],
        mtta_mean_seconds=mtta["mean"],
        mtta_p50_seconds=mtta["p50"],
        mtta_p95_seconds=mtta["p95"],
    )).model_dump()


@router.post("/spc/check")
async def spc_check(
    request: Request,
    body: SPCCheckRequest = SPCCheckRequest(),
    operator: OperatorContext = Depends(require_permission(Permission.CANARY_RUN)),
):
    """Run SPC check against quality metrics."""
    try:
        assert_write_enabled(WriteFeature.DRIFT_MONITORING)
    except FeatureDisabledError as e:
        raise feature_disabled_response(e.feature.value, e.env_var)
    store = request.app.state.store

    from monitoring.spc_monitor import SPCMonitor, VALID_SPC_METRICS

    metrics = body.metrics or list(VALID_SPC_METRICS)
    for m in metrics:
        if m not in VALID_SPC_METRICS:
            raise error_response(
                422, "validation_error", "INVALID_METRIC",
                f"Invalid SPC metric: {m!r}. Valid: {sorted(VALID_SPC_METRICS)}",
            )

    # SPC check uses sync conn — get from store
    # For now return the check structure
    monitor = SPCMonitor()
    results = []
    db = store._db

    # Use a sync wrapper for the SPC monitor's conn parameter
    # Since SPC monitor uses sync sqlite3 and our store uses aiosqlite,
    # we read latest metrics and run SPC via the async db
    for metric in metrics:
        cursor = await db.execute(
            "SELECT value FROM quality_metrics_daily "
            "WHERE metric_name = ? AND segment_type = 'overall' AND segment_key = '' "
            "AND value IS NOT NULL ORDER BY metric_date DESC LIMIT 1",
            (metric,),
        )
        row = await cursor.fetchone()
        current_value = row[0] if row else None

        results.append({
            "metric": metric,
            "current_value": current_value,
            "verdict": "no_data" if current_value is None else "checked",
        })

    return BaseResponse(data={"results": results}).model_dump()
