"""Tests for the push CLI command.

Phase 0, Task 0.6: Push specific signals to Notion by ID (manual push).
"""
import asyncio
import os
import sys
from argparse import Namespace
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
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
            reason TEXT,
            metadata TEXT,
            error_message TEXT
        )
    """)

    now = datetime.now(timezone.utc)
    signals = [
        ("github_trending", "github", "domain:acme.ai", "Acme AI", 0.75,
         (now - timedelta(days=5)).isoformat()),
        ("sec_filing", "sec_edgar", "domain:healthco.com", "HealthCo", 0.60,
         (now - timedelta(days=10)).isoformat()),
        ("job_posting", "greenhouse", "domain:travelx.io", "TravelX", 0.45,
         (now - timedelta(days=20)).isoformat()),
        ("product_launch", "product_hunt", "domain:snackbox.com", "SnackBox", 0.85,
         (now - timedelta(days=40)).isoformat()),
        ("hacker_news", "hacker_news", "domain:acme.ai", "Acme AI", 0.55,
         (now - timedelta(days=2)).isoformat()),
    ]

    for sig in signals:
        await db.execute(
            "INSERT INTO signals (signal_type, source_api, canonical_key, "
            "company_name, confidence, detected_at) VALUES (?,?,?,?,?,?)",
            sig,
        )

    # Add processing statuses
    processing = [
        (1, "pending"),
        (2, "pending"),
        (3, "pending"),
        (4, "pending"),
        (5, "pending"),
    ]
    for signal_id, status in processing:
        await db.execute(
            "INSERT INTO signal_processing (signal_id, status) VALUES (?,?)",
            (signal_id, status),
        )

    await db.commit()
    await db.close()


def _make_stored_signal(sid, signal_type, source_api, canonical_key,
                        company_name, confidence):
    """Create a mock StoredSignal-like object."""
    mock = MagicMock()
    mock.id = sid
    mock.signal_type = signal_type
    mock.source_api = source_api
    mock.canonical_key = canonical_key
    mock.company_name = company_name
    mock.confidence = confidence
    mock.detected_at = datetime.now(timezone.utc)
    mock.created_at = datetime.now(timezone.utc)
    mock.processing_status = "pending"
    mock.raw_data = {}
    return mock


class TestPushArgparse:
    """Tests for push argparse configuration."""

    def test_parser_accepts_push(self):
        """Parser should recognize push command."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "1,2,3"])
        assert args.command == "push"

    def test_parser_signal_ids_required(self):
        """--signal-ids should be required."""
        from run_pipeline import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["push"])

    def test_parser_signal_ids_single(self):
        """--signal-ids should accept a single ID."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "42"])
        assert args.signal_ids == "42"

    def test_parser_signal_ids_multiple(self):
        """--signal-ids should accept comma-separated IDs."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "1,2,3"])
        assert args.signal_ids == "1,2,3"

    def test_parser_dry_run_default_false(self):
        """--dry-run should default to False."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "1"])
        assert args.dry_run is False

    def test_parser_dry_run_flag(self):
        """--dry-run should set to True."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "1", "--dry-run"])
        assert args.dry_run is True

    def test_parser_db_path(self):
        """--db-path should set database path."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "1", "--db-path", "custom.db"])
        assert args.db_path == "custom.db"

    def test_parser_all_flags_combined(self):
        """All flags should work together."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args([
            "push",
            "--signal-ids", "1,2,3",
            "--dry-run",
            "--db-path", "my.db",
        ])
        assert args.command == "push"
        assert args.signal_ids == "1,2,3"
        assert args.dry_run is True
        assert args.db_path == "my.db"


