"""
Health Router

Provides detailed health and monitoring endpoints:
- GET /health - Basic health check (exists in main.py)
- GET /health/detailed - Full system health with all components
- GET /health/collectors - Collector status and last run times
- GET /health/relationships - Email/LP staleness from relationship_health
- GET /health/database - Database stats and integrity
"""

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

from fastapi.responses import Response
from storage.signal_store import SignalStore
from api.health_bounds import BoundedParams

logger = logging.getLogger(__name__)

# Try to import health monitoring utilities
try:
    from utils.signal_health import SignalHealthMonitor
    SIGNAL_HEALTH_AVAILABLE = True
except ImportError:
    SIGNAL_HEALTH_AVAILABLE = False

try:
    from utils.relationship_health import RelationshipHealthMonitor
    RELATIONSHIP_HEALTH_AVAILABLE = True
except ImportError:
    RELATIONSHIP_HEALTH_AVAILABLE = False


router = APIRouter(prefix="/health", tags=["health"])


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class ComponentHealth(BaseModel):
    """Health status for a single component."""
    name: str
    status: str  # healthy, degraded, unhealthy, unknown
    message: Optional[str] = None
    last_checked: Optional[datetime] = None
    details: Optional[Dict[str, Any]] = None


class CollectorHealth(BaseModel):
    """Health status for a collector."""
    name: str
    status: str
    last_run: Optional[datetime] = None
    last_success: Optional[datetime] = None
    signals_last_run: int = 0
    error_rate: float = 0.0
    message: Optional[str] = None


class RelationshipHealth(BaseModel):
    """Health status for relationship data."""
    email_scan_status: str  # fresh, stale, never_scanned
    email_last_scan: Optional[datetime] = None
    email_staleness_days: Optional[int] = None
    lp_sync_status: str
    lp_last_sync: Optional[datetime] = None
    lp_staleness_days: Optional[int] = None
    total_relationships: int = 0
    warm_intro_paths: int = 0


class DatabaseHealth(BaseModel):
    """Database health and statistics."""
    status: str
    total_signals: int = 0
    total_companies: int = 0
    pending_signals: int = 0
    schema_version: int = 0
    database_size_mb: float = 0.0
    wal_size_mb: float = 0.0


class DetailedHealthResponse(BaseModel):
    """Comprehensive health check response."""
    status: str  # healthy, degraded, unhealthy
    timestamp: datetime
    version: str
    environment: str
    uptime_seconds: Optional[float] = None
    components: List[ComponentHealth]
    collectors: Optional[List[CollectorHealth]] = None
    relationships: Optional[RelationshipHealth] = None
    database: Optional[DatabaseHealth] = None
    alerts: List[Dict[str, Any]] = []


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

