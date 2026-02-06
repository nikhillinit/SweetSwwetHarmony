"""Phase 5 storage tests — alert_rules, metric_snapshots, alert_evaluations.

TDD RED tests: These should all FAIL until the storage layer is implemented.
"""

import json
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta

from ops.storage import OpsStorage


@pytest.fixture
def ops_db(tmp_path):
    """Create a standalone OpsStorage (fallback table creation)."""
    db_path = tmp_path / "test_phase5.db"
    storage = OpsStorage(str(db_path))
    yield storage
    del storage


# ── Table existence ──────────────────────────────────────────────────────

class TestPhase5Tables:
    def test_alert_rules_table_exists(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            assert OpsStorage._table_exists(conn, "alert_rules")

    def test_metric_snapshots_table_exists(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            assert OpsStorage._table_exists(conn, "metric_snapshots")

    def test_alert_evaluations_table_exists(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            assert OpsStorage._table_exists(conn, "alert_evaluations")

    def test_alert_rules_columns(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(alert_rules)")
            cols = {row[1] for row in cursor.fetchall()}
            expected = {
                "id", "name", "condition_json", "severity", "component",
                "message_template", "enabled", "is_builtin",
                "created_at", "updated_at",
            }
            assert expected.issubset(cols)

    def test_metric_snapshots_columns(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(metric_snapshots)")
            cols = {row[1] for row in cursor.fetchall()}
            assert {"id", "timestamp", "snapshot_json"}.issubset(cols)

    def test_alert_evaluations_columns(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(alert_evaluations)")
            cols = {row[1] for row in cursor.fetchall()}
            expected = {
                "id", "rule_name", "fingerprint", "severity",
                "message", "fired_at", "resolved_at", "snapshot_id",
            }
            assert expected.issubset(cols)

    def test_alert_rules_name_unique(self, ops_db):
        """alert_rules.name must be UNIQUE."""
        with ops_db.transaction() as conn:
            conn.execute(
                "INSERT INTO alert_rules (name, condition_json, severity, message_template) "
                "VALUES ('r1', '{}', 'warning', 'test')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            with ops_db.transaction() as conn:
                conn.execute(
                    "INSERT INTO alert_rules (name, condition_json, severity, message_template) "
                    "VALUES ('r1', '{}', 'warning', 'test2')"
                )

    def test_alert_rules_severity_check(self, ops_db):
        """severity must be critical/warning/info."""
        with pytest.raises(sqlite3.IntegrityError):
            with ops_db.transaction() as conn:
                conn.execute(
                    "INSERT INTO alert_rules (name, condition_json, severity, message_template) "
                    "VALUES ('bad', '{}', 'panic', 'nope')"
                )

    def test_metric_snapshots_index_exists(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_metric_snapshots_ts'"
            )
            assert cursor.fetchone() is not None

    def test_alert_evaluations_index_exists(self, ops_db):
        with ops_db.pool.get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_alert_evals_rule'"
            )
            assert cursor.fetchone() is not None


# ── Alert Rules CRUD ─────────────────────────────────────────────────────

class TestAlertRulesCRUD:
    def _sample_condition(self):
        return {"field": "total_cost_24h", "op": ">", "value": 5.0}

    def test_create_alert_rule(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="cost_high",
            condition=self._sample_condition(),
            severity="warning",
            message_template="Cost exceeds $5",
            component="cost",
        )
        assert isinstance(rule_id, int)
        assert rule_id > 0

    def test_get_alert_rule(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="cost_high",
            condition=self._sample_condition(),
            severity="warning",
            message_template="Cost exceeds $5",
        )
        rule = ops_db.get_alert_rule(rule_id)
        assert rule is not None
        assert rule["name"] == "cost_high"
        assert rule["severity"] == "warning"
        assert rule["enabled"] == 1
        assert rule["is_builtin"] == 0
        assert json.loads(rule["condition_json"]) == self._sample_condition()

    def test_get_alert_rule_not_found(self, ops_db):
        result = ops_db.get_alert_rule(9999)
        assert result is None

    def test_list_alert_rules_empty(self, ops_db):
        rules = ops_db.list_alert_rules()
        assert rules == []

    def test_list_alert_rules(self, ops_db):
        ops_db.create_alert_rule(
            name="rule_a", condition={"field": "x", "op": ">", "value": 1},
            severity="warning", message_template="A",
        )
        ops_db.create_alert_rule(
            name="rule_b", condition={"field": "y", "op": "<", "value": 2},
            severity="critical", message_template="B",
        )
        rules = ops_db.list_alert_rules()
        assert len(rules) == 2
        names = {r["name"] for r in rules}
        assert names == {"rule_a", "rule_b"}

    def test_list_alert_rules_enabled_only(self, ops_db):
        ops_db.create_alert_rule(
            name="enabled_rule", condition={"field": "x", "op": ">", "value": 1},
            severity="warning", message_template="E",
        )
        r2 = ops_db.create_alert_rule(
            name="disabled_rule", condition={"field": "y", "op": "<", "value": 2},
            severity="info", message_template="D",
        )
        ops_db.update_alert_rule(r2, enabled=False)
        rules = ops_db.list_alert_rules(enabled_only=True)
        assert len(rules) == 1
        assert rules[0]["name"] == "enabled_rule"

    def test_update_alert_rule_severity(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="r1", condition=self._sample_condition(),
            severity="warning", message_template="T",
        )
        updated = ops_db.update_alert_rule(rule_id, severity="critical")
        assert updated is True
        rule = ops_db.get_alert_rule(rule_id)
        assert rule["severity"] == "critical"

    def test_update_alert_rule_condition(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="r1", condition=self._sample_condition(),
            severity="warning", message_template="T",
        )
        new_cond = {"field": "open_incidents", "op": ">", "value": 10}
        ops_db.update_alert_rule(rule_id, condition=new_cond)
        rule = ops_db.get_alert_rule(rule_id)
        assert json.loads(rule["condition_json"]) == new_cond

    def test_update_alert_rule_enabled_toggle(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="r1", condition=self._sample_condition(),
            severity="warning", message_template="T",
        )
        ops_db.update_alert_rule(rule_id, enabled=False)
        rule = ops_db.get_alert_rule(rule_id)
        assert rule["enabled"] == 0

        ops_db.update_alert_rule(rule_id, enabled=True)
        rule = ops_db.get_alert_rule(rule_id)
        assert rule["enabled"] == 1

    def test_update_alert_rule_not_found(self, ops_db):
        result = ops_db.update_alert_rule(9999, severity="info")
        assert result is False

    def test_update_alert_rule_sets_updated_at(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="r1", condition=self._sample_condition(),
            severity="warning", message_template="T",
        )
        before = ops_db.get_alert_rule(rule_id)
        ops_db.update_alert_rule(rule_id, severity="critical")
        after = ops_db.get_alert_rule(rule_id)
        assert after["updated_at"] >= before["updated_at"]

    def test_delete_alert_rule(self, ops_db):
        rule_id = ops_db.create_alert_rule(
            name="r1", condition=self._sample_condition(),
            severity="warning", message_template="T",
        )
        deleted = ops_db.delete_alert_rule(rule_id)
        assert deleted is True
        assert ops_db.get_alert_rule(rule_id) is None

    def test_delete_alert_rule_not_found(self, ops_db):
        result = ops_db.delete_alert_rule(9999)
        assert result is False

    def test_delete_builtin_rule_blocked(self, ops_db):
        """Cannot delete a rule where is_builtin=1."""
        with ops_db.transaction() as conn:
            conn.execute(
                "INSERT INTO alert_rules (name, condition_json, severity, message_template, is_builtin) "
                "VALUES ('builtin_x', '{}', 'warning', 'sys', 1)"
            )
            rule_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        deleted = ops_db.delete_alert_rule(rule_id)
        assert deleted is False
        assert ops_db.get_alert_rule(rule_id) is not None

    def test_create_duplicate_name_raises(self, ops_db):
        ops_db.create_alert_rule(
            name="unique_rule", condition=self._sample_condition(),
            severity="warning", message_template="T",
        )
        with pytest.raises(sqlite3.IntegrityError):
            ops_db.create_alert_rule(
                name="unique_rule", condition={"field": "x", "op": ">", "value": 1},
                severity="info", message_template="T2",
            )


# ── Metric Snapshots ─────────────────────────────────────────────────────

class TestMetricSnapshots:
    def _sample_snapshot(self):
        return {
            "timestamp": "2026-02-06T12:00:00+00:00",
            "health_summary": {},
            "overall_health_pct": 100.0,
            "total_cost_24h": "0.00",
            "extractions_24h": 0,
            "total_extractions_all_time": 0,
            "open_incidents": 0,
        }

    def test_save_metric_snapshot(self, ops_db):
        snap_id = ops_db.save_metric_snapshot(self._sample_snapshot())
        assert isinstance(snap_id, int)
        assert snap_id > 0

    def test_get_metric_snapshots_empty(self, ops_db):
        snaps = ops_db.get_metric_snapshots(hours=24)
        assert snaps == []

    def test_get_metric_snapshots(self, ops_db):
        s1 = self._sample_snapshot()
        s2 = self._sample_snapshot()
        s2["overall_health_pct"] = 95.0
        ops_db.save_metric_snapshot(s1)
        ops_db.save_metric_snapshot(s2)
        snaps = ops_db.get_metric_snapshots(hours=24)
        assert len(snaps) == 2

    def test_get_metric_snapshots_returns_parsed_json(self, ops_db):
        ops_db.save_metric_snapshot(self._sample_snapshot())
        snaps = ops_db.get_metric_snapshots(hours=24)
        assert isinstance(snaps[0]["snapshot"], dict)
        assert snaps[0]["snapshot"]["overall_health_pct"] == 100.0

    def test_get_metric_snapshots_ordered_desc(self, ops_db):
        """Most recent snapshot first."""
        s1 = self._sample_snapshot()
        s1["overall_health_pct"] = 80.0
        s2 = self._sample_snapshot()
        s2["overall_health_pct"] = 90.0
        ops_db.save_metric_snapshot(s1)
        ops_db.save_metric_snapshot(s2)
        snaps = ops_db.get_metric_snapshots(hours=24)
        # Last inserted = most recent = first in result
        assert snaps[0]["snapshot"]["overall_health_pct"] == 90.0

    def test_get_metric_snapshots_limit(self, ops_db):
        for i in range(10):
            s = self._sample_snapshot()
            s["overall_health_pct"] = float(i)
            ops_db.save_metric_snapshot(s)
        snaps = ops_db.get_metric_snapshots(hours=24, limit=5)
        assert len(snaps) == 5

    def test_purge_old_snapshots(self, ops_db):
        """purge_old_snapshots should delete rows older than retention days."""
        ops_db.save_metric_snapshot(self._sample_snapshot())
        # Insert an old snapshot directly
        with ops_db.transaction() as conn:
            conn.execute(
                "INSERT INTO metric_snapshots (timestamp, snapshot_json) "
                "VALUES (datetime('now', '-60 days'), ?)",
                (json.dumps(self._sample_snapshot()),),
            )
        # Before purge: 2 rows
        assert len(ops_db.get_metric_snapshots(hours=24 * 365)) == 2
        deleted = ops_db.purge_old_snapshots(retention_days=30)
        assert deleted == 1
        # After purge: 1 row
        assert len(ops_db.get_metric_snapshots(hours=24 * 365)) == 1

    def test_purge_old_snapshots_nothing_to_purge(self, ops_db):
        ops_db.save_metric_snapshot(self._sample_snapshot())
        deleted = ops_db.purge_old_snapshots(retention_days=30)
        assert deleted == 0


# ── Alert Evaluations ────────────────────────────────────────────────────

class TestAlertEvaluations:
    def test_record_alert_evaluation(self, ops_db):
        eval_id = ops_db.record_alert_evaluation(
            rule_name="cost_spike",
            fingerprint="cost_spike:warning:cost",
            severity="warning",
            message="Daily cost exceeds $5",
        )
        assert isinstance(eval_id, int)
        assert eval_id > 0

    def test_record_alert_evaluation_with_snapshot_id(self, ops_db):
        snap_id = ops_db.save_metric_snapshot({"test": True})
        eval_id = ops_db.record_alert_evaluation(
            rule_name="cost_spike",
            fingerprint="cost_spike:warning:cost",
            severity="warning",
            message="test",
            snapshot_id=snap_id,
        )
        evals = ops_db.get_alert_evaluations(rule_name="cost_spike")
        assert len(evals) == 1
        assert evals[0]["snapshot_id"] == snap_id

    def test_get_alert_evaluations_empty(self, ops_db):
        evals = ops_db.get_alert_evaluations(rule_name="nonexistent")
        assert evals == []

    def test_get_alert_evaluations_by_rule(self, ops_db):
        ops_db.record_alert_evaluation(
            rule_name="rule_a", fingerprint="a:w:g",
            severity="warning", message="msg_a",
        )
        ops_db.record_alert_evaluation(
            rule_name="rule_b", fingerprint="b:w:g",
            severity="warning", message="msg_b",
        )
        evals = ops_db.get_alert_evaluations(rule_name="rule_a")
        assert len(evals) == 1
        assert evals[0]["rule_name"] == "rule_a"

    def test_get_alert_evaluations_ordered_desc(self, ops_db):
        ops_db.record_alert_evaluation(
            rule_name="r", fingerprint="r:w:g",
            severity="warning", message="first",
        )
        ops_db.record_alert_evaluation(
            rule_name="r", fingerprint="r:w:g",
            severity="warning", message="second",
        )
        evals = ops_db.get_alert_evaluations(rule_name="r")
        assert evals[0]["message"] == "second"  # most recent first

    def test_get_alert_evaluations_limit(self, ops_db):
        for i in range(10):
            ops_db.record_alert_evaluation(
                rule_name="r", fingerprint="r:w:g",
                severity="warning", message=f"msg_{i}",
            )
        evals = ops_db.get_alert_evaluations(rule_name="r", limit=5)
        assert len(evals) == 5

    def test_resolve_alert_evaluation(self, ops_db):
        eval_id = ops_db.record_alert_evaluation(
            rule_name="r", fingerprint="r:w:g",
            severity="warning", message="test",
        )
        resolved = ops_db.resolve_alert_evaluation(eval_id)
        assert resolved is True
        evals = ops_db.get_alert_evaluations(rule_name="r")
        assert evals[0]["resolved_at"] is not None

    def test_resolve_alert_evaluation_not_found(self, ops_db):
        result = ops_db.resolve_alert_evaluation(9999)
        assert result is False

    def test_get_alert_evaluations_all_rules(self, ops_db):
        """get_alert_evaluations without rule_name returns all."""
        ops_db.record_alert_evaluation(
            rule_name="a", fingerprint="a:w:g",
            severity="warning", message="ma",
        )
        ops_db.record_alert_evaluation(
            rule_name="b", fingerprint="b:w:g",
            severity="critical", message="mb",
        )
        evals = ops_db.get_alert_evaluations()
        assert len(evals) == 2

    def test_get_unresolved_evaluations(self, ops_db):
        e1 = ops_db.record_alert_evaluation(
            rule_name="r", fingerprint="r:w:g",
            severity="warning", message="open",
        )
        ops_db.record_alert_evaluation(
            rule_name="r", fingerprint="r:w:g",
            severity="warning", message="also_open",
        )
        ops_db.resolve_alert_evaluation(e1)
        evals = ops_db.get_alert_evaluations(rule_name="r", unresolved_only=True)
        assert len(evals) == 1
        assert evals[0]["message"] == "also_open"
