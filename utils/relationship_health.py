"""
RelationshipHealthMonitor - Track relationship data staleness.

Monitors:
- Email scan staleness (days since last MBOX import)
- LP sync staleness (days since last Notion sync)
- Overall relationship health and coverage

Usage:
    from utils.relationship_health import RelationshipHealthMonitor

    monitor = RelationshipHealthMonitor(
        email_stale_days=7,
        lp_stale_days=3,
    )
    report = await monitor.generate_report(store, "user@example.com")
    print(report.to_dict())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.relationship_store import RelationshipStore

logger = logging.getLogger(__name__)


# =============================================================================
# DEFAULT THRESHOLDS
# =============================================================================

# Email scan staleness (days)
DEFAULT_EMAIL_STALE_DAYS = 7
DEFAULT_LP_STALE_DAYS = 3

# Critical multiplier (stale * multiplier = critical)
DEFAULT_CRITICAL_MULTIPLIER = 3


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class StalenessReport:
    """Report for a single data source staleness check."""
    source_type: str  # "email" or "lp"
    status: str = "UNKNOWN"  # HEALTHY, WARNING, CRITICAL, UNKNOWN
    is_stale: bool = False
    days_since_scan: Optional[int] = None
    days_since_sync: Optional[int] = None
    last_activity_at: Optional[datetime] = None
    record_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type,
            "status": self.status,
            "is_stale": self.is_stale,
            "days_since_scan": self.days_since_scan,
            "days_since_sync": self.days_since_sync,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "record_count": self.record_count,
        }


@dataclass
class HealthAlert:
    """A health alert for relationship data."""
    alert_type: str
    severity: str  # WARNING, CRITICAL
    source_type: str
    description: str
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "source_type": self.source_type,
            "description": self.description,
            "detected_at": self.detected_at.isoformat(),
        }


@dataclass
class RelationshipHealthReport:
    """Complete health report for relationship data."""
    email_health: StalenessReport
    lp_health: StalenessReport
    overall_status: str = "UNKNOWN"  # HEALTHY, WARNING, CRITICAL, UNKNOWN
    relationship_count: int = 0
    gmail_relationship_count: int = 0
    lp_relationship_count: int = 0
    combined_relationship_count: int = 0
    alerts: List[HealthAlert] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "email_health": self.email_health.to_dict(),
            "lp_health": self.lp_health.to_dict(),
            "overall_status": self.overall_status,
            "relationship_count": self.relationship_count,
            "gmail_relationship_count": self.gmail_relationship_count,
            "lp_relationship_count": self.lp_relationship_count,
            "combined_relationship_count": self.combined_relationship_count,
            "alerts": [a.to_dict() for a in self.alerts],
            "generated_at": self.generated_at.isoformat(),
        }


# =============================================================================
# RELATIONSHIP HEALTH MONITOR
# =============================================================================

class RelationshipHealthMonitor:
    """
    Monitors relationship data staleness and health.

    Tracks when email scans and LP syncs were last performed
    and generates alerts when data becomes stale.
    """

    def __init__(
        self,
        email_stale_days: int = DEFAULT_EMAIL_STALE_DAYS,
        lp_stale_days: int = DEFAULT_LP_STALE_DAYS,
        critical_stale_multiplier: int = DEFAULT_CRITICAL_MULTIPLIER,
    ):
        """
        Initialize RelationshipHealthMonitor.

        Args:
            email_stale_days: Days until email data is considered stale (default: 7)
            lp_stale_days: Days until LP data is considered stale (default: 3)
            critical_stale_multiplier: Multiplier for critical threshold (default: 3)
        """
        self.email_stale_days = email_stale_days
        self.lp_stale_days = lp_stale_days
        self.critical_stale_multiplier = critical_stale_multiplier

    async def check_email_staleness(
        self,
        store: "RelationshipStore",
        user_email: str,
    ) -> StalenessReport:
        """
        Check staleness of email scan data.

        Args:
            store: RelationshipStore instance
            user_email: User's email address

        Returns:
            StalenessReport for email data
        """
        report = StalenessReport(source_type="email")

        # Get most recent email activity
        relationships = await store.get_all_relationships(user_email, min_strength=0.0)

        # Filter to Gmail-only (has total_messages but no lp_status)
        gmail_only = [r for r in relationships if r.total_messages > 0]

        if not gmail_only:
            report.status = "UNKNOWN"
            return report

        report.record_count = len(gmail_only)

        # Find most recent activity
        most_recent = max(gmail_only, key=lambda r: r.last_contact_at)
        report.last_activity_at = most_recent.last_contact_at

        # Calculate days since scan (using updated_at or last_contact_at)
        now = datetime.now(timezone.utc)
        days_since = (now - most_recent.last_contact_at).days
        report.days_since_scan = days_since

        # Determine status
        critical_days = self.email_stale_days * self.critical_stale_multiplier
        if days_since >= critical_days:
            report.status = "CRITICAL"
            report.is_stale = True
        elif days_since >= self.email_stale_days:
            report.status = "WARNING"
            report.is_stale = True
        else:
            report.status = "HEALTHY"
            report.is_stale = False

        return report

    async def check_lp_staleness(
        self,
        store: "RelationshipStore",
        user_email: str,
    ) -> StalenessReport:
        """
        Check staleness of LP sync data.

        Args:
            store: RelationshipStore instance
            user_email: User's email address

        Returns:
            StalenessReport for LP data
        """
        report = StalenessReport(source_type="lp")

        # Get LP relationships by checking updated_at for lp_status records
        me_email_hash = store._hash_email(user_email)

        async with store.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT updated_at, lp_status
                FROM domain_relationships
                WHERE me_email_hash = ? AND lp_status IS NOT NULL
                ORDER BY updated_at DESC
                """,
                (me_email_hash,),
            )
            rows = await cursor.fetchall()

        if not rows:
            report.status = "UNKNOWN"
            return report

        report.record_count = len(rows)

        # Find most recent sync
        most_recent_str = rows[0][0]
        most_recent = datetime.fromisoformat(most_recent_str)
        if most_recent.tzinfo is None:
            most_recent = most_recent.replace(tzinfo=timezone.utc)
        report.last_activity_at = most_recent

        # Calculate days since sync
        now = datetime.now(timezone.utc)
        days_since = (now - most_recent).days
        report.days_since_sync = days_since

        # Determine status
        critical_days = self.lp_stale_days * self.critical_stale_multiplier
        if days_since >= critical_days:
            report.status = "CRITICAL"
            report.is_stale = True
        elif days_since >= self.lp_stale_days:
            report.status = "WARNING"
            report.is_stale = True
        else:
            report.status = "HEALTHY"
            report.is_stale = False

        return report

    async def _count_relationships(
        self,
        store: "RelationshipStore",
        user_email: str,
    ) -> Dict[str, int]:
        """Count relationships by type."""
        me_email_hash = store._hash_email(user_email)

        async with store.transaction() as conn:
            # Gmail-only: has total_messages, no lp_status
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM domain_relationships
                WHERE me_email_hash = ? AND total_messages > 0 AND lp_status IS NULL
                """,
                (me_email_hash,),
            )
            gmail_only = (await cursor.fetchone())[0]

            # LP-only: has lp_status, no total_messages
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM domain_relationships
                WHERE me_email_hash = ? AND lp_status IS NOT NULL AND total_messages = 0
                """,
                (me_email_hash,),
            )
            lp_only = (await cursor.fetchone())[0]

            # Combined: has both
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM domain_relationships
                WHERE me_email_hash = ? AND total_messages > 0 AND lp_status IS NOT NULL
                """,
                (me_email_hash,),
            )
            combined = (await cursor.fetchone())[0]

            # Total
            cursor = await conn.execute(
                """
                SELECT COUNT(*) FROM domain_relationships
                WHERE me_email_hash = ?
                """,
                (me_email_hash,),
            )
            total = (await cursor.fetchone())[0]

        return {
            "total": total,
            "gmail_only": gmail_only,
            "lp_only": lp_only,
            "combined": combined,
        }

    async def generate_report(
        self,
        store: "RelationshipStore",
        user_email: str,
    ) -> RelationshipHealthReport:
        """
        Generate complete health report.

        Args:
            store: RelationshipStore instance
            user_email: User's email address

        Returns:
            RelationshipHealthReport with all metrics and alerts
        """
        # Check staleness
        email_health = await self.check_email_staleness(store, user_email)
        lp_health = await self.check_lp_staleness(store, user_email)

        # Count relationships
        counts = await self._count_relationships(store, user_email)

        # Generate alerts
        alerts = []

        # Email staleness alerts
        if email_health.is_stale:
            severity = "CRITICAL" if email_health.status == "CRITICAL" else "WARNING"
            alerts.append(HealthAlert(
                alert_type="EMAIL_STALE",
                severity=severity,
                source_type="email",
                description=f"Email data is {email_health.days_since_scan} days old. "
                           f"Consider running 'import-emails' to refresh.",
            ))

        # LP staleness alerts
        if lp_health.is_stale:
            severity = "CRITICAL" if lp_health.status == "CRITICAL" else "WARNING"
            alerts.append(HealthAlert(
                alert_type="LP_STALE",
                severity=severity,
                source_type="lp",
                description=f"LP data is {lp_health.days_since_sync} days old. "
                           f"Consider running 'sync-lps' to refresh.",
            ))

        # Determine overall status
        if any(a.severity == "CRITICAL" for a in alerts):
            overall_status = "CRITICAL"
        elif any(a.severity == "WARNING" for a in alerts):
            overall_status = "WARNING"
        elif email_health.status == "UNKNOWN" and lp_health.status == "UNKNOWN":
            overall_status = "UNKNOWN"
        else:
            overall_status = "HEALTHY"

        return RelationshipHealthReport(
            email_health=email_health,
            lp_health=lp_health,
            overall_status=overall_status,
            relationship_count=counts["total"],
            gmail_relationship_count=counts["gmail_only"] + counts["combined"],
            lp_relationship_count=counts["lp_only"] + counts["combined"],
            combined_relationship_count=counts["combined"],
            alerts=alerts,
        )
