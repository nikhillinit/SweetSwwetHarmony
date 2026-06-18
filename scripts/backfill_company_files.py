"""
One-time backfill: re-group existing signals by standardized canonical keys
and upsert multi-source company_files.

Phase 3 of Step 3a remediation.

Usage:
    # Dry-run analysis (read-only, no DB writes)
    python scripts/backfill_company_files.py --db signals.db

    # Actually write company file updates
    python scripts/backfill_company_files.py --db signals.db --write
    python scripts/backfill_company_files.py --db signals.db --commit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

sys.path.insert(0, ".")

from utils.canonical_keys import build_canonical_key, is_strong_key
from utils.db_ops_ledger import append_db_ops_ledger
from utils.db_path_helper import resolve_db_path_env
from utils.db_tool_errors import DBToolError
from utils.db_tool_lock import DBToolLock
from utils.db_tool_preflight import read_sqlite_data_version
from utils.report_envelope import create_report, write_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

TOOL_NAME = "backfill_company_files"
ACTION = "backfill_company_files"
LOCK_TIMEOUT_SECONDS = 5


class BackfillCompanyFilesError(DBToolError):
    """Company-file backfill failure with rollback evidence."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        preflight_data_version: int | None = None,
        signals_total: int | None = None,
        group_count: int | None = None,
        multi_source_groups: int | None = None,
        attempted_upserts: int | None = None,
        created_attempted: int = 0,
        updated_attempted: int = 0,
        skipped: int = 0,
        current_key: str | None = None,
        current_source_api: str | None = None,
    ) -> None:
        super().__init__(
            message,
            partial_evidence={
                "phase": phase,
                "preflight_data_version": preflight_data_version,
                "signals_total": signals_total,
                "group_count": group_count,
                "multi_source_groups": multi_source_groups,
                "attempted_upserts": attempted_upserts,
                "created_attempted": created_attempted,
                "updated_attempted": updated_attempted,
                "skipped": skipped,
                "current_key": current_key,
                "current_source_api": current_source_api,
                "transaction": "rolled_back",
                "partial_success": False,
            },
        )

# Publisher domains that should NOT be used as canonical keys —
# these are news/aggregator sites, not actual companies.
PUBLISHER_DOMAINS = frozenset(
    {
        "prnewswire.com",
        "globenewswire.com",
        "businesswire.com",
        "techcrunch.com",
        "venturebeat.com",
        "theverge.com",
        "wired.com",
        "arstechnica.com",
        "usaherald.com",
        "reuters.com",
        "bloomberg.com",
        "cnbc.com",
        "forbes.com",
        "wsj.com",
        "nytimes.com",
        "bbc.com",
        "bbc.co.uk",
        "theguardian.com",
        "medium.com",
        "substack.com",
        "reddit.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "producthunt.com",
        "news.ycombinator.com",
        "ycombinator.com",
        "github.com",
        "github.io",
    }
)


def _extract_domain_from_url(url: str) -> str:
    """Extract root domain from a URL, stripping www. prefix."""
    if not url:
        return ""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc or parsed.path.split("/")[0]
        host = host.lower().strip()
        if host.startswith("www."):
            host = host[4:]
        # Strip port
        if ":" in host:
            host = host.split(":")[0]
        return host
    except Exception:
        return ""


def _is_publisher(domain: str) -> bool:
    """Check if domain is a known publisher/aggregator (not a company)."""
    if not domain:
        return True
    # Check exact match and parent domain
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in PUBLISHER_DOMAINS:
            return True
    return False


@dataclass
class SignalRecord:
    id: int
    company_name: Optional[str]
    canonical_key: str
    source_api: str
    raw_data: dict
    recomputed_key: str = ""


@dataclass
class CompanyGroup:
    canonical_key: str
    signals: List[SignalRecord] = field(default_factory=list)
    source_apis: set = field(default_factory=set)
    best_name: Optional[str] = None

    @property
    def is_multi_source(self) -> bool:
        return len(self.source_apis) >= 2


