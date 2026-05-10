#!/usr/bin/env python3
"""
CLI interface for the Discovery Engine pipeline.

Commands:
  full       - Run complete pipeline (collect + process + push)
  collect    - Run collectors only
  process    - Process pending signals
  sync       - Sync suppression cache from Notion
  stats      - Show pipeline statistics
  health     - Run health checks on all components
  metrics    - Show pipeline run metrics with per-collector breakdown
  pipeline   - Pipeline dashboard commands (status, qualified, push)
  triage     - Triage pending signals (list, approve, reject, defer, detail)
  import-csv    - Import signals from CSV files (OpenVC, etc.)
  export-queue  - Export pending/queued signals to CSV for offline review
  push          - Push specific signals to Notion by ID (manual push)

Available Collectors:
  - Traditional: github, sec_edgar, companies_house, domain_whois, product_hunt,
                 hacker_news, arxiv, job_postings, github_activity, linkedin,
                 crunchbase, uspto, opencorporates
  - Community:   telegram, discord (requires API credentials)
  - News:        news_api (requires GNEWS_API_KEY), rss_feeds (no API key needed)

Examples:
  # Run full pipeline with specific collectors (dry run)
  python run_pipeline.py full --collectors github,sec_edgar --dry-run

  # Run collectors only (persist to DB)
  python run_pipeline.py collect --collectors companies_house

  # Run community collectors (requires TELEGRAM_API_ID/HASH or DISCORD_BOT_TOKEN)
  python run_pipeline.py collect --collectors telegram,discord

  # Run news collectors (news_api requires GNEWS_API_KEY)
  python run_pipeline.py collect --collectors news_api,rss_feeds

  # Process all pending signals and push to Notion
  python run_pipeline.py process

  # Sync suppression cache
  python run_pipeline.py sync

  # Show statistics
  python run_pipeline.py stats

  # Run health check
  python run_pipeline.py health

  # View pipeline status (signal counts by status)
  python run_pipeline.py pipeline status

  # List qualified signals ready for push
  python run_pipeline.py pipeline qualified --limit 50

  # Preview push to Notion (dry run)
  python run_pipeline.py pipeline push --dry-run

  # Push qualified signals to Notion
  python run_pipeline.py pipeline push --confirm

  # View claim facts for an entity (Phase G)
  python run_pipeline.py pipeline claims <entity_id>

  # View claim history for an entity
  python run_pipeline.py pipeline claims <entity_id> --history

  # View claims at a specific point in time
  python run_pipeline.py pipeline claims <entity_id> --at 2024-06-15T00:00:00Z

  # View entity resolution statistics
  python run_pipeline.py pipeline entities

  # Import OpenVC CSV export (dry run)
  python run_pipeline.py import-csv --source openvc export.csv --dry-run

  # Import with thesis filter (consumer sectors only)
  python run_pipeline.py import-csv --source openvc export.csv --sectors Consumer,HealthTech
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Fix Windows console encoding for Unicode symbols
sys.stdout.reconfigure(errors='replace')
sys.stderr.reconfigure(errors='replace')

# Load environment variables from .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, rely on system env vars

from typing import Optional

from workflows.pipeline import (
    DiscoveryPipeline,
    PipelineConfig,
    PipelineMode,
    PipelineStats,
)
from utils.db_path_helper import (
    is_production_db_path,
    resolve_db_path,
)
import utils.db_guard as db_guard
from utils.signal_health import SignalHealthMonitor
from utils.cli_format import (
    BANNER_SEP, SECTION_SEP,
    STATUS_MAP, STATUS_OK, STATUS_FAIL, STATUS_WARN, STATUS_SKIP, STATUS_UNKNOWN,
    print_banner, print_section, print_phase, print_progress_item,
    format_verdict, explain,
)
from connectors.notion_connector_v2 import NotionConnector
from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION

try:
    import httpx
except ImportError:
    httpx = None


_DB_GUARD_BLOCK_EXIT_CODE = 2


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging(verbose: bool = False):
    """Configure logging for the pipeline"""
    level = logging.DEBUG if verbose else logging.INFO

    # Format with colors if terminal supports it
    if sys.stdout.isatty():
        # ANSI color codes
        colors = {
            "DEBUG": "\033[36m",    # Cyan
            "INFO": "\033[32m",     # Green
            "WARNING": "\033[33m",  # Yellow
            "ERROR": "\033[31m",    # Red
            "CRITICAL": "\033[35m", # Magenta
            "RESET": "\033[0m",
        }

        class ColoredFormatter(logging.Formatter):
            def format(self, record):
                levelname = record.levelname
                if levelname in colors:
                    record.levelname = f"{colors[levelname]}{levelname}{colors['RESET']}"
                return super().format(record)

        formatter = ColoredFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    # Configure root logger
    logging.basicConfig(level=level, handlers=[handler])

    # Reduce noise from some modules
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _db_guard_mode(args: argparse.Namespace) -> Optional[str]:
    """Return guard command type ('read' or 'write') for commands that operate on the production signals DB."""
    command = getattr(args, "command", None)
    if command in {"health", "health-json-pure"}:
        return "read"
    if command == "pipeline" and getattr(args, "pipeline_cmd", None) == "status":
        return "read"
    if command == "export-queue":
        return "read"
    if command == "triage" and getattr(args, "triage_cmd", None) == "list":
        return "read"
    if command in {"full", "collect", "process", "push"}:
        return "write"
    if command == "pipeline" and getattr(args, "pipeline_cmd", None) == "push":
        return "write"
    if command == "publish" and getattr(args, "publish_cmd", None) == "commit":
        return "write"
    if command == "triage" and getattr(args, "triage_cmd", None) in {"approve", "reject", "defer"}:
        return "write"
    if command == "outbox" and getattr(args, "outbox_cmd", None) == "drain":
        return "write"
    if command == "sync":
        return "write"
    return None


def _guard_db_path(args: argparse.Namespace) -> Optional[str]:
    """Resolve the DB path used by the guard-relevant command, if any."""
    mode = _db_guard_mode(args)
    if mode is None:
        return None
    try:
        return resolve_db_path(args)
    except Exception:
        return getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH") or "signals.db"


def _read_current_signal_count(db_path: str) -> tuple[Optional[int], Optional[str]]:
    """Return the current signal count or a read error for *db_path*.

    Thin wrapper retained for backward compatibility with existing tests.
    """
    return db_guard.read_current_signal_count(db_path)


def _enforce_signal_count_guard(args: argparse.Namespace) -> None:
    """Warn or fail closed when the production DB signal count collapses."""
    command_type = _db_guard_mode(args)
    if command_type is None:
        return

    db_path = _guard_db_path(args)
    if not db_path or not is_production_db_path(db_path):
        return

    # Scope strictly to ``sync``: the audited override exists only for the
    # catastrophic-drop recovery sync path. Non-sync callers (process/full/collect/push)
    # must never bypass the write guard, even if a synthesized Namespace carries
    # recovery_override=True. See .omx/wave6/db_guard_runbook.md.
    recovery_override = (
        getattr(args, "command", None) == "sync"
        and bool(getattr(args, "recovery_override", False))
    )

    # Operator-facing messaging before delegating to guard_command
    ok, message = db_guard.check_db_health(db_path)
    if not ok:
        if command_type == "read":
            print(
                f"WARNING: DB guard {message} on {db_path}. Allowing read command.",
                file=sys.stderr,
            )
        elif message == "watermark_missing":
            print(
                f"ERROR: DB guard watermark_missing on {db_path}. "
                "Run `python run_pipeline.py init-watermark` to bootstrap.",
                file=sys.stderr,
            )
        elif recovery_override and message == "catastrophic_drop_detected":
            print(
                f"WARNING: DB guard {message} on {db_path}. "
                "Proceeding because --recovery-override was supplied.",
                file=sys.stderr,
            )
        else:
            print(
                f"ERROR: DB guard {message} on {db_path}. Command blocked.",
                file=sys.stderr,
            )

    allowed = db_guard.guard_command(
        db_path, command_type, allow_override=recovery_override
    )
    if not allowed:
        raise SystemExit(_DB_GUARD_BLOCK_EXIT_CODE)


# =============================================================================
# API CONNECTIVITY HELPERS
# =============================================================================

async def check_github_api(timeout: float = 5.0) -> tuple[bool, str | None]:
    """Check GitHub API connectivity"""
    if not httpx:
        return False, "httpx not installed"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get("https://api.github.com/zen")
            if response.status_code == 200:
                return True, None
            else:
                return False, f"HTTP {response.status_code}"
    except httpx.TimeoutException:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)


async def check_sec_edgar_api(timeout: float = 5.0) -> tuple[bool, str | None]:
    """Check SEC EDGAR API connectivity"""
    if not httpx:
        return False, "httpx not installed"

    try:
        # SEC requires User-Agent header
        headers = {
            "User-Agent": "Discovery Engine Health Check contact@example.com",
        }
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            # Try the CIK lookup endpoint (lightweight)
            response = await client.get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&count=1")
            if response.status_code == 200:
                return True, None
            else:
                return False, f"HTTP {response.status_code}"
    except httpx.TimeoutException:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)


async def check_notion_api(api_key: str, timeout: float = 5.0) -> tuple[bool, str | None]:
    """Check Notion API connectivity"""
    if not httpx:
        return False, "httpx not installed"

    if not api_key:
        return False, "No API key configured"

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": "2022-06-28",
        }
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            # Try to get user info (lightweight endpoint)
            response = await client.get("https://api.notion.com/v1/users/me", headers=headers)
            if response.status_code == 200:
                return True, None
            elif response.status_code == 401:
                return False, "Invalid API key"
            else:
                return False, f"HTTP {response.status_code}"
    except httpx.TimeoutException:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)


async def check_gemini_api(api_key: str, timeout: float = 5.0) -> tuple[bool, str | None]:
    """Check Gemini API connectivity"""
    if not httpx:
        return False, "httpx not installed"

    if not api_key:
        return False, "No API key configured"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Try to list models (lightweight endpoint)
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            response = await client.get(url)
            if response.status_code == 200:
                return True, None
            elif response.status_code == 400:
                return False, "Invalid API key"
            else:
                return False, f"HTTP {response.status_code}"
    except httpx.TimeoutException:
        return False, "Connection timeout"
    except Exception as e:
        return False, str(e)


# =============================================================================
# COMMAND HANDLERS
# =============================================================================

async def cmd_full(args):
    """Run full pipeline: collect → process → push"""
    print_banner("DISCOVERY ENGINE - FULL PIPELINE")

    config = PipelineConfig.from_env()

    # Override config from args
    if args.db_path:
        config.db_path = args.db_path
    if args.parallel is not None:
        config.parallel_collectors = args.parallel
    if args.batch_size:
        config.batch_size = args.batch_size

    # Feature flags - explicit enable/disable
    if hasattr(args, "no_gating") and args.no_gating:
        config.use_gating = False
    elif hasattr(args, "use_gating") and args.use_gating:
        config.use_gating = True
    if hasattr(args, "use_entities") and args.use_entities:
        config.use_entities = True
    if hasattr(args, "use_asset_store") and args.use_asset_store:
        config.use_asset_store = True

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        # Parse collectors
        collectors = []
        if args.collectors:
            collectors = [c.strip() for c in args.collectors.split(",")]

        print(f"\nCollectors: {', '.join(collectors) if collectors else 'None specified'}")
        print(f"Dry run: {args.dry_run}")
        print(f"Database: {config.db_path}")
        print()

        # Run pipeline with progress output
        def _on_progress(phase: int, total: int, msg: str):
            print_phase(phase, total, msg)

        stats = await pipeline.run_full_pipeline(
            collectors=collectors,
            dry_run=args.dry_run,
            progress_callback=_on_progress,
        )

        # Print results with top-line verdict
        print()
        error_count = len(stats.errors) if stats.errors else 0
        verdict = format_verdict(
            "PIPELINE RESULTS",
            ok=error_count == 0,
            summary_parts=[
                f"{stats.signals_collected} collected",
                f"{stats.signals_stored} new",
                f"{error_count} errors",
                f"{stats.duration_seconds:.1f}s" if stats.duration_seconds else "0s",
            ],
            error_count=error_count,
        )
        print_banner(verdict)
        print()

        _print_stats(stats)

        # Save to JSON if requested
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(json.dumps(stats.to_dict(), indent=2))
            print(f"\nResults saved to: {output_path}")

    finally:
        await pipeline.close()


async def cmd_collect(args):
    """Run collectors only"""
    print_banner("DISCOVERY ENGINE - COLLECT SIGNALS")

    config = PipelineConfig.from_env()

    if args.db_path:
        config.db_path = args.db_path
    if args.parallel is not None:
        config.parallel_collectors = args.parallel

    # Feature flags
    if hasattr(args, "disable_gating") and args.disable_gating:
        config.use_gating = False
    elif hasattr(args, "enable_gating") and args.enable_gating:
        config.use_gating = True
    # Otherwise use default from config (True)

    if hasattr(args, "use_asset_store") and args.use_asset_store:
        config.use_asset_store = True

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        # Parse collectors
        if not args.collectors:
            print("ERROR: --collectors required for collect command")
            sys.exit(1)

        collectors = [c.strip() for c in args.collectors.split(",")]

        print(f"\nCollectors: {', '.join(collectors)}")
        print(f"Dry run: {args.dry_run}")
        print(f"Parallel: {config.parallel_collectors}")
        print()

        # Run collectors
        results = await pipeline.run_collectors(
            collector_names=collectors,
            dry_run=args.dry_run,
        )

        # Print results — compact single-line per collector (F4.1, F6.6)
        print()
        print_banner("COLLECTOR RESULTS")
        print()

        for result in results:
            status_symbol = STATUS_MAP.get(result.status.value, STATUS_WARN)
            detail = f"{result.signals_found} found, {result.signals_new} new, {result.signals_suppressed} suppressed"
            if result.error_message:
                detail = result.error_message
            print_progress_item(status_symbol, result.collector, detail)

        # Summary
        total_signals = sum(r.signals_found for r in results)
        succeeded = sum(1 for r in results if r.status.value == "success")
        skipped = sum(1 for r in results if r.status.value == "skipped")
        print(f"Summary: {succeeded}/{len(results)} collectors succeeded")
        print(f"Skipped collectors: {skipped}")
        print(f"Total signals: {total_signals}")

    finally:
        await pipeline.close()


async def cmd_process(args):
    """Process pending signals"""
    print(BANNER_SEP)
    print("DISCOVERY ENGINE - PROCESS PENDING SIGNALS")
    print(BANNER_SEP)

    config = PipelineConfig.from_env()

    if args.db_path:
        config.db_path = args.db_path
    if args.batch_size:
        config.batch_size = args.batch_size

    # Feature flags
    if hasattr(args, "disable_gating") and args.disable_gating:
        config.use_gating = False
    elif hasattr(args, "enable_gating") and args.enable_gating:
        config.use_gating = True
    # Otherwise use default from config (True)

    if hasattr(args, "use_entities") and args.use_entities:
        config.use_entities = True

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        source_api_filter = getattr(args, 'source_api', None)

        print(f"\nDatabase: {config.db_path}")
        print(f"Batch size: {config.batch_size}")
        print(f"Dry run: {args.dry_run}")
        print(f"Use gating: {config.use_gating}")
        if source_api_filter:
            print(f"Source API filter: {source_api_filter}")
        print()

        # Process pending signals
        result = await pipeline.process_pending(dry_run=args.dry_run, source_api=source_api_filter)

        # Print results
        print()
        print(BANNER_SEP)
        print("PROCESSING RESULTS")
        print(BANNER_SEP)
        print()

        print(f"Signals processed: {result['processed']}")
        print()
        print("Verification decisions:")
        print(f"  Auto-push:     {result['auto_push']}")
        print(f"  Needs review:  {result['needs_review']}")
        print(f"  Held:          {result['held']}")
        print(f"  Rejected:      {result['rejected']}")
        print()
        print("Notion actions:")
        print(f"  Created:   {result['prospects_created']}")
        print(f"  Updated:   {result['prospects_updated']}")
        print(f"  Skipped:   {result['prospects_skipped']}")

    finally:
        await pipeline.close()


async def cmd_sync(args):
    """Sync suppression cache from Notion"""
    print(BANNER_SEP)
    print("DISCOVERY ENGINE - SYNC SUPPRESSION CACHE")
    print(BANNER_SEP)

    config = PipelineConfig.from_env()

    if args.db_path:
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        print(f"\nDatabase: {config.db_path}")
        print("Syncing from Notion...")
        print()

        # Sync suppression cache
        count = await pipeline.sync_suppression()

        # Print results
        print()
        print(BANNER_SEP)
        print("SYNC COMPLETE")
        print(BANNER_SEP)
        print()
        print(f"Entries synced: {count}")
        print()
        print("Suppression cache is now up-to-date with Notion CRM")

    finally:
        await pipeline.close()


async def cmd_stats(args):
    """Show pipeline statistics"""
    print(BANNER_SEP)
    print("DISCOVERY ENGINE - STATISTICS")
    print(BANNER_SEP)

    config = PipelineConfig.from_env()

    if args.db_path:
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        # Get statistics
        stats = await pipeline.get_stats()

        # Print stats
        print()
        print("STORAGE")
        print(SECTION_SEP)
        storage = stats.get("storage", {})
        print(f"Database: {storage.get('database_path', 'Unknown')}")
        print(f"Total signals: {storage.get('total_signals', 0)}")
        print()

        print("Signals by type:")
        for signal_type, count in storage.get("signals_by_type", {}).items():
            print(f"  {signal_type}: {count}")
        print()

        print("PROCESSING STATUS")
        print(SECTION_SEP)
        processing = stats.get("processing", {})
        for status, count in processing.items():
            print(f"  {status}: {count}")
        print()

        print("SUPPRESSION CACHE")
        print(SECTION_SEP)
        print(f"Active entries: {storage.get('active_suppression_entries', 0)}")
        print()

        # Phase G: Entity Resolution & Claims
        print("PHASE G: ENTITY RESOLUTION & CLAIMS")
        print(SECTION_SEP)
        try:
            cursor = await pipeline._store._db.execute("SELECT COUNT(*) FROM entity_aliases")
            strong_keys = (await cursor.fetchone())[0]
            cursor = await pipeline._store._db.execute("SELECT COUNT(*) FROM entity_key_aliases")
            alias_keys = (await cursor.fetchone())[0]
            cursor = await pipeline._store._db.execute("SELECT COUNT(*) FROM entity_blocking_index")
            blocking_tokens = (await cursor.fetchone())[0]
            cursor = await pipeline._store._db.execute("SELECT COUNT(*) FROM claim_facts")
            claim_facts = (await cursor.fetchone())[0]
            cursor = await pipeline._store._db.execute(
                "SELECT COUNT(*) FROM claim_facts WHERE valid_until IS NULL AND is_retracted = 0"
            )
            active_claims = (await cursor.fetchone())[0]
            cursor = await pipeline._store._db.execute("SELECT COUNT(*) FROM entity_migrations")
            migrations = (await cursor.fetchone())[0]

            print(f"Strong key bindings: {strong_keys}")
            print(f"Weak alias bindings: {alias_keys}")
            print(f"Blocking tokens: {blocking_tokens}")
            print(f"Entity migrations: {migrations}")
            print(f"Total claim facts: {claim_facts}")
            print(f"Active claims: {active_claims}")
            print()
            print(f"Feature flags:")
            print(f"  USE_PHASE_G_IDENTITY_RESOLUTION: {config.use_phase_g_identity_resolution}")
            print(f"  USE_CLAIM_FACTS: {config.use_claim_facts}")
        except Exception as e:
            print(f"Phase G tables not available: {e}")
        print()

        print("CONFIGURATION")
        print(SECTION_SEP)
        cfg = stats.get("config", {})
        print(f"Parallel collectors: {cfg.get('parallel_collectors', False)}")
        print(f"Batch size: {cfg.get('batch_size', 0)}")
        print(f"Strict mode: {cfg.get('strict_mode', False)}")

    finally:
        await pipeline.close()


async def cmd_health(args):
    """Run health checks.

    Default behavior is intentionally backward-compatible:
      - External integration failures (GitHub/SEC/Notion/Gemini) fail the command.

    Flags:
      - --allow-external-failures: record integration failures as WARN and exit 0
        if core checks are healthy.
      - --core-only: skip all integration checks entirely.
    """

    from services.readiness import CheckResult, CheckScope, CheckStatus, ReadinessReport

    def _bool_arg(name: str, default: bool = False) -> bool:
        """Robust bool extraction for argparse args *and* MagicMock test args."""
        val = getattr(args, name, default)
        return val if isinstance(val, bool) else default

    def _int_arg(name: str, default: int) -> int:
        val = getattr(args, name, default)
        try:
            return int(val)
        except Exception:
            return default

    output_json = _bool_arg("output_json", False)
    verbose = _bool_arg("verbose", False)
    lookback_days = _int_arg("lookback_days", 30)
    core_only = _bool_arg("core_only", False)
    allow_external_failures = _bool_arg("allow_external_failures", False)

    total_checks = 5  # DB, Config, APIs, Suppression, Signal Health
    check_num = 0

    if not output_json:
        print_banner("DISCOVERY ENGINE - HEALTH CHECK")
        print()

    config = PipelineConfig.from_env()
    if getattr(args, "db_path", None):
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)

    checks: list[CheckResult] = []
    health_report_dict = None
    suppression_stats: dict = {}

    try:
        # ------------------------------------------------------------------
        # 1) Core: pipeline init / DB connectivity
        # ------------------------------------------------------------------
        check_num += 1
        if not output_json:
            print(f"[{check_num}/{total_checks}] Database connectivity...", end=" ", flush=True)
        try:
            await pipeline.initialize()
            db_ok = bool(getattr(getattr(pipeline, "_store", None), "_db", None))
            if db_ok:
                if not output_json:
                    print(STATUS_OK)
                checks.append(CheckResult("Database", CheckScope.CORE, CheckStatus.PASS, None))
            else:
                if not output_json:
                    print(f"{STATUS_FAIL} (no connection)")
                checks.append(CheckResult("Database", CheckScope.CORE, CheckStatus.FAIL, "No database connection"))
        except Exception as e:
            if not output_json:
                print(f"{STATUS_FAIL} ({e})")
            checks.append(CheckResult("Database", CheckScope.CORE, CheckStatus.FAIL, str(e)))

        # ------------------------------------------------------------------
        # 2) Core: configuration validation (non-fatal)
        # ------------------------------------------------------------------
        check_num += 1
        if not output_json:
            print(f"[{check_num}/{total_checks}] Configuration...", end=" ", flush=True)
        try:
            cfg = getattr(pipeline, "config", config)
            config_issues = []
            if not getattr(cfg, "notion_api_key", None):
                config_issues.append("NOTION_API_KEY not set")
            if not getattr(cfg, "notion_database_id", None):
                config_issues.append("NOTION_DATABASE_ID not set")

            if config_issues:
                if not output_json:
                    print(f"{STATUS_WARN} ({', '.join(config_issues)})")
                # Backward-compatible: this is informational, not a hard fail.
                checks.append(CheckResult("Configuration", CheckScope.CORE, CheckStatus.PASS, ", ".join(config_issues)))
            else:
                if not output_json:
                    print(STATUS_OK)
                checks.append(CheckResult("Configuration", CheckScope.CORE, CheckStatus.PASS, None))
        except Exception as e:
            if not output_json:
                print(f"{STATUS_FAIL} ({e})")
            checks.append(CheckResult("Configuration", CheckScope.CORE, CheckStatus.WARN, str(e)))

        # ------------------------------------------------------------------
        # 3) External integrations
        # ------------------------------------------------------------------
        check_num += 1
        if not output_json:
            print(f"[{check_num}/{total_checks}] API connectivity...")

        async def _run_external(name: str, fn):
            """Run a single external check with strict/lenient semantics."""
            if core_only:
                if not output_json:
                    print(f"  {STATUS_SKIP:6s} {name} (--core-only)")
                checks.append(CheckResult(name, CheckScope.EXTERNAL, CheckStatus.SKIP, "--core-only"))
                return

            try:
                ok, msg = await fn()
            except Exception as e:
                ok, msg = False, str(e)

            if ok:
                if not output_json:
                    print(f"  {STATUS_OK:6s} {name}")
                checks.append(CheckResult(name, CheckScope.EXTERNAL, CheckStatus.PASS, None))
            else:
                status = CheckStatus.WARN if allow_external_failures else CheckStatus.FAIL
                symbol = STATUS_WARN if allow_external_failures else STATUS_FAIL
                if not output_json:
                    print(f"  {symbol:6s} {name} ({msg})")
                checks.append(CheckResult(name, CheckScope.EXTERNAL, status, msg))

        await _run_external("GitHub API", lambda: check_github_api())
        await _run_external("SEC EDGAR API", lambda: check_sec_edgar_api())

        # Notion API (only if configured)
        cfg = getattr(pipeline, "config", config)
        notion_key = getattr(cfg, "notion_api_key", None)
        if core_only:
            if not output_json:
                print(f"  {STATUS_SKIP:6s} Notion API (--core-only)")
            checks.append(CheckResult("Notion API", CheckScope.EXTERNAL, CheckStatus.SKIP, "--core-only"))
        elif notion_key:
            await _run_external("Notion API", lambda: check_notion_api(notion_key))
        else:
            if not output_json:
                print(f"  {STATUS_SKIP:6s} Notion API (not configured)")
            checks.append(CheckResult("Notion API", CheckScope.EXTERNAL, CheckStatus.SKIP, "Not configured"))

        # Gemini API (only if configured)
        gemini_key = os.getenv("GOOGLE_API_KEY", "")
        if core_only:
            if not output_json:
                print(f"  {STATUS_SKIP:6s} Gemini API (--core-only)")
            checks.append(CheckResult("Gemini API", CheckScope.EXTERNAL, CheckStatus.SKIP, "--core-only"))
        elif gemini_key:
            await _run_external("Gemini API", lambda: check_gemini_api(gemini_key))
        else:
            if not output_json:
                print(f"  {STATUS_SKIP:6s} Gemini API (not configured)")
            checks.append(CheckResult("Gemini API", CheckScope.EXTERNAL, CheckStatus.SKIP, "Not configured"))

        # ------------------------------------------------------------------
        # 4) Core: suppression cache
        # ------------------------------------------------------------------
        check_num += 1
        if not output_json:
            print(f"[{check_num}/{total_checks}] Suppression cache...", end=" ", flush=True)
        try:
            if getattr(getattr(pipeline, "_store", None), "_db", None):
                stats = await pipeline.get_stats()
                storage = stats.get("storage", {})
                cache_entries = storage.get("active_suppression_entries", 0)
                suppression_stats = {
                    "active_entries": cache_entries,
                    "status": "HEALTHY" if cache_entries > 0 else "WARNING",
                }

                if cache_entries > 0:
                    if not output_json:
                        print(f"{STATUS_OK} ({cache_entries} entries)")
                    checks.append(CheckResult("Suppression Cache", CheckScope.CORE, CheckStatus.PASS, f"{cache_entries} entries"))
                else:
                    if not output_json:
                        print(f"{STATUS_WARN} (empty - run 'sync' command)")
                    checks.append(CheckResult("Suppression Cache", CheckScope.CORE, CheckStatus.WARN, "Empty - run 'sync' to populate"))
            else:
                if not output_json:
                    print(f"{STATUS_SKIP} (no database)")
                checks.append(CheckResult("Suppression Cache", CheckScope.CORE, CheckStatus.SKIP, "Database unavailable"))
        except Exception as e:
            if not output_json:
                print(f"{STATUS_WARN} ({e})")
            checks.append(CheckResult("Suppression Cache", CheckScope.CORE, CheckStatus.WARN, str(e)))

        # ------------------------------------------------------------------
        # 5) Core: signal health
        # ------------------------------------------------------------------
        check_num += 1
        if not output_json:
            print(f"[{check_num}/{total_checks}] Signal health (last {lookback_days} days)...", end=" ", flush=True)
        try:
            if getattr(getattr(pipeline, "_store", None), "_db", None):
                monitor = SignalHealthMonitor(pipeline._store)
                report = await monitor.generate_report(lookback_days=lookback_days)

                if not output_json:
                    status_sym = STATUS_OK if report.overall_status == "HEALTHY" else STATUS_WARN if report.overall_status == "DEGRADED" else STATUS_FAIL
                    print(f"{status_sym} ({report.overall_status})")

                health_report_dict = report.to_dict()

                if verbose and not output_json:
                    print()
                    print(report)

                if report.overall_status == "HEALTHY":
                    checks.append(CheckResult("Signal Health", CheckScope.CORE, CheckStatus.PASS, None))
                elif report.overall_status == "DEGRADED":
                    checks.append(CheckResult("Signal Health", CheckScope.CORE, CheckStatus.WARN, "System degraded (informational)"))
                else:
                    checks.append(CheckResult("Signal Health", CheckScope.CORE, CheckStatus.FAIL, "System critical"))
            else:
                if not output_json:
                    print(f"{STATUS_SKIP} (no database)")
                checks.append(CheckResult("Signal Health", CheckScope.CORE, CheckStatus.SKIP, "Database unavailable"))
        except Exception as e:
            if not output_json:
                print(f"{STATUS_WARN} ({e})")
            checks.append(CheckResult("Signal Health", CheckScope.CORE, CheckStatus.WARN, str(e)))

        # ------------------------------------------------------------------
        # Summarize
        # ------------------------------------------------------------------
        readiness = ReadinessReport(checks)

        if output_json:
            cfg = getattr(pipeline, "config", config)
            result = {
                "overall_status": readiness.overall_status,
                "core_status": readiness.core_status,
                "integration_status": readiness.integration_status,
                "checks": [c.to_dict() for c in checks],
                "signal_health": health_report_dict,
                "suppression_cache": suppression_stats,
                "flags": {
                    "core_only": core_only,
                    "allow_external_failures": allow_external_failures,
                },
                "config": {
                    "db_path": getattr(cfg, "db_path", config.db_path),
                    "use_gating": getattr(cfg, "use_gating", False),
                    "use_entities": getattr(cfg, "use_entities", False),
                    "use_asset_store": getattr(cfg, "use_asset_store", False),
                    "lookback_days": lookback_days,
                },
            }
            print(json.dumps(result, indent=2, default=str))
        else:
            # Print summary with pass/fail/warn/skip counts
            passed = sum(1 for c in checks if c.status.upper() in ("PASS", "OK"))
            failed = sum(1 for c in checks if c.status.upper() == "FAIL")
            warned = sum(1 for c in checks if c.status.upper() in ("WARN", "CRIT"))
            skipped = sum(1 for c in checks if c.status.upper() == "SKIP")
            total = len(checks)
            print()
            overall_ok = failed == 0
            parts = [f"{passed}/{total} passed"]
            if warned:
                parts.append(f"{warned} warning{'s' if warned != 1 else ''}")
            if skipped:
                parts.append(f"{skipped} skipped")
            verdict = format_verdict(
                "HEALTH CHECK SUMMARY",
                ok=overall_ok and failed == 0,
                summary_parts=parts,
                error_count=failed,
            )
            print_banner(verdict)
            print()

            for c in checks:
                sym = STATUS_MAP.get(c.status.lower(), STATUS_UNKNOWN)
                print(f"  {sym:6s} {c.name}")
                if c.message:
                    print(f"         {c.message}")

            print()
            print(f"Overall: {readiness.overall_status}")
            print()

        return readiness.exit_code()

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e), "overall_status": "ERROR"}, indent=2))
        else:
            print()
            print(f"Health check failed with error: {e}")
        logging.exception("Health check error")
        return 1
    finally:
        try:
            await pipeline.close()
        except Exception:
            pass


async def cmd_step3b_readiness(args):
    """Check Step 3B activation readiness."""
    output_json = getattr(args, "output_json", False)
    config = PipelineConfig.from_env()
    if args.db_path:
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)
    try:
        await pipeline.initialize()
        from monitoring.step3b_readiness import check_step3b_readiness

        result = await check_step3b_readiness(pipeline._store)

        if output_json:
            print(json.dumps(result.to_dict(), indent=2, default=str))
        else:
            print()
            print("=" * 50)
            print("STEP 3B READINESS CHECK")
            print("=" * 50)
            print()
            verdict_label = "READY" if result.can_proceed else "BLOCKED"
            print(f"  Verdict: {verdict_label}")
            print()
            m = result.metrics
            print(f"  Multi-source promoted files: {m.get('multi_source_promoted', '?')}"
                  f" (threshold: {m.get('multi_source_threshold', '?')})")
            print(f"  Canary verdict: {m.get('canary_verdict', 'none')}"
                  f" (pass_rate: {m.get('canary_pass_rate', '?')})")
            print(f"  Phase G verdict: {m.get('phase_g_verdict', '?')}")
            print()
            if result.blockers:
                print("  Blockers:")
                for b in result.blockers:
                    print(f"    - {b}")
            else:
                print("  No blockers. Ready to proceed.")
            print()

        return 0 if result.can_proceed else 1
    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Step 3B readiness check failed: {e}")
        return 1
    finally:
        await pipeline.close()


async def cmd_metrics(args):
    """Show pipeline run metrics with per-collector breakdown."""
    print_banner("DISCOVERY ENGINE - PIPELINE METRICS")

    config = PipelineConfig.from_env()
    if args.db_path:
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        # Get recent pipeline runs
        runs = await pipeline._store.get_pipeline_runs(limit=args.limit)

        if not runs:
            print("\nNo pipeline runs found.")
            return

        print(f"\nLast {len(runs)} runs:\n")

        for run in runs:
            run_id = run["run_id"]
            started = run["started_at"][:19].replace("T", " ")
            duration = run.get("duration_seconds", 0) or 0

            print(f"Run: {started} ({duration:.1f}s total)")

            # Get collector metrics for this run
            collector_metrics = await pipeline._store.get_collector_metrics(
                run_id=run_id,
                collector_name=args.collector,
            )

            if not collector_metrics:
                print("  (no collector metrics)")
            else:
                for cm in collector_metrics:
                    name = cm["collector_name"]
                    dur = cm.get("duration_seconds", 0) or 0
                    signals = cm.get("signals_found", 0)
                    status = cm.get("status", "unknown")
                    api_calls = cm.get("api_calls", 0)
                    retries = cm.get("retries", 0)
                    rate_limits = cm.get("rate_limit_hits", 0)

                    # Status indicator — unified symbol system
                    status_icon = STATUS_MAP.get(status, STATUS_UNKNOWN)

                    # Format API metrics
                    api_parts = [f"{api_calls} calls"]
                    if retries > 0:
                        api_parts.append(f"{retries} retries")
                    if rate_limits > 0:
                        api_parts.append(f"{rate_limits} rate limits")
                    api_str = ", ".join(api_parts)

                    print(f"  {status_icon:6s} {name:<16} {dur:>6.1f}s  {signals:>3} signals  |  API: {api_str}")

            print()

    finally:
        await pipeline.close()


# =============================================================================
# EMBEDDINGS BATCH COMMAND
# =============================================================================

async def cmd_embeddings(args):
    """Pre-compute company embeddings for similarity search."""
    import logging
    from utils.similar_companies_batch import run_batch_job

    # Setup logging
    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    db_path = getattr(args, "db_path", "signals.db")
    force = getattr(args, "force", False)
    limit = getattr(args, "limit", None)

    print("\n" + "=" * 50)
    print("Embeddings Batch Job")
    print("=" * 50)
    print(f"Database: {db_path}")
    print(f"Force recompute: {force}")
    if limit:
        print(f"Limit: {limit} companies")
    print()

    result = await run_batch_job(
        db_path=db_path,
        force_recompute=force,
        limit=limit,
    )

    # Display results
    print("\nResults:")
    print(f"  Total companies:    {result.total_companies}")
    print(f"  New embeddings:     {result.new_embeddings}")
    print(f"  Updated embeddings: {result.updated_embeddings}")
    print(f"  Skipped (cached):   {result.skipped_embeddings}")
    print(f"  Failed:             {result.failed_embeddings}")
    print(f"  Duration:           {result.duration_seconds:.1f}s")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors[:5]:
            print(f"  - {err}")
        if len(result.errors) > 5:
            print(f"  ... and {len(result.errors) - 5} more")

    print()


# =============================================================================
# EMAIL IMPORT COMMAND
# =============================================================================

async def cmd_import_emails(args):
    """Import emails from MBOX file into relationship graph."""
    from connectors.local_email_scanner import LocalEmailScanner
    from storage.relationship_store import RelationshipStore

    mbox_path = args.mbox
    my_email = args.email
    db_path = getattr(args, "db_path", None) or "private_graph.db"
    dry_run = getattr(args, "dry_run", False)

    print(BANNER_SEP)
    print("DISCOVERY ENGINE - IMPORT EMAILS")
    print(BANNER_SEP)
    print()
    print(f"MBOX file: {mbox_path}")
    print(f"My email: {my_email}")
    print(f"Database: {db_path}")
    print(f"Dry run: {dry_run}")
    print()

    # Scan MBOX
    print("Scanning MBOX file...")
    scanner = LocalEmailScanner(my_email=my_email)

    try:
        tracker = scanner.scan_mbox(mbox_path)
    except FileNotFoundError:
        print(f"ERROR: MBOX file not found: {mbox_path}")
        sys.exit(1)

    contacts = tracker.get_all_contacts()
    print(f"Found {len(contacts)} unique domains")
    print()

    if dry_run:
        print("DRY RUN - Would store the following relationships:")
        print(SECTION_SEP)
        for domain, contact in sorted(contacts.items(), key=lambda x: -x[1]['total_messages'])[:20]:
            print(f"  {domain:<30} {contact['total_messages']:>4} msgs, {contact['intro_count']:>2} intros, {contact['reply_count']:>2} replies")
        if len(contacts) > 20:
            print(f"  ... and {len(contacts) - 20} more domains")
        return

    # Store relationships
    print("Storing relationships...")
    store = RelationshipStore(db_path)
    await store.initialize()

    try:
        stored = 0
        for domain, contact in contacts.items():
            await store.upsert_domain_edge(
                me_email=my_email,
                target_domain=domain,
                intro_count=contact['intro_count'],
                reply_count=contact['reply_count'],
                total_messages=contact['total_messages'],
                last_contact_at=contact['last_contact'],
                first_contact_at=contact['first_contact'],
            )
            stored += 1

        print()
        print(BANNER_SEP)
        print("IMPORT COMPLETE")
        print(BANNER_SEP)
        print()
        print(f"Relationships stored: {stored}")
        print()
        print("Top 10 relationships by message count:")
        print(SECTION_SEP)
        for domain, contact in sorted(contacts.items(), key=lambda x: -x[1]['total_messages'])[:10]:
            strength = await store.get_domain_strength(my_email, domain)
            score = strength.strength_score if strength else 0.0
            print(f"  {domain:<30} {contact['total_messages']:>4} msgs, score: {score:.2f}")

    finally:
        await store.close()


# =============================================================================
# LP SYNC COMMAND
# =============================================================================

async def cmd_sync_lps(args):
    """Sync LP relationships from Notion database."""
    from connectors.notion_lp_sync import NotionLPSync

    dry_run = getattr(args, "dry_run", False)

    # Get database ID from env or args
    database_id = getattr(args, "database_id", None) or os.getenv("NOTION_LP_DATABASE_ID")
    api_key = os.getenv("NOTION_API_KEY")

    if not api_key:
        print("ERROR: NOTION_API_KEY environment variable not set")
        sys.exit(1)

    if not database_id:
        print("ERROR: NOTION_LP_DATABASE_ID environment variable not set")
        print("       Or provide --database-id argument")
        sys.exit(1)

    print(BANNER_SEP)
    print("DISCOVERY ENGINE - SYNC LP RELATIONSHIPS")
    print(BANNER_SEP)
    print()
    print(f"LP Database ID: {database_id[:8]}...")
    print(f"Dry run: {dry_run}")
    print()

    # Sync from Notion
    print("Fetching LP records from Notion...")
    sync = NotionLPSync(api_key=api_key, database_id=database_id)

    try:
        relationships = await sync.sync()
    except Exception as e:
        print(f"ERROR: Failed to fetch LP records: {e}")
        sys.exit(1)

    print(f"Found {len(relationships)} firm relationships")
    print()

    if dry_run:
        print("DRY RUN - Would store the following relationships:")
        print(SECTION_SEP)
        for rel in sorted(relationships, key=lambda r: -r.score)[:20]:
            print(f"  {rel.domain:<30} {rel.score:.2f}  {rel.badge}")
            print(f"    {rel.attribution}")
        if len(relationships) > 20:
            print(f"  ... and {len(relationships) - 20} more firms")
        return

    # Resolve user identity
    user_email = getattr(args, "user_email", None) or os.environ.get("USER_EMAIL", "")
    if not user_email:
        print("ERROR: --user-email required or set USER_EMAIL environment variable")
        sys.exit(1)

    db_path = getattr(args, "db_path", None) or "private_graph.db"

    # Store relationships via RelationshipStore
    from storage.relationship_store import RelationshipStore

    rel_store = RelationshipStore(db_path)
    await rel_store.initialize()

    stored = 0
    try:
        for rel in relationships:
            # Field mapping: domain -> target_domain, status.value -> lp_status,
            # attribution -> lp_name (LP contact or firm name), score -> notion_score
            await rel_store.upsert_lp_relationship(
                me_email=user_email,
                target_domain=rel.domain,
                lp_status=rel.status.value,
                lp_name=rel.attribution,  # Attribution carries the LP/firm name
                notion_score=rel.score,
            )
            stored += 1
    finally:
        await rel_store.close()

    print(BANNER_SEP)
    print("SYNC COMPLETE")
    print(BANNER_SEP)
    print()
    print(f"Firm relationships synced: {stored}")
    print(f"Database: {db_path}")
    print()
    print("Top 10 relationships by score:")
    print(SECTION_SEP)
    for rel in sorted(relationships, key=lambda r: -r.score)[:10]:
        print(f"  {rel.domain:<30} {rel.score:.2f}  {rel.badge}")


async def cmd_relationship_health(args):
    """Check relationship data health and staleness."""
    from utils.relationship_health import RelationshipHealthMonitor
    from storage.relationship_store import RelationshipStore

    db_path = getattr(args, "db_path", None) or "private_graph.db"
    user_email = getattr(args, "user_email", None) or os.environ.get("USER_EMAIL", "")
    output_json = getattr(args, "output_json", False)

    if not user_email:
        print("Error: --user-email required or set USER_EMAIL env var")
        return

    store = RelationshipStore(db_path=db_path)
    await store.initialize()

    try:
        # Configure thresholds
        email_stale_days = getattr(args, "email_stale_days", 7)
        lp_stale_days = getattr(args, "lp_stale_days", 3)

        monitor = RelationshipHealthMonitor(
            email_stale_days=email_stale_days,
            lp_stale_days=lp_stale_days,
        )

        report = await monitor.generate_report(store, user_email)

        if output_json:
            import json
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print()
            print(BANNER_SEP)
            print("RELATIONSHIP HEALTH REPORT")
            print(BANNER_SEP)
            print()
            print(f"Overall Status: {report.overall_status}")
            print()

            print("Relationship Counts:")
            print(SECTION_SEP)
            print(f"  Total:    {report.relationship_count}")
            print(f"  Gmail:    {report.gmail_relationship_count}")
            print(f"  LP:       {report.lp_relationship_count}")
            print(f"  Combined: {report.combined_relationship_count}")
            print()

            print("Email Scan Health:")
            print(SECTION_SEP)
            eh = report.email_health
            print(f"  Status:      {eh.status}")
            if eh.days_since_scan is not None:
                print(f"  Days since:  {eh.days_since_scan}")
            print(f"  Records:     {eh.record_count}")
            print()

            print("LP Sync Health:")
            print(SECTION_SEP)
            lh = report.lp_health
            print(f"  Status:      {lh.status}")
            if lh.days_since_sync is not None:
                print(f"  Days since:  {lh.days_since_sync}")
            print(f"  Records:     {lh.record_count}")
            print()

            if report.alerts:
                print("Alerts:")
                print(SECTION_SEP)
                for alert in report.alerts:
                    print(f"  [{alert.severity}] {alert.description}")
                print()

    finally:
        await store.close()


async def cmd_warm_intros(args):
    """Look up warm intro data for investor domains."""
    from storage.relationship_store import RelationshipStore
    from utils.warm_intro_boost import WarmIntroBoost
    from utils.warm_intro_enricher import WarmIntroEnricher

    db_path = getattr(args, "db_path", None) or "private_graph.db"
    user_email = getattr(args, "user_email", None) or os.environ.get("USER_EMAIL", "")
    output_json = getattr(args, "output_json", False)
    verbose = getattr(args, "verbose", False)
    domain = getattr(args, "domain", None)
    show_all = getattr(args, "show_all", False)

    if not user_email:
        print("Error: --user-email required or set USER_EMAIL env var")
        return

    if not domain and not show_all:
        print("Error: --domain or --all required")
        return

    store = RelationshipStore(db_path=db_path)
    await store.initialize()

    try:
        boost = WarmIntroBoost()
        enricher = WarmIntroEnricher(
            relationship_store=store,
            warm_intro_boost=boost,
        )

        if domain:
            # Single domain lookup
            candidate = await enricher.enrich_investor(
                investor_domain=domain,
                user_email=user_email,
            )

            if output_json:
                if candidate:
                    print(json.dumps({
                        "domain": candidate.investor_domain,
                        "score": candidate.score,
                        "badge": candidate.badge,
                        "attribution": candidate.attribution,
                        "source": candidate.source.value,
                        "confidence": candidate.confidence,
                    }, indent=2))
                else:
                    print(json.dumps({"domain": domain, "found": False}, indent=2))
            else:
                if candidate:
                    print(f"\nWarm Intro Data for {domain}:")
                    print(SECTION_SEP)
                    print(f"  Score:       {candidate.score:.2f}")
                    print(f"  Badge:       {candidate.badge}")
                    if verbose:
                        print(f"  Attribution: {candidate.attribution}")
                        print(f"  Source:      {candidate.source.value}")
                        print(f"  Confidence:  {candidate.confidence}")
                    print()
                else:
                    print(f"\nNo warm intro data found for {domain}")
                    print("  (Run import-emails or sync-lps to build relationship data)")
                    print()

        elif show_all:
            # List all domains with relationships
            # Get all domains from store
            all_domains = await store.get_all_domains(user_email)

            if not all_domains:
                print("\nNo relationship data found.")
                print("  (Run import-emails or sync-lps to build relationship data)")
                return

            results = []
            for target_domain in all_domains:
                candidate = await enricher.enrich_investor(
                    investor_domain=target_domain,
                    user_email=user_email,
                )
                if candidate:
                    results.append(candidate)

            # Sort by score descending
            results.sort(key=lambda c: c.score, reverse=True)

            if output_json:
                print(json.dumps([
                    {
                        "domain": c.investor_domain,
                        "score": c.score,
                        "badge": c.badge,
                        "attribution": c.attribution,
                        "source": c.source.value,
                        "confidence": c.confidence,
                    }
                    for c in results
                ], indent=2))
            else:
                print(f"\nWarm Intro Data ({len(results)} domains):")
                print(SECTION_SEP)
                if verbose:
                    print(f"{'Domain':<30} {'Score':>6} {'Badge':<15} {'Source':<10}")
                    print(SECTION_SEP)
                    for c in results:
                        print(f"{c.investor_domain:<30} {c.score:>6.2f} {c.badge:<15} {c.source.value:<10}")
                else:
                    print(f"{'Domain':<30} {'Score':>6} {'Badge':<15}")
                    print(SECTION_SEP)
                    for c in results:
                        print(f"{c.investor_domain:<30} {c.score:>6.2f} {c.badge:<15}")
                print()

    finally:
        await store.close()


# =============================================================================
# PIPELINE DASHBOARD COMMANDS
# =============================================================================

async def cmd_pipeline_status(db_path: str = "signals.db") -> None:
    """Show pipeline status overview."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        counts = await store.get_status_counts()

        print("\n" + "=" * 50)
        print("Pipeline Status")
        print("=" * 50)
        print(f"\n  Qualified:  {counts.get('qualified', 0):>5} signals (ready for push)")
        print(f"  Held:       {counts.get('held', 0):>5} signals (need review)")
        print(f"  Rejected:   {counts.get('rejected', 0):>5} signals (excluded)")
        print(f"  Pushed:     {counts.get('pushed', 0):>5} signals (in Notion)")
        print(f"  Pending:    {counts.get('pending', 0):>5} signals (not processed)")
        print()
        print("Commands:")
        print("  python run_pipeline.py pipeline qualified  - List signals ready for push")
        print("  python run_pipeline.py pipeline push       - Export qualified to Notion")
        print("=" * 50 + "\n")

    finally:
        await store.close()


