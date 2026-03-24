"""Tests for scripts/validate_kg_etl.py — KG validation skeleton."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.validate_kg_etl import main, _reject_live_db


class TestRejectLiveDb:
    """_reject_live_db() hard-stops on live database filenames."""

    def test_rejects_signals_db(self):
        with pytest.raises(SystemExit) as exc_info:
            _reject_live_db("signals.db")
        assert exc_info.value.code == 1

    def test_rejects_absolute_signals_db(self):
        with pytest.raises(SystemExit) as exc_info:
            _reject_live_db(os.path.abspath("signals.db"))
        assert exc_info.value.code == 1

    def test_accepts_snapshot_name(self):
        # Should not raise
        _reject_live_db("signals.db.kg-validation-snapshot")

    def test_accepts_other_name(self):
        _reject_live_db("test.db")


class TestMainHelp:
    """--help exits cleanly."""

    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0


class TestMainMissingDb:
    """Missing --db file returns error."""

    def test_missing_file_returns_1(self):
        rc = main(["--db", "nonexistent-snapshot.db", "--phase", "A"])
        assert rc == 1


class TestMainPhaseOutput:
    """Skeleton outputs plan-only results for each phase."""

    @pytest.fixture
    def snapshot_db(self, tmp_path):
        """Create a minimal snapshot file to satisfy the exists() check."""
        db_file = tmp_path / "test-snapshot.db"
        db_file.write_bytes(b"")
        return str(db_file)

    def test_single_phase_a(self, snapshot_db, capsys):
        rc = main(["--db", snapshot_db, "--phase", "A"])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Phase A" in output
        assert "plan_only" in output

    def test_all_phases(self, snapshot_db, capsys):
        rc = main(["--db", snapshot_db, "--phase", "all"])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Phase A" in output
        assert "Phase B" in output
        assert "Phase C" in output
        assert "Phase D" in output

    def test_json_output(self, snapshot_db, capsys):
        import json
        rc = main(["--db", snapshot_db, "--phase", "B", "--json"])
        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert "phases" in data
        assert len(data["phases"]) == 1
        assert data["phases"][0]["phase"] == "B"
        assert data["phases"][0]["status"] == "plan_only"


class TestLiveDbRejectedByMain:
    """main() rejects signals.db even if the file exists."""

    def test_signals_db_rejected(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--db", "signals.db", "--phase", "A"])
        assert exc_info.value.code == 1
