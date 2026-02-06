#!/usr/bin/env python3

if __name__ == "__main__" and __package__ is None:
    import sys
    from pathlib import Path

    here = Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / "requirements.txt").exists() or (parent / ".git").exists():
            sys.path.insert(0, str(parent))
            break

import argparse
import sys

# Windows console compatibility: replace unencodable chars instead of crashing
import io
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(errors='replace')
import json
import getpass
from datetime import datetime, timezone
from typing import Optional

from ops.storage import OpsStorage


def get_storage(args) -> OpsStorage:
    return OpsStorage(args.db)


def log_audit(
    conn,
    operation: str,
    target_type: str,
    target_id: Optional[int],
    before_state: Optional[dict],
    after_state: Optional[dict],
    reason: str = "",
):
    user = getpass.getuser()

    conn.execute(
        """
        INSERT INTO audit_log
        (operation, target_type, target_id, user, before_state, after_state, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            operation,
            target_type,
            target_id,
            user,
            json.dumps(before_state) if before_state else None,
            json.dumps(after_state) if after_state else None,
            reason,
        ),
    )


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse a timestamp string into a timezone-aware UTC datetime.

    SQLite's datetime('now') and CURRENT_TIMESTAMP produce YYYY-MM-DD HH:MM:SS
    without timezone info. We attach timezone.utc so that downstream arithmetic
    (e.g. computing days_old) is accurate.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def list_facts(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        query_parts = ["SELECT id, type, content, confidence, status, created_at, used_count FROM memory_facts"]
        params = []
        where_clauses = []

        if args.status:
            where_clauses.append("status = ?")
            params.append(args.status)

        if args.type:
            where_clauses.append("type = ?")
            params.append(args.type)

        if args.search:
            where_clauses.append("content LIKE ?")
            params.append(f"%{args.search}%")

        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))

        query_parts.append("ORDER BY created_at DESC")

        if args.limit:
            query_parts.append("LIMIT ?")
            params.append(args.limit)

        query = " ".join(query_parts)
        cursor = conn.execute(query, params)
        facts = cursor.fetchall()

    print(f"\n📋 Found {len(facts)} facts:")
    print("=" * 80)

    for fact in facts:
        fid, ftype, content, confidence, status, created, used_count = fact
        conf_pct = int((confidence or 0) * 100)

        status_color = {
            "active": "\033[92m",
            "pending": "\033[93m",
            "retired": "\033[90m",
        }.get(status, "")

        reset = "\033[0m"
        used_count = used_count or 0

        print(
            f"{status_color}[{status.upper()}]{reset} ID:{fid:4d} {ftype.upper():10s} ({conf_pct:3d}%) Used:{used_count:3d}x"
        )

        display_content = content
        if content and len(content) > 70:
            display_content = content[:67] + "..."

        print(f"      {display_content}")
        
        # Format timestamp for better readability
        created_dt = _parse_timestamp(created)
        if created_dt:
            formatted_date = created_dt.strftime("%Y-%m-%d %H:%M:%S")
            print(f"      {formatted_date}")
        else:
            print(f"      {created}")
        print()


def approve_fact(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        cursor = conn.execute("SELECT * FROM memory_facts WHERE id = ?", (args.id,))
        row = cursor.fetchone()

        if not row:
            print(f"❌ Fact {args.id} not found")
            sys.exit(1)

        before_state = dict(zip([col[0] for col in cursor.description], row))

        if before_state["status"] == "active":
            print(f"⚠️  Fact {args.id} is already active")
            sys.exit(0)

        if before_state["status"] != "pending":
            print(
                f"❌ Cannot approve fact with status '{before_state['status']}'. Only pending facts can be approved."
            )
            sys.exit(1)

        cursor = conn.execute(
            "UPDATE memory_facts SET status = 'active' WHERE id = ? AND status = 'pending'",
            (args.id,),
        )

        if cursor.rowcount == 0:
            print(f"❌ Failed to approve fact {args.id}")
            sys.exit(1)

        cursor = conn.execute("SELECT * FROM memory_facts WHERE id = ?", (args.id,))
        row = cursor.fetchone()
        after_state = dict(zip([col[0] for col in cursor.description], row))

        log_audit(
            conn,
            operation="approve_fact",
            target_type="memory_fact",
            target_id=args.id,
            before_state=before_state,
            after_state=after_state,
            reason=args.reason or "CLI approval",
        )

    print(f"✅ Fact {args.id} approved and set to active")


def retire_fact(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        cursor = conn.execute("SELECT * FROM memory_facts WHERE id = ?", (args.id,))
        row = cursor.fetchone()

        if not row:
            print(f"❌ Fact {args.id} not found")
            sys.exit(1)

        before_state = dict(zip([col[0] for col in cursor.description], row))

        if before_state["status"] == "retired":
            print(f"⚠️  Fact {args.id} is already retired")
            sys.exit(0)

        if before_state["status"] not in ("pending", "active"):
            print(f"❌ Cannot retire fact with status '{before_state['status']}'")
            sys.exit(1)

        cursor = conn.execute(
            "UPDATE memory_facts SET status = 'retired' WHERE id = ? AND status IN ('pending', 'active')",
            (args.id,),
        )

        if cursor.rowcount == 0:
            print(f"❌ Failed to retire fact {args.id}")
            sys.exit(1)

        cursor = conn.execute("SELECT * FROM memory_facts WHERE id = ?", (args.id,))
        row = cursor.fetchone()
        after_state = dict(zip([col[0] for col in cursor.description], row))

        log_audit(
            conn,
            operation="retire_fact",
            target_type="memory_fact",
            target_id=args.id,
            before_state=before_state,
            after_state=after_state,
            reason=args.reason or "CLI retirement",
        )

    print(f"✅ Fact {args.id} retired")


def list_actions(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        query = """
            SELECT
                mas.action_id,
                mas.status,
                mas.attempts,
                mas.last_attempt_at,
                mas.last_error,
                ua.rejection_reason
            FROM memory_action_state mas
            JOIN user_actions ua ON mas.action_id = ua.id
        """
        params = []

        if args.status:
            query += " WHERE mas.status = ?"
            params.append(args.status)

        query += " ORDER BY mas.last_attempt_at DESC LIMIT ?"
        params.append(args.limit)

        cursor = conn.execute(query, params)
        actions = cursor.fetchall()

    print(f"\n📝 Found {len(actions)} action states:")
    print("=" * 80)

    for action in actions:
        aid, status, attempts, last_attempt, error, reason = action

        status_color = {
            "processed": "\033[92m",
            "no_facts": "\033[94m",
            "failed": "\033[91m",
            "failed_permanent": "\033[90m",
            "suspicious": "\033[93m",
            "processing": "\033[96m",
        }.get(status, "")

        reset = "\033[0m"

        print(f"{status_color}[{status.upper()}]{reset} Action ID:{aid:4d} Attempts:{attempts}")
        print(f"      Reason: {reason or 'N/A'}")
        if error:
            print(f"      Error: {error}")
        print(f"      Last attempt: {last_attempt}")
        print()


def reset_action(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        cursor = conn.execute(
            "SELECT * FROM memory_action_state WHERE action_id = ?",
            (args.action_id,),
        )
        row = cursor.fetchone()

        if not row:
            print(f"❌ Action state {args.action_id} not found")
            sys.exit(1)

        before_state = dict(zip([col[0] for col in cursor.description], row))

        conn.execute(
            """
            UPDATE memory_action_state
            SET status='failed',
                attempts=0,
                last_error=NULL,
                last_attempt_at=datetime('now', '-31 minutes')
            WHERE action_id=?
            """,
            (args.action_id,),
        )

        cursor = conn.execute(
            "SELECT * FROM memory_action_state WHERE action_id = ?",
            (args.action_id,),
        )
        row = cursor.fetchone()
        after_state = dict(zip([col[0] for col in cursor.description], row))

        log_audit(
            conn,
            operation="reset_action",
            target_type="action_state",
            target_id=args.action_id,
            before_state=before_state,
            after_state=after_state,
            reason=args.reason or "CLI reset",
        )

    print(f"✅ Action {args.action_id} reset for retry")


def audit_unused(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        cursor = conn.execute(
            """
            SELECT
                id, type, content, confidence, used_count,
                created_at, last_used_at
            FROM memory_facts
            WHERE status = 'active'
            AND confidence >= ?
            AND COALESCE(used_count, 0) < ?
            AND created_at < datetime('now', ?)
            ORDER BY confidence DESC, created_at ASC
            LIMIT ?
            """,
            (args.min_confidence, args.max_usage, f"-{args.days} days", args.limit),
        )

        facts = cursor.fetchall()

    print(f"\n🔍 Found {len(facts)} potentially unused facts:")
    print("=" * 80)

    now = datetime.now(timezone.utc)

    for fact in facts:
        fid, ftype, content, confidence, used_count, created, last_used = fact
        used_count = used_count or 0

        created_dt = _parse_timestamp(created)
        days_old = (now - created_dt).days if created_dt else -1

        print(f"ID:{fid:4d} {ftype.upper():10s} ({confidence:.0%}) Used:{used_count}x Age:{days_old if days_old>=0 else 'N/A'}d")
        print(f"      {content}")
        if last_used:
            print(f"      Last used: {last_used}")
        print()

    if facts:
        print("\n💡 Consider retiring unused high-confidence facts to reduce noise")
        print(f"   Use: ops/cli.py retire <ID> --reason 'Unused after {args.days} days'")


def stats(args):
    storage = get_storage(args)

    with storage.transaction() as conn:
        cursor = conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM memory_facts
            GROUP BY status
            """
        )

        print("\n📊 FACT STATISTICS")
        print("-" * 40)

        total = 0
        for status, count in cursor.fetchall():
            print(f"  {status.upper():10s}: {count:4d}")
            total += count

        print(f"  {'TOTAL':10s}: {total:4d}")

        cursor = conn.execute(
            """
            SELECT
                COUNT(*) as total_facts,
                SUM(COALESCE(used_count,0)) as total_uses,
                AVG(COALESCE(used_count,0)) as avg_uses,
                MAX(COALESCE(used_count,0)) as max_uses
            FROM memory_facts
            WHERE status = 'active'
            """
        )
        row = cursor.fetchone()

        print("\n📈 USAGE STATISTICS")
        print("-" * 40)
        print(f"  Active facts:    {row[0] or 0:4d}")
        print(f"  Total uses:      {row[1] or 0:4d}")
        print(f"  Avg uses/fact:   {row[2] or 0:4.1f}")
        print(f"  Max uses:        {row[3] or 0:4d}")

        cursor = conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM memory_action_state
            GROUP BY status
            """
        )

        print("\n🔄 ACTION STATE STATISTICS")
        print("-" * 40)

        for status, count in cursor.fetchall():
            print(f"  {status.upper():15s}: {count:4d}")

        cursor = conn.execute(
            """
            SELECT
                DATE(run_at) as date,
                COUNT(*) as runs,
                SUM(decisions_processed) as decisions,
                SUM(facts_created) as facts,
                SUM(estimated_cost) as cost,
                AVG(duration_seconds) as avg_duration
            FROM extraction_runs
            WHERE run_at > datetime('now', '-7 days')
            GROUP BY DATE(run_at)
            ORDER BY date DESC
            """
        )

        print("\n📊 LAST 7 DAYS EXTRACTION")
        print("-" * 40)

        runs = cursor.fetchall()
        if runs:
            for row in runs:
                date, num_runs, decisions, facts, cost, duration = row
                print(f"  {date}: {facts or 0} facts from {decisions or 0} decisions")
                print(f"       {num_runs} runs, ${cost or 0:.4f} cost, {duration or 0:.1f}s avg")
        else:
            print("  No extraction runs in last 7 days")

    # Health summary uses its own transaction — must be outside the block above
    health = storage.get_health_summary(hours=24)
    if health:
        print("\n🏥 SYSTEM HEALTH (Last 24h)")
        print("-" * 40)

        for component, metrics in health.items():
            status_icon = (
                "✅"
                if metrics["health_percent"] > 90
                else "⚠️"
                if metrics["health_percent"] > 70
                else "❌"
            )
            print(
                f"  {status_icon} {component:15s}: {metrics['health_percent']:.0f}% healthy"
            )
            if metrics["avg_latency_ms"] > 0:
                print(f"       Avg latency: {metrics['avg_latency_ms']:.1f}ms")


def run_extraction(args):
    from ops.memory.extractor import MemoryExtractor

    try:
        extractor = MemoryExtractor(args.db)
    except (ValueError, ImportError) as e:
        print(f"❌ Extraction failed: {e}")
        print("Ensure GEMINI_API_KEY is set and google-genai is installed")
        sys.exit(1)

    print("🚀 Starting memory extraction...")
    results = extractor.run(max_items=args.limit)

    print("\n🎯 EXTRACTION RESULTS")
    print("-" * 40)
    print(f"Decisions processed: {results['decisions_processed']}")
    print(f"Facts created:       {results['facts_created']}")
    print(f"LLM failures:        {results['llm_failures']}")
    print(f"Estimated cost:      ${results['estimated_cost']:.4f}")

    if results.get("budget_exceeded"):
        print("⚠️  Daily budget exceeded or would be exceeded; stopped early.")


def cleanup(args):
    storage = get_storage(args)

    cutoff = f"-{args.days} days"

    with storage.transaction() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM memory_facts WHERE status = 'retired' AND created_at < datetime('now', ?)",
            (cutoff,),
        )
        before_count = cursor.fetchone()[0]

        archived = 0
        citation_archived = 0

        if before_count and not args.no_archive:
            # Create archive tables (non-authoritative; for audit retention only).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_facts_archive (
                    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    id INTEGER,
                    type TEXT,
                    content TEXT,
                    confidence REAL,
                    source_action_id INTEGER,
                    source_signal_id INTEGER,
                    status TEXT,
                    created_at TIMESTAMP,
                    superseded_by INTEGER,
                    used_count INTEGER,
                    last_used_at TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fact_citations_archive (
                    archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    id INTEGER,
                    fact_id INTEGER,
                    signal_id INTEGER,
                    cited_at TIMESTAMP,
                    context TEXT
                )
                """
            )

            cursor = conn.execute(
                """
                INSERT INTO memory_facts_archive (
                    archived_at, id, type, content, confidence, source_action_id, source_signal_id,
                    status, created_at, superseded_by, used_count, last_used_at
                )
                SELECT
                    CURRENT_TIMESTAMP, id, type, content, confidence, source_action_id, source_signal_id,
                    status, created_at, superseded_by, used_count, last_used_at
                FROM memory_facts
                WHERE status = 'retired'
                  AND created_at < datetime('now', ?)
                """,
                (cutoff,),
            )
            archived = cursor.rowcount

            cursor = conn.execute(
                """
                INSERT INTO fact_citations_archive (
                    archived_at, id, fact_id, signal_id, cited_at, context
                )
                SELECT
                    CURRENT_TIMESTAMP, id, fact_id, signal_id, cited_at, context
                FROM fact_citations
                WHERE fact_id IN (
                    SELECT id FROM memory_facts
                    WHERE status = 'retired'
                      AND created_at < datetime('now', ?)
                )
                """,
                (cutoff,),
            )
            citation_archived = cursor.rowcount

        cursor = conn.execute(
            """
            DELETE FROM memory_facts
            WHERE status = 'retired'
              AND created_at < datetime('now', ?)
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount

        cursor = conn.execute(
            """
            DELETE FROM system_health
            WHERE timestamp < datetime('now', '-30 days')
            """
        )
        health_deleted = cursor.rowcount

        log_audit(
            conn,
            operation="cleanup",
            target_type="system",
            target_id=None,
            before_state={"retired_facts_eligible": before_count},
            after_state={"retired_facts_eligible": max(0, before_count - deleted)},
            reason=f"Cleanup older than {args.days} days (archive={not args.no_archive}, vacuum={args.vacuum})",
        )

    # VACUUM cannot run inside a transaction in SQLite - must be outside
    if args.vacuum:
        try:
            with storage.pool.get_connection() as vacuum_conn:
                vacuum_conn.execute("VACUUM;")
        except Exception as e:
            print(f"⚠️  VACUUM failed: {e}")

    # Print summary (always shown, regardless of vacuum success)
    print("🧹 Cleanup completed:")
    if not args.no_archive:
        print(f"  Archived {archived} retired facts and {citation_archived} citations")
    print(f"  Deleted {deleted} retired facts")
    print(f"  Deleted {health_deleted} old health checks")
    if args.vacuum:
        print("  VACUUM completed")


def main():
    parser = argparse.ArgumentParser(
        description="Ops CLI - Internal Team Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 ops/cli.py list --status pending --limit 10
  python3 ops/cli.py approve 42 --reason "Valid constraint"
  python3 ops/cli.py list-actions --status failed
  python3 ops/cli.py audit-unused --days 30 --min-confidence 0.8
  python3 ops/cli.py run-extraction --limit 5
  python3 ops/cli.py stats

Tip:
  If you prefer module execution, this also works:
    python3 -m ops.cli <command>
        """,
    )

    parser.add_argument("--db", default="signals.db", help="Database path")

    subparsers = parser.add_subparsers(dest="command", help="Command")
    subparsers.required = True

    list_parser = subparsers.add_parser("list", help="List facts")
    list_parser.add_argument("--status", choices=["active", "pending", "retired"], help="Filter by status")
    list_parser.add_argument("--type", choices=["constraint", "nuance", "example"], help="Filter by type")
    list_parser.add_argument("--search", help="Search in content")
    list_parser.add_argument("--limit", type=int, default=50, help="Limit results")
    list_parser.set_defaults(func=list_facts)

    approve_parser = subparsers.add_parser("approve", help="Approve a pending fact")
    approve_parser.add_argument("id", type=int, help="Fact ID")
    approve_parser.add_argument("--reason", help="Reason for approval")
    approve_parser.set_defaults(func=approve_fact)

    retire_parser = subparsers.add_parser("retire", help="Retire a fact")
    retire_parser.add_argument("id", type=int, help="Fact ID")
    retire_parser.add_argument("--reason", help="Reason for retirement")
    retire_parser.set_defaults(func=retire_fact)

    list_actions_parser = subparsers.add_parser("list-actions", help="List action processing states")
    list_actions_parser.add_argument(
        "--status",
        choices=["processing", "processed", "no_facts", "failed", "failed_permanent", "suspicious"],
        help="Filter by status",
    )
    list_actions_parser.add_argument("--limit", type=int, default=20, help="Limit results")
    list_actions_parser.set_defaults(func=list_actions)

    reset_parser = subparsers.add_parser("reset-action", help="Reset an action for retry")
    reset_parser.add_argument("action_id", type=int, help="Action ID")
    reset_parser.add_argument("--reason", help="Reason for reset")
    reset_parser.set_defaults(func=reset_action)

    audit_parser = subparsers.add_parser("audit-unused", help="Find high-confidence unused facts")
    audit_parser.add_argument("--days", type=int, default=30, help="Age threshold in days")
    audit_parser.add_argument("--min-confidence", type=float, default=0.8, help="Minimum confidence")
    audit_parser.add_argument("--max-usage", type=int, default=1, help="Maximum usage count")
    audit_parser.add_argument("--limit", type=int, default=20, help="Limit results")
    audit_parser.set_defaults(func=audit_unused)

    stats_parser = subparsers.add_parser("stats", help="Show system statistics")
    stats_parser.set_defaults(func=stats)

    run_parser = subparsers.add_parser("run-extraction", help="Run memory extraction")
    run_parser.add_argument("--limit", type=int, default=10, help="Max items to process")
    run_parser.set_defaults(func=run_extraction)

    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up old data")
    cleanup_parser.add_argument("--days", type=int, default=90, help="Delete retired facts older than X days")
    cleanup_parser.add_argument("--no-archive", action="store_true", help="Do not archive facts/citations before deletion")
    cleanup_parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after cleanup (can be slow)")
    cleanup_parser.set_defaults(func=cleanup)

    args = parser.parse_args()

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nℹ️  Operation cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        if args.command in ["approve", "retire"]:
            print("Hint: Use 'list' to see available facts")
        sys.exit(1)


if __name__ == "__main__":
    main()