class TestPushHandlerDryRun:
    """Tests for cmd_push handler in dry-run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_shows_signals(self, tmp_db, capsys):
        """Dry run should display signal details without pushing."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1,2",
            dry_run=True,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        # Create mock signals for get_signal
        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        sig2 = _make_stored_signal(2, "sec_filing", "sec_edgar",
                                   "domain:healthco.com", "HealthCo", 0.60)

        mock_store.get_signal = AsyncMock(side_effect=lambda sid: {
            1: sig1, 2: sig2
        }.get(sid))

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_push(args)

        await real_db.close()

        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "Acme AI" in captured.out
        assert "HealthCo" in captured.out
        assert "PUSH SUMMARY" in captured.out

    @pytest.mark.asyncio
    async def test_dry_run_skips_delivery_check(self, tmp_db, capsys, monkeypatch):
        """Dry run should not check delivery policy."""
        await _setup_db(tmp_db)
        # Set mode to staging_only - would block real push
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1",
            dry_run=True,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        mock_store.get_signal = AsyncMock(return_value=sig1)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            # Should NOT raise even though mode is staging_only
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "Acme AI" in captured.out

    @pytest.mark.asyncio
    async def test_dry_run_groups_by_canonical_key(self, tmp_db, capsys):
        """Dry run should group signals by canonical key."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_push

        # Signals 1 and 5 share canonical_key "domain:acme.ai"
        args = Namespace(
            signal_ids="1,5",
            dry_run=True,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        sig5 = _make_stored_signal(5, "hacker_news", "hacker_news",
                                   "domain:acme.ai", "Acme AI", 0.55)

        mock_store.get_signal = AsyncMock(side_effect=lambda sid: {
            1: sig1, 5: sig5
        }.get(sid))

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "1 prospect(s)" in captured.out
        assert "2 signal(s)" in captured.out
        assert "Signals: 2" in captured.out


class TestPushHandlerNotFound:
    """Tests for cmd_push with non-existent signal IDs."""

    @pytest.mark.asyncio
    async def test_not_found_signal(self, tmp_db, capsys):
        """Should report NOT FOUND for missing signal IDs."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="999",
            dry_run=True,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()
        mock_store.get_signal = AsyncMock(return_value=None)

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[NOT FOUND]" in captured.out
        assert "999" in captured.out
        assert "No valid signals found" in captured.out

    @pytest.mark.asyncio
    async def test_partial_not_found(self, tmp_db, capsys):
        """Should handle mix of found and not-found signal IDs."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1,999",
            dry_run=True,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        mock_store.get_signal = AsyncMock(side_effect=lambda sid: {
            1: sig1
        }.get(sid))

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[NOT FOUND]" in captured.out
        assert "999" in captured.out
        assert "Acme AI" in captured.out
        assert "Not found:  1" in captured.out


class TestPushHandlerDeliveryPolicy:
    """Tests for delivery policy enforcement in push command."""

    @pytest.mark.asyncio
    async def test_blocked_by_staging_mode(self, tmp_db, monkeypatch):
        """Push should fail when DELIVERY_MODE is staging_only."""
        monkeypatch.setenv("DELIVERY_MODE", "staging_only")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1",
            dry_run=False,
            db_path=tmp_db,
        )

        with pytest.raises(SystemExit) as exc_info:
            await cmd_push(args)

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_allowed_by_manual_publish_mode(self, tmp_db, capsys, monkeypatch):
        """Push should proceed when DELIVERY_MODE is manual_publish."""
        await _setup_db(tmp_db)
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-db-id")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1",
            dry_run=False,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        mock_store.get_signal = AsyncMock(return_value=sig1)

        # Mock the NotionPusher and its dependencies
        mock_push_result = MagicMock()
        mock_push_result.error = None
        mock_push_result.decision = MagicMock()
        mock_push_result.decision.value = "push"
        mock_push_result.confidence = 0.75

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(return_value=mock_push_result)

        with patch("run_pipeline.SignalStore", return_value=mock_store), \
             patch("run_pipeline.cmd_push.__module__", "run_pipeline"), \
             patch("connectors.notion_connector_v2.NotionConnector") as mock_nc, \
             patch("verification.verification_gate_v2.VerificationGate") as mock_vg, \
             patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[PUSHED]" in captured.out
        assert "Pushed:     1" in captured.out


class TestPushHandlerInvalidInput:
    """Tests for invalid input handling."""

    @pytest.mark.asyncio
    async def test_invalid_signal_ids_non_numeric(self, tmp_db):
        """Should fail with non-numeric signal IDs."""
        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="abc,def",
            dry_run=True,
            db_path=tmp_db,
        )

        with pytest.raises(SystemExit) as exc_info:
            await cmd_push(args)
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_signal_ids_with_spaces(self, tmp_db, capsys):
        """Should handle signal IDs with spaces around them."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids=" 1 , 2 ",
            dry_run=True,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        sig2 = _make_stored_signal(2, "sec_filing", "sec_edgar",
                                   "domain:healthco.com", "HealthCo", 0.60)

        mock_store.get_signal = AsyncMock(side_effect=lambda sid: {
            1: sig1, 2: sig2
        }.get(sid))

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "2 signal(s)" in captured.out


