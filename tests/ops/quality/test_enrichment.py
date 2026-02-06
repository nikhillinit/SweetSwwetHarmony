"""Tier 3 Lower Risk -- Async enrichment runner tests.

Verifies enrich_signal() and enrich_signals_best_effort() with mocked
BrandSentimentClient and CommunityMetricsClient, covering happy path,
selective enrichment (brand-only, community-only), and error handling.
"""

from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ops.quality.enrichment import (
    EnrichmentResult,
    enrich_signal,
    enrich_signals_best_effort,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_brand_client(score: float = 0.5, labels: list | None = None):
    """Return a MagicMock for BrandSentimentClient with async analyze_brand_sentiment."""
    client = MagicMock()
    client.analyze_brand_sentiment = AsyncMock(
        return_value=(score, labels or ["positive"])
    )
    return client


def _mock_community_client(metrics: dict | None = None):
    """Return a MagicMock for CommunityMetricsClient with async get_community_metrics."""
    client = MagicMock()
    client.get_community_metrics = AsyncMock(
        return_value=metrics or {"stars": 100}
    )
    return client


def _run_async(coro):
    """Run an async coroutine and restore the event loop afterwards.

    asyncio.run() creates a new event loop, executes the coroutine, and then
    *closes* the loop.  The quality_db conftest fixture depends on
    asyncio.get_event_loop() in both setup and teardown.  This wrapper ensures
    a usable event loop is always present after async execution.
    """
    try:
        return asyncio.run(coro)
    finally:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# enrich_signal tests
# ---------------------------------------------------------------------------

class TestEnrichSignal:
    """Tests for enrich_signal() async function."""

    def test_enrich_signal_returns_result(self):
        """enrich_signal should return an EnrichmentResult with both brand and community data."""
        brand_client = _mock_brand_client(score=0.5, labels=["positive"])
        community_client = _mock_community_client(metrics={"stars": 100})

        with patch(
            "ops.quality.enrichment.BrandSentimentClient",
            return_value=brand_client,
        ), patch(
            "ops.quality.enrichment.CommunityMetricsClient",
            return_value=community_client,
        ):
            result = _run_async(
                enrich_signal(
                    signal_id=1,
                    canonical_key="domain:test.com",
                    text="A great consumer product",
                    domain="test.com",
                    enable_brand_sentiment=True,
                    enable_community_metrics=True,
                )
            )

        assert isinstance(result, EnrichmentResult)
        assert result.signal_id == 1
        assert result.canonical_key == "domain:test.com"
        assert result.brand_sentiment is not None
        assert result.brand_sentiment["score"] == 0.5
        assert result.brand_sentiment["labels"] == ["positive"]
        assert result.community_metrics is not None
        assert result.community_metrics["stars"] == 100

    def test_enrich_signal_brand_only(self):
        """enable_community_metrics=False should leave community_metrics as None."""
        brand_client = _mock_brand_client(score=0.7, labels=["positive", "trending"])

        with patch(
            "ops.quality.enrichment.BrandSentimentClient",
            return_value=brand_client,
        ), patch(
            "ops.quality.enrichment.CommunityMetricsClient",
            return_value=_mock_community_client(),
        ):
            result = _run_async(
                enrich_signal(
                    signal_id=2,
                    canonical_key="domain:brand.com",
                    text="Brand-focused company",
                    domain="brand.com",
                    enable_brand_sentiment=True,
                    enable_community_metrics=False,
                )
            )

        assert result.brand_sentiment is not None
        assert result.brand_sentiment["score"] == 0.7
        assert result.community_metrics is None

    def test_enrich_signal_community_only(self):
        """enable_brand_sentiment=False should leave brand_sentiment as None."""
        community_client = _mock_community_client(metrics={"stars": 250, "forks": 50})

        with patch(
            "ops.quality.enrichment.BrandSentimentClient",
            return_value=_mock_brand_client(),
        ), patch(
            "ops.quality.enrichment.CommunityMetricsClient",
            return_value=community_client,
        ):
            result = _run_async(
                enrich_signal(
                    signal_id=3,
                    canonical_key="domain:community.io",
                    text="Community platform",
                    domain="community.io",
                    enable_brand_sentiment=False,
                    enable_community_metrics=True,
                )
            )

        assert result.brand_sentiment is None
        assert result.community_metrics is not None
        assert result.community_metrics["stars"] == 250


# ---------------------------------------------------------------------------
# enrich_signals_best_effort tests
# ---------------------------------------------------------------------------

class TestEnrichSignalsBestEffort:
    """Tests for enrich_signals_best_effort()."""

    def test_enrich_signals_best_effort_with_signals(self, quality_db_with_signals):
        """Should return a result list matching the number of input signal_ids."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        brand_client = _mock_brand_client(score=0.6, labels=["neutral"])
        community_client = _mock_community_client(metrics={"stars": 50})

        # Patch asyncio.run at the module level so the loop is restored after
        # enrich_signals_best_effort's internal asyncio.run() call.
        _real_asyncio_run = asyncio.run

        def _patched_run(coro, **kwargs):
            try:
                return _real_asyncio_run(coro, **kwargs)
            finally:
                try:
                    asyncio.get_event_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())

        with patch(
            "ops.quality.enrichment.BrandSentimentClient",
            return_value=brand_client,
        ), patch(
            "ops.quality.enrichment.CommunityMetricsClient",
            return_value=community_client,
        ), patch(
            "ops.quality.enrichment.asyncio.run",
            side_effect=_patched_run,
        ):
            results = enrich_signals_best_effort(conn, signal_ids=signal_ids)

        assert isinstance(results, list)
        assert len(results) == len(signal_ids)

        # Each result should have signal_id and canonical_key (no errors).
        for r in results:
            assert "signal_id" in r
            assert "canonical_key" in r

        conn.close()

    def test_enrich_signals_best_effort_error_handling(self, quality_db_with_signals):
        """If BrandSentimentClient raises, result list should contain a dict with 'error' key."""
        db_path, _store, signal_ids = quality_db_with_signals
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        brand_client = MagicMock()
        brand_client.analyze_brand_sentiment = AsyncMock(
            side_effect=Exception("Sentiment API unavailable")
        )

        # Patch asyncio.run at the module level so the loop is restored after
        # enrich_signals_best_effort's internal asyncio.run() call.
        _real_asyncio_run = asyncio.run

        def _patched_run(coro, **kwargs):
            try:
                return _real_asyncio_run(coro, **kwargs)
            finally:
                try:
                    asyncio.get_event_loop()
                except RuntimeError:
                    asyncio.set_event_loop(asyncio.new_event_loop())

        with patch(
            "ops.quality.enrichment.BrandSentimentClient",
            return_value=brand_client,
        ), patch(
            "ops.quality.enrichment.CommunityMetricsClient",
            return_value=_mock_community_client(),
        ), patch(
            "ops.quality.enrichment.asyncio.run",
            side_effect=_patched_run,
        ):
            results = enrich_signals_best_effort(conn, signal_ids=signal_ids)

        assert isinstance(results, list)
        assert len(results) == len(signal_ids)

        # All results should have an 'error' key since brand sentiment fails first.
        error_results = [r for r in results if "error" in r]
        assert len(error_results) > 0
        assert "Sentiment API unavailable" in error_results[0]["error"]

        conn.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
