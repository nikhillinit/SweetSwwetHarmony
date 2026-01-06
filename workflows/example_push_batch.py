"""
Example: Push Batch of Signals to Notion

Demonstrates how to use NotionPusher to process pending signals.

Usage:
    python workflows/example_push_batch.py
    python workflows/example_push_batch.py --limit 10 --dry-run
"""

import asyncio
import logging
from pathlib import Path

from workflows.notion_pusher import NotionPusher, run_batch_push
from storage.signal_store import SignalStore
from connectors.notion_connector_v2 import create_connector_from_env
from verification.verification_gate_v2 import VerificationGate


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


async def example_basic_batch():
    """Example: Basic batch processing"""
    print("\n" + "=" * 60)
    print("EXAMPLE 1: Basic Batch Processing")
    print("=" * 60 + "\n")

    # Process all pending signals
    result = await run_batch_push(
        db_path="signals.db",
        limit=None,  # Process all
        dry_run=False
    )

    print(result.summary())


async def example_with_limit():
    """Example: Process limited batch"""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Process Limited Batch (10 signals)")
    print("=" * 60 + "\n")

    result = await run_batch_push(
        db_path="signals.db",
        limit=10,  # Only process 10 signals
        dry_run=False
    )

    print(result.summary())


async def example_dry_run():
    """Example: Dry run (preview without pushing)"""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Dry Run (Preview)")
    print("=" * 60 + "\n")

    result = await run_batch_push(
        db_path="signals.db",
        limit=5,
        dry_run=True  # Don't actually push
    )

    print(result.summary())

    print("\nProspects that would be pushed:")
    for push_result in result.results:
        if push_result.pushed:
            print(f"  - {push_result.company_name} ({push_result.canonical_key})")
            print(f"    Confidence: {push_result.confidence:.2%}")
            print(f"    Decision: {push_result.decision.value}")
            print(f"    Status: {push_result.notion_status}")
            print()


async def example_custom_configuration():
    """Example: Custom pusher configuration"""
    print("\n" + "=" * 60)
    print("EXAMPLE 4: Custom Configuration")
    print("=" * 60 + "\n")

    # Initialize components with custom config
    store = SignalStore("signals.db")
    await store.initialize()

    try:
        notion = create_connector_from_env()

        # Custom verification gate (strict mode)
        gate = VerificationGate(
            strict_mode=True,  # Require 2+ sources for auto-push
            auto_push_status="Source",
            needs_review_status="Tracking"
        )

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_batch(limit=20)

        print(result.summary())

    finally:
        await store.close()


async def example_single_prospect():
    """Example: Process single prospect by canonical key"""
    print("\n" + "=" * 60)
    print("EXAMPLE 5: Process Single Prospect")
    print("=" * 60 + "\n")

    canonical_key = "domain:acme.ai"  # Replace with actual key

    store = SignalStore("signals.db")
    await store.initialize()

    try:
        notion = create_connector_from_env()
        gate = VerificationGate()

        pusher = NotionPusher(
            signal_store=store,
            notion_connector=notion,
            verification_gate=gate,
            dry_run=False
        )

        result = await pusher.process_single_prospect(canonical_key)

        print(f"Company: {result.company_name}")
        print(f"Decision: {result.decision.value}")
        print(f"Confidence: {result.confidence:.2%}")
        print(f"Pushed: {result.pushed}")

        if result.error:
            print(f"Error: {result.error}")

    finally:
        await store.close()


async def example_detailed_results():
    """Example: Detailed result analysis"""
    print("\n" + "=" * 60)
    print("EXAMPLE 6: Detailed Result Analysis")
    print("=" * 60 + "\n")

    result = await run_batch_push(
        db_path="signals.db",
        limit=10,
        dry_run=True
    )

    print(result.summary())
    print("\n" + "-" * 60)
    print("DETAILED BREAKDOWN")
    print("-" * 60)

    # Group by decision
    by_decision = {
        "auto_push": [],
        "needs_review": [],
        "hold": [],
        "reject": []
    }

    for push_result in result.results:
        decision_key = push_result.decision.value.lower()
        by_decision[decision_key].append(push_result)

    # Print breakdown
    for decision, results in by_decision.items():
        if results:
            print(f"\n{decision.upper().replace('_', ' ')} ({len(results)}):")
            for r in results:
                print(f"  - {r.company_name}")
                print(f"    Confidence: {r.confidence:.2%} | Sources: {r.sources_count}")
                print(f"    Reason: {r.push_reason}")
                if r.error:
                    print(f"    Error: {r.error}")


async def example_monitoring():
    """Example: Continuous monitoring and pushing"""
    print("\n" + "=" * 60)
    print("EXAMPLE 7: Continuous Monitoring")
    print("=" * 60 + "\n")

    print("Running continuous batch processor (Ctrl+C to stop)...")

    interval_seconds = 60  # Check every 60 seconds
    batch_size = 20

    try:
        while True:
            logger.info("Checking for pending signals...")

            result = await run_batch_push(
                db_path="signals.db",
                limit=batch_size,
                dry_run=False
            )

            if result.total_processed > 0:
                logger.info(f"Processed batch: {result.summary()}")
            else:
                logger.info("No pending signals")

            logger.info(f"Sleeping for {interval_seconds}s...")
            await asyncio.sleep(interval_seconds)

    except KeyboardInterrupt:
        logger.info("Stopped by user")


async def main():
    """Run examples"""
    import sys

    examples = {
        "1": ("Basic batch processing", example_basic_batch),
        "2": ("Process with limit", example_with_limit),
        "3": ("Dry run (preview)", example_dry_run),
        "4": ("Custom configuration", example_custom_configuration),
        "5": ("Single prospect", example_single_prospect),
        "6": ("Detailed results", example_detailed_results),
        "7": ("Continuous monitoring", example_monitoring),
    }

    if len(sys.argv) > 1:
        choice = sys.argv[1]
        if choice in examples:
            name, func = examples[choice]
            await func()
            return

    # Interactive menu
    print("\nNotionPusher Examples")
    print("=" * 60)
    for key, (name, _) in examples.items():
        print(f"{key}. {name}")
    print("\nUsage: python example_push_batch.py <number>")
    print("   Or: Run from CLI with --help")


if __name__ == "__main__":
    asyncio.run(main())
