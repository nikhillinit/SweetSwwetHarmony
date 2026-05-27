from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from integrations.hermes.config import load_config
from integrations.hermes.locks import HermesLock
from integrations.hermes.run import EXIT_INVALID, run_hermes
from integrations.hermes.router import score_task_for_lane


def register_hermes_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "hermes",
        help="Hermes multi-model routing",
        description="Hermes multi-model routing",
    )
    _register_hermes_subcommands(parser)


def _register_hermes_subcommands(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="hermes_cmd")
    subparsers.required = True

    route = subparsers.add_parser("route", help="Route a task without creating files")
    _add_common_route_args(route)
    route.set_defaults(func=_cmd_route)

    run = subparsers.add_parser("run", help="Run a Hermes routing mode")
    _add_common_route_args(run)
    mode_group = run.add_mutually_exclusive_group()
    mode_group.add_argument("--plan-only", action="store_true", help="Print plan only")
    mode_group.add_argument("--dry-run", action="store_true", help="Write dry-run ledger")
    mode_group.add_argument("--preflight-only", action="store_true", help="Run preflight gates only")
    mode_group.add_argument("--execute", action="store_true", help="Execute selected provider")
    run.add_argument("--ack-risk", default=None, help="Required exact acknowledgement for high-risk execute")
    run.set_defaults(func=_cmd_run)

    providers = subparsers.add_parser("providers", help="Provider diagnostics")
    provider_sub = providers.add_subparsers(dest="providers_cmd")
    provider_sub.required = True
    doctor = provider_sub.add_parser("doctor", help="Check provider availability")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    doctor.add_argument("--strict", action="store_true")
    doctor.add_argument("--config", default=None)
    doctor.set_defaults(func=_cmd_providers_doctor)

    lock = subparsers.add_parser("lock", help="Hermes lock operations")
    lock_sub = lock.add_subparsers(dest="lock_cmd")
    lock_sub.required = True
    force = lock_sub.add_parser("force-unlock", help="Force remove the Hermes lock")
    force.add_argument("--config", default=None)
    force.add_argument("--reason", required=True)
    force.set_defaults(func=_cmd_force_unlock)


def _add_common_route_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None, help="Path to Hermes routing config")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--codex", action="store_true", help="Manually route to Codex")
    parser.add_argument("--kimi", action="store_true", help="Manually route to Kimi")
    parser.add_argument("--claude", action="store_true", help="Manually route to Claude")


def _cmd_route(args: argparse.Namespace) -> None:
    try:
        config = load_config(args.config)
        plan = score_task_for_lane(
            task_text=args.task,
            phase=args.phase,
            config=config,
            manual_model=_manual_model(args),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_INVALID)
    if args.json_output:
        print(json.dumps(plan.to_dict(), indent=2))
        return

    print(f"Recommended executor: {plan.recommended_executor}")
    print(f"Risk: {plan.risk}")
    print(f"Specialist: {plan.specialist or 'none'}")


def _cmd_run(args: argparse.Namespace) -> None:
    mode = _mode(args)
    try:
        result = asyncio.run(
            run_hermes(
                task=args.task,
                phase=args.phase,
                mode=mode,
                config_path=args.config,
                manual_model=_manual_model(args),
                ack_risk=args.ack_risk,
            )
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_INVALID)

    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2))
    elif mode == "plan-only":
        print(f"Recommended executor: {result.plan.recommended_executor}")
        print(f"Risk: {result.plan.risk}")
    elif mode == "dry-run":
        print(f"Dry-run ledger: {result.run_dir}")
    elif mode == "preflight-only":
        print(f"Preflight ledger: {result.run_dir}")
    else:
        print(f"Execute ledger: {result.run_dir}")

    if result.exit_code != 0:
        raise SystemExit(result.exit_code)


def _cmd_providers_doctor(args: argparse.Namespace) -> None:
    try:
        from integrations.hermes.providers import doctor
    except ImportError:
        print("Hermes provider doctor not available yet", file=sys.stderr)
        raise SystemExit(2)

    report = doctor(load_config(args.config), strict=args.strict)
    if args.json_output:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_text())
    if args.strict and not report.success:
        raise SystemExit(2)


def _cmd_force_unlock(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    lock_path = Path(config.ledger.lock_path)
    if not lock_path.is_absolute():
        from integrations.hermes.config import PROJECT_ROOT

        lock_path = PROJECT_ROOT / lock_path
    removed = HermesLock(lock_path).force_unlock(args.reason)
    print("Hermes lock removed" if removed else "No Hermes lock present")


def _manual_model(args: argparse.Namespace) -> str | None:
    selected = [
        name
        for name in ("codex", "kimi", "claude")
        if getattr(args, name, False)
    ]
    if len(selected) > 1:
        raise ValueError("select only one manual executor")
    return selected[0] if selected else None


def _mode(args: argparse.Namespace) -> str:
    if getattr(args, "dry_run", False):
        return "dry-run"
    if getattr(args, "preflight_only", False):
        return "preflight-only"
    if getattr(args, "execute", False):
        return "execute"
    return "plan-only"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Hermes multi-model routing")
    _register_hermes_subcommands(parser)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(EXIT_INVALID)
    except KeyboardInterrupt:
        print("\nOperation cancelled", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
