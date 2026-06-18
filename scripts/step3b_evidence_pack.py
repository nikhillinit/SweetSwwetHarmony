"""
Step 3B Evidence Pack — produce artifact bundle for readiness audit.

Outputs to artifacts/step3b/:
  1. step3b-readiness.json     — gate verdict + blockers + metrics
  2. convergence-metrics.json  — multi-source company_files breakdown
  3. phase-g-readiness.json    — Phase G entity resolution readiness
  4. decision-note.md          — short human-readable summary

Usage:
    python scripts/step3b_evidence_pack.py [--db signals.db] [--out-dir artifacts/step3b]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.db_path_helper import resolve_db_path_env


async def generate_evidence_pack(db_path: str, out_dir: str) -> dict:
    """Generate all evidence artifacts and write to out_dir."""
    import aiosqlite

    from storage.signal_store import SignalStore
    from monitoring.step3b_readiness import check_step3b_readiness
    from monitoring.phase_g_readiness import check_phase_g_readiness

    os.makedirs(out_dir, exist_ok=True)

    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        db = store._db

        # 1. Step 3B readiness
        readiness = await check_step3b_readiness(store)
        readiness_dict = readiness.to_dict()
        _write_json(os.path.join(out_dir, "step3b-readiness.json"), readiness_dict)

        # 2. Convergence metrics
        convergence = await _collect_convergence_metrics(db)
        _write_json(os.path.join(out_dir, "convergence-metrics.json"), convergence)

        # 3. Phase G readiness
        phase_g = await check_phase_g_readiness(store)
        phase_g_dict = phase_g.to_dict()
        _write_json(os.path.join(out_dir, "phase-g-readiness.json"), phase_g_dict)

        # 4. Decision note
        _write_decision_note(
            os.path.join(out_dir, "decision-note.md"),
            readiness_dict,
            convergence,
            phase_g_dict,
        )

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "out_dir": out_dir,
            "verdict": readiness.verdict,
            "blockers": len(readiness.blockers),
            "artifacts": [
                "step3b-readiness.json",
                "convergence-metrics.json",
                "phase-g-readiness.json",
                "decision-note.md",
            ],
        }
        print(json.dumps(summary, indent=2))
        return summary

    finally:
        await store.close()


async def _collect_convergence_metrics(db) -> dict:
    """Gather multi-source convergence stats from company_files."""
    # Total counts by status
    cursor = await db.execute(
        "SELECT status, COUNT(*) FROM company_files GROUP BY status"
    )
    status_counts = dict(await cursor.fetchall())

    # Multi-source breakdown (promoted only)
    cursor = await db.execute(
        """SELECT json_array_length(source_apis) as src_count, COUNT(*)
           FROM company_files
           WHERE status = 'promoted'
           GROUP BY src_count
           ORDER BY src_count"""
    )
    source_distribution = {row[0]: row[1] for row in await cursor.fetchall()}

    # Top multi-source companies
    cursor = await db.execute(
        """SELECT company_id, company_name, source_apis, canonical_key
           FROM company_files
           WHERE status = 'promoted'
             AND json_array_length(source_apis) >= 2
           ORDER BY json_array_length(source_apis) DESC, last_seen_at DESC
           LIMIT 20"""
    )
    multi_source_files = []
    for row in await cursor.fetchall():
        multi_source_files.append({
            "company_id": row[0],
            "company_name": row[1],
            "source_apis": json.loads(row[2]) if row[2] else [],
            "canonical_key": row[3],
        })

    # Distinct source APIs across all promoted
    cursor = await db.execute(
        "SELECT source_apis FROM company_files WHERE status = 'promoted'"
    )
    all_sources = set()
    for row in await cursor.fetchall():
        try:
            sources = json.loads(row[0]) if row[0] else []
            all_sources.update(sources)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "company_files_by_status": status_counts,
        "promoted_source_distribution": source_distribution,
        "multi_source_promoted_count": len(multi_source_files),
        "multi_source_companies": multi_source_files,
        "distinct_source_apis": sorted(all_sources),
    }


def _write_json(path: str, data: dict):
    """Atomic JSON write (tmp + rename)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _write_decision_note(
    path: str,
    readiness: dict,
    convergence: dict,
    phase_g: dict,
):
    """Write a short markdown decision note."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    verdict = readiness["verdict"].upper()
    multi = convergence["multi_source_promoted_count"]
    status_counts = convergence.get("company_files_by_status", {})
    canary = readiness["metrics"].get("canary_verdict", "unknown")
    canary_rate = readiness["metrics"].get("canary_pass_rate", "?")
    pg_verdict = phase_g["verdict"]

    lines = [
        f"# Step 3B Readiness Decision Note",
        f"",
        f"**Date:** {now}",
        f"**Verdict:** {verdict}",
        f"",
        f"## Predicate Summary",
        f"",
        f"| Predicate | Value | Threshold | Status |",
        f"|-----------|-------|-----------|--------|",
        f"| Multi-source promoted files | {multi} | >= 5 | {'PASS' if multi >= 5 else 'FAIL'} |",
        f"| Canary verdict | {canary} (rate={canary_rate}) | pass/degraded | {'PASS' if canary in ('pass', 'degraded') else 'FAIL'} |",
        f"| Phase G readiness | {pg_verdict} | ready | {'PASS' if pg_verdict == 'ready' else 'FAIL'} |",
        f"",
        f"## Company File Summary",
        f"",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"- **{status}**: {count}")

    lines.append("")

    if convergence["multi_source_companies"]:
        lines.append("## Multi-Source Companies")
        lines.append("")
        for co in convergence["multi_source_companies"][:10]:
            sources = ", ".join(co["source_apis"])
            lines.append(f"- **{co['company_name']}** ({co['canonical_key']}): {sources}")
        lines.append("")

    if readiness["blockers"]:
        lines.append("## Blockers")
        lines.append("")
        for b in readiness["blockers"]:
            lines.append(f"- {b}")
        lines.append("")

    content = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser(description="Generate Step 3B evidence pack")
    parser.add_argument("--db", default=None)
    parser.add_argument("--out-dir", default="artifacts/step3b")
    args = parser.parse_args()
    args.db = resolve_db_path_env(args.db)
    asyncio.run(generate_evidence_pack(args.db, args.out_dir))


if __name__ == "__main__":
    main()