def recompute_canonical_key(sig: SignalRecord) -> str:
    """Re-derive a canonical key from raw_data using build_canonical_key().

    Only UPGRADES weak keys — never replaces a strong key with a weaker one.

    Priority:
    1. If existing key is already strong (domain:, companies_house:, etc.), keep it
    2. company_domain from raw_data (hacker_news has this)
    3. Domain extracted from URL (if not a publisher)
    4. github_org from raw_data
    5. Fallback to company_name → name_loc:
    6. Keep original key as last resort
    """
    rd = sig.raw_data

    # 1. If existing key is already strong, keep it (never downgrade)
    if is_strong_key(sig.canonical_key):
        return sig.canonical_key

    # 2. company_domain field (hacker_news populates this)
    company_domain = rd.get("company_domain", "")
    if company_domain and not _is_publisher(company_domain):
        return build_canonical_key(domain_or_website=company_domain)

    # 3. URL-based domain (skip publishers)
    url = rd.get("url", "")
    if url:
        domain = _extract_domain_from_url(url)
        if domain and not _is_publisher(domain):
            return build_canonical_key(domain_or_website=domain)

    # 4. GitHub org
    repo_name = rd.get("repo_name", "")
    if repo_name and "/" in repo_name:
        org = repo_name.split("/")[0]
        return build_canonical_key(github_org=org)

    # 5. Fallback: company_name → name_loc key
    name = sig.company_name or rd.get("company_name", "")
    if name and len(name) > 2:
        return build_canonical_key(fallback_company_name=name)

    # 6. Keep original
    return sig.canonical_key


