"""
Tests for RelationshipHealthMonitor - Track relationship data staleness.

TDD: Write failing tests first, then implement.

Key requirements:
- Detect stale email scans (> N days since last import)
- Detect stale LP syncs (> N days since last sync)
- Configurable thresholds
- Generate alerts and health reports
"""

import pytest
import tempfile
import os
from datetime import datetime, timezone, timedelta


@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.unlink(path)
        except PermissionError:
            pass


# =============================================================================
# RELATIONSHIP HEALTH MONITOR TESTS
# =============================================================================

class TestRelationshipHealthMonitorInit:
    """Tests for RelationshipHealthMonitor initialization."""

    def test_init_with_defaults(self):
        """Should initialize with default thresholds."""
        from utils.relationship_health import RelationshipHealthMonitor

        monitor = RelationshipHealthMonitor()
        assert monitor.email_stale_days == 7
        assert monitor.lp_stale_days == 3
        assert monitor.critical_stale_multiplier == 3

    def test_init_with_custom_thresholds(self):
        """Should accept custom thresholds."""
        from utils.relationship_health import RelationshipHealthMonitor

        monitor = RelationshipHealthMonitor(
            email_stale_days=14,
            lp_stale_days=7,
            critical_stale_multiplier=2,
        )
        assert monitor.email_stale_days == 14
        assert monitor.lp_stale_days == 7
        assert monitor.critical_stale_multiplier == 2