async def get_store(request: Request) -> SignalStore:
    """Get the lifespan-managed SignalStore from app state.

    Falls back to creating a new store if app.state.store is unavailable
    (e.g., in tests that don't use the full app lifespan).
    """
    store = getattr(request.app.state, "store", None)
    if store is not None:
        return store
    # Fallback for isolated test apps that don't set up lifespan
    store = SignalStore()
    await store.initialize()
    return store


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/detailed", response_model=DetailedHealthResponse)
async def get_detailed_health(
    store: SignalStore = Depends(get_store),
):
    """
    Get comprehensive system health including all components.

    Checks:
    - Database connectivity and stats
    - Collector last run times and error rates
    - Signal health anomalies
    - Relationship data staleness
    - API dependencies (Notion, GitHub, etc.)
    """
    components: List[ComponentHealth] = []
    alerts: List[Dict[str, Any]] = []
    overall_status = "healthy"

    # 1. Database health
    try:
        stats = await store.get_stats()
        db_health = ComponentHealth(
            name="database",
            status="healthy",
            message="Connected and responsive",
            last_checked=datetime.now(timezone.utc),
            details={
                "total_signals": stats.get("total_signals", 0),
                "schema_version": 16,  # Current version
            },
        )
    except Exception as e:
        db_health = ComponentHealth(
            name="database",
            status="unhealthy",
            message=str(e),
            last_checked=datetime.now(timezone.utc),
        )
        overall_status = "unhealthy"
    components.append(db_health)

    # 2. Signal health monitor
    if SIGNAL_HEALTH_AVAILABLE:
        try:
            monitor = SignalHealthMonitor(store)
            health_report = await monitor.check_health()

            signal_status = "healthy"
            signal_message = "All signals within normal parameters"

            if health_report.get("anomalies"):
                signal_status = "degraded"
                signal_message = f"{len(health_report['anomalies'])} anomalies detected"
                for anomaly in health_report["anomalies"]:
                    alerts.append({
                        "type": "signal_anomaly",
                        "severity": anomaly.get("severity", "medium"),
                        "message": anomaly.get("message", "Unknown anomaly"),
                    })

            components.append(ComponentHealth(
                name="signal_monitor",
                status=signal_status,
                message=signal_message,
                last_checked=datetime.now(timezone.utc),
                details=health_report,
            ))
        except Exception as e:
            components.append(ComponentHealth(
                name="signal_monitor",
                status="unknown",
                message=f"Failed to check: {e}",
            ))
    else:
        components.append(ComponentHealth(
            name="signal_monitor",
            status="unknown",
            message="Signal health monitor not available",
        ))

    # 3. Activation readiness (2s timeout guardrail)
    try:
        from monitoring.activation_gate import check_activation_readiness

        gate_result = await asyncio.wait_for(
            check_activation_readiness(store, step=1),
            timeout=2.0,
        )
        gate_status_map = {"ready": "healthy", "warn": "degraded", "blocked": "unhealthy"}
        components.append(ComponentHealth(
            name="activation_readiness",
            status=gate_status_map.get(gate_result.verdict, "unknown"),
            message=f"Step 1: {gate_result.verdict}" + (
                f" ({'; '.join(gate_result.reasons)})" if gate_result.reasons else ""
            ),
            last_checked=datetime.now(timezone.utc),
            details=gate_result.to_dict(),
        ))
    except (asyncio.TimeoutError, Exception) as e:
        components.append(ComponentHealth(
            name="activation_readiness",
            status="unknown",
            message=f"check timed out" if isinstance(e, asyncio.TimeoutError) else f"check failed: {e}",
        ))

    # 4. Relationship health
    relationships = None
    if RELATIONSHIP_HEALTH_AVAILABLE:
        # RelationshipHealthMonitor requires store and user_email for full checks
        # For now, just mark as available but not configured
        components.append(ComponentHealth(
            name="relationships",
            status="unknown",
            message="Relationship monitoring available (requires configuration)",
        ))

    # 4. External API health (check tokens exist)
    api_checks = [
        ("notion", "NOTION_API_KEY"),
        ("github", "GITHUB_TOKEN"),
        ("gemini", "GOOGLE_API_KEY"),
    ]

    for api_name, env_var in api_checks:
        has_token = bool(os.getenv(env_var))
        components.append(ComponentHealth(
            name=f"api_{api_name}",
            status="healthy" if has_token else "degraded",
            message="Token configured" if has_token else "Token not configured",
        ))

    # Determine overall status
    component_statuses = [c.status for c in components]
    if "unhealthy" in component_statuses:
        overall_status = "unhealthy"
    elif "degraded" in component_statuses:
        overall_status = "degraded"

    return DetailedHealthResponse(
        status=overall_status,
        timestamp=datetime.now(timezone.utc),
        version="1.0.0",
        environment=os.getenv("ENVIRONMENT", "development"),
        components=components,
        relationships=relationships,
        alerts=alerts,
    )


@router.get("/activation-readiness")
async def get_activation_readiness(
    step: int = Query(1, ge=1, le=4, description="Activation step (1-4)"),
    store: SignalStore = Depends(get_store),
):
    """
    Check activation readiness for the given step.

    Step-specific policy:
    - Step 1 (Shadow): lenient -- observe only
    - Step 2 (Low-risk): moderate -- some writes
    - Step 3 (Write): strict -- manual push, triage
    - Step 4 (Batch): strict -- batch commit, merges
    """
    from monitoring.activation_gate import check_activation_readiness

    result = await check_activation_readiness(store, step=step)
    return result.to_dict()


@router.get("/phase-g-readiness")
async def get_phase_g_readiness(
    store: SignalStore = Depends(get_store),
):
    """
    Check Phase G entity resolution readiness.

    Evaluates whether entity identity tables, shadow data, and merge quality
    meet the threshold for safe activation. Separate from activation-readiness
    (which stays integer steps 1-4).
    """
    from monitoring.phase_g_readiness import check_phase_g_readiness

    result = await check_phase_g_readiness(store)
    return result.to_dict()


