"""
Curated Scout - Problem-based discovery with filter-first persistence.

Implements cache-first re-runnable discovery with thesis filtering at scale:
1. Check cache before calling Tavily (24hr TTL)
2. Tavily URL discovery
3. Ephemeral URL profiling (persist=False)
4. ThesisFilter classification for ALL candidates
5. Store thesis audit in discovery_candidates table (ALL)
6. SignalOrchestrator enrichment for survivors
7. Persist signals for qualified candidates
8. Store thesis_classifications for survivors with real signal_id

Usage:
    from discovery_engine.curated_scout import CuratedScout
    from storage.signal_store import SignalStore
    from profilers.url_profiler import URLProfiler
    from utils.thesis_filter import ThesisFilter
    from tavily import TavilyClient

    store = SignalStore()
    await store.initialize()

    scout = CuratedScout(
        signal_store=store,
        tavily_client=TavilyClient(api_key=os.getenv("TAVILY_API_KEY")),
        url_profiler=URLProfiler(signal_store=store),
        thesis_filter=ThesisFilter(),
    )

    result = await scout.discover("robotic noses for scent detection")
    print(f"Found {result['qualified_count']} qualified companies")
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from profilers.url_profiler import URLProfiler, CompanyProfile
    from utils.thesis_filter import ThesisFilter, ThesisClassification
    from discovery_engine.signal_orchestrator import SignalOrchestrator

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DiscoveryResult:
    """Result from discovery pipeline."""
    run_id: str
    query: str
    urls_discovered: int
    urls_profiled: int
    qualified_count: int
    held_count: int
    rejected_count: int
    signals_persisted: int
    cache_hit: bool
    created_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "urls_discovered": self.urls_discovered,
            "urls_profiled": self.urls_profiled,
            "qualified_count": self.qualified_count,
            "held_count": self.held_count,
            "rejected_count": self.rejected_count,
            "signals_persisted": self.signals_persisted,
            "cache_hit": self.cache_hit,
            "created_at": self.created_at.isoformat(),
        }


# =============================================================================
# CURATED SCOUT
# =============================================================================

class CuratedScout:
    """
    Problem-based discovery pipeline with filter-first persistence.

    Key features:
    - Cache-first: 24hr TTL for discovery runs
    - Ephemeral profiling: URLProfiler with persist=False
    - Thesis audit trail: ALL candidates tracked in discovery_candidates
    - Selective persistence: Only qualified candidates create signals
    - Multi-source enrichment: SignalOrchestrator for qualified survivors
    """

    DEFAULT_MAX_RESULTS = 20
    DEFAULT_CACHE_TTL_HOURS = 24
    DEFAULT_CACHE_EXPIRY_DAYS = 30

    def __init__(
        self,
        signal_store: "SignalStore",
        tavily_client: Any,  # TavilyClient
        url_profiler: "URLProfiler",
        thesis_filter: "ThesisFilter",
        signal_orchestrator: Optional["SignalOrchestrator"] = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        cache_ttl_hours: int = DEFAULT_CACHE_TTL_HOURS,
    ):
        """
        Initialize CuratedScout.

        Args:
            signal_store: Signal store for caching and persistence
            tavily_client: Tavily API client
            url_profiler: URL profiler for ephemeral profiling
            thesis_filter: Thesis filter for classification
            signal_orchestrator: Optional orchestrator for multi-source enrichment
            max_results: Max URLs to retrieve from Tavily
            cache_ttl_hours: Cache TTL in hours (default: 24)
        """
        self.signal_store = signal_store
        self.tavily_client = tavily_client
        self.url_profiler = url_profiler
        self.thesis_filter = thesis_filter
        self.signal_orchestrator = signal_orchestrator
        self.max_results = max_results
        self.cache_ttl_hours = cache_ttl_hours

    async def discover(
        self,
        query: str,
        max_age_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Execute discovery pipeline for a problem/market query.

        Args:
            query: Natural language problem/market description
            max_age_hours: Max age for cached runs (default: self.cache_ttl_hours)

        Returns:
            DiscoveryResult dict with stats

        Example:
            >>> result = await scout.discover("AI-powered fitness coaching apps")
            >>> print(f"Found {result['qualified_count']} qualified companies")
        """
        max_age_hours = max_age_hours or self.cache_ttl_hours
        now = datetime.now(timezone.utc)

        logger.info(f"Starting discovery for query: {query}")

        # Step 1: Check cache
        run_id, cache_hit = await self._get_or_create_discovery_run(query, max_age_hours)

        if cache_hit:
            logger.info(f"Cache hit for query '{query}' (run_id={run_id})")
            # Return cached results
            stats = await self._get_cached_stats(run_id)
            return stats

        logger.info(f"Cache miss - executing new discovery (run_id={run_id})")

        # Step 2: Tavily URL discovery
        urls = await self._search_tavily(query)
        logger.info(f"Tavily returned {len(urls)} URLs")

        if not urls:
            return DiscoveryResult(
                run_id=run_id,
                query=query,
                urls_discovered=0,
                urls_profiled=0,
                qualified_count=0,
                held_count=0,
                rejected_count=0,
                signals_persisted=0,
                cache_hit=False,
                created_at=now,
            ).to_dict()

        # Step 3: Ephemeral URL profiling (persist=False)
        profiles = await self._profile_urls_ephemeral(urls)
        logger.info(f"Profiled {len(profiles)} URLs")

        # Step 4: ThesisFilter classification for ALL candidates
        candidates = []
        for profile in profiles:
            # Build profile text for thesis classification
            profile_text = f"{profile.domain} " + " ".join([
                claim.value for claim in profile.claims if hasattr(claim, 'value')
            ])

            # Classify
            classification = await self.thesis_filter.classify(profile_text)

            candidates.append({
                "url": profile.source_urls[0] if profile.source_urls else f"https://{profile.domain}",
                "canonical_key": profile.canonical_key,
                "classification": classification,
                "routing": classification.routing,
                "profile": profile,
            })

        # Step 5: Save thesis audit for ALL candidates
        await self._save_thesis_audit(run_id, candidates)

        # Step 6: Enrich and persist qualified candidates
        qualified = [c for c in candidates if c["routing"] == "qualified"]
        held = [c for c in candidates if c["routing"] == "held"]
        rejected = [c for c in candidates if c["routing"] == "rejected"]

        signals_persisted = 0
        if qualified:
            signals_persisted = await self._enrich_and_persist_qualified(qualified)

        logger.info(f"Discovery complete: {len(qualified)} qualified, {len(held)} held, {len(rejected)} rejected")

        result = DiscoveryResult(
            run_id=run_id,
            query=query,
            urls_discovered=len(urls),
            urls_profiled=len(profiles),
            qualified_count=len(qualified),
            held_count=len(held),
            rejected_count=len(rejected),
            signals_persisted=signals_persisted,
            cache_hit=False,
            created_at=now,
        )

        return result.to_dict()

    async def _get_or_create_discovery_run(
        self,
        query: str,
        max_age_hours: int,
    ) -> tuple[str, bool]:
        """
        Get cached discovery run or create new one.

        Args:
            query: Query string
            max_age_hours: Max age for cache validity

        Returns:
            (run_id, cache_hit) tuple
        """
        now = datetime.now(timezone.utc)
        min_created_at = now - timedelta(hours=max_age_hours)

        # Check for existing valid run
        async with self.signal_store.transaction() as conn:
            cursor = await conn.execute(
                """
                SELECT run_id FROM discovery_runs
                WHERE query = ?
                  AND created_at >= ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (query, min_created_at.isoformat(), now.isoformat())
            )
            row = await cursor.fetchone()

        if row:
            return row[0], True  # Cache hit

        # Create new run
        run_id = str(uuid.uuid4())
        expires_at = now + timedelta(days=self.DEFAULT_CACHE_EXPIRY_DAYS)

        async with self.signal_store.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO discovery_runs (run_id, query, source, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, query, "tavily", now.isoformat(), expires_at.isoformat())
            )
            await conn.commit()

        return run_id, False  # Cache miss

    async def _search_tavily(self, query: str) -> List[str]:
        """
        Search Tavily for URLs matching query.

        Args:
            query: Search query

        Returns:
            List of URLs
        """
        try:
            result = await self.tavily_client.search(
                query=query,
                max_results=self.max_results,
            )
            urls = [r["url"] for r in result.get("results", [])]
            return urls
        except Exception as e:
            logger.error(f"Tavily search failed: {e}")
            return []

    async def _profile_urls_ephemeral(
        self,
        urls: List[str],
    ) -> List["CompanyProfile"]:
        """
        Profile URLs ephemerally (persist=False).

        Args:
            urls: List of URLs to profile

        Returns:
            List of CompanyProfile objects
        """
        profiles = []

        for url in urls:
            try:
                profile = await self.url_profiler.profile(url, persist=False)
                profiles.append(profile)
            except Exception as e:
                logger.warning(f"Failed to profile {url}: {e}")
                continue

        return profiles

    async def _save_thesis_audit(
        self,
        run_id: str,
        candidates: List[Dict[str, Any]],
    ) -> None:
        """
        Save thesis audit trail for ALL candidates.

        Args:
            run_id: Discovery run ID
            candidates: List of candidate dicts with classification
        """
        now = datetime.now(timezone.utc)

        async with self.signal_store.transaction() as conn:
            for candidate in candidates:
                classification = candidate["classification"]

                await conn.execute(
                    """
                    INSERT INTO discovery_candidates (
                        run_id, url, canonical_key,
                        keyword_score, keyword_category, negative_keywords,
                        llm_score, llm_category, llm_rationale,
                        routing, rejection_reason,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        candidate["url"],
                        candidate["canonical_key"],
                        classification.keyword_score,
                        classification.keyword_category,
                        json.dumps(classification.negative_keywords),
                        classification.llm_score,
                        classification.llm_category,
                        classification.llm_rationale,
                        classification.routing,
                        classification.rejection_reason,
                        now.isoformat(),
                    )
                )

            await conn.commit()

    async def _enrich_and_persist_qualified(
        self,
        qualified_candidates: List[Dict[str, Any]],
    ) -> int:
        """
        Enrich qualified candidates with SignalOrchestrator and persist signals.

        Args:
            qualified_candidates: List of qualified candidate dicts

        Returns:
            Number of signals persisted
        """
        if not self.signal_orchestrator:
            logger.warning("No SignalOrchestrator available for enrichment")
            return 0

        signals_persisted = 0

        for candidate in qualified_candidates:
            try:
                canonical_key = candidate["canonical_key"]

                # Enrich with orchestrator (will create signals)
                enrichment_result = await self.signal_orchestrator.enrich(canonical_key)

                signals_persisted += enrichment_result.get("signal_count", 0)

            except Exception as e:
                logger.error(f"Failed to enrich {candidate['canonical_key']}: {e}")
                continue

        return signals_persisted

    async def _get_cached_stats(self, run_id: str) -> Dict[str, Any]:
        """
        Get cached stats for a discovery run.

        Args:
            run_id: Discovery run ID

        Returns:
            DiscoveryResult dict
        """
        async with self.signal_store.transaction() as conn:
            # Get run info
            cursor = await conn.execute(
                "SELECT query, created_at FROM discovery_runs WHERE run_id = ?",
                (run_id,)
            )
            run_row = await cursor.fetchone()

            if not run_row:
                raise ValueError(f"Run {run_id} not found")

            # Get candidate counts by routing
            cursor = await conn.execute(
                """
                SELECT routing, COUNT(*) as count
                FROM discovery_candidates
                WHERE run_id = ?
                GROUP BY routing
                """,
                (run_id,)
            )
            routing_counts = {row[0]: row[1] for row in await cursor.fetchall()}

        qualified_count = routing_counts.get("qualified", 0)
        held_count = routing_counts.get("held", 0)
        rejected_count = routing_counts.get("rejected", 0)
        total_count = qualified_count + held_count + rejected_count

        return {
            "run_id": run_id,
            "query": run_row[0],
            "urls_discovered": total_count,
            "urls_profiled": total_count,
            "qualified_count": qualified_count,
            "held_count": held_count,
            "rejected_count": rejected_count,
            "signals_persisted": 0,  # Not tracked for cached runs
            "cache_hit": True,
            "created_at": run_row[1],
        }

    async def purge_expired_cache(self) -> int:
        """
        Purge expired discovery cache entries.

        Returns:
            Number of runs purged
        """
        now = datetime.now(timezone.utc)

        async with self.signal_store.transaction() as conn:
            # Delete expired runs (cascades to candidates)
            cursor = await conn.execute(
                "DELETE FROM discovery_runs WHERE expires_at < ?",
                (now.isoformat(),)
            )
            await conn.commit()
            purged_count = cursor.rowcount

        logger.info(f"Purged {purged_count} expired discovery runs")
        return purged_count
