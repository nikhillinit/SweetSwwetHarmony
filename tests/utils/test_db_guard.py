"""Tests for utils.db_guard — the external watermark guard.

Phase 2 hotfix Day 2.5: the guard no longer auto-initializes a missing
watermark. A missing watermark blocks writes by default. Operator must run
``python run_pipeline.py init-watermark`` to bootstrap. See
``.omx/wave6/db_guard_runbook.md`` for the canonical contract.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from utils import db_guard


def _create_signals_db(db_path: Path, count: int) -> None:
    """Create a minimal SQLite DB with a signals table sized to *count*."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM signals")
        if count:
            conn.executemany(
                "INSERT INTO signals (id) VALUES (?)",
                [(idx,) for idx in range(1, count + 1)],
            )
        conn.commit()
    finally:
        conn.close()


class TestLoadWatermark:
    def test_returns_empty_dict_when_missing(self, tmp_path):
        with patch.object(db_guard, "WATERMARK_PATH", tmp_path / "nonexistent.json"):
            assert db_guard.load_watermark() == {}

    def test_returns_empty_dict_when_corrupt(self, tmp_path):
        watermark = tmp_path / "watermark.json"
        watermark.write_text("not-json")
        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.load_watermark() == {}

    def test_returns_payload_when_present(self, tmp_path):
        watermark = tmp_path / "watermark.json"
        payload = {"signal_count": 100, "schema_version": 1, "timestamp": "2026-04-04T00:00:00Z"}
        watermark.write_text(json.dumps(payload))
        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.load_watermark() == payload


class TestSaveWatermark:
    def test_creates_file_and_directories(self, tmp_path):
        watermark = tmp_path / "state" / "db_watermark.json"
        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            db_guard.save_watermark(signal_count=42, schema_version=3, timestamp="2026-04-04T00:00:00Z")

        assert watermark.exists()
        payload = json.loads(watermark.read_text())
        assert payload["signal_count"] == 42
        assert payload["schema_version"] == 3
        assert payload["timestamp"] == "2026-04-04T00:00:00Z"


class TestReadCurrentSignalCount:
    def test_returns_count_for_existing_db(self, tmp_path):
        db_path = tmp_path / "signals.db"
        _create_signals_db(db_path, 150)
        count, error = db_guard.read_current_signal_count(str(db_path))
        assert count == 150
        assert error is None

    def test_returns_error_for_missing_db(self, tmp_path):
        db_path = tmp_path / "missing.db"
        count, error = db_guard.read_current_signal_count(str(db_path))
        assert count is None
        assert error is not None


class TestCheckDbHealth:
    def test_missing_watermark_reports_unhealthy_without_mutating(self, tmp_path):
        """Strict contract: missing watermark is a real state, not a soft-init opportunity."""
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 200)

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            ok, message = db_guard.check_db_health(str(db_path))

        assert ok is False
        assert message == "watermark_missing"
        # Filesystem must remain untouched — only `init-watermark` writes the file.
        assert not watermark.exists()

    def test_missing_watermark_with_unreadable_db(self, tmp_path):
        db_path = tmp_path / "missing.db"
        watermark = tmp_path / "watermark.json"

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            ok, message = db_guard.check_db_health(str(db_path))

        assert ok is False
        assert message == "watermark_missing"
        assert not watermark.exists()

    def test_healthy_db_passes(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 100)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            ok, message = db_guard.check_db_health(str(db_path))

        assert ok is True
        assert message == "healthy"

    def test_exactly_50_percent_drop_is_healthy(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 50)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            ok, message = db_guard.check_db_health(str(db_path))

        assert ok is True
        assert message == "healthy"

    def test_just_under_50_percent_drop_is_unhealthy(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 49)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            ok, message = db_guard.check_db_health(str(db_path))

        assert ok is False
        assert message == "catastrophic_drop_detected"

    def test_db_read_error_returns_unhealthy(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark), \
             patch.object(db_guard, "read_current_signal_count", return_value=(None, "disk I/O error")):
            ok, message = db_guard.check_db_health(str(db_path))

        assert ok is False
        assert "db_read_error" in message


class TestGuardCommand:
    def test_healthy_db_allows_any_command(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 100)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "read") is True
            assert db_guard.guard_command(str(db_path), "write") is True
            assert db_guard.guard_command(str(db_path), "write", allow_override=True) is True

    def test_catastrophic_drop_allows_read_with_warning(self, tmp_path, caplog):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 4)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "read") is True

    def test_catastrophic_drop_blocks_write_by_default(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 4)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "write", allow_override=False) is False

    def test_recovery_override_allows_write_through(self, tmp_path):
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 4)
        watermark.write_text(json.dumps({"signal_count": 100}))

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "write", allow_override=True) is True

    def test_missing_watermark_blocks_write(self, tmp_path):
        """Strict contract: writes blocked until operator runs init-watermark."""
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 75)

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "write") is False

        # Filesystem must remain untouched.
        assert not watermark.exists()

    def test_missing_watermark_allows_read_with_warning(self, tmp_path, caplog):
        """Reads on a missing watermark surface diagnostic state without mutation."""
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 75)

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "read") is True

        assert not watermark.exists()

    def test_missing_watermark_with_override_still_blocks_write(self, tmp_path):
        """Recovery override is for tripped baselines, not for bootstrapping a missing watermark."""
        db_path = tmp_path / "signals.db"
        watermark = tmp_path / "watermark.json"
        _create_signals_db(db_path, 75)

        with patch.object(db_guard, "WATERMARK_PATH", watermark):
            assert db_guard.guard_command(str(db_path), "write", allow_override=True) is False

        assert not watermark.exists()
