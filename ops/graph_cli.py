"""
Knowledge Graph CLI registration for ops/cli.py

Usage:
    python -m ops.cli graph --help
    python -m ops.cli graph --db signals.db build [--json]
    python -m ops.cli graph --db signals.db stats
    python -m ops.cli graph --db signals.db validate [--fail-fast] [--json]
    python -m ops.cli graph --db signals.db runs [--limit 20]
    python -m ops.cli graph --db signals.db etl --mode full [--dry-run] [--json]
    python -m ops.cli graph --db signals.db etl-status [--json]
    python -m ops.cli graph --db signals.db evidence <company_id> [--json]
    python -m ops.cli graph --db signals.db gaps --min-evidence 2 [--json]
    python -m ops.cli graph --db signals.db conflicts [--json]
    python -m ops.cli graph --db signals.db sector <sector_id> [--json]
    python -m ops.cli graph --db signals.db duplicates [--json]
    python -m ops.cli graph --db signals.db rank --min-sources 2 [--json]
    python -m ops.cli graph --db signals.db ego <node_id> --depth 2 [--out ego.json]
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

    # ------------------------------------------------------------------- etl
    p_etl = q.add_parser("etl", help="Run signal ETL to populate KG from signals/company_files")
    p_etl.add_argument("--mode", choices=["full", "incremental"], default="full",
                        help="ETL mode: full (backfill + tombstone) or incremental")
    p_etl.add_argument("--dry-run", action="store_true",
                        help="Compute counts without writing")
    p_etl.add_argument("--json", dest="json_output", action="store_true")
    p_etl.set_defaults(func=_cmd_etl)

    # -------------------------------------------------------------- etl-status
    p_etl_status = q.add_parser("etl-status", help="Show signal ETL status")
    p_etl_status.add_argument("--json", dest="json_output", action="store_true")
    p_etl_status.set_defaults(func=_cmd_etl_status)

    # -------------------------------------------------------------- evidence
    p_evidence = q.add_parser("evidence", help="Evidence chain for a company")
    p_evidence.add_argument("company_id", help="Company node ID")
    p_evidence.add_argument("--json", dest="json_output", action="store_true")
    p_evidence.set_defaults(func=_cmd_evidence)

    # ------------------------------------------------------------------ gaps
    p_gaps = q.add_parser("gaps", help="Find companies with thin evidence")
    p_gaps.add_argument("--min-evidence", type=int, default=2,
                         help="Minimum source count (default 2)")
    p_gaps.add_argument("--limit", type=int, default=100)
    p_gaps.add_argument("--json", dest="json_output", action="store_true")
    p_gaps.set_defaults(func=_cmd_gaps)

    # --------------------------------------------------------------- conflicts
    p_conflicts = q.add_parser("conflicts", help="Detect source disagreements")
    p_conflicts.add_argument("--limit", type=int, default=100)
    p_conflicts.add_argument("--json", dest="json_output", action="store_true")
    p_conflicts.set_defaults(func=_cmd_conflicts)

    # ---------------------------------------------------------------- sector
    p_sector = q.add_parser("sector", help="Companies in a sector cluster")
    p_sector.add_argument("sector_id", help="Sector node ID (e.g., sector:cpg)")
    p_sector.add_argument("--limit", type=int, default=100)
    p_sector.add_argument("--json", dest="json_output", action="store_true")
    p_sector.set_defaults(func=_cmd_sector)

    # ------------------------------------------------------------- duplicates
    p_dups = q.add_parser("duplicates", help="Find duplicate company candidates")
    p_dups.add_argument("--limit", type=int, default=100)
    p_dups.add_argument("--json", dest="json_output", action="store_true")
    p_dups.set_defaults(func=_cmd_duplicates)

    # ------------------------------------------------------------------ rank
    p_rank = q.add_parser("rank", help="Rank companies by evidence strength")
    p_rank.add_argument("--min-sources", type=int, default=1)
    p_rank.add_argument("--limit", type=int, default=100)
    p_rank.add_argument("--json", dest="json_output", action="store_true")
    p_rank.set_defaults(func=_cmd_rank)

    # ------------------------------------------------------------------- ego
    p_ego = q.add_parser("ego", help="Generate ego graph around a node")
    p_ego.add_argument("node_id", help="Center node ID")
    p_ego.add_argument("--depth", type=int, default=2)
    p_ego.add_argument("--out", dest="out_file", help="Write JSON to file")
    p_ego.add_argument("--json", dest="json_output", action="store_true")
    p_ego.set_defaults(func=_cmd_ego)

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


# ---------------------------------------------------------------------------
# Signal ETL commands
# ---------------------------------------------------------------------------

def _cmd_etl(args: argparse.Namespace) -> None:
    """Run signal ETL to populate KG from signals/company_files."""
    async def _run():
        from storage.signal_store import SignalStore
        from storage.kg_signal_builder import KGSignalBuilder

        store = SignalStore(args.db_path)
        await store.initialize()
        try:
            builder = KGSignalBuilder(store._db)
            report = await builder.build(
                mode=args.mode,
                dry_run=getattr(args, "dry_run", False),
            )

            if getattr(args, "json_output", False):
                print(json.dumps(report.to_dict(), indent=2))
            else:
                print("Signal ETL")
                print("=" * 40)
                print(f"Run ID:  {report.run_id}")
                print(f"Mode:    {report.mode}")
                print(f"Status:  {report.status}")
                print(f"\nNodes:")
                print(f"  Companies:  {report.company_nodes}")
                print(f"  Signals:    {report.signal_nodes}")
                print(f"  Locations:  {report.location_nodes}")
                print(f"\nEdges:")
                print(f"  detected_by:   {report.detected_by_edges}")
                print(f"  in_sector:     {report.in_sector_edges}")
                print(f"  located_in:    {report.located_in_edges}")
                print(f"  has_evidence:  {report.has_evidence_edges}")
                if report.nodes_tombstoned or report.edges_expired:
                    print(f"\nCleanup:")
                    print(f"  Tombstoned: {report.nodes_tombstoned}")
                    print(f"  Expired:    {report.edges_expired}")
                print(f"\nScanned: {report.companies_scanned} companies, "
                      f"{report.signals_scanned} signals")
                print(f"Duration: {report.duration_ms:.0f}ms")
                if report.warnings:
                    print("\nWarnings:")
                    for w in report.warnings:
                        print(f"  - {w}")
        finally:
            await store.close()

    _run_async(_run())


def _cmd_etl_status(args: argparse.Namespace) -> None:
    """Show signal ETL status."""
    async def _run():
        from storage.signal_store import SignalStore
        from storage.kg_signal_builder import KGSignalBuilder

        store = SignalStore(args.db_path)
        await store.initialize()
        try:
            builder = KGSignalBuilder(store._db)
            status = await builder.get_etl_status()

            if getattr(args, "json_output", False):
                print(json.dumps(status, indent=2))
            else:
                print("Signal ETL Status")
                print("=" * 40)
                nc = status.get("node_counts", {})
                ec = status.get("edge_counts", {})
                st = status.get("source_tables", {})
                print(f"\nSource tables:")
                print(f"  signals:        {st.get('signals', 0)}")
                print(f"  company_files:  {st.get('company_files', 0)}")
                if nc:
                    print(f"\nKG nodes (signal_etl):")
                    for nt, count in sorted(nc.items()):
                        print(f"  {nt}: {count}")
                if ec:
                    print(f"\nKG edges (signal_etl):")
                    for et, count in sorted(ec.items()):
                        print(f"  {et}: {count}")
                lr = status.get("last_run")
                if lr:
                    print(f"\nLast ETL run: {lr['run_id']} ({lr['mode']}, {lr['status']})")
                    print(f"  Started: {lr['started_at']}")
                else:
                    print("\nNo ETL runs found.")
        finally:
            await store.close()

    _run_async(_run())


# ---------------------------------------------------------------------------
# Query commands
# ---------------------------------------------------------------------------

def _cmd_evidence(args: argparse.Namespace) -> None:
    """Evidence chain for a company."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            chain = await engine.company_evidence_chain(args.company_id)
            if chain is None:
                print(f"Company {args.company_id} not found.", file=sys.stderr)
                sys.exit(1)
            if getattr(args, "json_output", False):
                print(json.dumps(chain.to_dict(), indent=2))
            else:
                print(f"Evidence Chain: {chain.company_label} ({chain.company_id})")
                print("=" * 50)
                print(f"Sources: {chain.source_count}")
                print(f"Weighted score: {chain.weighted_score:.3f}")
                print(f"Sectors: {', '.join(chain.sectors) or 'none'}")
                print(f"Locations: {', '.join(chain.locations) or 'none'}")
                print(f"Evidence families: {', '.join(chain.evidence_families) or 'none'}")
                if chain.signals:
                    print(f"\nSignals ({len(chain.signals)}):")
                    for s in chain.signals:
                        print(f"  {s['source_api']}/{s['signal_type']} "
                              f"conf={s['confidence']:.2f} at {s['detected_at']}")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_gaps(args: argparse.Namespace) -> None:
    """Find companies with thin evidence."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            gaps = await engine.find_data_gaps(
                min_evidence=args.min_evidence,
                limit=args.limit,
            )
            if getattr(args, "json_output", False):
                print(json.dumps(gaps, indent=2))
            else:
                if not gaps:
                    print("No data gaps found.")
                    return
                print(f"Data Gaps (< {args.min_evidence} sources)")
                print("=" * 50)
                for g in gaps:
                    print(f"  {g['company_label']} ({g['company_id']}): "
                          f"{g['source_count']} source(s), "
                          f"{g['signal_count']} signal(s)")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_conflicts(args: argparse.Namespace) -> None:
    """Detect source disagreements."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            conflicts = await engine.detect_conflicts(limit=args.limit)
            if getattr(args, "json_output", False):
                print(json.dumps([c.to_dict() for c in conflicts], indent=2))
            else:
                if not conflicts:
                    print("No conflicts detected.")
                    return
                print(f"Conflicts ({len(conflicts)})")
                print("=" * 50)
                for c in conflicts:
                    print(f"  {c.company_label} ({c.company_id}): "
                          f"{c.field_name} disagrees")
                    for src, val in c.values.items():
                        print(f"    {src}: {val}")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_sector(args: argparse.Namespace) -> None:
    """Companies in a sector cluster."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            results = await engine.sector_cluster(
                args.sector_id, limit=args.limit
            )
            if getattr(args, "json_output", False):
                print(json.dumps(results, indent=2))
            else:
                if not results:
                    print(f"No companies in sector {args.sector_id}.")
                    return
                print(f"Sector: {args.sector_id} ({len(results)} companies)")
                print("=" * 50)
                for r in results:
                    print(f"  {r['company_label']} ({r['company_id']}): "
                          f"{r['signal_count']} signals")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_duplicates(args: argparse.Namespace) -> None:
    """Find duplicate company candidates."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            dups = await engine.find_duplicate_candidates(limit=args.limit)
            if getattr(args, "json_output", False):
                print(json.dumps(dups, indent=2))
            else:
                if not dups:
                    print("No duplicate candidates found.")
                    return
                print(f"Duplicate Candidates ({len(dups)})")
                print("=" * 50)
                for d in dups:
                    print(f"  {d['company_a']} <-> {d['company_b']}")
                    print(f"    Location: {d['location']}")
                    print(f"    Shared: {', '.join(d['shared_connections'])}")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_rank(args: argparse.Namespace) -> None:
    """Rank companies by evidence strength."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            rankings = await engine.rank_by_evidence_strength(
                limit=args.limit,
                min_sources=args.min_sources,
            )
            if getattr(args, "json_output", False):
                print(json.dumps(rankings, indent=2))
            else:
                if not rankings:
                    print("No ranked companies found.")
                    return
                print(f"Evidence Strength Ranking ({len(rankings)})")
                print("=" * 60)
                print(f"{'Company':<30} {'Sources':<8} {'Signals':<8} {'Strength'}")
                print("-" * 60)
                for r in rankings:
                    label = (r['company_label'] or r['company_id'])[:28]
                    print(f"  {label:<28} {r['source_count']:<8} "
                          f"{r['signal_count']:<8} {r['evidence_strength']:.3f}")
        finally:
            await conn.close()

    _run_async(_run())


def _cmd_ego(args: argparse.Namespace) -> None:
    """Generate ego graph around a node."""
    async def _run():
        from storage.kg_queries import KGQueryEngine
        store, conn = await _open_kg_store(args.db_path)
        try:
            engine = KGQueryEngine(store, conn)
            graph = await engine.ego_graph(
                args.node_id,
                depth=args.depth,
            )

            out = getattr(args, "out_file", None)
            if out:
                with open(out, "w") as f:
                    json.dump(graph, f, indent=2)
                print(f"Ego graph written to {out} "
                      f"({graph['node_count']} nodes, {graph['edge_count']} edges)")
            elif getattr(args, "json_output", False):
                print(json.dumps(graph, indent=2))
            else:
                print(f"Ego Graph: {args.node_id} (depth={args.depth})")
                print("=" * 50)
                print(f"Nodes: {graph['node_count']}")
                print(f"Edges: {graph['edge_count']}")
                for n in graph["nodes"]:
                    indent = "  " * (n["depth"] + 1)
                    print(f"{indent}{n['type']}: {n['label']} ({n['id']})")
        finally:
            await conn.close()

    _run_async(_run())
