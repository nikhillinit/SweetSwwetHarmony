#!/usr/bin/env python3
"""
CLI interface for the Discovery Engine pipeline.

Commands:
  full      - Run complete pipeline (collect + process + push)
  collect   - Run collectors only
  process   - Process pending signals
  sync      - Sync suppression cache from Notion
  stats     - Show pipeline statistics
  health    - Run health checks on all components

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

from workflows.pipeline import (
    DiscoveryPipeline,
    PipelineConfig,
    PipelineMode,
    PipelineStats,
)
from utils.signal_health import SignalHealthMonitor


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
    if hasattr(args, "no_gating") and args.no_gating:
        config.use_gating = False
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
    if hasattr(args, "no_gating") and args.no_gating:
        config.use_gating = False
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

    try:
        # 1. Database connectivity check
        print("Checking database connectivity...")
        try:
            await pipeline.initialize()

            if pipeline.signal_store and pipeline.signal_store._conn:
                print("  Database: HEALTHY")
                checks.append(("Database", True, None))
            else:
                print("  Database: FAILED (no connection)")
                checks.append(("Database", False, "No database connection"))
                all_healthy = False
        except Exception as e:
            print(f"  Database: FAILED ({e})")
            checks.append(("Database", False, str(e)))
            all_healthy = False

        # 2. Notion API connectivity check
        print("Checking Notion API connectivity...")
        try:
            if hasattr(pipeline, 'notion_connector') and pipeline.notion_connector:
                # Try to test connection if method exists
                if hasattr(pipeline.notion_connector, 'test_connection'):
                    notion_ok = await pipeline.notion_connector.test_connection()
                    if notion_ok:
                        print("  Notion API: HEALTHY")
                        checks.append(("Notion API", True, None))
                    else:
                        print("  Notion API: DEGRADED (connection test failed)")
                        checks.append(("Notion API", False, "Connection test failed"))
                        all_healthy = False
                else:
                    print("  Notion API: UNKNOWN (no test method)")
                    checks.append(("Notion API", True, "No test method available"))
            else:
                print("  Notion API: SKIPPED (not configured)")
                checks.append(("Notion API", True, "Not configured"))
        except Exception as e:
            print(f"  Notion API: FAILED ({e})")
            checks.append(("Notion API", False, str(e)))
            all_healthy = False

        # 3. Signal health check
        print("Checking signal health...")
        try:
            if pipeline.signal_store and pipeline.signal_store._conn:
                monitor = SignalHealthMonitor(pipeline.signal_store)
                report = await monitor.generate_report(lookback_days=30)

                print(f"  Signal Health: {report.overall_status}")

                # Print the full health report
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
                print("  Signal Health: SKIPPED (no database)")
                checks.append(("Signal Health", True, "Database unavailable"))
        except Exception as e:
            print(f"  Signal Health: FAILED ({e})")
            checks.append(("Signal Health", False, str(e)))
            all_healthy = False

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
            print()
            return 0
        else:
            print("Overall Status: UNHEALTHY")
            print()
            return 1

    except Exception as e:
        print()
        print(f"Health check failed with error: {e}")
        logging.exception("Health check error")
        return 1
    finally:
        await pipeline.close()


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
    # Feature flags - gating is ON by default, use --no-gating to disable
    full_parser.add_argument(
        "--no-gating",
        action="store_true",
        help="Disable two-stage gating (TriggerGate + LLMClassifierV2)",
    )
    full_parser.add_argument(
        "--use-gating",
        action="store_true",
        help="Explicitly enable two-stage gating (enabled by default)",
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
        "--no-gating",
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
        "--no-gating",
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

    return parser


# =============================================================================
# MAIN
# =============================================================================

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
