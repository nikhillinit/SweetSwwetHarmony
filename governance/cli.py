"""Governance CLI — manage feature lifecycle events.

Usage:
    python -m governance feature promote DELIVERY_MODE \
        --from manual_publish --to batch_publish \
        --regret-check-date 2026-03-30 \
        --reason "Step 4A promotion — canary stable for 48h"

    python -m governance feature promote boilerplate_defense \
        --from shadow --to active \
        --reason "Canary stable for 48h"

    python -m governance feature regret-check DELIVERY_MODE \
        --verdict pass --canary-verdict pass --drift-status in_control \
        --reason "No regressions in 14-day window"

    python -m governance feature demote boilerplate_defense \
        --from active --to shadow \
        --reason "FP rate increased"

Transport:
    If DISCOVERY_API_URL is set (and DISCOVERY_API_TOKEN present),
    events are sent via the governance API.
    Otherwise, direct DB write via SignalStore.

    --direct-db PATH forces direct DB write, bypassing DISCOVERY_API_URL.

Actor resolution:
    GOV_ACTOR_ID env var > git user.email > OS username
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _resolve_actor_id() -> str:
    """Resolve actor identity for CLI governance events."""
    actor = os.environ.get("GOV_ACTOR_ID")
    if actor:
        return actor

    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"cli:{result.stdout.strip()}"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return f"cli:{getpass.getuser()}"


def _resolve_actor_email(actor_id: str) -> str:
    """Extract email from actor_id if available."""
    if actor_id.startswith("cli:") and "@" in actor_id:
        return actor_id[4:]
    return actor_id


def _build_operator(actor_id: str):
    """Build an OperatorContext for CLI use (includes correlation ID)."""
    from api.auth.jwt_auth import Role
    from api.auth.rbac import OperatorContext

    return OperatorContext(
        user_id=actor_id,
        email=_resolve_actor_email(actor_id),
        role=Role.GP,
        name=actor_id,
        request_id=f"gov-cli-{uuid.uuid4().hex[:12]}",
    )


def resolve_db_path_for_governance(
    direct_db: str | None = None,
) -> str:
    """Resolve DB path for governance direct-write.

    Uses --direct-db if provided, else DISCOVERY_DB_PATH env var.
    Requires the file to exist and be readable — never creates a new DB.

    Raises SystemExit on failure (fail-closed).
    """
    db_path = direct_db or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    if not os.path.isfile(db_path):
        print(f"Error: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)
    if not os.access(db_path, os.R_OK):
        print(f"Error: DB not readable: {db_path}", file=sys.stderr)
        sys.exit(1)
    return db_path


def _use_api(args: argparse.Namespace) -> tuple[str, str] | None:
    """Check if API transport should be used.

    Returns (api_url, api_token) or None for direct DB write.
    Exits non-zero if DISCOVERY_API_URL is set without DISCOVERY_API_TOKEN.
    """
    if getattr(args, "direct_db", None):
        return None

    api_url = os.environ.get("DISCOVERY_API_URL")
    api_token = os.environ.get("DISCOVERY_API_TOKEN")

    if api_url and not api_token:
        print(
            "Error: DISCOVERY_API_URL is set but DISCOVERY_API_TOKEN is missing.",
            file=sys.stderr,
        )
        sys.exit(1)

    if api_url and api_token:
        return (api_url, api_token)
    return None


async def _cmd_promote(args: argparse.Namespace) -> None:
    """Execute feature promote."""
    from governance.state_policies import (
        GovernanceStatePolicyError,
        _ENV_BACKED_FLAGS,
        allowed_states_for_flag,
    )
    from monitoring.feature_gate import compute_config_snapshot

    actor_id = _resolve_actor_id()
    operator = _build_operator(actor_id)

    # Compute snapshot with override for env-backed flags
    is_env_backed = args.flag in _ENV_BACKED_FLAGS
    if is_env_backed:
        snapshot = compute_config_snapshot(overrides={args.flag: args.to_state})
    else:
        snapshot = compute_config_snapshot()

    regret_due_at = args.regret_check_date or (
        datetime.now(timezone.utc) + timedelta(days=14)
    ).strftime("%Y-%m-%d")

    api_creds = _use_api(args)
    if api_creds:
        api_url, api_token = api_creds
        effective_at = getattr(args, "effective_at", None)
        repair_source = getattr(args, "repair_source", None)
        try:
            await _send_via_api(api_url, api_token, operator.request_id, {
                "feature_name": args.flag,
                "reason": args.reason,
                "metadata": {
                    "action_type": "feature_promote",
                    "feature_name": args.flag,
                    "from_state": args.from_state,
                    "to_state": args.to_state,
                    "regret_due_at": regret_due_at,
                    "config_snapshot_hash": snapshot["hash"],
                    "config_snapshot_flags": snapshot["flags"],
                    "effective_at": effective_at,
                    "repair_source": repair_source,
                },
            })
        except _ApiError:
            sys.exit(1)
    else:
        from governance.writer import record_feature_promote
        from storage.signal_store import SignalStore

        db_path = resolve_db_path_for_governance(
            getattr(args, "direct_db", None),
        )
        effective_at = getattr(args, "effective_at", None)
        repair_source = getattr(args, "repair_source", None)
        store = SignalStore(db_path=db_path)
        await store.initialize()
        try:
            event_id = await record_feature_promote(
                store, operator,
                feature_name=args.flag,
                from_state=args.from_state,
                to_state=args.to_state,
                regret_due_at=regret_due_at,
                reason=args.reason,
                config_snapshot_hash=snapshot["hash"],
                config_snapshot_flags=snapshot["flags"],
                effective_at=effective_at,
                repair_source=repair_source,
            )
            print(json.dumps({
                "event_id": event_id, "action": "feature_promote",
                "feature": args.flag, "correlation_id": operator.request_id,
            }, indent=2))
        finally:
            await store.close()


async def _cmd_regret_check(args: argparse.Namespace) -> None:
    """Execute regret check."""
    actor_id = _resolve_actor_id()
    operator = _build_operator(actor_id)

    api_creds = _use_api(args)
    if api_creds:
        api_url, api_token = api_creds
        try:
            await _send_via_api(api_url, api_token, operator.request_id, {
                "feature_name": args.flag,
                "reason": args.reason,
                "metadata": {
                    "action_type": "regret_check",
                    "verdict": args.verdict,
                    "canary_verdict": args.canary_verdict,
                    "drift_status": args.drift_status,
                    "window_days": args.window_days,
                },
            })
        except _ApiError:
            sys.exit(1)
    else:
        from governance.writer import record_regret_check
        from storage.signal_store import SignalStore

        db_path = resolve_db_path_for_governance(
            getattr(args, "direct_db", None),
        )
        store = SignalStore(db_path=db_path)
        await store.initialize()
        try:
            event_id = await record_regret_check(
                store, operator,
                feature_name=args.flag,
                verdict=args.verdict,
                canary_verdict=args.canary_verdict,
                drift_status=args.drift_status,
                reason=args.reason,
                window_days=args.window_days,
            )
            print(json.dumps({
                "event_id": event_id, "action": "regret_check",
                "feature": args.flag, "correlation_id": operator.request_id,
            }, indent=2))
        finally:
            await store.close()


async def _cmd_demote(args: argparse.Namespace) -> None:
    """Execute feature demote."""
    actor_id = _resolve_actor_id()
    operator = _build_operator(actor_id)

    api_creds = _use_api(args)
    if api_creds:
        api_url, api_token = api_creds
        try:
            await _send_via_api(api_url, api_token, operator.request_id, {
                "feature_name": args.flag,
                "reason": args.reason,
                "metadata": {
                    "action_type": "feature_demote",
                    "from_state": args.from_state,
                    "to_state": args.to_state,
                    "rollback_ticket": args.rollback_ticket,
                    "incident_id": args.incident_id,
                },
            })
        except _ApiError:
            sys.exit(1)
    else:
        from governance.writer import record_feature_demote
        from storage.signal_store import SignalStore

        db_path = resolve_db_path_for_governance(
            getattr(args, "direct_db", None),
        )
        store = SignalStore(db_path=db_path)
        await store.initialize()
        try:
            event_id = await record_feature_demote(
                store, operator,
                feature_name=args.flag,
                from_state=args.from_state,
                to_state=args.to_state,
                reason=args.reason,
                rollback_ticket=args.rollback_ticket,
                incident_id=args.incident_id,
            )
            print(json.dumps({
                "event_id": event_id, "action": "feature_demote",
                "feature": args.flag, "correlation_id": operator.request_id,
            }, indent=2))
        finally:
            await store.close()


class _ApiError(Exception):
    """Sentinel for handled API errors."""


async def _send_via_api(
    base_url: str, token: str, request_id: str, payload: dict,
) -> None:
    """Send governance event via HTTP API with auth + correlation."""
    import httpx

    url = f"{base_url.rstrip('/')}/api/v1/governance/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": request_id,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url, json=payload, headers=headers, timeout=30,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Try JSON detail extraction for 422 errors
            detail = ""
            try:
                body = exc.response.json()
                detail = body.get("detail", body.get("message", ""))
                if isinstance(detail, list):
                    detail = "; ".join(
                        d.get("msg", str(d)) for d in detail
                    )
            except Exception:
                detail = exc.response.text[:200]
            print(
                f"Error: API {exc.response.status_code}: {detail}",
                file=sys.stderr,
            )
            raise _ApiError() from exc
        print(json.dumps(resp.json(), indent=2))


def _add_feature_subcommands(feature_sub) -> None:
    """Add promote/regret-check/demote subcommands."""
    # promote
    p_promote = feature_sub.add_parser("promote", help="Promote a feature flag")
    p_promote.add_argument("flag", help="Feature flag name")
    p_promote.add_argument(
        "--from", dest="from_state", required=True,
    )
    p_promote.add_argument(
        "--to", dest="to_state", required=True,
    )
    p_promote.add_argument(
        "--regret-check-date", default=None,
        help="YYYY-MM-DD for regret check (default: +14 days)",
    )
    p_promote.add_argument(
        "--effective-at",
        default=None,
        help=(
            "ISO 8601 timestamp for the actual promotion time when "
            "recording a retroactive repair"
        ),
    )
    p_promote.add_argument(
        "--repair-source",
        default=None,
        help="Artifact or note that justifies a retroactive promotion repair",
    )
    p_promote.add_argument("--reason", required=True)
    p_promote.add_argument(
        "--direct-db", dest="direct_db", default=None,
        help="Break-glass: direct DB path, bypasses DISCOVERY_API_URL",
    )
    p_promote.set_defaults(func=lambda args: asyncio.run(_cmd_promote(args)))

    # regret-check
    p_regret = feature_sub.add_parser(
        "regret-check", help="Record a regret check"
    )
    p_regret.add_argument("flag", help="Feature flag name")
    p_regret.add_argument(
        "--verdict", required=True, choices=["pass", "fail"],
    )
    p_regret.add_argument(
        "--canary-verdict", required=True,
        choices=["pass", "fail", "no_data"],
    )
    p_regret.add_argument(
        "--drift-status", required=True,
        choices=["in_control", "warning", "critical", "no_data"],
    )
    p_regret.add_argument("--reason", required=True)
    p_regret.add_argument("--window-days", type=int, default=14)
    p_regret.add_argument(
        "--direct-db", dest="direct_db", default=None,
        help="Break-glass: direct DB path, bypasses DISCOVERY_API_URL",
    )
    p_regret.set_defaults(
        func=lambda args: asyncio.run(_cmd_regret_check(args))
    )

    # demote
    p_demote = feature_sub.add_parser("demote", help="Demote a feature flag")
    p_demote.add_argument("flag", help="Feature flag name")
    p_demote.add_argument(
        "--from", dest="from_state", required=True,
    )
    p_demote.add_argument(
        "--to", dest="to_state", required=True,
    )
    p_demote.add_argument("--reason", required=True)
    p_demote.add_argument("--rollback-ticket", default=None)
    p_demote.add_argument("--incident-id", default=None)
    p_demote.add_argument(
        "--direct-db", dest="direct_db", default=None,
        help="Break-glass: direct DB path, bypasses DISCOVERY_API_URL",
    )
    p_demote.set_defaults(
        func=lambda args: asyncio.run(_cmd_demote(args))
    )


def register_governance_commands(subparsers) -> None:
    """Register governance subcommands under `python -m ops.cli governance ...`"""
    gov_parser = subparsers.add_parser(
        "governance", help="Feature governance lifecycle commands"
    )
    gov_sub = gov_parser.add_subparsers(dest="governance_cmd")
    _add_feature_subcommands(gov_sub)


def _cli_main() -> None:
    """CLI entrypoint: python -m governance"""
    sys.stdout.reconfigure(errors="replace")

    from governance.state_policies import GovernanceStatePolicyError

    parser = argparse.ArgumentParser(description="Feature governance CLI")
    sub = parser.add_subparsers(dest="command")

    feature_parser = sub.add_parser(
        "feature", help="Feature lifecycle commands"
    )
    feature_sub = feature_parser.add_subparsers(dest="feature_cmd")
    _add_feature_subcommands(feature_sub)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except GovernanceStatePolicyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Surface pydantic ValidationError cleanly
        from pydantic import ValidationError
        if isinstance(exc.__context__, ValidationError) or isinstance(exc, ValidationError):
            ve = exc.__context__ if isinstance(exc.__context__, ValidationError) else exc
            print(f"Error: {ve}", file=sys.stderr)
            sys.exit(1)
        raise


if __name__ == "__main__":
    _cli_main()
