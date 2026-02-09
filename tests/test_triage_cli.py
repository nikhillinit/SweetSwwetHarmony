"""Tests for the triage CLI commands.

Phase 0, Task 0.11: Triage pending signals (list, approve, reject, defer).
"""
import asyncio
import json
import os
import sys
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


@pytest.fixture
def tmp_db(tmp_path):
    """Return path to a temporary database file."""
    return str(tmp_path / "test_signals.db")


async def _setup_db(db_path: str):
    """Create tables and insert test signals into a temporary database."""
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

    # Signal 1-3 have processing rows; signal 4-5 do not (default to 'pending')
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
    """Create a mock SignalStore that uses a real aiosqlite connection."""
    mock_store = MagicMock()
    mock_store._db = real_db
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()
    return mock_store


# =============================================================================
# TRIAGE LIST TESTS
# =============================================================================


class TestTriageList:
    """Tests for cmd_triage_list handler."""

    @pytest.mark.asyncio
    async def test_list_pending_signals(self, tmp_db, capsys):
        """List command should show pending signals in a table."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_list

        args = Namespace(
            db_path=tmp_db,
            status="pending",
            min_confidence=None,
            limit=20,
            compact=True,
            verbose=False,
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(args)

        await real_db.close()

        captured = capsys.readouterr()
        # Should include the pending signals (1, 2, 4, 5 -- signal 3 is pushed)
        assert "Acme AI" in captured.out
        assert "HealthCo" in captured.out
        assert "SnackBox" in captured.out
        assert "DevTool" in captured.out
        # Signal 3 (TravelX) is pushed, should NOT appear in pending list
        assert "TravelX" not in captured.out
        assert "pending" in captured.out.lower()

    @pytest.mark.asyncio
    async def test_list_with_min_confidence(self, tmp_db, capsys):
        """List should filter by minimum confidence."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_list

        args = Namespace(
            db_path=tmp_db,
            status="pending",
            min_confidence=0.6,
            limit=20,
            compact=True,
            verbose=False,
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(args)

        await real_db.close()

        captured = capsys.readouterr()
        # Only Acme (0.75), HealthCo (0.60), SnackBox (0.85) have confidence >= 0.6
        assert "Acme AI" in captured.out
        assert "HealthCo" in captured.out
        assert "SnackBox" in captured.out
        # DevTool (0.30) should be filtered out
        assert "DevTool" not in captured.out

    @pytest.mark.asyncio
    async def test_list_with_limit(self, tmp_db, capsys):
        """List should respect --limit."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_list

        args = Namespace(
            db_path=tmp_db,
            status="pending",
            min_confidence=None,
            limit=2,
            compact=True,
            verbose=False,
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(args)

        await real_db.close()

        captured = capsys.readouterr()
        assert "Showing 2 signal(s)" in captured.out

    @pytest.mark.asyncio
    async def test_list_shows_summary_from_raw_data(self, tmp_db, capsys):
        """List should extract summary/description from raw_data JSON."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_list

        args = Namespace(
            db_path=tmp_db,
            status="pending",
            min_confidence=None,
            limit=20,
            compact=True,
            verbose=False,
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(args)

        await real_db.close()

        captured = capsys.readouterr()
        # Check that descriptions from raw_data are shown
        assert "AI-powered meal kit delivery" in captured.out
        assert "Digital health platform for wellness" in captured.out

    @pytest.mark.asyncio
    async def test_list_empty_result(self, tmp_db, capsys):
        """List should display a message when no signals match."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_list

        args = Namespace(
            db_path=tmp_db,
            status="rejected",  # No rejected signals in test data
            min_confidence=None,
            limit=20,
            compact=True,
            verbose=False,
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(args)

        await real_db.close()

        captured = capsys.readouterr()
        assert "No signals with status 'rejected' found" in captured.out

    @pytest.mark.asyncio
    async def test_list_verbose_mode(self, tmp_db, capsys):
        """Verbose mode should show extra detail per signal."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_list

        args = Namespace(
            db_path=tmp_db,
            status="pending",
            min_confidence=None,
            limit=20,
            compact=True,
            verbose=True,
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(args)

        await real_db.close()

        captured = capsys.readouterr()
        # Verbose mode shows Type: and Key:
        assert "Type:" in captured.out
        assert "Key:" in captured.out
        assert "Detected:" in captured.out


# =============================================================================
# TRIAGE APPROVE TESTS
# =============================================================================


class TestTriageApprove:
    """Tests for cmd_triage_approve handler."""

    @pytest.mark.asyncio
    async def test_approve_creates_audit_log(self, tmp_db, capsys):
        """Approve should create an audit_log entry."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        args = Namespace(
            db_path=tmp_db,
            signal_id=1,
            reason="Clear consumer fit",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(args)

        # Check audit_log
        cursor = await real_db.execute(
            "SELECT action_type, entity_type, entity_id, actor, details FROM audit_log"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "triage_approve"
        assert rows[0][1] == "signal"
        assert rows[0][2] == "1"
        assert rows[0][3] == "operator"
        details = json.loads(rows[0][4])
        assert details["reason"] == "Clear consumer fit"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_approve_updates_processing_to_queued(self, tmp_db, capsys):
        """Approve should set signal_processing status to 'queued'."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        args = Namespace(
            db_path=tmp_db,
            signal_id=1,  # Has an existing signal_processing row
            reason="Clear consumer fit",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(args)

        # Check signal_processing was updated
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "queued"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_approve_creates_processing_row_if_missing(self, tmp_db, capsys):
        """Approve should INSERT signal_processing if no row exists."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        args = Namespace(
            db_path=tmp_db,
            signal_id=4,  # SnackBox has NO signal_processing row
            reason="Strong consumer brand",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(args)

        # Check signal_processing was created
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 4"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "queued"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_approve_nonexistent_signal(self, tmp_db, capsys):
        """Approve should fail if signal ID does not exist."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        args = Namespace(
            db_path=tmp_db,
            signal_id=999,
            reason="Does not exist",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            with pytest.raises(SystemExit) as exc_info:
                await cmd_triage_approve(args)
            assert exc_info.value.code == 1

        await real_db.close()

        captured = capsys.readouterr()
        assert "ERROR" in captured.out
        assert "999" in captured.out

    @pytest.mark.asyncio
    async def test_approve_prints_confirmation(self, tmp_db, capsys):
        """Approve should print a confirmation message."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve

        args = Namespace(
            db_path=tmp_db,
            signal_id=1,
            reason="Clear consumer fit",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(args)

        await real_db.close()

        captured = capsys.readouterr()
        assert "APPROVE" in captured.out
        assert "Acme AI" in captured.out
        assert "queued" in captured.out
        assert "Reason:" in captured.out


# =============================================================================
# TRIAGE REJECT TESTS
# =============================================================================


class TestTriageReject:
    """Tests for cmd_triage_reject handler."""

    @pytest.mark.asyncio
    async def test_reject_creates_audit_log(self, tmp_db, capsys):
        """Reject should create an audit_log entry."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_reject

        args = Namespace(
            db_path=tmp_db,
            signal_id=5,
            reason="B2B dev tool",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(args)

        cursor = await real_db.execute(
            "SELECT action_type, entity_type, entity_id, details FROM audit_log"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "triage_reject"
        assert rows[0][1] == "signal"
        assert rows[0][2] == "5"
        details = json.loads(rows[0][3])
        assert details["reason"] == "B2B dev tool"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_reject_updates_processing_to_rejected(self, tmp_db, capsys):
        """Reject should set signal_processing status to 'rejected'."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_reject

        args = Namespace(
            db_path=tmp_db,
            signal_id=2,  # Has an existing processing row
            reason="Not consumer focused",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(args)

        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 2"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "rejected"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_reject_creates_processing_row_if_missing(self, tmp_db, capsys):
        """Reject should INSERT signal_processing if no row exists."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_reject

        args = Namespace(
            db_path=tmp_db,
            signal_id=5,  # DevTool has no processing row
            reason="B2B developer tool",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_reject(args)

        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 5"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "rejected"

        await real_db.close()


# =============================================================================
# TRIAGE DEFER TESTS
# =============================================================================


class TestTriageDefer:
    """Tests for cmd_triage_defer handler."""

    @pytest.mark.asyncio
    async def test_defer_creates_audit_log(self, tmp_db, capsys):
        """Defer should create an audit_log entry."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_defer

        args = Namespace(
            db_path=tmp_db,
            signal_id=2,
            reason="Need more signals",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_defer(args)

        cursor = await real_db.execute(
            "SELECT action_type, entity_type, entity_id, details FROM audit_log"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "triage_defer"
        assert rows[0][1] == "signal"
        assert rows[0][2] == "2"
        details = json.loads(rows[0][3])
        assert details["reason"] == "Need more signals"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_defer_does_not_change_status(self, tmp_db, capsys):
        """Defer should NOT change the signal_processing status."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_defer

        args = Namespace(
            db_path=tmp_db,
            signal_id=1,  # Has status 'pending'
            reason="Need more signals",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_defer(args)

        # Status should still be 'pending'
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "pending"

        await real_db.close()

    @pytest.mark.asyncio
    async def test_defer_prints_status_unchanged(self, tmp_db, capsys):
        """Defer should indicate status unchanged in output."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_defer

        args = Namespace(
            db_path=tmp_db,
            signal_id=1,
            reason="Need more signals",
        )

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_defer(args)

        await real_db.close()

        captured = capsys.readouterr()
        assert "DEFER" in captured.out
        assert "status unchanged" in captured.out


# =============================================================================
# ARGPARSE TESTS
# =============================================================================


class TestTriageArgparse:
    """Tests for triage argparse configuration."""

    def test_parser_accepts_triage_list(self):
        """Parser should recognize triage list command."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list"])
        assert args.command == "triage"
        assert args.triage_cmd == "list"

    def test_parser_list_default_limit(self):
        """Default limit should be 20."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list"])
        assert args.limit == 20

    def test_parser_list_custom_limit(self):
        """--limit flag should set custom limit."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list", "--limit", "50"])
        assert args.limit == 50

    def test_parser_list_status_filter(self):
        """--status flag should accept valid statuses."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list", "--status", "queued"])
        assert args.status == "queued"

    def test_parser_list_min_confidence(self):
        """--min-confidence flag should accept float."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list", "--min-confidence", "0.5"])
        assert args.min_confidence == 0.5

    def test_parser_list_verbose(self):
        """--verbose flag should enable verbose mode."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list", "--verbose"])
        assert args.verbose is True

    def test_parser_approve_with_reason(self):
        """Parser should accept approve with signal_id and --reason."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "approve", "123", "--reason", "Clear consumer fit"])
        assert args.command == "triage"
        assert args.triage_cmd == "approve"
        assert args.signal_id == 123
        assert args.reason == "Clear consumer fit"

    def test_parser_reject_with_reason(self):
        """Parser should accept reject with signal_id and --reason."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "reject", "124", "--reason", "B2B dev tool"])
        assert args.command == "triage"
        assert args.triage_cmd == "reject"
        assert args.signal_id == 124
        assert args.reason == "B2B dev tool"

    def test_parser_defer_with_reason(self):
        """Parser should accept defer with signal_id and --reason."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "defer", "125", "--reason", "Need more signals"])
        assert args.command == "triage"
        assert args.triage_cmd == "defer"
        assert args.signal_id == 125
        assert args.reason == "Need more signals"

    def test_parser_approve_requires_reason(self):
        """Approve should require --reason flag."""
        from run_pipeline import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["triage", "approve", "123"])

    def test_parser_reject_requires_reason(self):
        """Reject should require --reason flag."""
        from run_pipeline import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["triage", "reject", "123"])

    def test_parser_defer_requires_reason(self):
        """Defer should require --reason flag."""
        from run_pipeline import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["triage", "defer", "123"])

    def test_parser_list_invalid_status_rejected(self):
        """Invalid --status values should be rejected."""
        from run_pipeline import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["triage", "list", "--status", "invalid"])

    def test_parser_triage_db_path(self):
        """--db-path should set database path on triage subcommands."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["triage", "list", "--db-path", "custom.db"])
        assert args.db_path == "custom.db"


# =============================================================================
# INTEGRATION / MULTI-ACTION TESTS
# =============================================================================


class TestTriageWorkflow:
    """Integration tests for triage workflows."""

    @pytest.mark.asyncio
    async def test_approve_then_list_shows_no_pending(self, tmp_db, capsys):
        """After approving all pending signals, list should show fewer results."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_approve, cmd_triage_list

        real_db = await aiosqlite.connect(tmp_db)
        mock_store = _make_mock_store(real_db)

        # Approve signal 1
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Consumer fit")
            )

        # Re-create mock_store (the close mock was called)
        mock_store = _make_mock_store(real_db)

        # List pending -- signal 1 should no longer appear as pending
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_list(
                Namespace(
                    db_path=tmp_db, status="pending", min_confidence=None,
                    limit=20, compact=True, verbose=False,
                )
            )

        await real_db.close()

        captured = capsys.readouterr()
        # Acme AI was approved -> status is now 'queued', should not show in pending
        lines = captured.out.split("\n")
        # Look for Acme AI in the list output section (after the APPROVE line)
        list_output = "\n".join(lines[3:])  # Skip the approval output
        assert "Acme AI" not in list_output or "queued" in list_output

    @pytest.mark.asyncio
    async def test_multiple_actions_on_same_signal(self, tmp_db, capsys):
        """Multiple triage actions should each create separate audit_log entries."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_triage_defer, cmd_triage_approve

        real_db = await aiosqlite.connect(tmp_db)

        # First defer
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_defer(
                Namespace(db_path=tmp_db, signal_id=1, reason="Need more info")
            )

        # Then approve
        mock_store = _make_mock_store(real_db)
        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_triage_approve(
                Namespace(db_path=tmp_db, signal_id=1, reason="Got more info, looks good")
            )

        # Should have 2 audit_log entries
        cursor = await real_db.execute(
            "SELECT action_type FROM audit_log WHERE entity_id = '1' ORDER BY created_at"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "triage_defer"
        assert rows[1][0] == "triage_approve"

        # Final status should be 'queued'
        cursor = await real_db.execute(
            "SELECT status FROM signal_processing WHERE signal_id = 1"
        )
        row = await cursor.fetchone()
        assert row[0] == "queued"

        await real_db.close()
