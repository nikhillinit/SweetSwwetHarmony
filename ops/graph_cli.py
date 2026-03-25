"""
Knowledge Graph CLI registration for ops/cli.py

Usage:
    python -m ops.cli graph --help
    python -m ops.cli graph --db signals.db build [--json]
    python -m ops.cli graph --db signals.db stats
    python -m ops.cli graph --db signals.db validate [--fail-fast] [--json]
    python -m ops.cli graph --db signals.db runs [--limit 20]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def _default_db_path() -> str:
    return os.getenv("DISCOVERY_DB_PATH", "signals.db")


def register_graph_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register knowledge graph CLI subcommands."""
    p = subparsers.add_parser("graph", help="Knowledge Graph operations")
    p.add_argument(
        "--db", dest="db_path", default=_default_db_path(),
        help="Path to signals SQLite DB",
    )
    q = p.add_subparsers(dest="graph_cmd")

    # ------------------------------------------------------------------ build
    p_build = q.add_parser("build", help="Build or refresh the architecture KG layer")
    p_build.add_argument(
        "--repo-root",
        default=None,
        help=(
            "Optional checkout root for AST file reads. Must resolve to the running "
            "checkout; alternate roots are rejected to avoid mixed-repo graphs."
        ),
    )
    p_build.add_argument("--json", dest="json_output", action="store_true")
    p_build.add_argument("--strict-warnings", action="store_true",
                         help="Exit non-zero if the build completes with warnings")
    p_build.set_defaults(func=_cmd_build)

    # ----------------------------------------------------------------- stats
    p_stats = q.add_parser("stats", help="Show graph statistics")
    p_stats.add_argument("--json", dest="json_output", action="store_true")
    p_stats.set_defaults(func=_cmd_stats)

    # -------------------------------------------------------------- validate
    p_val = q.add_parser("validate", help="Run named validation checks")
    p_val.add_argument("--fail-fast", action="store_true",
                       help="Stop on first failure")
    p_val.add_argument("--json", dest="json_output", action="store_true")
    p_val.set_defaults(func=_cmd_validate)

    # ------------------------------------------------------------------ runs
    p_runs = q.add_parser("runs", help="List recent KG runs")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.add_argument("--json", dest="json_output", action="store_true")
    p_runs.set_defaults(func=_cmd_runs)

    p.set_defaults(func=lambda args: p.print_help())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _open_kg_store(db_path: str):
    """Open an aiosqlite connection and return a KGStore."""
    import aiosqlite
    from storage.kg_store import KGStore

    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    store = KGStore(conn)
    await store.recover_stale_runs()
    return store, conn