async def cmd_pipeline_qualified(
    db_path: str = "signals.db",
    limit: int = 20,
) -> None:
    """List qualified signals ready for push."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        signals = await store.get_signals_by_status("qualified", limit=limit)

        print(f"\n{'='*60}")
        print(f"Qualified Signals ({len(signals)} shown, limit={limit})")
        print(f"{'='*60}\n")

        if not signals:
            print("  No qualified signals found.\n")
            return

        for i, sig in enumerate(signals, 1):
            print(f"{i:3}. {sig.company_name or 'Unknown'}")
            print(f"     Key: {sig.canonical_key}")
            print(f"     Confidence: {sig.confidence:.2f}")
            print(f"     Source: {sig.source_api}")
            print()

        print(f"Run 'python run_pipeline.py pipeline push --confirm' to export to Notion")
        print(f"{'='*60}\n")

    finally:
        await store.close()


async def cmd_pipeline_push(
    db_path: str = "signals.db",
    confirm: bool = False,
    dry_run: bool = False,
    signal_id: Optional[int] = None,
) -> None:
    """Push qualified signals to Notion."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        signals = await store.get_signals_by_status("qualified")

        if not signals:
            print("No qualified signals to push.")
            return

        print(f"\nFound {len(signals)} qualified signal(s) to push.")

        if not confirm and not dry_run:
            print("\nUse --confirm to push, or --dry-run to preview.")
            return

        if dry_run:
            print("\n[DRY RUN] Would push:")
            for sig in signals[:10]:
                print(f"  - {sig.company_name or sig.canonical_key}")
            if len(signals) > 10:
                print(f"  ... and {len(signals) - 10} more")
            return

        # Actual push would integrate with NotionPusher
        print(f"\nPushing {len(signals)} signals to Notion...")
        print("(Push integration with NotionPusher pending)")

    finally:
        await store.close()


# =============================================================================
# HELPERS
# =============================================================================

def _print_stats(stats: PipelineStats):
    """Pretty-print pipeline statistics."""

    # Collectors
    print_section("COLLECTORS")
    print(f"  Run: {stats.collectors_run}  |  OK: {stats.collectors_succeeded}  |  Failed: {stats.collectors_failed}  |  Collected: {stats.signals_collected}")
    print()

    # Storage
    if stats.signals_stored or stats.signals_deduplicated:
        print_section("STORAGE")
        print(f"  Stored: {stats.signals_stored}  |  Deduplicated: {stats.signals_deduplicated}")
        print()

    # Verification
    if stats.signals_processed:
        print_section("VERIFICATION")
        print(f"  Processed: {stats.signals_processed}  |  Auto-push: {stats.signals_auto_push}  |  Review: {stats.signals_needs_review}  |  Held: {stats.signals_held}  |  Rejected: {stats.signals_rejected}")
        print()

    # Notion
    if stats.prospects_created or stats.prospects_updated or stats.prospects_skipped:
        print_section("NOTION CRM")
        print(f"  Created: {stats.prospects_created}  |  Updated: {stats.prospects_updated}  |  Skipped: {stats.prospects_skipped}")
        print()

    # Errors (elevated visual weight)
    if stats.errors:
        print_section("ERRORS")
        for error in stats.errors:
            print(f"  {STATUS_FAIL} {error}")
        print()

    # Timing
    if stats.completed_at:
        print_section("TIMING")
        print(f"  Started: {stats.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  |  Duration: {stats.duration_seconds:.2f}s")
    print()
    print("Done.")


async def cmd_pipeline_claims(
    entity_id: str,
    db_path: str = "signals.db",
    show_history: bool = False,
    at_time: Optional[str] = None,
    predicate: Optional[str] = None,
) -> None:
    """Query claim facts for an entity."""
    from storage.claim_fact_store import ClaimFactStore

    store = SignalStore(db_path)
    await store.initialize()
    claim_store = ClaimFactStore(store)

    try:
        print(f"\n{'='*70}")
        print(f"Claim Facts for Entity: {entity_id}")
        print(f"{'='*70}\n")

        # Get predicates to query
        predicates = [predicate] if predicate else [
            "company_name", "founding_date", "location",
            "industry", "funding_raised", "website"
        ]

        if at_time:
            # Point-in-time query
            print(f"Point in time: {at_time}\n")
            for pred in predicates:
                fact = await claim_store.get_fact_at_time(entity_id, pred, at_time)
                if fact:
                    _print_fact(fact)
        elif show_history:
            # Full history
            print("Full History (most recent first)\n")
            for pred in predicates:
                history = await claim_store.get_fact_history(entity_id, pred)
                if history:
                    print(f"  {pred}:")
                    for fact in history:
                        status = "ACTIVE" if fact["valid_until"] is None else f"until {fact['valid_until'][:10]}"
                        print(f"    [{status}] {fact['value']} (tier={fact['source_tier']}, conf={fact['confidence']:.2f})")
                    print()
        else:
            # Current active facts
            print("Current Active Facts\n")
            found_any = False
            for pred in predicates:
                fact = await claim_store.get_active_fact(entity_id, pred)
                if fact:
                    found_any = True
                    _print_fact(fact)

            if not found_any:
                print("  No claim facts found for this entity.\n")
                print("  Hint: Run pipeline with USE_CLAIM_FACTS=true to extract claims.\n")

        print(f"{'='*70}\n")

    finally:
        await store.close()


def _print_fact(fact: dict) -> None:
    """Pretty print a claim fact."""
    print(f"  {fact['predicate']:15} = {fact['value']}")
    print(f"    {'':15}   tier={fact['source_tier']} | conf={fact['confidence']:.2f} | observed={fact['observed_at'][:10]}")
    if fact.get('supporting_signal_ids'):
        print(f"    {'':15}   signals: {fact['supporting_signal_ids']}")
    print()


async def cmd_pipeline_entities(
    db_path: str = "signals.db",
    limit: int = 20,
) -> None:
    """Show entity resolution statistics."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        print(f"\n{'='*70}")
        print("Entity Resolution Statistics")
        print(f"{'='*70}\n")

        # Query entity-related tables
        cursor = await store._db.execute("SELECT COUNT(*) FROM entity_aliases")
        strong_keys = (await cursor.fetchone())[0]

        cursor = await store._db.execute("SELECT COUNT(*) FROM entity_key_aliases")
        alias_keys = (await cursor.fetchone())[0]

        cursor = await store._db.execute("SELECT COUNT(*) FROM entity_blocking_index")
        blocking_tokens = (await cursor.fetchone())[0]

        cursor = await store._db.execute("SELECT COUNT(*) FROM entity_migrations")
        migrations = (await cursor.fetchone())[0]

        cursor = await store._db.execute("SELECT COUNT(*) FROM claim_facts")
        claim_facts = (await cursor.fetchone())[0]

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM claim_facts WHERE valid_until IS NULL AND is_retracted = 0"
        )
        active_claims = (await cursor.fetchone())[0]

        print("IDENTITY RESOLUTION")
        print(SECTION_SEP)
        print(f"  Strong key bindings:   {strong_keys:>6}")
        print(f"  Weak alias bindings:   {alias_keys:>6}")
        print(f"  Blocking tokens:       {blocking_tokens:>6}")
        print(f"  Entity migrations:     {migrations:>6}")
        print()

        print("BI-TEMPORAL CLAIMS")
        print(SECTION_SEP)
        print(f"  Total claim facts:     {claim_facts:>6}")
        print(f"  Active (current):      {active_claims:>6}")
        print(f"  Historical:            {claim_facts - active_claims:>6}")
        print()

        # Show recent entities with claims
        cursor = await store._db.execute("""
            SELECT entity_id, COUNT(*) as fact_count, MAX(created_at) as last_updated
            FROM claim_facts
            GROUP BY entity_id
            ORDER BY last_updated DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()

        if rows:
            print(f"RECENT ENTITIES (top {limit})")
            print(SECTION_SEP)
            for entity_id, count, last_updated in rows:
                print(f"  {entity_id[:16]}  | {count} facts | {last_updated[:10]}")
            print()

        print(f"{'='*70}\n")

    finally:
        await store.close()


# =============================================================================
# THESIS EVALUATION COMMANDS
# =============================================================================

async def cmd_eval_export(args):
    """Export ground truth from Notion for thesis evaluation."""
    from scripts.export_notion_ground_truth import NotionGroundTruthExporter

    output_path = getattr(args, "output", "datasets/thesis_ground_truth.jsonl")
    min_examples = getattr(args, "min_examples", 100)
    dry_run = getattr(args, "dry_run", False)

    print(BANNER_SEP)
    print("THESIS EVALUATION - EXPORT GROUND TRUTH")
    print(BANNER_SEP)
    print(f"Output: {output_path}")
    print(f"Min examples: {min_examples}")
    print()

    try:
        exporter = NotionGroundTruthExporter()
        samples = await exporter.export(min_examples=min_examples)

        # Show label distribution
        label_counts = {}
        for sample in samples:
            label_counts[sample.target] = label_counts.get(sample.target, 0) + 1

        print(f"Exported {len(samples)} samples")
        print("\nLabel distribution:")
        for label, count in sorted(label_counts.items()):
            print(f"  {label}: {count}")

        if dry_run:
            print("\n[DRY RUN] Not writing to file")
            print("\nSample examples:")
            for sample in samples[:3]:
                print(f"\n--- {sample.metadata['company_name']} ---")
                print(f"Target: {sample.target}")
                print(f"Input: {sample.input[:150]}...")
        else:
            import json
            from pathlib import Path
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, "w", encoding="utf-8") as f:
                for sample in samples:
                    f.write(json.dumps(sample.to_dict()) + "\n")

            print(f"\nWritten to {output_path}")

    except ValueError as e:
        print(f"\nError: {e}")
        print("Set NOTION_API_KEY and NOTION_DATABASE_ID environment variables")
        sys.exit(1)


async def cmd_eval_run(args):
    """Run thesis classification evaluation."""
    from utils.thesis_evaluator import (
        ThesisEvaluator,
        format_evaluation_result,
        format_comparison,
    )
    from storage.signal_store import SignalStore

    eval_type = getattr(args, "type", "keyword")
    dataset_path = getattr(args, "dataset", "datasets/thesis_sample.jsonl")
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    save_results = getattr(args, "save", True)

    print(BANNER_SEP)
    print("THESIS CLASSIFICATION EVALUATION")
    print(BANNER_SEP)
    print(f"Type: {eval_type}")
    print(f"Dataset: {dataset_path}")
    print()

    evaluator = ThesisEvaluator()

    if eval_type == "keyword":
        result = await evaluator.evaluate_keyword(dataset_path)
        print(format_evaluation_result(result))

        # Save to database
        if save_results:
            store = SignalStore(db_path)
            await store.initialize()
            try:
                await store.save_thesis_evaluation(
                    run_id=result.run_id,
                    evaluator_type=result.evaluator_type,
                    dataset_path=result.dataset_path,
                    accuracy=result.accuracy,
                    per_class_metrics={
                        k: v.to_dict() for k, v in result.per_class_metrics.items()
                    },
                    confusion_matrix=result.confusion_matrix,
                    latency_ms=result.latency_ms,
                    errors=result.errors,
                )
                print(f"\nResults saved to database (run_id: {result.run_id})")
            finally:
                await store.close()

    elif eval_type == "llm":
        result = await evaluator.evaluate_llm(dataset_path)
        print(format_evaluation_result(result))

        # Save to database
        if save_results:
            store = SignalStore(db_path)
            await store.initialize()
            try:
                await store.save_thesis_evaluation(
                    run_id=result.run_id,
                    evaluator_type=result.evaluator_type,
                    dataset_path=result.dataset_path,
                    accuracy=result.accuracy,
                    per_class_metrics={
                        k: v.to_dict() for k, v in result.per_class_metrics.items()
                    },
                    confusion_matrix=result.confusion_matrix,
                    latency_ms=result.latency_ms,
                    token_usage=result.token_usage,
                    errors=result.errors,
                )
                print(f"\nResults saved to database (run_id: {result.run_id})")
            finally:
                await store.close()

    elif eval_type == "both":
        comparison = await evaluator.evaluate_both(dataset_path)
        print(format_comparison(comparison))

        # Save both results
        if save_results:
            store = SignalStore(db_path)
            await store.initialize()
            try:
                # Save keyword result
                kw = comparison.keyword_result
                await store.save_thesis_evaluation(
                    run_id=kw.run_id,
                    evaluator_type=kw.evaluator_type,
                    dataset_path=kw.dataset_path,
                    accuracy=kw.accuracy,
                    per_class_metrics={
                        k: v.to_dict() for k, v in kw.per_class_metrics.items()
                    },
                    confusion_matrix=kw.confusion_matrix,
                    latency_ms=kw.latency_ms,
                    errors=kw.errors,
                )

                # Save LLM result if available
                if comparison.llm_result:
                    llm = comparison.llm_result
                    await store.save_thesis_evaluation(
                        run_id=llm.run_id,
                        evaluator_type=llm.evaluator_type,
                        dataset_path=llm.dataset_path,
                        accuracy=llm.accuracy,
                        per_class_metrics={
                            k: v.to_dict() for k, v in llm.per_class_metrics.items()
                        },
                        confusion_matrix=llm.confusion_matrix,
                        latency_ms=llm.latency_ms,
                        token_usage=llm.token_usage,
                        errors=llm.errors,
                    )

                print(f"\nResults saved to database")
            finally:
                await store.close()

    else:
        print(f"Unknown evaluation type: {eval_type}")
        print("Valid types: keyword, llm, both")
        sys.exit(1)


