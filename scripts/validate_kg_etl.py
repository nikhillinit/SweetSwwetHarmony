"""KG ETL Validation Script (skeleton).

Validates the Knowledge Graph ETL pipeline against a **snapshot** database.
Hard-rejects live database filenames to prevent accidental mutation.

Usage:
    python scripts/validate_kg_etl.py --db signals.db.kg-validation-snapshot --phase all
    python scripts/validate_kg_etl.py --db signals.db.kg-validation-snapshot --phase A --json

Phases:
    A  Dry-run acceptance (read-only ETL dry-run)
    B  Full ETL on snapshot + stats + validation
    C  Query sanity checks
    D  Idempotency check
    all  Run phases A through D in order

See docs/plans/2026-03-24-kg-post-window-validation.md for the full runbook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Safety: reject live DB filenames
# ---------------------------------------------------------------------------

_LIVE_DB_NAMES = frozenset({
    "signals.db",
    os.path.abspath("signals.db"),
})


def _reject_live_db(db_path: str) -> None:
    """Hard-stop if db_path resolves to a known live database."""
    resolved = os.path.abspath(db_path)
    basename = os.path.basename(db_path)

    if basename == "signals.db" or resolved in _LIVE_DB_NAMES:
        print(
            f"REJECTED: '{db_path}' resolves to a live database.\n"
            "This script must only target snapshot copies.\n"
            "Create a copy first:\n"
            "  PowerShell: Copy-Item signals.db signals.db.kg-validation-snapshot\n"
            "  bash:       cp signals.db signals.db.kg-validation-snapshot",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase stubs
# ---------------------------------------------------------------------------

def _run_phase_a(db_path: str, as_json: bool) -> dict:
    """Phase A: Dry-run acceptance (read-only)."""
    # TODO: Post-window implementation
    #   - Run KGSignalBuilder(db).build(mode="full", dry_run=True)
    #   - Check company_nodes > 0, signal_nodes > 0, warnings empty
    #   - Run get_etl_status() and verify empty baseline
    return {
        "phase": "A",
        "status": "plan_only",
        "description": "Dry-run acceptance: read-only ETL dry-run",
        "steps": ["A1: ETL dry-run", "A2: ETL status before build"],
    }


def _run_phase_b(db_path: str, as_json: bool) -> dict:
    """Phase B: Full ETL on snapshot."""
    # TODO: Post-window implementation
    #   - Run KGSignalBuilder(db).build(mode="full")
    #   - Capture report to artifacts/kg-validation/etl-report.json
    #   - Run graph stats and validate counts
    #   - Run graph validate (7 checks must pass)
    #   - Run get_etl_status() and verify populated
    return {
        "phase": "B",
        "status": "plan_only",
        "description": "Full ETL on snapshot + stats + validation",
        "steps": [
            "B1: Full ETL run",
            "B2: Graph stats",
            "B3: Validation checks (7 checks)",
            "B4: ETL status after build",
        ],
    }


def _run_phase_c(db_path: str, as_json: bool) -> dict:
    """Phase C: Query sanity checks."""
    # TODO: Post-window implementation
    #   - evidence chain for a known company
    #   - data gaps with min_evidence=2
    #   - conflict detection
    #   - evidence ranking with min_sources=2
    #   - ego graph extraction
    return {
        "phase": "C",
        "status": "plan_only",
        "description": "Query sanity checks",
        "steps": [
            "C1: Evidence chain",
            "C2: Data gaps",
            "C3: Conflicts",
            "C4: Evidence ranking",
            "C5: Ego graph",
        ],
    }


def _run_phase_d(db_path: str, as_json: bool) -> dict:
    """Phase D: Idempotency check."""
    # TODO: Post-window implementation
    #   - Run ETL a second time
    #   - Verify nodes_tombstoned=0, edges_expired=0
    #   - Re-run validate (7 checks still pass)
    return {
        "phase": "D",
        "status": "plan_only",
        "description": "Idempotency check (re-run ETL, verify no churn)",
        "steps": [
            "D1: Second ETL run",
            "D2: Post-idempotency validation",
        ],
    }


_PHASES = {
    "A": _run_phase_a,
    "B": _run_phase_b,
    "C": _run_phase_c,
    "D": _run_phase_d,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate KG ETL pipeline against a snapshot database.",
        epilog="See docs/plans/2026-03-24-kg-post-window-validation.md for details.",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to snapshot database (must NOT be signals.db)",
    )
    parser.add_argument(
        "--phase",
        choices=["A", "B", "C", "D", "all"],
        default="all",
        help="Phase to run (default: all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output results as JSON",
    )

    args = parser.parse_args(argv)

    # Safety check: reject live DB
    _reject_live_db(args.db)

    # Verify DB file exists
    if not os.path.exists(args.db):
        print(f"ERROR: Database file not found: {args.db}", file=sys.stderr)
        return 1

    # Select phases to run
    if args.phase == "all":
        phase_keys = ["A", "B", "C", "D"]
    else:
        phase_keys = [args.phase]

    results = []
    for key in phase_keys:
        result = _PHASES[key](args.db, args.as_json)
        results.append(result)

    if args.as_json:
        print(json.dumps({"phases": results}, indent=2))
    else:
        for r in results:
            print(f"\nPhase {r['phase']}: {r['description']}")
            print(f"  Status: {r['status']}")
            for step in r["steps"]:
                print(f"    - {step}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
