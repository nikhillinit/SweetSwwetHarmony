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
  import-csv - Import signals from CSV files (OpenVC, etc.)

Examples:
  # Run full pipeline with specific collectors (dry run)
  python run_pipeline.py full --collectors github,sec_edgar --dry-run

  # Run collectors only (persist to DB)
  python run_pipeline.py collect --collectors companies_house

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
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from typing import Optional

from workflows.pipeline import (
    DiscoveryPipeline,
    PipelineConfig,
    PipelineMode,
    PipelineStats,
)
from utils.signal_health import SignalHealthMonitor
from connectors.notion_connector_v2 import NotionConnector
from storage.signal_store import SignalStore

try:
    import httpx
except ImportError:
    httpx = None


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
    print("=" * 70)
    print("DISCOVERY ENGINE - FULL PIPELINE")
    print("=" * 70)

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
        print(f"Use gating: {config.use_gating}")
        print(f"Use entities: {config.use_entities}")
        print(f"Use asset store: {config.use_asset_store}")
        print()

        # Run pipeline
        stats = await pipeline.run_full_pipeline(
            collectors=collectors,
            dry_run=args.dry_run,
        )

        # Print results
        print()
        print("=" * 70)
        print("PIPELINE RESULTS")
        print("=" * 70)
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
    print("=" * 70)
    print("DISCOVERY ENGINE - COLLECT SIGNALS")
    print("=" * 70)

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

        # Print results
        print()
        print("=" * 70)
        print("COLLECTOR RESULTS")
        print("=" * 70)
        print()

        for result in results:
            status_symbol = "✓" if result.status.value == "success" else "✗"
            print(f"{status_symbol} {result.collector}")
            print(f"  Status: {result.status.value}")
            print(f"  Signals found: {result.signals_found}")
            print(f"  Signals new: {result.signals_new}")
            print(f"  Signals suppressed: {result.signals_suppressed}")
            if result.error_message:
                print(f"  Error: {result.error_message}")
            print()

        # Summary
        total_signals = sum(r.signals_found for r in results)
        succeeded = sum(1 for r in results if r.status.value == "success")
        print(f"Summary: {succeeded}/{len(results)} collectors succeeded")
        print(f"Total signals: {total_signals}")

    finally:
        await pipeline.close()


async def cmd_process(args):
    """Process pending signals"""
    print("=" * 70)
    print("DISCOVERY ENGINE - PROCESS PENDING SIGNALS")
    print("=" * 70)

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

        print(f"\nDatabase: {config.db_path}")
        print(f"Batch size: {config.batch_size}")
        print(f"Dry run: {args.dry_run}")
        print(f"Use gating: {config.use_gating}")
        print()

        # Process pending signals
        result = await pipeline.process_pending(dry_run=args.dry_run)

        # Print results
        print()
        print("=" * 70)
        print("PROCESSING RESULTS")
        print("=" * 70)
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
    print("=" * 70)
    print("DISCOVERY ENGINE - SYNC SUPPRESSION CACHE")
    print("=" * 70)

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
        print("=" * 70)
        print("SYNC COMPLETE")
        print("=" * 70)
        print()
        print(f"Entries synced: {count}")
        print()
        print("Suppression cache is now up-to-date with Notion CRM")

    finally:
        await pipeline.close()


async def cmd_stats(args):
    """Show pipeline statistics"""
    print("=" * 70)
    print("DISCOVERY ENGINE - STATISTICS")
    print("=" * 70)

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
        print("-" * 70)
        storage = stats.get("storage", {})
        print(f"Database: {storage.get('database_path', 'Unknown')}")
        print(f"Total signals: {storage.get('total_signals', 0)}")
        print()

        print("Signals by type:")
        for signal_type, count in storage.get("signals_by_type", {}).items():
            print(f"  {signal_type}: {count}")
        print()

        print("PROCESSING STATUS")
        print("-" * 70)
        processing = stats.get("processing", {})
        for status, count in processing.items():
            print(f"  {status}: {count}")
        print()

        print("SUPPRESSION CACHE")
        print("-" * 70)
        print(f"Active entries: {storage.get('active_suppression_entries', 0)}")
        print()

        print("CONFIGURATION")
        print("-" * 70)
        cfg = stats.get("config", {})
        print(f"Parallel collectors: {cfg.get('parallel_collectors', False)}")
        print(f"Batch size: {cfg.get('batch_size', 0)}")
        print(f"Strict mode: {cfg.get('strict_mode', False)}")

    finally:
        await pipeline.close()


