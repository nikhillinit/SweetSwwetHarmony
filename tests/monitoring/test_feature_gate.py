"""Tests for monitoring/feature_gate.py.

Covers: compute_config_snapshot, _parse_due_at (Z handling),
_ensure_feature_decisions_view (DROP+CREATE), get_overdue_regret_checks
(strict mode, actor_id NOT NULL validation).
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from monitoring.feature_gate import (
    REGRET_CHECK_WINDOW_DAYS,
    _ensure_feature_decisions_view,
    _parse_due_at,
    compute_config_snapshot,
    get_overdue_regret_checks,
)

# v35 schema subset needed for tests
_AUDIT_EVENTS_DDL = """
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_email TEXT,
    actor_role TEXT,
    before_state TEXT,
    after_state TEXT,
    reason TEXT,
    correlation_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
"""


@pytest.fixture
def gate_db(tmp_path):
    """Create a temp DB with audit_events table."""
    db_path = str(tmp_path / "gate_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUDIT_EVENTS_DDL)
    conn.close()
    return db_path


def _insert_event(db_path, action_type, entity_id, created_at, actor_id="operator:test"):
    """Helper to insert an audit_event."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO audit_events "
        "(action_type, entity_type, entity_id, actor_id, created_at) "
        "VALUES (?, 'feature_flag', ?, ?, ?)",
        (action_type, entity_id, actor_id, created_at),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# compute_config_snapshot
# ---------------------------------------------------------------------------

class TestComputeConfigSnapshot:
    def test_returns_hash_and_flags(self, monkeypatch):
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        result = compute_config_snapshot()
        assert "hash" in result
        assert len(result["hash"]) == 16
        assert "flags" in result
        assert result["flags"]["LLM_THESIS_MODE"] == "shadow"

    def test_deterministic_hash(self, monkeypatch):
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
        s1 = compute_config_snapshot()
        s2 = compute_config_snapshot()
        assert s1["hash"] == s2["hash"]

    def test_missing_env_excluded(self, monkeypatch):
        for key in ["LLM_THESIS_MODE", "DELIVERY_MODE", "ML_ENABLEMENT"]:
            monkeypatch.delenv(key, raising=False)
        result = compute_config_snapshot()
        assert "LLM_THESIS_MODE" not in result["flags"]


# ---------------------------------------------------------------------------
# _parse_due_at — timestamp Z suffix handling
# ---------------------------------------------------------------------------

class TestParseDueAt:
    def test_iso_with_timezone(self):
        due = _parse_due_at("2026-02-10T00:00:00+00:00", window_days=14)
        assert due == datetime(2026, 2, 24, tzinfo=timezone.utc)

    def test_naive_timestamp(self):
        due = _parse_due_at("2026-02-10 00:00:00", window_days=14)
        assert due == datetime(2026, 2, 24, tzinfo=timezone.utc)

    def test_z_suffix(self):
        """Z suffix must be handled correctly, not cause a fallback."""
        due = _parse_due_at("2026-02-10T00:00:00Z", window_days=14)
        assert due == datetime(2026, 2, 24, tzinfo=timezone.utc)

    def test_z_suffix_with_fractional_seconds(self):
        due = _parse_due_at("2026-02-10T12:30:45.123Z", window_days=14)
        expected = datetime(2026, 2, 24, 12, 30, 45, 123000, tzinfo=timezone.utc)
        assert due == expected

    def test_garbage_falls_back(self):
        """Unparseable timestamps fall back to now + window."""
        before = datetime.now(timezone.utc) + timedelta(days=14)
        due = _parse_due_at("not-a-date", window_days=14)
        after = datetime.now(timezone.utc) + timedelta(days=14)
        assert before <= due <= after


# ---------------------------------------------------------------------------
# _ensure_feature_decisions_view — DROP+CREATE pattern
# ---------------------------------------------------------------------------

class TestFeatureDecisionsView:
    def test_creates_view(self, gate_db):
        conn = sqlite3.connect(gate_db)
        _ensure_feature_decisions_view(conn)
        # View should exist
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name='feature_decisions'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_drop_recreate_updates_definition(self, gate_db):
        """DROP+CREATE ensures the view is always current (not stale)."""
        conn = sqlite3.connect(gate_db)
        # Create an old-style view manually
        conn.execute(
            "CREATE VIEW feature_decisions AS "
            "SELECT id FROM audit_events WHERE action_type = 'feature_promote'"
        )
        # Now call our function — should DROP and recreate
        _ensure_feature_decisions_view(conn)
        # Verify the new view has more columns (not just id)
        cursor = conn.execute("PRAGMA table_info(feature_decisions)")
        columns = [row[1] for row in cursor.fetchall()]
        assert "action_type" in columns
        assert "actor_id" in columns
        conn.close()

    def test_view_uses_wildcard(self, gate_db):
        """View uses LIKE 'feature_%' for forward compatibility."""
        conn = sqlite3.connect(gate_db)
        _ensure_feature_decisions_view(conn)
        # Insert a feature_promote and a feature_demote event
        conn.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, created_at) "
            "VALUES ('feature_promote', 'flag', 'A', 'op', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, created_at) "
            "VALUES ('feature_demote', 'flag', 'B', 'op', '2026-01-02T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, created_at) "
            "VALUES ('signal_collected', 'signal', 'C', 'pipeline', '2026-01-03T00:00:00Z')"
        )
        conn.commit()
        rows = conn.execute("SELECT * FROM feature_decisions").fetchall()
        # Only feature_ prefixed events should appear
        assert len(rows) == 2
        conn.close()