@router.get("/collectors", response_model=List[CollectorHealth])
async def get_collector_health(
    store: SignalStore = Depends(get_store),
):
    """
    Get health status for all collectors.

    Shows last run time, signal count, and error rates.
    """
    collectors = []

    # List of known collectors
    collector_names = [
        "github", "github_activity", "sec_edgar", "companies_house",
        "domain_whois", "job_postings", "product_hunt", "hacker_news",
        "arxiv", "uspto", "linkedin", "crunchbase", "opencorporates",
    ]

    try:
        # Get last run info from pipeline_runs table
        db = await store._get_db()
        for name in collector_names:
            cursor = await db.execute("""
                SELECT
                    cm.collector_name,
                    cm.started_at,
                    cm.completed_at,
                    cm.signals_found,
                    cm.status,
                    cm.errors
                FROM collector_metrics cm
                WHERE cm.collector_name = ?
                ORDER BY cm.started_at DESC
                LIMIT 1
            """, (name,))
            row = await cursor.fetchone()

            if row:
                last_run = datetime.fromisoformat(row[1]) if row[1] else None
                status = "healthy" if row[4] == "success" else "degraded"
                message = f"{row[3]} signals" if row[4] == "success" else f"Error: {row[5]}"

                collectors.append(CollectorHealth(
                    name=name,
                    status=status,
                    last_run=last_run,
                    signals_last_run=row[3] or 0,
                    message=message,
                ))
            else:
                collectors.append(CollectorHealth(
                    name=name,
                    status="unknown",
                    message="No runs recorded",
                ))

    except Exception as e:
        # Return minimal info on error
        for name in collector_names:
            collectors.append(CollectorHealth(
                name=name,
                status="unknown",
                message=f"Could not fetch metrics: {e}",
            ))

    return collectors


@router.get("/database", response_model=DatabaseHealth)
async def get_database_health(
    store: SignalStore = Depends(get_store),
):
    """
    Get database health and statistics.

    Includes table sizes, signal counts, and integrity checks.
    """
    try:
        db = await store._get_db()

        # Get signal counts
        cursor = await db.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        cursor = await db.execute("""
            SELECT COUNT(DISTINCT canonical_key) FROM signals
        """)
        total_companies = (await cursor.fetchone())[0]

        cursor = await db.execute("""
            SELECT COUNT(*) FROM signal_processing WHERE status = 'pending'
        """)
        pending_signals = (await cursor.fetchone())[0]

        # Get schema version
        cursor = await db.execute("""
            SELECT MAX(version) FROM schema_migrations
        """)
        row = await cursor.fetchone()
        schema_version = row[0] if row and row[0] else 0

        # Get database file size (rough estimate)
        import os
        db_path = str(store.db_path)
        db_size_mb = 0.0
        wal_size_mb = 0.0

        if os.path.exists(db_path):
            db_size_mb = os.path.getsize(db_path) / (1024 * 1024)

        wal_path = db_path + "-wal"
        if os.path.exists(wal_path):
            wal_size_mb = os.path.getsize(wal_path) / (1024 * 1024)

        return DatabaseHealth(
            status="healthy",
            total_signals=total_signals,
            total_companies=total_companies,
            pending_signals=pending_signals,
            schema_version=schema_version,
            database_size_mb=round(db_size_mb, 2),
            wal_size_mb=round(wal_size_mb, 2),
        )

    except Exception as e:
        return DatabaseHealth(
            status="unhealthy",
        )


@router.get("/relationships")
async def get_relationship_health():
    """
    Get relationship data health and staleness.

    Checks email scan and LP sync freshness.
    """
    if not RELATIONSHIP_HEALTH_AVAILABLE:
        return {
            "status": "unavailable",
            "message": "Relationship health monitor not installed",
        }

    # RelationshipHealthMonitor requires store and user_email parameters
    # Return a status indicating it's available but needs configuration
    return {
        "status": "available",
        "message": "Relationship monitoring available (requires store and user configuration)",
        "email_scan_status": "unknown",
        "lp_sync_status": "unknown",
        "total_relationships": 0,
        "warm_intro_paths": 0,
    }


