#!/usr/bin/env python3
"""
Consumer Discovery Engine - CLI Entry Point

Run the full consumer signal discovery pipeline.

Usage:
    # Full pipeline run
    python -m consumer.run_consumer_pipeline run

    # Individual stages
    python -m consumer.run_consumer_pipeline collect
    python -m consumer.run_consumer_pipeline filter
    python -m consumer.run_consumer_pipeline push
    python -m consumer.run_consumer_pipeline poll

    # Get statistics
    python -m consumer.run_consumer_pipeline stats

    # Test mode (skip LLM and Notion)
    python -m consumer.run_consumer_pipeline run --test

Environment Variables Required:
    GOOGLE_API_KEY - For LLM thesis classification (FREE via Google AI Studio)
    NOTION_API_KEY - For Notion integration
    NOTION_INBOX_DATABASE_ID - Target Notion database

    Set these in .env file (copy from .env.example)
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file from project root
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"Loaded environment from {env_path}")

from consumer.workflows.consumer_pipeline import ConsumerPipeline
from consumer.storage.consumer_store import consumer_store


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Quiet noisy loggers
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("aiohttp").setLevel(logging.WARNING)


# =============================================================================
# CLI COMMANDS
# =============================================================================

async def cmd_run(args: argparse.Namespace) -> int:
    """Run full pipeline."""
    print("Starting Consumer Discovery Pipeline...")

    async with ConsumerPipeline(
        db_path=args.db,
        skip_llm=args.test or args.skip_llm,
        skip_notion=args.test or args.skip_notion,
    ) as pipeline:
        result = await pipeline.run()

        # Print summary
        print("\n" + "=" * 50)
        print("PIPELINE RESULTS")
        print("=" * 50)
        print(f"Duration: {result.duration_seconds:.1f}s")
        print()
        print("Collection:")
        print(f"  Signals collected: {result.signals_collected}")
        print(f"  New signals: {result.signals_new}")
        print()
        print("Filtering:")
        print(f"  Total filtered: {result.signals_filtered}")
        print(f"  Auto-rejected: {result.auto_rejected}")
        print(f"  LLM rejected: {result.llm_rejected}")
        print(f"  For review: {result.llm_review}")
        print(f"  Auto-approved: {result.llm_auto_approved}")
        print()
        print("Notion:")
        print(f"  Pushed: {result.signals_pushed}")
        print(f"  Skipped: {result.push_skipped}")
        print(f"  Errors: {result.push_errors}")
        print(f"  Decisions synced: {result.decisions_synced}")

        if result.errors:
            print("\nErrors:")
            for error in result.errors:
                print(f"  - {error}")
            return 1

        return 0


async def cmd_collect(args: argparse.Namespace) -> int:
    """Run only collection stage."""
    print("Running collection stage...")

    async with ConsumerPipeline(db_path=args.db, skip_notion=True) as pipeline:
        results = await pipeline.collect()

        print("\nCollection Results:")
        for r in results:
            print(f"  {r.collector_name}: {r.signals_found} found, {r.signals_new} new")
            if r.errors:
                for error in r.errors:
                    print(f"    ERROR: {error}")

        return 0


async def cmd_filter(args: argparse.Namespace) -> int:
    """Run only filter stage."""
    print("Running filter stage...")

    async with ConsumerPipeline(
        db_path=args.db,
        skip_llm=args.skip_llm,
        skip_notion=True,
    ) as pipeline:
        stats = await pipeline.filter_pending()

        print("\nFilter Results:")
        print(f"  Total: {stats.get('total', 0)}")
        print(f"  Auto-rejected: {stats.get('auto_reject', 0)}")
        print(f"  LLM rejected: {stats.get('llm_reject', 0)}")
        print(f"  For review: {stats.get('llm_review', 0)}")
        print(f"  Auto-approved: {stats.get('llm_auto', 0)}")

        return 0


async def cmd_push(args: argparse.Namespace) -> int:
    """Run only push stage."""
    print("Pushing qualified signals to Notion...")

    async with ConsumerPipeline(db_path=args.db) as pipeline:
        stats = await pipeline.push_qualified()

        print("\nPush Results:")
        print(f"  Pushed: {stats.get('pushed', 0)}")
        print(f"  Skipped: {stats.get('skipped', 0)}")
        print(f"  Errors: {stats.get('errors', 0)}")

        return 0


async def cmd_poll(args: argparse.Namespace) -> int:
    """Poll Notion for user decisions."""
    print("Polling Notion for decisions...")

    async with ConsumerPipeline(db_path=args.db) as pipeline:
        count = await pipeline.poll_decisions(since_minutes=args.since)

        print(f"\nSynced {count} decisions from Notion")

        return 0


async def cmd_stats(args: argparse.Namespace) -> int:
    """Show pipeline statistics."""
    async with consumer_store(args.db) as store:
        stats = await store.get_stats()

        print("\n" + "=" * 50)
        print("PIPELINE STATISTICS")
        print("=" * 50)
        print(f"\nDatabase: {stats.get('database_path')}")
        print(f"Total signals: {stats.get('total_signals', 0)}")
        print(f"Total classifications: {stats.get('total_classifications', 0)}")

        print("\nSignals by Status:")
        for status, count in stats.get("signals_by_status", {}).items():
            print(f"  {status}: {count}")

        print("\nSignals by Source:")
        for source, count in stats.get("signals_by_source", {}).items():
            print(f"  {source}: {count}")

        print("\nFilter Results:")
        for result, count in stats.get("signals_by_filter_result", {}).items():
            print(f"  {result}: {count}")

        # Get cost summary
        cost_summary = await store.get_cost_summary(days=30)
        if cost_summary:
            print("\nCost (last 30 days):")
            total = 0.0
            for service, cost in cost_summary.items():
                print(f"  {service}: ${cost:.4f}")
                total += cost
            print(f"  TOTAL: ${total:.4f}")

        return 0


async def cmd_watch(args: argparse.Namespace) -> int:
    """Run pipeline continuously."""
    print(f"Starting continuous pipeline (every {args.interval} minutes)...")
    print("Press Ctrl+C to stop")

    async with ConsumerPipeline(db_path=args.db) as pipeline:
        while True:
            try:
                print(f"\n[{asyncio.get_event_loop().time():.0f}] Running pipeline...")
                result = await pipeline.run()
                print(
                    f"Done: {result.signals_new} new, "
                    f"{result.signals_pushed} pushed"
                )
            except KeyboardInterrupt:
                print("\nStopping...")
                break
            except Exception as e:
                print(f"Error: {e}")

            await asyncio.sleep(args.interval * 60)

    return 0


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Consumer Discovery Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--db",
        default="consumer_signals.db",
        help="Database path (default: consumer_signals.db)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run full pipeline")
    run_parser.add_argument("--test", action="store_true", help="Skip LLM and Notion")
    run_parser.add_argument("--skip-llm", action="store_true", help="Skip LLM classification")
    run_parser.add_argument("--skip-notion", action="store_true", help="Skip Notion integration")

    # collect command
    subparsers.add_parser("collect", help="Run only collection stage")

    # filter command
    filter_parser = subparsers.add_parser("filter", help="Run only filter stage")
    filter_parser.add_argument("--skip-llm", action="store_true", help="Skip LLM classification")

    # push command
    subparsers.add_parser("push", help="Push qualified signals to Notion")

    # poll command
    poll_parser = subparsers.add_parser("poll", help="Poll Notion for decisions")
    poll_parser.add_argument("--since", type=int, default=10, help="Minutes to look back")

    # stats command
    subparsers.add_parser("stats", help="Show pipeline statistics")

    # watch command
    watch_parser = subparsers.add_parser("watch", help="Run pipeline continuously")
    watch_parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Minutes between runs (default: 60)",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        return 1

    # Check environment
    if args.command in ("run", "push", "poll") and not args.__dict__.get("test"):
        if not os.environ.get("NOTION_API_KEY"):
            print("Warning: NOTION_API_KEY not set")
        if not os.environ.get("NOTION_INBOX_DATABASE_ID"):
            print("Warning: NOTION_INBOX_DATABASE_ID not set")

    if args.command in ("run", "filter") and not args.__dict__.get("skip_llm"):
        if not os.environ.get("OPENAI_API_KEY"):
            print("Warning: OPENAI_API_KEY not set (required for LLM classification)")

    # Run command
    commands = {
        "run": cmd_run,
        "collect": cmd_collect,
        "filter": cmd_filter,
        "push": cmd_push,
        "poll": cmd_poll,
        "stats": cmd_stats,
        "watch": cmd_watch,
    }

    return asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    sys.exit(main())
