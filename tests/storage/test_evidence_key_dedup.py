"""Tests for evidence_key dedup in SignalStore.save_signal() and is_duplicate().

Covers:
- Cross-run duplicate detection (THE BUG FIX)
- Multi-source convergence preserved
- Legacy signals without source_url
- evidence_key persisted in DB
- No second signal_processing record on dedup
- Dry-run parity
- Consecutive-run bug proof
- Two-store concurrency (BEGIN IMMEDIATE safety)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from storage.signal_store import SignalStore
from utils.evidence_key import compute_evidence_key


@pytest.fixture
async def store(tmp_path: Path):
    """Create an initialized SignalStore for testing."""
    db_path = tmp_path / "test.db"
    s = SignalStore(str(db_path))
    await s.initialize()
    yield s
    if s._db:
        await s._db.close()


class TestEvidenceKeyDedup:
    """Core evidence_key dedup behavior."""

    @pytest.mark.asyncio
    async def test_cross_run_duplicate_detected(self, store: SignalStore):
        """THE BUG FIX: same URL re-collected in a later run is detected as duplicate.

        Before this feature, datetime.now() produced a different detected_at,
        bypassing the UNIQUE(canonical_key, signal_type, source_api, detected_at) constraint.
        """
        url = "https://example.com/article/123"
        evidence_key = compute_evidence_key("news_api", url)
        raw = {"_provenance": {"source_url": url}, "title": "Test"}

        # Run 1: signal saved
        id1 = await store.save_signal(
            signal_type="news_mention",
            source_api="news_api",
            canonical_key="domain:example.com",
            confidence=0.5,
            raw_data=raw,
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            evidence_key=evidence_key,
        )
        assert id1 > 0

        # Run 2: same URL, different detected_at (simulates re-collection)
        id2 = await store.save_signal(
            signal_type="news_mention",
            source_api="news_api",
            canonical_key="domain:example.com",
            confidence=0.5,
            raw_data=raw,
            detected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),  # Different!
            evidence_key=evidence_key,
        )

        # Should return existing ID, not insert a new row
        assert id2 == id1

        # Verify only 1 row in DB
        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        count = (await cursor.fetchone())[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_multi_source_convergence_preserved(self, store: SignalStore):
        """Same company from different source_apis → both saved (different evidence_keys)."""
        url_news = "https://news.com/acme-launches"
        url_rss = "https://techcrunch.com/acme-launches"
        ek_news = compute_evidence_key("news_api", url_news)
        ek_rss = compute_evidence_key("rss_feeds", url_rss)

        id1 = await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:acme.com", confidence=0.5,
            raw_data={"url": url_news}, evidence_key=ek_news,
        )
        id2 = await store.save_signal(
            signal_type="news_mention", source_api="rss_feeds",
            canonical_key="domain:acme.com", confidence=0.6,
            raw_data={"url": url_rss}, evidence_key=ek_rss,
        )

        assert id1 != id2

        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        assert (await cursor.fetchone())[0] == 2

    @pytest.mark.asyncio
    async def test_legacy_signal_without_source_url(self, store: SignalStore):
        """Signals without source_url still save normally (legacy mode)."""
        id1 = await store.save_signal(
            signal_type="funding_event", source_api="sec_edgar",
            canonical_key="domain:stealth.com", confidence=0.7,
            raw_data={"form_type": "D"},
            evidence_key=None,
        )
        assert id1 > 0

    @pytest.mark.asyncio
    async def test_evidence_key_persisted_in_db(self, store: SignalStore):
        """evidence_key is stored in the signals table."""
        url = "https://example.com/article/456"
        ek = compute_evidence_key("news_api", url)

        signal_id = await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data={"url": url}, evidence_key=ek,
        )

        cursor = await store._db.execute(
            "SELECT evidence_key FROM signals WHERE id = ?", (signal_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == ek

    @pytest.mark.asyncio
    async def test_no_processing_record_on_dedup(self, store: SignalStore):
        """When dedup returns existing ID, no new signal_processing record is created."""
        url = "https://example.com/article/789"
        ek = compute_evidence_key("news_api", url)
        raw = {"url": url}

        await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data=raw, evidence_key=ek,
        )

        # Second save (dedup)
        await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data=raw, evidence_key=ek,
        )

        cursor = await store._db.execute("SELECT COUNT(*) FROM signal_processing")
        count = (await cursor.fetchone())[0]
        assert count == 1  # Only one processing record

    @pytest.mark.asyncio
    async def test_consecutive_runs_single_signal(self, store: SignalStore):
        """NowTimestampCollector bug proof: 3 runs with different detected_at → 1 signal."""
        url = "https://example.com/trending/repo"
        ek = compute_evidence_key("github", url)
        raw = {"url": url, "repo": "owner/repo"}

        ids = []
        for i in range(3):
            signal_id = await store.save_signal(
                signal_type="github_trending", source_api="github",
                canonical_key="domain:repo.dev", confidence=0.5,
                raw_data=raw,
                detected_at=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
                evidence_key=ek,
            )
            ids.append(signal_id)

        # All three should return the same ID
        assert ids[0] == ids[1] == ids[2]

        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        assert (await cursor.fetchone())[0] == 1


class TestIsDuplicateEvidenceKey:
    """Test is_duplicate() with evidence_key parameter."""

    @pytest.mark.asyncio
    async def test_evidence_key_fast_path(self, store: SignalStore):
        """is_duplicate() returns True when evidence_key matches."""
        url = "https://example.com/article"
        ek = compute_evidence_key("news_api", url)

        await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data={"url": url}, evidence_key=ek,
        )

        result = await store.is_duplicate(
            "domain:example.com", evidence_key=ek,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_evidence_key_miss(self, store: SignalStore):
        """is_duplicate() returns False for unknown evidence_key."""
        result = await store.is_duplicate(
            "domain:example.com",
            evidence_key="0000000000000000000000000000abcd",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_falls_through_to_tuple_check(self, store: SignalStore):
        """When evidence_key is None, falls through to tuple check."""
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data={},
            detected_at=dt,
            evidence_key=None,
        )

        # Tuple match (legacy path)
        result = await store.is_duplicate(
            "domain:example.com",
            signal_type="news_mention",
            source_api="news_api",
            detected_at=dt,
        )
        assert result is True


class TestEvidenceKeyFallback:
    """Test evidence_key extraction from raw_data when not passed as kwarg."""

    @pytest.mark.asyncio
    async def test_extract_from_provenance(self, store: SignalStore):
        """When evidence_key kwarg is None, extracts from raw_data._provenance.source_url."""
        url = "https://example.com/article/auto"
        raw = {"_provenance": {"source_url": url}, "title": "Auto-extract"}

        id1 = await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data=raw,
            evidence_key=None,  # Not passed — should auto-extract
        )

        # Second save with same raw_data — should dedup
        id2 = await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data=raw,
            evidence_key=None,
        )

        assert id1 == id2

    @pytest.mark.asyncio
    async def test_extract_from_url_fallback(self, store: SignalStore):
        """When no _provenance, falls back to raw_data['url']."""
        url = "https://example.com/article/fallback"
        raw = {"url": url, "title": "Fallback"}

        id1 = await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data=raw, evidence_key=None,
        )

        id2 = await store.save_signal(
            signal_type="news_mention", source_api="news_api",
            canonical_key="domain:example.com", confidence=0.5,
            raw_data=raw, evidence_key=None,
        )

        assert id1 == id2


class TestTwoStoreConcurrency:
    """Test BEGIN IMMEDIATE safety with two SignalStore instances on the same DB."""

    @pytest.mark.asyncio
    async def test_two_stores_no_duplicate(self, tmp_path: Path):
        """Two stores racing to save the same signal — only one should win."""
        db_path = str(tmp_path / "concurrent.db")

        store1 = SignalStore(db_path)
        store2 = SignalStore(db_path)
        await store1.initialize()
        await store2.initialize()

        url = "https://example.com/race"
        ek = compute_evidence_key("news_api", url)
        raw = {"url": url}

        async def save(s: SignalStore):
            return await s.save_signal(
                signal_type="news_mention", source_api="news_api",
                canonical_key="domain:example.com", confidence=0.5,
                raw_data=raw, evidence_key=ek,
            )

        # Race both stores
        results = await asyncio.gather(save(store1), save(store2))

        # Both should return the same ID (one inserts, one dedup-returns)
        assert results[0] == results[1]

        # Verify only 1 row
        cursor = await store1._db.execute("SELECT COUNT(*) FROM signals")
        assert (await cursor.fetchone())[0] == 1

        await store1._db.close()
        await store2._db.close()