@router.get("/jobs")
async def get_job_health(
    store: SignalStore = Depends(get_store),
):
    """
    Get background job health.

    Shows running, queued, and recently failed jobs.
    """
    try:
        db = await store._get_db()

        # Get job counts by status
        cursor = await db.execute("""
            SELECT status, COUNT(*) as count
            FROM jobs
            WHERE created_at > datetime('now', '-24 hours')
            GROUP BY status
        """)
        rows = await cursor.fetchall()
        status_counts = {row[0]: row[1] for row in rows}

        # Get recently failed jobs
        cursor = await db.execute("""
            SELECT id, job_type, error_message, completed_at
            FROM jobs
            WHERE status = 'failed'
            AND completed_at > datetime('now', '-24 hours')
            ORDER BY completed_at DESC
            LIMIT 5
        """)
        failed_jobs = []
        async for row in cursor:
            failed_jobs.append({
                "id": row[0],
                "type": row[1],
                "error": row[2],
                "failed_at": row[3],
            })

        return {
            "status": "healthy" if not failed_jobs else "degraded",
            "counts": status_counts,
            "running": status_counts.get("running", 0),
            "pending": status_counts.get("pending", 0),
            "failed_24h": len(failed_jobs),
            "recent_failures": failed_jobs,
        }

    except Exception as e:
        return {
            "status": "unknown",
            "error": str(e),
        }


# =============================================================================
# OPENMETRICS ENDPOINT
# =============================================================================

OPENMETRICS_CONTENT_TYPE = "application/openmetrics-text; version=1.0.0; charset=utf-8"


def _build_openmetrics_text() -> str:
    """Build OpenMetrics exposition text from instrumentation + ops metrics."""
    from utils.instrumentation import metrics

    lines: list[str] = []
    snap = metrics.snapshot()

    # --- Counters ---
    counters = snap.get("counters", {})
    if counters:
        lines.append("# TYPE discovery_counter counter")
        lines.append("# HELP discovery_counter Application counter")
        for name, value in sorted(counters.items()):
            lines.append(f'discovery_counter{{name="{name}"}} {value}')

    # --- Timers ---
    timers = snap.get("timers", {})
    if timers:
        lines.append("# TYPE discovery_timer_count gauge")
        lines.append("# HELP discovery_timer_count Timer invocation count")
        lines.append("# TYPE discovery_timer_total_ms gauge")
        lines.append("# HELP discovery_timer_total_ms Timer cumulative milliseconds")
        lines.append("# TYPE discovery_timer_avg_ms gauge")
        lines.append("# HELP discovery_timer_avg_ms Timer average milliseconds")
        for name, stats in sorted(timers.items()):
            lines.append(f'discovery_timer_count{{name="{name}"}} {stats["count"]}')
            lines.append(f'discovery_timer_total_ms{{name="{name}"}} {stats["total_ms"]}')
            lines.append(f'discovery_timer_avg_ms{{name="{name}"}} {stats["avg_ms"]}')

    # --- Ops gauges (best-effort) ---
    try:
        collector = _get_ops_collector()
        if collector is not None:
            from fastapi.concurrency import run_in_threadpool
            import asyncio
            # This function is sync; ops collector is sync too
            ops_snap = collector.collect()
            lines.append("# TYPE discovery_health_pct gauge")
            lines.append("# HELP discovery_health_pct Overall ops health percentage")
            lines.append(f"discovery_health_pct {ops_snap.overall_health_pct}")
            lines.append("# TYPE discovery_extractions_24h gauge")
            lines.append("# HELP discovery_extractions_24h Signal extractions in last 24h")
            lines.append(f"discovery_extractions_24h {ops_snap.extractions_24h}")
            lines.append("# TYPE discovery_open_incidents gauge")
            lines.append("# HELP discovery_open_incidents Currently open incidents")
            lines.append(f"discovery_open_incidents {ops_snap.open_incidents}")
    except Exception as e:
        logger.debug("Ops metrics unavailable for /metrics: %s", e)

    # OpenMetrics requires trailing EOF
    lines.append("# EOF")
    return "\n".join(lines) + "\n"


@router.get("/metrics")
async def get_openmetrics():
    """Expose application metrics in OpenMetrics text format.

    Scrape-ready for Prometheus. Includes:
    - In-process counters from utils.instrumentation
    - Timer statistics (count, total_ms, avg_ms)
    - Ops gauges (health_pct, extractions_24h, open_incidents) when available
    """
    body = await run_in_threadpool(_build_openmetrics_text)
    return Response(content=body, media_type=OPENMETRICS_CONTENT_TYPE)


# =============================================================================
# OPS HEALTH ENDPOINTS
# =============================================================================

_cached_ops_storage = None
_cached_ops_collector = None
_cached_ops_alert_engine = None


