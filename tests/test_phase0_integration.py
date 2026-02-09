"""Phase 0 integration tests -- cross-component verification.

Tests that delivery policy, triage CLI, CSV export, config validation,
and audit log all work TOGETHER as an integrated system.

Unit tests for each component already exist in:
  - tests/test_export_queue.py
  - tests/test_triage_cli.py

These integration tests verify cross-component interactions.
"""
import asyncio
import csv
import json
import os
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def tmp_db(tmp_path):
    """Return path to a temporary database file."""
    return str(tmp_path / "test_signals.db")


async def _setup_db(db_path: str):
    """Create tables and insert test signals into a temporary database.

    Sets up:
        - signals table with 5 test signals (various types/confidences)
        - signal_processing table (signals 1-3 have rows; 4-5 do not)
        - audit_log table (empty initially)
    """
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA foreign_keys = OFF")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT,
            company_name TEXT,
            confidence REAL DEFAULT 0.5,
            raw_data TEXT DEFAULT '{}',
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            company_id TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS signal_processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            notion_page_id TEXT,
            processed_at TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)

    now = datetime.now(timezone.utc)
    signals = [
        (
            "github_trending", "github", "domain:acme.ai", "Acme AI", 0.75,
            json.dumps({"description": "AI-powered meal kit delivery"}),
            (now - timedelta(days=5)).isoformat(),
        ),
        (
            "sec_filing", "sec_edgar", "domain:healthco.com", "HealthCo", 0.60,
            json.dumps({"description": "Digital health platform for wellness"}),
            (now - timedelta(days=10)).isoformat(),
        ),
        (
            "job_posting", "greenhouse", "domain:travelx.io", "TravelX", 0.45,
            json.dumps({"title": "Travel marketplace startup"}),
            (now - timedelta(days=20)).isoformat(),
        ),
        (
            "product_launch", "product_hunt", "domain:snackbox.com", "SnackBox", 0.85,
            json.dumps({"description": "Subscription snack box for health-conscious consumers"}),
            (now - timedelta(days=3)).isoformat(),
        ),
        (
            "hacker_news", "hacker_news", "domain:devtool.dev", "DevTool", 0.30,
            json.dumps({"description": "B2B developer productivity tool"}),
            (now - timedelta(days=2)).isoformat(),
        ),
    ]

    for sig in signals:
        await db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at)
               VALUES (?,?,?,?,?,?,?)""",
            sig,
        )

    # Signals 1-3 have processing rows; signals 4-5 do not
    processing = [
        (1, "pending"),
        (2, "pending"),
        (3, "pushed"),
    ]
    for signal_id, status in processing:
        await db.execute(
            "INSERT INTO signal_processing (signal_id, status) VALUES (?,?)",
            (signal_id, status),
        )

    await db.commit()
    await db.close()


def _make_mock_store(real_db):
    """Create a mock SignalStore that uses a real aiosqlite connection.

    The mock skips migrations on initialize() but delegates all _db
    operations to the real aiosqlite connection for actual SQL execution.
    """
    mock_store = MagicMock()
    mock_store._db = real_db
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()
    return mock_store


# ============================================================================
# SCENARIO 1: Full Delivery Policy Lifecycle
# ============================================================================


class TestDeliveryPolicyLifecycle:
    """Verify that delivery modes correctly block/allow Notion writes
    across the full system -- not just the guard function in isolation."""

    def test_staging_only_blocks_all_intents(self, monkeypatch):
        """DELIVERY_MODE=staging_only should block manual, batch, and auto pushes."""
        from workflows.delivery_policy import (
            assert_notion_write_allowed,
            DeliveryIntent,
            DeliveryPolicyError,
        )

        monkeypatch.setenv("DELIVERY_MODE", "staging_only")

        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)

    def test_manual_publish_allows_manual_only(self, monkeypatch):
        """DELIVERY_MODE=manual_publish should allow manual push, block auto/batch."""
        from workflows.delivery_policy import (
            assert_notion_write_allowed,
            DeliveryIntent,
            DeliveryPolicyError,
        )

        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")

        # Manual push should succeed (no exception)
        assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)

        # Auto and batch should be blocked
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)
        with pytest.raises(DeliveryPolicyError):
            assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)

    def test_auto_publish_allows_all(self, monkeypatch):
        """DELIVERY_MODE=auto_publish should allow all intent types."""
        from workflows.delivery_policy import (
            assert_notion_write_allowed,
            DeliveryIntent,
        )

        monkeypatch.setenv("DELIVERY_MODE", "auto_publish")

        # All should succeed (no exception)
        assert_notion_write_allowed(DeliveryIntent.AUTO_PUSH)
        assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)
        assert_notion_write_allowed(DeliveryIntent.BATCH_PUSH)

    def test_push_command_checks_delivery_policy(self, monkeypatch, capsys):
        """cmd_push should fail early if DELIVERY_MODE=staging_only."""
        from workflows.delivery_policy import DeliveryPolicyError

        monkeypatch.setenv("DELIVERY_MODE", "staging_only")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1,2",
            dry_run=False,
            db_path="dummy.db",
        )

        with pytest.raises(SystemExit) as exc_info:
            asyncio.get_event_loop().run_until_complete(cmd_push(args))

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "blocked" in captured.out.lower() or "ERROR" in captured.out

    def test_push_command_dry_run_skips_policy_check(self, monkeypatch, tmp_db):
        """cmd_push --dry-run should NOT check delivery policy.

        Even staging_only should allow dry-run so operators can preview.
        """
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")

        async def _run():
            await _setup_db(tmp_db)
            from run_pipeline import cmd_push

            args = Namespace(
                signal_ids="1",
                dry_run=True,
                db_path=tmp_db,
            )

            real_db = await aiosqlite.connect(tmp_db)
            mock_store = _make_mock_store(real_db)

            # get_signal needs to return something
            async def fake_get_signal(sid):
                cursor = await real_db.execute(
                    "SELECT id, signal_type, source_api, canonical_key, company_name, "
                    "confidence, raw_data, detected_at FROM signals WHERE id = ?",
                    (sid,),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                mock_sig = MagicMock()
                mock_sig.id = row[0]
                mock_sig.signal_type = row[1]
                mock_sig.source_api = row[2]
                mock_sig.canonical_key = row[3]
                mock_sig.company_name = row[4]
                mock_sig.confidence = row[5]
                mock_sig.raw_data = json.loads(row[6]) if row[6] else {}
                mock_sig.detected_at = row[7]
                return mock_sig

            mock_store.get_signal = AsyncMock(side_effect=fake_get_signal)

            with patch("run_pipeline.SignalStore", return_value=mock_store):
                # Should NOT raise DeliveryPolicyError or SystemExit
                await cmd_push(args)

            await real_db.close()

        asyncio.get_event_loop().run_until_complete(_run())

    def test_config_validator_and_delivery_mode_agree(self, monkeypatch):
        """Config validator should flag the same invalid values that delivery_policy
        falls back on. Verifies the two components share the same validity rules."""
        from utils.config_validator import validate_config, VALID_DELIVERY_MODES
        from workflows.delivery_policy import DeliveryMode

        # All valid DeliveryMode values should be in the validator's set
        for mode in DeliveryMode:
            assert mode.value in VALID_DELIVERY_MODES, (
                f"DeliveryMode.{mode.name} ({mode.value}) not in config_validator's "
                f"VALID_DELIVERY_MODES"
            )

        # And vice versa
        for mode_str in VALID_DELIVERY_MODES:
            try:
                DeliveryMode(mode_str)
            except ValueError:
                pytest.fail(
                    f"VALID_DELIVERY_MODES contains '{mode_str}' which is not a "
                    f"valid DeliveryMode enum value"
                )


# ============================================================================
# SCENARIO 2: Triage Workflow End-to-End
# ============================================================================


class TestTriageWorkflowE2E:
    """Full triage workflow: list signals, approve/reject/defer, verify
    audit_log entries and signal_processing status updates."""

    @pytest.mark.asyncio
    async def test_full_triage_workflow(self, tmp_db, capsys):
        """Approve signal 1, reject signal 2, defer signal 4.
        Verify audit_log has 3 entries and statuses are correct."""
        await _setup_db(tmp_db)

        from run_pipeline import (
            cmd_triage_list,
            cmd_triage_approve,
            cmd_triage_reject,
            cmd_triage_defer,
        )

        # -- Step 1: List pending signals (verify we see them) --
        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(
                Namespace(
                    db_path=tmp_db, status="pending", min_confidence=None,
                    limit=20, compact=True, verbose=False,
                )
            )

        captured_list = capsys.readouterr()
        assert "Acme AI" in captured_list.out
        assert "HealthCo" in captured_list.out

        # -- Step 2: Approve signal 1 --
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Strong consumer CPG fit")
            )

        # -- Step 3: Reject signal 2 --
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(
                Namespace(db_path=tmp_db, signal_id=2, reason="Not consumer-focused enough")
            )

        # -- Step 4: Defer signal 4 (SnackBox, no processing row yet) --
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_defer(
                Namespace(db_path=tmp_db, signal_id=4, reason="Awaiting more signals")
            )

        # -- Verify audit_log has exactly 3 entries --
        cursor = await real_db.execute(
            "SELECT action_type, entity_id, details FROM audit_log ORDER BY id"
        )
        audit_rows = await cursor.fetchall()
        assert len(audit_rows) == 3

        assert audit_rows[0][0] == "triage_approve"
        assert audit_rows[0][1] == "1"
        assert "Strong consumer CPG fit" in audit_rows[0][2]

        assert audit_rows[1][0] == "triage_reject"
        assert audit_rows[1][1] == "2"
        assert "Not consumer-focused enough" in audit_rows[1][2]

        assert audit_rows[2][0] == "triage_defer"
        assert audit_rows[2][1] == "4"
        assert "Awaiting more signals" in audit_rows[2][2]

        # -- Verify signal_processing statuses --
        # Signal 1: was pending -> now queued
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == "queued"

        # Signal 2: was pending -> now rejected
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 2"
        )
        row = await cursor.fetchone()
        assert row[0] == "rejected"

        # Signal 4: defer does NOT create/update signal_processing
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 4"
        )
        row = await cursor.fetchone()
        # Signal 4 had no processing row and defer should not create one
        assert row is None

        await real_db.close()

    @pytest.mark.asyncio
    async def test_triage_then_list_reflects_changes(self, tmp_db, capsys):
        """After approve/reject, triage list should reflect updated statuses."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve, cmd_triage_reject, cmd_triage_list

        real_db = await aiosqlite.connect(tmp_db)

        # Approve signal 1, reject signal 2
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Good fit")
            )

        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(
                Namespace(db_path=tmp_db, signal_id=2, reason="B2B focused")
            )

        # Clear captured output from the approve/reject calls
        capsys.readouterr()

        # List pending -- signals 1 and 2 should no longer appear
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(
                Namespace(
                    db_path=tmp_db, status="pending", min_confidence=None,
                    limit=20, compact=True, verbose=False,
                )
            )

        captured = capsys.readouterr()
        # Acme AI (signal 1) is now queued, should not appear in pending
        assert "Acme AI" not in captured.out
        # HealthCo (signal 2) is now rejected, should not appear in pending
        assert "HealthCo" not in captured.out
        # SnackBox and DevTool should still be pending
        assert "SnackBox" in captured.out
        assert "DevTool" in captured.out

        await real_db.close()