async def cmd_health(args):
    """Run health checks on all components"""
    output_json = getattr(args, "output_json", False)
    verbose = getattr(args, "verbose", False)
    lookback_days = getattr(args, "lookback_days", 30)

    if not output_json:
        print("=" * 70)
        print("DISCOVERY ENGINE - HEALTH CHECK")
        print("=" * 70)
        print()

    config = PipelineConfig.from_env()

    if args.db_path:
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)

    all_healthy = True
    checks = []
    health_report_dict = None
    suppression_stats = {}

    try:
        # 1. Database connectivity check
        if not output_json:
            print("Checking database connectivity...")
        try:
            await pipeline.initialize()

            if pipeline._store and pipeline._store._db:
                if not output_json:
                    print("  Database: HEALTHY")
                checks.append(("Database", True, None))
            else:
                if not output_json:
                    print("  Database: FAILED (no connection)")
                checks.append(("Database", False, "No database connection"))
                all_healthy = False
        except Exception as e:
            if not output_json:
                print(f"  Database: FAILED ({e})")
            checks.append(("Database", False, str(e)))
            all_healthy = False

        # 2. Configuration validation check
        if not output_json:
            print("Checking configuration...")
        try:
            config_issues = []
            if not config.notion_api_key:
                config_issues.append("NOTION_API_KEY not set")
            if not config.notion_database_id:
                config_issues.append("NOTION_DATABASE_ID not set")

            if config_issues:
                if not output_json:
                    print(f"  Configuration: WARNING ({', '.join(config_issues)})")
                checks.append(("Configuration", True, ", ".join(config_issues)))
            else:
                if not output_json:
                    print("  Configuration: HEALTHY")
                checks.append(("Configuration", True, None))
        except Exception as e:
            if not output_json:
                print(f"  Configuration: FAILED ({e})")
            checks.append(("Configuration", False, str(e)))

        # 3. API connectivity checks
        if not output_json:
            print("Checking API connectivity...")

        # GitHub API
        try:
            ok, msg = await check_github_api()
            if ok:
                if not output_json:
                    print("  GitHub API: OK")
                checks.append(("GitHub API", True, None))
            else:
                if not output_json:
                    print(f"  GitHub API: FAILED ({msg})")
                checks.append(("GitHub API", False, msg))
                all_healthy = False
        except Exception as e:
            if not output_json:
                print(f"  GitHub API: FAILED ({e})")
            checks.append(("GitHub API", False, str(e)))
            all_healthy = False

        # SEC EDGAR API
        try:
            ok, msg = await check_sec_edgar_api()
            if ok:
                if not output_json:
                    print("  SEC EDGAR API: OK")
                checks.append(("SEC EDGAR API", True, None))
            else:
                if not output_json:
                    print(f"  SEC EDGAR API: FAILED ({msg})")
                checks.append(("SEC EDGAR API", False, msg))
                all_healthy = False
        except Exception as e:
            if not output_json:
                print(f"  SEC EDGAR API: FAILED ({e})")
            checks.append(("SEC EDGAR API", False, str(e)))
            all_healthy = False

        # Notion API
        try:
            if config.notion_api_key:
                ok, msg = await check_notion_api(config.notion_api_key)
                if ok:
                    if not output_json:
                        print("  Notion API: OK")
                    checks.append(("Notion API", True, None))
                else:
                    if not output_json:
                        print(f"  Notion API: FAILED ({msg})")
                    checks.append(("Notion API", False, msg))
                    all_healthy = False
            else:
                if not output_json:
                    print("  Notion API: SKIPPED (not configured)")
                checks.append(("Notion API", True, "Not configured"))
        except Exception as e:
            if not output_json:
                print(f"  Notion API: FAILED ({e})")
            checks.append(("Notion API", False, str(e)))
            all_healthy = False

        # Gemini API
        try:
            gemini_key = os.getenv("GOOGLE_API_KEY", "")
            if gemini_key:
                ok, msg = await check_gemini_api(gemini_key)
                if ok:
                    if not output_json:
                        print("  Gemini API: OK")
                    checks.append(("Gemini API", True, None))
                else:
                    if not output_json:
                        print(f"  Gemini API: FAILED ({msg})")
                    checks.append(("Gemini API", False, msg))
                    all_healthy = False
            else:
                if not output_json:
                    print("  Gemini API: SKIPPED (not configured)")
                checks.append(("Gemini API", True, "Not configured"))
        except Exception as e:
            if not output_json:
                print(f"  Gemini API: FAILED ({e})")
            checks.append(("Gemini API", False, str(e)))
            all_healthy = False

        # 4. Suppression cache stats
        if not output_json:
            print("Checking suppression cache...")
        try:
            if pipeline._store and pipeline._store._db:
                stats = await pipeline.get_stats()
                storage = stats.get("storage", {})
                cache_entries = storage.get("active_suppression_entries", 0)
                suppression_stats = {
                    "active_entries": cache_entries,
                    "status": "HEALTHY" if cache_entries > 0 else "WARNING",
                }

                if cache_entries > 0:
                    if not output_json:
                        print(f"  Suppression Cache: HEALTHY ({cache_entries} entries)")
                    checks.append(("Suppression Cache", True, f"{cache_entries} entries"))
                else:
                    if not output_json:
                        print("  Suppression Cache: WARNING (empty - run 'sync' command)")
                    checks.append(("Suppression Cache", True, "Empty - run 'sync' to populate"))
            else:
                if not output_json:
                    print("  Suppression Cache: SKIPPED (no database)")
                checks.append(("Suppression Cache", True, "Database unavailable"))
        except Exception as e:
            if not output_json:
                print(f"  Suppression Cache: FAILED ({e})")
            checks.append(("Suppression Cache", False, str(e)))

        # 5. Signal health check
        if not output_json:
            print(f"Checking signal health (last {lookback_days} days)...")
        try:
            if pipeline._store and pipeline._store._db:
                monitor = SignalHealthMonitor(pipeline._store)
                report = await monitor.generate_report(lookback_days=lookback_days)

                if not output_json:
                    print(f"  Signal Health: {report.overall_status}")

                # Store for JSON output
                health_report_dict = report.to_dict()

                # Print the full health report if verbose
                if verbose and not output_json:
                    print()
                    print(report)

                # Track health status
                if report.overall_status == "HEALTHY":
                    checks.append(("Signal Health", True, None))
                elif report.overall_status == "DEGRADED":
                    checks.append(("Signal Health", False, "System degraded"))
                    all_healthy = False
                else:  # CRITICAL
                    checks.append(("Signal Health", False, "System critical"))
                    all_healthy = False
            else:
                if not output_json:
                    print("  Signal Health: SKIPPED (no database)")
                checks.append(("Signal Health", True, "Database unavailable"))
        except Exception as e:
            if not output_json:
                print(f"  Signal Health: FAILED ({e})")
            checks.append(("Signal Health", False, str(e)))
            all_healthy = False

        # Output as JSON or human-readable
        if output_json:
            result = {
                "overall_status": "HEALTHY" if all_healthy else "UNHEALTHY",
                "checks": [
                    {"name": name, "passed": ok, "message": msg}
                    for name, ok, msg in checks
                ],
                "signal_health": health_report_dict,
                "suppression_cache": suppression_stats,
                "config": {
                    "db_path": config.db_path,
                    "use_gating": config.use_gating,
                    "use_entities": config.use_entities,
                    "use_asset_store": config.use_asset_store,
                    "lookback_days": lookback_days,
                },
            }
            print(json.dumps(result, indent=2, default=str))
        else:
            # Print summary
            print()
            print("=" * 70)
            print("HEALTH CHECK SUMMARY")
            print("=" * 70)
            print()

            for check_name, check_ok, check_msg in checks:
                status_symbol = "PASS" if check_ok else "FAIL"
                print(f"  [{status_symbol}] {check_name}")
                if check_msg:
                    print(f"       {check_msg}")

            print()
            if all_healthy:
                print("Overall Status: HEALTHY")
            else:
                print("Overall Status: UNHEALTHY")
            print()

        return 0 if all_healthy else 1

    except Exception as e:
        if output_json:
            print(json.dumps({"error": str(e), "overall_status": "ERROR"}, indent=2))
        else:
            print()
            print(f"Health check failed with error: {e}")
        logging.exception("Health check error")
        return 1
    finally:
        await pipeline.close()


