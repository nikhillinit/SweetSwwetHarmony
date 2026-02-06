"""Phase 5.4 — CLI tests for monitor rules & snapshots subcommands."""

import json
import subprocess
import sys
import pytest

from ops.storage import OpsStorage


@pytest.fixture
def ops_db(tmp_path):
    db_path = tmp_path / "test_rules_cli.db"
    storage = OpsStorage(str(db_path))
    yield str(db_path), storage
    del storage


def _run_cli(db_path: str, *args) -> subprocess.CompletedProcess:
    """Run ops CLI as subprocess."""
    cmd = [sys.executable, "-m", "ops.cli", "--db", db_path] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


# ── monitor rules list ────────────────────────────────────────────────


class TestMonitorRulesList:
    def test_rules_list_empty(self, ops_db):
        """rules list with no custom rules shows header."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "rules", "list")
        assert result.returncode == 0
        assert "ALERT RULES" in result.stdout

    def test_rules_list_shows_builtins(self, ops_db):
        """rules list includes builtin rules."""
        db_path, storage = ops_db
        # Insert a builtin rule
        storage.create_alert_rule(
            name="builtin_test",
            condition={"field": "open_incidents", "op": ">", "value": 5},
            severity="warning",
            message_template="Test builtin",
            is_builtin=True,
        )
        result = _run_cli(db_path, "monitor", "rules", "list")
        assert result.returncode == 0
        assert "builtin_test" in result.stdout

    def test_rules_list_with_custom(self, ops_db):
        """rules list shows custom rules."""
        db_path, storage = ops_db
        storage.create_alert_rule(
            name="cost_high",
            condition={"field": "total_cost_24h", "op": ">", "value": 10},
            severity="critical",
            message_template="Cost too high",
        )
        result = _run_cli(db_path, "monitor", "rules", "list")
        assert result.returncode == 0
        assert "cost_high" in result.stdout
        assert "critical" in result.stdout.lower()

    def test_rules_list_ascii_safe(self, ops_db):
        """Output should be ASCII-safe."""
        db_path, storage = ops_db
        storage.create_alert_rule(
            name="test_rule",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info",
            message_template="Test",
        )
        result = _run_cli(db_path, "monitor", "rules", "list")
        assert result.returncode == 0
        for char in result.stdout:
            assert ord(char) < 128, f"Non-ASCII character found: {repr(char)}"


# ── monitor rules add ─────────────────────────────────────────────────


class TestMonitorRulesAdd:
    def test_rules_add_simple(self, ops_db):
        """Add a simple rule with required args."""
        db_path, storage = ops_db
        condition = json.dumps({"field": "total_cost_24h", "op": ">", "value": 5})
        result = _run_cli(
            db_path, "monitor", "rules", "add",
            "--name", "cost_alert",
            "--condition", condition,
            "--severity", "warning",
            "--message", "Cost exceeded $5",
        )
        assert result.returncode == 0
        assert "created" in result.stdout.lower() or "cost_alert" in result.stdout

        # Verify it's in the DB
        rules = storage.list_alert_rules()
        assert any(r["name"] == "cost_alert" for r in rules)

    def test_rules_add_with_component(self, ops_db):
        """Add a rule with --component flag."""
        db_path, storage = ops_db
        condition = json.dumps({"field": "open_incidents", "op": ">", "value": 3})
        result = _run_cli(
            db_path, "monitor", "rules", "add",
            "--name", "incidents_high",
            "--condition", condition,
            "--severity", "critical",
            "--message", "Too many incidents",
            "--component", "incidents",
        )
        assert result.returncode == 0
        rules = storage.list_alert_rules()
        rule = next(r for r in rules if r["name"] == "incidents_high")
        assert rule["component"] == "incidents"

    def test_rules_add_invalid_json(self, ops_db):
        """Invalid JSON condition should fail."""
        db_path, _ = ops_db
        result = _run_cli(
            db_path, "monitor", "rules", "add",
            "--name", "bad_rule",
            "--condition", "not valid json{",
            "--severity", "warning",
            "--message", "Bad",
        )
        assert result.returncode != 0

    def test_rules_add_composite(self, ops_db):
        """Add a composite (all/any) rule."""
        db_path, storage = ops_db
        condition = json.dumps({
            "all": [
                {"field": "total_cost_24h", "op": ">", "value": 1},
                {"field": "open_incidents", "op": ">", "value": 0},
            ]
        })
        result = _run_cli(
            db_path, "monitor", "rules", "add",
            "--name", "composite_test",
            "--condition", condition,
            "--severity", "warning",
            "--message", "Cost and incidents",
        )
        assert result.returncode == 0
        rules = storage.list_alert_rules()
        assert any(r["name"] == "composite_test" for r in rules)


# ── monitor rules enable / disable ────────────────────────────────────


class TestMonitorRulesEnableDisable:
    def test_rules_disable(self, ops_db):
        """Disable an enabled rule."""
        db_path, storage = ops_db
        rid = storage.create_alert_rule(
            name="to_disable",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info",
            message_template="Test",
        )
        result = _run_cli(db_path, "monitor", "rules", "disable", str(rid))
        assert result.returncode == 0
        assert "disabled" in result.stdout.lower()

        rule = storage.get_alert_rule(rid)
        assert rule["enabled"] == 0

    def test_rules_enable(self, ops_db):
        """Enable a disabled rule."""
        db_path, storage = ops_db
        rid = storage.create_alert_rule(
            name="to_enable",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info",
            message_template="Test",
            enabled=False,
        )
        result = _run_cli(db_path, "monitor", "rules", "enable", str(rid))
        assert result.returncode == 0
        assert "enabled" in result.stdout.lower()

        rule = storage.get_alert_rule(rid)
        assert rule["enabled"] == 1

    def test_rules_enable_not_found(self, ops_db):
        """Enable a non-existent rule should error."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "rules", "enable", "9999")
        assert result.returncode != 0

    def test_rules_disable_not_found(self, ops_db):
        """Disable a non-existent rule should error."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "rules", "disable", "9999")
        assert result.returncode != 0


# ── monitor rules delete ──────────────────────────────────────────────


class TestMonitorRulesDelete:
    def test_rules_delete_custom(self, ops_db):
        """Delete a custom rule."""
        db_path, storage = ops_db
        rid = storage.create_alert_rule(
            name="to_delete",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info",
            message_template="Test",
        )
        result = _run_cli(db_path, "monitor", "rules", "delete", str(rid))
        assert result.returncode == 0
        assert "deleted" in result.stdout.lower()

        rule = storage.get_alert_rule(rid)
        assert rule is None

    def test_rules_delete_not_found(self, ops_db):
        """Delete a non-existent rule should error."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "rules", "delete", "9999")
        assert result.returncode != 0

    def test_rules_delete_builtin_blocked(self, ops_db):
        """Cannot delete a builtin rule."""
        db_path, storage = ops_db
        rid = storage.create_alert_rule(
            name="builtin_keep",
            condition={"field": "open_incidents", "op": ">", "value": 0},
            severity="info",
            message_template="Builtin",
            is_builtin=True,
        )
        result = _run_cli(db_path, "monitor", "rules", "delete", str(rid))
        assert result.returncode != 0
        # Rule should still exist
        rule = storage.get_alert_rule(rid)
        assert rule is not None