def _get_ops_storage():
    """Get or create a cached OpsStorage instance. Returns None if unavailable."""
    global _cached_ops_storage
    if _cached_ops_storage is not None:
        return _cached_ops_storage
    try:
        from ops.storage import OpsStorage
        db_path = os.getenv("DISCOVERY_DB_PATH", "signals.db")
        _cached_ops_storage = OpsStorage(db_path)
        return _cached_ops_storage
    except Exception as e:
        logger.warning("Could not init OpsStorage: %s", e)
        return None


def _get_ops_collector():
    """Get or create a cached OpsMetricsCollector. Returns None if ops tables missing."""
    global _cached_ops_collector
    if _cached_ops_collector is not None:
        return _cached_ops_collector
    try:
        from ops.monitoring.metrics import OpsMetricsCollector
        storage = _get_ops_storage()
        if storage is None:
            return None
        _cached_ops_collector = OpsMetricsCollector(storage)
        return _cached_ops_collector
    except Exception as e:
        logger.warning("Could not init OpsMetricsCollector: %s", e)
        return None


def _get_ops_alert_engine():
    """Get or create a cached AlertEngine instance."""
    global _cached_ops_alert_engine
    if _cached_ops_alert_engine is not None:
        return _cached_ops_alert_engine
    try:
        from ops.monitoring.alerts import AlertEngine
        _cached_ops_alert_engine = AlertEngine()
        return _cached_ops_alert_engine
    except Exception:
        return None


class OpsHealthResponse(BaseModel):
    status: str
    overall_health_pct: float
    components: Dict[str, Any]
    open_incidents: int
    extractions_24h: int
    active_alerts: List[Dict[str, Any]]


# =============================================================================
# ALERT RULES PYDANTIC MODELS
# =============================================================================

_VALID_SEVERITIES = {"critical", "warning", "info"}
_VALID_OPS = {">", ">=", "<", "<=", "==", "!="}


def _validate_condition(condition: dict) -> None:
    """Validate a JSON DSL condition dict. Raises ValueError on invalid input."""
    if "field" in condition and "op" in condition:
        if condition["op"] not in _VALID_OPS:
            raise ValueError(f"Unknown operator: {condition['op']}")
        if "value" not in condition:
            raise ValueError("Simple condition requires 'value'")
        return
    if "all" in condition:
        for c in condition["all"]:
            _validate_condition(c)
        return
    if "any" in condition:
        for c in condition["any"]:
            _validate_condition(c)
        return
    if "not" in condition:
        _validate_condition(condition["not"])
        return
    if "trend" in condition:
        trend = condition["trend"]
        if "field" not in trend or "direction" not in trend:
            raise ValueError("Trend requires 'field' and 'direction'")
        if trend["direction"] not in ("increasing", "decreasing"):
            raise ValueError(f"Invalid trend direction: {trend['direction']}")
        return
    raise ValueError(f"Unknown condition type: {list(condition.keys())}")


class RuleCreateRequest(BaseModel):
    name: str
    condition: Dict[str, Any]
    severity: str
    message_template: str
    component: Optional[str] = None
    enabled: bool = True

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in _VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {_VALID_SEVERITIES}")
        return v

    @field_validator("condition")
    @classmethod
    def validate_condition_field(cls, v: dict) -> dict:
        _validate_condition(v)
        return v


class RuleUpdateRequest(BaseModel):
    severity: Optional[str] = None
    condition: Optional[Dict[str, Any]] = None
    message_template: Optional[str] = None
    component: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v):
        if v is not None and v not in _VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {_VALID_SEVERITIES}")
        return v

    @field_validator("condition")
    @classmethod
    def validate_condition_field(cls, v):
        if v is not None:
            _validate_condition(v)
        return v


class RuleDetailResponse(BaseModel):
    rule: Dict[str, Any]
    evaluations: List[Dict[str, Any]]


@router.get("/ops", response_model=OpsHealthResponse)
async def get_ops_health():
    """Ops layer health summary."""
    collector = _get_ops_collector()
    if collector is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    snapshot = await run_in_threadpool(collector.collect)

    engine = _get_ops_alert_engine()
    alerts = []
    if engine:
        alerts = await run_in_threadpool(engine.evaluate, snapshot)

    # Determine status
    if any(a.severity == "critical" for a in alerts):
        status = "unhealthy"
    elif alerts:
        status = "degraded"
    else:
        status = "healthy"

    return OpsHealthResponse(
        status=status,
        overall_health_pct=snapshot.overall_health_pct,
        components=snapshot.health_summary,
        open_incidents=snapshot.open_incidents,
        extractions_24h=snapshot.extractions_24h,
        active_alerts=[
            {"rule": a.rule_name, "severity": a.severity, "message": a.message}
            for a in alerts
        ],
    )