class TestPushHandlerActualPush:
    """Tests for actual push behavior (non-dry-run)."""

    @pytest.mark.asyncio
    async def test_push_error_handling(self, tmp_db, capsys, monkeypatch):
        """Should handle push errors gracefully."""
        await _setup_db(tmp_db)
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-db-id")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1",
            dry_run=False,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        mock_store.get_signal = AsyncMock(return_value=sig1)

        # Simulate an error from the pusher
        mock_push_result = MagicMock()
        mock_push_result.error = "Connection timeout"
        mock_push_result.decision = MagicMock()
        mock_push_result.decision.value = "error"
        mock_push_result.confidence = 0.0

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(return_value=mock_push_result)

        with patch("run_pipeline.SignalStore", return_value=mock_store), \
             patch("connectors.notion_connector_v2.NotionConnector"), \
             patch("verification.verification_gate_v2.VerificationGate"), \
             patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out
        assert "Errors:     1" in captured.out

    @pytest.mark.asyncio
    async def test_push_rejected_signal(self, tmp_db, capsys, monkeypatch):
        """Should report rejected signals."""
        await _setup_db(tmp_db)
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-db-id")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="3",
            dry_run=False,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig3 = _make_stored_signal(3, "job_posting", "greenhouse",
                                   "domain:travelx.io", "TravelX", 0.45)
        mock_store.get_signal = AsyncMock(return_value=sig3)

        # Simulate rejection
        mock_push_result = MagicMock()
        mock_push_result.error = None
        mock_push_result.decision = MagicMock()
        mock_push_result.decision.value = "reject"
        mock_push_result.confidence = 0.25

        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(return_value=mock_push_result)

        with patch("run_pipeline.SignalStore", return_value=mock_store), \
             patch("connectors.notion_connector_v2.NotionConnector"), \
             patch("verification.verification_gate_v2.VerificationGate"), \
             patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[REJECTED]" in captured.out
        assert "Rejected:   1" in captured.out

    @pytest.mark.asyncio
    async def test_push_exception_handling(self, tmp_db, capsys, monkeypatch):
        """Should handle exceptions from pusher gracefully."""
        await _setup_db(tmp_db)
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.setenv("NOTION_API_KEY", "test-key")
        monkeypatch.setenv("NOTION_DATABASE_ID", "test-db-id")

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1",
            dry_run=False,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        mock_store.get_signal = AsyncMock(return_value=sig1)

        # Simulate an exception
        mock_pusher = MagicMock()
        mock_pusher.process_single_prospect = AsyncMock(
            side_effect=RuntimeError("Network error")
        )

        with patch("run_pipeline.SignalStore", return_value=mock_store), \
             patch("connectors.notion_connector_v2.NotionConnector"), \
             patch("verification.verification_gate_v2.VerificationGate"), \
             patch("workflows.notion_pusher.NotionPusher", return_value=mock_pusher):
            await cmd_push(args)

        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out
        assert "Network error" in captured.out
        assert "Errors:     1" in captured.out

    @pytest.mark.asyncio
    async def test_push_missing_notion_keys(self, tmp_db, monkeypatch):
        """Should fail if NOTION_API_KEY or NOTION_DATABASE_ID not set."""
        await _setup_db(tmp_db)
        monkeypatch.setenv("DELIVERY_MODE", "manual_publish")
        monkeypatch.delenv("NOTION_API_KEY", raising=False)
        monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)

        from run_pipeline import cmd_push

        args = Namespace(
            signal_ids="1",
            dry_run=False,
            db_path=tmp_db,
        )

        mock_store = MagicMock()
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        sig1 = _make_stored_signal(1, "github_trending", "github",
                                   "domain:acme.ai", "Acme AI", 0.75)
        mock_store.get_signal = AsyncMock(return_value=sig1)

        with patch("run_pipeline.SignalStore", return_value=mock_store), \
             pytest.raises(SystemExit) as exc_info:
            await cmd_push(args)

        assert exc_info.value.code == 1


class TestPushDispatch:
    """Tests for push command dispatch in main()."""

    def test_dispatch_push_in_main(self):
        """Verify push command is dispatched in main()."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["push", "--signal-ids", "1"])
        assert args.command == "push"
        assert args.signal_ids == "1"
