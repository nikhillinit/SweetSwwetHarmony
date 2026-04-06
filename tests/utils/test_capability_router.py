"""Tests for utils.capability_router."""

from __future__ import annotations

import json
from pathlib import Path

from utils.capability_router import (
    build_capability_manifest,
    default_capability_roots,
    execute_capabilities,
    invoke_capabilities,
    recommend_capabilities,
)


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
                "keywords": ["workflow", "orchestration", "approval", "agent"],
                "skills": [{"name": display_name}],
                "hooks": {"SessionStart": "hooks/start.sh"},
                "commands": [{"name": command_name}],
            }
        ),
    )
    tools_line = ", ".join(allowed_tools)
    _write(
        install_path / "commands" / f"{command_name}.md",
        "---\n"
        f"description: {command_description}\n"
        f"allowed-tools: {tools_line}\n"
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


def _make_roots(tmp_path: Path, *, include_duplicate_named_plugin: bool = False):
    user_home = tmp_path / "user-home"
    repo_root = tmp_path / "repo-root"

    _write(
        user_home / ".claude" / "skills" / "docs-architect.md",
        "# Docs Architect\nWrite durable documentation.\n",
    )
    _write(
        user_home / ".claude" / "skills" / "python-testing-patterns.md",
        "---\nname: python-testing-patterns\ndescription: Generic Python test design.\n---\n",
    )
    _write(
        user_home / ".claude" / "skills" / "cross-pollination-engine" / "SKILL.md",
        "---\nname: cross-pollination-engine\ndescription: Cross-domain ideation.\n---\n",
    )
    _write(
        user_home / ".claude" / "agents" / "code-reviewer.md",
        "# Code Reviewer\nFind bugs, regressions, and missing tests.\n",
    )

    plugin_root = user_home / ".claude" / "plugins"
    _write_plugin(
        plugin_root,
        plugin_id="babysitter@a5c.ai",
        display_name="babysitter",
        command_name="plan",
        command_description="Build a gated plan for orchestrated work.",
        allowed_tools=["planner", "approval"],
        version="4.0.149",
    )
    if include_duplicate_named_plugin:
        _write_plugin(
            plugin_root,
            plugin_id="babysitter@shadow.ai",
            display_name="babysitter",
            command_name="deploy",
            command_description="Deploy a workflow plan after review.",
            allowed_tools=[],
            version="1.2.0",
        )
    _write(
        plugin_root / "cache" / "a5c-ai" / "babysitter" / "4.0.149" / "node_modules" / "ignored" / "package.json",
        json.dumps({"name": "ignored"}),
    )

    _write(
        repo_root / ".claude" / "skills" / "knowledge-graph-builder" / "SKILL.md",
        "---\nname: knowledge-graph-builder\ndescription: Build and validate KG ETL pipelines.\n---\n",
    )
    _write(
        repo_root / ".claude" / "skills" / "python-testing-patterns.md",
        "---\nname: python-testing-patterns\ndescription: Repo-specific Streamlit and pytest patterns.\n---\n",
    )
    _write(
        repo_root / ".claude" / "agents" / "sqlite-expert.md",
        "# sqlite-expert\nSQLite safety, migrations, and snapshot handling.\n",
    )
    return default_capability_roots(user_home=user_home, repo_root=repo_root)


class TestBuildCapabilityManifest:
    def test_builds_skill_agent_plugin_manifest(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        assert manifest.counts["skill"] == 5
        assert manifest.counts["agent"] == 2
        assert manifest.counts["plugin"] == 1

        names = {asset.name for asset in manifest.assets}
        assert "Docs Architect" in names
        assert "knowledge-graph-builder" in names
        assert "Code Reviewer" in names
        assert "sqlite-expert" in names
        assert "babysitter" in names

    def test_plugin_inventory_is_bounded_to_plugin_manifest(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))
        plugin_assets = [asset for asset in manifest.assets if asset.kind == "plugin"]

        assert len(plugin_assets) == 1
        assert "node_modules" not in plugin_assets[0].path
        assert plugin_assets[0].metadata["commands"] == ["plan"]


class TestRecommendCapabilities:
    def test_prefers_repo_specific_skill_on_duplicate_name(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        recommendation = recommend_capabilities(
            "Need python testing patterns for Streamlit dashboard tests",
            manifest,
        )

        assert recommendation.skills
        assert recommendation.skills[0].asset.name == "python-testing-patterns"
        assert recommendation.skills[0].asset.source == "repo"

    def test_routes_kg_and_sqlite_task_to_repo_assets(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        recommendation = recommend_capabilities(
            "Add KG ETL validation with sqlite safety checks",
            manifest,
        )

        skill_names = [item.asset.name for item in recommendation.skills]
        agent_names = [item.asset.name for item in recommendation.agents]
        assert "knowledge-graph-builder" in skill_names
        assert "sqlite-expert" in agent_names

    def test_recommends_plugin_for_workflow_orchestration(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        recommendation = recommend_capabilities(
            "Need workflow orchestration with human approval checkpoints",
            manifest,
        )

        plugin_names = [item.asset.name for item in recommendation.plugins]
        assert "babysitter" in plugin_names

    def test_duplicate_named_plugins_are_not_deduped_by_display_name(self, tmp_path):
        manifest = build_capability_manifest(
            _make_roots(tmp_path, include_duplicate_named_plugin=True)
        )

        recommendation = recommend_capabilities(
            "Need workflow orchestration with human approval checkpoints",
            manifest,
            max_plugins=2,
        )

        assert len(recommendation.plugins) == 2
        assert {item.asset.id for item in recommendation.plugins} == {
            "plugin:babysitter@a5c.ai",
            "plugin:babysitter@shadow.ai",
        }


class TestInvokeCapabilities:
    def test_materializes_skill_and_agent_content(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        bundle = invoke_capabilities(
            "Add KG ETL validation with sqlite safety checks",
            "Use repo-specific guidance and provide implementation context.",
            manifest,
        )

        assert bundle.skills
        assert any(item.asset.name == "knowledge-graph-builder" for item in bundle.skills)
        assert any("Build and validate KG ETL pipelines." in (item.content or "") for item in bundle.skills)
        assert bundle.agents
        assert any(item.asset.name == "sqlite-expert" for item in bundle.agents)
        assert any("SQLite safety" in (item.content or "") for item in bundle.agents)

    def test_selects_plugin_command_for_workflow_prompt(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        bundle = invoke_capabilities(
            "Need workflow orchestration with human approval checkpoints",
            "Plan the work, route approvals, and keep humans in the loop.",
            manifest,
        )

        plugin_items = [item for item in bundle.plugins if item.asset.name == "babysitter"]
        assert plugin_items
        command = plugin_items[0].command
        assert command is not None
        assert command.command_name == "plan"
        assert command.invocation.startswith("/babysitter:plan ")
        assert "workflow-oriented command" in command.reasons


class TestExecuteCapabilities:
    def test_safe_plugin_command_requires_tool_review_by_default(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        plan = execute_capabilities(
            "Need workflow orchestration with human approval checkpoints",
            "Plan the work and keep humans in the loop.",
            manifest,
        )

        plugin_actions = [item for item in plan.plugin_actions if item.item.asset.name == "babysitter"]
        assert plugin_actions
        assert plugin_actions[0].disposition == "manual_review_required"
        assert any("Missing tool approvals" in note for note in plugin_actions[0].policy_notes)

    def test_explicit_tool_approval_promotes_plugin_to_approved_manual_invoke(self, tmp_path):
        manifest = build_capability_manifest(_make_roots(tmp_path))

        plan = execute_capabilities(
            "Need workflow orchestration with human approval checkpoints",
            "Plan the work and keep humans in the loop.",
            manifest,
            allow_plugin_tools=["planner", "approval"],
        )

        plugin_actions = [item for item in plan.plugin_actions if item.item.asset.name == "babysitter"]
        assert plugin_actions
        assert plugin_actions[0].disposition == "approved_manual_invoke"
        assert plugin_actions[0].next_step.startswith("/babysitter:plan ")

    def test_canonical_plugin_command_allowlist_does_not_cross_approve_same_name_plugins(self, tmp_path):
        manifest = build_capability_manifest(
            _make_roots(tmp_path, include_duplicate_named_plugin=True)
        )

        plan = execute_capabilities(
            "Need workflow orchestration with human approval checkpoints",
            "Plan the work and keep humans in the loop.",
            manifest,
            max_plugins=2,
            allow_plugin_commands=["plugin:babysitter@shadow.ai:deploy"],
        )

        actions_by_id = {
            action.item.asset.id: action
            for action in plan.plugin_actions
        }
        assert actions_by_id["plugin:babysitter@shadow.ai"].disposition == "approved_manual_invoke"
        assert actions_by_id["plugin:babysitter@shadow.ai"].item.command.command_name == "deploy"
        assert actions_by_id["plugin:babysitter@a5c.ai"].disposition == "manual_review_required"
