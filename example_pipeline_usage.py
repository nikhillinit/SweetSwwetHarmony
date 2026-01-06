#!/usr/bin/env python3
"""
Example usage of the Discovery Engine pipeline.

This demonstrates how to use the pipeline programmatically.
"""

import asyncio
import logging
from workflows.pipeline import DiscoveryPipeline, PipelineConfig

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def example_full_pipeline():
    """Example: Run full pipeline"""

    logger.info("Example 1: Full Pipeline")
    print("=" * 70)

    # Load config from environment
    config = PipelineConfig.from_env()

    # Create pipeline
    pipeline = DiscoveryPipeline(config)

    try:
        # Initialize
        await pipeline.initialize()

        # Run full pipeline
        stats = await pipeline.run_full_pipeline(
            collectors=["github", "sec_edgar"],
            dry_run=True,  # Set to False to actually push to Notion
        )

        # Print results
        print("\nResults:")
        print(f"  Collectors run: {stats.collectors_run}")
        print(f"  Signals collected: {stats.signals_collected}")
        print(f"  Signals processed: {stats.signals_processed}")
        print(f"  Auto-push: {stats.signals_auto_push}")
        print(f"  Needs review: {stats.signals_needs_review}")
        print(f"  Prospects created: {stats.prospects_created}")
        print(f"  Duration: {stats.duration_seconds:.2f}s")

    finally:
        await pipeline.close()


async def example_staged_execution():
    """Example: Run pipeline stages independently"""

    logger.info("Example 2: Staged Execution")
    print("=" * 70)

    config = PipelineConfig.from_env()
    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        # Stage 1: Run collectors
        print("\nStage 1: Running collectors...")
        collector_results = await pipeline.run_collectors(
            collector_names=["github"],
            dry_run=False,  # Persist to database
        )

        for result in collector_results:
            print(f"  {result.collector}: {result.signals_found} signals")

        # Stage 2: Sync suppression cache
        print("\nStage 2: Syncing suppression cache...")
        sync_count = await pipeline.sync_suppression()
        print(f"  Synced {sync_count} entries")

        # Stage 3: Process pending signals
        print("\nStage 3: Processing pending signals...")
        process_stats = await pipeline.process_pending(dry_run=True)
        print(f"  Processed: {process_stats['processed']}")
        print(f"  Auto-push: {process_stats['auto_push']}")
        print(f"  Needs review: {process_stats['needs_review']}")

        # Stage 4: Get statistics
        print("\nStage 4: Pipeline statistics...")
        stats = await pipeline.get_stats()
        print(f"  Total signals: {stats['storage']['total_signals']}")
        print(f"  Pending: {stats['processing'].get('pending', 0)}")
        print(f"  Pushed: {stats['processing'].get('pushed', 0)}")

    finally:
        await pipeline.close()


async def example_context_manager():
    """Example: Using context manager"""

    logger.info("Example 3: Context Manager")
    print("=" * 70)

    from workflows.pipeline import pipeline_context

    # Automatic initialization and cleanup
    async with pipeline_context() as pipeline:
        # Get stats
        stats = await pipeline.get_stats()
        print(f"\nTotal signals: {stats['storage']['total_signals']}")

        # Run collectors
        await pipeline.run_collectors(["github"], dry_run=True)

        # Process
        await pipeline.process_pending(dry_run=True)


async def example_custom_config():
    """Example: Custom configuration"""

    logger.info("Example 4: Custom Configuration")
    print("=" * 70)

    # Create custom config
    config = PipelineConfig(
        db_path="custom_signals.db",
        parallel_collectors=False,  # Run sequentially
        batch_size=10,             # Process in small batches
        strict_mode=True,          # Require 2+ sources for auto-push
    )

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        print(f"\nConfiguration:")
        print(f"  Database: {config.db_path}")
        print(f"  Parallel collectors: {config.parallel_collectors}")
        print(f"  Batch size: {config.batch_size}")
        print(f"  Strict mode: {config.strict_mode}")

        # Run with custom config
        stats = await pipeline.run_full_pipeline(
            collectors=["github"],
            dry_run=True,
        )

        print(f"\nResults: {stats.signals_collected} signals collected")

    finally:
        await pipeline.close()


async def main():
    """Run all examples"""

    print("\n" + "=" * 70)
    print("DISCOVERY ENGINE PIPELINE - USAGE EXAMPLES")
    print("=" * 70 + "\n")

    # Run examples
    try:
        await example_full_pipeline()
        print("\n")

        # Uncomment to run other examples:
        # await example_staged_execution()
        # await example_context_manager()
        # await example_custom_config()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        logger.exception("Example failed")
        print(f"\nError: {e}")

    print("\n" + "=" * 70)
    print("Examples complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