async def cmd_eval_results(args):
    """Show historical thesis evaluation results."""
    from storage.signal_store import SignalStore

    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    limit = getattr(args, "limit", 10)
    eval_type = getattr(args, "type", None)

    print(BANNER_SEP)
    print("THESIS EVALUATION HISTORY")
    print(BANNER_SEP)

    store = SignalStore(db_path)
    await store.initialize()

    try:
        results = await store.get_thesis_evaluations(
            evaluator_type=eval_type,
            limit=limit,
        )

        if not results:
            print("\nNo evaluation runs found.")
            print("Run: python run_pipeline.py eval run --type keyword")
            return

        print(f"\nShowing {len(results)} most recent runs:")
        print()
        print(f"{'Run ID':<16} {'Type':<10} {'Accuracy':>10} {'Dataset':<30} {'Date':<20}")
        print(SECTION_SEP)

        for r in results:
            run_id = r["run_id"][:14] + ".." if len(r["run_id"]) > 16 else r["run_id"]
            eval_type = r["evaluator_type"]
            accuracy = f"{r['accuracy']:.1%}" if r["accuracy"] else "N/A"
            dataset = r["dataset_path"][:28] + ".." if len(r["dataset_path"]) > 30 else r["dataset_path"]
            date = r["created_at"][:19] if r["created_at"] else "N/A"

            print(f"{run_id:<16} {eval_type:<10} {accuracy:>10} {dataset:<30} {date:<20}")

        # Show trend if we have baseline
        if len(results) >= 2:
            latest = results[0]
            baseline = results[1]

            if latest["accuracy"] and baseline["accuracy"]:
                delta = latest["accuracy"] - baseline["accuracy"]
                if abs(delta) > 0.01:
                    trend = "IMPROVED" if delta > 0 else "REGRESSED"
                    print(f"\nTrend vs previous: {delta:+.1%} ({trend})")

    finally:
        await store.close()


# =============================================================================
# SHADOW STATUS
# =============================================================================

async def cmd_shadow_status(args) -> int:
    """Show shadow feature flag state and data volumes."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    days = getattr(args, "days", 7)
    json_output = getattr(args, "json_output", False)

    # Feature flag state
    flags = {
        "LLM_THESIS_MODE": os.getenv("LLM_THESIS_MODE", "off"),
        "ML_ENABLEMENT": os.getenv("ML_ENABLEMENT", "disabled"),
        "V2_ENABLEMENT": os.getenv("V2_ENABLEMENT", "disabled"),
        "USE_SHADOW_ENTITY_RESOLUTION": os.getenv("USE_SHADOW_ENTITY_RESOLUTION", "false"),
        "MERGE_WRITES_ENABLED": os.getenv("MERGE_WRITES_ENABLED", "disabled"),
        "DELIVERY_MODE": os.getenv("DELIVERY_MODE", "staging_only"),
        "BULK_TRIAGE_ENABLED": os.getenv("BULK_TRIAGE_ENABLED", "disabled"),
        "HUNTER_PROMOTE_ENABLED": os.getenv("HUNTER_PROMOTE_ENABLED", "disabled"),
        "USE_PHASE_G_IDENTITY_RESOLUTION": os.getenv("USE_PHASE_G_IDENTITY_RESOLUTION", "false"),
        "USE_CLAIM_FACTS": os.getenv("USE_CLAIM_FACTS", "false"),
    }

    store = SignalStore(db_path=db_path)
    try:
        await store.initialize()
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Shadow data volumes
        volumes = {}
        volume_queries = {
            "thesis_classifications": (
                "SELECT COUNT(*) FROM thesis_classifications WHERE classified_at >= ?",
                (cutoff,),
            ),
            "merge_suggestions": (
                "SELECT COUNT(*) FROM merge_suggestions WHERE created_at >= ?",
                (cutoff,),
            ),
            "claim_facts": (
                "SELECT COUNT(*) FROM claim_facts WHERE observed_at >= ?",
                (cutoff,),
            ),
            "entity_blocking_index": (
                "SELECT COUNT(*) FROM entity_blocking_index",
                (),
            ),
        }

        async with store._db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
            existing_tables = {row[0] for row in await cursor.fetchall()}

        for table, (query, params) in volume_queries.items():
            if table in existing_tables:
                async with store._db.execute(query, params) as cursor:
                    row = await cursor.fetchone()
                    volumes[table] = row[0] if row else 0
            else:
                volumes[table] = None  # table doesn't exist

        # Quick health metrics
        health = {}

        # LLM vs keyword agreement rate
        if volumes.get("thesis_classifications") and volumes["thesis_classifications"] > 0:
            agree_query = """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE
                        WHEN keyword_score >= 0.7 AND thesis_fit_score >= 0.7 THEN 1
                        WHEN keyword_score < 0.4 AND (thesis_fit_score IS NULL OR thesis_fit_score < 0.4) THEN 1
                        ELSE 0
                    END) as agreed
                FROM thesis_classifications
                WHERE classified_at >= ?
            """
            async with store._db.execute(agree_query, (cutoff,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0] > 0:
                    health["llm_agreement_rate"] = round(row[1] / row[0], 3)
                    health["llm_total_classifications"] = row[0]

        # Merge suggestion rejection rate
        if volumes.get("merge_suggestions") and volumes["merge_suggestions"] > 0:
            reject_query = """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected
                FROM merge_suggestions
                WHERE created_at >= ?
            """
            async with store._db.execute(reject_query, (cutoff,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0] > 0:
                    health["merge_rejection_rate"] = round(row[1] / row[0], 3)
                    health["merge_total_suggestions"] = row[0]

        result = {
            "period_days": days,
            "flags": flags,
            "volumes": volumes,
            "health": health,
        }

        if json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Shadow Status (last {days} days)")
            print("=" * 60)

            print("\nFeature Flags:")
            for flag, value in flags.items():
                active = value not in ("off", "disabled", "false", "staging_only")
                marker = "*" if active else " "
                print(f"  [{marker}] {flag} = {value}")

            print(f"\nShadow Data Volumes (last {days}d):")
            for table, count in volumes.items():
                if count is None:
                    print(f"  {table}: (table not created)")
                else:
                    print(f"  {table}: {count:,} rows")

            if health:
                print("\nQuick Health:")
                if "llm_agreement_rate" in health:
                    print(f"  LLM/keyword agreement: {health['llm_agreement_rate']:.1%} ({health['llm_total_classifications']} classified)")
                if "merge_rejection_rate" in health:
                    print(f"  Merge rejection rate: {health['merge_rejection_rate']:.1%} ({health['merge_total_suggestions']} suggestions)")
            else:
                print("\nQuick Health: no shadow data in period")

            print(f"\nFor detailed analysis: python scripts/shadow_report.py report ...")

        return 0
    finally:
        await store.close()


# =============================================================================
# ACTIVATION READINESS CHECK
# =============================================================================

async def cmd_activation_check(args) -> int:
    """Check activation readiness for the given step."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    try:
        await store.initialize()
        from monitoring.activation_gate import check_activation_readiness

        result = await check_activation_readiness(store, step=args.step)

        if getattr(args, "json_output", False):
            print(json.dumps(result.to_dict(), indent=2))
        else:
            symbol = STATUS_MAP.get(result.verdict, STATUS_UNKNOWN)
            print(f"{symbol} Activation Step {result.step}: {result.verdict.upper()}")
            if result.canary_verdict:
                age = f" ({result.canary_run_age_hours}h old)" if result.canary_run_age_hours else ""
                print(f"    Canary: {result.canary_verdict} (pass_rate={result.canary_pass_rate}){age}")
            else:
                print("    Canary: no data")
            if result.open_critical_alerts or result.open_warning_alerts:
                print(f"    Alerts: {result.open_critical_alerts} critical, {result.open_warning_alerts} warning")
            if result.reasons:
                for reason in result.reasons:
                    print(f"    - {reason}")
            # Inline jargon hint if SPC terms appear in reasons
            if any("drift monitor" in r or "SPC" in r for r in (result.reasons or [])):
                print(f"    Note: {explain('SPC')} — drift monitors track metric stability over time")
            print(f"    Can proceed: {result.can_proceed}")

        return 0 if result.can_proceed else 1
    finally:
        await store.close()


# =============================================================================
# PHASE G CHECK
# =============================================================================

async def cmd_phase_g_check(args) -> int:
    """Check Phase G entity resolution readiness."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    try:
        await store.initialize()
        from monitoring.phase_g_readiness import check_phase_g_readiness

        result = await check_phase_g_readiness(store)

        if getattr(args, "json_output", False):
            print(json.dumps(result.to_dict(), indent=2))
        else:
            symbol = STATUS_MAP.get(result.verdict, STATUS_UNKNOWN)
            print(f"{symbol} {explain('Phase G')} Readiness: {result.verdict.upper()}")
            for reason in result.reasons:
                print(f"    - {reason}")
            if result.metrics:
                print("    Metrics:")
                for k, v in result.metrics.items():
                    print(f"      {k}: {v}")
            print(f"    Can proceed: {result.can_proceed}")

        return 0 if result.can_proceed else 1
    finally:
        await store.close()


# =============================================================================
# ENTITY MERGE PREVIEW (read-only)
# =============================================================================

async def cmd_entity_merge_preview(args) -> int:
    """Preview pending entity merges without applying them."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    limit = getattr(args, "limit", 10)
    json_output = getattr(args, "json_output", False)

    store = SignalStore(db_path=db_path)
    try:
        await store.initialize()
        db = store._db

        # Check if merge_suggestions table exists
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='merge_suggestions'"
        ) as cursor:
            if not await cursor.fetchone():
                print("No merge_suggestions table found.")
                return 0

        # Query proposed merge pairs
        async with db.execute("""
            SELECT
                ms.id,
                ms.entity_a_company_id,
                ms.entity_b_company_id,
                ms.entity_a_canonical_key,
                ms.entity_b_canonical_key,
                ms.entity_a_company_name,
                ms.entity_b_company_name,
                ms.match_type,
                ms.similarity_score,
                ms.created_at
            FROM merge_suggestions ms
            WHERE ms.status = 'pending'
            ORDER BY ms.similarity_score DESC, ms.created_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            if json_output:
                print(json.dumps({"previews": [], "count": 0}))
            else:
                print("No pending merge suggestions found.")
            return 0

        previews = []
        for row in rows:
            (
                suggestion_id, entity_a, entity_b,
                key_a, key_b, name_a, name_b,
                match_type, similarity, created_at,
            ) = row

            # Determine lexmin winner
            winner = min(entity_a, entity_b)
            loser = max(entity_a, entity_b)

            # Count what would be affected (read-only queries)
            async with db.execute(
                "SELECT COUNT(*) FROM signals WHERE company_id = ?", (loser,)
            ) as c:
                signals_affected = (await c.fetchone())[0]

            review_items_affected = 0
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='review_items'"
            ) as c:
                if await c.fetchone():
                    async with db.execute(
                        "SELECT COUNT(*) FROM review_items WHERE company_id = ?", (loser,)
                    ) as c2:
                        review_items_affected = (await c2.fetchone())[0]

            files_affected = 0
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='company_files'"
            ) as c:
                if await c.fetchone():
                    async with db.execute(
                        "SELECT COUNT(*) FROM company_files WHERE company_id = ?", (loser,)
                    ) as c2:
                        files_affected = (await c2.fetchone())[0]

            previews.append({
                "suggestion_id": suggestion_id,
                "winner": winner,
                "loser": loser,
                "winner_key": key_a if entity_a == winner else key_b,
                "loser_key": key_b if entity_a == winner else key_a,
                "winner_name": name_a if entity_a == winner else name_b,
                "loser_name": name_b if entity_a == winner else name_a,
                "match_type": match_type,
                "similarity": similarity,
                "signals_affected": signals_affected,
                "review_items_affected": review_items_affected,
                "files_affected": files_affected,
                "created_at": created_at,
            })

        if json_output:
            print(json.dumps({"previews": previews, "count": len(previews)}, indent=2))
        else:
            print(f"Entity Merge Preview ({len(previews)} proposed)")
            print("=" * 90)
            for p in previews:
                print(f"\n  Suggestion #{p['suggestion_id']} ({p['match_type']}, similarity={p['similarity']:.2f})")
                print(f"    Winner: {p['winner']} ({p['winner_name'] or p['winner_key']})")
                print(f"    Loser:  {p['loser']} ({p['loser_name'] or p['loser_key']})")
                print(f"    Impact: {p['signals_affected']} signals, {p['review_items_affected']} reviews, {p['files_affected']} files")

        return 0
    finally:
        await store.close()


# =============================================================================
# ENTITY AUDIT
# =============================================================================

async def cmd_entity_audit(args) -> int:
    """Audit recent entity migrations and integrity."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    days = getattr(args, "days", 7)
    json_output = getattr(args, "json_output", False)

    store = SignalStore(db_path=db_path)
    try:
        await store.initialize()
        db = store._db
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Check tables exist
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cursor:
            existing = {row[0] for row in await cursor.fetchall()}

        report = {"period_days": days, "migrations": [], "lifo_intact": True, "orphaned_count": 0}

        # Recent migrations
        if "entity_migrations" in existing:
            async with db.execute("""
                SELECT from_entity_id, to_entity_id, merge_reason, merged_at
                FROM entity_migrations
                WHERE merged_at >= ?
                ORDER BY merged_at DESC
            """, (cutoff,)) as cursor:
                for row in await cursor.fetchall():
                    report["migrations"].append({
                        "from_entity_id": row[0],
                        "to_entity_id": row[1],
                        "merge_reason": row[2],
                        "merged_at": row[3],
                    })

            # LIFO chain integrity: check no circular references
            async with db.execute("""
                SELECT em1.from_entity_id
                FROM entity_migrations em1
                JOIN entity_migrations em2 ON em1.to_entity_id = em2.from_entity_id
                WHERE em2.to_entity_id = em1.from_entity_id
            """) as cursor:
                circular = await cursor.fetchall()
                if circular:
                    report["lifo_intact"] = False
                    report["circular_refs"] = len(circular)

        # Orphaned entity_ids
        async with db.execute("""
            SELECT COUNT(DISTINCT s.company_id) FROM signals s
            WHERE s.company_id IS NOT NULL
              AND s.company_id IN (
                  SELECT from_entity_id FROM entity_migrations
              )
              AND s.company_id NOT IN (
                  SELECT to_entity_id FROM entity_migrations
              )
        """) as cursor:
            report["orphaned_count"] = (await cursor.fetchone())[0]

        if json_output:
            print(json.dumps(report, indent=2))
        else:
            print(f"Entity Audit (last {days} days)")
            print("=" * 60)

            migrations = report["migrations"]
            if migrations:
                print(f"\nRecent Migrations ({len(migrations)}):")
                for m in migrations:
                    print(f"  {m['from_entity_id']} -> {m['to_entity_id']}")
                    print(f"    reason: {m['merge_reason']}, at: {m['merged_at']}")
            else:
                print("\nNo migrations in period.")

            lifo = "INTACT" if report["lifo_intact"] else "BROKEN"
            print(f"\nLIFO Chain Integrity: {lifo}")

            orphaned = report["orphaned_count"]
            status = "CLEAN" if orphaned == 0 else f"WARNING ({orphaned} orphaned)"
            print(f"Orphaned Entity IDs: {status}")

        return 0
    finally:
        await store.close()


# =============================================================================
# CLI ARGUMENT PARSER
# =============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Create argument parser"""

    parser = argparse.ArgumentParser(
        description="Discovery Engine Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full pipeline (dry run)
  python run_pipeline.py full --collectors github,sec_edgar --dry-run

  # Run collectors and persist to database
  python run_pipeline.py collect --collectors companies_house

  # Process pending signals and push to Notion
  python run_pipeline.py process

  # Sync suppression cache
  python run_pipeline.py sync

  # Show statistics
  python run_pipeline.py stats

  # Run health check
  python run_pipeline.py health

Environment variables:
  DISCOVERY_DB_PATH          - Path to SQLite database (default: signals.db)
  NOTION_API_KEY             - Notion integration token
  NOTION_DATABASE_ID         - Notion database ID
  GITHUB_TOKEN               - GitHub API token
  COMPANIES_HOUSE_API_KEY    - UK Companies House API key
  GNEWS_API_KEY              - GNews API key (for news_api collector)
  RSS_FEEDS                  - Custom RSS feeds (comma-separated URLs)
  RSS_CATEGORIES             - RSS feed categories (comma-separated: startup,health_tech,cpg)
  PARALLEL_COLLECTORS        - Run collectors in parallel (default: true)
  BATCH_SIZE                 - Processing batch size (default: 50)
  STRICT_MODE                - Require 2+ sources for auto-push (default: false)
  USE_GATING                 - Enable consumer filtering (default: true)
  USE_ENTITIES               - Enable entity resolution (default: false)
  USE_ASSET_STORE            - Enable asset store (default: false)
        """,
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Full pipeline command
    full_parser = subparsers.add_parser(
        "full",
        help="Run complete pipeline (collect + process + push)",
    )
    full_parser.add_argument(
        "--collectors",
        type=str,
        help="Comma-separated list of collectors (e.g., github,sec_edgar)",
    )
    full_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually push to Notion (default: false)",
    )
    full_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database (overrides env var)",
    )
    full_parser.add_argument(
        "--parallel",
        type=lambda x: x.lower() in ("true", "1", "yes"),
        help="Run collectors in parallel (true/false)",
    )
    full_parser.add_argument(
        "--batch-size",
        type=int,
        help="Processing batch size",
    )
    full_parser.add_argument(
        "--output",
        type=str,
        help="Save results to JSON file",
    )
    # Feature flags - gating is ON by default
    full_parser.add_argument(
        "--enable-gating",
        action="store_true",
        help="Explicitly enable two-stage gating (enabled by default)",
    )
    full_parser.add_argument(
        "--disable-gating",
        action="store_true",
        help="Disable two-stage gating (TriggerGate + LLMClassifierV2)",
    )
    full_parser.add_argument(
        "--use-entities",
        action="store_true",
        help="Enable entity resolution (asset to lead mapping)",
    )
    full_parser.add_argument(
        "--use-asset-store",
        action="store_true",
        help="Save raw snapshots to SourceAssetStore",
    )

    # Collect command
    collect_parser = subparsers.add_parser(
        "collect",
        help="Run collectors only",
    )
    collect_parser.add_argument(
        "--collectors",
        type=str,
        required=True,
        help="Comma-separated list of collectors (e.g., github,sec_edgar)",
    )
    collect_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't persist signals to database",
    )
    collect_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )
    collect_parser.add_argument(
        "--parallel",
        type=lambda x: x.lower() in ("true", "1", "yes"),
        help="Run collectors in parallel (true/false)",
    )
    # Feature flags for collect
    collect_parser.add_argument(
        "--enable-gating",
        action="store_true",
        help="Explicitly enable two-stage gating (enabled by default)",
    )
    collect_parser.add_argument(
        "--disable-gating",
        action="store_true",
        help="Disable two-stage gating",
    )
    collect_parser.add_argument(
        "--use-asset-store",
        action="store_true",
        help="Save raw snapshots to SourceAssetStore",
    )

    # Process command
    process_parser = subparsers.add_parser(
        "process",
        help="Process pending signals",
    )
    process_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually push to Notion",
    )
    process_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )
    process_parser.add_argument(
        "--batch-size",
        type=int,
        help="Processing batch size",
    )
    # Feature flags for process
    process_parser.add_argument(
        "--enable-gating",
        action="store_true",
        help="Explicitly enable two-stage gating (enabled by default)",
    )
    process_parser.add_argument(
        "--disable-gating",
        action="store_true",
        help="Disable two-stage gating",
    )
    process_parser.add_argument(
        "--use-entities",
        action="store_true",
        help="Enable entity resolution",
    )
    process_parser.add_argument(
        "--source-api",
        type=str,
        help="Only process signals from this source API (e.g., hacker_news)",
    )

    # Sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync suppression cache from Notion",
    )
    sync_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )
    sync_parser.add_argument(
        "--recovery-override",
        action="store_true",
        help="Allow the audited recovery sync path to proceed even when the production DB guard is tripped",
    )

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show pipeline statistics",
    )
    stats_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )

    # Health command
    health_parser = subparsers.add_parser(
        "health",
        help="Run health checks on all components",
    )
    health_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )
    health_parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Days to analyze for signal health (default: 30)",
    )
    health_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON",
    )
    health_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed signal health report",
    )

    # Health semantics flags
    # - default behavior remains strict (external failures fail the command)
    # - operators can opt into core-only or degraded-on-external semantics
    health_parser.add_argument(
        "--core-only",
        action="store_true",
        help="Run only core checks (DB/pipeline/signal health); skip external integrations",
    )
    health_parser.add_argument(
        "--allow-external-failures",
        action="store_true",
        help="Treat external integration failures as warnings (exit 0 if core is healthy)",
    )

    # Step 3B readiness command
    step3b_parser = subparsers.add_parser(
        "step3b-readiness",
        help="Check Step 3B activation readiness (multi-source, canary, Phase G)",
    )
    step3b_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )
    step3b_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON",
    )

    # Metrics command
    metrics_parser = subparsers.add_parser(
        "metrics",
        help="Show pipeline run metrics with per-collector breakdown",
    )
    metrics_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=5,
        help="Number of recent runs to show (default: 5)",
    )
    metrics_parser.add_argument(
        "--collector", "-c",
        type=str,
        default=None,
        help="Filter to specific collector",
    )
    metrics_parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        dest="db_path",
        help="Path to signals database",
    )

    # Embeddings batch command
    embeddings_parser = subparsers.add_parser(
        "embeddings",
        help="Pre-compute company embeddings for similarity search",
    )
    embeddings_parser.add_argument(
        "--db-path",
        type=str,
        default="signals.db",
        help="Path to SQLite database (default: signals.db)",
    )
    embeddings_parser.add_argument(
        "--force",
        action="store_true",
        help="Force recompute all embeddings (ignore cache)",
    )
    embeddings_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum companies to process (for testing)",
    )
    embeddings_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose logging",
    )

    # Pipeline subcommands
    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help="Pipeline dashboard commands",
    )
    pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_cmd")

    # pipeline status
    pipeline_status_parser = pipeline_sub.add_parser("status", help="Show pipeline status overview")
    pipeline_status_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )

    # pipeline qualified
    qual_parser = pipeline_sub.add_parser("qualified", help="List qualified signals")
    qual_parser.add_argument("--limit", type=int, default=20, help="Max signals to show")
    qual_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )

    # pipeline push
    push_parser = pipeline_sub.add_parser("push", help="Push qualified to Notion")
    push_parser.add_argument("--confirm", action="store_true", help="Confirm push")
    push_parser.add_argument("--dry-run", action="store_true", help="Preview only")
    push_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )

    # pipeline claims
    claims_parser = pipeline_sub.add_parser("claims", help="Query claim facts for an entity")
    claims_parser.add_argument("entity_id", type=str, help="Entity ID (16-char hex)")
    claims_parser.add_argument("--history", action="store_true", help="Show full history")
    claims_parser.add_argument("--at", type=str, dest="at_time", help="Point-in-time query (ISO timestamp)")
    claims_parser.add_argument("--predicate", type=str, help="Filter by predicate (company_name, etc.)")
    claims_parser.add_argument("--db-path", type=str, help="Path to SQLite database")

    # pipeline entities
    entities_parser = pipeline_sub.add_parser("entities", help="Show entity resolution statistics")
    entities_parser.add_argument("--limit", type=int, default=20, help="Max entities to show")
    entities_parser.add_argument("--db-path", type=str, help="Path to SQLite database")

    # Schema command with subcommands
    schema_parser = subparsers.add_parser(
        "schema",
        help="Notion schema management (validate, repair, docs)",
    )
    schema_subparsers = schema_parser.add_subparsers(dest="schema_command", help="Schema operation")

    # schema validate subcommand
    validate_parser = schema_subparsers.add_parser(
        "validate",
        help="Validate Notion database schema",
    )
    validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output results as JSON",
    )

    # schema repair subcommand
    repair_parser = schema_subparsers.add_parser(
        "repair",
        help="Repair Notion database schema (create missing properties/options)",
    )
    repair_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show repair plan without executing",
    )
    repair_parser.add_argument(
        "--properties",
        type=str,
        help="Comma-separated property names to repair (selective repair)",
    )

    # schema docs subcommand
    docs_parser = schema_subparsers.add_parser(
        "docs",
        help="Generate schema documentation",
    )
    docs_parser.add_argument(
        "--output",
        type=str,
        help="Output file path (default: stdout)",
    )

    # --- hunter command group ---
    hunter_parser = subparsers.add_parser(
        "hunter",
        help="Active Hunter sandbox — pattern-driven deal sourcing",
    )
    hunter_sub = hunter_parser.add_subparsers(dest="hunter_cmd")

    hunter_gen_parser = hunter_sub.add_parser("generate", help="Generate queries from patterns or seeds")
    hunter_gen_parser.add_argument("--bootstrap", type=str, help="Path to seeds JSON file")
    hunter_gen_parser.add_argument("--db-path", type=str, help="Database path")

    hunter_run_parser = hunter_sub.add_parser("run", help="Execute hunter queries")
    hunter_run_parser.add_argument("--dry-run", action="store_true", help="Create queries but don't execute")
    hunter_run_parser.add_argument("--db-path", type=str, help="Database path")
    hunter_run_parser.add_argument("--collector", type=str, default="github", help="Collector to use")
    hunter_run_parser.add_argument("--bootstrap", type=str, help="Path to seeds JSON file for bootstrap mode")

    hunter_status_parser = hunter_sub.add_parser("status", help="Show hunter run status")
    hunter_status_parser.add_argument("--run-id", type=str, help="Specific run ID")
    hunter_status_parser.add_argument("--db-path", type=str, help="Database path")

    hunter_review_parser = hunter_sub.add_parser("review", help="List results pending review")
    hunter_review_parser.add_argument("--run-id", type=str, help="Filter by run ID")
    hunter_review_parser.add_argument("--status", type=str, default="pending", help="Result status filter")
    hunter_review_parser.add_argument("--limit", type=int, default=20, help="Max results")
    hunter_review_parser.add_argument("--db-path", type=str, help="Database path")

    hunter_fb_parser = hunter_sub.add_parser("feedback", help="Provide feedback on a result")
    hunter_fb_parser.add_argument("result_id", type=int, help="Result ID")
    hunter_fb_parser.add_argument("status", choices=["relevant", "not_relevant"], help="Feedback status")
    hunter_fb_parser.add_argument("--reason", type=str, help="Feedback reason")
    hunter_fb_parser.add_argument("--db-path", type=str, help="Database path")

    hunter_promote_parser = hunter_sub.add_parser("promote", help="Promote a result to signals")
    hunter_promote_parser.add_argument("result_id", type=int, help="Result ID to promote")
    hunter_promote_parser.add_argument("--db-path", type=str, help="Database path")

    hunter_budget_parser = hunter_sub.add_parser("budget", help="Show budget status")
    hunter_budget_parser.add_argument("--date", type=str, help="Budget date (YYYY-MM-DD)")
    hunter_budget_parser.add_argument("--db-path", type=str, help="Database path")

    # --- drift command group ---
    drift_parser = subparsers.add_parser(
        "drift",
        help="Drift monitoring — SPC checks, daily aggregation, alert management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Drift monitoring commands for SPC quality checks and alert management.

  drift check          Run SPC check (read-only)
  drift aggregate      Aggregate daily metrics
  drift alerts         List drift alerts
  drift ack            Acknowledge an alert
  drift snooze         Snooze an alert
  drift resolve        Resolve an alert
  drift recommend      Generate recommendations (read-only)
  drift gc             Delete old metrics/alerts
  drift export-metrics Export metrics to CSV/JSONL
""",
    )
    drift_sub = drift_parser.add_subparsers(dest="drift_cmd")

    # drift check
    drift_check_parser = drift_sub.add_parser("check", help="Run SPC check (read-only)")
    drift_check_parser.add_argument("--metrics", nargs="*", help="Specific metrics to check")
    drift_check_parser.add_argument("--db-path", type=str, help="Database path")

    # drift aggregate
    drift_agg_parser = drift_sub.add_parser("aggregate", help="Aggregate daily metrics")
    drift_agg_parser.add_argument("--days", type=int, default=90, help="Days to backfill (default: 90)")
    drift_agg_parser.add_argument("--db-path", type=str, help="Database path")

    # drift alerts
    drift_alerts_parser = drift_sub.add_parser("alerts", help="List drift alerts")
    drift_alerts_parser.add_argument("--status", type=str, choices=["open", "acknowledged", "snoozed", "resolved"], help="Filter by status")
    drift_alerts_parser.add_argument("--limit", type=int, default=50, help="Max alerts to show (default: 50)")
    drift_alerts_parser.add_argument("--db-path", type=str, help="Database path")

    # drift ack
    drift_ack_parser = drift_sub.add_parser("ack", help="Acknowledge an alert")
    drift_ack_parser.add_argument("alert_id", type=int, help="Alert ID to acknowledge")
    drift_ack_parser.add_argument("--reason", type=str, required=True, help="Reason for acknowledgement")
    drift_ack_parser.add_argument("--db-path", type=str, help="Database path")

    # drift snooze
    drift_snooze_parser = drift_sub.add_parser("snooze", help="Snooze an alert")
    drift_snooze_parser.add_argument("alert_id", type=int, help="Alert ID to snooze")
    drift_snooze_parser.add_argument("--hours", type=int, required=True, help="Hours to snooze (1-168)")
    drift_snooze_parser.add_argument("--reason", type=str, help="Reason for snooze")
    drift_snooze_parser.add_argument("--db-path", type=str, help="Database path")

    # drift resolve
    drift_resolve_parser = drift_sub.add_parser("resolve", help="Resolve an alert")
    drift_resolve_parser.add_argument("alert_id", type=int, help="Alert ID to resolve")
    drift_resolve_parser.add_argument("--reason", type=str, required=True, help="Resolution reason")
    drift_resolve_parser.add_argument("--db-path", type=str, help="Database path")

    # drift recommend
    drift_rec_parser = drift_sub.add_parser("recommend", help="Generate recommendations (read-only)")
    drift_rec_parser.add_argument("--days", type=int, default=7, help="Lookback days (default: 7)")
    drift_rec_parser.add_argument("--db-path", type=str, help="Database path")

    # drift gc
    drift_gc_parser = drift_sub.add_parser("gc", help="Delete old metrics/alerts")
    drift_gc_parser.add_argument("--metrics-days", type=int, default=365, help="Keep metrics newer than N days (default: 365)")
    drift_gc_parser.add_argument("--alerts-days", type=int, default=180, help="Keep alerts newer than N days (default: 180)")
    drift_gc_parser.add_argument("--db-path", type=str, help="Database path")

    # drift export-metrics
    drift_export_parser = drift_sub.add_parser("export-metrics", help="Export metrics to CSV/JSONL")
    drift_export_parser.add_argument("--days", type=int, default=365, help="Export last N days (default: 365)")
    drift_export_parser.add_argument("--format", type=str, choices=["csv", "jsonl"], default="csv", help="Output format (default: csv)")
    drift_export_parser.add_argument("--out", type=str, help="Output file path")
    drift_export_parser.add_argument("--db-path", type=str, help="Database path")

    # --- import-csv command ---
    import_parser = subparsers.add_parser(
        "import-csv",
        help="Import signals from CSV files (OpenVC, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Import signals from CSV exports.

Supported sources:
  openvc    - OpenVC.app CSV exports (deal flow, investor connections)
  pitchbook - PitchBook CSV exports (company data, deal flow)

Examples:
  # Dry run to see what would be imported
  python run_pipeline.py import-csv --source openvc path/to/export.csv --dry-run

  # Import with thesis filter (only consumer sectors)
  python run_pipeline.py import-csv --source openvc export.csv --sectors Consumer,HealthTech

  # Import only pre-seed and seed stage companies
  python run_pipeline.py import-csv --source openvc export.csv --stages pre-seed,seed,series-a
""",
    )
    import_parser.add_argument(
        "file",
        type=str,
        help="Path to CSV file to import",
    )
    import_parser.add_argument(
        "--source",
        type=str,
        default="openvc",
        choices=["openvc", "pitchbook"],
        help="CSV source format (default: openvc)",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without persisting",
    )
    import_parser.add_argument(
        "--sectors",
        type=str,
        help="Only import these sectors (comma-separated, e.g., Consumer,HealthTech)",
    )
    import_parser.add_argument(
        "--stages",
        type=str,
        help="Only import these stages (comma-separated, e.g., pre-seed,seed,series-a)",
    )
    import_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- corroborate command ---
    corroborate_parser = subparsers.add_parser(
        "corroborate",
        help="Look up companies in incorporation databases for corroboration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Look up companies in OpenCorporates to add corroborating incorporation signals.

This command takes held/pending signals and looks them up in corporate registries
(Delaware, California, UK Companies House) to find incorporation records.

Examples:
  # Look up top 20 held signals
  python run_pipeline.py corroborate --limit 20

  # Dry run to see what would be looked up
  python run_pipeline.py corroborate --dry-run --limit 10

  # Look up specific source (e.g., PitchBook signals)
  python run_pipeline.py corroborate --source pitchbook --limit 50
""",
    )
    corroborate_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum companies to look up (default: 20, max 50 for rate limits)",
    )
    corroborate_parser.add_argument(
        "--source",
        type=str,
        help="Only look up signals from this source (e.g., pitchbook)",
    )
    corroborate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be looked up without making API calls",
    )
    corroborate_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- gold-set command group ---
    goldset_parser = subparsers.add_parser(
        "gold-set",
        help="Manage gold set for evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Gold set management for evaluation framework.

Examples:
  # List gold set companies
  python run_pipeline.py gold-set list

  # Show gold set statistics
  python run_pipeline.py gold-set stats

  # Export gold set to JSON
  python run_pipeline.py gold-set export --format json --output gold_set.json

  # Import gold set from JSON
  python run_pipeline.py gold-set import gold_set.json
""",
    )
    goldset_sub = goldset_parser.add_subparsers(dest="goldset_cmd")

    # gold-set list
    goldset_list_parser = goldset_sub.add_parser("list", help="List gold set companies")
    goldset_list_parser.add_argument(
        "--category",
        type=str,
        choices=["core_sector", "long_tail", "ambiguous", "hard_negative"],
        help="Filter by category",
    )
    goldset_list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum companies to list",
    )
    goldset_list_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # gold-set stats
    goldset_stats_parser = goldset_sub.add_parser("stats", help="Show gold set statistics")
    goldset_stats_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # gold-set export
    goldset_export_parser = goldset_sub.add_parser("export", help="Export gold set")
    goldset_export_parser.add_argument(
        "--format",
        type=str,
        choices=["json", "csv"],
        default="json",
        help="Export format (default: json)",
    )
    goldset_export_parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output file path",
    )
    goldset_export_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # gold-set import
    goldset_import_parser = goldset_sub.add_parser("import", help="Import gold set")
    goldset_import_parser.add_argument(
        "file",
        type=str,
        help="File to import (JSON or CSV)",
    )
    goldset_import_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- evaluate command ---
    evaluate_parser = subparsers.add_parser(
        "evaluate",
        help="Run evaluation against gold set",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Run evaluation against gold set.

Examples:
  # Run full evaluation
  python run_pipeline.py evaluate --type full

  # Run extraction evaluation only
  python run_pipeline.py evaluate --type extraction

  # Run with drift detection
  python run_pipeline.py evaluate --type full --check-drift
""",
    )
    evaluate_parser.add_argument(
        "--type",
        type=str,
        choices=["extraction", "similarity", "investor_match", "full"],
        default="full",
        help="Evaluation type (default: full)",
    )
    evaluate_parser.add_argument(
        "--gold-set-version",
        type=str,
        default="v1",
        help="Gold set version (default: v1)",
    )
    evaluate_parser.add_argument(
        "--check-drift",
        action="store_true",
        help="Check for drift and generate alerts",
    )
    evaluate_parser.add_argument(
        "--notify-slack",
        action="store_true",
        help="Send Slack notifications for red alerts",
    )
    evaluate_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- monitor command with subcommands ---
    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Website monitoring commands",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Website monitoring for tracked companies.

