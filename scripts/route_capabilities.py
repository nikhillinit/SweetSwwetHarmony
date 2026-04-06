"""
Capability router CLI.

Builds a bounded manifest for local skills, agents, and installed plugins, then
recommends a small set of relevant capabilities for a task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.capability_router import (
    build_capability_manifest,
    default_capability_roots,
    execute_capabilities,
    invoke_capabilities,
    load_manifest,
    recommend_capabilities,
    write_manifest,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route tasks to relevant local skills, agents, and plugins.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build-manifest",
        help="Inventory capability assets and optionally write a manifest JSON file.",
    )
    _add_root_args(build_parser)
    build_parser.add_argument("--output", help="Path to write manifest JSON.")
    build_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print manifest JSON to stdout.",
    )

    recommend_parser = subparsers.add_parser(
        "recommend",
        help="Recommend skills, agents, and plugins for a task.",
    )
    _add_root_args(recommend_parser)
    recommend_parser.add_argument("--manifest", help="Existing manifest JSON to load.")
    recommend_parser.add_argument("--task", required=True, help="Task description to route.")
    recommend_parser.add_argument("--max-skills", type=int, default=3)
    recommend_parser.add_argument("--max-agents", type=int, default=2)
    recommend_parser.add_argument("--max-plugins", type=int, default=2)
    recommend_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print recommendation JSON.",
    )

    invoke_parser = subparsers.add_parser(
        "invoke",
        help="Materialize selected capabilities into skill context, agent prompts, and plugin command suggestions.",
    )
    _add_root_args(invoke_parser)
    invoke_parser.add_argument("--manifest", help="Existing manifest JSON to load.")
    invoke_parser.add_argument("--task", required=True, help="Task description to route.")
    invoke_parser.add_argument(
        "--prompt",
        required=True,
        help="Concrete prompt or operator instruction to combine with the task.",
    )
    invoke_parser.add_argument("--max-skills", type=int, default=3)
    invoke_parser.add_argument("--max-agents", type=int, default=2)
    invoke_parser.add_argument("--max-plugins", type=int, default=2)
    invoke_parser.add_argument("--max-content-chars", type=int, default=4000)
    invoke_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print invocation bundle JSON.",
    )

    execute_parser = subparsers.add_parser(
        "execute",
        help="Produce a policy-vetted execution plan for selected capabilities.",
    )
    _add_root_args(execute_parser)
    execute_parser.add_argument("--manifest", help="Existing manifest JSON to load.")
    execute_parser.add_argument("--task", required=True, help="Task description to route.")
    execute_parser.add_argument(
        "--prompt",
        required=True,
        help="Concrete prompt or operator instruction to combine with the task.",
    )
    execute_parser.add_argument("--max-skills", type=int, default=3)
    execute_parser.add_argument("--max-agents", type=int, default=2)
    execute_parser.add_argument("--max-plugins", type=int, default=2)
    execute_parser.add_argument("--max-content-chars", type=int, default=4000)
    execute_parser.add_argument(
        "--allow-plugin",
        action="append",
        default=[],
        help="Allow a plugin by canonical identity, e.g. plugin:babysitter@a5c.ai.",
    )
    execute_parser.add_argument(
        "--allow-plugin-command",
        action="append",
        default=[],
        help="Allow a specific plugin command by canonical identity, e.g. plugin:babysitter@a5c.ai:plan.",
    )
    execute_parser.add_argument(
        "--allow-plugin-tool",
        action="append",
        default=[],
        help="Approve a declared plugin tool requirement, e.g. Read.",
    )
    execute_parser.add_argument(
        "--allow-all-plugins",
        action="store_true",
        help="Approve all selected plugins for manual invocation review.",
    )
    execute_parser.add_argument(
        "--allow-all-plugin-tools",
        action="store_true",
        help="Approve all declared plugin tool requirements.",
    )
    execute_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print execution plan JSON.",
    )
    return parser


def _add_root_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--user-home",
        help="Override the user home used to resolve ~/.claude roots.",
    )
    parser.add_argument(
        "--repo-root",
        help="Override the repo root used to resolve .claude repo-local roots.",
    )


def _resolve_roots(args: argparse.Namespace):
    user_home = Path(args.user_home).resolve() if args.user_home else None
    repo_root = Path(args.repo_root).resolve() if args.repo_root else None
    return default_capability_roots(user_home=user_home, repo_root=repo_root)


def _print_manifest_summary(manifest) -> None:
    print("Capability manifest built:")
    print(f"  scanned_at: {manifest.scanned_at}")
    print(f"  skills: {manifest.counts.get('skill', 0)}")
    print(f"  agents: {manifest.counts.get('agent', 0)}")
    print(f"  plugins: {manifest.counts.get('plugin', 0)}")
    print("  roots:")
    for root in manifest.roots:
        print(f"    - {root}")


def _print_recommendation_text(recommendation) -> None:
    print(f"Task: {recommendation.task}")
    for label, items in (
        ("Skills", recommendation.skills),
        ("Agents", recommendation.agents),
        ("Plugins", recommendation.plugins),
    ):
        print(f"\n{label}:")
        if not items:
            print("  - none")
            continue
        for item in items:
            print(
                f"  - {item.asset.name} "
                f"[{item.asset.kind}/{item.asset.source}] score={item.score:.2f}"
            )
            print(f"    path: {item.asset.path}")
            if item.asset.summary:
                print(f"    summary: {item.asset.summary}")
            if item.reasons:
                print(f"    why: {', '.join(item.reasons)}")
    print("\nNotes:")
    for note in recommendation.notes:
        print(f"  - {note}")


def _print_invocation_text(bundle) -> None:
    print(bundle.execution_brief)

    print("\nSkills:")
    if not bundle.skills:
        print("  - none")
    else:
        for item in bundle.skills:
            print(f"  - {item.asset.name} [{item.asset.source}]")
            print(f"    path: {item.content_path}")
            if item.reasons:
                print(f"    why: {', '.join(item.reasons)}")

    print("\nAgents:")
    if not bundle.agents:
        print("  - none")
    else:
        for item in bundle.agents:
            print(f"  - {item.asset.name} [{item.asset.source}]")
            print(f"    path: {item.content_path}")
            if item.reasons:
                print(f"    why: {', '.join(item.reasons)}")

    print("\nPlugins:")
    if not bundle.plugins:
        print("  - none")
    else:
        for item in bundle.plugins:
            print(f"  - {item.asset.name} [{item.asset.source}]")
            if item.command:
                print(f"    invoke: {item.command.invocation}")
                print(f"    command_path: {item.command.path}")
                if item.command.reasons:
                    print(f"    why: {', '.join(item.command.reasons)}")
            elif item.reasons:
                print(f"    why: {', '.join(item.reasons)}")

    print("\nNotes:")
    for note in bundle.notes:
        print(f"  - {note}")


def _print_execution_text(plan) -> None:
    print(plan.execution_brief)

    print("\nSkill actions:")
    if not plan.skill_actions:
        print("  - none")
    else:
        for action in plan.skill_actions:
            print(f"  - {action.item.asset.name}: {action.disposition}")
            if action.next_step:
                print(f"    next: {action.next_step}")

    print("\nAgent actions:")
    if not plan.agent_actions:
        print("  - none")
    else:
        for action in plan.agent_actions:
            print(f"  - {action.item.asset.name}: {action.disposition}")
            if action.next_step:
                print(f"    next: {action.next_step}")

    print("\nPlugin actions:")
    if not plan.plugin_actions:
        print("  - none")
    else:
        for action in plan.plugin_actions:
            command = action.item.command.command_name if action.item.command else "none"
            print(
                f"  - {action.item.asset.name}:{command} "
                f"=> {action.disposition}"
            )
            if action.next_step:
                print(f"    next: {action.next_step}")
            if action.policy_notes:
                print(f"    policy: {'; '.join(action.policy_notes)}")

    print("\nNotes:")
    for note in plan.notes:
        print(f"  - {note}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build-manifest":
        manifest = build_capability_manifest(_resolve_roots(args))
        if args.output:
            write_manifest(args.output, manifest)
        if args.as_json:
            print(json.dumps(manifest.to_dict(), indent=2))
        else:
            _print_manifest_summary(manifest)
            if args.output:
                print(f"  wrote: {Path(args.output).resolve()}")
        return 0

    if args.command == "recommend":
        if args.manifest:
            manifest = load_manifest(args.manifest)
        else:
            manifest = build_capability_manifest(_resolve_roots(args))
        recommendation = recommend_capabilities(
            args.task,
            manifest,
            max_skills=args.max_skills,
            max_agents=args.max_agents,
            max_plugins=args.max_plugins,
        )
        if args.as_json:
            print(json.dumps(recommendation.to_dict(), indent=2))
        else:
            _print_recommendation_text(recommendation)
        return 0

    if args.command == "invoke":
        if args.manifest:
            manifest = load_manifest(args.manifest)
        else:
            manifest = build_capability_manifest(_resolve_roots(args))
        bundle = invoke_capabilities(
            args.task,
            args.prompt,
            manifest,
            max_skills=args.max_skills,
            max_agents=args.max_agents,
            max_plugins=args.max_plugins,
            max_content_chars=args.max_content_chars,
        )
        if args.as_json:
            print(json.dumps(bundle.to_dict(), indent=2))
        else:
            _print_invocation_text(bundle)
        return 0

    if args.command == "execute":
        if args.manifest:
            manifest = load_manifest(args.manifest)
        else:
            manifest = build_capability_manifest(_resolve_roots(args))
        plan = execute_capabilities(
            args.task,
            args.prompt,
            manifest,
            max_skills=args.max_skills,
            max_agents=args.max_agents,
            max_plugins=args.max_plugins,
            max_content_chars=args.max_content_chars,
            allow_plugins=args.allow_plugin,
            allow_plugin_commands=args.allow_plugin_command,
            allow_plugin_tools=args.allow_plugin_tool,
            allow_all_plugins=args.allow_all_plugins,
            allow_all_plugin_tools=args.allow_all_plugin_tools,
        )
        if args.as_json:
            print(json.dumps(plan.to_dict(), indent=2))
        else:
            _print_execution_text(plan)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