# ── monitor rules test ────────────────────────────────────────────────


class TestMonitorRulesTest:
    def test_rules_test_fires(self, ops_db):
        """Test a rule that fires against current snapshot."""
        db_path, storage = ops_db
        # Create a rule that checks open_incidents > 0
        # On a clean DB open_incidents is 0, so this won't fire
        # Instead check total_facts == 0 (always true on clean DB)
        rid = storage.create_alert_rule(
            name="always_fires",
            condition={"field": "total_facts", "op": "==", "value": 0},
            severity="warning",
            message_template="No facts",
        )
        result = _run_cli(db_path, "monitor", "rules", "test", str(rid))
        assert result.returncode == 0
        assert "FIRED" in result.stdout or "fired" in result.stdout.lower()

    def test_rules_test_passes(self, ops_db):
        """Test a rule that does not fire."""
        db_path, storage = ops_db
        rid = storage.create_alert_rule(
            name="never_fires",
            condition={"field": "total_facts", "op": ">", "value": 999999},
            severity="info",
            message_template="Lots of facts",
        )
        result = _run_cli(db_path, "monitor", "rules", "test", str(rid))
        assert result.returncode == 0
        assert "OK" in result.stdout or "pass" in result.stdout.lower() or "not fire" in result.stdout.lower()

    def test_rules_test_not_found(self, ops_db):
        """Test a non-existent rule should error."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "rules", "test", "9999")
        assert result.returncode != 0


# ── monitor snapshots ─────────────────────────────────────────────────


class TestMonitorSnapshots:
    def test_snapshots_empty(self, ops_db):
        """No snapshots → clean output."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "snapshots")
        assert result.returncode == 0
        assert "METRIC SNAPSHOTS" in result.stdout or "No snapshots" in result.stdout

    def test_snapshots_with_data(self, ops_db):
        """Shows snapshot summary when data exists."""
        db_path, storage = ops_db
        storage.save_metric_snapshot({
            "overall_health_pct": 95.0,
            "total_cost_24h": "1.50",
            "extractions_24h": 3,
            "open_incidents": 0,
        })
        result = _run_cli(db_path, "monitor", "snapshots")
        assert result.returncode == 0
        assert "95" in result.stdout or "1.50" in result.stdout

    def test_snapshots_hours_flag(self, ops_db):
        """--hours flag is accepted."""
        db_path, _ = ops_db
        result = _run_cli(db_path, "monitor", "snapshots", "--hours", "48")
        assert result.returncode == 0

    def test_snapshots_ascii_safe(self, ops_db):
        """Output should be ASCII-safe."""
        db_path, storage = ops_db
        storage.save_metric_snapshot({"overall_health_pct": 85.0})
        result = _run_cli(db_path, "monitor", "snapshots")
        assert result.returncode == 0
        for char in result.stdout:
            assert ord(char) < 128, f"Non-ASCII character found: {repr(char)}"
