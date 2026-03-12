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

# Load environment variables from .env for local CLI parity with run_pipeline.py.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from ops.storage import OpsStorage


def get_storage(args) -> OpsStorage:
    return OpsStorage(args.db)


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

        storage.log_audit(
            operation="approve_fact",
            target_type="memory_fact",
            target_id=args.id,
            user=getpass.getuser(),
            before_state=before_state,
            after_state=after_state,
            reason=args.reason or "CLI approval",
            conn=conn,
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

        storage.log_audit(
            operation="retire_fact",
            target_type="memory_fact",
            target_id=args.id,
            user=getpass.getuser(),
            before_state=before_state,
            after_state=after_state,
            reason=args.reason or "CLI retirement",
            conn=conn,
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

        storage.log_audit(
            operation="reset_action",
            target_type="action_state",
            target_id=args.action_id,
            user=getpass.getuser(),
            before_state=before_state,
            after_state=after_state,
            reason=args.reason or "CLI reset",
            conn=conn,
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

        storage.log_audit(
            operation="cleanup",
            target_type="system",
            target_id=None,
            user=getpass.getuser(),
            before_state={"retired_facts_eligible": before_count},
            after_state={"retired_facts_eligible": max(0, before_count - deleted)},
            reason=f"Cleanup older than {args.days} days (archive={not args.no_archive}, vacuum={args.vacuum})",
            conn=conn,
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


def list_incidents_cmd(args):
    from ops.maintenance.incident import list_incidents as _list_incidents

    incidents = _list_incidents(status_filter=args.status)
    if not incidents:
        print("No incidents found.")
        return

    print(f"\n{'ID':<40s} {'Status':<14s} {'Component':<20s} {'Error'}")
    print("=" * 100)
    for inc in incidents:
        err_short = inc.error_message[:50] + "..." if len(inc.error_message) > 50 else inc.error_message
        print(f"{inc.incident_id:<40s} {inc.status:<14s} {inc.component:<20s} {err_short}")


def show_incident_cmd(args):
    from ops.maintenance.incident import load_incident

    incident = load_incident(args.incident_id)
    if not incident:
        print(f"Incident {args.incident_id} not found")
        sys.exit(1)

    print(f"\nIncident: {incident.incident_id}")
    print(f"Component: {incident.component}")
    print(f"Status: {incident.status}")
    print(f"Error Type: {incident.error_type}")
    print(f"Error: {incident.error_message}")
    print(f"Created: {incident.created_at}")
    print(f"Updated: {incident.updated_at}")
    if incident.artifact_dir:
        print(f"Artifacts: {incident.artifact_dir}")
    if incident.traceback_text:
        print(f"\nTraceback:\n{incident.traceback_text}")
    if incident.repair_attempts:
        print(f"\nRepair Attempts ({len(incident.repair_attempts)}):")
        for attempt in incident.repair_attempts:
            print(f"  [{attempt.get('timestamp', '?')}] {attempt.get('status', '?')}: {attempt.get('notes', '')[:100]}")


def repair_latest_cmd(args):
    from ops.maintenance.repair_agent import RepairAgent

    agent = RepairAgent()
    if not agent.available:
        print("claude CLI not available. Install with: npm install -g @anthropic-ai/claude")
        sys.exit(1)

    result = agent.repair_latest()
    if result.get("message"):
        print(result["message"])
    elif result.get("success"):
        print("Repair completed successfully")
        if result.get("output"):
            print(result["output"][:500])
    else:
        print(f"Repair failed: {result.get('error', 'unknown')}")
        sys.exit(1)


def repair_cmd(args):
    from ops.maintenance.repair_agent import RepairAgent

    agent = RepairAgent()
    if not agent.available:
        print("claude CLI not available. Install with: npm install -g @anthropic-ai/claude")
        sys.exit(1)

    result = agent.repair_incident(args.incident_id)
    if result.get("message"):
        print(result["message"])
    elif result.get("success"):
        print("Repair completed successfully")
        if result.get("output"):
            print(result["output"][:500])
    else:
        print(f"Repair failed: {result.get('error', 'unknown')}")
        sys.exit(1)


def monitor_status(args):
    """Quick ops health summary — ASCII-safe output."""
    storage = get_storage(args)

    from ops.monitoring.metrics import OpsMetricsCollector
    collector = OpsMetricsCollector(storage)
    snap = collector.collect()

    print("\nOPS MONITOR STATUS")
    print("=" * 60)
    print(f"  Overall Health: {snap.overall_health_pct:.1f}%")
    print(f"  Extractions (24h): {snap.extractions_24h}")
    print(f"  Total Cost (24h): ${snap.total_cost_24h}")
    print(f"  Total Facts: {snap.total_facts}")
    print(f"  Open Incidents: {snap.open_incidents}")
    print(f"  Audit Entries (24h): {snap.audit_entries_24h}")

    if snap.health_summary:
        print("\nComponents:")
        print("-" * 60)
        for comp, metrics in snap.health_summary.items():
            pct = metrics["health_percent"]
            if pct >= 90:
                tag = "[OK]"
            elif pct >= 70:
                tag = "[WARN]"
            else:
                tag = "[CRIT]"
            latency = metrics.get("avg_latency_ms", 0)
            print(f"  {tag:6s} {comp:20s} {pct:5.1f}% healthy  ({latency:.1f}ms avg)")
    else:
        print("\n  No health data recorded yet.")

    if snap.facts_by_status:
        print("\nFacts by Status:")
        for status, count in sorted(snap.facts_by_status.items()):
            print(f"  {status:10s}: {count}")

    print()


def monitor_alerts(args):
    """Check alert rules and optionally send notifications."""
    storage = get_storage(args)

    from ops.monitoring.metrics import OpsMetricsCollector
    from ops.monitoring.alerts import AlertEngine

    collector = OpsMetricsCollector(storage)
    snap = collector.collect()
    engine = AlertEngine()
    alerts = engine.evaluate(snap)

    if not alerts:
        print("\nNo alerts fired. All systems nominal.")
    else:
        print(f"\n{len(alerts)} alert(s) fired:")
        print("-" * 60)
        for alert in alerts:
            sev = alert.severity.upper()
            if sev == "CRITICAL":
                tag = "[CRIT]"
            elif sev == "WARNING":
                tag = "[WARN]"
            else:
                tag = "[INFO]"
            print(f"  {tag:6s} {alert.rule_name}: {alert.message}")

    if getattr(args, "send", False) and alerts:
        try:
            from ops.monitoring.notifier import OpsAlertNotifier
            notifier = OpsAlertNotifier(storage)
            result = notifier.send_alerts(alerts)
            sent = result.get("sent", 0)
            suppressed = result.get("suppressed", 0)
            print(f"\nNotifications: {sent} sent, {suppressed} suppressed (cooldown)")
        except Exception as e:
            print(f"\nNotification failed (graceful): {e}")

    print()


def monitor_history(args):
    """Show extraction history trends."""
    storage = get_storage(args)

    from ops.monitoring.metrics import OpsMetricsCollector
    collector = OpsMetricsCollector(storage)
    history = collector.get_daily_history(days=args.days)

    print(f"\nEXTRACTION HISTORY (last {args.days} days)")
    print("=" * 60)

    if not history:
        print("  No extraction runs recorded.")
    else:
        print(f"  {'Date':<12s} {'Runs':>5s} {'Cost':>10s} {'Avg Duration':>14s}")
        print(f"  {'-'*12} {'-'*5} {'-'*10} {'-'*14}")
        for entry in history:
            print(
                f"  {entry['date']:<12s} {entry['runs']:>5d} "
                f"${entry['cost']:>8s} {entry['avg_duration_s']:>12.1f}s"
            )

    print()


def rules_list_cmd(args):
    """List all alert rules (builtin DB rows + custom)."""
    storage = get_storage(args)
    rules = storage.list_alert_rules()

    print("\nALERT RULES")
    print("=" * 78)

    if not rules:
        print("  No rules configured.")
    else:
        print(
            f"  {'ID':<5s} {'Name':<25s} {'Severity':<10s} {'Enabled':<8s} "
            f"{'Builtin':<8s} {'Component'}"
        )
        print(
            f"  {'-'*5} {'-'*25} {'-'*10} {'-'*8} {'-'*8} {'-'*12}"
        )
        for r in rules:
            enabled = "Yes" if r["enabled"] else "No"
            builtin = "Yes" if r.get("is_builtin") else "No"
            comp = r.get("component") or "-"
            name = r["name"][:25]
            print(
                f"  {r['id']:<5d} {name:<25s} {r['severity']:<10s} "
                f"{enabled:<8s} {builtin:<8s} {comp}"
            )

    print()


def rules_add_cmd(args):
    """Create a new custom alert rule."""
    # Validate JSON condition
    try:
        condition = json.loads(args.condition)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON condition: {e}")
        sys.exit(1)

    if not isinstance(condition, dict):
        print("Error: condition must be a JSON object")
        sys.exit(1)

    storage = get_storage(args)

    try:
        rid = storage.create_alert_rule(
            name=args.name,
            condition=condition,
            severity=args.severity,
            message_template=args.message,
            component=getattr(args, "component", None),
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Rule created: id={rid} name={args.name} severity={args.severity}")


def rules_enable_cmd(args):
    """Enable a rule by ID."""
    storage = get_storage(args)
    rule = storage.get_alert_rule(args.rule_id)
    if not rule:
        print(f"Error: rule {args.rule_id} not found")
        sys.exit(1)

    storage.update_alert_rule(args.rule_id, enabled=True)
    print(f"Rule {args.rule_id} ({rule['name']}) enabled")


def rules_disable_cmd(args):
    """Disable a rule by ID."""
    storage = get_storage(args)
    rule = storage.get_alert_rule(args.rule_id)
    if not rule:
        print(f"Error: rule {args.rule_id} not found")
        sys.exit(1)

    storage.update_alert_rule(args.rule_id, enabled=False)
    print(f"Rule {args.rule_id} ({rule['name']}) disabled")


def rules_delete_cmd(args):
    """Delete a custom rule by ID."""
    storage = get_storage(args)
    success = storage.delete_alert_rule(args.rule_id)
    if not success:
        rule = storage.get_alert_rule(args.rule_id)
        if rule and rule.get("is_builtin"):
            print(f"Error: cannot delete builtin rule {args.rule_id}")
        else:
            print(f"Error: rule {args.rule_id} not found")
        sys.exit(1)

    print(f"Rule {args.rule_id} deleted")


def rules_test_cmd(args):
    """Dry-run evaluate a single rule against the current snapshot."""
    storage = get_storage(args)
    rule = storage.get_alert_rule(args.rule_id)
    if not rule:
        print(f"Error: rule {args.rule_id} not found")
        sys.exit(1)

    from ops.monitoring.metrics import OpsMetricsCollector
    from ops.monitoring.rule_evaluator import evaluate_condition

    collector = OpsMetricsCollector(storage)
    snap = collector.collect()
    snapshot_dict = snap.to_dict()

    # Enrich with scheduler metrics
    from ops.monitoring.alerts import AlertEngine
    sched_metrics = AlertEngine.collect_scheduler_metrics(storage)
    enriched = {**snapshot_dict, **sched_metrics}

    # Get history for trend rules
    history_rows = storage.get_metric_snapshots(hours=24 * 7, limit=20)
    history = [row["snapshot"] for row in history_rows]

    try:
        condition = json.loads(rule["condition_json"])
        fired = evaluate_condition(condition, enriched, history)
    except Exception as e:
        print(f"Error evaluating rule: {e}")
        sys.exit(1)

    print(f"\nRule: {rule['name']} (id={rule['id']}, severity={rule['severity']})")
    if fired:
        print(f"Result: FIRED - {rule['message_template']}")
    else:
        print(f"Result: OK (did not fire)")

    print()


def monitor_snapshots_cmd(args):
    """Show metric snapshot history."""
    storage = get_storage(args)
    hours = getattr(args, "hours", 24)
    snapshots = storage.get_metric_snapshots(hours=hours)

    print(f"\nMETRIC SNAPSHOTS (last {hours} hours)")
    print("=" * 70)

    if not snapshots:
        print("  No snapshots recorded.")
    else:
        print(
            f"  {'ID':<6s} {'Timestamp':<22s} {'Health%':>8s} "
            f"{'Cost':>8s} {'Incidents':>10s}"
        )
        print(
            f"  {'-'*6} {'-'*22} {'-'*8} {'-'*8} {'-'*10}"
        )
        for s in snapshots:
            snap = s["snapshot"]
            ts = s["timestamp"]
            if ts and len(str(ts)) > 22:
                ts = str(ts)[:22]
            health = snap.get("overall_health_pct", "-")
            cost = snap.get("total_cost_24h", "-")
            incidents = snap.get("open_incidents", "-")
            print(
                f"  {s['id']:<6d} {str(ts):<22s} {str(health):>8s} "
                f"{str(cost):>8s} {str(incidents):>10s}"
            )

    print()


def docker_status_cmd(args):
    from ops.infra.docker_manager import DockerManager

    mgr = DockerManager()
    if not mgr.available:
        print("Docker CLI not found on PATH")
        sys.exit(1)

    result = mgr.service_status(name=args.name)
    if not result["success"]:
        print(f"Error: {result.get('error', 'unknown')}")
        sys.exit(1)

    containers = result.get("containers", [])
    if not containers:
        print("No running containers found.")
        return

    print(f"\n{'Name':<30s} {'Status':<20s} {'Image'}")
    print("=" * 80)
    for c in containers:
        print(f"{c.get('Names', 'N/A'):<30s} {c.get('Status', 'N/A'):<20s} {c.get('Image', 'N/A')}")


def docker_restart_cmd(args):
    from ops.infra.docker_manager import DockerManager

    mgr = DockerManager()
    if not mgr.available:
        print("Docker CLI not found on PATH")
        sys.exit(1)

    result = mgr.restart_service(args.name)
    if result["success"]:
        print(f"Restarted: {args.name}")
    else:
        print(f"Restart failed: {result.get('error', 'unknown')}")
        sys.exit(1)


def docker_prune_cmd(args):
    from ops.infra.docker_manager import DockerManager

    mgr = DockerManager()
    if not mgr.available:
        print("Docker CLI not found on PATH")
        sys.exit(1)

    result = mgr.prune_networks()
    if result["success"]:
        print("Network prune completed")
        if result.get("output"):
            print(result["output"].strip())
    else:
        print(f"Prune failed: {result.get('error', 'unknown')}")
        sys.exit(1)


def schedule_add_cmd(args):
    """Create a new pipeline schedule."""
    from croniter import croniter as _croniter

    # Validate cron expression
    if not _croniter.is_valid(args.cron_expression):
        print(f"Error: invalid cron expression: {args.cron_expression}")
        sys.exit(1)

    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler, ScheduleConfig

    scheduler = PipelineScheduler(storage)
    collectors = [c.strip() for c in args.collectors.split(",") if c.strip()] if args.collectors else []

    config = ScheduleConfig(
        name=args.name,
        cron_expression=args.cron_expression,
        collectors=collectors,
        mode=args.mode,
        dry_run=args.dry_run,
    )

    try:
        sid = scheduler.create_schedule(config)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Schedule created: id={sid} name={config.name} cron={config.cron_expression}")


def schedule_list_cmd(args):
    """List all pipeline schedules."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)
    schedules = scheduler.list_schedules()

    if not schedules:
        print("\nNo schedules configured.")
        return

    print(f"\nPIPELINE SCHEDULES ({len(schedules)})")
    print("=" * 70)
    print(f"  {'ID':<5s} {'Name':<20s} {'Cron':<16s} {'Mode':<10s} {'Enabled':<8s}")
    print(f"  {'-'*5} {'-'*20} {'-'*16} {'-'*10} {'-'*8}")

    for s in schedules:
        enabled = "Yes" if s["enabled"] else "No"
        name = s["name"][:20]
        cron = s["cron_expression"][:16]
        print(f"  {s['id']:<5d} {name:<20s} {cron:<16s} {s['mode']:<10s} {enabled:<8s}")

    print()


def schedule_status_cmd(args):
    """Show detailed status for a schedule."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)

    try:
        status = scheduler.get_schedule_status(args.schedule_id)
    except ValueError:
        print(f"Error: schedule {args.schedule_id} not found")
        sys.exit(1)

    print(f"\nSCHEDULE STATUS: {status['name']}")
    print("=" * 60)
    print(f"  ID:              {status['id']}")
    print(f"  Cron:            {status['cron_expression']}")
    print(f"  Enabled:         {'Yes' if status['enabled'] else 'No'}")
    print(f"  Next Run:        {status['next_run'] or 'N/A'}")
    print(f"  Total Runs:      {status['total_runs']}")
    print(f"  Success Rate:    {status['success_rate']:.1f}%")

    if status["last_run"]:
        lr = status["last_run"]
        print(f"  Last Run:        {lr.get('status', 'N/A')} at {lr.get('started_at', 'N/A')}")
    else:
        print(f"  Last Run:        None")

    print()


def schedule_run_cmd(args):
    """Manually enqueue a pipeline run for a schedule."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)

    try:
        run_id = scheduler.enqueue_run(args.schedule_id)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Run enqueued: run_id={run_id} schedule_id={args.schedule_id}")


def schedule_pause_cmd(args):
    """Pause a schedule (disable)."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)

    try:
        scheduler.pause_schedule(args.schedule_id)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Schedule {args.schedule_id} paused")


def schedule_resume_cmd(args):
    """Resume a paused schedule (enable)."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)

    try:
        scheduler.resume_schedule(args.schedule_id)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Schedule {args.schedule_id} resumed (enabled)")


def schedule_history_cmd(args):
    """Show run history for a schedule."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)

    schedule = scheduler.get_schedule(args.schedule_id)
    if not schedule:
        print(f"Error: schedule {args.schedule_id} not found")
        sys.exit(1)

    history = scheduler.get_run_history(args.schedule_id, limit=args.limit)

    print(f"\nRUN HISTORY: {schedule['name']} (id={args.schedule_id})")
    print("=" * 70)

    if not history:
        print("  No run history recorded.")
    else:
        print(f"  {'ID':<6s} {'Status':<10s} {'Started':<22s} {'Signals':<10s} {'Errors':<8s}")
        print(f"  {'-'*6} {'-'*10} {'-'*22} {'-'*10} {'-'*8}")
        for run in history:
            started = run.get("started_at", "N/A")
            if started and len(str(started)) > 22:
                started = str(started)[:22]
            signals = run.get("signals_found", 0) or 0
            errors = run.get("errors", 0) or 0
            status_str = run.get("status", "N/A")
            print(f"  {run['id']:<6d} {status_str:<10s} {str(started):<22s} {signals:<10d} {errors:<8d}")

    print()


def schedule_delete_cmd(args):
    """Delete a schedule."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler

    scheduler = PipelineScheduler(storage)

    try:
        scheduler.delete_schedule(args.schedule_id)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"Schedule {args.schedule_id} deleted")


def schedule_add_quality_sync_cmd(args):
    """Create quality-sync schedule (sync Notion status events every 6 hours)."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler, ScheduleConfig

    scheduler = PipelineScheduler(storage)

    config = ScheduleConfig(
        name="quality-sync-notion-status",
        cron_expression="0 */6 * * *",  # Every 6 hours
        collectors=[],
        mode="quality-sync",
        enabled=not args.disabled,
    )

    try:
        sid = scheduler.create_schedule(config)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"✅ Quality sync schedule created: id={sid}")
    print(f"   Name: {config.name}")
    print(f"   Cron: {config.cron_expression} (every 6 hours)")
    print(f"   Mode: {config.mode}")
    print(f"   Enabled: {config.enabled}")


def schedule_add_quality_classify_cmd(args):
    """Create quality-classify schedule (batch classify signals daily at 2am UTC)."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler, ScheduleConfig

    scheduler = PipelineScheduler(storage)

    config = ScheduleConfig(
        name="quality-thesis-classify-batch",
        cron_expression="0 2 * * *",  # Daily at 2am UTC
        collectors=[],
        mode="quality-classify",
        enabled=not args.disabled,
    )

    try:
        sid = scheduler.create_schedule(config)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"✅ Quality classify schedule created: id={sid}")
    print(f"   Name: {config.name}")
    print(f"   Cron: {config.cron_expression} (daily at 2am UTC)")
    print(f"   Mode: {config.mode}")
    print(f"   Enabled: {config.enabled}")
    print(f"   Note: Requires LLM_THESIS_MODE=shadow or active")


def schedule_add_quality_patterns_cmd(args):
    """Create quality-patterns schedule (detect FP patterns weekly on Sundays at 3am UTC)."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler, ScheduleConfig

    scheduler = PipelineScheduler(storage)

    config = ScheduleConfig(
        name="quality-find-patterns",
        cron_expression="0 3 * * 0",  # Sundays at 3am UTC
        collectors=[],
        mode="quality-patterns",
        enabled=not args.disabled,
    )

    try:
        sid = scheduler.create_schedule(config)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    print(f"✅ Quality patterns schedule created: id={sid}")
    print(f"   Name: {config.name}")
    print(f"   Cron: {config.cron_expression} (Sundays at 3am UTC)")
    print(f"   Mode: {config.mode}")
    print(f"   Enabled: {config.enabled}")
    print(f"   Note: Pattern results stored in ops DB (pattern_runs table)")


def schedule_tick_cmd(args):
    """Execute all due enabled schedules (or one by --name)."""
    import asyncio
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler
    scheduler = PipelineScheduler(storage)

    async def _tick():
        if args.name:
            sched = scheduler.get_schedule_by_name(args.name)
            if not sched:
                print(f"Error: schedule '{args.name}' not found")
                return 1
            if not sched["enabled"]:
                print(f"Schedule '{args.name}' is disabled, skipping")
                return 0
            if not scheduler.should_run(sched["id"]):
                print(f"Schedule '{args.name}' is not due, skipping")
                return 0
            targets = [sched]
        else:
            all_scheds = scheduler.list_schedules()
            targets = [s for s in all_scheds if s["enabled"] and scheduler.should_run(s["id"])]

        if not targets:
            print("No schedules due")
            return 0

        failures = 0
        for sched in targets:
            print(f"Executing: {sched['name']} (id={sched['id']}, mode={sched['mode']})")
            try:
                result = await scheduler.execute_run(sched["id"])
                status = result.get("status", "unknown")
                print(f"  -> {status} (duration={result.get('duration_seconds', 0):.1f}s)")
                if status == "failed":
                    failures += 1
            except Exception as e:
                print(f"  -> ERROR: {e}")
                failures += 1

        return 1 if failures else 0

    exit_code = asyncio.run(_tick())
    sys.exit(exit_code)


def schedule_add_canary_monitor_cmd(args):
    """Create canary-monitor schedule (every 6h). Idempotent."""
    storage = get_storage(args)
    from ops.scheduler import PipelineScheduler, ScheduleConfig
    scheduler = PipelineScheduler(storage)

    config = ScheduleConfig(
        name="canary-monitor-6h",
        cron_expression="0 */6 * * *",
        collectors=[],
        mode="canary-monitor",
        enabled=not args.disabled,
    )

    sid, created, warnings = scheduler.ensure_schedule(config)
    if created:
        print(f"Canary monitor schedule created: id={sid}")
    else:
        print(f"Canary monitor schedule already exists: id={sid}")
    print(f"   Name: {config.name}")
    print(f"   Mode: canary-monitor")
    print(f"   Cron: 0 */6 * * * (every 6 hours)")
    for w in warnings:
        print(f"   WARNING: {w}")


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
  python3 ops/cli.py schedule add nightly "0 2 * * *" --collectors github,sec_edgar
  python3 ops/cli.py schedule list
  python3 ops/cli.py schedule status 1
  python3 ops/cli.py schedule run 1

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

    # ── monitor commands ────────────────────────────────────────────
    monitor_parser = subparsers.add_parser("monitor", help="Monitoring commands")
    monitor_sub = monitor_parser.add_subparsers(dest="monitor_command", help="Monitor sub-command")
    monitor_sub.required = True

    monitor_status_p = monitor_sub.add_parser("status", help="Quick health summary")
    monitor_status_p.set_defaults(func=monitor_status)

    monitor_alerts_p = monitor_sub.add_parser("alerts", help="Check alert rules")
    monitor_alerts_p.add_argument("--send", action="store_true", help="Send alerts via notifier (for cron)")
    monitor_alerts_p.set_defaults(func=monitor_alerts)

    monitor_history_p = monitor_sub.add_parser("history", help="Extraction trends")
    monitor_history_p.add_argument("--days", type=int, default=7, help="Number of days")
    monitor_history_p.set_defaults(func=monitor_history)

    # ── monitor rules sub-sub-commands ──────────────────────────────
    rules_parser = monitor_sub.add_parser("rules", help="Alert rule management")
    rules_sub = rules_parser.add_subparsers(dest="rules_command", help="Rules sub-command")
    rules_sub.required = True

    rules_list_p = rules_sub.add_parser("list", help="List all alert rules")
    rules_list_p.set_defaults(func=rules_list_cmd)

    rules_add_p = rules_sub.add_parser("add", help="Create a custom alert rule")
    rules_add_p.add_argument("--name", required=True, help="Rule name (unique)")
    rules_add_p.add_argument("--condition", required=True, help="JSON DSL condition")
    rules_add_p.add_argument(
        "--severity", required=True, choices=["critical", "warning", "info"],
        help="Alert severity",
    )
    rules_add_p.add_argument("--message", required=True, help="Alert message template")
    rules_add_p.add_argument("--component", default=None, help="Component tag (optional)")
    rules_add_p.set_defaults(func=rules_add_cmd)

    rules_enable_p = rules_sub.add_parser("enable", help="Enable a rule")
    rules_enable_p.add_argument("rule_id", type=int, help="Rule ID")
    rules_enable_p.set_defaults(func=rules_enable_cmd)

    rules_disable_p = rules_sub.add_parser("disable", help="Disable a rule")
    rules_disable_p.add_argument("rule_id", type=int, help="Rule ID")
    rules_disable_p.set_defaults(func=rules_disable_cmd)

    rules_delete_p = rules_sub.add_parser("delete", help="Delete a custom rule")
    rules_delete_p.add_argument("rule_id", type=int, help="Rule ID")
    rules_delete_p.set_defaults(func=rules_delete_cmd)

    rules_test_p = rules_sub.add_parser("test", help="Dry-run evaluate a rule")
    rules_test_p.add_argument("rule_id", type=int, help="Rule ID")
    rules_test_p.set_defaults(func=rules_test_cmd)

    # ── monitor snapshots ───────────────────────────────────────────
    monitor_snap_p = monitor_sub.add_parser("snapshots", help="Metric snapshot history")
    monitor_snap_p.add_argument("--hours", type=int, default=24, help="Hours to look back")
    monitor_snap_p.set_defaults(func=monitor_snapshots_cmd)

    # ── maint (maintenance / incident) commands ──────────────────────
    maint_parser = subparsers.add_parser("maint", help="Maintenance / incident commands")
    maint_sub = maint_parser.add_subparsers(dest="maint_command", help="Maintenance sub-command")
    maint_sub.required = True

    maint_list = maint_sub.add_parser("list-incidents", help="List incident capsules")
    maint_list.add_argument(
        "--status",
        choices=["open", "investigating", "resolved", "wont_fix"],
        help="Filter by status",
    )
    maint_list.set_defaults(func=list_incidents_cmd)

    maint_show = maint_sub.add_parser("show", help="Show incident details")
    maint_show.add_argument("incident_id", help="Incident ID")
    maint_show.set_defaults(func=show_incident_cmd)

    maint_repair_latest = maint_sub.add_parser("repair-latest", help="Repair most recent open incident")
    maint_repair_latest.set_defaults(func=repair_latest_cmd)

    maint_repair = maint_sub.add_parser("repair", help="Repair a specific incident")
    maint_repair.add_argument("incident_id", help="Incident ID")
    maint_repair.set_defaults(func=repair_cmd)

    # ── docker commands ──────────────────────────────────────────────
    docker_parser = subparsers.add_parser("docker", help="Docker management commands")
    docker_sub = docker_parser.add_subparsers(dest="docker_command", help="Docker sub-command")
    docker_sub.required = True

    docker_status_p = docker_sub.add_parser("status", help="Show running containers")
    docker_status_p.add_argument("name", nargs="?", default=None, help="Filter by container name")
    docker_status_p.set_defaults(func=docker_status_cmd)

    docker_restart_p = docker_sub.add_parser("restart", help="Restart a container")
    docker_restart_p.add_argument("name", help="Container name")
    docker_restart_p.set_defaults(func=docker_restart_cmd)

    docker_prune_p = docker_sub.add_parser("prune-networks", help="Remove unused Docker networks")
    docker_prune_p.set_defaults(func=docker_prune_cmd)

    # ── schedule commands ───────────────────────────────────────────
    schedule_parser = subparsers.add_parser("schedule", help="Pipeline schedule commands")
    schedule_sub = schedule_parser.add_subparsers(dest="schedule_command", help="Schedule sub-command")
    schedule_sub.required = True

    sched_add_p = schedule_sub.add_parser("add", help="Create a new schedule")
    sched_add_p.add_argument("name", help="Schedule name (unique)")
    sched_add_p.add_argument("cron_expression", help="Cron expression (e.g. '0 2 * * *')")
    sched_add_p.add_argument("--collectors", default="", help="Comma-separated collector names")
    sched_add_p.add_argument("--mode", choices=["full", "collect", "process", "quality-sync", "quality-classify", "quality-patterns", "canary-monitor"], default="full", help="Pipeline mode")
    sched_add_p.add_argument("--dry-run", action="store_true", help="Enable dry-run mode")
    sched_add_p.set_defaults(func=schedule_add_cmd)

    sched_list_p = schedule_sub.add_parser("list", help="List all schedules")
    sched_list_p.set_defaults(func=schedule_list_cmd)

    sched_status_p = schedule_sub.add_parser("status", help="Show schedule status")
    sched_status_p.add_argument("schedule_id", type=int, help="Schedule ID")
    sched_status_p.set_defaults(func=schedule_status_cmd)

    sched_run_p = schedule_sub.add_parser("run", help="Manually enqueue a pipeline run")
    sched_run_p.add_argument("schedule_id", type=int, help="Schedule ID")
    sched_run_p.set_defaults(func=schedule_run_cmd)

    sched_pause_p = schedule_sub.add_parser("pause", help="Pause a schedule")
    sched_pause_p.add_argument("schedule_id", type=int, help="Schedule ID")
    sched_pause_p.set_defaults(func=schedule_pause_cmd)

    sched_resume_p = schedule_sub.add_parser("resume", help="Resume a paused schedule")
    sched_resume_p.add_argument("schedule_id", type=int, help="Schedule ID")
    sched_resume_p.set_defaults(func=schedule_resume_cmd)

    sched_history_p = schedule_sub.add_parser("history", help="Show run history")
    sched_history_p.add_argument("schedule_id", type=int, help="Schedule ID")
    sched_history_p.add_argument("--limit", type=int, default=20, help="Max entries to show")
    sched_history_p.set_defaults(func=schedule_history_cmd)

    sched_delete_p = schedule_sub.add_parser("delete", help="Delete a schedule")
    sched_delete_p.add_argument("schedule_id", type=int, help="Schedule ID")
    sched_delete_p.set_defaults(func=schedule_delete_cmd)

    # Quality schedule convenience commands
    sched_add_qsync_p = schedule_sub.add_parser("add-quality-sync", help="Create quality-sync schedule (every 6h)")
    sched_add_qsync_p.add_argument("--disabled", action="store_true", help="Create in disabled state")
    sched_add_qsync_p.set_defaults(func=schedule_add_quality_sync_cmd)

    sched_add_qclass_p = schedule_sub.add_parser("add-quality-classify", help="Create quality-classify schedule (daily 2am)")
    sched_add_qclass_p.add_argument("--disabled", action="store_true", help="Create in disabled state")
    sched_add_qclass_p.set_defaults(func=schedule_add_quality_classify_cmd)

    sched_add_qpatt_p = schedule_sub.add_parser("add-quality-patterns", help="Create quality-patterns schedule (weekly Sun 3am)")
    sched_add_qpatt_p.add_argument("--disabled", action="store_true", help="Create in disabled state")
    sched_add_qpatt_p.set_defaults(func=schedule_add_quality_patterns_cmd)

    # Canary monitor convenience commands
    sched_add_canary_p = schedule_sub.add_parser("add-canary-monitor",
        help="Create canary-monitor schedule (every 6h, idempotent)")
    sched_add_canary_p.add_argument("--disabled", action="store_true",
        help="Create in disabled state")
    sched_add_canary_p.set_defaults(func=schedule_add_canary_monitor_cmd)

    sched_tick_p = schedule_sub.add_parser("tick", help="Execute all due schedules (or one by --name)")
    sched_tick_p.add_argument("--name", help="Execute only the schedule with this name")
    sched_tick_p.set_defaults(func=schedule_tick_cmd)

    # ── quality ops commands ────────────────────────────────────────
    try:
        from ops.quality_cli import register_quality_commands
        register_quality_commands(subparsers)
    except ImportError:
        pass  # Quality ops module not installed
    except Exception as e:
        print(f"Warning: Quality ops CLI registration failed: {e}", file=sys.stderr)

    # ── governance commands ────────────────────────────────────────
    try:
        from governance.cli import register_governance_commands
        register_governance_commands(subparsers)
    except ImportError:
        pass  # Governance module not installed
    except Exception as e:
        print(f"Warning: Governance CLI registration failed: {e}", file=sys.stderr)

    # ── knowledge graph commands ───────────────────────────────────
    try:
        from ops.graph_cli import register_graph_commands
        register_graph_commands(subparsers)
    except ImportError:
        pass  # Graph module not installed
    except Exception as e:
        print(f"Warning: Graph CLI registration failed: {e}", file=sys.stderr)

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