Examples:
  # Add a watch for a URL
  python run_pipeline.py monitor add https://acme.ai

  # Run monitoring checks
  python run_pipeline.py monitor run

  # Show monitoring status
  python run_pipeline.py monitor status
""",
    )
    monitor_sub = monitor_parser.add_subparsers(dest="monitor_cmd", help="Monitor operation")

    # monitor add
    monitor_add_parser = monitor_sub.add_parser("add", help="Add a new watch")
    monitor_add_parser.add_argument("url", help="URL to monitor")
    monitor_add_parser.add_argument(
        "--canonical-key",
        type=str,
        help="Canonical key (auto-generated from URL if not provided)",
    )
    monitor_add_parser.add_argument(
        "--type",
        type=str,
        default="website",
        choices=["website", "portfolio", "linkedin_about"],
        help="Watch type (default: website)",
    )
    monitor_add_parser.add_argument(
        "--interval",
        type=int,
        default=86400,
        help="Check interval in seconds (default: 86400 = 24h)",
    )
    monitor_add_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # monitor run
    monitor_run_parser = monitor_sub.add_parser("run", help="Run monitoring checks")
    monitor_run_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max watches to check (default: 100)",
    )
    monitor_run_parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Disable semantic drift detection",
    )
    monitor_run_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )
    monitor_run_parser.add_argument(
        "--lock-timeout",
        type=int,
        default=30,
        help="Seconds to wait for advisory lock (default: 30)",
    )
    monitor_run_parser.add_argument(
        "--force-break-lock",
        action="store_true",
        help="Force-break existing lock (use if lock is stale)",
    )
    monitor_run_parser.add_argument(
        "--only-portfolio",
        action="store_true",
        help="Only check portfolio watches (watch_type='portfolio')",
    )

    # monitor status
    monitor_status_parser = monitor_sub.add_parser("status", help="Show monitoring status")
    monitor_status_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # monitor list
    monitor_list_parser = monitor_sub.add_parser("list", help="List active watches")
    monitor_list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max watches to list (default: 50)",
    )
    monitor_list_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # monitor sync-portfolio
    monitor_sync_parser = monitor_sub.add_parser(
        "sync-portfolio",
        help="Sync portfolio companies to monitoring watches",
    )
    monitor_sync_parser.add_argument(
        "--portfolio-path",
        type=str,
        default="config/portfolio.json",
        help="Path to portfolio.json (default: config/portfolio.json)",
    )
    monitor_sync_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )
    monitor_sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't make changes, just show what would happen",
    )
    monitor_sync_parser.add_argument(
        "--no-deactivate",
        action="store_true",
        help="Don't deactivate portfolio watches not in config",
    )

    # monitor dispatch (Slack alerts)
    monitor_dispatch_parser = monitor_sub.add_parser(
        "dispatch",
        help="Send Slack alerts for unnotified monitoring alerts",
    )
    monitor_dispatch_parser.add_argument(
        "--run-url",
        type=str,
        help="GitHub Actions run URL to include in alerts",
    )
    monitor_dispatch_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )
    monitor_dispatch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't send Slack messages, just show what would be sent",
    )

    # --- outbox command ---
    outbox_parser = subparsers.add_parser(
        "outbox",
        help="Outbox queue management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Manage the outbox queue for async writes.

Examples:
  # Drain pending outbox entries
  python run_pipeline.py outbox drain

  # Drain without Notion (monitoring events only)
  python run_pipeline.py outbox drain --no-notion
""",
    )
    outbox_sub = outbox_parser.add_subparsers(dest="outbox_cmd", help="Outbox operation")

    # outbox drain
    outbox_drain_parser = outbox_sub.add_parser("drain", help="Drain pending entries")
    outbox_drain_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max entries to process (default: 50)",
    )
    outbox_drain_parser.add_argument(
        "--no-notion",
        action="store_true",
        help="Skip Notion connector (process monitoring events only)",
    )
    outbox_drain_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- shadow-backfill command ---
    shadow_backfill_parser = subparsers.add_parser(
        "shadow-backfill",
        help="Backfill shadow logs for v1/v2 comparison (Phase 0C)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Backfill shadow logs from existing signals with stratified sampling.

Generates thesis_match shadow logs for v1/v2 comparison without mutating
signal status or triggering Notion pushes.

Features:
  - Stratified sampling by (source, month) for diverse coverage
  - Forced v2 execution for shadow comparison
  - Run isolation with UUID run_id
  - Policy hash tracking from v2_shadow

Examples:
  # Preview sampling distribution (dry run)
  python run_pipeline.py shadow-backfill --dry-run

  # Run with default settings (20 per bucket, 90 day lookback)
  python run_pipeline.py shadow-backfill

  # Custom sampling
  python run_pipeline.py shadow-backfill --per-bucket 50 --lookback-days 180
""",
    )
    shadow_backfill_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview sampling distribution without writing to database",
    )
    shadow_backfill_parser.add_argument(
        "--per-bucket",
        type=int,
        default=20,
        help="Samples per (source, month) bucket (default: 20)",
    )
    shadow_backfill_parser.add_argument(
        "--lookback-days",
        type=int,
        default=90,
        help="Days to look back for signals (default: 90)",
    )
    shadow_backfill_parser.add_argument(
        "--max-candidates",
        type=int,
        default=20000,
        help="Max candidates to fetch (default: 20000)",
    )
    shadow_backfill_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- ground-truth command ---
    ground_truth_parser = subparsers.add_parser(
        "ground-truth",
        help="Export ground truth labels from Notion CRM (Phase 0C)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Export labeled deals from Notion CRM for thesis matcher evaluation.

Exports companies with human-assigned labels for ground truth comparison:
  - Positive (thesis fit): Source, Initial Meeting, Dilligence, Committed, Funded, Tracking
  - Negative (thesis reject): Passed

Output format: JSONL with company_name, status, sector, description, canonical_key

Examples:
  # Export all labeled deals
  python run_pipeline.py ground-truth --out ground_truth.jsonl

  # Export only positive labels
  python run_pipeline.py ground-truth --positive-only --out positives.jsonl

  # Export with specific statuses
  python run_pipeline.py ground-truth --statuses Funded,Passed --out eval_set.jsonl
""",
    )
    ground_truth_parser.add_argument(
        "--out",
        type=str,
        default="ground_truth.jsonl",
        help="Output JSONL file path (default: ground_truth.jsonl)",
    )
    ground_truth_parser.add_argument(
        "--positive-only",
        action="store_true",
        help="Export only positive labels (excludes Passed)",
    )
    ground_truth_parser.add_argument(
        "--statuses",
        type=str,
        help="Comma-separated list of statuses to export (overrides defaults)",
    )

    # --- import-emails command ---
    import_emails_parser = subparsers.add_parser(
        "import-emails",
        help="Import emails from MBOX file into relationship graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Import Gmail Takeout MBOX file into the relationship graph.

Extracts email headers and builds domain-level relationship strength scores
based on intro count, reply rate, and recency.

Examples:
  # Dry run to preview
  python run_pipeline.py import-emails --mbox ~/takeout.mbox --email me@startup.com --dry-run

  # Import to database
  python run_pipeline.py import-emails --mbox ~/takeout.mbox --email me@startup.com
""",
    )
    import_emails_parser.add_argument(
        "--mbox",
        type=str,
        required=True,
        help="Path to MBOX file (Gmail Takeout export)",
    )
    import_emails_parser.add_argument(
        "--email",
        type=str,
        required=True,
        help="Your email address (to determine sent vs received)",
    )
    import_emails_parser.add_argument(
        "--db-path",
        type=str,
        default="private_graph.db",
        help="Path to relationship database (default: private_graph.db)",
    )
    import_emails_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be imported without storing",
    )

    # --- eval command group ---
    eval_parser = subparsers.add_parser(
        "eval",
        help="Thesis classification evaluation harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Thesis classification evaluation harness.

Evaluates keyword matcher and LLM classifier accuracy against ground truth datasets.
Provides per-class metrics, confusion matrices, and trend tracking.

Examples:
  # Export ground truth from Notion
  python run_pipeline.py eval export --output datasets/thesis_ground_truth.jsonl

  # Run keyword evaluation
  python run_pipeline.py eval run --type keyword

  # Run LLM evaluation
  python run_pipeline.py eval run --type llm

  # Compare keyword vs LLM
  python run_pipeline.py eval run --type both

  # View historical results
  python run_pipeline.py eval results --limit 10
""",
    )
    eval_sub = eval_parser.add_subparsers(dest="eval_cmd", help="Evaluation operation")

    # eval export
    eval_export_parser = eval_sub.add_parser(
        "export",
        help="Export ground truth from Notion",
    )
    eval_export_parser.add_argument(
        "--output", "-o",
        type=str,
        default="datasets/thesis_ground_truth.jsonl",
        help="Output JSONL file path (default: datasets/thesis_ground_truth.jsonl)",
    )
    eval_export_parser.add_argument(
        "--min-examples",
        type=int,
        default=100,
        help="Minimum examples to export (default: 100)",
    )
    eval_export_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show samples without writing to file",
    )

    # eval run
    eval_run_parser = eval_sub.add_parser(
        "run",
        help="Run thesis classification evaluation",
    )
    eval_run_parser.add_argument(
        "--type", "-t",
        type=str,
        default="keyword",
        choices=["keyword", "llm", "both"],
        help="Evaluation type (default: keyword)",
    )
    eval_run_parser.add_argument(
        "--dataset", "-d",
        type=str,
        default="datasets/thesis_sample.jsonl",
        help="Path to JSONL dataset (default: datasets/thesis_sample.jsonl)",
    )
    eval_run_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )
    eval_run_parser.add_argument(
        "--no-save",
        action="store_true",
        dest="no_save",
        help="Don't save results to database",
    )

    # eval results
    eval_results_parser = eval_sub.add_parser(
        "results",
        help="View historical evaluation results",
    )
    eval_results_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=10,
        help="Number of results to show (default: 10)",
    )
    eval_results_parser.add_argument(
        "--type", "-t",
        type=str,
        choices=["keyword", "llm"],
        default=None,
        help="Filter by evaluation type",
    )
    eval_results_parser.add_argument(
        "--db-path",
        type=str,
        default=os.getenv("DISCOVERY_DB_PATH", "signals.db"),
        help="Path to signals database",
    )

    # --- sync-lps command ---
    sync_lps_parser = subparsers.add_parser(
        "sync-lps",
        help="Sync LP relationships from Notion database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Sync LP (Limited Partner) records from Notion to build firm relationships.

Extracts LP status tiers and maps to relationship scores:
- Docs Signed: 0.95
- Verbal Confirm: 0.70
- Engagement Sent: 0.40
- In Database: 0.25

Examples:
  # Dry run to preview
  python run_pipeline.py sync-lps --dry-run

  # Sync LP relationships
  python run_pipeline.py sync-lps

  # Sync with explicit database ID
  python run_pipeline.py sync-lps --database-id abc123
""",
    )
    sync_lps_parser.add_argument(
        "--database-id",
        type=str,
        help="Notion LP database ID (overrides NOTION_LP_DATABASE_ID env var)",
    )
    sync_lps_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be synced without storing",
    )
    sync_lps_parser.add_argument(
        "--user-email",
        type=str,
        help="User email for relationship graph (overrides USER_EMAIL env var)",
    )
    sync_lps_parser.add_argument(
        "--db-path",
        type=str,
        default="private_graph.db",
        help="Path to relationship database (default: private_graph.db)",
    )

    # --- relationship-health command ---
    rel_health_parser = subparsers.add_parser(
        "relationship-health",
        help="Check relationship data health and staleness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Check relationship data health and staleness.

Monitors:
- Email scan freshness (days since last MBOX import)
- LP sync freshness (days since last Notion sync)
- Relationship counts by source

Examples:
  # Basic health check
  python run_pipeline.py relationship-health --user-email user@example.com

  # Custom thresholds
  python run_pipeline.py relationship-health --user-email user@example.com \\
      --email-stale-days 14 --lp-stale-days 7

  # JSON output
  python run_pipeline.py relationship-health --user-email user@example.com --json
""",
    )
    rel_health_parser.add_argument(
        "--user-email",
        type=str,
        help="User email address (or set USER_EMAIL env var)",
    )
    rel_health_parser.add_argument(
        "--db-path",
        type=str,
        default="private_graph.db",
        help="Path to private graph database (default: private_graph.db)",
    )
    rel_health_parser.add_argument(
        "--email-stale-days",
        type=int,
        default=7,
        help="Days until email data is considered stale (default: 7)",
    )
    rel_health_parser.add_argument(
        "--lp-stale-days",
        type=int,
        default=3,
        help="Days until LP data is considered stale (default: 3)",
    )
    rel_health_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as JSON",
    )

    # --- warm-intros command ---
    warm_intros_parser = subparsers.add_parser(
        "warm-intros",
        help="Look up warm intro data for investor domains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Look up warm intro relationship data for investor domains.

Shows warm intro scores, badges, and attribution for domains that have
existing relationship data (from Gmail import or LP sync).

Examples:
  # Look up a single domain
  python run_pipeline.py warm-intros --domain sequoia.com

  # Look up a single domain (verbose output)
  python run_pipeline.py warm-intros --domain sequoia.com --verbose

  # List all domains with warm intro data
  python run_pipeline.py warm-intros --all

  # JSON output (for automation)
  python run_pipeline.py warm-intros --domain a16z.com --json
""",
    )
    warm_intros_parser.add_argument(
        "--domain",
        type=str,
        help="Investor domain to look up (e.g., sequoia.com)",
    )
    warm_intros_parser.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Show all domains with warm intro data",
    )
    warm_intros_parser.add_argument(
        "--user-email",
        type=str,
        help="User email address (or set USER_EMAIL env var)",
    )
    warm_intros_parser.add_argument(
        "--db-path",
        type=str,
        default="private_graph.db",
        help="Path to private graph database (default: private_graph.db)",
    )
    warm_intros_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed output (score, badge, attribution, source)",
    )
    warm_intros_parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as JSON",
    )

    # --- export-queue command ---
    export_queue_parser = subparsers.add_parser(
        "export-queue",
        help="Export pending/queued signals to CSV for offline review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Export pending/queued signals to CSV for offline operator review.

Examples:
  # Export all signals to CSV file
  python run_pipeline.py export-queue --out queue.csv

  # Export only pending signals with minimum confidence
  python run_pipeline.py export-queue --status pending --min-confidence 0.4

  # Export signals from last 30 days to stdout
  python run_pipeline.py export-queue --days 30
""",
    )
    export_queue_parser.add_argument(
        "--format",
        type=str,
        default="csv",
        choices=["csv"],
        help="Output format (default: csv)",
    )
    export_queue_parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output file path (default: stdout)",
    )
    export_queue_parser.add_argument(
        "--status",
        type=str,
        choices=["pending", "queued", "pushed", "rejected"],
        help="Filter by processing status",
    )
    export_queue_parser.add_argument(
        "--min-confidence",
        type=float,
        help="Minimum confidence score (e.g., 0.4)",
    )
    export_queue_parser.add_argument(
        "--days",
        type=int,
        help="Only include signals from the last N days",
    )
    export_queue_parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (overrides env var)",
    )
    export_queue_parser.add_argument(
        "--schema",
        type=str,
        choices=["v1", "v2"],
        default="v1",
        help="Export schema version: v1 (core columns, default) or v2 (extended with precedents/exemplars/ACH)",
    )

    # --- push command ---
    push_parser = subparsers.add_parser(
        "push",
        help="Push specific signals to Notion by ID (manual push)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Push specific signals to Notion by their signal IDs.

Requires DELIVERY_MODE >= manual_publish (unless --dry-run).

Examples:
  # Dry run to preview what would be pushed
  python run_pipeline.py push --signal-ids 1,2,3 --dry-run

  # Actually push specific signals
  python run_pipeline.py push --signal-ids 5

  # Push with custom database path
  python run_pipeline.py push --signal-ids 1,2 --db-path custom.db
""",
    )
    push_parser.add_argument(
        "--signal-ids",
        type=str,
        required=True,
        help="Comma-separated signal IDs to push (e.g., 1,2,3)",
    )
    push_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview what would be pushed without actually pushing",
    )
    push_parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to SQLite database (overrides env var)",
    )
    push_parser.add_argument(
        "--override-hold",
        action="store_true",
        default=False,
        help="Override low-confidence HOLD to NEEDS_REVIEW (push as Tracking)",
    )

    # --- triage command group ---
    triage_parser = subparsers.add_parser(
        "triage",
        help="Triage pending signals (list, approve, reject, defer)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Triage pending signals for review.

Examples:
  # List pending signals (compact table)
  python run_pipeline.py triage list --limit 20

  # Show full intelligence for a signal
  python run_pipeline.py triage detail 123

  # Approve a signal for push
  python run_pipeline.py triage approve 123 --reason "Clear consumer fit"

  # Reject a signal
  python run_pipeline.py triage reject 124 --reason "B2B dev tool"

  # Defer a signal for later review
  python run_pipeline.py triage defer 125 --reason "Need more signals"
""",
    )
    triage_sub = triage_parser.add_subparsers(dest="triage_cmd")

    # triage list
    triage_list_parser = triage_sub.add_parser("list", help="List pending signals for triage")
    triage_list_parser.add_argument(
        "--limit", type=int, default=20,
        help="Maximum signals to show (default: 20)",
    )
    triage_list_parser.add_argument(
        "--status", type=str, default="pending",
        choices=["pending", "queued", "pushed", "rejected"],
        help="Filter by status (default: pending)",
    )
    triage_list_parser.add_argument(
        "--min-confidence", type=float, default=None, dest="min_confidence",
        help="Minimum confidence score filter",
    )
    triage_list_parser.add_argument(
        "--compact", action="store_true", default=True,
        help="Compact mode: single line per signal (default)",
    )
    triage_list_parser.add_argument(
        "--verbose", action="store_true", default=False,
        help="Show more detail per signal",
    )
    triage_list_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # triage approve
    triage_approve_parser = triage_sub.add_parser("approve", help="Approve a signal for push")
    triage_approve_parser.add_argument(
        "signal_id", type=int,
        help="Signal ID to approve",
    )
    triage_approve_parser.add_argument(
        "--reason", type=str, required=True,
        help="Reason for approval (required)",
    )
    triage_approve_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # triage reject
    triage_reject_parser = triage_sub.add_parser("reject", help="Reject a signal")
    triage_reject_parser.add_argument(
        "signal_id", type=int,
        help="Signal ID to reject",
    )
    triage_reject_parser.add_argument(
        "--reason", type=str, required=True,
        help="Reason for rejection (required)",
    )
    triage_reject_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # triage defer
    triage_defer_parser = triage_sub.add_parser("defer", help="Defer a signal for later review")
    triage_defer_parser.add_argument(
        "signal_id", type=int,
        help="Signal ID to defer",
    )
    triage_defer_parser.add_argument(
        "--reason", type=str, required=True,
        help="Reason for deferral (required)",
    )
    triage_defer_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # triage detail
    triage_detail_parser = triage_sub.add_parser("detail", help="Show full intelligence for a signal")
    triage_detail_parser.add_argument(
        "signal_id", type=int,
        help="Signal ID to show detail for",
    )
    triage_detail_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # triage ach
    triage_ach_parser = triage_sub.add_parser("ach", help="Run ACH analysis for a review item")
    triage_ach_parser.add_argument(
        "review_id", type=int,
        help="Review ID to analyze",
    )
    triage_ach_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # -------------------------------------------------------------------------
    # Batch publish commands
    # -------------------------------------------------------------------------
    publish_parser = subparsers.add_parser(
        "publish",
        help="Batch publish workflow (create, preview, commit, abort, list)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""
Git-style batch publish workflow for Notion CRM.

Examples:
  # Create a batch from approved reviews
  python run_pipeline.py publish create --limit 10

  # Preview batch contents
  python run_pipeline.py publish preview batch-20260208-143022-a1b2c3

  # Commit (dry-run first)
  python run_pipeline.py publish commit batch-20260208-143022-a1b2c3 --dry-run

  # Commit for real (requires --yes or interactive confirm)
  python run_pipeline.py publish commit batch-20260208-143022-a1b2c3 --yes

  # Abort a draft batch
  python run_pipeline.py publish abort batch-20260208-143022-a1b2c3 --reason "wrong items"

  # List recent batches
  python run_pipeline.py publish list --status draft
""",
    )
    publish_sub = publish_parser.add_subparsers(dest="publish_cmd")

    # publish create
    publish_create_parser = publish_sub.add_parser("create", help="Create batch from approved reviews")
    publish_create_parser.add_argument(
        "--limit", type=int, default=50,
        help="Max reviews to include (default: 50)",
    )
    publish_create_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # publish preview
    publish_preview_parser = publish_sub.add_parser("preview", help="Preview batch contents")
    publish_preview_parser.add_argument(
        "batch_id", type=str,
        help="Batch ID to preview",
    )
    publish_preview_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # publish commit
    publish_commit_parser = publish_sub.add_parser("commit", help="Commit batch (push to Notion)")
    publish_commit_parser.add_argument(
        "batch_id", type=str,
        help="Batch ID to commit",
    )
    publish_commit_parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview what would be pushed without mutations",
    )
    publish_commit_parser.add_argument(
        "--yes", "-y", action="store_true", default=False,
        help="Skip interactive confirmation",
    )
    publish_commit_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )
    publish_commit_parser.add_argument(
        "--override-reason", type=str, default=None,
        help="Override a non-ready activation gate (logged to audit_events)",
    )

    # publish abort
    publish_abort_parser = publish_sub.add_parser("abort", help="Abort a draft batch")
    publish_abort_parser.add_argument(
        "batch_id", type=str,
        help="Batch ID to abort",
    )
    publish_abort_parser.add_argument(
        "--reason", type=str, default="",
        help="Reason for aborting",
    )
    publish_abort_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # publish list
    publish_list_parser = publish_sub.add_parser("list", help="List recent batches")
    publish_list_parser.add_argument(
        "--limit", type=int, default=20,
        help="Max batches to show (default: 20)",
    )
    publish_list_parser.add_argument(
        "--status", type=str, default=None,
        choices=["draft", "committing", "committed", "committed_with_errors", "aborted"],
        help="Filter by batch status",
    )
    publish_list_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # Activation readiness check
    activation_parser = subparsers.add_parser(
        "activation-check",
        help="Check activation readiness for a given step (1-4)",
    )
    activation_parser.add_argument(
        "--step", type=int, default=1, choices=[1, 2, 3, 4],
        help="Activation step to check (default: 1)",
    )
    activation_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )
    activation_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # -------------------------------------------------------------------------
    # Shadow Status
    # -------------------------------------------------------------------------
    shadow_status_parser = subparsers.add_parser(
        "shadow-status",
        help="Shadow observability — view feature flag state and shadow data volumes",
    )
    shadow_status_parser.add_argument(
        "--days", type=int, default=7,
        help="Look-back period in days (default: 7)",
    )
    shadow_status_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )
    shadow_status_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # -------------------------------------------------------------------------
    # Phase G Check
    # -------------------------------------------------------------------------
    phase_g_parser = subparsers.add_parser(
        "phase-g-check",
        help="Check Phase G entity resolution readiness",
    )
    phase_g_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )
    phase_g_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # -------------------------------------------------------------------------
    # Entity Merge Preview (read-only)
    # -------------------------------------------------------------------------
    merge_preview_parser = subparsers.add_parser(
        "entity-merge-preview",
        help="Preview pending entity merges (read-only, no mutations)",
    )
    merge_preview_parser.add_argument(
        "--limit", type=int, default=10,
        help="Max merge pairs to display (default: 10)",
    )
    merge_preview_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )
    merge_preview_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # -------------------------------------------------------------------------
    # Entity Audit
    # -------------------------------------------------------------------------
    entity_audit_parser = subparsers.add_parser(
        "entity-audit",
        help="Audit recent entity migrations and integrity",
    )
    entity_audit_parser.add_argument(
        "--days", type=int, default=7,
        help="Look-back period in days (default: 7)",
    )
    entity_audit_parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output as JSON",
    )
    entity_audit_parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (overrides env var)",
    )

    # =========================================================================
    # v6.6.2 Canary Phase 0 commands
    # =========================================================================

    from utils.db_path_helper import add_db_path_args

    # canary-preflight
    preflight_parser = subparsers.add_parser(
        "canary-preflight",
        help="Run canary pre-flight checks (schema, env, backup)",
    )
    add_db_path_args(preflight_parser)
    preflight_parser.add_argument(
        "--reports", type=str, default=None,
        help="Directory for report artifacts",
    )
    preflight_parser.add_argument(
        "--report", type=str, default=None,
        help="Path to write JSON report",
    )
    preflight_parser.add_argument(
        "--require-env", action="append", default=[],
        help="Required env var KEY=VALUE (can repeat)",
    )
    preflight_parser.add_argument(
        "--create-backup", action="store_true", default=False,
        help="Create WAL-safe backup before proceeding",
    )
    preflight_parser.add_argument(
        "--backup-dir", type=str, default=None,
        help="Directory for backup file",
    )
    preflight_parser.add_argument(
        "--apply-migrations", action="store_true", default=False,
        help="Apply pending schema migrations",
    )
    preflight_parser.add_argument(
        "--maintenance-window-id", type=str, default=None,
        help="Maintenance window identifier for audit trail",
    )
    preflight_parser.add_argument(
        "--writer-exclusivity-mode", type=str, default="best_effort",
        choices=["none", "best_effort"],
        help="Writer exclusivity check mode (default: best_effort)",
    )

    # backfill-evidence-family
    backfill_ef_parser = subparsers.add_parser(
        "backfill-evidence-family",
        help="Backfill evidence_family column for existing signals",
    )
    add_db_path_args(backfill_ef_parser)
    backfill_ef_group = backfill_ef_parser.add_mutually_exclusive_group(required=True)
    backfill_ef_group.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    backfill_ef_group.add_argument("--commit", action="store_true", help="Apply changes to database")
    backfill_ef_parser.add_argument("--chunk-size", type=int, default=1000, help="Rows per chunk (default: 1000)")
    backfill_ef_parser.add_argument("--report", type=str, default=None, help="Path to write JSON report")
    backfill_ef_parser.add_argument("--rewrite-unknown", action="store_true", help="Also rewrite 'unknown' rows")
    backfill_ef_parser.add_argument("--source-api", type=str, default=None, help="Filter by source_api")
    backfill_ef_parser.add_argument("--signal-type", type=str, default=None, help="Filter by signal_type")
    backfill_ef_parser.add_argument("--baseline-report", type=str, default=None, help="Path to baseline report for delta comparison")
    backfill_ef_parser.add_argument("--baseline-unknown-rate", type=float, default=None, help="Baseline unknown rate for delta gate")
    backfill_ef_parser.add_argument("--abort-on-delta", action="store_true", help="Abort if unknown rate exceeds delta threshold")
    backfill_ef_parser.add_argument("--unknown-delta-max-pp", type=float, default=10.0, help="Max unknown rate delta in percentage points (default: 10.0)")

    # rehydrate-canonical-keys-v2
    rehydrate_parser = subparsers.add_parser(
        "rehydrate-canonical-keys-v2",
        help="Rehydrate canonical_key_v2 column for existing signals",
    )
    add_db_path_args(rehydrate_parser)
    rehydrate_group = rehydrate_parser.add_mutually_exclusive_group(required=True)
    rehydrate_group.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    rehydrate_group.add_argument("--commit", action="store_true", help="Apply changes to database")
    rehydrate_parser.add_argument("--sources", type=str, default="all", help="Comma-separated source_api filter (default: all)")
    rehydrate_parser.add_argument("--chunk-size", type=int, default=1000, help="Rows per chunk (default: 1000)")
    rehydrate_parser.add_argument("--report", type=str, default=None, help="Path to write JSON report")
    rehydrate_parser.add_argument("--max-fanin", type=int, default=10, help="Max signals per canonical_key_v2 (default: 10)")
    rehydrate_parser.add_argument("--audit-sample", type=int, default=100, help="Number of rows to audit sample (default: 100)")
    rehydrate_parser.add_argument("--audit-sample-out", type=str, default=None, help="Path to write audit sample JSON")
    rehydrate_parser.add_argument("--max-collision-rate", type=float, default=None, help="Max collision rate threshold")
    rehydrate_parser.add_argument("--limit", type=int, default=None, help="Max rows to process")

    # convergence-kpi
    convergence_parser = subparsers.add_parser(
        "convergence-kpi",
        help="Compute multi-source convergence KPI metrics",
    )
    add_db_path_args(convergence_parser)
    convergence_parser.add_argument("--days", type=int, default=30, help="Look-back period in days (default: 30)")
    convergence_parser.add_argument("--exclude-unlinked-buzz", action="store_true", default=True, help="Exclude unlinked_buzz synthetic keys (default: true)")
    convergence_parser.add_argument("--no-exclude-unlinked-buzz", action="store_false", dest="exclude_unlinked_buzz", help="Include unlinked_buzz synthetic keys")
    convergence_parser.add_argument("--report", type=str, default=None, help="Path to write JSON report")
    convergence_parser.add_argument("--baseline-report", type=str, default=None, help="Path to baseline report for delta comparison")
    convergence_parser.add_argument("--baseline-kpi-report", type=str, default=None, help="Path to baseline KPI report")
    convergence_parser.add_argument("--abort-on-delta", action="store_true", help="Abort if unknown rate exceeds delta threshold")
    convergence_parser.add_argument("--unknown-delta-max-pp", type=float, default=10.0, help="Max unknown rate delta in pp (default: 10.0)")
    convergence_parser.add_argument("--unlinked-delta-max-pp", type=float, default=10.0, help="Max unlinked rate delta in pp (default: 10.0)")

    # health-json-pure
    health_json_parser = subparsers.add_parser(
        "health-json-pure",
        help="Health check with strict JSON output (no text on stdout)",
    )
    add_db_path_args(health_json_parser)
    health_json_parser.add_argument("--report", type=str, default=None, help="Path to write JSON report")
    health_json_parser.add_argument("--allow-external-failures", action="store_true", help="Record external failures as warnings instead of errors")

    # dns-phase2-guardrails
    dns_guard_parser = subparsers.add_parser(
        "dns-phase2-guardrails",
        help="DNS Phase 2 guardrail checks with artifact emission",
    )
    add_db_path_args(dns_guard_parser)
    dns_guard_parser.add_argument(
        "--report", type=str, default=None,
        help="Path to write JSON report",
    )
    dns_guard_parser.add_argument(
        "--alias-threshold", type=int, default=100,
        help="Max aliases allowed before breach (default: 100)",
    )

    # init-watermark command
    init_watermark_parser = subparsers.add_parser(
        "init-watermark",
        help="Initialize the external DB watermark from the current signal count",
    )
    init_watermark_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database",
    )

    return parser


# =============================================================================
# MAIN
# =============================================================================

async def cmd_schema_validate(args):
    """Validate Notion database schema"""
    connector = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    result = await connector.validate_schema(force_refresh=True)

    if getattr(args, "output_json", False):
        # JSON output
        output = {
            "valid": result.valid,
            "missing_properties": result.missing_properties,
            "missing_optional_properties": result.missing_optional_properties,
            "wrong_property_types": result.wrong_property_types,
            "missing_status_options": result.missing_status_options,
            "missing_stage_options": result.missing_stage_options,
        }
        print(json.dumps(output, indent=2))
    else:
        # Text output with emoji codes and instructions
        print(result)


async def cmd_schema_repair(args):
    """Repair Notion database schema (create missing properties/options)"""
    connector = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    # Parse selective repair list if provided
    repair_properties = None
    if getattr(args, "properties", None):
        repair_properties = [p.strip() for p in args.properties.split(",")]

    # Execute repair
    plan = await connector.repair_schema(
        auto_repair=True,
        dry_run=getattr(args, "dry_run", False),
        repair_properties=repair_properties
    )

    print(plan)


async def cmd_schema_docs(args):
    """Generate schema documentation"""
    connector = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    docs = await connector.generate_schema_docs(
        include_validation=True,
        include_examples=True
    )

    if getattr(args, "output", None):
        # Save to file
        with open(args.output, "w") as f:
            f.write(docs)
        print(f"Schema documentation saved to: {args.output}")
    else:
        # Output to stdout
        print(docs)


async def cmd_import_csv(args):
    """Import signals from CSV files."""
    from importers.openvc_csv import OpenVCImporter
    from importers.pitchbook_csv import PitchBookImporter

    # Parse filters
    sector_filter = None
    if getattr(args, "sectors", None):
        sector_filter = [s.strip() for s in args.sectors.split(",")]

    stage_filter = None
    if getattr(args, "stages", None):
        stage_filter = [s.strip() for s in args.stages.split(",")]

    # Initialize store
    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        if args.source == "openvc":
            importer = OpenVCImporter(store)
            result = await importer.import_csv(
                args.file,
                dry_run=args.dry_run,
                sector_filter=sector_filter,
                stage_filter=stage_filter,
            )
        elif args.source == "pitchbook":
            importer = PitchBookImporter(store)
            result = await importer.import_csv(
                args.file,
                dry_run=args.dry_run,
                sector_filter=sector_filter,
                stage_filter=stage_filter,
            )
        else:
            print(f"Unknown source: {args.source}")
            sys.exit(1)

        # Print results
        print("\n" + "=" * 60)
        print("CSV IMPORT RESULTS")
        print("=" * 60)
        print(f"Source:   {args.source}")
        print(f"File:     {args.file}")
        print(f"Dry Run:  {args.dry_run}")
        if sector_filter:
            print(f"Sectors:  {', '.join(sector_filter)}")
        if stage_filter:
            print(f"Stages:   {', '.join(stage_filter)}")
        print(SECTION_SEP)
        print(f"Imported: {result['imported']}")
        print(f"Skipped:  {result['skipped']}")
        print(f"Errors:   {result['errors']}")
        print("=" * 60)

        if args.dry_run:
            print("\n(Dry run - no changes made)")

    finally:
        await store.close()


async def cmd_corroborate(args):
    """Look up companies in OpenCorporates for corroboration."""
    import json
    from collectors.opencorporates import OpenCorporatesCollector

    # Enforce rate limit
    limit = min(args.limit, 50)

    # Initialize store
    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        # Build query to get companies to look up
        source_filter = ""
        params = []
        if args.source:
            source_filter = "AND source_api = ?"
            params.append(args.source)

        query = f"""
            SELECT DISTINCT company_name, canonical_key, source_api, confidence
            FROM signals
            WHERE company_name IS NOT NULL
              AND company_name != ''
              {source_filter}
            ORDER BY confidence DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = await store._db.execute(query, params)
        rows = await cursor.fetchall()

        print("\n" + BANNER_SEP)
        print("CORROBORATE - OpenCorporates Lookup")
        print(BANNER_SEP)
        print(f"Companies to look up: {len(rows)}")
        print(f"Source filter: {args.source or 'all'}")
        print(f"Dry run: {args.dry_run}")
        print(SECTION_SEP)

        if args.dry_run:
            print("\nCompanies that would be looked up:")
            for row in rows:
                name, key, source, conf = row
                print(f"  {name[:40]:<40} | {source:<12} | conf={conf:.2f}")
            print("\n(Dry run - no API calls made)")
            return

        # Initialize OpenCorporates collector
        collector = OpenCorporatesCollector(store=store)

        found = 0
        not_found = 0
        errors = 0

        print("\nLooking up companies...")
        for row in rows:
            name, existing_key, source, conf = row
            try:
                signal = await collector.lookup_and_corroborate(name, existing_key)
                if signal:
                    # Save the corroboration signal
                    await store.save_signal(
                        signal_type=signal["signal_type"],
                        source_api=signal["source_api"],
                        canonical_key=signal["canonical_key"],
                        company_name=signal["company_name"],
                        confidence=signal["confidence"],
                        raw_data=signal["raw_data"],
                        detected_at=signal["detected_at"],
                    )
                    status = signal["raw_data"].get("status", "Unknown")
                    juris = signal["raw_data"].get("jurisdiction", "?")
                    print(f"  [FOUND] {name[:35]:<35} | {juris:<6} | {status}")
                    found += 1
                else:
                    print(f"  [-----] {name[:35]:<35} | not found")
                    not_found += 1
            except Exception as e:
                print(f"  [ERROR] {name[:35]:<35} | {str(e)[:30]}")
                errors += 1

        print(SECTION_SEP)
        print(f"Found: {found} | Not found: {not_found} | Errors: {errors}")
        print(BANNER_SEP)

        # Close collector
        await collector.close()

    finally:
        await store.close()


async def cmd_goldset_list(args):
    """List gold set companies."""
    from utils.gold_set_manager import GoldSetManager

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        manager = GoldSetManager(store)
        companies = await manager.list_companies(
            category=getattr(args, "category", None),
            limit=getattr(args, "limit", 50),
        )

        print("\n" + BANNER_SEP)
        print("GOLD SET COMPANIES")
        print(BANNER_SEP)
        print(f"{'Canonical Key':<40} | {'Name':<20} | Category")
        print(SECTION_SEP)

        for company in companies:
            print(f"{company.canonical_key:<40} | {company.company_name[:20]:<20} | {company.category}")

        print(SECTION_SEP)
        print(f"Total: {len(companies)} companies")
        print(BANNER_SEP)

    finally:
        await store.close()


async def cmd_goldset_stats(args):
    """Show gold set statistics."""
    from utils.gold_set_manager import GoldSetManager

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        manager = GoldSetManager(store)
        stats = await manager.get_stats()

        print("\n" + BANNER_SEP)
        print("GOLD SET STATISTICS")
        print(BANNER_SEP)
        print(f"Total companies:  {stats.total_companies}")
        print(f"Total labels:     {stats.total_labels}")
        print(f"Investor labels:  {stats.total_investor_labels}")
        print(SECTION_SEP)
        print("By category:")
        for cat, count in sorted(stats.by_category.items()):
            print(f"  {cat:<20}: {count}")
        print(SECTION_SEP)
        print(f"Annotators: {', '.join(stats.annotators) if stats.annotators else 'None'}")
        print(BANNER_SEP)

    finally:
        await store.close()


async def cmd_goldset_export(args):
    """Export gold set."""
    from pathlib import Path
    from utils.gold_set_manager import GoldSetManager

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        manager = GoldSetManager(store)
        output_path = Path(args.output)

        if args.format == "json":
            count = await manager.export_to_json(output_path)
        else:
            count = await manager.export_to_csv(output_path)

        print(f"Exported {count} companies to {output_path}")

    finally:
        await store.close()


async def cmd_goldset_import(args):
    """Import gold set."""
    from pathlib import Path
    from utils.gold_set_manager import GoldSetManager

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        manager = GoldSetManager(store)
        file_path = Path(args.file)

        if file_path.suffix.lower() == ".json":
            count = await manager.import_from_json(file_path)
        else:
            count = await manager.import_from_csv(file_path)

        print(f"Imported {count} companies from {file_path}")

    finally:
        await store.close()


async def cmd_evaluate(args):
    """Run evaluation against gold set."""
    from utils.gold_set_manager import GoldSetManager
    from utils.evaluation_runner import EvaluationRunner
    from utils.drift_detector import DriftDetector

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        gold_set = GoldSetManager(store)
        runner = EvaluationRunner(store, gold_set)

        print("\n" + BANNER_SEP)
        print(f"EVALUATION - {args.type.upper()}")
        print(BANNER_SEP)
        print(f"Gold set version: {args.gold_set_version}")
        print(f"Check drift: {args.check_drift}")
        print(SECTION_SEP)

        results = {}

        if args.type == "full":
            results = await runner.run_full_evaluation(args.gold_set_version)
        elif args.type == "extraction":
            results["extraction"] = await runner.run_extraction_evaluation(
                args.gold_set_version
            )
        elif args.type == "similarity":
            results["similarity"] = await runner.run_similarity_evaluation(
                args.gold_set_version
            )
        elif args.type == "investor_match":
            results["investor_match"] = await runner.run_investor_match_evaluation(
                args.gold_set_version
            )

        # Print results
        for run_type, result in results.items():
            print(f"\n{run_type.upper()} METRICS:")
            print(SECTION_SEP)

            if result.extraction_metrics:
                m = result.extraction_metrics
                print(f"  Precision:       {m.precision:.1%}")
                print(f"  Recall:          {m.recall:.1%}")
                print(f"  F1:              {m.f1:.1%}")
                print(f"  Abstention rate: {m.abstention_rate:.1f}%")
                print(f"  Total samples:   {m.total_samples}")

            if result.similarity_metrics:
                m = result.similarity_metrics
                print(f"  Top-1 recall:   {m.top_1_recall:.1f}%")
                print(f"  Top-5 recall:   {m.top_5_recall:.1f}%")
                print(f"  Top-10 recall:  {m.top_10_recall:.1f}%")
                print(f"  MRR:            {m.mean_reciprocal_rank:.3f}")
                print(f"  Total queries:  {m.total_queries}")

            if result.investor_match_metrics:
                m = result.investor_match_metrics
                print(f"  Precision@5:      {m.precision_at_5:.1%}")
                print(f"  Mean Precision@5: {m.mean_precision_at_5:.1%}")
                print(f"  Total queries:    {m.total_queries}")

        # Check drift if requested
        if args.check_drift and results:
            print("\n" + "-" * 70)
            print("DRIFT DETECTION")
            print(SECTION_SEP)

            slack_notifier = None
            if args.notify_slack:
                try:
                    from utils.slack_notifier import SlackNotifier
                    slack_notifier = SlackNotifier()
                except Exception:
                    pass

            detector = DriftDetector(store, slack_notifier=slack_notifier)

            for run_type, result in results.items():
                drift_result = await detector.check_evaluation_drift(
                    evaluation_run_id=1,  # Simplified for now
                    current_metrics=result.to_dict(),
                    run_type=run_type,
                )

                if drift_result.alerts:
                    print(f"\n{run_type.upper()} drift alerts:")
                    for alert in drift_result.alerts:
                        severity_marker = "[RED]" if alert.severity == "red" else "[YELLOW]"
                        print(f"  {severity_marker} {alert.alert_type}: {alert.message}")
                else:
                    print(f"\n{run_type.upper()}: No drift detected")

            print(f"\nTotal alerts: {sum(r.red_count + r.yellow_count for r in [drift_result])}")

        print("\n" + BANNER_SEP)
        print("Evaluation complete")
        print(BANNER_SEP)

    finally:
        await store.close()


# =============================================================================
# MONITORING COMMANDS
# =============================================================================

async def cmd_monitor_add(args):
    """Add a new watch for a URL."""
    from monitoring.website_monitor import create_watch_for_company
    from profilers.url_profiler import generate_canonical_key

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        canonical_key = args.canonical_key
        if not canonical_key:
            canonical_key = generate_canonical_key(args.url)

        watch = await create_watch_for_company(
            signal_store=store,
            canonical_key=canonical_key,
            url=args.url,
            watch_type=args.type,
            interval_seconds=args.interval,
        )

        print(f"Created watch {watch.id}:")
        print(f"  URL:           {watch.url}")
        print(f"  Canonical key: {watch.canonical_key}")
        print(f"  Type:          {watch.watch_type}")
        print(f"  Interval:      {watch.interval_seconds}s ({watch.interval_seconds // 3600}h)")

    finally:
        await store.close()


async def cmd_monitor_run(args):
    """Run monitoring checks on due watches."""
    from monitoring.website_monitor import WebsiteMonitor
    from utils.monitor_lock import MonitorLock, MonitorLockError

    # Advisory lock to prevent concurrent sweeps (v2.4)
    lock = MonitorLock(args.db_path, ttl_seconds=3600)

    # Handle force-break option
    if getattr(args, 'force_break_lock', False):
        if lock.force_break():
            print("Force-broke existing lock")
        else:
            print("No existing lock to break")
        return

    # Check if already locked
    if lock.is_locked():
        holder = lock.get_holder_info()
        print(f"Monitoring sweep already in progress")
        if holder:
            print(f"  PID: {holder.get('pid')}")
            print(f"  Hostname: {holder.get('hostname')}")
            print(f"  Started: {holder.get('acquired_at')}")
        print("\nUse --force-break-lock to force-break the lock if stale.")
        return

    # Try to acquire lock
    lock_timeout = getattr(args, 'lock_timeout', 30)
    if not lock.acquire(timeout_seconds=lock_timeout):
        print(f"Failed to acquire lock within {lock_timeout}s. Another sweep may be running.")
        return

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        # Optional embedding support
        embedding_store = None
        embedding_generator = None

        if not args.no_embeddings:
            try:
                from storage.embedding_store import EmbeddingStore
                from utils.embedding_generator import EmbeddingGenerator

                embedding_store = EmbeddingStore(args.db_path)
                embedding_generator = EmbeddingGenerator()
            except Exception as e:
                print(f"Warning: Embeddings disabled: {e}")

        monitor = WebsiteMonitor(
            signal_store=store,
            embedding_store=embedding_store,
            embedding_generator=embedding_generator,
        )

        # Determine watch type filter
        watch_type = "portfolio" if getattr(args, 'only_portfolio', False) else None

        results = await monitor.run_due_checks(limit=args.limit, watch_type=watch_type)

        # Print summary
        successful = sum(1 for r in results if r.success)
        skipped = sum(1 for r in results if r.skipped)
        failed = sum(1 for r in results if r.error)
        alerts = sum(1 for r in results if r.alert)

        print(f"\nMonitoring run complete:")
        print(f"  Checked:  {len(results)}")
        print(f"  Success:  {successful}")
        print(f"  Skipped:  {skipped} (unchanged)")
        print(f"  Failed:   {failed}")
        print(f"  Alerts:   {alerts}")

        # Show high severity diffs
        high_severity = [r for r in results if r.diff and r.diff.severity_score >= 0.6]
        if high_severity:
            print(f"\nHigh severity changes:")
            for r in high_severity:
                print(f"  {r.watch.canonical_key}: {r.diff.severity_score:.2f}")

    finally:
        await store.close()
        lock.release()  # Release advisory lock


async def cmd_monitor_status(args):
    """Show monitoring status overview."""
    from monitoring.monitor_store import MonitorStore

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        monitor_store = MonitorStore(store)

        # Get stats
        due_watches = await monitor_store.get_due_watches(limit=1000)
        unacked_alerts = await monitor_store.get_unacked_alerts(limit=100)
        recent_runs = await monitor_store.get_recent_runs(limit=5)

        print("\n" + "=" * 60)
        print("MONITORING STATUS")
        print("=" * 60)

        print(f"\nWatches due: {len(due_watches)}")
        print(f"Unacked alerts: {len(unacked_alerts)}")

        if recent_runs:
            print(f"\nRecent runs:")
            for run in recent_runs[:3]:
                duration = run.get("duration_seconds", 0) or 0
                print(f"  {run['started_at'][:16]}: "
                      f"{run['watches_checked']} checked, "
                      f"{run['high_severity_events']} high sev "
                      f"({duration:.1f}s)")

        if unacked_alerts:
            print(f"\nPending alerts:")
            for alert in unacked_alerts[:5]:
                print(f"  [{alert.id}] {alert.alert_reason}: "
                      f"severity={alert.severity_score:.2f}")

    finally:
        await store.close()


async def cmd_monitor_list(args):
    """List active watches."""
    from monitoring.monitor_store import MonitorStore

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        monitor_store = MonitorStore(store)

        # Get all due watches as a proxy for active watches
        watches = await monitor_store.get_due_watches(limit=args.limit)

        print(f"\nActive watches ({len(watches)}):")
        print(SECTION_SEP)

        for w in watches:
            last_check = w.last_checked_at.isoformat()[:16] if w.last_checked_at else "never"
            print(f"  [{w.id}] {w.canonical_key}")
            print(f"       URL: {w.url}")
            print(f"       Last checked: {last_check}")
            print()

    finally:
        await store.close()


async def cmd_monitor_sync_portfolio(args):
    """Sync portfolio companies to monitoring watches."""
    from pathlib import Path
    from services.portfolio_watch_sync import PortfolioWatchSync

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        sync = PortfolioWatchSync(
            signal_store=store,
            portfolio_path=Path(args.portfolio_path),
        )

        # Sync portfolio
        stats = await sync.sync_portfolio(
            dry_run=args.dry_run,
            deactivate_missing=not args.no_deactivate,
        )

        # Print results
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"\n{prefix}Portfolio sync complete:")
        print(f"  Companies in config: {stats.companies_in_config}")
        print(f"  Watches created:     {stats.watches_created}")
        print(f"  Watches updated:     {stats.watches_updated}")
        print(f"  Watches deactivated: {stats.watches_deactivated}")
        print(f"  Watches adopted:     {stats.watches_adopted}")

        if stats.errors:
            print(f"\nErrors ({len(stats.errors)}):")
            for err in stats.errors[:5]:
                print(f"  - {err}")

    finally:
        await store.close()


