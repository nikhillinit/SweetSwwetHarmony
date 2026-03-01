"""Feature governance gate — overdue regret checks and config snapshots.

Provides:
- compute_config_snapshot(): Capture current env feature flags for run pinning
- get_overdue_regret_checks(): Find feature promotions missing timely regret checks
- CLI: python -m monitoring.feature_gate overdue --db signals.db --json
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from utils.canonical_keys import derive_company_id

logger = logging.getLogger(__name__)

# Feature flag env vars to capture in config snapshots
_CONFIG_KEYS = [
    "BULK_TRIAGE_ENABLED",
    "DELIVERY_MODE",
    "DRIFT_MONITORING_ENABLED",
    "HUNTER_ENABLEMENT",
    "HUNTER_PROMOTE_ENABLED",
    "LLM_THESIS_MODE",
    "MERGE_WRITES_ENABLED",
    "ML_ENABLEMENT",
    "USE_SHADOW_ENTITY_RESOLUTION",
    "USE_THIN_FILES",
    "V2_ENABLEMENT",
]

REGRET_CHECK_WINDOW_DAYS = 14


def compute_config_snapshot() -> Dict[str, Any]:
    """Capture current feature flag env vars as a deterministic snapshot.

    Returns dict with 'flags' (sorted env values) and 'hash' (SHA256[:16]).
    """
    flags = {}
    for key in _CONFIG_KEYS:
        val = os.getenv(key)
        if val is not None:
            flags[key] = val
    raw = json.dumps(flags, sort_keys=True)
    # Reuse shared deterministic short-hash helper (lint-safe Rule2).
    content_hash = derive_company_id(raw)
    return {"flags": flags, "hash": content_hash}


def _ensure_feature_decisions_view(conn: sqlite3.Connection) -> None:
    """Create or replace the feature_decisions convenience view.

    Uses DROP+CREATE (not CREATE IF NOT EXISTS) so the view definition
    stays current as new governance action types are added.
    Uses LIKE 'feature_%' wildcard for forward-compatibility.
    """
    conn.execute("DROP VIEW IF EXISTS feature_decisions")
    conn.execute("""
        CREATE VIEW feature_decisions AS
        SELECT
            id,
            action_type,
            entity_type,
            entity_id,
            actor_id,
            reason,
            metadata,
            created_at
        FROM audit_events
        WHERE action_type LIKE 'feature_%'
        ORDER BY created_at DESC
    """)


def _parse_due_at(
    created_at: str, window_days: int = REGRET_CHECK_WINDOW_DAYS
) -> datetime:
    """Parse audit_events.created_at and add regret window.

    Handles multiple timestamp formats:
    - 2026-02-10T00:00:00+00:00  (ISO with timezone)
    - 2026-02-10 00:00:00        (naive, assumed UTC)
    - 2026-02-10T00:00:00Z       (Z suffix)

    Falls back to window_days from now if parsing fails.
    """
    ts = created_at.strip()

    # Handle Z suffix — replace with +00:00 for fromisoformat
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt + timedelta(days=window_days)
    except (ValueError, TypeError):
        logger.warning("Could not parse timestamp %r, using fallback", created_at)
        return datetime.now(timezone.utc) + timedelta(days=window_days)


def get_overdue_regret_checks(
    db_path: str,
    *,
    strict: bool = False,
    window_days: int = REGRET_CHECK_WINDOW_DAYS,
) -> Dict[str, Any]:
    """Find feature promotions that lack a timely regret check.

    Queries audit_events for 'feature_promote' actions, then checks
    if a matching 'regret_check' action exists for the same entity_id
    within window_days.

    Args:
        db_path: Path to signals.db
        strict: If True, raise on schema/locking issues instead of
                returning empty. Useful for the scheduler's catch block
                to produce an honest ok=false payload.
        window_days: Days after promotion before regret check is overdue.

    Returns:
        {"count": int, "overdue": [...]}

    Raises:
        sqlite3.OperationalError: If strict=True and audit_events table
            is missing or locked.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            # Check table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='audit_events'"
            )
            if cursor.fetchone() is None:
                if strict:
                    raise sqlite3.OperationalError("audit_events table missing")
                return {"count": 0, "overdue": []}

            _ensure_feature_decisions_view(conn)

            now = datetime.now(timezone.utc)

            # Find all feature_promote events
            promotions = conn.execute(
                "SELECT entity_id, created_at, metadata "
                "FROM audit_events "
                "WHERE action_type = 'feature_promote' "
                "ORDER BY created_at ASC"
            ).fetchall()

            overdue = []
            for row in promotions:
                entity_id = row["entity_id"]
                promoted_at = row["created_at"]

                # Prefer metadata.regret_due_at when available
                regret_due_at = None
                raw_meta = row["metadata"]
                if raw_meta:
                    try:
                        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                        regret_due_at = meta.get("regret_due_at")
                    except (json.JSONDecodeError, TypeError):
                        pass

                if regret_due_at:
                    due_at = _parse_due_at(regret_due_at, 0)
                else:
                    due_at = _parse_due_at(promoted_at, window_days)

                if now < due_at:
                    continue

                # Check for a matching regret_check
                check = conn.execute(
                    "SELECT 1 FROM audit_events "
                    "WHERE action_type = 'regret_check' "
                    "  AND entity_id = ? "
                    "  AND created_at >= ? "
                    "LIMIT 1",
                    (entity_id, promoted_at),
                ).fetchone()

                if check is None:
                    overdue.append({
                        "entity_id": entity_id,
                        "promoted_at": promoted_at,
                        "due_at": due_at.isoformat(),
                    })

            return {"count": len(overdue), "overdue": overdue}
        finally:
            conn.close()
    except sqlite3.OperationalError:
        if strict:
            raise
        logger.warning("Could not query audit_events: %s", db_path, exc_info=True)
        return {"count": 0, "overdue": []}


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _cli_main():
    """CLI: python -m monitoring.feature_gate overdue --db signals.db --json"""
    import argparse
    import sys

    sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Feature governance gate")
    sub = parser.add_subparsers(dest="command")

    overdue_parser = sub.add_parser("overdue", help="Check for overdue regret checks")
    overdue_parser.add_argument("--db", required=True, help="Path to signals.db")
    overdue_parser.add_argument("--json", action="store_true", help="JSON output")
    overdue_parser.add_argument(
        "--strict", action="store_true", help="Raise on schema errors"
    )

    snapshot_parser = sub.add_parser("snapshot", help="Print config snapshot")
    snapshot_parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.command == "overdue":
        result = get_overdue_regret_checks(args.db, strict=args.strict)
        if getattr(args, "json", False):
            json.dump(result, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"Overdue regret checks: {result['count']}")
            for item in result["overdue"]:
                print(
                    f"  - {item['entity_id']}: promoted {item['promoted_at']}, "
                    f"due {item['due_at']}"
                )
    elif args.command == "snapshot":
        result = compute_config_snapshot()
        if getattr(args, "json", False):
            json.dump(result, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"Config hash: {result['hash']}")
            for k, v in result["flags"].items():
                print(f"  {k}={v}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()
