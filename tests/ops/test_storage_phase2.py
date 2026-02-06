"""Phase 2.0 storage layer prep tests.

Tests for read_transaction(), optional conn in get_health_summary(), and log_audit().
"""

import asyncio
import json
import sqlite3
import pytest

from ops.storage import OpsStorage


@pytest.fixture
def ops_db(tmp_path):
    """Create a standalone OpsStorage (fallback table creation)."""
    db_path = tmp_path / "test_ops.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


class TestReadTransaction:
    def test_read_transaction_does_not_use_immediate(self, ops_db):
        """read_transaction() should use plain BEGIN, not BEGIN IMMEDIATE.

        We verify by inspecting the source code of read_transaction, since
        sqlite3.Connection.execute cannot be monkey-patched on CPython.
        """
        import inspect
        source = inspect.getsource(ops_db.read_transaction)
        # Strip docstring to only check executable code
        lines = source.split("\n")
        code_lines = [l for l in lines if not l.strip().startswith(('"""', '#'))]
        code_only = "\n".join(code_lines)
        assert '"BEGIN"' in code_only or "'BEGIN'" in code_only, \
            "read_transaction should execute BEGIN"
        assert "IMMEDIATE" not in code_only, \
            "read_transaction should NOT use BEGIN IMMEDIATE"

    def test_read_transaction_commits_on_success(self, ops_db):
        """read_transaction() should commit after the block."""
        with ops_db.read_transaction() as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result[0] == 1

    def test_read_transaction_rolls_back_on_error(self, ops_db):
        """read_transaction() should rollback on exception."""
        with pytest.raises(ValueError, match="test error"):
            with ops_db.read_transaction() as conn:
                conn.execute(
                    "INSERT INTO system_health (component, status) VALUES ('test', 'healthy')"
                )
                raise ValueError("test error")

        # Verify the insert was rolled back
        with ops_db.pool.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM system_health").fetchone()[0]
            assert count == 0


class TestGetHealthSummaryWithConn:
    def test_get_health_summary_with_provided_conn(self, ops_db):
        """get_health_summary(conn=conn) should use the provided connection."""
        # Insert test data
        ops_db.log_health("comp_a", "healthy", latency_ms=10.0)
        ops_db.log_health("comp_a", "degraded", latency_ms=50.0)

        with ops_db.read_transaction() as conn:
            result = ops_db.get_health_summary(hours=24, conn=conn)

        assert "comp_a" in result
        assert result["comp_a"]["total_checks"] == 2

    def test_get_health_summary_without_conn_still_works(self, ops_db):
        """get_health_summary() without conn should still work (legacy)."""
        ops_db.log_health("comp_b", "healthy", latency_ms=5.0)

        result = ops_db.get_health_summary(hours=24)
        assert "comp_b" in result
        assert result["comp_b"]["total_checks"] == 1

    def test_get_health_summary_inside_read_transaction(self, ops_db):
        """Should not deadlock when called within read_transaction()."""
        ops_db.log_health("comp_c", "healthy", latency_ms=1.0)

        with ops_db.read_transaction() as conn:
            # This must NOT deadlock (previously it would try nested BEGIN IMMEDIATE)
            result = ops_db.get_health_summary(hours=24, conn=conn)
            assert "comp_c" in result


class TestLogAudit:
    def test_log_audit_writes_entry(self, ops_db):
        """log_audit() should insert a row into audit_log."""
        ops_db.log_audit(
            operation="TEST_OP",
            target_type="test",
            target_id=42,
            user="tester",
            before_state='{"old": true}',
            after_state='{"new": true}',
            reason="testing log_audit",
        )

        with ops_db.pool.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM audit_log WHERE operation = 'TEST_OP'"
            ).fetchone()

        assert row is not None
        assert row["target_type"] == "test"
        assert row["target_id"] == 42
        assert row["user"] == "tester"
        assert row["reason"] == "testing log_audit"

    def test_log_audit_with_conn(self, ops_db):
        """log_audit(conn=conn) should use the provided connection."""
        with ops_db.transaction() as conn:
            ops_db.log_audit(
                operation="CONN_TEST",
                target_type="alert",
                user="system",
                reason="test with conn",
                conn=conn,
            )

        with ops_db.pool.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE operation = 'CONN_TEST'"
            ).fetchone()[0]
            assert count == 1

    def test_log_audit_defaults(self, ops_db):
        """log_audit() should handle optional parameters gracefully."""
        ops_db.log_audit(
            operation="MINIMAL",
            target_type="system",
        )

        with ops_db.pool.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM audit_log WHERE operation = 'MINIMAL'"
            ).fetchone()

        assert row is not None
        assert row["user"] == "system"
        assert row["target_id"] is None
        assert row["before_state"] is None
        assert row["after_state"] is None
        assert row["reason"] is None
