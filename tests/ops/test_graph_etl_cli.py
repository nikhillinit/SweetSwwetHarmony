"""CLI tests for graph etl, etl-status, and query subcommands.

All tests use temp-file DBs with minimal fixture data.
No live signals.db is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ops.graph_cli import register_graph_commands


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register_graph_commands(subparsers)
    return parser


def _seed_db(path: str) -> None:
    """Create a DB via SignalStore.initialize() (full migration chain), then seed test data."""
    import asyncio
    from storage.signal_store import SignalStore

    async def _setup():
        store = SignalStore(path)
        await store.initialize()
        try:
            await store._db.execute(
                "INSERT INTO company_files "
                "(company_id, company_name, canonical_key, status, source_apis, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("comp_cli1", "CliCo", "domain:clico.ai", "promoted",
                 '["sec_edgar"]', "2026-01-01", "2026-03-01"),
            )
            await store._db.execute(
                "INSERT INTO signals "
                "(signal_type, source_api, canonical_key, company_name, confidence, raw_data, detected_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("funding_event", "sec_edgar", "domain:clico.ai", "CliCo", 0.75,
                 json.dumps({"company_name": "CliCo", "state": "NY"}),
                 "2026-01-15", "2026-01-15"),
            )
            await store._db.commit()
        finally:
            await store.close()

    asyncio.run(_setup())


@pytest.fixture
def seeded_db(tmp_path):
    path = str(tmp_path / "test_cli.db")
    _seed_db(path)
    yield path


# ---------------------------------------------------------------------------
# Argument parsing tests (no DB needed)
# ---------------------------------------------------------------------------

class TestArgParsing:
    def test_etl_subcommand_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "etl", "--mode", "full", "--dry-run", "--json"])
        assert args.mode == "full"
        assert args.dry_run is True
        assert args.json_output is True

    def test_etl_incremental_mode(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "etl", "--mode", "incremental"])
        assert args.mode == "incremental"

    def test_etl_status_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "etl-status", "--json"])
        assert args.json_output is True

    def test_evidence_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "evidence", "comp123", "--json"])
        assert args.company_id == "comp123"

    def test_gaps_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "gaps", "--min-evidence", "3", "--limit", "50"])
        assert args.min_evidence == 3
        assert args.limit == 50

    def test_conflicts_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "conflicts", "--limit", "10", "--json"])
        assert args.limit == 10

    def test_sector_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "sector", "sector:cpg", "--json"])
        assert args.sector_id == "sector:cpg"

    def test_duplicates_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "duplicates", "--limit", "25"])
        assert args.limit == 25

    def test_rank_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "rank", "--min-sources", "2", "--limit", "20", "--json"])
        assert args.min_sources == 2
        assert args.limit == 20

    def test_ego_parses(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "ego", "node123", "--depth", "3"])
        assert args.node_id == "node123"
        assert args.depth == 3

    def test_ego_with_out_file(self):
        parser = _make_parser()
        args = parser.parse_args(["graph", "ego", "node123", "--out", "ego.json"])
        assert args.out_file == "ego.json"


# ---------------------------------------------------------------------------
# ETL execution tests (use seeded DB)
# ---------------------------------------------------------------------------

class TestETLExecution:
    def test_etl_dry_run_json(self, seeded_db, capsys):
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "etl", "--mode", "full", "--dry-run", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "dry_run"
        assert data["company_nodes"] == 1
        assert data["signal_nodes"] == 1
        assert data["detected_by_edges"] == 1

    def test_etl_full_json(self, seeded_db, capsys):
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "etl", "--mode", "full", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "completed"
        assert data["company_nodes"] == 1
        assert data["signal_nodes"] == 1
        assert data["detected_by_edges"] == 1

    def test_etl_full_human_output(self, seeded_db, capsys):
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "etl", "--mode", "full"])
        args.func(args)

        captured = capsys.readouterr()
        assert "Signal ETL" in captured.out
        assert "Companies:" in captured.out
        assert "Status:  completed" in captured.out


class TestETLStatusExecution:
    def test_etl_status_before_build(self, seeded_db, capsys):
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "etl-status", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["last_run"] is None
        assert data["source_tables"]["signals"] == 1
        assert data["source_tables"]["company_files"] == 1

    def test_etl_status_after_build(self, seeded_db, capsys):
        parser = _make_parser()
        # Run ETL first
        args = parser.parse_args(["graph", "--db", seeded_db, "etl", "--mode", "full", "--json"])
        args.func(args)
        capsys.readouterr()  # discard

        # Now check status
        args = parser.parse_args(["graph", "--db", seeded_db, "etl-status", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["last_run"] is not None
        assert data["last_run"]["status"] == "completed"
        assert data["node_counts"].get("company", 0) > 0


# ---------------------------------------------------------------------------
# Query command execution tests (require ETL to have run)
# ---------------------------------------------------------------------------

def _run_etl(db_path: str) -> None:
    parser = _make_parser()
    args = parser.parse_args(["graph", "--db", db_path, "etl", "--mode", "full", "--json"])
    args.func(args)


class TestEvidenceCommand:
    def test_evidence_json(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "evidence", "comp_cli1", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["company_id"] == "comp_cli1"
        assert data["company_label"] == "CliCo"
        assert len(data["signals"]) == 1
        assert data["source_count"] == 1

    def test_evidence_nonexistent_company(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "evidence", "nonexistent", "--json"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
        assert exc.value.code == 1

    def test_evidence_human_output(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "evidence", "comp_cli1"])
        args.func(args)

        captured = capsys.readouterr()
        assert "Evidence Chain" in captured.out
        assert "CliCo" in captured.out


class TestGapsCommand:
    def test_gaps_json(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "gaps", "--min-evidence", "2", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # comp_cli1 has only 1 source, so it's a gap at min_evidence=2
        assert len(data) == 1
        assert data[0]["company_id"] == "comp_cli1"


class TestConflictsCommand:
    def test_conflicts_json_empty(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "conflicts", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Single-source company has no conflicts
        assert isinstance(data, list)


class TestSectorCommand:
    def test_sector_nonexistent(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "sector", "sector:nonexistent", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == []


class TestRankCommand:
    def test_rank_json(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "rank", "--min-sources", "1", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 1
        assert data[0]["company_id"] == "comp_cli1"
        assert 0 <= data[0]["evidence_strength"] <= 1

    def test_rank_human_output(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "rank", "--min-sources", "1"])
        args.func(args)

        captured = capsys.readouterr()
        assert "Evidence Strength Ranking" in captured.out


class TestDuplicatesCommand:
    def test_duplicates_json_empty(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "duplicates", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Single company, no duplicates
        assert data == []


class TestEgoCommand:
    def test_ego_json(self, seeded_db, capsys):
        _run_etl(seeded_db)
        capsys.readouterr()

        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "ego", "comp_cli1", "--depth", "1", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["center"] == "comp_cli1"
        assert data["node_count"] >= 1
        assert data["edge_count"] >= 0

    def test_ego_out_file(self, seeded_db, capsys, tmp_path):
        _run_etl(seeded_db)
        capsys.readouterr()

        out_file = str(tmp_path / "ego_test.json")
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", seeded_db, "ego", "comp_cli1", "--out", out_file])
        args.func(args)

        with open(out_file) as f:
            data = json.load(f)
        assert data["center"] == "comp_cli1"
        assert "nodes" in data
        assert "edges" in data
