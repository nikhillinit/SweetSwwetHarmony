"""Tests for the export-queue CLI command.

Phase 0, Task 0.5: Export pending/queued signals to CSV for offline review.
"""
import asyncio
import csv
import io
import os
import sys
import tempfile
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
            reason TEXT,
            metadata TEXT
        )
    """)

    now = datetime.now(timezone.utc)
    signals = [
        ("github_trending", "github", "domain:acme.ai", "Acme AI", 0.75, (now - timedelta(days=5)).isoformat()),
        ("sec_filing", "sec_edgar", "domain:healthco.com", "HealthCo", 0.60, (now - timedelta(days=10)).isoformat()),
        ("job_posting", "greenhouse", "domain:travelx.io", "TravelX", 0.45, (now - timedelta(days=20)).isoformat()),
        ("product_launch", "product_hunt", "domain:snackbox.com", "SnackBox", 0.85, (now - timedelta(days=40)).isoformat()),
        ("hacker_news", "hacker_news", "domain:devtool.dev", "DevTool", 0.30, (now - timedelta(days=2)).isoformat()),
    ]

    for sig in signals:
        await db.execute(
            "INSERT INTO signals (signal_type, source_api, canonical_key, company_name, confidence, detected_at) VALUES (?,?,?,?,?,?)",
            sig,
        )

    # Add processing statuses for some signals
    processing = [
        (1, "pending"),
        (2, "queued"),
        (3, "pushed"),
        (4, "rejected"),
        # Signal 5 has no processing row -> defaults to 'pending'
    ]
    for signal_id, status in processing:
        await db.execute(
            "INSERT INTO signal_processing (signal_id, status) VALUES (?,?)",
            (signal_id, status),
        )

    await db.commit()
    await db.close()


class TestExportQueueHandler:
    """Tests for cmd_export_queue handler function."""

    @pytest.mark.asyncio
    async def test_export_all_to_file(self, tmp_db, tmp_path):
        """Export all signals to a CSV file."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "queue.csv")

        # We need to mock SignalStore to use our pre-built db
        # but the handler opens SignalStore directly, so we patch it
        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status=None,
            min_confidence=None,
            days=None,
            format="csv",
        )

        # Patch SignalStore so initialize() skips migrations
        # but the _db connection uses our pre-populated database
        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        # Verify output file
        assert os.path.exists(out_file)
        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header + 5 data rows
        assert len(rows) == 6
        assert rows[0] == [
            "signal_id", "company_name", "canonical_key", "confidence",
            "signal_type", "source_api", "detected_at", "status", "company_id",
        ]

    @pytest.mark.asyncio
    async def test_export_filter_by_status(self, tmp_db, tmp_path):
        """Export only signals with a specific status."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "pending.csv")

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status="pending",
            min_confidence=None,
            days=None,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header + 2 pending signals (signal 1 explicit pending + signal 5 default pending)
        assert len(rows) == 3
        statuses = [row[7] for row in rows[1:]]
        assert all(s == "pending" for s in statuses)

    @pytest.mark.asyncio
    async def test_export_filter_by_min_confidence(self, tmp_db, tmp_path):
        """Export only signals above minimum confidence threshold."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "high_conf.csv")

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status=None,
            min_confidence=0.6,
            days=None,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header + signals with confidence >= 0.6 (Acme 0.75, HealthCo 0.60, SnackBox 0.85)
        assert len(rows) == 4
        confidences = [float(row[3]) for row in rows[1:]]
        assert all(c >= 0.6 for c in confidences)

    @pytest.mark.asyncio
    async def test_export_filter_by_days(self, tmp_db, tmp_path):
        """Export only signals from the last N days."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "recent.csv")

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status=None,
            min_confidence=None,
            days=7,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header + signals from last 7 days (Acme 5 days, DevTool 2 days)
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_export_combined_filters(self, tmp_db, tmp_path):
        """Apply multiple filters simultaneously."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "combined.csv")

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status="pending",
            min_confidence=0.5,
            days=30,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header + Acme AI (pending, 0.75, 5 days ago) - only one matches all filters
        assert len(rows) == 2
        assert rows[1][1] == "Acme AI"

    @pytest.mark.asyncio
    async def test_export_to_stdout(self, tmp_db, capsys):
        """Export to stdout when no --out specified."""
        await _setup_db(tmp_db)

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=None,
            status=None,
            min_confidence=None,
            days=None,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # Header + 5 data rows
        assert len(lines) == 6
        assert "signal_id" in lines[0]

    @pytest.mark.asyncio
    async def test_export_empty_result(self, tmp_db, tmp_path):
        """Export produces header-only CSV when no signals match."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "empty.csv")

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status="queued",
            min_confidence=0.99,  # no signal has this confidence
            days=None,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Header only, no data rows
        assert len(rows) == 1
        assert rows[0][0] == "signal_id"

    @pytest.mark.asyncio
    async def test_export_ordered_by_detected_at_desc(self, tmp_db, tmp_path):
        """Results should be ordered by detected_at descending (newest first)."""
        await _setup_db(tmp_db)
        out_file = str(tmp_path / "ordered.csv")

        from run_pipeline import cmd_export_queue

        args = Namespace(
            db_path=tmp_db,
            out=out_file,
            status=None,
            min_confidence=None,
            days=None,
            format="csv",
        )

        mock_store = MagicMock()
        real_db = await aiosqlite.connect(tmp_db)
        mock_store._db = real_db
        mock_store.initialize = AsyncMock()
        mock_store.close = AsyncMock()

        with patch("run_pipeline.SignalStore", return_value=mock_store):
            await cmd_export_queue(args)

        await real_db.close()

        with open(out_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Verify ordering: DevTool (2d), Acme (5d), HealthCo (10d), TravelX (20d), SnackBox (40d)
        data_rows = rows[1:]
        assert data_rows[0][1] == "DevTool"
        assert data_rows[1][1] == "Acme AI"
        assert data_rows[-1][1] == "SnackBox"


class TestExportQueueArgparse:
    """Tests for export-queue argparse configuration."""

    def test_parser_accepts_export_queue(self):
        """Parser should recognize export-queue command."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue"])
        assert args.command == "export-queue"

    def test_parser_default_format_csv(self):
        """Default format should be csv."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue"])
        assert args.format == "csv"

    def test_parser_out_flag(self):
        """--out flag should set output file path."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue", "--out", "queue.csv"])
        assert args.out == "queue.csv"

    def test_parser_status_filter(self):
        """--status flag should accept valid statuses."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue", "--status", "pending"])
        assert args.status == "pending"

    def test_parser_min_confidence(self):
        """--min-confidence should accept float value."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue", "--min-confidence", "0.4"])
        assert args.min_confidence == 0.4

    def test_parser_days_filter(self):
        """--days should accept integer value."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue", "--days", "30"])
        assert args.days == 30

    def test_parser_db_path(self):
        """--db-path should set database path."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args(["export-queue", "--db-path", "custom.db"])
        assert args.db_path == "custom.db"

    def test_parser_all_flags_combined(self):
        """All flags should work together."""
        from run_pipeline import create_parser

        parser = create_parser()
        args = parser.parse_args([
            "export-queue",
            "--format", "csv",
            "--out", "queue.csv",
            "--status", "pending",
            "--min-confidence", "0.5",
            "--days", "14",
            "--db-path", "my.db",
        ])
        assert args.command == "export-queue"
        assert args.format == "csv"
        assert args.out == "queue.csv"
        assert args.status == "pending"
        assert args.min_confidence == 0.5
        assert args.days == 14
        assert args.db_path == "my.db"

    def test_parser_invalid_status_rejected(self):
        """Invalid --status values should be rejected."""
        from run_pipeline import create_parser

        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export-queue", "--status", "invalid"])
