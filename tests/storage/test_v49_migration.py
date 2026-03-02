"""Tests for v49 migration — ADJ label in CHECK constraints.

Verifies:
- Migration preserves existing data and PKs
- CHECK constraint accepts ADJ
- CHECK constraint still rejects INVALID
- Migration is idempotent (running twice doesn't break)
"""
from __future__ import annotations

import sqlite3

import pytest

from storage.migrations.v49_adjacent_label import V49_ADJACENT_LABEL_DDL


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _create_pre_v49_tables(conn: sqlite3.Connection) -> None:
    """Create quality tables with the old CHECK (no ADJ)."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL DEFAULT '{}',
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notion_status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_key TEXT NOT NULL,
            new_status TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quality_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            label TEXT CHECK(label IN ('TP', 'FP', 'UNSURE')) NOT NULL,
            reason TEXT,
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            metadata TEXT,
            FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_feedback_signal ON quality_feedback(signal_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_quality_feedback_created ON quality_feedback(created_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_quality_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL UNIQUE,
            canonical_key TEXT NOT NULL,
            human_label TEXT CHECK(human_label IN ('TP', 'FP', 'UNSURE')) NOT NULL,
            label_source TEXT NOT NULL,
            labeled_by TEXT,
            labeled_at TEXT NOT NULL,
            notion_page_id TEXT,
            notion_status TEXT,
            status_event_id INTEGER,
            days_to_outcome REAL,
            notes TEXT,
            metadata TEXT,
            FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE,
            FOREIGN KEY(status_event_id) REFERENCES notion_status_events(id) ON DELETE SET NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_quality_label ON signal_quality_metrics(human_label)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_quality_source ON signal_quality_metrics(label_source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_quality_labeled_at ON signal_quality_metrics(labeled_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_quality_key ON signal_quality_metrics(canonical_key)")
    conn.commit()


def _insert_signal(conn: sqlite3.Connection, canonical_key: str = "domain:test.com") -> int:
    cur = conn.execute(
        "INSERT INTO signals (signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) "
        "VALUES ('test', 'test', ?, 'Test Co', 0.5, '{}', ?, ?)",
        (canonical_key, _utc_now(), _utc_now()),
    )
    conn.commit()
    return cur.lastrowid


class TestV49Migration:
    """v49 migration preserves data and adds ADJ to CHECK."""

    def test_migration_preserves_data(self):
        """Existing TP/FP/UNSURE rows survive the migration with correct PKs."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _create_pre_v49_tables(conn)

        sig_id = _insert_signal(conn)
        conn.execute(
            "INSERT INTO quality_feedback (signal_id, label, created_at) VALUES (?, 'FP', ?)",
            (sig_id, _utc_now()),
        )
        conn.execute(
            "INSERT INTO signal_quality_metrics (signal_id, canonical_key, human_label, label_source, labeled_at) "
            "VALUES (?, 'domain:test.com', 'FP', 'manual', ?)",
            (sig_id, _utc_now()),
        )
        conn.commit()

        # Run migration
        conn.executescript(V49_ADJACENT_LABEL_DDL)

        # Verify data preserved
        fb = conn.execute("SELECT * FROM quality_feedback WHERE signal_id = ?", (sig_id,)).fetchone()
        assert fb is not None
        assert fb["label"] == "FP"
        assert fb["signal_id"] == sig_id

        sqm = conn.execute("SELECT * FROM signal_quality_metrics WHERE signal_id = ?", (sig_id,)).fetchone()
        assert sqm is not None
        assert sqm["human_label"] == "FP"
        assert sqm["canonical_key"] == "domain:test.com"
        conn.close()

    def test_adj_accepted_after_migration(self):
        """ADJ label can be inserted after migration."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _create_pre_v49_tables(conn)
        conn.executescript(V49_ADJACENT_LABEL_DDL)

        sig_id = _insert_signal(conn)

        # Should not raise
        conn.execute(
            "INSERT INTO quality_feedback (signal_id, label, created_at) VALUES (?, 'ADJ', ?)",
            (sig_id, _utc_now()),
        )
        conn.execute(
            "INSERT INTO signal_quality_metrics (signal_id, canonical_key, human_label, label_source, labeled_at) "
            "VALUES (?, 'domain:test.com', 'ADJ', 'manual', ?)",
            (sig_id, _utc_now()),
        )
        conn.commit()

        fb = conn.execute("SELECT label FROM quality_feedback WHERE signal_id = ?", (sig_id,)).fetchone()
        assert fb["label"] == "ADJ"

        sqm = conn.execute("SELECT human_label FROM signal_quality_metrics WHERE signal_id = ?", (sig_id,)).fetchone()
        assert sqm["human_label"] == "ADJ"
        conn.close()

    def test_invalid_still_rejected_after_migration(self):
        """INVALID label is still rejected by CHECK after migration."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _create_pre_v49_tables(conn)
        conn.executescript(V49_ADJACENT_LABEL_DDL)

        sig_id = _insert_signal(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO quality_feedback (signal_id, label, created_at) VALUES (?, 'INVALID', ?)",
                (sig_id, _utc_now()),
            )

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO signal_quality_metrics (signal_id, canonical_key, human_label, label_source, labeled_at) "
                "VALUES (?, 'domain:test.com', 'INVALID', 'manual', ?)",
                (sig_id, _utc_now()),
            )
        conn.close()

    def test_migration_idempotent(self):
        """Running v49 migration twice must not raise."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _create_pre_v49_tables(conn)

        sig_id = _insert_signal(conn)
        conn.execute(
            "INSERT INTO quality_feedback (signal_id, label, created_at) VALUES (?, 'TP', ?)",
            (sig_id, _utc_now()),
        )
        conn.commit()

        conn.executescript(V49_ADJACENT_LABEL_DDL)
        conn.executescript(V49_ADJACENT_LABEL_DDL)

        # Data should still be there
        fb = conn.execute("SELECT label FROM quality_feedback WHERE signal_id = ?", (sig_id,)).fetchone()
        assert fb["label"] == "TP"
        conn.close()

    def test_indexes_recreated(self):
        """All expected indexes exist after migration."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _create_pre_v49_tables(conn)
        conn.executescript(V49_ADJACENT_LABEL_DDL)

        indexes = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

        expected = {
            "idx_quality_feedback_signal",
            "idx_quality_feedback_created",
            "idx_signal_quality_label",
            "idx_signal_quality_source",
            "idx_signal_quality_labeled_at",
            "idx_signal_quality_key",
        }
        assert expected.issubset(indexes), f"Missing: {expected - indexes}"
        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