# ============================================================================
# SCENARIO 3: CSV Export Covers All Signals
# ============================================================================


class TestCSVExportIntegration:
    """CSV export should produce complete queue snapshots consistent
    with the signals in the database and their current statuses."""

    @pytest.mark.asyncio
    async def test_export_reflects_triage_status_changes(self, tmp_db, tmp_path):
        """After triage actions, CSV export should show updated statuses."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve, cmd_triage_reject, cmd_export_queue

        real_db = await aiosqlite.connect(tmp_db)

        # Approve signal 1, reject signal 5
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Consumer fit")
            )

        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(
                Namespace(db_path=tmp_db, signal_id=5, reason="B2B dev tool")
            )

        # Export all signals to CSV
        out_file = str(tmp_path / "queue_after_triage.csv")
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(
                Namespace(
                    db_path=tmp_db, out=out_file, status=None,
                    min_confidence=None, days=None, format="csv",
                )
            )

        await real_db.close()

        # Read and verify CSV
        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 5  # All 5 signals present

        # Build a lookup by signal_id
        by_id = {row["signal_id"]: row for row in rows}
        assert by_id["1"]["status"] == "queued"       # approved
        assert by_id["2"]["status"] == "pending"       # untouched
        assert by_id["3"]["status"] == "pushed"        # from setup
        assert by_id["5"]["status"] == "rejected"      # rejected

    @pytest.mark.asyncio
    async def test_export_has_correct_columns(self, tmp_db, tmp_path):
        """CSV export should have the expected header columns."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_export_queue

        out_file = str(tmp_path / "columns_check.csv")
        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(
                Namespace(
                    db_path=tmp_db, out=out_file, status=None,
                    min_confidence=None, days=None, format="csv",
                )
            )

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)

        expected = [
            "signal_id", "company_name", "canonical_key", "confidence",
            "signal_type", "source_api", "detected_at", "status", "company_id",
        ]
        assert header == expected

    @pytest.mark.asyncio
    async def test_export_pending_only_after_triage(self, tmp_db, tmp_path):
        """Export with --status=pending should exclude approved/rejected signals."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve, cmd_triage_reject, cmd_export_queue

        real_db = await aiosqlite.connect(tmp_db)

        # Approve signal 1
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Good")
            )

        # Reject signal 2
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(
                Namespace(db_path=tmp_db, signal_id=2, reason="Bad")
            )

        # Export pending only
        out_file = str(tmp_path / "pending_only.csv")
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(
                Namespace(
                    db_path=tmp_db, out=out_file, status="pending",
                    min_confidence=None, days=None, format="csv",
                )
            )

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Only SnackBox (4) and DevTool (5) should remain pending
        # (Signal 3 is pushed, signal 1 is queued, signal 2 is rejected)
        assert len(rows) == 2
        names = {row["company_name"] for row in rows}
        assert names == {"SnackBox", "DevTool"}


# ============================================================================
# SCENARIO 4: Config Validation Reports
# ============================================================================


class TestConfigValidationIntegration:
    """Config validation should correctly detect invalid/valid/missing values
    and produce consistent reports."""

    def test_invalid_delivery_mode_returns_error(self, monkeypatch):
        """Invalid DELIVERY_MODE should produce an error-level ConfigIssue."""
        monkeypatch.setenv("DELIVERY_MODE", "yolo_mode")
        # Clear optional keys to reduce noise
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)

        from utils.config_validator import validate_config

        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]

        assert len(delivery_issues) == 1
        assert delivery_issues[0].level == "error"
        assert "yolo_mode" in delivery_issues[0].message

    def test_valid_delivery_mode_returns_info(self, monkeypatch):
        """Valid DELIVERY_MODE should produce an info-level ConfigIssue."""
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")

        from utils.config_validator import validate_config

        issues = validate_config()
        delivery_issues = [i for i in issues if i.key == "DELIVERY_MODE"]

        assert len(delivery_issues) == 1
        assert delivery_issues[0].level == "info"
        assert "manual_publish" in delivery_issues[0].message

    def test_missing_notion_keys_returns_warnings(self, monkeypatch):
        """Missing NOTION_API_KEY and NOTION_DATABASE_ID should produce warnings."""
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)

        from utils.config_validator import validate_config

        issues = validate_config()
        notion_issues = [
            i for i in issues
            if i.key in ("NOTION_API_KEY", "NOTION_DATABASE_ID")
        ]

        assert len(notion_issues) == 2
        assert all(i.level == "warning" for i in notion_issues)

    def test_print_config_report_returns_true_on_errors(self, monkeypatch, capsys):
        """print_config_report should return True when there are errors."""
        monkeypatch.setenv("DELIVERY_MODE", "invalid_mode")
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)

        from utils.config_validator import validate_config, print_config_report

        issues = validate_config()
        has_errors = print_config_report(issues)

        assert has_errors is True

        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out

    def test_print_config_report_returns_false_when_clean(self, monkeypatch, capsys):
        """print_config_report should return False when no errors."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")
        monkeypatch.setenv("NOTION_API_KEY", "secret_test_key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test_db_id")
        # Clear any threshold env vars that might cause errors
        for key in (
            "MATCHING_HIGH_CONFIDENCE", "MATCHING_MEDIUM_CONFIDENCE",
            "MATCHING_IS_FIT_THRESHOLD", "MATCHING_QUALIFIED_THRESHOLD",
            "MATCHING_HELD_THRESHOLD", "MATCHING_THESIS_THRESHOLD",
            "WORKFLOW_HOLD_THRESHOLD", "WORKFLOW_SKIP_LLM_THRESHOLD",
            "WORKFLOW_KEYWORD_HIGH", "WORKFLOW_KEYWORD_LOW",
            "WORKFLOW_LLM_REVIEW_THRESHOLD", "WORKFLOW_LLM_AUTO_APPROVE_THRESHOLD",
        ):
            monkeypatch.delenv(key, raising=False)

        from utils.config_validator import validate_config, print_config_report

        issues = validate_config()
        has_errors = print_config_report(issues)

        assert has_errors is False

        captured = capsys.readouterr()
        assert "All checks passed" in captured.out

    def test_invalid_threshold_detected(self, monkeypatch):
        """Out-of-range threshold should produce an error."""
        monkeypatch.setenv("MATCHING_HIGH_CONFIDENCE", "1.5")  # Out of [0, 1]

        from utils.config_validator import validate_config

        issues = validate_config()
        threshold_errors = [
            i for i in issues
            if i.key == "MATCHING_HIGH_CONFIDENCE" and i.level == "error"
        ]

        assert len(threshold_errors) == 1
        assert "out of range" in threshold_errors[0].message


