"""Trust-release CLI registration for ops/cli.py

Usage:
    python -m ops.cli trust status
    python -m ops.cli trust status --out docs/plans/2026-06-15-trust-release/status-table.md
    python -m ops.cli trust status --evidence ai-logs/hermes/runs/<run>/ledger_audit_report.json
    python -m ops.cli trust status --live-gh   # network: verify cited run conclusions via gh

Emits the trust-release milestone-status table (markdown) from the declarative
config in ops/trust_release_status.py plus local read-only evidence. No network
access by default; never writes to ai-logs/** or any signals.db.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ops.trust_release_status import build_status_report, render_status_markdown


def _default_repo_root() -> Path:
    # ops/trust_cli.py -> ops/ -> repo root
    return Path(__file__).resolve().parent.parent


def _cmd_status(args: argparse.Namespace) -> None:
    repo_root = Path(getattr(args, "repo_root", None) or _default_repo_root())
    evidence = getattr(args, "evidence", None)
    report = build_status_report(
        repo_root=repo_root,
        evidence_path=Path(evidence) if evidence else None,
        live_gh=bool(getattr(args, "live_gh", False)),
    )
    md = render_status_markdown(report)
    print(md)
    out = getattr(args, "out", None)
    if out:
        Path(out).write_text(md, encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)


def register_trust_commands(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "trust",
        help="Trust-release status tooling (milestone table generator)",
    )
    t = p.add_subparsers(dest="trust_cmd")
    t.required = True

    p_status = t.add_parser(
        "status",
        help="Emit the trust-release milestone-status table (markdown, stdout)",
    )
    p_status.add_argument(
        "--out",
        default=None,
        help="Also write the markdown to this file (UTF-8)",
    )
    p_status.add_argument(
        "--evidence",
        default=None,
        help=(
            "Path to a hermes ledger_audit_report.json to cite (skips scanning "
            "ai-logs/hermes/runs/; input is read-only)"
        ),
    )
    p_status.add_argument(
        "--live-gh",
        dest="live_gh",
        action="store_true",
        default=False,
        help=(
            "Verify cited GitHub Actions run conclusions via the gh CLI "
            "(network access; off by default)"
        ),
    )
    p_status.add_argument(
        "--repo-root",
        dest="repo_root",
        default=None,
        help="Repo root override for evidence checks (defaults to this checkout)",
    )
    p_status.set_defaults(func=_cmd_status)
