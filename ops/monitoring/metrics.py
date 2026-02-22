"""Ops metrics collector — collects a consistent snapshot of ops health data.

Uses a single read-only transaction to avoid nested-transaction deadlocks
and ensure a consistent point-in-time view.
"""

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ops.storage import OpsStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpsMetricsSnapshot:
    """Immutable point-in-time ops metrics."""
    timestamp: str

    # Health
    health_summary: dict
    overall_health_pct: float

    # Extraction
    last_extraction: Optional[dict]
    extractions_24h: int
    total_cost_24h: Decimal
    avg_extraction_duration: float
    total_extractions_all_time: int

    # Facts
    facts_by_status: dict
    total_facts: int
    avg_fact_confidence: float
    unused_high_confidence_facts: int

    # Incidents
    open_incidents: int
    recent_incidents_24h: int

    # Audit
    audit_entries_24h: int

    # Phase 9: LLM Thesis Classification Metrics
    llm_calls_today: int = 0
    llm_calls_last_hour: int = 0
    llm_rate_limited_today: int = 0
    llm_timeouts_today: int = 0
    llm_errors_today: int = 0
    llm_circuit_breaker_tripped: int = 0
    thesis_disagreement_count: int = 0
    thesis_disagreement_rate: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to dict, converting Decimal to string."""
        d = asdict(self)
        d["total_cost_24h"] = str(d["total_cost_24h"])
        return d


class OpsMetricsCollector:
    """Collects ops metrics in a single consistent read transaction."""

    def __init__(self, storage: OpsStorage):
        self.storage = storage

    def collect(self) -> OpsMetricsSnapshot:
        """Collect all metrics in a SINGLE read-only transaction."""
        with self.storage.read_transaction() as conn:
            health_summary = self.storage.get_health_summary(hours=24, conn=conn)
            overall_health_pct = self._calc_overall_health(health_summary)
            last_extraction = self._get_last_extraction(conn)
            extractions_24h = self._get_extraction_count_24h(conn)
            total_cost_24h = self._get_total_cost_24h(conn)
            avg_duration = self._get_avg_extraction_duration(conn)
            total_extractions = self._get_total_extractions(conn)
            facts_by_status = self._get_facts_by_status(conn)
            total_facts = sum(facts_by_status.values())
            avg_confidence = self._get_avg_fact_confidence(conn)
            unused_hc = self._get_unused_high_confidence(conn)
            open_incidents = self._get_open_incidents(conn)
            recent_incidents = self._get_recent_incidents_24h(conn)
            audit_24h = self._get_audit_entries_24h(conn)

            # Phase 9: LLM metrics (query signals DB if available)
            llm_metrics = self._get_llm_metrics()

        return OpsMetricsSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            health_summary=health_summary,
            overall_health_pct=overall_health_pct,
            last_extraction=last_extraction,
            extractions_24h=extractions_24h,
            total_cost_24h=total_cost_24h,
            avg_extraction_duration=avg_duration,
            total_extractions_all_time=total_extractions,
            facts_by_status=facts_by_status,
            total_facts=total_facts,
            avg_fact_confidence=avg_confidence,
            unused_high_confidence_facts=unused_hc,
            open_incidents=open_incidents,
            recent_incidents_24h=recent_incidents,
            audit_entries_24h=audit_24h,
            # Phase 9: LLM metrics
            llm_calls_today=llm_metrics.get("calls_today", 0),
            llm_calls_last_hour=llm_metrics.get("calls_last_hour", 0),
            llm_rate_limited_today=llm_metrics.get("rate_limited_today", 0),
            llm_timeouts_today=llm_metrics.get("timeouts_today", 0),
            llm_errors_today=llm_metrics.get("errors_today", 0),
            llm_circuit_breaker_tripped=llm_metrics.get("circuit_breaker_tripped", 0),
            thesis_disagreement_count=llm_metrics.get("disagreement_count", 0),
            thesis_disagreement_rate=llm_metrics.get("disagreement_rate", 0.0),
        )

    def get_daily_history(self, days: int = 7) -> list:
        """Grouped extraction history for CLI/Streamlit trends."""
        with self.storage.read_transaction() as conn:
            cursor = conn.execute(
                """
                SELECT date(run_at) AS d, COUNT(*) AS runs,
                       COALESCE(SUM(estimated_cost), 0) AS cost,
                       COALESCE(AVG(duration_seconds), 0) AS avg_duration_s
                FROM extraction_runs
                WHERE run_at >= datetime('now', ?)
                GROUP BY date(run_at)
                ORDER BY d DESC
                """,
                (f"-{days} days",),
            )
            return [
                {
                    "date": row[0],
                    "runs": row[1],
                    "cost": str(Decimal(str(row[2]))),
                    "avg_duration_s": round(row[3], 2),
                }
                for row in cursor.fetchall()
            ]

    @staticmethod
    def _calc_overall_health(summary: dict) -> float:
        """Equal-weight average across all components."""
        if not summary:
            return 100.0
        pcts = [v["health_percent"] for v in summary.values()]
        return round(sum(pcts) / len(pcts), 2)

    @staticmethod
    def _get_last_extraction(conn) -> Optional[dict]:
        row = conn.execute(
            """
            SELECT id, run_at, decisions_processed, facts_created,
                   llm_failures, duration_seconds, estimated_cost
            FROM extraction_runs
            ORDER BY run_at DESC LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "run_at": row[1],
            "decisions_processed": row[2], "facts_created": row[3],
            "llm_failures": row[4], "duration_seconds": row[5],
            "estimated_cost": str(Decimal(str(row[6]))),
        }

    @staticmethod
    def _get_extraction_count_24h(conn) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM extraction_runs WHERE run_at >= datetime('now', '-24 hours')"
        ).fetchone()
        return row[0]

    @staticmethod
    def _get_total_cost_24h(conn) -> Decimal:
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) FROM extraction_runs WHERE run_at >= datetime('now', '-24 hours')"
        ).fetchone()
        return Decimal(str(row[0]))

    @staticmethod
    def _get_avg_extraction_duration(conn) -> float:
        row = conn.execute(
            "SELECT COALESCE(AVG(duration_seconds), 0) FROM extraction_runs WHERE run_at >= datetime('now', '-24 hours')"
        ).fetchone()
        return round(float(row[0]), 2)

    @staticmethod
    def _get_total_extractions(conn) -> int:
        row = conn.execute("SELECT COUNT(*) FROM extraction_runs").fetchone()
        return row[0]

    @staticmethod
    def _get_facts_by_status(conn) -> dict:
        cursor = conn.execute(
            "SELECT status, COUNT(*) FROM memory_facts GROUP BY status"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}

    @staticmethod
    def _get_avg_fact_confidence(conn) -> float:
        row = conn.execute(
            "SELECT COALESCE(AVG(confidence), 0) FROM memory_facts WHERE status = 'active'"
        ).fetchone()
        return round(float(row[0]), 4)

    @staticmethod
    def _get_unused_high_confidence(conn) -> int:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM memory_facts
            WHERE status = 'active'
            AND confidence >= 0.8
            AND COALESCE(used_count, 0) = 0
            """
        ).fetchone()
        return row[0]

    @staticmethod
    def _get_open_incidents(conn) -> int:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action_type = 'incident_open'"
            ).fetchone()
            return row[0]
        except Exception:
            return 0

    @staticmethod
    def _get_recent_incidents_24h(conn) -> int:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action_type LIKE 'incident%' AND created_at >= datetime('now', '-24 hours')"
            ).fetchone()
            return row[0]
        except Exception:
            return 0

    @staticmethod
    def _get_audit_entries_24h(conn) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE created_at >= datetime('now', '-24 hours')"
        ).fetchone()
        return row[0]

    def _get_llm_metrics(self) -> dict:
        """
        Phase 9: Collect LLM thesis classification metrics from signals DB.

        Returns dict with:
        - calls_today: Total LLM calls today
        - calls_last_hour: LLM calls in last hour
        - rate_limited_today: Rate limit failures
        - timeouts_today: Timeout failures
        - errors_today: Other errors
        - circuit_breaker_tripped: Circuit breaker trips
        - disagreement_count: Keyword-LLM disagreements today
        - disagreement_rate: % of classifications with disagreement
        """
        try:
            import os
            import sqlite3
            from pathlib import Path

            # Get signals DB path
            signals_db = os.getenv("DISCOVERY_DB_PATH", "signals.db")
            if not Path(signals_db).exists():
                logger.warning(f"Signals DB not found: {signals_db}")
                return {}

            # Connect to signals DB (separate connection)
            conn = sqlite3.connect(signals_db)
            conn.row_factory = sqlite3.Row

            # Check if thesis_classifications table exists
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_classifications'"
            ).fetchone()

            if not table_check:
                conn.close()
                return {}

            # Calls today (where LLM actually ran)
            calls_today = conn.execute(
                """
                SELECT COUNT(*) FROM thesis_classifications
                WHERE thesis_fit_score IS NOT NULL
                AND classified_at >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]

            # Calls last hour
            calls_last_hour = conn.execute(
                """
                SELECT COUNT(*) FROM thesis_classifications
                WHERE thesis_fit_score IS NOT NULL
                AND classified_at >= datetime('now', '-1 hour')
                """
            ).fetchone()[0]

            # Get error metrics from rationale field (heuristic)
            rate_limited = conn.execute(
                """
                SELECT COUNT(*) FROM thesis_classifications
                WHERE rationale LIKE '%rate limit%'
                AND classified_at >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]

            timeouts = conn.execute(
                """
                SELECT COUNT(*) FROM thesis_classifications
                WHERE rationale LIKE '%timeout%'
                AND classified_at >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]

            errors = conn.execute(
                """
                SELECT COUNT(*) FROM thesis_classifications
                WHERE rationale LIKE '%failed%' OR rationale LIKE '%error%'
                AND classified_at >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]

            circuit_breaker = conn.execute(
                """
                SELECT COUNT(*) FROM thesis_classifications
                WHERE rationale LIKE '%circuit breaker%'
                AND classified_at >= datetime('now', '-24 hours')
                """
            ).fetchone()[0]

            # Disagreements (need migration 26 for disagreement_detected column)
            # For now, compute heuristically
            disagreement_count = 0
            disagreement_rate = 0.0

            try:
                # Check if disagreement_detected column exists (migration 26)
                conn.execute("SELECT disagreement_detected FROM thesis_classifications LIMIT 1")

                disagreement_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM thesis_classifications
                    WHERE disagreement_detected = 1
                    AND classified_at >= datetime('now', '-24 hours')
                    """
                ).fetchone()[0]

                total_with_llm = conn.execute(
                    """
                    SELECT COUNT(*) FROM thesis_classifications
                    WHERE thesis_fit_score IS NOT NULL
                    AND classified_at >= datetime('now', '-24 hours')
                    """
                ).fetchone()[0]

                if total_with_llm > 0:
                    disagreement_rate = round(disagreement_count / total_with_llm, 4)

            except sqlite3.OperationalError:
                # Column doesn't exist yet (migration 26 not run)
                pass

            conn.close()

            return {
                "calls_today": calls_today,
                "calls_last_hour": calls_last_hour,
                "rate_limited_today": rate_limited,
                "timeouts_today": timeouts,
                "errors_today": errors,
                "circuit_breaker_tripped": circuit_breaker,
                "disagreement_count": disagreement_count,
                "disagreement_rate": disagreement_rate,
            }

        except Exception as e:
            logger.error(f"Failed to collect LLM metrics: {e}")
            return {}