# ============================================================================
# SCENARIO 5: Audit Trail Integrity
# ============================================================================


class TestAuditTrailIntegrity:
    """Verify that the audit trail correctly records all triage decisions,
    including multiple actions on the same signal."""

    @pytest.mark.asyncio
    async def test_multiple_actions_same_signal_all_recorded(self, tmp_db):
        """Approve then reject the same signal -- both audit entries should exist."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve, cmd_triage_reject

        real_db = await aiosqlite.connect(tmp_db)

        # First: approve signal 1
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Looks like consumer CPG")
            )

        # Then: reject the same signal (operator changed mind)
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(
                Namespace(db_path=tmp_db, signal_id=1, reason="Actually it is B2B SaaS")
            )

        # Verify audit_log has BOTH entries in chronological order
        cursor = await real_db.execute(
            "SELECT action_type, entity_id, details, created_at "
            "FROM audit_log WHERE entity_id = '1' ORDER BY id"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2

        assert rows[0][0] == "triage_approve"
        assert "consumer CPG" in rows[0][2]

        assert rows[1][0] == "triage_reject"
        assert "B2B SaaS" in rows[1][2]

        # Timestamps should be in order (second >= first)
        assert rows[1][3] >= rows[0][3]

        # Signal_processing should reflect the LATEST action (rejected)
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == "rejected"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_defer_then_approve_preserves_both_entries(self, tmp_db):
        """Defer then approve -- both should be in audit_log, status = queued."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_defer, cmd_triage_approve

        real_db = await aiosqlite.connect(tmp_db)

        # Defer first
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_defer(
                Namespace(db_path=tmp_db, signal_id=2, reason="Need more data")
            )

        # Then approve
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=2, reason="Got confirmation from founder")
            )

        # Check audit_log
        cursor = await real_db.execute(
            "SELECT action_type FROM audit_log WHERE entity_id = '2' ORDER BY id"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "triage_defer"
        assert rows[1][0] == "triage_approve"

        # Status should be queued (from the approve)
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 2"
        )
        row = await cursor.fetchone()
        assert row[0] == "queued"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_audit_log_details_contain_valid_json(self, tmp_db):
        """Every audit_log details field should be valid JSON with a 'reason' key."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve, cmd_triage_reject, cmd_triage_defer

        real_db = await aiosqlite.connect(tmp_db)

        actions = [
            (cmd_triage_approve, 1, "Consumer CPG brand"),
            (cmd_triage_reject, 5, "B2B developer tool"),
            (cmd_triage_defer, 2, "Need founder background check"),
        ]

        for cmd, signal_id, reason in actions:
            mock_store = _make_mock_store(real_db)
            with patch("run_pipeline.SignalStore", return_value=mock_store):
                await cmd(Namespace(db_path=tmp_db, signal_id=signal_id, reason=reason))

        # Verify all audit_log entries have valid JSON in details
        cursor = await real_db.execute("SELECT details FROM audit_log")
        rows = await cursor.fetchall()
        assert len(rows) == 3

        for row in rows:
            details = json.loads(row[0])  # Should not raise
            assert "reason" in details
            assert isinstance(details["reason"], str)
            assert len(details["reason"]) > 0

        await real_db.close()

    @pytest.mark.asyncio
    async def test_audit_log_actor_is_operator(self, tmp_db):
        """All triage audit_log entries should have actor='operator'."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Test")
            )

        cursor = await real_db.execute(
            "SELECT actor FROM audit_log WHERE entity_id = '1'"
        )
        row = await cursor.fetchone()
        assert row[0] == "operator"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_audit_log_timestamps_are_iso8601(self, tmp_db):
        """Audit log created_at should be parseable ISO 8601 timestamps."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Test")
            )

        cursor = await real_db.execute("SELECT created_at FROM audit_log")
        row = await cursor.fetchone()

        # Should parse without error
        ts = datetime.fromisoformat(row[0])
        assert ts.year >= 2025  # Sanity check

        await real_db.close()
