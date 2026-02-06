"""Baseline integration tests for ops layer.

Verifies that ops tables are created correctly alongside signal_store,
with no schema conflicts.
"""

import asyncio
import pytest
import sqlite3
from pathlib import Path


@pytest.fixture
def clean_db(tmp_path):
    """Create a clean DB with signal_store v24 + ops tables."""
    db_path = tmp_path / "test.db"

    # Initialize signal_store tables first (owns the signals table)
    from storage.signal_store import SignalStore

    store = SignalStore(str(db_path))
    asyncio.get_event_loop().run_until_complete(store.initialize())

    # Now init OpsStorage (should detect v24 and skip fallback)
    from ops.storage import OpsStorage

    storage = OpsStorage(str(db_path))
    yield storage
    del storage

    # Cleanup
    asyncio.get_event_loop().run_until_complete(store.close())


def test_fts5_available(clean_db):
    """Verify FTS5 virtual table was created."""
    with clean_db.pool.get_connection() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_facts_fts'"
        )
        assert cursor.fetchone() is not None, "memory_facts_fts table should exist"


def test_wal_mode_enabled(clean_db):
    """Verify WAL mode is enabled for concurrent access."""
    with clean_db.pool.get_connection() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"


def test_migrations_applied(clean_db):
    """Verify v24 migration was applied."""
    with clean_db.pool.get_connection() as conn:
        cursor = conn.execute("SELECT MAX(version) FROM schema_migrations")
        version = cursor.fetchone()[0]
        assert version >= 24, f"Expected schema version >= 24, got {version}"


def test_search_facts_empty(clean_db):
    """Verify search_facts returns empty list on empty DB."""
    results = clean_db.search_facts("test query", limit=10)
    assert results == []


def test_no_signals_table_conflict(clean_db):
    """CRITICAL: Verify ops didn't create a conflicting signals table."""
    with clean_db.pool.get_connection() as conn:
        cursor = conn.execute("PRAGMA table_info(signals)")
        columns = [row[1] for row in cursor.fetchall()]

        # Must have signal_store columns
        assert "canonical_key" in columns, "signals table should have canonical_key"
        assert "signal_type" in columns, "signals table should have signal_type"
        assert "source_api" in columns, "signals table should have source_api"
        assert "raw_data" in columns, "signals table should have raw_data"

        # Must NOT have ops columns
        assert "title" not in columns, "signals table should NOT have ops 'title' column"
        assert "company_id" not in columns, "signals table should NOT have ops 'company_id' column"
        assert "description" not in columns, "signals table should NOT have ops 'description' column"


def test_ops_tables_exist(clean_db):
    """Verify all 8 ops tables were created."""
    expected_tables = [
        "user_actions",
        "memory_facts",
        "memory_facts_fts",
        "memory_action_state",
        "extraction_runs",
        "audit_log",
        "system_health",
        "fact_citations",
    ]

    with clean_db.pool.get_connection() as conn:
        for table in expected_tables:
            cursor = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                (table,),
            )
            assert cursor.fetchone() is not None, f"Table {table} should exist"


def test_fts5_triggers_exist(clean_db):
    """Verify FTS5 sync triggers were created."""
    expected_triggers = ["memory_facts_ai", "memory_facts_ad", "memory_facts_au"]

    with clean_db.pool.get_connection() as conn:
        for trigger in expected_triggers:
            cursor = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger,),
            )
            assert cursor.fetchone() is not None, f"Trigger {trigger} should exist"


def test_insert_and_search_fact(clean_db):
    """Verify FTS5 insert + search round-trip."""
    with clean_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO memory_facts (type, content, confidence, status)
            VALUES ('constraint', 'Avoid hardware companies with margins below 20 percent', 0.9, 'active')
            """
        )

    results = clean_db.search_facts("hardware margin")
    assert len(results) >= 1, "FTS5 search should return the inserted fact"
    assert "hardware" in results[0]["content"].lower()


def test_health_logging(clean_db):
    """Verify health check logging works."""
    clean_db.log_health("test_component", "healthy", latency_ms=42.5)
    clean_db.log_health("test_component", "degraded", latency_ms=150.0, error="slow")

    summary = clean_db.get_health_summary(hours=1)
    assert "test_component" in summary
    assert summary["test_component"]["total_checks"] == 2
    assert summary["test_component"]["health_percent"] == 50.0


def test_fact_lifecycle(clean_db):
    """Verify fact lifecycle: pending -> active -> retired."""
    with clean_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO memory_facts (type, content, confidence, status)
            VALUES ('nuance', 'Test fact for lifecycle', 0.8, 'pending')
            """
        )
        fact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Promote to active
        conn.execute(
            "UPDATE memory_facts SET status = 'active' WHERE id = ?",
            (fact_id,),
        )
        status = conn.execute(
            "SELECT status FROM memory_facts WHERE id = ?", (fact_id,)
        ).fetchone()[0]
        assert status == "active"

        # Retire
        conn.execute(
            "UPDATE memory_facts SET status = 'retired' WHERE id = ?",
            (fact_id,),
        )
        status = conn.execute(
            "SELECT status FROM memory_facts WHERE id = ?", (fact_id,)
        ).fetchone()[0]
        assert status == "retired"


def test_audit_log(clean_db):
    """Verify audit log captures entries."""
    with clean_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO audit_log (operation, target_type, target_id, user, reason)
            VALUES ('test_op', 'memory_fact', 1, 'test_user', 'testing')
            """
        )
        cursor = conn.execute("SELECT COUNT(*) FROM audit_log")
        assert cursor.fetchone()[0] == 1


def test_record_fact_usage(clean_db):
    """Verify fact usage tracking and citation recording."""
    # First insert a signal (needed for FK) and a fact
    with clean_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO signals (signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at)
            VALUES ('test', 'test', 'domain:test.com', 'Test Co', 0.5, '{}', datetime('now'), datetime('now'))
            """
        )
        signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """
            INSERT INTO memory_facts (type, content, confidence, status)
            VALUES ('constraint', 'Test usage tracking', 0.85, 'active')
            """
        )
        fact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Record usage
    clean_db.record_fact_usage([fact_id], signal_id=signal_id, context="test context")

    # Verify
    with clean_db.transaction() as conn:
        conn.row_factory = sqlite3.Row
        fact = conn.execute(
            "SELECT used_count FROM memory_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        assert fact["used_count"] == 1

        citation = conn.execute(
            "SELECT * FROM fact_citations WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        assert citation is not None
        assert citation["signal_id"] == signal_id
        assert citation["context"] == "test context"
