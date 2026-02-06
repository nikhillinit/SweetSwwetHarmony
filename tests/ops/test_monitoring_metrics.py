"""Phase 2.1 — Ops metrics collector tests."""

import pytest
from decimal import Decimal

from ops.storage import OpsStorage
from ops.monitoring.metrics import OpsMetricsCollector, OpsMetricsSnapshot


@pytest.fixture
def ops_db(tmp_path):
    db_path = tmp_path / "test_metrics.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


@pytest.fixture
def collector(ops_db):
    return OpsMetricsCollector(ops_db)


class TestCollectEmptyDb:
    def test_collect_empty_db(self, collector):
        """All zeros/empty on fresh DB."""
        snap = collector.collect()
        assert isinstance(snap, OpsMetricsSnapshot)
        assert snap.health_summary == {}
        assert snap.overall_health_pct == 100.0
        assert snap.last_extraction is None
        assert snap.extractions_24h == 0
        assert snap.total_cost_24h == Decimal("0")
        assert snap.avg_extraction_duration == 0.0
        assert snap.total_extractions_all_time == 0
        assert snap.facts_by_status == {}
        assert snap.total_facts == 0
        assert snap.avg_fact_confidence == 0.0
        assert snap.unused_high_confidence_facts == 0
        assert snap.open_incidents == 0
        assert snap.audit_entries_24h == 0

    def test_total_cost_is_decimal(self, collector):
        """Verify Decimal type, not float."""
        snap = collector.collect()
        assert isinstance(snap.total_cost_24h, Decimal)


class TestCollectWithData:
    def test_collect_with_health_data(self, ops_db, collector):
        """Insert health rows, verify summary."""
        ops_db.log_health("db", "healthy", latency_ms=5.0)
        ops_db.log_health("db", "healthy", latency_ms=10.0)
        ops_db.log_health("api", "degraded", latency_ms=200.0)

        snap = collector.collect()
        assert "db" in snap.health_summary
        assert snap.health_summary["db"]["total_checks"] == 2
        assert snap.health_summary["db"]["health_percent"] == 100.0
        assert snap.health_summary["api"]["health_percent"] == 0.0

    def test_collect_with_extraction_runs(self, ops_db, collector):
        """Insert runs, verify counts/costs."""
        with ops_db.transaction() as conn:
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now', '-1 hour'), 10, 3, 1, 5.5, 0.25)"""
            )
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now'), 8, 2, 0, 3.0, 0.15)"""
            )

        snap = collector.collect()
        assert snap.extractions_24h == 2
        assert snap.total_cost_24h == Decimal("0.4")
        assert snap.total_extractions_all_time == 2
        assert snap.last_extraction is not None
        assert snap.last_extraction["facts_created"] == 2  # most recent by run_at

    def test_collect_with_facts(self, ops_db, collector):
        """Insert facts, verify status breakdown."""
        # Use raw connection with FK off (FK pragma must be set outside a tx)
        with ops_db.pool.get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")
            for _ in range(3):
                conn.execute(
                    "INSERT INTO memory_facts (type, content, confidence, status) "
                    "VALUES ('constraint', 'test', 0.9, 'active')"
                )
            for _ in range(2):
                conn.execute(
                    "INSERT INTO memory_facts (type, content, confidence, status) "
                    "VALUES ('nuance', 'test', 0.7, 'pending')"
                )
            conn.execute("COMMIT")
            conn.execute("PRAGMA foreign_keys = ON")

        snap = collector.collect()
        assert snap.facts_by_status.get("active") == 3
        assert snap.facts_by_status.get("pending") == 2
        assert snap.total_facts == 5

    def test_overall_health_pct_equal_weight(self, ops_db, collector):
        """Equal-weight average across components."""
        ops_db.log_health("a", "healthy")
        ops_db.log_health("b", "degraded")  # 0% healthy
        # a=100%, b=0% => average = 50%

        snap = collector.collect()
        assert snap.overall_health_pct == 50.0

    def test_total_extractions_all_time(self, ops_db, collector):
        """Count across full history, not just 24h."""
        with ops_db.transaction() as conn:
            # Insert one old run
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now', '-48 hours'), 5, 1, 0, 2.0, 0.1)"""
            )
            # Insert one recent run
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now'), 5, 1, 0, 2.0, 0.1)"""
            )

        snap = collector.collect()
        assert snap.total_extractions_all_time == 2
        assert snap.extractions_24h == 1  # only the recent one

    def test_collect_single_read_transaction(self, ops_db, collector):
        """Verify collect uses read_transaction (no BEGIN IMMEDIATE nesting)."""
        import inspect
        source = inspect.getsource(collector.collect)
        assert "read_transaction" in source


class TestDailyHistory:
    def test_get_daily_history_empty(self, collector):
        """No runs returns empty list."""
        result = collector.get_daily_history(days=7)
        assert result == []

    def test_get_daily_history_with_data(self, ops_db, collector):
        """Verify grouping and ordering."""
        with ops_db.transaction() as conn:
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now'), 5, 2, 0, 3.0, 0.10)"""
            )
            conn.execute(
                """INSERT INTO extraction_runs
                   (run_at, decisions_processed, facts_created, llm_failures,
                    duration_seconds, estimated_cost)
                   VALUES (datetime('now'), 3, 1, 1, 2.0, 0.05)"""
            )

        result = collector.get_daily_history(days=7)
        assert len(result) >= 1
        # Today's entry should have 2 runs
        today_entry = result[0]
        assert today_entry["runs"] == 2
        # SQLite SUM of floats may have precision issues
        assert abs(Decimal(today_entry["cost"]) - Decimal("0.15")) < Decimal("0.001")


class TestSnapshotSerialization:
    def test_to_dict_converts_decimal(self, collector):
        """to_dict should convert Decimal to string."""
        snap = collector.collect()
        d = snap.to_dict()
        assert isinstance(d["total_cost_24h"], str)
