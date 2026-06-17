"""
Exit Predictor Batch Job

Nightly job to compute percentile rankings for exit predictions.
Run via: python -m utils.exit_predictor_batch

See: docs/plans/2026-01-15-exit-predictor-phase1-design.md
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


class ExitPredictorBatch:
    """
    Batch job for computing percentile rankings.

    Designed to run nightly to update percentile_rank for all predictions.
    Percentile is computed based on deal_quality_score ranking.
    """

    def __init__(self, signal_store: "SignalStore"):
        """
        Initialize batch job.

        Args:
            signal_store: SignalStore instance for database access
        """
        self._signal_store = signal_store

    async def compute_percentiles(self) -> int:
        """
        Compute and update percentile ranks for all predictions.

        Percentile calculation:
        - Rank 1 (highest quality) = 99th percentile
        - Rank N (lowest quality) = 1st percentile

        Returns:
            Number of predictions updated
        """
        # Get all predictions ordered by deal_quality_score DESC
        predictions = await self._signal_store.get_all_exit_predictions(
            order_by="deal_quality_score DESC"
        )

        total = len(predictions)
        if total == 0:
            logger.info("No predictions to rank")
            return 0

        logger.info(f"Computing percentiles for {total} predictions")

        updated = 0
        for position, pred in enumerate(predictions):
            # Percentile: top 1% = 99, bottom 1% = 1
            # Position 0 = highest quality = 99th percentile
            # Position N-1 = lowest quality = 1st percentile
            if total == 1:
                percentile = 50  # Single prediction gets 50th percentile
            else:
                percentile = int((1 - position / (total - 1)) * 98) + 1
                percentile = max(1, min(99, percentile))

            success = await self._signal_store.update_exit_prediction_percentile(
                canonical_key=pred["canonical_key"],
                percentile_rank=percentile,
            )

            if success:
                updated += 1
            else:
                logger.warning(
                    f"Failed to update percentile for {pred['canonical_key']}"
                )

        logger.info(f"Updated {updated} of {total} predictions")
        return updated


async def run_batch_job(db_path: str | None = None) -> int:
    """
    Run the batch job standalone.

    Args:
        db_path: Path to signals database

    Returns:
        Number of predictions updated
    """
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        batch = ExitPredictorBatch(store)
        return await batch.compute_percentiles()
    finally:
        await store.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    updated = asyncio.run(run_batch_job(db_path))
    print(f"Updated {updated} predictions")
