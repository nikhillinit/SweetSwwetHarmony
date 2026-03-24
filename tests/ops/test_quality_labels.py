"""Tests for ops.quality.labels module.

Covers CRUD operations, idempotent relabeling, audit trail, validation,
has_manual_label, and upsert_resolved_label override behavior.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

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
from tests.fixtures.db import tmp_db  # noqa: F401 -- pytest fixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enable_row_factory(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Enable Row factory so dict-style access works (required by labels.py)."""
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_quality_feedback_table(conn: sqlite3.Connection) -> None:
    """Create the quality_feedback table that labels.py writes to.

    The tmp_db fixture from tests.fixtures.db creates quality_label_feedback,
    but the production code (and labels.py) uses quality_feedback as defined
    in the canonical migration DDL.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS quality_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            label TEXT CHECK(label IN ('TP', 'FP', 'UNSURE', 'ADJ')) NOT NULL,
            reason TEXT,
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            metadata TEXT,
            FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
        );
    """)
    conn.commit()


def _insert_test_signal(
    conn: sqlite3.Connection,
    signal_id: int | None = None,
    canonical_key: str = "domain:acme.ai",
    company_name: str = "Acme AI",
) -> int:
    """Insert a minimal signal row and return its id."""
    params = {
        "signal_type": "test_signal",
        "source_api": "test_source",
        "canonical_key": canonical_key,
        "company_name": company_name,
        "confidence": 0.75,
        "raw_data": json.dumps({"description": "Test company"}),
        "detected_at": _utc_iso(),
        "created_at": _utc_iso(),
    }
    if signal_id is not None:
        cur = conn.execute(
            """INSERT INTO signals
               (id, signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (?, :signal_type, :source_api, :canonical_key, :company_name,
                       :confidence, :raw_data, :detected_at, :created_at)""",
            (signal_id, *[params[k] for k in [
                "signal_type", "source_api", "canonical_key", "company_name",
                "confidence", "raw_data", "detected_at", "created_at",
            ]]),
        )
    else:
        cur = conn.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (:signal_type, :source_api, :canonical_key, :company_name,
                       :confidence, :raw_data, :detected_at, :created_at)""",
            params,
        )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def db(tmp_db):  # noqa: F811
    """Wrap the shared tmp_db fixture with row_factory and quality_feedback table."""
    conn = _enable_row_factory(tmp_db)
    _ensure_quality_feedback_table(conn)
    return conn


@pytest.fixture
def db_with_signals(db):
    """db fixture pre-populated with three test signals.

    Returns (conn, signal_ids) where signal_ids is a list of three integer ids.
    """
    ids = [
        _insert_test_signal(db, canonical_key="domain:alpha.com", company_name="Alpha Inc"),
        _insert_test_signal(db, canonical_key="domain:beta.io", company_name="Beta Corp"),
        _insert_test_signal(db, canonical_key="domain:gamma.co", company_name="Gamma LLC"),
    ]
    return db, ids


# ===========================================================================
# 1. normalize_label
# ===========================================================================


class TestNormalizeLabel:
    """Validate label string normalization and rejection of invalid values."""

    def test_accepts_tp(self):
        """TP is a valid label."""
        assert normalize_label("TP") == "TP"

    def test_accepts_fp(self):
        """FP is a valid label."""
        assert normalize_label("FP") == "FP"

    def test_accepts_unsure(self):
        """UNSURE is a valid label."""
        assert normalize_label("UNSURE") == "UNSURE"

    def test_accepts_adj(self):
        """ADJ is a valid label."""
        assert normalize_label("ADJ") == "ADJ"

    def test_case_insensitive(self):
        """Labels are normalized to uppercase regardless of input case."""
        assert normalize_label("tp") == "TP"
        assert normalize_label("Fp") == "FP"
        assert normalize_label("unsure") == "UNSURE"
        assert normalize_label("adj") == "ADJ"

    def test_strips_whitespace(self):
        """Leading and trailing whitespace is stripped."""
        assert normalize_label("  TP  ") == "TP"
        assert normalize_label("\tFP\n") == "FP"

    def test_raises_on_invalid_label(self):
        """Invalid label strings must raise ValueError."""
        with pytest.raises(ValueError, match="label must be one of"):
            normalize_label("INVALID")

    def test_raises_on_empty_string(self):
        """Empty string must raise ValueError."""
        with pytest.raises(ValueError, match="label must be one of"):
            normalize_label("")

    def test_raises_on_none_like(self):
        """None-ish empty input must raise ValueError."""
        with pytest.raises(ValueError, match="label must be one of"):
            normalize_label("   ")


# ===========================================================================
# 2. get_signal_canonical_key
# ===========================================================================


class TestGetSignalCanonicalKey:
    """Validate canonical key lookup from the signals table."""

    def test_returns_correct_key(self, db_with_signals):
        """Returns the canonical_key for an existing signal."""
        conn, sids = db_with_signals
        key = get_signal_canonical_key(conn, sids[0])
        assert key == "domain:alpha.com"

    def test_raises_for_missing_signal(self, db):
        """Raises ValueError when signal_id does not exist."""
        with pytest.raises(ValueError, match="not found"):
            get_signal_canonical_key(db, 999999)


# ===========================================================================
# 3. label_signal_manual -- CRUD
# ===========================================================================


class TestLabelSignalManualCRUD:
    """label_signal_manual creates correct rows in both tables."""

    def test_creates_metric_and_feedback_rows(self, db_with_signals):
        """First call creates one row in signal_quality_metrics and one in quality_feedback."""
        conn, sids = db_with_signals
        fb_id, upsert = label_signal_manual(
            conn,
            signal_id=sids[0],
            label="TP",
            created_by="analyst",
            reason="Matches thesis",
        )

        # Feedback row
        assert isinstance(fb_id, int) and fb_id > 0
        fb_row = conn.execute(
            "SELECT * FROM quality_feedback WHERE id = ?", (fb_id,)
        ).fetchone()
        assert fb_row is not None
        assert fb_row["signal_id"] == sids[0]
        assert fb_row["label"] == "TP"
        assert fb_row["created_by"] == "analyst"
        assert fb_row["reason"] == "Matches thesis"

        # Metrics row
        assert isinstance(upsert, UpsertResult)
        assert upsert.human_label == "TP"
        assert upsert.label_source == "manual"
        assert upsert.canonical_key == "domain:alpha.com"
        assert upsert.signal_id == sids[0]
        assert upsert.overwritten is False

        metric_row = conn.execute(
            "SELECT * FROM signal_quality_metrics WHERE signal_id = ?", (sids[0],)
        ).fetchone()
        assert metric_row is not None
        assert metric_row["human_label"] == "TP"
        assert metric_row["label_source"] == "manual"

    def test_metadata_with_reason_merged(self, db_with_signals):
        """When reason is provided, metadata in the metrics row includes it."""
        conn, sids = db_with_signals
        _fb_id, _upsert = label_signal_manual(
            conn,
            signal_id=sids[1],
            label="FP",
            reason="B2B SaaS",
            metadata={"extra": "info"},
        )
        metric_row = conn.execute(
            "SELECT metadata FROM signal_quality_metrics WHERE signal_id = ?",
            (sids[1],),
        ).fetchone()
        meta = json.loads(metric_row["metadata"])
        assert meta["reason"] == "B2B SaaS"
        assert meta["extra"] == "info"


# ===========================================================================
# 4. Idempotent relabeling
# ===========================================================================


class TestIdempotentRelabeling:
    """Calling label_signal_manual twice on the same signal overwrites the label."""

    def test_second_call_overwrites(self, db_with_signals):
        """Second label_signal_manual call returns overwritten=True and updates the label."""
        conn, sids = db_with_signals
        _fb1, upsert1 = label_signal_manual(
            conn, signal_id=sids[0], label="TP", created_by="analyst"
        )
        assert upsert1.overwritten is False

        _fb2, upsert2 = label_signal_manual(
            conn, signal_id=sids[0], label="FP", created_by="analyst"
        )
        assert upsert2.overwritten is True
        assert upsert2.human_label == "FP"

    def test_only_one_metric_row_after_overwrite(self, db_with_signals):
        """After relabeling, there is still only one row in signal_quality_metrics."""
        conn, sids = db_with_signals
        label_signal_manual(conn, signal_id=sids[0], label="TP")
        label_signal_manual(conn, signal_id=sids[0], label="FP")

        count = conn.execute(
            "SELECT COUNT(*) AS c FROM signal_quality_metrics WHERE signal_id = ?",
            (sids[0],),
        ).fetchone()["c"]
        assert count == 1

    def test_both_feedback_rows_preserved(self, db_with_signals):
        """After relabeling, both feedback audit rows exist (append-only)."""
        conn, sids = db_with_signals
        label_signal_manual(conn, signal_id=sids[0], label="TP", reason="first")
        label_signal_manual(conn, signal_id=sids[0], label="FP", reason="second")

        fb_rows = conn.execute(
            "SELECT * FROM quality_feedback WHERE signal_id = ? ORDER BY id",
            (sids[0],),
        ).fetchall()
        assert len(fb_rows) == 2
        assert fb_rows[0]["label"] == "TP"
        assert fb_rows[0]["reason"] == "first"
        assert fb_rows[1]["label"] == "FP"
        assert fb_rows[1]["reason"] == "second"


# ===========================================================================
# 5. Audit trail -- insert_feedback
# ===========================================================================


class TestInsertFeedback:
    """insert_feedback creates a feedback row without touching signal_quality_metrics."""

    def test_creates_feedback_row(self, db_with_signals):
        """insert_feedback writes to quality_feedback and returns a positive id."""
        conn, sids = db_with_signals
        fb_id = insert_feedback(
            conn,
            signal_id=sids[0],
            label="FP",
            created_by="reviewer",
            reason="Not consumer",
            notes="Pure B2B SaaS",
        )
        assert isinstance(fb_id, int) and fb_id > 0

        row = conn.execute(
            "SELECT * FROM quality_feedback WHERE id = ?", (fb_id,)
        ).fetchone()
        assert row["signal_id"] == sids[0]
        assert row["label"] == "FP"
        assert row["created_by"] == "reviewer"
        assert row["reason"] == "Not consumer"
        assert row["notes"] == "Pure B2B SaaS"

    def test_does_not_create_metric_row(self, db_with_signals):
        """insert_feedback must not upsert into signal_quality_metrics."""
        conn, sids = db_with_signals
        insert_feedback(conn, signal_id=sids[0], label="TP")

        metric = conn.execute(
            "SELECT * FROM signal_quality_metrics WHERE signal_id = ?", (sids[0],)
        ).fetchone()
        assert metric is None

    def test_multiple_feedback_rows_allowed(self, db_with_signals):
        """Multiple feedback rows for the same signal are allowed (append-only)."""
        conn, sids = db_with_signals
        insert_feedback(conn, signal_id=sids[0], label="TP")
        insert_feedback(conn, signal_id=sids[0], label="FP")
        insert_feedback(conn, signal_id=sids[0], label="UNSURE")

        count = conn.execute(
            "SELECT COUNT(*) AS c FROM quality_feedback WHERE signal_id = ?",
            (sids[0],),
        ).fetchone()["c"]
        assert count == 3

    def test_metadata_stored_as_json(self, db_with_signals):
        """Metadata dict is serialized as JSON in the quality_feedback row."""
        conn, sids = db_with_signals
        fb_id = insert_feedback(
            conn,
            signal_id=sids[0],
            label="FP",
            metadata={"source": "bulk_review", "batch": 42},
        )
        row = conn.execute(
            "SELECT metadata FROM quality_feedback WHERE id = ?", (fb_id,)
        ).fetchone()
        meta = json.loads(row["metadata"])
        assert meta == {"source": "bulk_review", "batch": 42}


# ===========================================================================
# 6. has_manual_label
# ===========================================================================


class TestHasManualLabel:
    """Validate has_manual_label returns True/False correctly."""

    def test_returns_false_when_no_label(self, db_with_signals):
        """Returns False when signal has no entry in signal_quality_metrics."""
        conn, sids = db_with_signals
        assert has_manual_label(conn, sids[0]) is False

    def test_returns_true_after_manual_label(self, db_with_signals):
        """Returns True after a manual label has been set via upsert."""
        conn, sids = db_with_signals
        upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="manual",
        )
        assert has_manual_label(conn, sids[0]) is True

    def test_returns_false_for_non_manual_label(self, db_with_signals):
        """Returns False when an entry exists but label_source is not 'manual'."""
        conn, sids = db_with_signals
        upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="notion_status_event",
        )
        assert has_manual_label(conn, sids[0]) is False

    def test_returns_false_for_nonexistent_signal(self, db):
        """Returns False for a signal_id that has never been labeled."""
        assert has_manual_label(db, 999999) is False


# ===========================================================================
# 7. upsert_resolved_label -- override_manual behavior
# ===========================================================================


class TestUpsertResolvedLabelOverride:
    """Validate that override_manual controls whether manual labels are overwritten."""

    def test_first_upsert_overwritten_false(self, db_with_signals):
        """First upsert for a signal returns overwritten=False."""
        conn, sids = db_with_signals
        result = upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="manual",
        )
        assert result.overwritten is False
        assert result.human_label == "TP"

    def test_override_manual_false_skips_overwrite(self, db_with_signals):
        """Non-manual source with override_manual=False preserves existing manual label."""
        conn, sids = db_with_signals

        # Set a manual label
        upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="manual",
        )

        # Attempt non-manual overwrite
        result = upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="FP",
            label_source="notion_status_event",
            override_manual=False,
        )

        assert result.overwritten is False
        assert result.human_label == "TP"  # manual label preserved
        assert result.label_source == "manual"

        # Verify the DB still has the manual label
        row = conn.execute(
            "SELECT human_label, label_source FROM signal_quality_metrics WHERE signal_id = ?",
            (sids[0],),
        ).fetchone()
        assert row["human_label"] == "TP"
        assert row["label_source"] == "manual"

    def test_override_manual_true_overwrites(self, db_with_signals):
        """Non-manual source with override_manual=True overwrites existing manual label."""
        conn, sids = db_with_signals

        # Set a manual label
        upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="manual",
        )

        # Override it
        result = upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="FP",
            label_source="notion_status_event",
            override_manual=True,
        )

        assert result.overwritten is True
        assert result.human_label == "FP"
        assert result.label_source == "notion_status_event"

        # Verify the DB was updated
        row = conn.execute(
            "SELECT human_label, label_source FROM signal_quality_metrics WHERE signal_id = ?",
            (sids[0],),
        ).fetchone()
        assert row["human_label"] == "FP"
        assert row["label_source"] == "notion_status_event"

    def test_non_manual_overwrites_non_manual(self, db_with_signals):
        """A non-manual source can overwrite another non-manual source without override flag."""
        conn, sids = db_with_signals

        # Set a notion_status_event label
        upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="notion_status_event",
        )

        # Another non-manual source overwrites it (no manual label exists)
        result = upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="FP",
            label_source="auto",
            override_manual=False,
        )

        assert result.overwritten is True
        assert result.human_label == "FP"
        assert result.label_source == "auto"

    def test_manual_overwrite_default_is_false(self, db_with_signals):
        """Default value for override_manual is False (protection is on by default)."""
        conn, sids = db_with_signals

        upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="TP",
            label_source="manual",
        )

        # Call without specifying override_manual (defaults to False)
        result = upsert_resolved_label(
            conn,
            signal_id=sids[0],
            canonical_key="domain:alpha.com",
            human_label="FP",
            label_source="notion_status_event",
        )

        assert result.overwritten is False
        assert result.human_label == "TP"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
