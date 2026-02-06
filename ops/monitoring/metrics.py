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
                "SELECT COUNT(*) FROM audit_log WHERE operation = 'incident_open'"
            ).fetchone()
            return row[0]
        except Exception:
            return 0

    @staticmethod
    def _get_recent_incidents_24h(conn) -> int:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE operation LIKE 'incident%' AND timestamp >= datetime('now', '-24 hours')"
            ).fetchone()
            return row[0]
        except Exception:
            return 0

    @staticmethod
    def _get_audit_entries_24h(conn) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE timestamp >= datetime('now', '-24 hours')"
        ).fetchone()
        return row[0]