async def cmd_monitor_dispatch(args):
    """Send Slack alerts for unnotified monitoring alerts."""
    import os
    import json
    import httpx
    from monitoring.monitor_store import MonitorStore

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        monitor_store = MonitorStore(store)

        # Get unnotified alerts (not acknowledged, not yet sent to Slack)
        cursor = await store._db.execute(
            """
            SELECT
                a.id, a.watch_id, a.diff_id, a.alert_reason, a.severity_score,
                a.acknowledged, a.created_at, a.payload_json,
                w.canonical_key, w.url, w.watch_type
            FROM monitoring_alerts a
            JOIN watches w ON a.watch_id = w.id
            WHERE a.acknowledged = 0
              AND a.slack_notified = 0
            ORDER BY w.watch_type = 'portfolio' DESC, a.severity_score DESC, a.created_at ASC
            LIMIT 50
            """,
        )
        alerts = await cursor.fetchall()

        if not alerts:
            # Send heartbeat summary
            due_count_result = await store._db.execute(
                "SELECT COUNT(*) FROM watches WHERE active = 1 AND watch_type = 'portfolio'"
            )
            portfolio_count = (await due_count_result.fetchone())[0]

            error_count_result = await store._db.execute(
                """
                SELECT COUNT(*) FROM watches
                WHERE active = 1 AND consecutive_failures > 0
                """
            )
            error_count = (await error_count_result.fetchone())[0]

            heartbeat_msg = (
                f"Daily Scan Complete - Portfolio: {portfolio_count} companies checked. "
                f"Alerts: 0. Errors: {error_count}."
            )

            if args.dry_run:
                print(f"\n[DRY RUN] Would send heartbeat: {heartbeat_msg}")
            else:
                webhook_url = os.getenv("SLACK_WEBHOOK_URL")
                if webhook_url:
                    await _send_slack_message(webhook_url, heartbeat_msg, args.run_url)
                    print(f"Sent heartbeat: {heartbeat_msg}")
                else:
                    print(f"No SLACK_WEBHOOK_URL set. Would send: {heartbeat_msg}")

            return

        # Group alerts by company (canonical_key)
        grouped = {}
        for alert in alerts:
            (alert_id, watch_id, diff_id, reason, severity, acked,
             created_at, payload_json, canonical_key, url, watch_type) = alert

            if canonical_key not in grouped:
                grouped[canonical_key] = {
                    "canonical_key": canonical_key,
                    "url": url,
                    "watch_type": watch_type,
                    "alerts": [],
                }
            grouped[canonical_key]["alerts"].append({
                "id": alert_id,
                "reason": reason,
                "severity": severity,
                "created_at": created_at,
            })

        # Build digest message (storm protection: cap at 5 detailed entries)
        portfolio_companies = [g for g in grouped.values() if g["watch_type"] == "portfolio"]
        other_companies = [g for g in grouped.values() if g["watch_type"] != "portfolio"]

        # Portfolio alerts first
        ordered = portfolio_companies + other_companies

        message_lines = [f"*Monitoring Alerts* ({len(alerts)} total)"]

        for i, company in enumerate(ordered[:5]):
            is_portfolio = company["watch_type"] == "portfolio"
            prefix = "[Portfolio] " if is_portfolio else ""
            alert_summary = ", ".join([
                f"{a['reason']} ({a['severity']:.2f})"
                for a in company["alerts"][:2]
            ])
            message_lines.append(
                f"- {prefix}{company['canonical_key']}: {alert_summary}"
            )

        if len(ordered) > 5:
            message_lines.append(f"... and {len(ordered) - 5} more companies")

        message = "\n".join(message_lines)

        if args.dry_run:
            print(f"\n[DRY RUN] Would send to Slack:\n{message}")
            print(f"\nAlerts to mark as notified: {[a[0] for a in alerts]}")
        else:
            webhook_url = os.getenv("SLACK_WEBHOOK_URL")
            if webhook_url:
                await _send_slack_message(webhook_url, message, args.run_url)
                print(f"Sent Slack digest for {len(grouped)} companies")

                # Mark alerts as notified
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat()
                for alert in alerts:
                    await store._db.execute(
                        """
                        UPDATE monitoring_alerts
                        SET slack_notified = 1, slack_notified_at = ?
                        WHERE id = ?
                        """,
                        (now, alert[0])
                    )
                await store._db.commit()
                print(f"Marked {len(alerts)} alerts as notified")
            else:
                print(f"No SLACK_WEBHOOK_URL set. Would send:\n{message}")

    finally:
        await store.close()