async def cmd_metrics(args):
    """Show pipeline run metrics with per-collector breakdown."""
    print("=" * 70)
    print("DISCOVERY ENGINE - PIPELINE METRICS")
    print("=" * 70)

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

                    # Status indicator
                    status_icon = "+" if status == "success" else "x" if status == "error" else "o"

                    # Format API metrics
                    api_parts = [f"{api_calls} calls"]
                    if retries > 0:
                        api_parts.append(f"{retries} retries")
                    if rate_limits > 0:
                        api_parts.append(f"{rate_limits} rate limits")
                    api_str = ", ".join(api_parts)

                    print(f"  {name:<16} {dur:>6.1f}s   {status_icon}   {signals:>3} signals   |  API: {api_str}")

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

    print("=" * 70)
    print("DISCOVERY ENGINE - IMPORT EMAILS")
    print("=" * 70)
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
        print("-" * 70)
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
        print("=" * 70)
        print("IMPORT COMPLETE")
        print("=" * 70)
        print()
        print(f"Relationships stored: {stored}")
        print()
        print("Top 10 relationships by message count:")
        print("-" * 70)
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

    print("=" * 70)
    print("DISCOVERY ENGINE - SYNC LP RELATIONSHIPS")
    print("=" * 70)
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
        print("-" * 70)
        for rel in sorted(relationships, key=lambda r: -r.score)[:20]:
            print(f"  {rel.domain:<30} {rel.score:.2f}  {rel.badge}")
            print(f"    {rel.attribution}")
        if len(relationships) > 20:
            print(f"  ... and {len(relationships) - 20} more firms")
        return

    # TODO: Store relationships when RelationshipStore.upsert_lp_relationship is added (Phase 4)
    print("=" * 70)
    print("SYNC COMPLETE")
    print("=" * 70)
    print()
    print(f"Firm relationships synced: {len(relationships)}")
    print()
    print("Top 10 relationships by score:")
    print("-" * 70)
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
            print("=" * 70)
            print("RELATIONSHIP HEALTH REPORT")
            print("=" * 70)
            print()
            print(f"Overall Status: {report.overall_status}")
            print()

            print("Relationship Counts:")
            print("-" * 40)
            print(f"  Total:    {report.relationship_count}")
            print(f"  Gmail:    {report.gmail_relationship_count}")
            print(f"  LP:       {report.lp_relationship_count}")
            print(f"  Combined: {report.combined_relationship_count}")
            print()

            print("Email Scan Health:")
            print("-" * 40)
            eh = report.email_health
            print(f"  Status:      {eh.status}")
            if eh.days_since_scan is not None:
                print(f"  Days since:  {eh.days_since_scan}")
            print(f"  Records:     {eh.record_count}")
            print()

            print("LP Sync Health:")
            print("-" * 40)
            lh = report.lp_health
            print(f"  Status:      {lh.status}")
            if lh.days_since_sync is not None:
                print(f"  Days since:  {lh.days_since_sync}")
            print(f"  Records:     {lh.record_count}")
            print()

            if report.alerts:
                print("Alerts:")
                print("-" * 40)
                for alert in report.alerts:
                    print(f"  [{alert.severity}] {alert.description}")
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
    """Pretty-print pipeline statistics"""

    # Collectors
    print("COLLECTORS")
    print("-" * 70)
    print(f"Collectors run: {stats.collectors_run}")
    print(f"Succeeded: {stats.collectors_succeeded}")
    print(f"Failed: {stats.collectors_failed}")
    print(f"Signals collected: {stats.signals_collected}")
    print()

    # Storage
    if stats.signals_stored or stats.signals_deduplicated:
        print("STORAGE")
        print("-" * 70)
        print(f"Signals stored: {stats.signals_stored}")
        print(f"Signals deduplicated: {stats.signals_deduplicated}")
        print()

    # Verification
    if stats.signals_processed:
        print("VERIFICATION")
        print("-" * 70)
        print(f"Signals processed: {stats.signals_processed}")
        print(f"Auto-push: {stats.signals_auto_push}")
        print(f"Needs review: {stats.signals_needs_review}")
        print(f"Held: {stats.signals_held}")
        print(f"Rejected: {stats.signals_rejected}")
        print()

    # Notion
    if stats.prospects_created or stats.prospects_updated or stats.prospects_skipped:
        print("NOTION CRM")
        print("-" * 70)
        print(f"Prospects created: {stats.prospects_created}")
        print(f"Prospects updated: {stats.prospects_updated}")
        print(f"Prospects skipped: {stats.prospects_skipped}")
        print()

    # Errors
    if stats.errors:
        print("ERRORS")
        print("-" * 70)
        for error in stats.errors:
            print(f"  • {error}")
        print()

    # Timing
    print("TIMING")
    print("-" * 70)
    print(f"Started: {stats.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    if stats.completed_at:
        print(f"Completed: {stats.completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Duration: {stats.duration_seconds:.2f}s")


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
        print("-" * 60)
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

        print("\n" + "=" * 70)
        print("CORROBORATE - OpenCorporates Lookup")
        print("=" * 70)
        print(f"Companies to look up: {len(rows)}")
        print(f"Source filter: {args.source or 'all'}")
        print(f"Dry run: {args.dry_run}")
        print("-" * 70)

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

        print("-" * 70)
        print(f"Found: {found} | Not found: {not_found} | Errors: {errors}")
        print("=" * 70)

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

        print("\n" + "=" * 70)
        print("GOLD SET COMPANIES")
        print("=" * 70)
        print(f"{'Canonical Key':<40} | {'Name':<20} | Category")
        print("-" * 70)

        for company in companies:
            print(f"{company.canonical_key:<40} | {company.company_name[:20]:<20} | {company.category}")

        print("-" * 70)
        print(f"Total: {len(companies)} companies")
        print("=" * 70)

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

        print("\n" + "=" * 70)
        print("GOLD SET STATISTICS")
        print("=" * 70)
        print(f"Total companies:  {stats.total_companies}")
        print(f"Total labels:     {stats.total_labels}")
        print(f"Investor labels:  {stats.total_investor_labels}")
        print("-" * 70)
        print("By category:")
        for cat, count in sorted(stats.by_category.items()):
            print(f"  {cat:<20}: {count}")
        print("-" * 70)
        print(f"Annotators: {', '.join(stats.annotators) if stats.annotators else 'None'}")
        print("=" * 70)

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

        print("\n" + "=" * 70)
        print(f"EVALUATION - {args.type.upper()}")
        print("=" * 70)
        print(f"Gold set version: {args.gold_set_version}")
        print(f"Check drift: {args.check_drift}")
        print("-" * 70)

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
            print("-" * 40)

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
            print("-" * 40)

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

        print("\n" + "=" * 70)
        print("Evaluation complete")
        print("=" * 70)

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
        print("-" * 80)

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


async def main():
    """Main entry point"""

    parser = create_parser()
    args = parser.parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)

    # Check for command
    if not args.command:
        parser.print_help()
        sys.exit(1)

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
            else:
                print("Pipeline command requires a subcommand (status, qualified, push)")
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
