"""Tier 1 Foundation -- Quality Ops labels.py tests.

Tests for normalize_label, insert_feedback, get_signal_canonical_key,
upsert_resolved_label, has_manual_label, and label_signal_manual.
"""

from __future__ import annotations

import sqlite3

import pytest

from ops.quality.labels import (
    UpsertResult,
    get_signal_canonical_key,
    has_manual_label,
    insert_feedback,
    label_signal_manual,
    normalize_label,
    upsert_resolved_label,
)
from tests.ops.quality.conftest import _utc_iso


# ---------------------------------------------------------------------------
# normalize_label
# ---------------------------------------------------------------------------

class TestNormalizeLabel:
    """Tests for normalize_label()."""

    def test_normalize_label_tp(self):
        """'TP' is accepted and returned as-is."""
        assert normalize_label("TP") == "TP"

    def test_normalize_label_fp(self):
        """'FP' is accepted and returned as-is."""
        assert normalize_label("FP") == "FP"

    def test_normalize_label_unsure(self):
        """'UNSURE' is accepted and returned as-is."""
        assert normalize_label("UNSURE") == "UNSURE"

    def test_normalize_label_case_insensitive(self):
        """Labels are case-insensitive and whitespace-trimmed."""
        assert normalize_label("tp") == "TP"
        assert normalize_label("Fp") == "FP"
        assert normalize_label(" TP ") == "TP"
        assert normalize_label("  unsure  ") == "UNSURE"

    def test_normalize_label_adj(self):
        """'ADJ' is accepted and returned as-is."""
        assert normalize_label("ADJ") == "ADJ"

    def test_normalize_label_adj_case_insensitive(self):
        """ADJ is case-insensitive."""
        assert normalize_label("adj") == "ADJ"
        assert normalize_label(" Adj ") == "ADJ"

    def test_normalize_label_invalid(self):
        """Invalid label strings must raise ValueError."""
        with pytest.raises(ValueError, match="label must be one of"):
            normalize_label("INVALID")


# ---------------------------------------------------------------------------
# insert_feedback
# ---------------------------------------------------------------------------

class TestInsertFeedback:
    """Tests for insert_feedback()."""

    def test_insert_feedback(self, quality_db_with_signals):
        """insert_feedback must create a row in quality_feedback."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        insert_feedback(conn, signal_id=signal_ids[0], label="TP", created_by="tester")

        row = conn.execute(
            "SELECT * FROM quality_feedback WHERE signal_id = ?", (signal_ids[0],)
        ).fetchone()

        assert row is not None
        assert row["label"] == "TP"
        assert row["created_by"] == "tester"
        conn.close()

    def test_insert_feedback_returns_id(self, quality_db_with_signals):
        """insert_feedback must return a positive integer (the row id)."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        fb_id = insert_feedback(conn, signal_id=signal_ids[1], label="FP")

        assert isinstance(fb_id, int)
        assert fb_id > 0
        conn.close()


# ---------------------------------------------------------------------------
# get_signal_canonical_key
# ---------------------------------------------------------------------------