class TestEmailScanStaleness:
    """Tests for email scan staleness detection."""

    @pytest.mark.asyncio
    async def test_detects_stale_email_scan(self, temp_db):
        """Should detect when email scan is stale."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add old email relationship (10 days ago)
            old_date = datetime.now(timezone.utc) - timedelta(days=10)
            await store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="old.com",
                intro_count=1,
                reply_count=0,
                total_messages=1,
                last_contact_at=old_date,
                first_contact_at=old_date,
            )

            monitor = RelationshipHealthMonitor(email_stale_days=7)
            report = await monitor.check_email_staleness(store, "user@example.com")

            assert report.is_stale is True
            assert report.days_since_scan > 7

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_fresh_email_scan_not_stale(self, temp_db):
        """Should not flag fresh email scans as stale."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add recent email relationship (1 day ago)
            recent_date = datetime.now(timezone.utc) - timedelta(days=1)
            await store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="recent.com",
                intro_count=1,
                reply_count=0,
                total_messages=1,
                last_contact_at=recent_date,
            )

            monitor = RelationshipHealthMonitor(email_stale_days=7)
            report = await monitor.check_email_staleness(store, "user@example.com")

            assert report.is_stale is False

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_no_email_data_returns_unknown(self, temp_db):
        """Should return unknown status when no email data exists."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            monitor = RelationshipHealthMonitor()
            report = await monitor.check_email_staleness(store, "user@example.com")

            assert report.status == "UNKNOWN"
            assert report.days_since_scan is None

        finally:
            await store.close()


class TestLPSyncStaleness:
    """Tests for LP sync staleness detection."""

    @pytest.mark.asyncio
    async def test_detects_stale_lp_sync(self, temp_db):
        """Should detect when LP sync is stale."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add LP relationship with old updated_at
            await store.upsert_lp_relationship(
                me_email="user@example.com",
                target_domain="old-lp.com",
                lp_status="Docs Signed",
                lp_name="Old LP",
                notion_score=0.95,
            )

            # Manually backdate the updated_at (simulate old sync)
            async with store.transaction() as conn:
                old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
                await conn.execute(
                    "UPDATE domain_relationships SET updated_at = ? WHERE target_domain = ?",
                    (old_date, "old-lp.com"),
                )
                await conn.commit()

            monitor = RelationshipHealthMonitor(lp_stale_days=3)
            report = await monitor.check_lp_staleness(store, "user@example.com")

            assert report.is_stale is True
            assert report.days_since_sync > 3

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_fresh_lp_sync_not_stale(self, temp_db):
        """Should not flag fresh LP syncs as stale."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add recent LP relationship
            await store.upsert_lp_relationship(
                me_email="user@example.com",
                target_domain="fresh-lp.com",
                lp_status="Verbal Confirm",
                lp_name="Fresh LP",
                notion_score=0.70,
            )

            monitor = RelationshipHealthMonitor(lp_stale_days=3)
            report = await monitor.check_lp_staleness(store, "user@example.com")

            assert report.is_stale is False

        finally:
            await store.close()


class TestHealthReport:
    """Tests for overall health report generation."""

    @pytest.mark.asyncio
    async def test_generates_combined_health_report(self, temp_db):
        """Should generate combined health report."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add some data
            now = datetime.now(timezone.utc)
            await store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="gmail.investor.com",
                intro_count=3,
                reply_count=2,
                total_messages=5,
                last_contact_at=now,
            )

            await store.upsert_lp_relationship(
                me_email="user@example.com",
                target_domain="lp.fund.com",
                lp_status="Docs Signed",
                lp_name="LP Fund",
                notion_score=0.95,
            )

            monitor = RelationshipHealthMonitor()
            report = await monitor.generate_report(store, "user@example.com")

            assert report.email_health is not None
            assert report.lp_health is not None
            assert report.overall_status in ["HEALTHY", "WARNING", "CRITICAL", "UNKNOWN"]
            assert hasattr(report, "relationship_count")

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_report_to_dict(self, temp_db):
        """Health report should convert to dict."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            monitor = RelationshipHealthMonitor()
            report = await monitor.generate_report(store, "user@example.com")

            report_dict = report.to_dict()
            assert isinstance(report_dict, dict)
            assert "email_health" in report_dict
            assert "lp_health" in report_dict
            assert "overall_status" in report_dict

        finally:
            await store.close()


class TestHealthAlerts:
    """Tests for health alert generation."""

    @pytest.mark.asyncio
    async def test_generates_warning_for_stale_data(self, temp_db):
        """Should generate warning alert for stale data."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add stale email data
            old_date = datetime.now(timezone.utc) - timedelta(days=10)
            await store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="stale.com",
                intro_count=1,
                reply_count=0,
                total_messages=1,
                last_contact_at=old_date,
            )

            monitor = RelationshipHealthMonitor(email_stale_days=7)
            report = await monitor.generate_report(store, "user@example.com")

            assert len(report.alerts) > 0
            assert any(a.severity == "WARNING" for a in report.alerts)

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_generates_critical_for_very_stale_data(self, temp_db):
        """Should generate critical alert for very stale data."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add very stale email data (30 days)
            old_date = datetime.now(timezone.utc) - timedelta(days=30)
            await store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="very-stale.com",
                intro_count=1,
                reply_count=0,
                total_messages=1,
                last_contact_at=old_date,
            )

            # 7 days stale, 3x multiplier = 21 days for critical
            monitor = RelationshipHealthMonitor(
                email_stale_days=7,
                critical_stale_multiplier=3,
            )
            report = await monitor.generate_report(store, "user@example.com")

            assert any(a.severity == "CRITICAL" for a in report.alerts)

        finally:
            await store.close()


class TestRelationshipStats:
    """Tests for relationship statistics."""

    @pytest.mark.asyncio
    async def test_counts_gmail_relationships(self, temp_db):
        """Should count Gmail-only relationships."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            now = datetime.now(timezone.utc)

            # Add Gmail relationships
            for i in range(3):
                await store.upsert_domain_edge(
                    me_email="user@example.com",
                    target_domain=f"gmail{i}.com",
                    intro_count=1,
                    reply_count=0,
                    total_messages=1,
                    last_contact_at=now,
                )

            monitor = RelationshipHealthMonitor()
            report = await monitor.generate_report(store, "user@example.com")

            assert report.gmail_relationship_count == 3

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_counts_lp_relationships(self, temp_db):
        """Should count LP relationships."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            # Add LP relationships
            for i in range(2):
                await store.upsert_lp_relationship(
                    me_email="user@example.com",
                    target_domain=f"lp{i}.com",
                    lp_status="Docs Signed",
                    lp_name=f"LP {i}",
                    notion_score=0.95,
                )

            monitor = RelationshipHealthMonitor()
            report = await monitor.generate_report(store, "user@example.com")

            assert report.lp_relationship_count == 2

        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_counts_combined_relationships(self, temp_db):
        """Should count relationships with both Gmail and LP data."""
        from utils.relationship_health import RelationshipHealthMonitor
        from storage.relationship_store import RelationshipStore

        store = RelationshipStore(db_path=temp_db)
        await store.initialize()

        try:
            now = datetime.now(timezone.utc)

            # Add Gmail first
            await store.upsert_domain_edge(
                me_email="user@example.com",
                target_domain="combined.com",
                intro_count=5,
                reply_count=3,
                total_messages=10,
                last_contact_at=now,
            )

            # Then add LP to same domain
            await store.upsert_lp_relationship(
                me_email="user@example.com",
                target_domain="combined.com",
                lp_status="Verbal Confirm",
                lp_name="Combined LP",
                notion_score=0.70,
            )

            monitor = RelationshipHealthMonitor()
            report = await monitor.generate_report(store, "user@example.com")

            assert report.combined_relationship_count == 1

        finally:
            await store.close()
