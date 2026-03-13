"""CLI tests for `python -m ops.cli graph build`."""

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


def test_graph_build_json_output(capsys):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", path, "build", "--json"])
        args.func(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "completed"
        assert data["nodes_upserted"] > 50
        assert data["edges_upserted"] > 100
        assert data["details"]["collector_count"] == 17
        assert any("cofounder_search" in warning for warning in data["warnings"])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_graph_build_rejects_cross_checkout_repo_root(capsys, tmp_path):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        parser = _make_parser()
        args = parser.parse_args(["graph", "--db", path, "build", "--repo-root", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            args.func(args)

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "Cross-checkout graph builds are not supported" in captured.err
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
