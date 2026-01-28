"""
Tests for CuratedScout - problem-based discovery pipeline.

TDD: Write failing tests first, then implement.

CuratedScout flow:
1. Check cache before calling Tavily (24hr TTL)
2. Tavily URL discovery
3. Ephemeral URL profiling (persist=False)
4. ThesisFilter classification for ALL candidates
5. Store thesis audit in discovery_candidates table (ALL)
6. SignalOrchestrator enrichment for survivors
7. Persist signals for qualified candidates
8. Store thesis_classifications for survivors with real signal_id
"""

import pytest
import tempfile
import os
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, Any, List


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestCuratedScoutInit:
    """Tests for CuratedScout initialization."""

    def test_init_with_minimal_dependencies(self):
        """CuratedScout should initialize with required dependencies."""
        from discovery_engine.curated_scout import CuratedScout

        mock_store = Mock()
        mock_tavily = Mock()
        mock_url_profiler = Mock()
        mock_thesis_filter = Mock()

        scout = CuratedScout(
            signal_store=mock_store,
            tavily_client=mock_tavily,
            url_profiler=mock_url_profiler,
            thesis_filter=mock_thesis_filter,
        )

        assert scout.signal_store == mock_store
        assert scout.tavily_client == mock_tavily
        assert scout.url_profiler == mock_url_profiler
        assert scout.thesis_filter == mock_thesis_filter