async def _send_slack_message(webhook_url: str, message: str, run_url: str = None):
    """Send a message to Slack via webhook."""
    import httpx

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": message,
            }
        }
    ]

    if run_url:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"<{run_url}|View Run>"
                }
            ]
        })

    payload = {
        "blocks": blocks,
        "text": message,  # Fallback for notifications
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()


async def cmd_outbox_drain(args):
    """Drain the outbox queue."""
    from workflows.notion_outbox_worker import NotionOutboxWorker

    store = SignalStore(args.db_path)
    await store.initialize()

    try:
        # Create worker (Notion connector optional for monitoring events)
        notion = None
        if not args.no_notion:
            try:
                from connectors.notion_connector_v2 import NotionConnector
                notion = NotionConnector()
            except Exception as e:
                logger.warning(f"Notion disabled: {e}")

        worker = NotionOutboxWorker(
            signal_store=store,
            notion_connector=notion,
        )

        stats = await worker.drain(limit=args.limit)

        print(f"\nOutbox drain complete:")
        print(f"  Processed:       {stats['processed']}")
        print(f"  Sent:            {stats['sent']}")
        print(f"  Failed:          {stats['failed']}")
        print(f"  Profile updates: {stats.get('profile_updates', 0)}")

    finally:
        await store.close()


async def cmd_shadow_backfill(args):
    """Backfill shadow logs with stratified sampling for v1/v2 comparison.

    Phase 0C: Parity validation on real corpus.
    """
    import json
    import uuid
    from collections import defaultdict
    from datetime import datetime, timedelta

    from storage.signal_store import SignalStore
    from utils.thesis_matcher import ThesisMatcher
    from utils.text_extraction import extract_text

    logger = logging.getLogger(__name__)

    db_path = args.db_path
    dry_run = args.dry_run
    per_bucket = args.per_bucket
    lookback_days = args.lookback_days
    max_candidates = args.max_candidates

    # Generate run_id for this backfill session
    run_id = str(uuid.uuid4())[:8]

    print(f"Shadow Backfill (Phase 0C)")
    print(f"  Run ID:         {run_id}")
    print(f"  DB path:        {db_path}")
    print(f"  Lookback days:  {lookback_days}")
    print(f"  Per bucket:     {per_bucket}")
    print(f"  Max candidates: {max_candidates}")
    print(f"  Dry run:        {dry_run}")
    print()

    store = SignalStore(db_path)
    await store.initialize()

    try:
        # Create matcher in shadow mode with v2 execution ENABLED
        matcher = ThesisMatcher(
            v2_enablement="shadow",
            v2_execution_enabled=True,
        )

        policy_hash = matcher._policy_hash[:8] if matcher._policy_hash else "N/A"
        print(f"ThesisMatcher: v2_enablement=shadow, policy_hash={policy_hash}")
        print()

        # Calculate cutoff date
        cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")

        # Fetch candidates (raw query - read-only, safe)
        async with store.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT id, canonical_key, company_name, source_api, raw_data, created_at, company_id
                FROM signals
                WHERE created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (cutoff_str, max_candidates),
            )
            rows = await cursor.fetchall()

        print(f"Fetched {len(rows)} candidate signals (since {cutoff_str})")

        if not rows:
            print("No signals found in date range.")
            return

        # Group by (source, month) buckets for stratified sampling
        buckets = defaultdict(list)
        for row in rows:
            signal_id, canonical_key, company_name, source_api, raw_data_json, created_at, _company_id = row

            # Parse month from created_at (format: YYYY-MM-DD or ISO timestamp)
            try:
                if "T" in str(created_at):
                    month = str(created_at)[:7]  # "2024-01"
                else:
                    month = str(created_at)[:7]
            except:
                month = "unknown"

            source = source_api or "unknown"
            bucket_key = (source, month)
            buckets[bucket_key].append(row)

        print(f"Found {len(buckets)} (source, month) buckets:")

        # Sort buckets for consistent output
        sorted_buckets = sorted(buckets.keys())

        # Sample from each bucket
        sampled = []
        for bucket_key in sorted_buckets:
            bucket_rows = buckets[bucket_key]
            sample_count = min(per_bucket, len(bucket_rows))
            # Take first N (already sorted by created_at DESC)
            sampled.extend(bucket_rows[:sample_count])
            print(f"  {bucket_key[0]:20} {bucket_key[1]:10} -> {len(bucket_rows):4} signals, sampling {sample_count}")

        print(f"\nTotal sampled: {len(sampled)} signals")
        print()

        if dry_run:
            print("[DRY RUN] Would process these signals. Exiting.")
            return

        # Process sampled signals
        logged = 0
        skipped_no_text = 0
        skipped_no_v2_shadow = 0
        errors = 0

        for row in sampled:
            signal_id, canonical_key, company_name, source_api, raw_data_json, created_at, _company_id = row

            # Parse raw_data
            try:
                raw_data = json.loads(raw_data_json) if raw_data_json else {}
            except (json.JSONDecodeError, TypeError):
                errors += 1
                continue

            # Extract text using source-aware extraction
            text = extract_text(raw_data, source=source_api or "_default")
            if len(text) < 20:
                skipped_no_text += 1
                continue

            # Run thesis matching in shadow mode
            try:
                fit = matcher.score(text, company_name=company_name)
            except Exception as e:
                logger.warning(f"Scoring failed for signal {signal_id}: {e}")
                errors += 1
                continue

            # Check if v2_shadow was generated
            if not fit.trace or not fit.trace.v2_shadow:
                skipped_no_v2_shadow += 1
                continue

            # Build computed_value dict for shadow log
            fit_dict = fit.to_dict()
            computed_value = {
                "keyword_score": fit.score,
                "keyword_category": fit.thesis.value,
                "v2_shadow": fit.trace.v2_shadow,
                "run_id": run_id,
                "fit": fit_dict,
            }

            # Log to shadow_log table
            await store.log_shadow_computation(
                feature_name="thesis_match",
                canonical_key=canonical_key,
                computed_value=computed_value,
                signal_id=signal_id,
            )

            logged += 1

            # Progress logging
            if logged % 50 == 0:
                print(f"  Progress: {logged} shadow logs created...")

        # Summary
        print()
        print(f"Shadow backfill complete:")
        print(f"  Run ID:             {run_id}")
        print(f"  Logged:             {logged}")
        print(f"  Skipped (no text):  {skipped_no_text}")
        print(f"  Skipped (no v2):    {skipped_no_v2_shadow}")
        print(f"  Errors:             {errors}")

    finally:
        await store.close()


# =============================================================================
# TRIAGE COMMANDS
# =============================================================================


async def cmd_triage_list(args):
    """List pending/queued signals in a compact table for triage review.

    Phase 0, Task 0.11: Lets operators quickly scan signals needing review.
    """
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()

    try:
        status_filter = getattr(args, "status", "pending") or "pending"
        min_confidence = getattr(args, "min_confidence", None)
        limit = getattr(args, "limit", 20) or 20
        verbose_mode = getattr(args, "verbose", False)

        query = """
            SELECT s.id, s.company_name, s.canonical_key, s.confidence,
                   s.signal_type, s.source_api, s.raw_data,
                   COALESCE(sp.status, 'pending') as status,
                   s.detected_at, s.company_id,
                   fs.problem_solved_text,
                   fs.customer_archetype,
                   fs.schema_confidence,
                   fs.is_advisory,
                   fs.approach_text,
                   fs.evidence_signal_ids
            FROM signals s
            LEFT JOIN signal_processing sp ON sp.signal_id = s.id
            LEFT JOIN functional_schemas fs
                ON fs.company_id = s.company_id AND fs.is_active = 1
            WHERE COALESCE(sp.status, 'pending') = ?
        """
        params: list = [status_filter]

        if min_confidence is not None:
            query += " AND s.confidence >= ?"
            params.append(min_confidence)

        query += " ORDER BY s.confidence DESC, s.detected_at DESC"
        query += " LIMIT ?"
        params.append(limit)

        cursor = await store._db.execute(query, params)
        rows = await cursor.fetchall()

        # Compute Sim column data (case-law similarity per signal)
        sim_map = {}
        try:
            from intelligence.vectorizer_config import load_latest_metadata, VECTORIZER_DIR
            from utils.corpus_text_builder import build_corpus_text
            meta = load_latest_metadata(VECTORIZER_DIR)
            if meta:
                vec_path = os.path.join(VECTORIZER_DIR, f"case_law_{meta.version}.joblib")
                if os.path.exists(vec_path):
                    from intelligence.case_law_retriever import CaseLawRetriever
                    _retriever = CaseLawRetriever(vectorizer_path=vec_path)
                    pcursor = await store._db.execute(
                        "SELECT * FROM precedents WHERE vectorizer_version = ?",
                        (meta.version,),
                    )
                    pcols = [d[0] for d in pcursor.description]
                    all_precs = [dict(zip(pcols, r)) for r in await pcursor.fetchall()]
                    if all_precs:
                        for r in rows:
                            schema_d = None
                            if r[10]:
                                schema_d = {"problem_solved_text": r[10], "customer_archetype": r[11]}
                            qt = build_corpus_text(r[1] or "", r[6] or "{}", schema_d)
                            cl = _retriever.find_similar(qt, all_precs)
                            if cl.max_similarity_tp > 0 or cl.max_similarity_fp > 0:
                                sim_map[r[0]] = f"{cl.max_similarity_tp:.2f}W/{cl.max_similarity_fp:.2f}L"
        except Exception:
            pass

        if not rows:
            print(f"No signals with status '{status_filter}' found.")
            return

        # Print header
        print(f"\n{'ID':>6}  {'Company':<25}  {'Problem':<40}  {'Archetype':<13}  {'Conf':>5}  {'Sim':<11}  {'Source':<15}  {'Status':<10}")
        print("-" * 138)

        for row in rows:
            sig_id = row[0]
            company = (row[1] or row[2] or "Unknown")[:25]
            confidence = row[3]
            signal_type = row[4] or ""
            source_api = row[5] or ""
            raw_data_str = row[6] or "{}"
            status = row[7]

            # Functional schema columns (indices 10-15)
            problem_text = row[10] or ""
            archetype = row[11] or ""
            is_advisory = row[13]

            if problem_text:
                display_problem = problem_text[:40]
            else:
                # Fallback to raw_data summary
                try:
                    raw_data = json.loads(raw_data_str)
                    display_problem = (
                        raw_data.get("description")
                        or raw_data.get("title")
                        or raw_data.get("name")
                        or signal_type
                    )
                except (json.JSONDecodeError, TypeError):
                    display_problem = signal_type
                display_problem = (display_problem or "")[:40]

            if archetype:
                display_archetype = archetype + "*" if is_advisory else archetype
            else:
                display_archetype = "\u2014"

            source = source_api[:15] if source_api else ""
            sim_display = sim_map.get(sig_id, "\u2014")

            print(f"{sig_id:>6}  {company:<25}  {display_problem:<40}  {display_archetype:<13}  {confidence:>5.2f}  {sim_display:<11}  {source:<15}  {status:<10}")

            if verbose_mode:
                detected_at = row[8] or ""
                approach = row[14] or ""
                evidence = row[15] or ""
                print(f"        Detected: {detected_at}  Type: {signal_type}  Key: {row[2]}")
                if approach:
                    print(f"        Approach: {approach}")
                if evidence:
                    print(f"        Evidence: {evidence}")

        print(f"\nShowing {len(rows)} signal(s) with status '{status_filter}'")

    finally:
        await store.close()


async def _triage_action(args, action_type: str, new_status=None):
    """Shared handler for triage approve/reject/defer actions.

    Writes an audit_log entry and optionally updates signal_processing status.

    Args:
        args: Parsed CLI arguments (signal_id, reason, db_path)
        action_type: The audit action type (e.g., 'triage_approve')
        new_status: New signal_processing status, or None to keep unchanged
    """
    from datetime import datetime, timezone

    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()

    try:
        signal_id = args.signal_id
        reason = args.reason
        now = datetime.now(timezone.utc).isoformat()

        # Verify signal exists
        cursor = await store._db.execute(
            "SELECT id, company_name FROM signals WHERE id = ?",
            (signal_id,),
        )
        signal_row = await cursor.fetchone()
        if not signal_row:
            print(f"ERROR: Signal ID {signal_id} not found")
            sys.exit(1)

        company_name = signal_row[1] or f"Signal #{signal_id}"

        # Insert audit_log entry
        details = json.dumps({"reason": reason})
        await store._db.execute(
            """INSERT INTO audit_log (action_type, entity_type, entity_id, actor, details, created_at)
               VALUES (?, 'signal', ?, 'operator', ?, ?)""",
            (action_type, str(signal_id), details, now),
        )

        # Update or insert signal_processing row (if new_status is set)
        if new_status is not None:
            cursor = await store._db.execute(
                "SELECT id FROM signal_processing WHERE signal_id = ?",
                (signal_id,),
            )
            existing = await cursor.fetchone()

            if existing:
                await store._db.execute(
                    """UPDATE signal_processing
                       SET status = ?, processed_at = ?, metadata = ?
                       WHERE signal_id = ?""",
                    (new_status, now, details, signal_id),
                )
            else:
                await store._db.execute(
                    """INSERT INTO signal_processing
                       (signal_id, status, processed_at, metadata, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (signal_id, new_status, now, details, now, now),
                )

        await store._db.commit()

        action_label = action_type.replace("triage_", "").upper()
        status_msg = f" -> status={new_status}" if new_status else " (status unchanged)"
        print(f"[{action_label}] Signal {signal_id} ({company_name}){status_msg}")
        print(f"  Reason: {reason}")
        print(f"  Audit log entry created at {now}")

    finally:
        await store.close()


async def cmd_triage_approve(args):
    """Approve a signal for push (sets status to 'queued').

    Phase 0, Task 0.11: Operator approves signal, recording reason in audit_log.
    """
    await _triage_action(args, action_type="triage_approve", new_status="queued")


async def cmd_triage_reject(args):
    """Reject a signal (sets status to 'rejected').

    Phase 0, Task 0.11: Operator rejects signal, recording reason in audit_log.
    """
    await _triage_action(args, action_type="triage_reject", new_status="rejected")


async def cmd_triage_defer(args):
    """Defer a signal for later review (status unchanged).

    Phase 0, Task 0.11: Operator defers signal, recording reason in audit_log.
    """
    await _triage_action(args, action_type="triage_defer", new_status=None)


async def cmd_triage_ach(args):
    """Run ACH analysis for a review item.

    Wave 1, Phase B: Builds deterministic ACH matrix, stores result,
    and prints formatted output (hypothesis scores, bull/bear, differentiators).
    """
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()

    try:
        from intelligence.ach_matrix import ACHBuilder, store_ach_analysis, update_ach_narratives
        from intelligence.tribunal import narrate_summary

        # Resolve review → company_id
        cursor = await store._db.execute(
            "SELECT company_id FROM review_items WHERE id = ?",
            (args.review_id,),
        )
        row = await cursor.fetchone()
        if not row:
            print(f"ERROR: Review {args.review_id} not found")
            sys.exit(1)
        company_id = row[0]

        # Build ACH matrix
        builder = ACHBuilder()
        matrix = await builder.build(company_id, store._db)

        # Store
        ach_id = await store_ach_analysis(store._db, matrix, review_id=args.review_id)

        # Narrate
        summary = narrate_summary(matrix)
        await update_ach_narratives(
            store._db, ach_id,
            bull_summary=summary.bull_summary,
            bear_summary=summary.bear_summary,
            differentiator_count=summary.differentiator_count,
        )

        # Print formatted output
        print(f"\n{'='*60}")
        print(f"ACH Analysis — Review #{args.review_id} (company: {company_id})")
        print(f"{'='*60}")
        print(f"Builder: {matrix.builder_version}  Rubric: {matrix.rubric_version}")
        print(f"Evidence: {matrix.evidence_count}/14 available")
        print(f"Hash: {matrix.inputs_hash}")
        print()

        # Hypothesis scores table
        print("Hypothesis Scores:")
        print(f"{'Hypothesis':<35} {'Score':>8}")
        print(f"{'-'*35} {'-'*8}")
        for h in matrix.hypotheses:
            score = matrix.hypothesis_scores.get(h.id, 0)
            marker = " <--" if h.id == matrix.top_hypothesis else ""
            print(f"  {h.id} {h.label:<30} {score:>6.1f}{marker}")
        print()

        # Bull/Bear summaries
        if summary.bull_summary:
            print(f"BULL: {summary.bull_summary}")
        if summary.bear_summary:
            print(f"BEAR: {summary.bear_summary}")
        print()

        # Differentiators
        if summary.differentiators:
            print(f"Differentiators ({summary.differentiator_count}):")
            for d in summary.differentiators:
                print(f"  {d.evidence_id} ({d.evidence_label}): "
                      f"favors {','.join(d.favors)} | opposes {','.join(d.opposes)}")
        print()
    finally:
        await store.close()


async def cmd_triage_detail(args):
    """Show full intelligence detail for a single signal.

    Phase 3, Task 3.10: Displays case-law precedents, exemplar matches,
    and veto status for a single signal.
    """
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()

    try:
        signal_id = args.signal_id

        query = """
            SELECT s.id, s.company_name, s.canonical_key, s.confidence,
                   s.signal_type, s.source_api, s.raw_data,
                   COALESCE(sp.status, 'pending') as status,
                   s.detected_at, s.company_id,
                   fs.problem_solved_text,
                   fs.customer_archetype,
                   fs.schema_confidence,
                   fs.is_advisory,
                   fs.approach_text,
                   tc.category as thesis_category,
                   tc.rationale as thesis_rationale
            FROM signals s
            LEFT JOIN signal_processing sp ON sp.signal_id = s.id
            LEFT JOIN functional_schemas fs
                ON fs.company_id = s.company_id AND fs.is_active = 1
            LEFT JOIN thesis_classifications tc ON tc.signal_id = s.id
            WHERE s.id = ?
        """
        cursor = await store._db.execute(query, (signal_id,))
        row = await cursor.fetchone()

        if not row:
            print(f"Signal #{signal_id} not found.")
            return

        company = row[1] or row[2] or "Unknown"
        confidence = row[3]
        signal_type = row[4] or ""
        source_api = row[5] or ""
        raw_data_str = row[6] or "{}"
        status = row[7]
        detected_at = row[8] or ""
        problem_text = row[10] or ""
        archetype = row[11] or ""
        schema_conf = row[12]
        is_advisory = row[13]
        approach = row[14] or ""
        thesis_cat = row[15] or ""
        thesis_rat = row[16] or ""

        # Header
        print(f"\nCompany: {company} | Confidence: {confidence:.2f} | Status: {status}")
        if detected_at:
            print(f"Detected: {detected_at} | Source: {source_api} | Type: {signal_type}")

        # Functional Schema
        if problem_text:
            advisory_tag = " [advisory]" if is_advisory else ""
            conf_str = f", {schema_conf:.2f} conf" if schema_conf else ""
            print(f'Functional: "{problem_text}" ({archetype or chr(8212)}{conf_str}){advisory_tag}')
            if approach:
                print(f"  Approach: {approach}")
        else:
            print("Functional: [no schema]")

        # Thesis
        if thesis_cat:
            print(f"Thesis: {thesis_cat}" + (f" \u2014 {thesis_rat}" if thesis_rat else ""))

        # Case-law + Exemplar Intelligence
        try:
            from intelligence.vectorizer_config import load_latest_metadata, VECTORIZER_DIR
            from utils.corpus_text_builder import build_corpus_text

            meta = load_latest_metadata(VECTORIZER_DIR)
            if not meta:
                print("\nIntelligence: No vectorizer metadata found")
                return

            vec_path = os.path.join(VECTORIZER_DIR, f"case_law_{meta.version}.joblib")
            if not os.path.exists(vec_path):
                print("\nIntelligence: Vectorizer not built (run scripts/build_case_law_corpus.py)")
                return

            from intelligence.case_law_retriever import CaseLawRetriever
            from intelligence.exemplar_matcher import ExemplarMatcher

            retriever = CaseLawRetriever(vectorizer_path=vec_path)
            matcher = ExemplarMatcher(vectorizer_path=vec_path)

            schema_dict = None
            if problem_text:
                schema_dict = {"problem_solved_text": problem_text, "customer_archetype": archetype}
            query_text = build_corpus_text(company, raw_data_str, schema_dict)

            # Case-law precedents
            try:
                pcursor = await store._db.execute(
                    "SELECT * FROM precedents WHERE vectorizer_version = ?",
                    (meta.version,),
                )
                pcols = [d[0] for d in pcursor.description]
                precedents = [dict(zip(pcols, r)) for r in await pcursor.fetchall()]
            except Exception:
                precedents = []

            print(f"\nCase-law (similar precedents):")
            if precedents:
                cl_result = retriever.find_similar(query_text, precedents)
                if cl_result.wins:
                    for m in cl_result.wins:
                        stale = " [STALE]" if m.is_stale else ""
                        print(f'  WIN:  {m.company_name} ({m.similarity_score:.2f} sim, TP, "{m.label_reason}"){stale}')
                else:
                    print("  No similar wins (TP precedents)")
                if cl_result.losses:
                    for m in cl_result.losses:
                        print(f'  LOSS: {m.company_name} ({m.similarity_score:.2f} sim, FP, "{m.label_reason}")')
                else:
                    print("  No similar losses (FP precedents)")
            else:
                print("  No precedents available")

            # Exemplar matches
            try:
                ecursor = await store._db.execute(
                    "SELECT * FROM thesis_exemplars WHERE vectorizer_version = ? AND is_active = 1",
                    (meta.version,),
                )
                ecols = [d[0] for d in ecursor.description]
                exemplars = [dict(zip(ecols, r)) for r in await ecursor.fetchall()]
            except Exception:
                exemplars = []

            print(f"\nExemplar matches:")
            if exemplars:
                em_result = matcher.match(query_text, exemplars, threshold=0.3)
                if em_result.matches:
                    for em in em_result.matches:
                        print(f'  {em.exemplar_key} ({em.similarity_score:.2f} sim, category: {em.category}) \u2014 "{em.description}"')
                    veto_threshold = float(os.environ.get("EXEMPLAR_VETO_THRESHOLD", "0.75"))
                    if em_result.veto_eligible:
                        print(f"\nVeto: ACTIVE (exemplar similarity {em_result.max_similarity:.2f} >= {veto_threshold} threshold)")
                    else:
                        print(f"\nVeto: inactive (max similarity {em_result.max_similarity:.2f} < {veto_threshold} threshold)")
                else:
                    print("  None above threshold")
            else:
                print("  No exemplar library available")

        except Exception as e:
            print(f"\nIntelligence: Not available ({e})")

    finally:
        await store.close()


async def cmd_export_queue(args):
    """Export pending/queued signals to CSV for offline review.

    Phase 0, Task 0.5: Lets operators review the signal queue offline
    without needing Notion access.

    Schema versions:
        v1 (default): Core 14 columns (signals + processing + schema + thesis).
        v2: Extended 23 columns (v1 + precedents + exemplars + ACH analysis).
             Missing optional tables are handled gracefully (defaults to empty/0).
    """
    import csv
    from datetime import datetime, timedelta, timezone

    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    schema_version = getattr(args, "schema", "v1") or "v1"
    store = SignalStore(db_path)
    await store.initialize()

    try:
        # Discover which optional tables exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        existing_tables = {row[0] for row in await cursor.fetchall()}

        has_functional_schemas = "functional_schemas" in existing_tables
        has_thesis_classifications = "thesis_classifications" in existing_tables
        has_precedents = "precedents" in existing_tables
        has_exemplars = "thesis_exemplars" in existing_tables
        has_ach = "ach_analyses" in existing_tables

        # --- Build SELECT columns ---
        select_parts = [
            "s.id", "s.company_name", "s.canonical_key", "s.confidence",
            "s.signal_type", "s.source_api", "s.detected_at",
            "COALESCE(sp.status, 'pending') as status", "s.company_id",
        ]
        if has_functional_schemas:
            select_parts += [
                "fs.problem_solved_text",
                "fs.customer_archetype",
                "fs.schema_confidence",
                "fs.is_advisory",
            ]
        else:
            select_parts += [
                "NULL as problem_solved_text",
                "NULL as customer_archetype",
                "NULL as schema_confidence",
                "NULL as is_advisory",
            ]
        if has_thesis_classifications:
            select_parts += [
                "tc.category as thesis_category",
                "tc.rationale as thesis_rationale",
            ]
        else:
            select_parts += [
                "NULL as thesis_category",
                "NULL as thesis_rationale",
            ]

        if schema_version == "v2":
            if has_precedents:
                select_parts += [
                    "COALESCE(pcl.tp_count, 0) as precedent_tp_count",
                    "COALESCE(pcl.fp_count, 0) as precedent_fp_count",
                ]
            else:
                select_parts += [
                    "0 as precedent_tp_count",
                    "0 as precedent_fp_count",
                ]
            if has_exemplars:
                select_parts += ["em.exemplar_category", "em.exemplar_key"]
            else:
                select_parts += ["NULL as exemplar_category", "NULL as exemplar_key"]
            if has_ach:
                select_parts += [
                    "ach.top_hypothesis as ach_top_hypothesis",
                    "ach.top_score as ach_top_score",
                    "ach.bull_summary as ach_bull_summary",
                    "ach.bear_summary as ach_bear_summary",
                    "ach.differentiator_count as ach_differentiator_count",
                ]
            else:
                select_parts += [
                    "NULL as ach_top_hypothesis",
                    "NULL as ach_top_score",
                    "NULL as ach_bull_summary",
                    "NULL as ach_bear_summary",
                    "0 as ach_differentiator_count",
                ]

        # --- Build FROM / JOIN ---
        join_parts = ["FROM signals s", "LEFT JOIN signal_processing sp ON sp.signal_id = s.id"]
        if has_functional_schemas:
            join_parts.append(
                "LEFT JOIN functional_schemas fs ON fs.company_id = s.company_id AND fs.is_active = 1"
            )
        if has_thesis_classifications:
            join_parts.append("LEFT JOIN thesis_classifications tc ON tc.signal_id = s.id")
        if schema_version == "v2":
            if has_precedents:
                join_parts.append("""LEFT JOIN (
                    SELECT company_id,
                           SUM(CASE WHEN human_label = 'TP' THEN 1 ELSE 0 END) as tp_count,
                           SUM(CASE WHEN human_label = 'FP' THEN 1 ELSE 0 END) as fp_count
                    FROM precedents
                    GROUP BY company_id
                ) pcl ON pcl.company_id = s.company_id""")
            if has_exemplars:
                join_parts.append("""LEFT JOIN (
                    SELECT canonical_key,
                           category as exemplar_category,
                           exemplar_key
                    FROM thesis_exemplars
                    WHERE is_active = 1
                    GROUP BY canonical_key
                ) em ON em.canonical_key = s.canonical_key""")
            if has_ach:
                join_parts.append("""LEFT JOIN (
                    SELECT company_id, top_hypothesis, top_score, bull_summary,
                           bear_summary, differentiator_count,
                           ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY created_at DESC, id DESC) as rn
                    FROM ach_analyses
                ) ach ON ach.company_id = s.company_id AND ach.rn = 1""")

        # --- Assemble query ---
        query = "SELECT " + ",\n                   ".join(select_parts)
        query += "\n            " + "\n            ".join(join_parts)
        query += "\n            WHERE 1=1\n        "

        params = []
        if getattr(args, "status", None):
            query += " AND COALESCE(sp.status, 'pending') = ?"
            params.append(args.status)

        if getattr(args, "min_confidence", None) is not None:
            query += " AND s.confidence >= ?"
            params.append(args.min_confidence)

        if getattr(args, "days", None) is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
            query += " AND s.detected_at >= ?"
            params.append(cutoff.isoformat())

        query += " ORDER BY s.detected_at DESC"

        cursor = await store._db.execute(query, params)
        rows = await cursor.fetchall()

        # --- Column headers ---
        columns = [
            "signal_id", "company_name", "canonical_key", "confidence",
            "signal_type", "source_api", "detected_at", "status", "company_id",
            "problem_solved", "customer_archetype", "schema_confidence",
            "thesis_category", "thesis_rationale",
        ]
        if schema_version == "v2":
            columns += [
                "precedent_tp", "precedent_fp", "exemplar_category", "exemplar_key",
                "ach_top_hypothesis", "ach_top_score", "ach_bull_summary",
                "ach_bear_summary", "ach_differentiator_count",
            ]

        def _sanitize_csv_field(value):
            """Prefix text fields starting with = + - @ to prevent CSV injection."""
            if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@'):
                return "'" + value
            return value

        # Post-process rows: advisory * suffix, convert NULLs to empty strings
        # Query columns (both v1 and v2): 0-8 core, 9 problem, 10 archetype,
        #   11 schema_conf, 12 is_advisory, 13 thesis_cat, 14 thesis_rat
        # v2 extends: 15 tp_count, 16 fp_count, 17 exemplar_cat, 18 exemplar_key,
        #   19 ach_top_hypothesis, 20 ach_top_score, 21 ach_bull_summary,
        #   22 ach_bear_summary, 23 ach_differentiator_count
        output_rows = []
        for row in rows:
            row = list(row)
            is_advisory = row[12]
            archetype = row[10] or ""
            if is_advisory and archetype:
                archetype = archetype + "*"
            out_row = (
                row[:10]                            # signal_id..problem_solved_text
                + [archetype]                       # customer_archetype (with * if advisory)
                + [row[11] or ""]                   # schema_confidence
                + [row[13] or ""]                   # thesis_category
                + [row[14] or ""]                   # thesis_rationale
            )
            if schema_version == "v2":
                out_row += [
                    row[15] or 0,                   # precedent_tp_count
                    row[16] or 0,                   # precedent_fp_count
                    row[17] or "",                   # exemplar_category
                    row[18] or "",                   # exemplar_key
                    row[19] or "",                   # ach_top_hypothesis
                    row[20] if row[20] is not None else "",  # ach_top_score
                    row[21] or "",                   # ach_bull_summary
                    row[22] or "",                   # ach_bear_summary
                    row[23] if row[23] is not None else 0,  # ach_differentiator_count
                ]
            # CSV injection sanitization (B7)
            output_rows.append([_sanitize_csv_field(v) for v in out_row])

        # Write CSV output
        output_file = getattr(args, "out", None)
        if output_file:
            with open(output_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(output_rows)
            print(f"Exported {len(output_rows)} signals to {output_file}")
        else:
            writer = csv.writer(sys.stdout)
            writer.writerow(columns)
            writer.writerows(output_rows)
            # Print summary to stderr so it doesn't mix with CSV data
            print(f"\n# Exported {len(output_rows)} signals", file=sys.stderr)

    finally:
        await store.close()


async def cmd_push(args):
    """Push specific signals to Notion by ID (manual push).

    Phase 0, Task 0.6: Lets operators manually push selected signals
    to Notion after reviewing them in the export queue.
    """
    from workflows.delivery_policy import (
        assert_notion_write_allowed,
        DeliveryIntent,
        DeliveryPolicyError,
    )

    # Parse signal IDs
    try:
        signal_ids = [int(x.strip()) for x in args.signal_ids.split(",")]
    except ValueError:
        print("ERROR: --signal-ids must be comma-separated integers (e.g., 1,2,3)")
        sys.exit(1)

    if not signal_ids:
        print("ERROR: No signal IDs provided")
        sys.exit(1)

    dry_run = getattr(args, "dry_run", False)

    # Check delivery policy upfront (skip for dry-run)
    if not dry_run:
        try:
            assert_notion_write_allowed(DeliveryIntent.MANUAL_PUSH)
        except DeliveryPolicyError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()

    results = {"pushed": 0, "rejected": 0, "not_found": 0, "error": 0}

    try:
        # Fetch signals by ID
        signals = []
        for sid in signal_ids:
            signal = await store.get_signal(sid)
            if signal is None:
                print(f"  [NOT FOUND] Signal ID {sid} does not exist")
                results["not_found"] += 1
            else:
                signals.append(signal)

        if not signals:
            print("\nNo valid signals found to push.")
            return

        # Group signals by canonical_key
        grouped: dict[str, list] = {}
        for sig in signals:
            grouped.setdefault(sig.canonical_key, []).append(sig)

        print(f"\nFound {len(signals)} signal(s) across {len(grouped)} prospect(s)")
        print()

        if dry_run:
            # Dry-run: just show what would be pushed
            print("[DRY RUN] Would push the following signals:\n")
            for canonical_key, sigs in grouped.items():
                company_name = sigs[0].company_name or "Unknown"
                confidence_max = max(s.confidence for s in sigs)
                print(f"  Prospect: {company_name} ({canonical_key})")
                print(f"    Signals: {len(sigs)}, Max confidence: {confidence_max:.2f}")
                for s in sigs:
                    print(f"      ID={s.id}  type={s.signal_type}  source={s.source_api}  "
                          f"confidence={s.confidence:.2f}")
                print()
            results["pushed"] = len(signals)
        else:
            # Actual push: instantiate NotionPusher and push per canonical key
            from connectors.notion_connector_v2 import NotionConnector
            from verification.verification_gate_v2 import VerificationGate

            notion_api_key = os.environ.get("NOTION_API_KEY")
            notion_db_id = os.environ.get("NOTION_DATABASE_ID")

            if not notion_api_key or not notion_db_id:
                print("ERROR: NOTION_API_KEY and NOTION_DATABASE_ID must be set")
                sys.exit(1)

            connector = NotionConnector(
                api_key=notion_api_key,
                database_id=notion_db_id,
            )

            from workflows.notion_pusher import NotionPusher

            pusher = NotionPusher(
                signal_store=store,
                notion_connector=connector,
                verification_gate=VerificationGate(
                    strict_mode=False,
                    auto_push_status="Source",
                    needs_review_status="Tracking",
                ),
                dry_run=False,
            )

            for canonical_key, sigs in grouped.items():
                company_name = sigs[0].company_name or "Unknown"
                print(f"  Pushing: {company_name} ({canonical_key}) ...")
                try:
                    result = await pusher.process_single_prospect(
                        canonical_key, intent=DeliveryIntent.MANUAL_PUSH,
                        override_hold=getattr(args, "override_hold", False),
                    )
                    if result.error:
                        print(f"    [ERROR] {result.error}")
                        results["error"] += len(sigs)
                    elif result.decision.value == "reject":
                        print(f"    [REJECTED] confidence={result.confidence:.2f}")
                        results["rejected"] += len(sigs)
                    else:
                        print(f"    [PUSHED] decision={result.decision.value} "
                              f"confidence={result.confidence:.2f}")
                        results["pushed"] += len(sigs)
                except Exception as e:
                    print(f"    [ERROR] {e}")
                    results["error"] += len(sigs)

        # Summary
        print("=" * 50)
        print("PUSH SUMMARY")
        print("=" * 50)
        print(f"  Pushed:     {results['pushed']}")
        print(f"  Rejected:   {results['rejected']}")
        print(f"  Not found:  {results['not_found']}")
        print(f"  Errors:     {results['error']}")

    finally:
        await store.close()


async def cmd_ground_truth(args):
    """Export ground truth labels from Notion CRM for evaluation.

    Phase 0C: Data-driven tuning - ground truth export.
    """
    import json

    from connectors.notion_connector_v2 import NotionConnector

    logger = logging.getLogger(__name__)

    out_path = args.out
    positive_only = args.positive_only
    custom_statuses = args.statuses

    # Define label categories
    POSITIVE_STATUSES = [
        "Source",
        "Initial Meeting / Call",
        "Dilligence",
        "Committed",
        "Funded",
        "Tracking",
    ]
    NEGATIVE_STATUSES = ["Passed"]

    # Determine which statuses to export
    if custom_statuses:
        statuses = [s.strip() for s in custom_statuses.split(",")]
    elif positive_only:
        statuses = POSITIVE_STATUSES
    else:
        statuses = POSITIVE_STATUSES + NEGATIVE_STATUSES

    print(f"Ground Truth Export (Phase 0C)")
    print(f"  Output:   {out_path}")
    print(f"  Statuses: {', '.join(statuses)}")
    print()

    # Initialize Notion connector
    notion = NotionConnector(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_DATABASE_ID"],
    )

    # Query all deals with specified statuses
    print("Querying Notion CRM...")
    pages = await notion._query_by_statuses(statuses)
    print(f"Found {len(pages)} deals")

    # Extract relevant fields
    records = []
    for page in pages:
        props = page.get("properties", {})

        company_name = notion._extract_title(props.get("Company Name", {}))
        status = notion._extract_select(props.get("Status", {})) or ""
        sector = notion._extract_select(props.get("Sector", {})) or ""
        description = notion._extract_text(props.get("Short Description", {})) or ""
        discovery_id = notion._extract_text(props.get("Discovery ID", {})) or ""
        canonical_key = notion._extract_text(props.get("Canonical Key", {})) or ""
        website = props.get("Website", {}).get("url", "") or ""

        # Determine ground truth label
        if status in POSITIVE_STATUSES:
            label = "POSITIVE"
        elif status in NEGATIVE_STATUSES:
            label = "NEGATIVE"
        else:
            label = "UNKNOWN"

        records.append({
            "company_name": company_name,
            "status": status,
            "label": label,
            "sector": sector,
            "description": description,
            "discovery_id": discovery_id,
            "canonical_key": canonical_key,
            "website": website,
            "page_id": page["id"],
        })

    # Write to JSONL
    with open(out_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Summary by status
    print()
    print("Export complete:")
    print(f"  Total records: {len(records)}")

    status_counts = {}
    for r in records:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1

    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status:25} {count:4}")

    label_counts = {}
    for r in records:
        label_counts[r["label"]] = label_counts.get(r["label"], 0) + 1

    print()
    print("By label:")
    for label, count in sorted(label_counts.items()):
        print(f"  {label:10} {count:4}")

    print(f"\nWritten to: {out_path}")


# =============================================================================
# BATCH PUBLISH COMMANDS
# =============================================================================

async def cmd_publish_create(args):
    """Create a batch from approved reviews."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        from workflows.batch_publisher import create_batch, BatchError
        result = await create_batch(store, limit=args.limit)

        print(f"Created batch: {result['batch_id']}")
        print(f"Items: {result['item_count']}")
        print()
        for item in result["items"]:
            print(f"  {item['company_id']}  {item['canonical_key']}")
    except BatchError as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        await store.close()


