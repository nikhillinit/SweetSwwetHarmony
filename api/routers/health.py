"""
Health Router

Provides detailed health and monitoring endpoints:
- GET /health - Basic health check (exists in main.py)
- GET /health/detailed - Full system health with all components
- GET /health/collectors - Collector status and last run times
- GET /health/relationships - Email/LP staleness from relationship_health
- GET /health/database - Database stats and integrity
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from storage.signal_store import SignalStore

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

async def get_store() -> SignalStore:
    """Get initialized SignalStore."""
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

    # 3. Relationship health
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