class TestCuratedScoutCacheLogic:
    """Tests for discovery cache and TTL logic."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_existing_run(self, temp_db):
        """If valid cached run exists, should return it without calling Tavily."""
        from discovery_engine.curated_scout import CuratedScout
        from storage.signal_store import SignalStore

        store = SignalStore(temp_db)
        await store.initialize()

        # Create a cached run that's still valid
        run_id = str(uuid.uuid4())
        query = "robotic noses for scent detection"
        expires_at = datetime.now(timezone.utc) + timedelta(hours=12)

        # Mock insert into discovery_runs table
        async with store.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO discovery_runs (run_id, query, source, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, query, "tavily", datetime.now(timezone.utc).isoformat(), expires_at.isoformat())
            )
            await conn.commit()

        mock_tavily = Mock()
        mock_tavily.search = AsyncMock()  # Should NOT be called

        scout = CuratedScout(
            signal_store=store,
            tavily_client=mock_tavily,
            url_profiler=Mock(),
            thesis_filter=Mock(),
        )

        # Should find cached run and not call Tavily
        cached_run_id = await scout._get_or_create_discovery_run(query, max_age_hours=24)

        assert cached_run_id == run_id
        mock_tavily.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_tavily(self, temp_db):
        """If no valid cache, should call Tavily and create new run."""
        from discovery_engine.curated_scout import CuratedScout
        from storage.signal_store import SignalStore

        store = SignalStore(temp_db)
        await store.initialize()

        mock_tavily = Mock()
        mock_tavily.search = AsyncMock(return_value={
            "results": [{"url": "https://example.com"}]
        })

        scout = CuratedScout(
            signal_store=store,
            tavily_client=mock_tavily,
            url_profiler=Mock(),
            thesis_filter=Mock(),
        )

        query = "AI fitness coaching"
        run_id = await scout._get_or_create_discovery_run(query, max_age_hours=24)

        # Should have created new run
        assert run_id is not None
        assert isinstance(run_id, str)

    @pytest.mark.asyncio
    async def test_expired_cache_ignored(self, temp_db):
        """Expired cached run should be ignored, new run created."""
        from discovery_engine.curated_scout import CuratedScout
        from storage.signal_store import SignalStore

        store = SignalStore(temp_db)
        await store.initialize()

        # Create an expired run
        old_run_id = str(uuid.uuid4())
        query = "expired query"
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)  # Already expired

        async with store.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO discovery_runs (run_id, query, source, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (old_run_id, query, "tavily", datetime.now(timezone.utc).isoformat(), expires_at.isoformat())
            )
            await conn.commit()

        mock_tavily = Mock()
        mock_tavily.search = AsyncMock(return_value={"results": []})

        scout = CuratedScout(
            signal_store=store,
            tavily_client=mock_tavily,
            url_profiler=Mock(),
            thesis_filter=Mock(),
        )

        new_run_id = await scout._get_or_create_discovery_run(query, max_age_hours=24)

        # Should have created NEW run (not reused expired one)
        assert new_run_id != old_run_id


class TestCuratedScoutTavilyIntegration:
    """Tests for Tavily API integration."""

    @pytest.mark.asyncio
    async def test_tavily_search_extracts_urls(self):
        """Should extract URLs from Tavily search results."""
        from discovery_engine.curated_scout import CuratedScout

        mock_tavily = Mock()
        mock_tavily.search = AsyncMock(return_value={
            "results": [
                {"url": "https://company1.com", "title": "Company 1"},
                {"url": "https://company2.com", "title": "Company 2"},
                {"url": "https://company3.com", "title": "Company 3"},
            ]
        })

        scout = CuratedScout(
            signal_store=Mock(),
            tavily_client=mock_tavily,
            url_profiler=Mock(),
            thesis_filter=Mock(),
        )

        urls = await scout._search_tavily("test query")

        assert len(urls) == 3
        assert "https://company1.com" in urls
        assert "https://company2.com" in urls
        assert "https://company3.com" in urls

    @pytest.mark.asyncio
    async def test_tavily_search_handles_empty_results(self):
        """Should handle empty Tavily results gracefully."""
        from discovery_engine.curated_scout import CuratedScout

        mock_tavily = Mock()
        mock_tavily.search = AsyncMock(return_value={"results": []})

        scout = CuratedScout(
            signal_store=Mock(),
            tavily_client=mock_tavily,
            url_profiler=Mock(),
            thesis_filter=Mock(),
        )

        urls = await scout._search_tavily("nonexistent niche")

        assert urls == []


class TestCuratedScoutEphemeralProfiling:
    """Tests for ephemeral URL profiling (persist=False)."""

    @pytest.mark.asyncio
    async def test_profile_urls_with_persist_false(self):
        """Should call URLProfiler with persist=False."""
        from discovery_engine.curated_scout import CuratedScout
        from profilers.url_profiler import CompanyProfile

        mock_profiler = Mock()
        mock_profiler.profile = AsyncMock(return_value=CompanyProfile(
            canonical_key="domain:test.com",
            domain="test.com",
            claims=[],
            extraction_result=None,
            source_urls=[],
            pages_fetched=[],
            profile_complete=False,
            last_profiled_at=datetime.now(timezone.utc),
        ))

        scout = CuratedScout(
            signal_store=Mock(),
            tavily_client=Mock(),
            url_profiler=mock_profiler,
            thesis_filter=Mock(),
        )

        urls = ["https://test.com"]
        await scout._profile_urls_ephemeral(urls)

        # Verify profile was called with persist=False
        mock_profiler.profile.assert_called_once()
        call_kwargs = mock_profiler.profile.call_args.kwargs
        assert call_kwargs.get("persist") == False


class TestCuratedScoutThesisAudit:
    """Tests for thesis filter integration and audit trail."""

    @pytest.mark.asyncio
    async def test_thesis_audit_for_all_candidates(self, temp_db):
        """Should store thesis audit in discovery_candidates for ALL candidates."""
        from discovery_engine.curated_scout import CuratedScout
        from storage.signal_store import SignalStore
        from utils.thesis_filter import ThesisClassification

        store = SignalStore(temp_db)
        await store.initialize()

        # Mock thesis filter to return different results
        mock_filter = Mock()
        mock_filter.classify = AsyncMock(side_effect=[
            ThesisClassification(
                keyword_score=0.8,
                keyword_category="Consumer CPG",
                negative_keywords=[],
                llm_score=0.85,
                llm_category="Consumer CPG",
                llm_rationale="Food delivery platform",
                thesis_fit=0.825,
                routing="qualified",
            ),
            ThesisClassification(
                keyword_score=0.1,
                keyword_category="excluded",
                negative_keywords=["enterprise", "b2b"],
                llm_score=0.0,
                llm_category="excluded",
                llm_rationale="B2B enterprise software",
                thesis_fit=0.05,
                routing="rejected",
                rejection_reason="B2B enterprise focus",
            ),
        ])

        scout = CuratedScout(
            signal_store=store,
            tavily_client=Mock(),
            url_profiler=Mock(),
            thesis_filter=mock_filter,
        )

        run_id = str(uuid.uuid4())
        candidates = [
            {"url": "https://foodco.com", "canonical_key": "domain:foodco.com"},
            {"url": "https://enterprisesaas.com", "canonical_key": "domain:enterprisesaas.com"},
        ]

        await scout._save_thesis_audit(run_id, candidates)

        # Verify both candidates are in discovery_candidates table
        async with store.transaction() as conn:
            cursor = await conn.execute(
                "SELECT url, routing, rejection_reason FROM discovery_candidates WHERE run_id = ?",
                (run_id,)
            )
            rows = await cursor.fetchall()

        assert len(rows) == 2

        # Check qualified candidate
        qualified = [r for r in rows if r[0] == "https://foodco.com"][0]
        assert qualified[1] == "qualified"
        assert qualified[2] is None

        # Check rejected candidate
        rejected = [r for r in rows if r[0] == "https://enterprisesaas.com"][0]
        assert rejected[1] == "rejected"
        assert rejected[2] == "B2B enterprise focus"


class TestCuratedScoutSignalPersistence:
    """Tests for selective signal persistence (only qualified candidates)."""

    @pytest.mark.asyncio
    async def test_persist_only_qualified_candidates(self):
        """Should persist signals only for qualified candidates, not rejected ones."""
        from discovery_engine.curated_scout import CuratedScout

        mock_orchestrator = Mock()
        mock_orchestrator.enrich = AsyncMock(return_value={"signal_count": 3})

        scout = CuratedScout(
            signal_store=Mock(),
            tavily_client=Mock(),
            url_profiler=Mock(),
            thesis_filter=Mock(),
            signal_orchestrator=mock_orchestrator,
        )

        # Mix of qualified and rejected candidates
        candidates = [
            {"canonical_key": "domain:good1.com", "routing": "qualified"},
            {"canonical_key": "domain:bad1.com", "routing": "rejected"},
            {"canonical_key": "domain:good2.com", "routing": "qualified"},
            {"canonical_key": "domain:held1.com", "routing": "held"},
        ]

        await scout._enrich_and_persist_qualified(candidates)

        # Should only call orchestrator for qualified candidates
        assert mock_orchestrator.enrich.call_count == 2


class TestCuratedScoutFullPipeline:
    """Integration tests for full discovery pipeline."""

    @pytest.mark.asyncio
    async def test_full_pipeline_end_to_end(self, temp_db):
        """Should execute full pipeline: cache check → Tavily → profile → thesis → persist."""
        from discovery_engine.curated_scout import CuratedScout
        from storage.signal_store import SignalStore
        from profilers.url_profiler import CompanyProfile
        from utils.thesis_filter import ThesisClassification

        store = SignalStore(temp_db)
        await store.initialize()

        # Mock dependencies
        mock_tavily = Mock()
        mock_tavily.search = AsyncMock(return_value={
            "results": [{"url": "https://fooddelivery.com"}]
        })

        mock_profiler = Mock()
        mock_profiler.profile = AsyncMock(return_value=CompanyProfile(
            canonical_key="domain:fooddelivery.com",
            domain="fooddelivery.com",
            claims=[],
            extraction_result=None,
            source_urls=[],
            pages_fetched=[],
            profile_complete=False,
            last_profiled_at=datetime.now(timezone.utc),
        ))

        mock_filter = Mock()
        mock_filter.classify = AsyncMock(return_value=ThesisClassification(
            keyword_score=0.75,
            keyword_category="Consumer CPG",
            negative_keywords=[],
            llm_score=0.80,
            llm_category="Consumer CPG",
            llm_rationale="Food delivery",
            thesis_fit=0.775,
            routing="qualified",
        ))

        scout = CuratedScout(
            signal_store=store,
            tavily_client=mock_tavily,
            url_profiler=mock_profiler,
            thesis_filter=mock_filter,
        )

        # Run discovery
        result = await scout.discover("food delivery platforms")

        # Verify pipeline executed
        assert result is not None
        assert "run_id" in result
        mock_tavily.search.assert_called_once()
        mock_profiler.profile.assert_called_once()
        mock_filter.classify.assert_called_once()

    @pytest.mark.asyncio
    async def test_persistence_ratio_under_10_percent(self, temp_db):
        """Should persist ≤10% of profiled URLs (acceptance criteria)."""
        from discovery_engine.curated_scout import CuratedScout
        from storage.signal_store import SignalStore
        from profilers.url_profiler import CompanyProfile
        from utils.thesis_filter import ThesisClassification

        store = SignalStore(temp_db)
        await store.initialize()

        # Mock 100 URLs, but thesis filter rejects 92% (only 8% qualified)
        mock_tavily = Mock()
        urls = [f"https://company{i}.com" for i in range(100)]
        mock_tavily.search = AsyncMock(return_value={
            "results": [{"url": url} for url in urls]
        })

        mock_profiler = Mock()

        def profile_side_effect(url, persist=True):
            domain = url.replace("https://", "").replace("/", "")
            return CompanyProfile(
                canonical_key=f"domain:{domain}",
                domain=domain,
                claims=[],
                extraction_result=None,
                source_urls=[],
                pages_fetched=[],
                profile_complete=False,
                last_profiled_at=datetime.now(timezone.utc),
            )

        mock_profiler.profile = AsyncMock(side_effect=profile_side_effect)

        # Thesis filter: 8% qualified, 92% rejected
        def thesis_side_effect(profile_text):
            # Simple logic: company0-7 are qualified, rest rejected
            if any(f"company{i}" in profile_text for i in range(8)):
                return ThesisClassification(
                    keyword_score=0.7,
                    keyword_category="Consumer CPG",
                    negative_keywords=[],
                    llm_score=0.75,
                    llm_category="Consumer CPG",
                    llm_rationale="Consumer focus",
                    thesis_fit=0.725,
                    routing="qualified",
                )
            else:
                return ThesisClassification(
                    keyword_score=0.1,
                    keyword_category="excluded",
                    negative_keywords=["enterprise"],
                    llm_score=0.0,
                    llm_category="excluded",
                    llm_rationale="Not consumer",
                    thesis_fit=0.05,
                    routing="rejected",
                    rejection_reason="Not consumer-facing",
                )

        mock_filter = Mock()
        mock_filter.classify = AsyncMock(side_effect=thesis_side_effect)

        mock_orchestrator = Mock()
        mock_orchestrator.enrich = AsyncMock(return_value={"signal_count": 2})

        scout = CuratedScout(
            signal_store=store,
            tavily_client=mock_tavily,
            url_profiler=mock_profiler,
            thesis_filter=mock_filter,
            signal_orchestrator=mock_orchestrator,
        )

        result = await scout.discover("test query")

        # Verify persistence ratio
        profiled_count = 100
        qualified_count = result.get("qualified_count", 0)
        persistence_ratio = qualified_count / profiled_count if profiled_count > 0 else 0

        assert persistence_ratio <= 0.10  # ≤10% acceptance criteria
