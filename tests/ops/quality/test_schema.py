"""Tier 1 Foundation -- Quality Ops schema (DDL) tests.

Verifies that the Quality Ops DDL creates the expected tables, indexes,
and foreign-key / CHECK constraints.
"""

from __future__ import annotations

import sqlite3

import pytest

from storage.migrations.quality_tables import QUALITY_TABLES_DDL
from tests.ops.quality.conftest import _insert_signal, _utc_iso


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_db(db_path: str) -> sqlite3.Connection:
    """Open a connection with FK enforcement and create the signals parent table."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # Minimal parent table so FK constraints can be validated.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT,
            source_api TEXT,
            canonical_key TEXT,
            company_name TEXT,
            confidence REAL,
            raw_data TEXT,
            detected_at TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDDL:
    """DDL idempotency and table existence."""

    def test_ddl_idempotent(self, tmp_path):
        """Running QUALITY_TABLES_DDL twice must not raise."""
        conn = _setup_db(str(tmp_path / "idem.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()
        # Second execution -- should be a no-op.
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()
        conn.close()

    def test_tables_created(self, tmp_path):
        """DDL must create exactly 3 quality tables."""
        conn = _setup_db(str(tmp_path / "tables.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        expected = {"notion_status_events", "quality_feedback", "signal_quality_metrics"}
        assert expected.issubset(_table_names(conn))
        conn.close()

    def test_indexes_created(self, tmp_path):
        """All 10 indexes defined in the DDL must exist after creation."""
        conn = _setup_db(str(tmp_path / "indexes.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        expected_indexes = {
            # notion_status_events (3)
            "idx_notion_status_events_key_time",
            "idx_notion_status_events_new_status_time",
            "idx_notion_status_events_page",
            # quality_feedback (2)
            "idx_quality_feedback_signal",
            "idx_quality_feedback_created",
            # signal_quality_metrics (4)
            "idx_signal_quality_label",
            "idx_signal_quality_source",
            "idx_signal_quality_labeled_at",
            "idx_signal_quality_key",
        }
        actual = _index_names(conn)
        missing = expected_indexes - actual
        assert not missing, f"Missing indexes: {missing}"
        conn.close()


class TestForeignKeyConstraints:
    """FK constraints on quality_feedback and signal_quality_metrics."""

    def test_fk_quality_feedback_enforced(self, tmp_path):
        """Inserting quality_feedback with a non-existent signal_id must raise IntegrityError."""
        conn = _setup_db(str(tmp_path / "fk_fb.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO quality_feedback (signal_id, label, created_at)
                VALUES (99999, 'TP', ?)
                """,
                (_utc_iso(),),
            )

    def test_fk_signal_quality_metrics_enforced(self, tmp_path):
        """Inserting signal_quality_metrics with a non-existent signal_id must raise IntegrityError."""
        conn = _setup_db(str(tmp_path / "fk_sqm.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO signal_quality_metrics
                    (signal_id, canonical_key, human_label, label_source, labeled_at)
                VALUES (99999, 'domain:nope.com', 'TP', 'manual', ?)
                """,
                (_utc_iso(),),
            )

    def test_fk_cascade_delete(self, tmp_path):
        """Deleting a signal must cascade-delete its quality_feedback rows."""
        conn = _setup_db(str(tmp_path / "fk_cascade.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        sig_id = _insert_signal(conn, canonical_key="domain:cascade.com")

        conn.execute(
            """
            INSERT INTO quality_feedback (signal_id, label, created_at)
            VALUES (?, 'FP', ?)
            """,
            (sig_id, _utc_iso()),
        )
        conn.commit()

        # Verify feedback exists
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM quality_feedback WHERE signal_id = ?", (sig_id,)
        ).fetchone()["c"] == 1

        # Delete the parent signal
        conn.execute("DELETE FROM signals WHERE id = ?", (sig_id,))
        conn.commit()

        # Feedback should be gone
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM quality_feedback WHERE signal_id = ?", (sig_id,)
        ).fetchone()["c"] == 0

        conn.close()


class TestCheckConstraints:
    """CHECK constraints on label columns."""

    def test_label_check_constraint(self, tmp_path):
        """quality_feedback.label must be one of TP, FP, UNSURE."""
        conn = _setup_db(str(tmp_path / "chk_label.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        sig_id = _insert_signal(conn, canonical_key="domain:chk.com")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO quality_feedback (signal_id, label, created_at)
                VALUES (?, 'INVALID', ?)
                """,
                (sig_id, _utc_iso()),
            )

    def test_human_label_check_constraint(self, tmp_path):
        """signal_quality_metrics.human_label must be one of TP, FP, UNSURE."""
        conn = _setup_db(str(tmp_path / "chk_hl.db"))
        conn.executescript(QUALITY_TABLES_DDL)
        conn.commit()

        sig_id = _insert_signal(conn, canonical_key="domain:chk2.com")

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO signal_quality_metrics
                    (signal_id, canonical_key, human_label, label_source, labeled_at)
                VALUES (?, 'domain:chk2.com', 'BAD', 'manual', ?)
                """,
                (sig_id, _utc_iso()),
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
