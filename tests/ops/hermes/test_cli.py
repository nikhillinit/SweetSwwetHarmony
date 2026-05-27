from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ops.hermes_cli import register_hermes_commands

from .conftest import minimal_config_dict


def _write_cli_config(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_register_hermes_commands_adds_route_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_hermes_commands(subparsers)
    args = parser.parse_args(
        ["hermes", "route", "--phase", "production", "--task", "schema migration"]
    )

    assert args.command == "hermes"
    assert args.hermes_cmd == "route"
    assert callable(args.func)


def test_route_json_cli_creates_no_files(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "route",
            "--json",
            "--config",
            str(config_path),
            "--phase",
            "production",
            "--task",
            "fix thesis filter",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendedExecutor"] == "kimi"
    assert not (tmp_path / "ai-logs").exists()


def test_ops_cli_hermes_help_works() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ops.cli", "hermes", "--help"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Hermes multi-model routing" in result.stdout


def test_hermes_cli_module_help_works() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ops.hermes_cli", "--help"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "route" in result.stdout


def test_run_dry_run_cli_writes_artifacts(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "run",
            "--dry-run",
            "--config",
            str(config_path),
            "--phase",
            "production",
            "--task",
            "schema migration",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Dry-run ledger" in result.stdout
    assert list((tmp_path / "ai-logs" / "hermes" / "runs").iterdir())


def test_providers_doctor_placeholder_exits_until_provider_module_exists() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ops.cli", "hermes", "providers", "doctor"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "not available yet" in result.stderr


def test_lock_force_unlock_requires_reason(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "lock",
            "force-unlock",
            "--config",
            str(config_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--reason" in result.stderr