async def cmd_publish_preview(args):
    """Preview batch contents."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        from workflows.batch_publisher import preview_batch, BatchNotFoundError
        preview = await preview_batch(store, args.batch_id)

        print(f"Batch: {preview['batch_id']}")
        print(f"Status: {preview['status']}")
        print(f"Items: {preview['item_count']}  Pushed: {preview['pushed_count']}  Errors: {preview['error_count']}")
        print(f"Created: {preview['created_at']}")
        if preview["committed_at"]:
            print(f"Committed: {preview['committed_at']}")
        print()

        if not preview["items"]:
            print("  (no items)")
        else:
            # Header
            print(f"  {'ID':>4}  {'Status':<12}  {'Company':<30}  {'Canonical Key':<35}  {'Conf':>5}")
            print(f"  {'─'*4}  {'─'*12}  {'─'*30}  {'─'*35}  {'─'*5}")
            for item in preview["items"]:
                name = (item.get("company_name") or "—")[:30]
                conf = f"{item['confidence']:.2f}" if item.get("confidence") else "—"
                print(f"  {item['id']:>4}  {item['status']:<12}  {name:<30}  {item['canonical_key']:<35}  {conf:>5}")
    except BatchNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        await store.close()


async def cmd_publish_commit(args):
    """Commit a batch to Notion."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        from workflows.batch_publisher import (
            ActivationGateError, commit_batch, preview_batch,
            BatchError, BatchNotFoundError, BatchStateError,
        )
        from workflows.delivery_policy import DeliveryPolicyError

        if args.dry_run:
            result = await commit_batch(store, args.batch_id, dry_run=True)
            print(f"Dry run for batch: {result['batch_id']}")
            print(f"Pending items: {result['pending_count']}")
            print("No changes made.")
            return

        # Interactive confirmation unless --yes
        if not args.yes:
            preview = await preview_batch(store, args.batch_id)
            print(f"About to push {preview['item_count']} items to Notion.")
            confirm = input("Proceed? [y/N] ")
            if confirm.lower() != "y":
                print("Aborted.")
                return

        # Real commit needs NotionPusher
        from verification.verification_gate_v2 import VerificationGate
        from workflows.notion_pusher import NotionPusher

        notion_connector = NotionConnector(
            api_key=os.environ["NOTION_API_KEY"],
            database_id=os.environ["NOTION_DATABASE_ID"],
        )
        gate = VerificationGate()
        pusher = NotionPusher(
            signal_store=store,
            notion_connector=notion_connector,
            verification_gate=gate,
        )

        override_reason = getattr(args, "override_reason", None)
        result = await commit_batch(
            store, args.batch_id, pusher=pusher,
            override_reason=override_reason,
        )
        print(f"Batch committed: {result['batch_id']}")
        print(f"Status: {result['final_status']}")
        print(f"Pushed: {result['pushed_count']}  Errors: {result['error_count']}")

    except ActivationGateError as e:
        print(f"Activation gate blocked: {e}")
        sys.exit(1)
    except (BatchError, BatchNotFoundError, BatchStateError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    except DeliveryPolicyError as e:
        print(f"Delivery policy blocked: {e}")
        sys.exit(1)
    finally:
        await store.close()


async def cmd_publish_abort(args):
    """Abort a draft batch."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        from workflows.batch_publisher import abort_batch, BatchNotFoundError, BatchStateError
        result = await abort_batch(store, args.batch_id, reason=args.reason)
        print(f"Batch aborted: {result['batch_id']}")
        print(f"Reviews reverted to approved: {result['reverted_count']}")
    except (BatchNotFoundError, BatchStateError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        await store.close()


async def cmd_publish_list(args):
    """List recent batches."""
    db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        from workflows.batch_publisher import list_batches
        batches = await list_batches(store, status=args.status, limit=args.limit)

        if not batches:
            print("No batches found.")
            return

        print(f"{'Batch ID':<35}  {'Status':<22}  {'Items':>5}  {'Push':>4}  {'Err':>3}  Created")
        print(f"{'─'*35}  {'─'*22}  {'─'*5}  {'─'*4}  {'─'*3}  {'─'*20}")
        for b in batches:
            print(
                f"{b['batch_id']:<35}  {b['status']:<22}  "
                f"{b['item_count']:>5}  {b['pushed_count']:>4}  {b['error_count']:>3}  "
                f"{b['created_at'][:19]}"
            )
    finally:
        await store.close()


# =============================================================================
# HUNTER SUBCOMMANDS
# =============================================================================


async def cmd_hunter_generate(args):
    """Generate hunter queries from patterns or seed file."""
    import json as _json
    from storage.signal_store import SignalStore
    from intelligence.pattern_miner import mine_patterns, ManualSeed
    from intelligence.query_generator import generate_queries
    from storage.hunter_result_store import get_active_negative_keywords

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        seeds = None
        if getattr(args, "bootstrap", None):
            with open(args.bootstrap, "r") as f:
                raw_seeds = _json.load(f)
            seeds = [ManualSeed(**s) for s in raw_seeds]

        templates = await mine_patterns(store, manual_seeds=seeds)
        neg_kws = await get_active_negative_keywords(store)
        queries = generate_queries(templates, neg_kws)

        print(f"Generated {len(queries)} queries from {len(templates)} templates:")
        for q in queries:
            print(f"  [{q.collector}] {q.query_text[:80]}")
    finally:
        await store.close()


async def _hunter_collector_dispatch(collector: str, query_text: str):
    """Dispatch a hunter query to the appropriate collector's search.

    Uses shared company name extractor for HN/news results instead
    of raw titles. Produces canonical keys for all results.

    Args:
        collector: Collector name (github, hacker_news, news_api)
        query_text: Formatted query string

    Returns:
        List of dicts with company_name, source_api, canonical_key,
        confidence, raw_data.
    """
    import httpx
    from utils.company_name_extractor import (
        extract_company_info,
        is_blocked_domain,
    )
    from utils.canonical_keys import normalize_domain as _norm_domain

    if collector == "github":
        token = os.environ.get("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                params={"q": query_text, "sort": "stars", "per_page": 10},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        results = []
        for item in items:
            homepage = item.get("homepage", "") or ""
            results.append({
                "company_name": item.get("full_name", ""),
                "source_api": "github",
                "canonical_key": f"github_repo:{item.get('full_name', '')}",
                "confidence": 0.5,
                "raw_data": {
                    "stars": item.get("stargazers_count", 0),
                    "description": item.get("description", ""),
                    "homepage": homepage,
                    "language": item.get("language", ""),
                },
            })
        return results

    elif collector == "hacker_news":
        from utils.hn_title import strip_hn_prefix, extract_name_from_hn_body

        # Strip "search?query=" prefix from formatted query text
        clean_query = query_text
        if clean_query.startswith("search?query="):
            clean_query = clean_query[len("search?query="):]
        # Strip "&tags=show_hn" suffix (passed as separate param)
        clean_query = clean_query.replace("&tags=show_hn", "").strip()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": clean_query, "tags": "story", "hitsPerPage": 10},
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
        results = []
        for hit in hits:
            title = hit.get("title", "")
            url = hit.get("url", "")

            # Parse HN prefix
            cleaned_title, hn_prefix = strip_hn_prefix(title)
            effective_title = cleaned_title if hn_prefix else title

            # Try HN separator extraction for prefixed titles
            hn_name = None
            if hn_prefix in ("show", "launch", "demo"):
                hn_name = extract_name_from_hn_body(cleaned_title)

            # Always run shared extractor for domain candidates
            info = extract_company_info(effective_title, url=url, mode="url_promote")

            # Best company name: shared extractor result OR HN-parsed name
            company_name = info.company_name or hn_name or ""

            # Canonical key: prefer domain, fall back to name_loc
            canonical_key = ""
            if info.promoted_domain and not is_blocked_domain(info.promoted_domain):
                canonical_key = f"domain:{_norm_domain(info.promoted_domain)}"
            elif company_name:
                canonical_key = f"name_loc:{company_name.lower()}"

            # Invariant: non-empty company_name requires non-empty canonical_key
            if company_name and not canonical_key:
                company_name = ""

            results.append({
                "company_name": company_name,
                "source_api": "hacker_news",
                "canonical_key": canonical_key,
                "confidence": 0.5,
                "raw_data": {
                    "title": title,
                    "url": url,
                    "points": hit.get("points", 0),
                    "num_comments": hit.get("num_comments", 0),
                },
            })
        return results

    elif collector == "news_api":
        gnews_key = os.environ.get("GNEWS_API_KEY", "")
        if not gnews_key:
            return []
        # Strip "search?q=" prefix from formatted query text
        clean_query = query_text
        if clean_query.startswith("search?q="):
            clean_query = clean_query[len("search?q="):]
        if clean_query.startswith("search?query="):
            clean_query = clean_query[len("search?query="):]
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://gnews.io/api/v4/search",
                params={"q": clean_query, "lang": "en", "max": 10, "apikey": gnews_key},
                timeout=30,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
        results = []
        for article in articles:
            title = article.get("title", "")
            description = article.get("description", "")
            article_url = article.get("url", "")
            source_name = article.get("source", {}).get("name", "")

            # Extract company name via shared extractor
            info = extract_company_info(title, description=description, url=article_url, mode="url_promote")
            company_name = info.company_name or ""

            # Canonical key: non-publisher domain or name_loc fallback
            canonical_key = ""
            if info.promoted_domain and not is_blocked_domain(info.promoted_domain):
                canonical_key = f"domain:{_norm_domain(info.promoted_domain)}"
            elif company_name:
                canonical_key = f"name_loc:{company_name.lower()}"

            # Invariant: non-empty company_name requires non-empty canonical_key
            if company_name and not canonical_key:
                company_name = ""

            results.append({
                "company_name": company_name,
                "source_api": "news_api",
                "canonical_key": canonical_key,
                "confidence": 0.5,
                "raw_data": {
                    "title": title,
                    "description": description,
                    "url": article_url,
                    "source": source_name,
                },
            })
        return results

    else:
        return []


async def cmd_hunter_run(args):
    """Execute a hunter run."""
    from storage.signal_store import SignalStore
    from intelligence.pattern_miner import mine_patterns
    from intelligence.query_generator import generate_queries
    from storage.hunter_result_store import get_active_negative_keywords
    from workflows.active_hunter import execute_hunter_run

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        seeds = None
        if getattr(args, "bootstrap", None):
            import json as _json
            from intelligence.pattern_miner import ManualSeed
            with open(args.bootstrap, "r") as f:
                raw_seeds = _json.load(f)
            seeds = [ManualSeed(**s) for s in raw_seeds]

        templates = await mine_patterns(store, manual_seeds=seeds)
        neg_kws = await get_active_negative_keywords(store)
        queries = generate_queries(templates, neg_kws)

        if not queries:
            print("No queries generated. Use 'hunter run --bootstrap seeds.json' to bootstrap.")
            return

        dry_run = getattr(args, "dry_run", False)
        result = await execute_hunter_run(
            store, queries, collector_fn=_hunter_collector_dispatch, dry_run=dry_run,
        )

        print(f"Hunter run: {result.get('run_id', 'N/A')}")
        print(f"  Executed: {result.get('executed', 0)}")
        print(f"  Skipped: {result.get('skipped', 0)}")
        print(f"  Failed: {result.get('failed', 0)}")
        print(f"  Results: {result.get('total_results', 0)} ({result.get('new_results', 0)} new)")
    finally:
        await store.close()


async def cmd_hunter_status(args):
    """Show hunter run status."""
    from storage.signal_store import SignalStore
    from workflows.run_manager import list_runs, RunType

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        runs = await list_runs(store, run_type=RunType.HUNTER.value, limit=10)
        if not runs:
            print("No hunter runs found.")
            return
        for run in runs:
            print(f"  [{run.status.value}] {run.id} — {run.created_at}")
    finally:
        await store.close()


async def cmd_hunter_review(args):
    """List hunter results pending review."""
    from storage.signal_store import SignalStore
    from storage.hunter_result_store import get_results_for_run

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        run_id = getattr(args, "run_id", None)
        status = getattr(args, "status", "pending")
        limit = getattr(args, "limit", 20)

        if not run_id:
            cursor = await store._db.execute(
                "SELECT id FROM run_history WHERE run_type = 'hunter' ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if not row:
                print("No hunter runs found.")
                return
            run_id = row[0]

        results = await get_results_for_run(store, run_id, status=status, limit=limit)
        print(f"Run {run_id}: {len(results)} results (status={status})")
        for r in results:
            known = " [KNOWN]" if r["already_known"] else ""
            score = f" score={r['thesis_fit_score']:.2f}" if r["thesis_fit_score"] else ""
            print(f"  #{r['id']} {r['company_name']}{known}{score} — {r['status']}")
    finally:
        await store.close()


async def cmd_hunter_feedback(args):
    """Provide feedback on a hunter result."""
    from storage.signal_store import SignalStore
    from storage.hunter_result_store import update_result_status

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        await update_result_status(
            store, args.result_id, args.status,
            operator_feedback=getattr(args, "reason", None),
            actor="cli_operator",
        )
        print(f"Result #{args.result_id} marked as '{args.status}'")
    finally:
        await store.close()


async def cmd_hunter_promote(args):
    """Promote a hunter result to the signals table."""
    from storage.signal_store import SignalStore
    from workflows.hunter_promotion import promote_hunter_result

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        result = await promote_hunter_result(store, args.result_id, actor="cli_operator")
        if result.collision:
            print(f"Collision: canonical key already exists as signal #{result.signal_id}")
        elif result.status == "already_promoted":
            print(f"Already promoted as signal #{result.signal_id}")
        else:
            print(f"Promoted to signal #{result.signal_id}")
    finally:
        await store.close()


async def cmd_hunter_budget(args):
    """Show hunter budget status."""
    from storage.signal_store import SignalStore
    from storage.hunter_result_store import get_budget_summary

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    budget_date = getattr(args, "date", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        summary = await get_budget_summary(store, budget_date)
        print(f"Budget for {budget_date}:")
        g = summary.get("global", {})
        if g:
            print(f"  Global: {g.get('cost_units', 0):.1f}/{g.get('cost_cap', 'N/A')} cost units")
        for coll, info in summary.get("collectors", {}).items():
            print(f"  {coll}: {info['queries_executed']}/{info.get('queries_cap', 'N/A')} queries")
        if not g and not summary.get("collectors"):
            print("  No budget activity today.")
    finally:
        await store.close()


# =============================================================================
# DRIFT COMMANDS
# =============================================================================


async def cmd_drift_check(args):
    """Run SPC check (read-only)."""
    import sqlite3
    from monitoring.spc_monitor import SPCMonitor, VALID_SPC_METRICS

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    monitor = SPCMonitor()
    metrics_to_check = getattr(args, "metrics", None) or list(VALID_SPC_METRICS)

    with sqlite3.connect(db_path) as conn:
        for metric in metrics_to_check:
            limits = monitor.compute_control_limits(conn, metric)
            if limits is None:
                print(f"  {metric}: insufficient data")
                continue
            result = monitor.check_metric(conn, metric, limits.mean)
            print(
                f"  {metric}: {result.verdict} "
                f"(mean={limits.mean:.4f}, UCL={limits.ucl:.4f}, "
                f"LCL={limits.lcl:.4f}, method={limits.method})"
            )
            for alert in result.alerts:
                print(f"    ALERT: {alert.message}")

        # Zero-volume check for collector segments
        collector_rows = conn.execute(
            """SELECT DISTINCT segment_key FROM quality_metrics_daily
               WHERE metric_name = 'collector_volume' AND segment_type = 'collector'
                 AND segment_key != ''"""
        ).fetchall()
        for (collector_key,) in collector_rows:
            latest = conn.execute(
                """SELECT value FROM quality_metrics_daily
                   WHERE metric_name = 'collector_volume' AND segment_type = 'collector'
                     AND segment_key = ? AND value IS NOT NULL
                   ORDER BY metric_date DESC LIMIT 1""",
                (collector_key,),
            ).fetchone()
            if latest is not None:
                zero_alert = monitor.check_zero_volume(conn, collector_key, latest[0])
                if zero_alert:
                    print(f"  ZERO-VOLUME [{collector_key}]: {zero_alert.message}")


async def cmd_drift_aggregate(args):
    """Aggregate daily metrics."""
    import sqlite3 as _sqlite3
    from monitoring.daily_aggregator import backfill_daily_metrics
    from workflows.feature_guards import assert_write_enabled, WriteFeature

    assert_write_enabled(WriteFeature.DRIFT_MONITORING)

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    days = getattr(args, "days", 90)
    sync_conn = _sqlite3.connect(str(db_path), timeout=5)
    try:
        sync_conn.execute("PRAGMA busy_timeout=5000")
        result = backfill_daily_metrics(sync_conn, days=days)
        print(f"Aggregated metrics for {result.get('computed', 0)} dates (backfill {days} days).")
    finally:
        sync_conn.close()


async def cmd_drift_alerts(args):
    """List drift alerts."""
    from storage.signal_store import SignalStore

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        status_filter = getattr(args, "status", None)
        limit = getattr(args, "limit", 50)

        query = "SELECT id, alert_type, severity, metric_name, message, status, created_at FROM canary_drift_alerts"
        params = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await store._db.execute(query, params)
        rows = await cursor.fetchall()

        if not rows:
            print("No drift alerts found.")
            return

        print(f"{'ID':>5}  {'Type':<25}  {'Sev':<10}  {'Status':<14}  {'Message'}")
        print("-" * 100)
        for row in rows:
            message = ((row[4] or "")[:60]).encode("ascii", "replace").decode("ascii")
            print(f"{row[0]:>5}  {row[1]:<25}  {row[2]:<10}  {row[5]:<14}  {message}")
    finally:
        await store.close()


async def cmd_drift_ack(args):
    """Acknowledge a drift alert."""
    from storage.signal_store import SignalStore
    from monitoring.alert_escalation import acknowledge_alert
    from workflows.feature_guards import assert_write_enabled, WriteFeature

    assert_write_enabled(WriteFeature.DRIFT_MONITORING)

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        result = await acknowledge_alert(store, args.alert_id, "cli-operator", args.reason)
        if result.success:
            print(f"Alert #{args.alert_id} acknowledged.")
        else:
            print(f"Failed: {result.error}")
    finally:
        await store.close()


async def cmd_drift_snooze(args):
    """Snooze a drift alert."""
    from storage.signal_store import SignalStore
    from monitoring.alert_escalation import snooze_alert
    from workflows.feature_guards import assert_write_enabled, WriteFeature

    assert_write_enabled(WriteFeature.DRIFT_MONITORING)

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        reason = getattr(args, "reason", None) or "Snoozed via CLI"
        result = await snooze_alert(store, args.alert_id, "cli-operator", args.hours, reason)
        if result.success:
            print(f"Alert #{args.alert_id} snoozed for {args.hours}h.")
        else:
            print(f"Failed: {result.error}")
    finally:
        await store.close()


async def cmd_drift_resolve(args):
    """Resolve a drift alert."""
    from storage.signal_store import SignalStore
    from monitoring.alert_escalation import resolve_alert
    from workflows.feature_guards import assert_write_enabled, WriteFeature

    assert_write_enabled(WriteFeature.DRIFT_MONITORING)

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    await store.initialize()
    try:
        result = await resolve_alert(store, args.alert_id, "cli-operator", args.reason)
        if result.success:
            print(f"Alert #{args.alert_id} resolved.")
        else:
            print(f"Failed: {result.error}")
    finally:
        await store.close()


async def cmd_drift_recommend(args):
    """Generate drift recommendations."""
    from storage.signal_store import SignalStore
    from monitoring.drift_recommendations import generate_recommendations

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    days = getattr(args, "days", 7)
    store = SignalStore(db_path)
    await store.initialize()
    try:
        recs = await generate_recommendations(store, lookback_days=days)
        if not recs:
            print("No recommendations at this time.")
            return
        for r in recs:
            print(f"  [{r.priority.upper()}] {r.type}: {r.message}")
            if r.action_template:
                print(f"    Action: {r.action_template}")
    finally:
        await store.close()


async def cmd_drift_gc(args):
    """Delete old metrics and alerts."""
    from storage.signal_store import SignalStore
    from workflows.feature_guards import assert_write_enabled, WriteFeature

    assert_write_enabled(WriteFeature.DRIFT_MONITORING)

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    metrics_days = getattr(args, "metrics_days", 365)
    alerts_days = getattr(args, "alerts_days", 180)
    store = SignalStore(db_path)
    await store.initialize()
    try:
        from datetime import timedelta
        metrics_cutoff = (datetime.now(timezone.utc) - timedelta(days=metrics_days)).strftime("%Y-%m-%d")
        alerts_cutoff = (datetime.now(timezone.utc) - timedelta(days=alerts_days)).isoformat()

        cursor = await store._db.execute("DELETE FROM quality_metrics_daily WHERE metric_date < ?", (metrics_cutoff,))
        metrics_deleted = cursor.rowcount
        cursor = await store._db.execute("DELETE FROM canary_drift_alerts WHERE created_at < ?", (alerts_cutoff,))
        alerts_deleted = cursor.rowcount
        await store._db.commit()

        print(f"GC complete: deleted {metrics_deleted} metric rows (>{metrics_days}d), {alerts_deleted} alerts (>{alerts_days}d).")
    finally:
        await store.close()


async def cmd_drift_export_metrics(args):
    """Export daily metrics to CSV or JSONL."""
    import csv as csv_mod
    import json as json_mod
    from storage.signal_store import SignalStore

    db_path = getattr(args, "db_path", None) or os.environ.get("DISCOVERY_DB_PATH", "signals.db")
    days = getattr(args, "days", 365)
    fmt = getattr(args, "format", "csv")
    out_path = getattr(args, "out", None)

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    store = SignalStore(db_path)
    await store.initialize()
    try:
        cursor = await store._db.execute(
            "SELECT metric_date, metric_name, segment_type, segment_key, value, n, created_at, updated_at "
            "FROM quality_metrics_daily WHERE metric_date >= ? ORDER BY metric_date, metric_name",
            (cutoff,),
        )
        rows = await cursor.fetchall()

        if not rows:
            print("No metrics to export.")
            return

        cols = ["metric_date", "metric_name", "segment_type", "segment_key", "value", "n", "created_at", "updated_at"]
        output_lines = []

        if fmt == "csv":
            import io
            buf = io.StringIO()
            writer = csv_mod.writer(buf)
            writer.writerow(cols)
            for row in rows:
                writer.writerow(row)
            output_lines.append(buf.getvalue())
        else:
            for row in rows:
                output_lines.append(json_mod.dumps(dict(zip(cols, row))))

        content = "\n".join(output_lines) if fmt == "jsonl" else output_lines[0]
        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Exported {len(rows)} rows to {out_path}")
        else:
            print(content)
    finally:
        await store.close()


# =============================================================================
# v6.6.2 CANARY PHASE 0 COMMAND HANDLERS
# =============================================================================


async def cmd_canary_preflight(args):
    """Run canary pre-flight checks: schema, env, backup, migrations."""
    from datetime import datetime, timezone
    from utils.db_path_helper import resolve_db_path
    from utils.report_envelope import create_report, write_report

    started_at = datetime.now(timezone.utc)
    db_path = resolve_db_path(args)
    report_path = getattr(args, "report", None)

    try:
        checks = []
        warnings = []
        errors = []
        metrics = {}

        # Check DB exists
        db_file = Path(db_path)
        if not db_file.exists():
            errors.append(f"Database not found: {db_path}")
        else:
            # Schema version check
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                schema_ver = row[0] if row and row[0] else 0
                metrics["schema_version_pre"] = schema_ver
                metrics["schema_version_expected"] = CURRENT_SCHEMA_VERSION

                if schema_ver < CURRENT_SCHEMA_VERSION:
                    if getattr(args, "apply_migrations", False):
                        warnings.append(
                            f"Schema at v{schema_ver}, will apply migrations to v{CURRENT_SCHEMA_VERSION}"
                        )
                    else:
                        errors.append(
                            f"Schema version {schema_ver} < {CURRENT_SCHEMA_VERSION}. "
                            "Use --apply-migrations to upgrade."
                        )

                # Integrity check
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] != "ok":
                    errors.append(f"Database integrity check failed: {result[0]}")
            except sqlite3.OperationalError as exc:
                errors.append(f"Database error: {exc}")
            finally:
                conn.close()

        # Check required env vars
        for req in getattr(args, "require_env", []):
            if "=" in req:
                key, expected = req.split("=", 1)
                actual = os.environ.get(key)
                if actual is None:
                    errors.append(f"Required env var {key} is not set")
                elif actual != expected:
                    errors.append(f"Env var {key}={actual!r}, expected {expected!r}")
            else:
                if not os.environ.get(req):
                    errors.append(f"Required env var {req} is not set")

        # Apply migrations if requested
        if getattr(args, "apply_migrations", False) and db_file.exists() and not errors:
            store = SignalStore(db_path)
            await store.initialize()
            await store.close()
            # Re-check schema version
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                metrics["schema_version_post"] = row[0] if row and row[0] else 0
            finally:
                conn.close()

        # Writer exclusivity check
        if getattr(args, "writer_exclusivity_mode", "none") == "best_effort" and db_file.exists():
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
                conn.close()
                metrics["writer_exclusivity"] = "ok"
            except sqlite3.OperationalError as exc:
                warnings.append(f"Writer exclusivity check failed: {exc}")
                metrics["writer_exclusivity"] = "failed"

        # Create backup if requested
        if getattr(args, "create_backup", False) and db_file.exists():
            import shutil
            backup_dir = getattr(args, "backup_dir", None) or str(db_file.parent)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            backup_path = Path(backup_dir) / f"{db_file.stem}.backup-{timestamp}{db_file.suffix}"
            shutil.copy2(str(db_file), str(backup_path))
            metrics["backup_path"] = str(backup_path)

        ok = len(errors) == 0
        report = create_report(
            command="canary-preflight", ok=ok, db_path=db_path,
            started_at=started_at, metrics=metrics,
            warnings=warnings, errors=errors,
        )

        if report_path:
            write_report(report, report_path)

        # Output summary
        if ok:
            print(f"Canary preflight: PASS ({len(warnings)} warnings)")
        else:
            print(f"Canary preflight: FAIL ({len(errors)} errors)")
            for e in errors:
                print(f"  ERROR: {e}")

        return 0 if ok else 1

    except Exception as exc:
        report = create_report(
            command="canary-preflight", ok=False, db_path=db_path,
            started_at=started_at, errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"Canary preflight: FATAL ({exc})")
        return 1


async def cmd_backfill_evidence_family(args):
    """Backfill evidence_family column for existing signals."""
    from datetime import datetime, timezone
    from utils.db_ops_ledger import append_db_ops_ledger
    from utils.db_path_helper import resolve_db_path
    from utils.db_tool_errors import DBToolError
    from utils.db_tool_lock import DBToolLock
    from utils.db_tool_preflight import read_sqlite_data_version
    from utils.report_envelope import create_report, write_report

    started_at = datetime.now(timezone.utc)
    db_path = resolve_db_path(args)
    report_path = getattr(args, "report", None)
    dry_run = getattr(args, "dry_run", True)
    commit_mode = not dry_run
    tool_name = "backfill_evidence_family"
    command_name = "backfill-evidence-family"
    lock = None
    preflight_data_version = None

    try:
        preflight_data_version = read_sqlite_data_version(db_path)

        if commit_mode:
            lock = DBToolLock(db_path, tool_name=tool_name)
            if not lock.acquire(timeout_seconds=5):
                holder = lock.get_holder_info()
                error = f"Could not acquire DB tool lock. Holder: {holder}"
                append_db_ops_ledger(
                    tool_name=tool_name,
                    db_path=db_path,
                    action=command_name,
                    status="lock_blocked",
                    details={
                        "holder": holder,
                        "commit": True,
                        "preflight_data_version": preflight_data_version,
                    },
                )
                report = create_report(
                    command=command_name,
                    ok=False,
                    db_path=db_path,
                    started_at=started_at,
                    metrics={
                        "holder": holder,
                        "preflight_data_version": preflight_data_version,
                    },
                    errors=[error],
                )
                if report_path:
                    write_report(report, report_path)
                print(f"Backfill evidence_family: FATAL ({error})")
                return 2

        from scripts.backfill_evidence_family import run as run_backfill

        result = await run_backfill(
            db_path=db_path,
            dry_run=dry_run,
            chunk_size=getattr(args, "chunk_size", 1000),
            rewrite_unknown=getattr(args, "rewrite_unknown", False),
            source_api=getattr(args, "source_api", None),
            signal_type=getattr(args, "signal_type", None),
            baseline_unknown_rate=getattr(args, "baseline_unknown_rate", None),
            unknown_delta_max_pp=getattr(args, "unknown_delta_max_pp", 10.0),
        )
        result = {
            **result,
            "preflight_data_version": preflight_data_version,
        }

        ok = not result.get("delta_exceeded", False)
        errors = ["Unknown-rate delta exceeded"] if not ok else []
        report = create_report(
            command=command_name, ok=ok, db_path=db_path,
            started_at=started_at, metrics=result, errors=errors,
        )
        if report_path:
            write_report(report, report_path)

        if commit_mode:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=db_path,
                action=command_name,
                status="success" if ok else "error",
                details={
                    **result,
                    **({"error": errors[0]} if errors else {}),
                },
            )

        mode = "DRY RUN" if dry_run else "COMMIT"
        print(f"Backfill evidence_family [{mode}]: {result['rows_updated']}/{result['rows_scanned']} rows")
        print(f"  Unknown rate: {result['unknown_rate']}%")
        return 0 if ok else 1

    except DBToolError as exc:
        details = {
            **exc.partial_evidence,
            "preflight_data_version": preflight_data_version,
            "error": str(exc),
        }
        if commit_mode:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=db_path,
                action=command_name,
                status="error",
                details=details,
            )
        report = create_report(
            command=command_name, ok=False, db_path=db_path,
            started_at=started_at,
            metrics={
                **exc.partial_evidence,
                "preflight_data_version": preflight_data_version,
            },
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"Backfill evidence_family: FATAL ({exc})")
        return 1
    except Exception as exc:
        if commit_mode:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=db_path,
                action=command_name,
                status="error",
                details={
                    "preflight_data_version": preflight_data_version,
                    "error": str(exc),
                },
            )
        report = create_report(
            command=command_name, ok=False, db_path=db_path,
            started_at=started_at,
            metrics={"preflight_data_version": preflight_data_version},
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"Backfill evidence_family: FATAL ({exc})")
        return 1
    finally:
        if lock is not None:
            lock.release()


async def cmd_rehydrate_canonical_keys_v2(args):
    """Rehydrate canonical_key_v2 column for existing signals."""
    from datetime import datetime, timezone
    from utils.db_ops_ledger import append_db_ops_ledger
    from utils.db_path_helper import resolve_db_path
    from utils.db_tool_errors import DBToolError
    from utils.db_tool_lock import DBToolLock
    from utils.db_tool_preflight import read_sqlite_data_version
    from utils.report_envelope import create_report, write_report

    started_at = datetime.now(timezone.utc)
    db_path = resolve_db_path(args)
    report_path = getattr(args, "report", None)
    dry_run = getattr(args, "dry_run", True)
    commit_mode = not dry_run
    tool_name = "rehydrate_canonical_keys_v2"
    command_name = "rehydrate-canonical-keys-v2"
    lock = None
    preflight_data_version = None

    try:
        preflight_data_version = read_sqlite_data_version(db_path)

        if commit_mode:
            lock = DBToolLock(db_path, tool_name=tool_name)
            if not lock.acquire(timeout_seconds=5):
                holder = lock.get_holder_info()
                error = f"Could not acquire DB tool lock. Holder: {holder}"
                append_db_ops_ledger(
                    tool_name=tool_name,
                    db_path=db_path,
                    action=command_name,
                    status="lock_blocked",
                    details={
                        "holder": holder,
                        "commit": True,
                        "preflight_data_version": preflight_data_version,
                    },
                )
                report = create_report(
                    command=command_name,
                    ok=False,
                    db_path=db_path,
                    started_at=started_at,
                    metrics={
                        "holder": holder,
                        "preflight_data_version": preflight_data_version,
                    },
                    errors=[error],
                )
                if report_path:
                    write_report(report, report_path)
                print(f"Rehydrate canonical_key_v2: FATAL ({error})")
                return 2

        from scripts.rehydrate_canonical_keys_v2 import run as run_rehydrate

        result = await run_rehydrate(
            db_path=db_path,
            dry_run=dry_run,
            chunk_size=getattr(args, "chunk_size", 1000),
            sources=getattr(args, "sources", "all"),
            max_fanin=getattr(args, "max_fanin", 10),
            audit_sample=getattr(args, "audit_sample", 100),
            audit_sample_out=getattr(args, "audit_sample_out", None),
            max_collision_rate=getattr(args, "max_collision_rate", None),
            limit=getattr(args, "limit", None),
        )
        result = {
            **result,
            "preflight_data_version": preflight_data_version,
        }

        ok = len(result.get("fanin_violations", [])) == 0
        errors = ["Fan-in violations detected"] if not ok else []
        report = create_report(
            command=command_name, ok=ok, db_path=db_path,
            started_at=started_at, metrics=result, errors=errors,
        )
        if report_path:
            write_report(report, report_path)

        if commit_mode:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=db_path,
                action=command_name,
                status="success" if ok else "error",
                details={
                    **result,
                    **({"error": errors[0]} if errors else {}),
                },
            )

        mode = "DRY RUN" if dry_run else "COMMIT"
        print(f"Rehydrate canonical_key_v2 [{mode}]: {result['rows_updated']}/{result['rows_scanned']} rows")
        print(f"  Null v2 rate: {result['null_v2_rate']}%")
        if result.get("fanin_violations"):
            print(f"  Fan-in violations: {len(result['fanin_violations'])}")
        return 0 if ok else 1

    except DBToolError as exc:
        details = {
            **exc.partial_evidence,
            "preflight_data_version": preflight_data_version,
            "error": str(exc),
        }
        if commit_mode:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=db_path,
                action=command_name,
                status="error",
                details=details,
            )
        report = create_report(
            command=command_name, ok=False, db_path=db_path,
            started_at=started_at,
            metrics={
                **exc.partial_evidence,
                "preflight_data_version": preflight_data_version,
            },
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"Rehydrate canonical_key_v2: FATAL ({exc})")
        return 1
    except Exception as exc:
        if commit_mode:
            append_db_ops_ledger(
                tool_name=tool_name,
                db_path=db_path,
                action=command_name,
                status="error",
                details={
                    "preflight_data_version": preflight_data_version,
                    "error": str(exc),
                },
            )
        report = create_report(
            command=command_name, ok=False, db_path=db_path,
            started_at=started_at,
            metrics={"preflight_data_version": preflight_data_version},
            errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"Rehydrate canonical_key_v2: FATAL ({exc})")
        return 1
    finally:
        if lock is not None:
            lock.release()


async def cmd_convergence_kpi(args):
    """Compute multi-source convergence KPI metrics."""
    from datetime import datetime, timezone
    from utils.db_path_helper import resolve_db_path
    from utils.report_envelope import create_report, write_report

    started_at = datetime.now(timezone.utc)
    db_path = resolve_db_path(args)
    report_path = getattr(args, "report", None)

    try:
        from scripts.convergence_kpi import run as run_kpi

        result = await run_kpi(
            db_path=db_path,
            days=getattr(args, "days", 30),
            exclude_unlinked_buzz=getattr(args, "exclude_unlinked_buzz", True),
            unknown_delta_max_pp=getattr(args, "unknown_delta_max_pp", 10.0),
            unlinked_delta_max_pp=getattr(args, "unlinked_delta_max_pp", 10.0),
        )

        if not result.get("ok", False):
            error_msg = result.get("error", "Unknown error")
            report = create_report(
                command="convergence-kpi", ok=False, db_path=db_path,
                started_at=started_at, metrics=result, errors=[error_msg],
            )
            if report_path:
                write_report(report, report_path)
            print(f"Convergence KPI: FAIL ({error_msg})")
            return 1

        report = create_report(
            command="convergence-kpi", ok=True, db_path=db_path,
            started_at=started_at, metrics=result,
        )
        if report_path:
            write_report(report, report_path)

        print(f"Convergence KPI ({result['days']}d window):")
        print(f"  Keys with 2+ families: {result['keys_with_2plus_families']}")
        print(f"  Keys with 2+ source APIs: {result['keys_with_2plus_source_apis']}")
        print(f"  Unknown family rate: {result['unknown_family_rate']}%")
        print(f"  Unlinked buzz rate: {result['unlinked_buzz_rate']}%")
        return 0

    except Exception as exc:
        report = create_report(
            command="convergence-kpi", ok=False, db_path=db_path,
            started_at=started_at, errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(f"Convergence KPI: FATAL ({exc})")
        return 1


async def cmd_health_json_pure(args):
    """Health check with strict JSON output (no non-JSON text on stdout)."""
    from datetime import datetime, timezone
    from utils.db_path_helper import resolve_db_path
    from utils.report_envelope import create_report, write_report

    from services.readiness import CheckResult, CheckScope, CheckStatus, ReadinessReport

    started_at = datetime.now(timezone.utc)
    db_path = resolve_db_path(args)
    report_path = getattr(args, "report", None)
    allow_external = getattr(args, "allow_external_failures", False)
    allow_external = allow_external if isinstance(allow_external, bool) else False

    try:
        check_results: list[CheckResult] = []
        warnings_list = []
        errors_list = []
        metrics = {}

        # DB check
        db_file = Path(db_path)
        if not db_file.exists():
            errors_list.append(f"Database not found: {db_path}")
            check_results.append(CheckResult("Database", CheckScope.CORE, CheckStatus.FAIL, f"Database not found: {db_path}"))
        else:
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] == "ok":
                    check_results.append(CheckResult("Database", CheckScope.CORE, CheckStatus.PASS, None))
                else:
                    errors_list.append(f"Database integrity: {result[0]}")
                    check_results.append(CheckResult("Database", CheckScope.CORE, CheckStatus.FAIL, result[0]))

                # Schema version
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                schema_ver = row[0] if row and row[0] else 0
                metrics["schema_version"] = schema_ver
                check_results.append(
                    CheckResult(
                        "Schema Version",
                        CheckScope.CORE,
                        CheckStatus.PASS,
                        f"v{schema_ver}",
                        details={"version": schema_ver},
                    )
                )

                # Signal count
                sig_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
                metrics["signal_count"] = sig_count
            except sqlite3.OperationalError as exc:
                errors_list.append(f"Database error: {exc}")
                check_results.append(CheckResult("Database", CheckScope.CORE, CheckStatus.FAIL, str(exc)))
            finally:
                conn.close()

        # External API checks (suppressed output)
        for api_name, check_fn_name in [
            ("github", "check_github_api"),
            ("sec_edgar", "check_sec_edgar_api"),
        ]:
            try:
                check_fn = globals().get(check_fn_name)
                if check_fn:
                    ok, msg = await check_fn()
                    if ok:
                        check_results.append(CheckResult(api_name, CheckScope.EXTERNAL, CheckStatus.PASS, None))
                    else:
                        if allow_external:
                            warnings_list.append(f"{api_name}: {msg}")
                            check_results.append(CheckResult(api_name, CheckScope.EXTERNAL, CheckStatus.WARN, msg))
                        else:
                            errors_list.append(f"{api_name}: {msg}")
                            check_results.append(CheckResult(api_name, CheckScope.EXTERNAL, CheckStatus.FAIL, msg))
            except Exception as exc:
                if allow_external:
                    warnings_list.append(f"{api_name}: {exc}")
                    check_results.append(CheckResult(api_name, CheckScope.EXTERNAL, CheckStatus.WARN, str(exc)))
                else:
                    errors_list.append(f"{api_name}: {exc}")
                    check_results.append(CheckResult(api_name, CheckScope.EXTERNAL, CheckStatus.FAIL, str(exc)))

        readiness = ReadinessReport(check_results)
        ok = readiness.overall_status != "UNHEALTHY"
        # Keep checks as a metric for contract continuity.
        metrics["checks"] = [c.to_dict() for c in check_results]
        metrics["core_status"] = readiness.core_status
        metrics["integration_status"] = readiness.integration_status
        metrics["overall_status"] = readiness.overall_status

        report = create_report(
            command="health-json-pure", ok=ok, db_path=db_path,
            started_at=started_at, metrics=metrics,
            warnings=warnings_list, errors=errors_list,
        )

        if report_path:
            write_report(report, report_path)

        # Strict JSON to stdout — nothing else
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    except Exception as exc:
        report = create_report(
            command="health-json-pure", ok=False, db_path=db_path,
            started_at=started_at, errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        # Still output JSON even on fatal
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1


async def cmd_dns_phase2_guardrails(args):
    """DNS Phase 2 guardrail checks with artifact emission."""
    from datetime import datetime, timezone
    from utils.db_path_helper import resolve_db_path
    from utils.report_envelope import create_report, write_report
    import sqlite3

    started_at = datetime.now(timezone.utc)
    db_path = resolve_db_path(args)
    report_path = getattr(args, "report", None)
    alias_threshold = getattr(args, "alias_threshold", 100)

    try:
        warnings_list = []
        errors_list = []
        metrics = {}

        db_file = Path(db_path)
        if not db_file.exists():
            errors_list.append(f"Database not found: {db_path}")
        else:
            conn = sqlite3.connect(db_path)
            try:
                # Check if dns_promotion_aliases table exists
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dns_promotion_aliases'"
                ).fetchone()
                table_exists = row is not None
                metrics["table_exists"] = table_exists

                if not table_exists:
                    warnings_list.append("dns_promotion_aliases table not found (pre-v44 schema)")
                    metrics["alias_count"] = 0
                    metrics["enabled_count"] = 0
                    metrics["orphan_count"] = 0
                else:
                    # Alias count
                    alias_count = conn.execute(
                        "SELECT COUNT(*) FROM dns_promotion_aliases"
                    ).fetchone()[0]
                    metrics["alias_count"] = alias_count

                    # Enabled count
                    enabled_count = conn.execute(
                        "SELECT COUNT(*) FROM dns_promotion_aliases WHERE enabled = 1"
                    ).fetchone()[0]
                    metrics["enabled_count"] = enabled_count

                    # Orphan aliases (target_key not in signals)
                    orphan_count = conn.execute(
                        "SELECT COUNT(*) FROM dns_promotion_aliases a "
                        "WHERE a.enabled = 1 AND NOT EXISTS ("
                        "  SELECT 1 FROM signals s WHERE s.canonical_key = a.target_key"
                        ")"
                    ).fetchone()[0]
                    metrics["orphan_count"] = orphan_count

                    if orphan_count > 0:
                        warnings_list.append(
                            f"{orphan_count} orphan alias(es) point to keys not in signals"
                        )

                    # Enabled ratio
                    if alias_count > 0:
                        metrics["enabled_ratio"] = round(enabled_count / alias_count, 4)
                    else:
                        metrics["enabled_ratio"] = None

                    # Threshold breach check
                    if enabled_count > alias_threshold:
                        override = os.environ.get("DNS_PHASE2_GUARDRAILS_OVERRIDE", "").lower()
                        if override in ("1", "true", "yes"):
                            warnings_list.append(
                                f"Alias threshold breached ({enabled_count} > {alias_threshold}) "
                                f"but DNS_PHASE2_GUARDRAILS_OVERRIDE is set"
                            )
                        else:
                            errors_list.append(
                                f"Alias threshold breached: {enabled_count} enabled aliases "
                                f"exceeds threshold of {alias_threshold}"
                            )

            except sqlite3.OperationalError as exc:
                errors_list.append(f"Database error: {exc}")
            finally:
                conn.close()

        ok = len(errors_list) == 0
        report = create_report(
            command="dns-phase2-guardrails", ok=ok, db_path=db_path,
            started_at=started_at, metrics=metrics,
            warnings=warnings_list, errors=errors_list,
        )

        if report_path:
            write_report(report, report_path)

        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if ok else 1

    except Exception as exc:
        report = create_report(
            command="dns-phase2-guardrails", ok=False, db_path=db_path,
            started_at=started_at, errors=[str(exc)],
        )
        if report_path:
            write_report(report, report_path)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1


async def main():
    """Main entry point"""

    parser = create_parser()
    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Config validation
    from utils.config_validator import validate_config, print_config_report
    config_issues = validate_config()
    has_errors = print_config_report(config_issues)
    if os.getenv("STRICT_CONFIG_VALIDATION", "false").lower() == "true" and has_errors:
        sys.exit(1)

    # Check for command
    if not args.command:
        parser.print_help()
        sys.exit(1)

    _enforce_signal_count_guard(args)

    # Dispatch to command handler
    try:
        exit_code = 0
        if args.command == "full":
            await cmd_full(args)
        elif args.command == "collect":
            await cmd_collect(args)
        elif args.command == "process":
            await cmd_process(args)
        elif args.command == "sync":
            await cmd_sync(args)
        elif args.command == "stats":
            await cmd_stats(args)
        elif args.command == "health":
            exit_code = await cmd_health(args)
        elif args.command == "step3b-readiness":
            exit_code = await cmd_step3b_readiness(args)
        elif args.command == "metrics":
            await cmd_metrics(args)
        elif args.command == "embeddings":
            await cmd_embeddings(args)
        elif args.command == "pipeline":
            # Handle pipeline subcommands
            db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
            if args.pipeline_cmd == "status":
                await cmd_pipeline_status(db_path=db_path)
            elif args.pipeline_cmd == "qualified":
                await cmd_pipeline_qualified(db_path=db_path, limit=args.limit)
            elif args.pipeline_cmd == "push":
                await cmd_pipeline_push(
                    db_path=db_path,
                    confirm=args.confirm,
                    dry_run=args.dry_run,
                )
            elif args.pipeline_cmd == "claims":
                await cmd_pipeline_claims(
                    entity_id=args.entity_id,
                    db_path=db_path,
                    show_history=args.history,
                    at_time=args.at_time,
                    predicate=args.predicate,
                )
            elif args.pipeline_cmd == "entities":
                await cmd_pipeline_entities(
                    db_path=db_path,
                    limit=args.limit,
                )
            else:
                print("Pipeline command requires a subcommand (status, qualified, push, claims, entities)")
                sys.exit(1)
        elif args.command == "schema":
            # Handle schema subcommands
            if hasattr(args, "schema_command"):
                if args.schema_command == "validate":
                    await cmd_schema_validate(args)
                elif args.schema_command == "repair":
                    await cmd_schema_repair(args)
                elif args.schema_command == "docs":
                    await cmd_schema_docs(args)
                else:
                    print(f"Unknown schema command: {args.schema_command}")
                    sys.exit(1)
            else:
                print("Schema command requires a subcommand (validate, repair, docs)")
                sys.exit(1)
        elif args.command == "import-emails":
            await cmd_import_emails(args)
        elif args.command == "sync-lps":
            await cmd_sync_lps(args)
        elif args.command == "relationship-health":
            await cmd_relationship_health(args)
        elif args.command == "warm-intros":
            await cmd_warm_intros(args)
        elif args.command == "import-csv":
            await cmd_import_csv(args)
        elif args.command == "corroborate":
            await cmd_corroborate(args)
        elif args.command == "gold-set":
            # Handle gold-set subcommands
            if hasattr(args, "goldset_cmd") and args.goldset_cmd:
                if args.goldset_cmd == "list":
                    await cmd_goldset_list(args)
                elif args.goldset_cmd == "stats":
                    await cmd_goldset_stats(args)
                elif args.goldset_cmd == "export":
                    await cmd_goldset_export(args)
                elif args.goldset_cmd == "import":
                    await cmd_goldset_import(args)
                else:
                    print(f"Unknown gold-set command: {args.goldset_cmd}")
                    sys.exit(1)
            else:
                print("Gold-set command requires a subcommand (list, stats, export, import)")
                sys.exit(1)
        elif args.command == "evaluate":
            await cmd_evaluate(args)
        elif args.command == "monitor":
            # Handle monitor subcommands
            if hasattr(args, "monitor_cmd") and args.monitor_cmd:
                if args.monitor_cmd == "add":
                    await cmd_monitor_add(args)
                elif args.monitor_cmd == "run":
                    await cmd_monitor_run(args)
                elif args.monitor_cmd == "status":
                    await cmd_monitor_status(args)
                elif args.monitor_cmd == "list":
                    await cmd_monitor_list(args)
                elif args.monitor_cmd == "sync-portfolio":
                    await cmd_monitor_sync_portfolio(args)
                elif args.monitor_cmd == "dispatch":
                    await cmd_monitor_dispatch(args)
                else:
                    print(f"Unknown monitor command: {args.monitor_cmd}")
                    sys.exit(1)
            else:
                print("Monitor command requires a subcommand (add, run, status, list)")
                sys.exit(1)
        elif args.command == "eval":
            # Handle eval subcommands
            if hasattr(args, "eval_cmd") and args.eval_cmd:
                if args.eval_cmd == "export":
                    await cmd_eval_export(args)
                elif args.eval_cmd == "run":
                    # Convert no_save flag to save flag
                    args.save = not getattr(args, "no_save", False)
                    await cmd_eval_run(args)
                elif args.eval_cmd == "results":
                    await cmd_eval_results(args)
                else:
                    print(f"Unknown eval command: {args.eval_cmd}")
                    sys.exit(1)
            else:
                print("Eval command requires a subcommand (export, run, results)")
                sys.exit(1)
        elif args.command == "outbox":
            # Handle outbox subcommands
            if hasattr(args, "outbox_cmd") and args.outbox_cmd:
                if args.outbox_cmd == "drain":
                    await cmd_outbox_drain(args)
                else:
                    print(f"Unknown outbox command: {args.outbox_cmd}")
                    sys.exit(1)
            else:
                print("Outbox command requires a subcommand (drain)")
                sys.exit(1)
        elif args.command == "shadow-backfill":
            await cmd_shadow_backfill(args)
        elif args.command == "ground-truth":
            await cmd_ground_truth(args)
        elif args.command == "export-queue":
            await cmd_export_queue(args)
        elif args.command == "push":
            await cmd_push(args)
        elif args.command == "triage":
            # Handle triage subcommands
            if hasattr(args, "triage_cmd") and args.triage_cmd:
                if args.triage_cmd == "list":
                    await cmd_triage_list(args)
                elif args.triage_cmd == "approve":
                    await cmd_triage_approve(args)
                elif args.triage_cmd == "reject":
                    await cmd_triage_reject(args)
                elif args.triage_cmd == "defer":
                    await cmd_triage_defer(args)
                elif args.triage_cmd == "detail":
                    await cmd_triage_detail(args)
                elif args.triage_cmd == "ach":
                    await cmd_triage_ach(args)
                else:
                    print(f"Unknown triage command: {args.triage_cmd}")
                    sys.exit(1)
            else:
                print("Triage command requires a subcommand (list, approve, reject, defer, detail, ach)")
                sys.exit(1)
        elif args.command == "publish":
            # Handle publish subcommands
            if hasattr(args, "publish_cmd") and args.publish_cmd:
                if args.publish_cmd == "create":
                    await cmd_publish_create(args)
                elif args.publish_cmd == "preview":
                    await cmd_publish_preview(args)
                elif args.publish_cmd == "commit":
                    await cmd_publish_commit(args)
                elif args.publish_cmd == "abort":
                    await cmd_publish_abort(args)
                elif args.publish_cmd == "list":
                    await cmd_publish_list(args)
                else:
                    print(f"Unknown publish command: {args.publish_cmd}")
                    sys.exit(1)
            else:
                print("Publish command requires a subcommand (create, preview, commit, abort, list)")
                sys.exit(1)
        elif args.command == "hunter":
            if hasattr(args, "hunter_cmd") and args.hunter_cmd:
                if args.hunter_cmd == "generate":
                    await cmd_hunter_generate(args)
                elif args.hunter_cmd == "run":
                    await cmd_hunter_run(args)
                elif args.hunter_cmd == "status":
                    await cmd_hunter_status(args)
                elif args.hunter_cmd == "review":
                    await cmd_hunter_review(args)
                elif args.hunter_cmd == "feedback":
                    await cmd_hunter_feedback(args)
                elif args.hunter_cmd == "promote":
                    await cmd_hunter_promote(args)
                elif args.hunter_cmd == "budget":
                    await cmd_hunter_budget(args)
                else:
                    print(f"Unknown hunter command: {args.hunter_cmd}")
                    sys.exit(1)
            else:
                print("Hunter command requires a subcommand (generate, run, status, review, feedback, promote, budget)")
                sys.exit(1)
        elif args.command == "drift":
            if hasattr(args, "drift_cmd") and args.drift_cmd:
                if args.drift_cmd == "check":
                    await cmd_drift_check(args)
                elif args.drift_cmd == "aggregate":
                    await cmd_drift_aggregate(args)
                elif args.drift_cmd == "alerts":
                    await cmd_drift_alerts(args)
                elif args.drift_cmd == "ack":
                    await cmd_drift_ack(args)
                elif args.drift_cmd == "snooze":
                    await cmd_drift_snooze(args)
                elif args.drift_cmd == "resolve":
                    await cmd_drift_resolve(args)
                elif args.drift_cmd == "recommend":
                    await cmd_drift_recommend(args)
                elif args.drift_cmd == "gc":
                    await cmd_drift_gc(args)
                elif args.drift_cmd == "export-metrics":
                    await cmd_drift_export_metrics(args)
                else:
                    print(f"Unknown drift command: {args.drift_cmd}")
                    sys.exit(1)
            else:
                print("Drift command requires a subcommand (check, aggregate, alerts, ack, snooze, resolve, recommend, gc, export-metrics)")
                sys.exit(1)
        elif args.command == "activation-check":
            exit_code = await cmd_activation_check(args)
        elif args.command == "shadow-status":
            exit_code = await cmd_shadow_status(args)
        elif args.command == "phase-g-check":
            exit_code = await cmd_phase_g_check(args)
        elif args.command == "entity-merge-preview":
            exit_code = await cmd_entity_merge_preview(args)
        elif args.command == "entity-audit":
            exit_code = await cmd_entity_audit(args)
        elif args.command == "canary-preflight":
            exit_code = await cmd_canary_preflight(args)
        elif args.command == "backfill-evidence-family":
            exit_code = await cmd_backfill_evidence_family(args)
        elif args.command == "rehydrate-canonical-keys-v2":
            exit_code = await cmd_rehydrate_canonical_keys_v2(args)
        elif args.command == "convergence-kpi":
            exit_code = await cmd_convergence_kpi(args)
        elif args.command == "health-json-pure":
            exit_code = await cmd_health_json_pure(args)
        elif args.command == "dns-phase2-guardrails":
            exit_code = await cmd_dns_phase2_guardrails(args)
        elif args.command == "init-watermark":
            db_path = getattr(args, "db_path", None) or os.getenv("DISCOVERY_DB_PATH", "signals.db")
            try:
                count, error = db_guard.read_current_signal_count(db_path)
                if error:
                    print(f"ERROR: Could not read signal count: {error}", file=sys.stderr)
                    sys.exit(1)
                db_guard.save_watermark(
                    signal_count=count,
                    schema_version=CURRENT_SCHEMA_VERSION,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                print(f"Watermark initialized: signal_count={count}, schema_version={CURRENT_SCHEMA_VERSION}")
            except Exception as exc:
                print(f"ERROR: Could not initialize watermark: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Unknown command: {args.command}")
            parser.print_help()
            sys.exit(1)

        # Exit with the returned code (health command may return non-zero)
        if exit_code != 0:
            sys.exit(exit_code)

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logging.exception("Fatal error")
        print(f"\nFatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