class TestGetSignalCanonicalKey:
    """Tests for get_signal_canonical_key()."""

    def test_get_signal_canonical_key(self, quality_db_with_signals):
        """Returns the correct canonical_key for an existing signal."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        key = get_signal_canonical_key(conn, signal_ids[0])
        assert key == "domain:company0.com"
        conn.close()

    def test_get_signal_canonical_key_missing(self, quality_db_with_signals):
        """Raises ValueError for a non-existent signal_id."""
        db_path, _store, _signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        with pytest.raises(ValueError, match="not found"):
            get_signal_canonical_key(conn, 999999)
        conn.close()


# ---------------------------------------------------------------------------
# upsert_resolved_label
# ---------------------------------------------------------------------------

class TestUpsertResolvedLabel:
    """Tests for upsert_resolved_label()."""

    def test_upsert_resolved_label_new(self, quality_db_with_signals):
        """First upsert creates a row and returns overwritten=False."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        result = upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="TP",
            label_source="manual",
        )

        assert isinstance(result, UpsertResult)
        assert result.overwritten is False
        assert result.human_label == "TP"
        assert result.signal_id == signal_ids[0]
        conn.close()

    def test_upsert_resolved_label_update(self, quality_db_with_signals):
        """Second upsert updates the existing row and returns overwritten=True."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="TP",
            label_source="manual",
        )

        result2 = upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="FP",
            label_source="manual",
            override_manual=True,
        )

        assert result2.overwritten is True
        assert result2.human_label == "FP"
        conn.close()

    def test_upsert_resolved_label_manual_wins(self, quality_db_with_signals):
        """A non-manual label must not overwrite an existing manual label."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        # First: set a manual label
        upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="TP",
            label_source="manual",
        )

        # Second: attempt a non-manual override (should be rejected)
        result = upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="FP",
            label_source="notion_status_event",
        )

        assert result.overwritten is False
        assert result.human_label == "TP"  # manual label preserved
        assert result.label_source == "manual"
        conn.close()

    def test_upsert_resolved_label_override(self, quality_db_with_signals):
        """override_manual=True must allow overwriting a manual label."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="TP",
            label_source="manual",
        )

        result = upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="FP",
            label_source="notion_status_event",
            override_manual=True,
        )

        assert result.overwritten is True
        assert result.human_label == "FP"
        assert result.label_source == "notion_status_event"
        conn.close()


# ---------------------------------------------------------------------------
# has_manual_label
# ---------------------------------------------------------------------------

class TestHasManualLabel:
    """Tests for has_manual_label()."""

    def test_has_manual_label_true(self, quality_db_with_signals):
        """Returns True after a manual label has been set."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        upsert_resolved_label(
            conn,
            signal_id=signal_ids[0],
            canonical_key="domain:company0.com",
            human_label="TP",
            label_source="manual",
        )

        assert has_manual_label(conn, signal_ids[0]) is True
        conn.close()

    def test_has_manual_label_false(self, quality_db_with_signals):
        """Returns False when no manual label exists."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        assert has_manual_label(conn, signal_ids[0]) is False
        conn.close()


# ---------------------------------------------------------------------------
# label_signal_manual
# ---------------------------------------------------------------------------

class TestLabelSignalManual:
    """Tests for label_signal_manual() -- full convenience flow."""

    def test_label_signal_manual(self, quality_db_with_signals):
        """Full flow: returns (feedback_id, UpsertResult) with correct fields."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        fb_id, upsert = label_signal_manual(
            conn,
            signal_id=signal_ids[0],
            label="TP",
            created_by="analyst",
            reason="Matches thesis",
        )

        assert isinstance(fb_id, int)
        assert fb_id > 0
        assert isinstance(upsert, UpsertResult)
        assert upsert.human_label == "TP"
        assert upsert.label_source == "manual"
        assert upsert.canonical_key == "domain:company0.com"
        assert upsert.signal_id == signal_ids[0]
        conn.close()

    def test_label_signal_manual_overwrite(self, quality_db_with_signals):
        """Labeling the same signal twice must overwrite the first label."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

        _fb1, upsert1 = label_signal_manual(
            conn,
            signal_id=signal_ids[0],
            label="TP",
            created_by="analyst",
        )
        assert upsert1.overwritten is False

        _fb2, upsert2 = label_signal_manual(
            conn,
            signal_id=signal_ids[0],
            label="FP",
            created_by="analyst",
        )
        assert upsert2.overwritten is True
        assert upsert2.human_label == "FP"

        # Verify only one resolved label exists for this signal
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM signal_quality_metrics WHERE signal_id = ?",
            (signal_ids[0],),
        ).fetchone()["c"]
        assert count == 1

        # Verify two feedback audit rows exist
        fb_count = conn.execute(
            "SELECT COUNT(*) AS c FROM quality_feedback WHERE signal_id = ?",
            (signal_ids[0],),
        ).fetchone()["c"]
        assert fb_count == 2

        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