# ---------------------------------------------------------------------------
# get_overdue_regret_checks
# ---------------------------------------------------------------------------

class TestGetOverdueRegretChecks:
    def test_no_audit_events_table_non_strict(self, tmp_path):
        """Missing table + strict=False → empty result."""
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        result = get_overdue_regret_checks(db_path, strict=False)
        assert result == {"count": 0, "overdue": []}

    def test_no_audit_events_table_strict(self, tmp_path):
        """Missing table + strict=True → raises OperationalError."""
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        with pytest.raises(sqlite3.OperationalError, match="audit_events table missing"):
            get_overdue_regret_checks(db_path, strict=True)

    def test_no_promotions(self, gate_db):
        """Empty audit_events → count 0."""
        result = get_overdue_regret_checks(gate_db)
        assert result["count"] == 0
        assert result["overdue"] == []

    def test_promotion_not_yet_due(self, gate_db):
        """Promotion within window → not overdue."""
        now = datetime.now(timezone.utc)
        _insert_event(gate_db, "feature_promote", "FLAG_A", now.isoformat())
        result = get_overdue_regret_checks(gate_db)
        assert result["count"] == 0

    def test_overdue_promotion(self, gate_db):
        """Promotion past window with no regret_check → overdue."""
        old = datetime.now(timezone.utc) - timedelta(days=REGRET_CHECK_WINDOW_DAYS + 1)
        _insert_event(gate_db, "feature_promote", "FLAG_B", old.isoformat())
        result = get_overdue_regret_checks(gate_db)
        assert result["count"] == 1
        assert result["overdue"][0]["entity_id"] == "FLAG_B"

    def test_regret_check_clears_overdue(self, gate_db):
        """Promotion + matching regret_check → not overdue."""
        old = datetime.now(timezone.utc) - timedelta(days=REGRET_CHECK_WINDOW_DAYS + 1)
        _insert_event(gate_db, "feature_promote", "FLAG_C", old.isoformat())
        # Add a regret check after the promotion
        check_time = old + timedelta(days=7)
        _insert_event(gate_db, "regret_check", "FLAG_C", check_time.isoformat())
        result = get_overdue_regret_checks(gate_db)
        assert result["count"] == 0

    def test_actor_id_not_null_enforced(self, gate_db):
        """Verify schema enforces actor_id NOT NULL (documentation bug fix)."""
        conn = sqlite3.connect(gate_db)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audit_events "
                "(action_type, entity_type, entity_id, actor_id, created_at) "
                "VALUES ('feature_promote', 'flag', 'X', NULL, '2026-01-01T00:00:00Z')"
            )
        conn.close()

    def test_z_suffix_timestamp_in_promotion(self, gate_db):
        """Promotion with Z-suffixed timestamp is parsed correctly."""
        old = datetime.now(timezone.utc) - timedelta(days=REGRET_CHECK_WINDOW_DAYS + 1)
        z_ts = old.strftime("%Y-%m-%dT%H:%M:%SZ")
        _insert_event(gate_db, "feature_promote", "FLAG_Z", z_ts)
        result = get_overdue_regret_checks(gate_db)
        assert result["count"] == 1
        assert result["overdue"][0]["entity_id"] == "FLAG_Z"

    def test_metadata_regret_due_at_overrides_legacy(self, gate_db):
        """metadata.regret_due_at in future prevents false overdue even if created_at is old."""
        # created_at far in past → legacy math would mark overdue
        old = datetime.now(timezone.utc) - timedelta(days=REGRET_CHECK_WINDOW_DAYS + 30)
        # But metadata says due date is in the future
        future_due = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        metadata = json.dumps({"regret_due_at": future_due})

        conn = sqlite3.connect(gate_db)
        conn.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
            "VALUES (?, 'feature_flag', ?, ?, ?, ?)",
            ("feature_promote", "FLAG_META_FUTURE", "operator:test", metadata, old.isoformat()),
        )
        conn.commit()
        conn.close()

        result = get_overdue_regret_checks(gate_db)
        # Should NOT be overdue because metadata.regret_due_at is in the future
        overdue_ids = [r["entity_id"] for r in result["overdue"]]
        assert "FLAG_META_FUTURE" not in overdue_ids

    def test_metadata_regret_due_at_past_marks_overdue(self, gate_db):
        """metadata.regret_due_at in past marks feature as overdue."""
        # created_at recent → legacy math would NOT mark overdue
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        # But metadata says due date has already passed
        past_due = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        metadata = json.dumps({"regret_due_at": past_due})

        conn = sqlite3.connect(gate_db)
        conn.execute(
            "INSERT INTO audit_events "
            "(action_type, entity_type, entity_id, actor_id, metadata, created_at) "
            "VALUES (?, 'feature_flag', ?, ?, ?, ?)",
            ("feature_promote", "FLAG_META_PAST", "operator:test", metadata, recent.isoformat()),
        )
        conn.commit()
        conn.close()

        result = get_overdue_regret_checks(gate_db)
        overdue_ids = [r["entity_id"] for r in result["overdue"]]
        assert "FLAG_META_PAST" in overdue_ids