def _run_async(coro):
    """Run an async coroutine from sync CLI context."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _cmd_build(args: argparse.Namespace) -> None:
    """Build or refresh the v50-compatible architecture graph layer."""
    async def _run():
        from storage.kg_builder import KGArchitectureBuilder
        from storage.signal_store import SignalStore

        store = SignalStore(args.db_path)
        await store.initialize()
        try:
            repo_root = Path(args.repo_root).resolve() if getattr(args, "repo_root", None) else None
            try:
                builder = KGArchitectureBuilder(store._db, repo_root=repo_root)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(2)
            report = await builder.build()

            if getattr(args, "json_output", False):
                print(json.dumps(report.to_dict(), indent=2))
            else:
                print("Knowledge Graph Build")
                print("=" * 40)
                print(f"Run ID: {report.run_id}")
                print(f"Status: {report.status}")
                print(f"Nodes upserted: {report.nodes_upserted}")
                print(f"Edges upserted: {report.edges_upserted}")
                print(f"Nodes tombstoned: {report.nodes_tombstoned}")
                print(f"Edges expired: {report.edges_expired}")
                if report.details:
                    print("\nBuild details:")
                    for key, value in sorted(report.details.items()):
                        print(f"  {key}: {value}")
                if report.source_rows:
                    print("\nSources:")
                    for source_name, stats in sorted(report.source_rows.items()):
                        print(
                            f"  {source_name}: "
                            f"{stats['nodes']} nodes, {stats['edges']} edges, "
                            f"{stats['evidence_rows']} evidence rows"
                        )
                if report.warnings:
                    print("\nWarnings:")
                    for warning in report.warnings:
                        print(f"  - {warning}")

            if getattr(args, "strict_warnings", False) and report.warnings:
                sys.exit(2)
        finally:
            await store.close()

    _run_async(_run())


def _cmd_stats(args: argparse.Namespace) -> None:
    """Display graph statistics."""
    async def _run():
        store, conn = await _open_kg_store(args.db_path)
        try:
            stats = await store.get_stats()
            if getattr(args, "json_output", False):
                data = {
                    "total_nodes": stats.total_nodes,
                    "live_nodes": stats.live_nodes,
                    "tombstoned_nodes": stats.tombstoned_nodes,
                    "total_edges": stats.total_edges,
                    "live_edges": stats.live_edges,
                    "expired_edges": stats.expired_edges,
                    "nodes_by_type": stats.nodes_by_type,
                    "edges_by_type": stats.edges_by_type,
                    "total_runs": stats.total_runs,
                    "last_run": stats.last_run,
                }
                print(json.dumps(data, indent=2))
            else:
                print("Knowledge Graph Statistics")
                print("=" * 40)
                print(f"Nodes: {stats.live_nodes} live, {stats.tombstoned_nodes} tombstoned ({stats.total_nodes} total)")
                print(f"Edges: {stats.live_edges} live, {stats.expired_edges} expired ({stats.total_edges} total)")
                print(f"Runs:  {stats.total_runs}")
                if stats.nodes_by_type:
                    print("\nNodes by type:")
                    for nt, count in sorted(stats.nodes_by_type.items()):
                        print(f"  {nt}: {count}")
                if stats.edges_by_type:
                    print("\nEdges by type (live):")
                    for et, count in sorted(stats.edges_by_type.items()):
                        print(f"  {et}: {count}")
                if stats.last_run:
                    lr = stats.last_run
                    print(f"\nLast run: {lr['run_id']} ({lr['mode']}, {lr['status']})")
                    print(f"  Started: {lr['started_at']}")
                    if lr.get("completed_at"):
                        print(f"  Completed: {lr['completed_at']}")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_validate(args: argparse.Namespace) -> None:
    """Run named validation checks."""
    async def _run():
        store, conn = await _open_kg_store(args.db_path)
        try:
            results = await store.validate(
                fail_fast=getattr(args, "fail_fast", False),
            )
            if getattr(args, "json_output", False):
                print(json.dumps([r.to_dict() for r in results], indent=2))
            else:
                all_pass = True
                for r in results:
                    status_icon = "PASS" if r.status == "pass" else "FAIL"
                    line = f"  [{status_icon}] {r.check}"
                    if r.details:
                        line += f" — {r.details}"
                    print(line)
                    if r.status != "pass":
                        all_pass = False
                print()
                if all_pass:
                    print("All checks passed.")
                else:
                    print("Some checks failed.")
                    sys.exit(1)
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_runs(args: argparse.Namespace) -> None:
    """List recent KG runs."""
    async def _run():
        store, conn = await _open_kg_store(args.db_path)
        try:
            runs = await store.list_runs(limit=args.limit)
            if getattr(args, "json_output", False):
                data = []
                for r in runs:
                    data.append({
                        "run_id": r.run_id,
                        "mode": r.mode,
                        "started_at": r.started_at,
                        "completed_at": r.completed_at,
                        "status": r.status,
                        "nodes_upserted": r.nodes_upserted,
                        "edges_upserted": r.edges_upserted,
                        "nodes_tombstoned": r.nodes_tombstoned,
                        "edges_expired": r.edges_expired,
                    })
                print(json.dumps(data, indent=2))
            else:
                if not runs:
                    print("No KG runs found.")
                    return
                print(f"{'Run ID':<18} {'Mode':<14} {'Status':<12} {'Nodes':<8} {'Edges':<8} {'Started'}")
                print("-" * 90)
                for r in runs:
                    print(
                        f"{r.run_id:<18} {r.mode:<14} {r.status:<12} "
                        f"{r.nodes_upserted:<8} {r.edges_upserted:<8} "
                        f"{r.started_at}"
                    )
        finally:
            await conn.close()

    _run_async(_run())
