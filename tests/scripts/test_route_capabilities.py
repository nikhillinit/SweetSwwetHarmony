"""Tests for scripts/route_capabilities.py."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.route_capabilities import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_plugin(
    plugin_root: Path,
    *,
    plugin_id: str,
    display_name: str,
    command_name: str,
    command_description: str,
    allowed_tools: list[str],
    version: str,
) -> None:
    marketplace = plugin_id.split("@", 1)[1]
    install_slug = plugin_id.split("@", 1)[0]
    install_path = plugin_root / "cache" / marketplace.replace(".", "-") / install_slug / version
    _write(
        install_path / "plugin.json",
        json.dumps(
            {
                "name": display_name,
                "description": "Workflow orchestration with human approval.",
                "keywords": ["workflow", "orchestration", "approval"],
                "skills": [{"name": display_name}],
                "commands": [{"name": command_name}],
            }
        ),
    )
    _write(
        install_path / "commands" / f"{command_name}.md",
        "---\n"
        f"description: {command_description}\n"
        f"allowed-tools: {', '.join(allowed_tools)}\n"
        "---\n"
        f"# {command_name}\n"
        f"{command_description}\n",
    )
    installed_manifest = plugin_root / "installed_plugins.json"
    payload = {"version": 2, "plugins": {}}
    if installed_manifest.exists():
        payload = json.loads(installed_manifest.read_text(encoding="utf-8"))
    payload.setdefault("plugins", {})[plugin_id] = [
        {
            "scope": "user",
            "installPath": str(install_path),
            "version": version,
            "installedAt": "2026-03-10T03:52:24.175Z",
            "lastUpdated": "2026-03-10T03:52:24.175Z",
        }
    ]
    _write(installed_manifest, json.dumps(payload))


def _build_capability_fixture(tmp_path: Path):
    user_home = tmp_path / "user-home"
    repo_root = tmp_path / "repo-root"

    _write(
        user_home / ".claude" / "skills" / "docs-architect.md",
        "# Docs Architect\nWrite durable documentation.\n",
    )
    _write(
        user_home / ".claude" / "agents" / "code-reviewer.md",
        "# Code Reviewer\nFind bugs and regressions.\n",
    )
    plugin_root = user_home / ".claude" / "plugins"
    _write_plugin(
        plugin_root,
        plugin_id="babysitter@a5c.ai",
        display_name="babysitter",
        command_name="plan",
        command_description="Build a gated workflow plan.",
        allowed_tools=["planner", "approval"],
        version="4.0.149",
    )
    _write(
        repo_root / ".claude" / "skills" / "knowledge-graph-builder" / "SKILL.md",
        "---\nname: knowledge-graph-builder\ndescription: Build KG ETL pipelines.\n---\n",
    )
    _write(
        repo_root / ".claude" / "agents" / "sqlite-expert.md",
        "# sqlite-expert\nSQLite safety and migrations.\n",
    )
    return user_home, repo_root


@pytest.fixture
def capability_fixture(tmp_path):
    return _build_capability_fixture(tmp_path)


@pytest.fixture
def duplicate_plugin_fixture(tmp_path):
    user_home, repo_root = _build_capability_fixture(tmp_path)
    plugin_root = user_home / ".claude" / "plugins"
    _write_plugin(
        plugin_root,
        plugin_id="babysitter@shadow.ai",
        display_name="babysitter",
        command_name="deploy",
        command_description="Deploy a workflow plan after review.",
        allowed_tools=[],
        version="1.2.0",
    )
    return user_home, repo_root


class TestRouteCapabilitiesCli:
    def test_build_manifest_writes_json(self, capability_fixture, tmp_path):
        user_home, repo_root = capability_fixture
        out_path = tmp_path / "manifest.json"

        rc = main(
            [
                "build-manifest",
                "--user-home",
                str(user_home),
                "--repo-root",
                str(repo_root),
                "--output",
                str(out_path),
            ]
        )

        assert rc == 0
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert data["counts"]["plugin"] == 1
        assert any(asset["name"] == "knowledge-graph-builder" for asset in data["assets"])

    def test_recommend_json_uses_manifest(self, capability_fixture, tmp_path, capsys):
        user_home, repo_root = capability_fixture
        manifest_path = tmp_path / "manifest.json"
        rc = main(
            [
                "build-manifest",
                "--user-home",
                str(user_home),
                "--repo-root",
                str(repo_root),
                "--output",
                str(manifest_path),
            ]
        )
        assert rc == 0
        capsys.readouterr()

        rc = main(
            [
                "recommend",
                "--manifest",
                str(manifest_path),
                "--task",
                "Need workflow orchestration plus KG ETL safety",
                "--json",
            ]
        )

        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert any(item["asset"]["name"] == "knowledge-graph-builder" for item in data["skills"])
        assert any(item["asset"]["name"] == "babysitter" for item in data["plugins"])

    def test_invoke_json_materializes_commands_and_context(self, capability_fixture, capsys):
        user_home, repo_root = capability_fixture

        rc = main(
            [
                "invoke",
                "--user-home",
                str(user_home),
                "--repo-root",
                str(repo_root),
                "--task",
                "Need workflow orchestration plus KG ETL safety",
                "--prompt",
                "Load the right assets and propose the best plugin command.",
                "--json",
            ]
        )

        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        assert any(item["asset"]["name"] == "knowledge-graph-builder" for item in data["skills"])
        assert any(item["asset"]["name"] == "sqlite-expert" for item in data["agents"])
        plugin_items = [item for item in data["plugins"] if item["asset"]["name"] == "babysitter"]
        assert plugin_items
        assert plugin_items[0]["command"]["command_name"] == "plan"

    def test_execute_json_applies_policy_to_plugin_command(self, capability_fixture, capsys):
        user_home, repo_root = capability_fixture

        rc = main(
            [
                "execute",
                "--user-home",
                str(user_home),
                "--repo-root",
                str(repo_root),
                "--task",
                "Need workflow orchestration plus KG ETL safety",
                "--prompt",
                "Load the right assets and propose the best plugin command.",
                "--allow-plugin-tool",
                "planner",
                "--allow-plugin-tool",
                "approval",
                "--json",
            ]
        )

        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        plugin_actions = [
            item for item in data["plugin_actions"] if item["item"]["asset"]["name"] == "babysitter"
        ]
        assert plugin_actions
        assert plugin_actions[0]["disposition"] == "approved_manual_invoke"
        assert plugin_actions[0]["item"]["command"]["command_name"] == "plan"

    def test_execute_json_uses_canonical_plugin_command_identity(self, duplicate_plugin_fixture, capsys):
        user_home, repo_root = duplicate_plugin_fixture

        rc = main(
            [
                "execute",
                "--user-home",
                str(user_home),
                "--repo-root",
                str(repo_root),
                "--task",
                "Need workflow orchestration plus KG ETL safety",
                "--prompt",
                "Load the right assets and propose the best plugin command.",
                "--max-plugins",
                "2",
                "--allow-plugin-command",
                "plugin:babysitter@shadow.ai:deploy",
                "--json",
            ]
        )

        assert rc == 0
        output = capsys.readouterr().out
        data = json.loads(output)
        plugin_actions = {item["item"]["asset"]["id"]: item for item in data["plugin_actions"]}
        assert plugin_actions["plugin:babysitter@shadow.ai"]["disposition"] == "approved_manual_invoke"
        assert plugin_actions["plugin:babysitter@shadow.ai"]["item"]["command"]["command_name"] == "deploy"
        assert plugin_actions["plugin:babysitter@a5c.ai"]["disposition"] == "manual_review_required"
