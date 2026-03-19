"""
Tests for DiffEngine — biased toward _compute_semantic_drift() lifecycle.

DiffEngine uses monitoring.models.MonitoringConfig (NOT monitoring.config.MonitoringConfigV2)
per diff_engine.py:26.

Covers:
- No embedding store/generator => None
- Short text => None
- Cold start: stores initial embedding, returns None
- Old snapshot exists but old embedding missing => stores new, returns None
- Old embedding fetch error => treated as missing old embedding
- New embedding generation failure => graceful None
- Save of new embedding fails after generation => drift still returned
- Summary includes page-type fields when classification exists
- Light coverage for _cosine_similarity and _calculate_severity
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from monitoring.diff_engine import DiffEngine
from monitoring.models import Snapshot, SeverityComponents, MonitoringConfig


def _make_snapshot(sid=1, watch_id=10, requested_url="https://example.com",
                   final_url=None, text_length=500, page_state="live",
                   content_hash="abc", status_code=200):
    return Snapshot(
        id=sid, watch_id=watch_id, requested_url=requested_url,
        final_url=final_url, text_length=text_length, page_state=page_state,
        content_hash=content_hash, status_code=status_code,
    )


def _fake_embedding(dim=768, seed=42):
    rng = np.random.RandomState(seed)
    return rng.randn(dim).astype(np.float32)


class TestComputeSemanticDrift:
    """Tests for DiffEngine._compute_semantic_drift lifecycle branches."""

    def _engine(self, *, store=None, generator=None):
        return DiffEngine(
            embedding_store=store,
            embedding_generator=generator,
        )

    # --- no store / generator ---

    @pytest.mark.asyncio
    async def test_no_embedding_store_returns_none(self):
        engine = self._engine(store=None, generator=None)
        result = await engine._compute_semantic_drift(
            old_snapshot_id=1,
            new_snapshot=_make_snapshot(sid=2),
            new_text="x" * 200,
        )
        assert result is None

    # --- short text ---

    @pytest.mark.asyncio
    async def test_short_text_returns_none(self):
        engine = self._engine(store=AsyncMock(), generator=AsyncMock())
        result = await engine._compute_semantic_drift(
            old_snapshot_id=1,
            new_snapshot=_make_snapshot(sid=2),
            new_text="too short",
        )
        assert result is None

    # --- cold start (old_snapshot_id is None) ---

    @pytest.mark.asyncio
    async def test_cold_start_stores_embedding_returns_none(self):
        store = AsyncMock()
        generator = AsyncMock()
        emb = _fake_embedding()
        generator.embed.return_value = emb

        engine = self._engine(store=store, generator=generator)
        result = await engine._compute_semantic_drift(
            old_snapshot_id=None,
            new_snapshot=_make_snapshot(sid=5),
            new_text="a" * 200,
        )

        assert result is None
        store.save_embedding.assert_awaited_once()
        call_kwargs = store.save_embedding.call_args.kwargs
        assert call_kwargs["canonical_key"] == "snapshot:5"
        assert call_kwargs["embedding_kind"] == "snapshot_v1"

    # --- old snapshot exists, but old embedding missing ---

    @pytest.mark.asyncio
    async def test_old_embedding_missing_stores_new_returns_none(self):
        store = AsyncMock()
        store.get_embedding.return_value = None  # missing
        generator = AsyncMock()
        generator.embed.return_value = _fake_embedding()

        engine = self._engine(store=store, generator=generator)
        result = await engine._compute_semantic_drift(
            old_snapshot_id=10,
            new_snapshot=_make_snapshot(sid=11),
            new_text="b" * 200,
        )

        assert result is None
        store.save_embedding.assert_awaited_once()
        call_kwargs = store.save_embedding.call_args.kwargs
        assert call_kwargs["canonical_key"] == "snapshot:11"
        assert call_kwargs["embedding_kind"] == "snapshot_v1"

    # --- old embedding fetch raises ---

    @pytest.mark.asyncio
    async def test_old_embedding_fetch_error_treated_as_missing(self):
        store = AsyncMock()
        store.get_embedding.side_effect = RuntimeError("db error")
        generator = AsyncMock()
        generator.embed.return_value = _fake_embedding()

        engine = self._engine(store=store, generator=generator)
        result = await engine._compute_semantic_drift(
            old_snapshot_id=10,
            new_snapshot=_make_snapshot(sid=11),
            new_text="c" * 200,
        )

        # Treated as missing => stores new, returns None
        assert result is None
        store.save_embedding.assert_awaited_once()

    # --- new embedding generation failure ---

    @pytest.mark.asyncio
    async def test_new_embedding_generation_failure_returns_none(self):
        old_emb = _fake_embedding(seed=1)
        store = AsyncMock()
        store.get_embedding.return_value = old_emb
        generator = AsyncMock()
        generator.embed.side_effect = RuntimeError("embed failed")

        engine = self._engine(store=store, generator=generator)
        result = await engine._compute_semantic_drift(
            old_snapshot_id=10,
            new_snapshot=_make_snapshot(sid=11),
            new_text="d" * 200,
        )

        assert result is None

    # --- save of new embedding fails, but drift still returned ---

    @pytest.mark.asyncio
    async def test_save_failure_after_generation_still_returns_drift(self):
        old_emb = _fake_embedding(seed=1)
        new_emb = _fake_embedding(seed=2)

        store = AsyncMock()
        store.get_embedding.return_value = old_emb
        store.save_embedding.side_effect = RuntimeError("write error")
        generator = AsyncMock()
        generator.embed.return_value = new_emb

        engine = self._engine(store=store, generator=generator)
        result = await engine._compute_semantic_drift(
            old_snapshot_id=10,
            new_snapshot=_make_snapshot(sid=11),
            new_text="e" * 200,
        )

        # Drift should still be computed even though save failed
        assert result is not None
        assert 0.0 <= result <= 1.0


class TestComputeDiffSummary:
    """Test that summary includes page-type fields when classification exists."""

    @pytest.mark.asyncio
    async def test_summary_has_page_type_fields(self):
        engine = DiffEngine()
        old = _make_snapshot(sid=1, content_hash="aaa", text_length=500)
        new = _make_snapshot(sid=2, requested_url="https://example.com/pricing",
                             content_hash="bbb", text_length=600)

        result = await engine.compute_diff(old, new, "pricing page content " * 50)

        summary = result.diff.diff_summary
        assert "page_type" in summary
        assert "page_type_confidence" in summary
        assert "page_type_boost" in summary


class TestCosineSimilarity:
    """Light coverage for _cosine_similarity."""

    def test_identical_vectors(self):
        engine = DiffEngine()
        v = np.array([1.0, 2.0, 3.0])
        assert engine._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_zero_vector(self):
        engine = DiffEngine()
        v = np.array([1.0, 2.0])
        z = np.array([0.0, 0.0])
        assert engine._cosine_similarity(v, z) == 0.0


class TestCalculateSeverity:
    """Light coverage for _calculate_severity."""

    def test_instant_trigger_gets_high_severity(self):
        engine = DiffEngine()
        components = SeverityComponents(content_delta=0.1)
        score = engine._calculate_severity(components, instant_trigger=True)
        assert score >= 0.80
