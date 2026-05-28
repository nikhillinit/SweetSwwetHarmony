from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ops.hermes_cli import register_hermes_commands

from .conftest import minimal_config_dict


def _write_cli_config(tmp_path: Path, *, non_executable_docs: bool = False) -> Path:
    data = minimal_config_dict()
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    data["ledger"]["lockPath"] = str(tmp_path / "ai-logs" / "hermes" / "hermes.lock")
    data["gates"]["preflight"] = []
    if non_executable_docs:
        data["executors"]["claude"] = {
            "provider": "claude",
            "displayName": "Claude CLI",
            "enabled": True,
            "required": False,
            "binary": "claude",
            "env": [],
            "supportsExecute": False,
        }
        data["specialists"]["docs"] = {
            "keywords": ["docs", "readme"],
            "risk": "low",
            "preferredExecutors": ["claude"],
            "fallbackExecutors": ["codex"],
        }
        data["phases"]["production"]["fallbackExecutors"].append("claude")
        data["routing"]["fallbackOrder"].append("claude")
    path = tmp_path / "model-routing.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_gemini_cli_config(tmp_path: Path) -> Path:
    data = minimal_config_dict()
    data["deferredExecutors"].pop("gemini")
    data["executors"]["gemini"] = {
        "provider": "gemini",
        "displayName": "Gemini CLI",
        "enabled": True,
        "required": False,
        "binary": "gemini",
        "env": [],
        "supportsExecute": True,
    }
    data["routing"]["fallbackOrder"].append("gemini")
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


def test_register_hermes_commands_adds_restore_db_task_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_hermes_commands(subparsers)
    args = parser.parse_args(
        [
            "hermes",
            "task",
            "restore-db",
            "--plan-only",
            "--backup",
            "backup.db",
            "--target",
            "signals.db",
        ]
    )

    assert args.command == "hermes"
    assert args.hermes_cmd == "task"
    assert args.task_name == "restore-db"
    assert callable(args.func)


def test_register_hermes_commands_adds_suppression_sync_task_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_hermes_commands(subparsers)
    args = parser.parse_args(
        [
            "hermes",
            "task",
            "suppression-sync",
            "--dry-run",
            "--db-path",
            "signals.db",
            "--ttl-days",
            "14",
        ]
    )

    assert args.command == "hermes"
    assert args.hermes_cmd == "task"
    assert args.task_name == "suppression-sync"
    assert args.db_path == "signals.db"
    assert args.ttl_days == 14
    assert callable(args.func)


def test_register_hermes_commands_adds_governance_task_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_hermes_commands(subparsers)
    args = parser.parse_args(
        [
            "hermes",
            "task",
            "governance",
            "--dry-run",
            "--feature",
            "boilerplate_defense",
            "--from-state",
            "shadow",
            "--target-state",
            "active",
            "--reason",
            "canary stable",
        ]
    )

    assert args.command == "hermes"
    assert args.hermes_cmd == "task"
    assert args.task_name == "governance"
    assert args.feature == "boilerplate_defense"
    assert args.from_state == "shadow"
    assert args.target_state == "active"
    assert args.reason == "canary stable"
    assert callable(args.func)


def test_register_hermes_commands_adds_incident_task_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_hermes_commands(subparsers)
    args = parser.parse_args(
        [
            "hermes",
            "task",
            "incident",
            "--dry-run",
            "--incident-id",
            "github_20260528_010203",
            "--phase-name",
            "freeze",
            "--artifact-root",
            "ops/artifacts/maintenance",
        ]
    )

    assert args.command == "hermes"
    assert args.hermes_cmd == "task"
    assert args.task_name == "incident"
    assert args.incident_id == "github_20260528_010203"
    assert args.incident_phase == "freeze"
    assert args.artifact_root == "ops/artifacts/maintenance"
    assert callable(args.func)


def test_register_hermes_commands_adds_deliberate_task_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    register_hermes_commands(subparsers)
    args = parser.parse_args(
        [
            "hermes",
            "task",
            "deliberate",
            "--dry-run",
            "--task-text",
            "review the Track A plan",
            "--panel",
            "codex,kimi",
            "--rounds",
            "2",
            "--synthesizer",
            "codex",
            "--coding-pair",
        ]
    )

    assert args.command == "hermes"
    assert args.hermes_cmd == "task"
    assert args.task_name == "deliberate"
    assert args.task_text == "review the Track A plan"
    assert args.panel == "codex,kimi"
    assert args.rounds == 2
    assert args.synthesizer == "codex"
    assert args.coding_pair is True
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


def test_suppression_sync_plan_only_cli_writes_ledger_json(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path)
    db_path = tmp_path / "signals.db"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "task",
            "suppression-sync",
            "--plan-only",
            "--json",
            "--config",
            str(config_path),
            "--db-path",
            str(db_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["task"] == "suppression-sync"
    assert payload["status"] == "planned"
    assert payload["plan"]["mutation"]["allowed"] is False
    assert list((tmp_path / "ai-logs" / "hermes" / "runs").iterdir())


def test_route_json_surfaces_non_executable_executor_metadata(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path, non_executable_docs=True)

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
            "update readme",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendedExecutor"] == "claude"
    assert payload["executorMetadata"]["claude"]["supportsExecute"] is False


def test_route_json_cli_supports_gemini_manual_override(tmp_path: Path) -> None:
    config_path = _write_gemini_cli_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "route",
            "--json",
            "--gemini",
            "--config",
            str(config_path),
            "--phase",
            "production",
            "--task",
            "review runbook",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["recommendedExecutor"] == "gemini"
    assert payload["manualModel"] == "gemini"
    assert payload["executorMetadata"]["gemini"]["supportsExecute"] is True
    assert not (tmp_path / "ai-logs").exists()


def test_run_execute_rejects_non_executable_manual_override(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path, non_executable_docs=True)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "run",
            "--execute",
            "--claude",
            "--config",
            str(config_path),
            "--phase",
            "production",
            "--task",
            "update readme",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "does not support execute mode" in result.stderr
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


def test_providers_doctor_json_works_with_read_only_checks(tmp_path: Path) -> None:
    config_path = _write_cli_config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "providers",
            "doctor",
            "--json",
            "--config",
            str(config_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["providers"]["codex"]["provider"] == "codex"


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