def load_signals(db_path: str) -> List[SignalRecord]:
    """Load all signals from the database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, company_name, canonical_key, source_api, raw_data FROM signals"
    ).fetchall()
    conn.close()

    signals = []
    for r in rows:
        raw = {}
        if r["raw_data"]:
            try:
                raw = json.loads(r["raw_data"])
            except (json.JSONDecodeError, TypeError):
                pass
        signals.append(
            SignalRecord(
                id=r["id"],
                company_name=r["company_name"],
                canonical_key=r["canonical_key"] or "",
                source_api=r["source_api"] or "",
                raw_data=raw,
            )
        )
    return signals


def group_signals(signals: List[SignalRecord]) -> Dict[str, CompanyGroup]:
    """Group signals by recomputed canonical key."""
    groups: Dict[str, CompanyGroup] = {}

    for sig in signals:
        sig.recomputed_key = recompute_canonical_key(sig)
        key = sig.recomputed_key

        if key not in groups:
            groups[key] = CompanyGroup(canonical_key=key)
        g = groups[key]
        g.signals.append(sig)
        g.source_apis.add(sig.source_api)
        # Best name: prefer non-None, non-truncated name
        if sig.company_name and (
            g.best_name is None or len(sig.company_name) < len(g.best_name)
        ):
            g.best_name = sig.company_name

    return groups


def analyze(
    signals: List[SignalRecord], groups: Dict[str, CompanyGroup]
) -> None:
    """Print dry-run analysis."""
    print(f"\n{'='*60}")
    print(f"BACKFILL ANALYSIS — {len(signals)} signals, {len(groups)} unique keys")
    print(f"{'='*60}")

    # Key rekeying stats
    rekeyed = sum(1 for s in signals if s.recomputed_key != s.canonical_key)
    print(f"\nRe-keyed signals: {rekeyed}/{len(signals)}")

    # Show rekeyed examples
    if rekeyed:
        print("\nSample re-keyed signals:")
        count = 0
        for s in signals:
            if s.recomputed_key != s.canonical_key and count < 10:
                print(f"  #{s.id} [{s.source_api}] {s.canonical_key!r} -> {s.recomputed_key!r}")
                count += 1

    # Key strength distribution
    strong = sum(1 for k in groups if is_strong_key(k))
    weak = len(groups) - strong
    print(f"\nKey strength: {strong} strong, {weak} weak")

    # Multi-source groups
    multi = [g for g in groups.values() if g.is_multi_source]
    single = len(groups) - len(multi)
    print(f"\nMulti-source groups: {len(multi)}")
    print(f"Single-source groups: {single}")

    if multi:
        print("\nMulti-source groups (will create/update company_files):")
        for g in sorted(multi, key=lambda g: -len(g.source_apis)):
            sources = sorted(g.source_apis)
            sig_ids = [s.id for s in g.signals]
            print(f"  {g.canonical_key}")
            print(f"    Name: {g.best_name}")
            print(f"    Sources: {', '.join(sources)}")
            print(f"    Signals: {sig_ids}")
    else:
        print("\n  (none found — no cross-source convergence with current data)")

    # Source API distribution in groups
    print(f"\nSource API coverage in groups:")
    src_counts: Dict[str, int] = defaultdict(int)
    for g in groups.values():
        for src in g.source_apis:
            src_counts[src] += 1
    for src, cnt in sorted(src_counts.items(), key=lambda x: -x[1]):
        print(f"  {src}: {cnt} groups")

    print()


async def write_company_files(
    db_path: str,
    groups: Dict[str, CompanyGroup],
    *,
    preflight_data_version: int | None = None,
    signals_total: int | None = None,
) -> dict[str, Any]:
    """Write multi-source company files as one fail-fast transaction."""
    # Import here to avoid import issues in dry-run mode
    from storage.signal_store import SignalStore
    from workflows.thin_file_manager import upsert_company_file
    from storage.entity_identity_store import EntityIdentityStore

    created = 0
    updated = 0
    skipped = 0
    current_key: str | None = None
    current_source_api: str | None = None

    multi_groups = [g for g in groups.values() if g.is_multi_source]
    attempted_upserts = sum(len(g.source_apis) for g in multi_groups)
    store = SignalStore(db_path)

    try:
        await store.initialize()
        async with store.transaction_immediate() as tx:
            for g in multi_groups:
                current_key = g.canonical_key
                company_id = EntityIdentityStore.entity_id_for_seed(g.canonical_key)

                for source_api in sorted(g.source_apis):
                    current_source_api = source_api
                    result = await upsert_company_file(
                        store=store,
                        company_id=company_id,
                        company_name=g.best_name,
                        canonical_key=g.canonical_key,
                        source_api=source_api,
                        tx=tx,
                    )
                    if result == "created":
                        created += 1
                    elif result in ("updated", "reactivated"):
                        updated += 1
                    logger.info(
                        "upsert %s key=%s src=%s -> %s",
                        company_id[:8],
                        g.canonical_key,
                        source_api,
                        result,
                    )

        return {
            "dry_run": False,
            "preflight_data_version": preflight_data_version,
            "signals_total": signals_total,
            "group_count": len(groups),
            "multi_source_groups": len(multi_groups),
            "attempted_upserts": attempted_upserts,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "transaction": "committed",
            "partial_success": False,
        }
    except DBToolError:
        raise
    except Exception as exc:
        raise BackfillCompanyFilesError(
            "Failed to backfill company_files",
            phase="upsert_company_file",
            preflight_data_version=preflight_data_version,
            signals_total=signals_total,
            group_count=len(groups),
            multi_source_groups=len(multi_groups),
            attempted_upserts=attempted_upserts,
            created_attempted=created,
            updated_attempted=updated,
            skipped=skipped,
            current_key=current_key,
            current_source_api=current_source_api,
        ) from exc
    finally:
        try:
            await store.close()
        except Exception:
            logger.exception("Failed to close SignalStore after company_files backfill")


def _base_metrics(
    *,
    dry_run: bool,
    preflight_data_version: int | None,
    signals: list[SignalRecord],
    groups: Dict[str, CompanyGroup],
) -> dict[str, Any]:
    multi_groups = [g for g in groups.values() if g.is_multi_source]
    return {
        "dry_run": dry_run,
        "preflight_data_version": preflight_data_version,
        "signals_total": len(signals),
        "group_count": len(groups),
        "multi_source_groups": len(multi_groups),
        "attempted_upserts": sum(len(g.source_apis) for g in multi_groups),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "transaction": "not_started" if dry_run else "not_needed",
        "partial_success": False,
    }


def _write_report_if_requested(
    *,
    report_path: str | None,
    ok: bool,
    started_at: datetime,
    db_path: str,
    metrics: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> None:
    if not report_path:
        return
    report = create_report(
        command=ACTION,
        ok=ok,
        db_path=db_path,
        started_at=started_at,
        metrics=metrics,
        errors=errors,
    )
    write_report(report, report_path)


def _append_ledger(
    *,
    db_path: str,
    status: str,
    details: dict[str, Any],
) -> None:
    append_db_ops_ledger(
        tool_name=TOOL_NAME,
        db_path=db_path,
        action=ACTION,
        status=status,
        details=details,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill company_files from existing signals using standardized canonical keys."
    )
    parser.add_argument("--db", default=None, help="Path to SQLite database")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually write changes (default: dry-run analysis only)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Alias for --write; apply company_file updates",
    )
    parser.add_argument("--report", default="", help="Optional path to write a JSON report envelope")
    args = parser.parse_args()
    args.db = resolve_db_path_env(args.db)

    # Windows console encoding fix
    if sys.platform == "win32":
        sys.stdout.reconfigure(errors="replace")

    write_mode = args.write or args.commit
    report_path = args.report or None
    started_at = datetime.now(timezone.utc)
    preflight_data_version: int | None = None
    lock: DBToolLock | None = None

    try:
        preflight_data_version = read_sqlite_data_version(args.db)
    except Exception as exc:
        details = {
            "phase": "preflight_data_version",
            "commit": write_mode,
            "preflight_data_version": None,
            "error": str(exc),
        }
        if write_mode:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            started_at=started_at,
            db_path=args.db,
            metrics=details,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if write_mode:
        lock = DBToolLock(args.db, tool_name=TOOL_NAME)
        if not lock.acquire(timeout_seconds=LOCK_TIMEOUT_SECONDS):
            holder = lock.get_holder_info()
            error = f"Could not acquire DB tool lock. Holder: {holder}"
            details = {
                "holder": holder,
                "commit": True,
                "preflight_data_version": preflight_data_version,
            }
            _append_ledger(db_path=args.db, status="lock_blocked", details=details)
            _write_report_if_requested(
                report_path=report_path,
                ok=False,
                started_at=started_at,
                db_path=args.db,
                metrics=details,
                errors=[error],
            )
            print(f"ERROR: {error}", file=sys.stderr)
            return 2

    try:
        signals = load_signals(args.db)
        logger.info("Loaded %d signals from %s", len(signals), args.db)

        groups = group_signals(signals)
        logger.info("Grouped into %d unique canonical keys", len(groups))

        analyze(signals, groups)

        multi = [g for g in groups.values() if g.is_multi_source]

        if not write_mode:
            metrics = _base_metrics(
                dry_run=True,
                preflight_data_version=preflight_data_version,
                signals=signals,
                groups=groups,
            )
            _write_report_if_requested(
                report_path=report_path,
                ok=True,
                started_at=started_at,
                db_path=args.db,
                metrics=metrics,
            )
            if multi:
                print(f"Run with --write or --commit to create/update {len(multi)} company files.")
            else:
                print("No multi-source groups found. No writes needed.")
                print("Tip: Run more collectors to build cross-source evidence.")
            return 0

        if not multi:
            metrics = _base_metrics(
                dry_run=False,
                preflight_data_version=preflight_data_version,
                signals=signals,
                groups=groups,
            )
            _write_report_if_requested(
                report_path=report_path,
                ok=True,
                started_at=started_at,
                db_path=args.db,
                metrics=metrics,
            )
            _append_ledger(db_path=args.db, status="success", details=metrics)
            print("No multi-source groups found. Nothing to write.")
            return 0

        print(f"\nWriting {len(multi)} multi-source company files...")
        metrics = asyncio.run(
            write_company_files(
                args.db,
                groups,
                preflight_data_version=preflight_data_version,
                signals_total=len(signals),
            )
        )
        _write_report_if_requested(
            report_path=report_path,
            ok=True,
            started_at=started_at,
            db_path=args.db,
            metrics=metrics,
        )
        _append_ledger(db_path=args.db, status="success", details=metrics)
        print(
            f"\nResults: {metrics['created']} created, "
            f"{metrics['updated']} updated, {metrics['skipped']} skipped"
        )
        return 0
    except DBToolError as exc:
        details = {**exc.partial_evidence, "error": str(exc)}
        if write_mode:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            started_at=started_at,
            db_path=args.db,
            metrics=exc.partial_evidence,
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        details = {"error": str(exc), "preflight_data_version": preflight_data_version}
        if write_mode:
            _append_ledger(db_path=args.db, status="error", details=details)
        _write_report_if_requested(
            report_path=report_path,
            ok=False,
            started_at=started_at,
            db_path=args.db,
            metrics={"preflight_data_version": preflight_data_version},
            errors=[str(exc)],
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
