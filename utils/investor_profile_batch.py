"""
Investor Profile Batch Job

Nightly job to:
1. Compute global baselines from portfolio/claims data
2. Refresh investor profile claims with lift scores
3. Update investor profile distributions
4. Rebuild FTS index

Run via: python -m utils.investor_profile_batch

Sprint 5: Investor Matching v1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# BATCH RESULT
# =============================================================================

@dataclass
class BatchResult:
    """Result of running the batch job."""
    total_investors: int = 0
    profiles_updated: int = 0
    claims_refreshed: int = 0
    baselines_computed: int = 0
    fts_entries_created: int = 0
    cold_start_count: int = 0
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
            "total_investors": self.total_investors,
            "profiles_updated": self.profiles_updated,
            "claims_refreshed": self.claims_refreshed,
            "baselines_computed": self.baselines_computed,
            "fts_entries_created": self.fts_entries_created,
            "cold_start_count": self.cold_start_count,
            "error_count": len(self.errors),
            "duration_seconds": self.duration_seconds,
        }


# =============================================================================
# BATCH JOB
# =============================================================================

class InvestorProfileBatch:
    """
    Batch job for investor profile maintenance.

    Designed to run nightly to:
    - Compute global baselines (P(predicate=value) across all companies)
    - Refresh investor profile claims with lift scores
    - Update investor profile distributions
    - Rebuild FTS index for fast matching
    """

    # Global baseline configuration
    BASELINE_SAMPLE_SOURCES = {
        'portfolio_all': {
            'description': 'All companies in any investor portfolio',
        },
        'signals_90d': {
            'description': 'Companies from signals in last 90 days',
        },
    }

    BASELINE_PREDICATES = [
        'sector',         # fintech, health, cpg, etc.
        'stage',          # pre_seed, seed, series_a, etc.
        'geo',            # US, UK, EU, etc.
        'business_model', # b2c, marketplace, subscription, etc.
    ]

    # Lift threshold: only keep claims where lift > this value
    LIFT_THRESHOLD = 0.1  # log-odds units

    # Cold-start threshold
    COLD_START_THRESHOLD = 3

    # Epsilon for log calculations
    EPS = 1e-6

    def __init__(self, store: "SignalStore"):
        """
        Initialize batch job.

        Args:
            store: SignalStore instance for database access
        """
        self._store = store

    async def run(
        self,
        force_refresh: bool = False,
    ) -> BatchResult:
        """
        Execute full batch job.

        Args:
            force_refresh: If True, refresh all profiles regardless of staleness

        Returns:
            BatchResult with statistics
        """
        result = BatchResult(started_at=datetime.now(timezone.utc))

        try:
            # Step 1: Compute global baselines
            baselines = await self._compute_global_baselines()
            result.baselines_computed = baselines

            # Step 2: Refresh profile claims for all investors
            claims, profiles, cold = await self._refresh_all_profiles()
            result.claims_refreshed = claims
            result.profiles_updated = profiles
            result.cold_start_count = cold

            # Step 3: Rebuild FTS index
            fts = await self._rebuild_fts_index()
            result.fts_entries_created = fts

            # Get total investor count
            result.total_investors = await self._count_investors()

            result.completed_at = datetime.now(timezone.utc)
            logger.info(f"Batch complete: {result.to_dict()}")

        except Exception as e:
            result.errors.append(str(e))
            result.completed_at = datetime.now(timezone.utc)
            logger.exception(f"Batch job failed: {e}")

        return result

    async def _count_investors(self) -> int:
        """Count total investors in database."""
        if not self._store._db:
            return 0

        cursor = await self._store._db.execute(
            "SELECT COUNT(*) FROM investors"
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def _compute_global_baselines(self) -> int:
        """
        Compute P(predicate=value) across the global company population.

        Returns:
            Number of baselines computed
        """
        logger.info("Computing global baselines...")

        if not self._store._db:
            raise RuntimeError("Database not initialized")

        count = 0

        # Source 1: Portfolio companies
        count += await self._compute_baselines_from_portfolios()

        # Source 2: Recent signals
        count += await self._compute_baselines_from_signals()

        logger.info(f"Computed {count} baselines")
        return count

    async def _compute_baselines_from_portfolios(self) -> int:
        """Compute baselines from portfolio company claims."""
        count = 0

        # Get total distinct companies in portfolios
        cursor = await self._store._db.execute(
            "SELECT COUNT(DISTINCT company_key) FROM investor_portfolios"
        )
        row = await cursor.fetchone()
        total_n = row[0] if row else 0

        if total_n == 0:
            logger.debug("No portfolio companies found for baselines")
            return 0

        for predicate in self.BASELINE_PREDICATES:
            # Count companies by predicate value
            # Join portfolios with claims to get predicate values
            cursor = await self._store._db.execute(
                """
                SELECT c.value, COUNT(DISTINCT ip.company_key) as cnt
                FROM investor_portfolios ip
                JOIN claims c ON ip.company_key = c.entity_key
                WHERE c.predicate = ? AND c.status = 'active'
                GROUP BY c.value
                """,
                (predicate,),
            )
            rows = await cursor.fetchall()

            for value, value_count in rows:
                probability = value_count / total_n

                await self._store.save_global_baseline(
                    predicate=predicate,
                    value=value,
                    global_probability=probability,
                    sample_size=total_n,
                    sample_source='portfolio_all',
                )
                count += 1

        return count

    async def _compute_baselines_from_signals(self) -> int:
        """Compute baselines from recent signal claims."""
        count = 0

        # Get total distinct companies from signals in last 90 days
        cursor = await self._store._db.execute(
            """
            SELECT COUNT(DISTINCT canonical_key)
            FROM signals
            WHERE created_at > datetime('now', '-90 days')
            """
        )
        row = await cursor.fetchone()
        total_n = row[0] if row else 0

        if total_n == 0:
            logger.debug("No recent signals found for baselines")
            return 0

        for predicate in self.BASELINE_PREDICATES:
            # Count by predicate value from claims joined with signals
            cursor = await self._store._db.execute(
                """
                SELECT c.value, COUNT(DISTINCT s.canonical_key) as cnt
                FROM signals s
                JOIN claims c ON s.canonical_key = c.entity_key
                WHERE s.created_at > datetime('now', '-90 days')
                  AND c.predicate = ?
                  AND c.status = 'active'
                GROUP BY c.value
                """,
                (predicate,),
            )
            rows = await cursor.fetchall()

            for value, value_count in rows:
                probability = value_count / total_n

                await self._store.save_global_baseline(
                    predicate=predicate,
                    value=value,
                    global_probability=probability,
                    sample_size=total_n,
                    sample_source='signals_90d',
                )
                count += 1

        return count

    async def _compute_lift_score(
        self,
        predicate: str,
        value: str,
        investor_probability: float,
    ) -> float:
        """
        Compute lift score: log( P(value|investor) / P(value|global) )

        Positive lift = investor overweights this value
        Negative lift = investor underweights this value
        """
        global_prob = await self._store.get_global_baseline(
            predicate=predicate,
            value=value,
            sample_source='portfolio_all',
        )

        if global_prob is None:
            global_prob = self.EPS

        lift = math.log(
            (investor_probability + self.EPS) / (global_prob + self.EPS)
        )
        return lift

    async def _refresh_all_profiles(self) -> tuple[int, int, int]:
        """
        Refresh profile claims and distributions for all investors.

        Returns:
            Tuple of (claims_refreshed, profiles_updated, cold_start_count)
        """
        logger.info("Refreshing investor profiles...")

        if not self._store._db:
            raise RuntimeError("Database not initialized")

        # Get all investors with portfolio counts
        cursor = await self._store._db.execute(
            """
            SELECT i.id, COUNT(ip.id) as portfolio_size
            FROM investors i
            LEFT JOIN investor_portfolios ip ON i.id = ip.investor_id
            GROUP BY i.id
            """
        )
        investors = await cursor.fetchall()

        claims_count = 0
        profiles_count = 0
        cold_start_count = 0

        for investor_id, portfolio_size in investors:
            is_cold_start = portfolio_size < self.COLD_START_THRESHOLD

            if is_cold_start:
                cold_start_count += 1
                # Still update profile with cold_start flag
                await self._update_investor_profile(
                    investor_id=investor_id,
                    portfolio_count=portfolio_size,
                    is_cold_start=True,
                    stage_distribution={},
                    sector_distribution={},
                    geo_distribution={},
                )
                profiles_count += 1
                continue

            # Refresh claims and get distributions
            claims, distributions = await self._refresh_investor_claims(
                investor_id, portfolio_size
            )
            claims_count += claims

            # Update profile
            await self._update_investor_profile(
                investor_id=investor_id,
                portfolio_count=portfolio_size,
                is_cold_start=False,
                stage_distribution=distributions.get('stage', {}),
                sector_distribution=distributions.get('sector', {}),
                geo_distribution=distributions.get('geo', {}),
            )
            profiles_count += 1

        logger.info(
            f"Updated {profiles_count} profiles, "
            f"{claims_count} claims, "
            f"{cold_start_count} cold-start"
        )
        return claims_count, profiles_count, cold_start_count

    async def _refresh_investor_claims(
        self,
        investor_id: str,
        portfolio_size: int,
    ) -> tuple[int, Dict[str, Dict[str, float]]]:
        """
        Generate profile claims for a single investor from portfolio behavior.

        Returns:
            Tuple of (claims_count, distributions_by_predicate)
        """
        claims_count = 0
        distributions: Dict[str, Dict[str, float]] = {}

        for predicate in self.BASELINE_PREDICATES:
            # Count portfolio companies by predicate value
            cursor = await self._store._db.execute(
                """
                SELECT c.value, COUNT(DISTINCT ip.company_key) as cnt
                FROM investor_portfolios ip
                JOIN claims c ON ip.company_key = c.entity_key
                WHERE ip.investor_id = ? AND c.predicate = ? AND c.status = 'active'
                GROUP BY c.value
                """,
                (investor_id, predicate),
            )
            rows = await cursor.fetchall()

            predicate_dist: Dict[str, float] = {}

            for value, count in rows:
                investor_prob = count / portfolio_size
                predicate_dist[value] = round(investor_prob, 3)

                lift = await self._compute_lift_score(predicate, value, investor_prob)

                # Only save claims with significant lift
                if lift >= self.LIFT_THRESHOLD:
                    # Build evidence
                    evidence_cursor = await self._store._db.execute(
                        """
                        SELECT ip.company_key, ip.extraction_id
                        FROM investor_portfolios ip
                        JOIN claims c ON ip.company_key = c.entity_key
                        WHERE ip.investor_id = ?
                          AND c.predicate = ?
                          AND c.value = ?
                          AND c.status = 'active'
                        LIMIT 5
                        """,
                        (investor_id, predicate, value),
                    )
                    evidence_rows = await evidence_cursor.fetchall()
                    evidence = [
                        {"company_key": r[0], "extraction_id": r[1]}
                        for r in evidence_rows
                    ]

                    # Convert lift to confidence (0.5 + lift * 0.1, capped at 0.95)
                    confidence = min(0.5 + lift * 0.1, 0.95)

                    await self._store.save_investor_profile_claim(
                        investor_id=investor_id,
                        predicate=f"{predicate}_preference",
                        value=value,
                        confidence=confidence,
                        lift_score=lift,
                        support_count=count,
                        support_evidence=evidence,
                        status='active',
                    )
                    claims_count += 1

            if predicate_dist:
                distributions[predicate] = predicate_dist

        return claims_count, distributions

    async def _update_investor_profile(
        self,
        investor_id: str,
        portfolio_count: int,
        is_cold_start: bool,
        stage_distribution: Dict[str, float],
        sector_distribution: Dict[str, float],
        geo_distribution: Dict[str, float],
    ) -> None:
        """Update the cached investor profile."""
        if not self._store._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()

        # Count active claims
        cursor = await self._store._db.execute(
            """
            SELECT COUNT(*) FROM investor_profile_claims
            WHERE investor_id = ? AND status = 'active'
            """,
            (investor_id,),
        )
        row = await cursor.fetchone()
        active_claim_count = row[0] if row else 0

        # Compute lead rate
        lead_cursor = await self._store._db.execute(
            """
            SELECT
                CAST(SUM(CASE WHEN is_lead = 1 THEN 1 ELSE 0 END) AS FLOAT) /
                NULLIF(COUNT(*), 0)
            FROM investor_portfolios
            WHERE investor_id = ?
            """,
            (investor_id,),
        )
        lead_row = await lead_cursor.fetchone()
        lead_rate = lead_row[0] if lead_row and lead_row[0] else 0.0

        await self._store._db.execute(
            """
            INSERT INTO investor_profiles (
                investor_id, stage_distribution, sector_distribution, geo_distribution,
                portfolio_count, active_claim_count, is_cold_start, lead_rate, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(investor_id) DO UPDATE SET
                stage_distribution = excluded.stage_distribution,
                sector_distribution = excluded.sector_distribution,
                geo_distribution = excluded.geo_distribution,
                portfolio_count = excluded.portfolio_count,
                active_claim_count = excluded.active_claim_count,
                is_cold_start = excluded.is_cold_start,
                lead_rate = excluded.lead_rate,
                updated_at = excluded.updated_at
            """,
            (
                investor_id,
                json.dumps(stage_distribution),
                json.dumps(sector_distribution),
                json.dumps(geo_distribution),
                portfolio_count,
                active_claim_count,
                1 if is_cold_start else 0,
                lead_rate,
                now,
            ),
        )
        await self._store._db.commit()

    async def _rebuild_fts_index(self) -> int:
        """
        Rebuild FTS5 index from profile claims.

        Returns:
            Number of FTS entries created
        """
        logger.info("Rebuilding FTS index...")

        if not self._store._db:
            raise RuntimeError("Database not initialized")

        # Clear existing
        await self._store._db.execute("DELETE FROM investor_profile_fts")

        # Rebuild from claims
        cursor = await self._store._db.execute(
            """
            SELECT investor_id, GROUP_CONCAT(predicate || ':' || value, ' ') as claim_text
            FROM investor_profile_claims
            WHERE status = 'active'
            GROUP BY investor_id
            """
        )
        rows = await cursor.fetchall()

        count = 0
        for investor_id, claim_text in rows:
            if claim_text:
                await self._store._db.execute(
                    """
                    INSERT INTO investor_profile_fts (investor_id, claim_text)
                    VALUES (?, ?)
                    """,
                    (investor_id, claim_text),
                )
                count += 1

        await self._store._db.commit()
        logger.info(f"Created {count} FTS entries")
        return count


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

async def run_batch_job(db_path: str | None = None) -> BatchResult:
    """
    Run the batch job standalone.

    Args:
        db_path: Path to signals database

    Returns:
        BatchResult with statistics
    """
    from storage.signal_store import SignalStore

    store = SignalStore(db_path)
    await store.initialize()

    try:
        batch = InvestorProfileBatch(store)
        result = await batch.run()
        return result
    finally:
        await store.close()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Run investor profile batch job"
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to signals database (default: canonical DISCOVERY_DB_PATH)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run batch
    result = asyncio.run(run_batch_job(args.db_path))

    # Print summary
    print("\n=== Investor Profile Batch Complete ===")
    print(f"Total investors: {result.total_investors}")
    print(f"Profiles updated: {result.profiles_updated}")
    print(f"Claims refreshed: {result.claims_refreshed}")
    print(f"Baselines computed: {result.baselines_computed}")
    print(f"FTS entries: {result.fts_entries_created}")
    print(f"Cold-start investors: {result.cold_start_count}")
    print(f"Duration: {result.duration_seconds:.2f}s")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
