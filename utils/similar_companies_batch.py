"""
Similar Companies Batch Job

Nightly job to pre-compute embeddings for all companies.
Run via: python -m utils.similar_companies_batch

Sprint 4: Similar Companies feature.

This solves the cold-start problem by ensuring embeddings are available
for similarity queries without needing to compute them in real-time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.embedding_store import EmbeddingStore
    from storage.signal_store import SignalStore
    from utils.embedding_generator import EmbeddingGenerator

logger = logging.getLogger(__name__)


# =============================================================================
# BATCH RESULT
# =============================================================================

@dataclass
class BatchResult:
    """Result of running the batch job."""
    total_companies: int = 0
    new_embeddings: int = 0
    updated_embeddings: int = 0
    skipped_embeddings: int = 0
    failed_embeddings: int = 0
    errors: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        """Duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "total_companies": self.total_companies,
            "new_embeddings": self.new_embeddings,
            "updated_embeddings": self.updated_embeddings,
            "skipped_embeddings": self.skipped_embeddings,
            "failed_embeddings": self.failed_embeddings,
            "error_count": len(self.errors),
            "duration_seconds": self.duration_seconds,
        }


# =============================================================================
# BATCH JOB
# =============================================================================

class SimilarCompaniesBatch:
    """
    Batch job for pre-computing company embeddings.

    Designed to run nightly to ensure embeddings are available for
    similarity queries. Uses staleness detection to only re-compute
    embeddings when the underlying profile has changed.
    """

    def __init__(
        self,
        embedding_store: "EmbeddingStore",
        embedding_generator: "EmbeddingGenerator",
        signal_store: Optional["SignalStore"] = None,
        batch_size: int = 50,
    ):
        """
        Initialize batch job.

        Args:
            embedding_store: Storage for embeddings
            embedding_generator: Generator for new embeddings
            signal_store: Optional SignalStore for getting company profiles
            batch_size: Number of embeddings to process at once
        """
        self.embedding_store = embedding_store
        self.embedding_generator = embedding_generator
        self.signal_store = signal_store
        self.batch_size = batch_size

    async def run(
        self,
        force_recompute: bool = False,
        limit: Optional[int] = None,
    ) -> BatchResult:
        """
        Run the batch job to compute/update embeddings.

        Args:
            force_recompute: If True, recompute all embeddings regardless of staleness
            limit: Maximum number of companies to process (for testing)

        Returns:
            BatchResult with statistics
        """
        from utils.profile_text_builder import ProfileTextBuilder

        result = BatchResult(started_at=datetime.now(timezone.utc))
        builder = ProfileTextBuilder()

        try:
            # Step 1: Get all companies to process
            companies = await self._get_all_companies()
            if limit:
                companies = companies[:limit]

            result.total_companies = len(companies)
            logger.info(f"Processing {result.total_companies} companies")

            if not companies:
                result.completed_at = datetime.now(timezone.utc)
                return result

            # Step 2: Build profile texts and compute hashes
            profile_texts = {}
            current_hashes = {}

            for company in companies:
                key = company["canonical_key"]
                profile_text = self._build_profile_text(company, builder)
                text_hash = builder.compute_hash(profile_text)

                profile_texts[key] = profile_text
                current_hashes[key] = text_hash

            # Step 3: Find stale/missing embeddings
            if force_recompute:
                keys_to_embed = list(current_hashes.keys())
                logger.info(f"Force recompute: {len(keys_to_embed)} embeddings")
            else:
                keys_to_embed = await self.embedding_store.get_stale_keys(current_hashes)
                logger.info(f"Found {len(keys_to_embed)} stale/missing embeddings")

            result.skipped_embeddings = len(companies) - len(keys_to_embed)

            # Step 4: Process in batches
            for i in range(0, len(keys_to_embed), self.batch_size):
                batch_keys = keys_to_embed[i : i + self.batch_size]
                batch_num = i // self.batch_size + 1
                total_batches = (len(keys_to_embed) + self.batch_size - 1) // self.batch_size

                logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch_keys)} companies)")

                batch_texts = [profile_texts[k] for k in batch_keys]

                try:
                    # Generate embeddings
                    embeddings = await self.embedding_generator.embed_batch(batch_texts)

                    # Save embeddings
                    for j, key in enumerate(batch_keys):
                        try:
                            # Check if this is new or update
                            existing = await self.embedding_store.get_embedding(key)
                            is_new = existing is None

                            preview = builder.get_preview(profile_texts[key])
                            await self.embedding_store.save_embedding(
                                canonical_key=key,
                                embedding=embeddings[j],
                                source_text_hash=current_hashes[key],
                                source_text_preview=preview,
                            )

                            if is_new:
                                result.new_embeddings += 1
                            else:
                                result.updated_embeddings += 1

                        except Exception as e:
                            result.failed_embeddings += 1
                            error_msg = f"Failed to save embedding for {key}: {e}"
                            result.errors.append(error_msg)
                            logger.error(error_msg)

                except Exception as e:
                    # Batch failed
                    result.failed_embeddings += len(batch_keys)
                    error_msg = f"Batch {batch_num} failed: {e}"
                    result.errors.append(error_msg)
                    logger.error(error_msg)

        except Exception as e:
            error_msg = f"Batch job failed: {e}"
            result.errors.append(error_msg)
            logger.error(error_msg)

        result.completed_at = datetime.now(timezone.utc)

        logger.info(
            f"Batch complete: {result.new_embeddings} new, "
            f"{result.updated_embeddings} updated, "
            f"{result.skipped_embeddings} skipped, "
            f"{result.failed_embeddings} failed "
            f"in {result.duration_seconds:.1f}s"
        )

        return result

    async def _get_all_companies(self) -> List[Dict[str, Any]]:
        """
        Get all companies that need embeddings.

        Returns:
            List of company dicts with canonical_key and profile fields
        """
        # Get all indexed profiles from the FTS table
        companies = await self.embedding_store.get_all_profiles(limit=10000)

        # If no profiles in embedding store, try signal store
        if not companies and self.signal_store:
            # Get unique canonical keys from signals
            signals = await self.signal_store.get_all_signals(limit=10000)
            seen = set()
            companies = []
            for s in signals:
                key = s.get("canonical_key") or getattr(s, "canonical_key", None)
                if key and key not in seen:
                    seen.add(key)
                    companies.append({
                        "canonical_key": key,
                        "company_name": s.get("company_name", "") or getattr(s, "company_name", ""),
                    })

        return companies

    def _build_profile_text(
        self, company: Dict[str, Any], builder: "ProfileTextBuilder"
    ) -> str:
        """
        Build profile text for embedding.

        Args:
            company: Company dict from get_all_profiles or search results
            builder: ProfileTextBuilder instance

        Returns:
            Profile text string
        """
        # Map from FTS result to profile format
        # searchable_text contains the combined profile text
        searchable = company.get("searchable_text", "")

        profile_dict = {
            "company_name": company.get("company_name", ""),
            "problem_solved": searchable,  # Best we have from FTS
            "target_customer": "",  # Not stored separately in FTS
            "business_model": company.get("business_model", ""),
            "category_hints": [company.get("category", "")] if company.get("category") else [],
        }

        return builder.build_from_dict(profile_dict)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

async def run_batch_job(
    db_path: str = "signals.db",
    force_recompute: bool = False,
    limit: Optional[int] = None,
) -> BatchResult:
    """
    Run the batch job standalone.

    Args:
        db_path: Path to database
        force_recompute: Whether to recompute all embeddings
        limit: Maximum companies to process

    Returns:
        BatchResult with statistics
    """
    from storage.embedding_store import EmbeddingStore
    from utils.embedding_generator import EmbeddingGenerator

    async with EmbeddingStore(db_path=db_path) as store:
        generator = EmbeddingGenerator()
        batch = SimilarCompaniesBatch(
            embedding_store=store,
            embedding_generator=generator,
        )
        return await batch.run(force_recompute=force_recompute, limit=limit)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Pre-compute company embeddings")
    parser.add_argument(
        "--db",
        default="signals.db",
        help="Path to database (default: signals.db)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force recompute all embeddings",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum companies to process",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run batch job
    result = asyncio.run(
        run_batch_job(
            db_path=args.db,
            force_recompute=args.force,
            limit=args.limit,
        )
    )

    print(f"\nBatch Result: {result.to_dict()}")


if __name__ == "__main__":
    main()