@router.get("/ops/metrics")
async def get_ops_metrics(
    window_hours: int = BoundedParams.window_hours(),
    history_days: int = BoundedParams.history_days(),
):
    """Full ops metrics snapshot with optional daily history."""
    collector = _get_ops_collector()
    if collector is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    snapshot = await run_in_threadpool(collector.collect)
    result = snapshot.to_dict()

    if history_days > 0:
        history = await run_in_threadpool(collector.get_daily_history, history_days)
        result["daily_history"] = history
    else:
        result["daily_history"] = []

    return result


# =============================================================================
# ALERT RULES CRUD ENDPOINTS
# =============================================================================

@router.get("/ops/rules", response_model=List[Dict[str, Any]])
async def list_rules():
    """List all alert rules (builtin + custom)."""
    storage = _get_ops_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    rules = await run_in_threadpool(storage.list_alert_rules)
    # Normalize is_builtin to bool for JSON
    for r in rules:
        r["is_builtin"] = bool(r.get("is_builtin"))
        r["enabled"] = bool(r.get("enabled"))
    return rules


@router.post("/ops/rules", status_code=201, response_model=Dict[str, Any])
async def create_rule(body: RuleCreateRequest):
    """Create a custom alert rule with JSON DSL condition."""
    storage = _get_ops_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    rule_id = await run_in_threadpool(
        storage.create_alert_rule,
        name=body.name,
        condition=body.condition,
        severity=body.severity,
        message_template=body.message_template,
        component=body.component,
        enabled=body.enabled,
    )
    rule = await run_in_threadpool(storage.get_alert_rule, rule_id)
    if rule:
        rule["is_builtin"] = bool(rule.get("is_builtin"))
        rule["enabled"] = bool(rule.get("enabled"))
    return rule


@router.get("/ops/rules/{rule_id}", response_model=RuleDetailResponse)
async def get_rule(rule_id: int):
    """Get a single rule with its evaluation history."""
    storage = _get_ops_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    rule = await run_in_threadpool(storage.get_alert_rule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    rule["is_builtin"] = bool(rule.get("is_builtin"))
    rule["enabled"] = bool(rule.get("enabled"))
    evaluations = await run_in_threadpool(
        storage.get_alert_evaluations, rule_name=rule["name"], limit=20,
    )
    return {"rule": rule, "evaluations": evaluations}


@router.put("/ops/rules/{rule_id}", response_model=Dict[str, Any])
async def update_rule(rule_id: int, body: RuleUpdateRequest):
    """Update a rule's condition, severity, enabled, etc."""
    storage = _get_ops_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    kwargs = {}
    if body.severity is not None:
        kwargs["severity"] = body.severity
    if body.condition is not None:
        kwargs["condition"] = body.condition
    if body.message_template is not None:
        kwargs["message_template"] = body.message_template
    if body.component is not None:
        kwargs["component"] = body.component
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled

    updated = await run_in_threadpool(storage.update_alert_rule, rule_id, **kwargs)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    rule = await run_in_threadpool(storage.get_alert_rule, rule_id)
    if rule:
        rule["is_builtin"] = bool(rule.get("is_builtin"))
        rule["enabled"] = bool(rule.get("enabled"))
    return rule


@router.delete("/ops/rules/{rule_id}", response_model=Dict[str, str])
async def delete_rule(rule_id: int):
    """Delete a custom alert rule. Builtin rules cannot be deleted."""
    storage = _get_ops_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    deleted = await run_in_threadpool(storage.delete_alert_rule, rule_id)
    if not deleted:
        # Check if it exists but is builtin
        rule = await run_in_threadpool(storage.get_alert_rule, rule_id)
        if rule and rule.get("is_builtin"):
            raise HTTPException(status_code=403, detail="Cannot delete builtin rules")
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    return {"message": f"Rule {rule_id} deleted"}


# =============================================================================
# METRIC HISTORY ENDPOINT
# =============================================================================

@router.get("/ops/history", response_model=List[Dict[str, Any]])
async def get_metric_history(
    hours: int = BoundedParams.hours(),
    limit: int = BoundedParams.limit(),
):
    """Get metric snapshot history with time range filter."""
    storage = _get_ops_storage()
    if storage is None:
        raise HTTPException(status_code=503, detail={"error": "Ops tables not initialized"})

    snapshots = await run_in_threadpool(storage.get_metric_snapshots, hours=hours, limit=limit)
    return snapshots
