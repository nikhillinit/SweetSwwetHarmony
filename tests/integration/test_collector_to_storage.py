"""
Integration tests for collector → storage flow.

Tests the integration between BaseCollector and SignalStore:
1. Collectors properly store signals in SignalStore
2. Signals can be retrieved after storage
3. Collectors respect dry_run mode (no storage)
4. Deduplication works correctly

Uses HackerNewsCollector as test subject (simple, no API key required).
"""

import pytest
import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from collectors.hacker_news import HackerNewsCollector
from storage.signal_store import SignalStore
from discovery_engine.mcp_server import CollectorStatus


@pytest.mark.asyncio
class TestCollectorToStorage:
    """Integration tests for collector → storage flow"""

    async def test_collector_stores_signals_when_dry_run_false(self):
        """
        GIVEN: A collector with SignalStore configured
        WHEN: run(dry_run=False) is called
        THEN: Signals are stored in the database
        """
        # Create in-memory SignalStore for isolation
        store = SignalStore(":memory:")
        await store.initialize()

        # Create collector with mocked HTTP client
        collector = HackerNewsCollector(store=store, lookback_days=7, min_points=10)

        # Mock the HTTP response to return a sample HN post
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "12345",
                    "title": "Show HN: My Awesome Startup",
                    "url": "https://myawesomestartup.com",
                    "author": "founder",
                    "points": 150,
                    "num_comments": 50,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                }
            ],
            "nbPages": 1,
        }

        # Mock httpx.AsyncClient.get at the module level
        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector:
                # Run collector WITHOUT dry_run (should store signals)
                result = await collector.run(dry_run=False)

        # Verify collector result
        assert result.status == CollectorStatus.SUCCESS
        assert result.signals_found == 1
        assert result.signals_new == 1
        assert result.signals_suppressed == 0
        assert result.dry_run is False

        # Verify signals are in the database
        pending_signals = await store.get_pending_signals()
        assert len(pending_signals) == 1

        signal = pending_signals[0]
        assert signal.signal_type == "hacker_news_mention"
        assert signal.source_api == "hacker_news"
        assert signal.canonical_key == "domain:myawesomestartup.com"
        assert signal.processing_status == "pending"

        await store.close()

    async def test_collector_retrieves_stored_signals(self):
        """
        GIVEN: Signals stored by a collector
        WHEN: Querying the store
        THEN: Signals can be retrieved with correct data
        """
        # Create in-memory SignalStore
        store = SignalStore(":memory:")
        await store.initialize()

        # Create and run collector
        collector = HackerNewsCollector(store=store, lookback_days=7, min_points=10)

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "67890",
                    "title": "Show HN: Consumer Health App",
                    "url": "https://healthapp.io",
                    "author": "healthfounder",
                    "points": 200,
                    "num_comments": 75,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                    "story_text": "We built a health tracking app",
                }
            ],
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector:
                await collector.run(dry_run=False)

        # Retrieve signals by canonical key
        signals = await store.get_signals_for_company("domain:healthapp.io")

        assert len(signals) == 1
        signal = signals[0]

        # Verify signal data
        assert signal.signal_type == "hacker_news_mention"
        assert signal.source_api == "hacker_news"
        assert signal.confidence > 0.5  # HN signals have moderate confidence
        assert signal.raw_data["hacker_news_id"] == "67890"
        assert signal.raw_data["title"] == "Show HN: Consumer Health App"
        assert signal.raw_data["points"] == 200
        assert signal.raw_data["num_comments"] == 75
        assert signal.raw_data["is_show_hn"] is True

        await store.close()

    async def test_collector_respects_dry_run_mode(self):
        """
        GIVEN: A collector with SignalStore configured
        WHEN: run(dry_run=True) is called
        THEN: Signals are NOT stored in the database
        """
        # Create in-memory SignalStore
        store = SignalStore(":memory:")
        await store.initialize()

        collector = HackerNewsCollector(store=store, lookback_days=7, min_points=10)

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "99999",
                    "title": "Show HN: DryRun Test",
                    "url": "https://dryrun.test",
                    "author": "tester",
                    "points": 100,
                    "num_comments": 25,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                }
            ],
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector:
                result = await collector.run(dry_run=True)

        # Verify result shows DRY_RUN status
        assert result.status == CollectorStatus.DRY_RUN
        assert result.signals_found == 1
        assert result.dry_run is True

        # Verify NO signals were stored
        pending_signals = await store.get_pending_signals()
        assert len(pending_signals) == 0

        await store.close()

    async def test_collector_deduplication(self):
        """
        GIVEN: A signal already stored for a canonical key
        WHEN: Collector runs again with same signal
        THEN: Signal is suppressed (not stored again)
        """
        # Create in-memory SignalStore
        store = SignalStore(":memory:")
        await store.initialize()

        collector = HackerNewsCollector(store=store, lookback_days=7, min_points=10)

        # Mock HTTP response with the same post
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "11111",
                    "title": "Show HN: Duplicate Test",
                    "url": "https://duplicate.test",
                    "author": "founder",
                    "points": 120,
                    "num_comments": 30,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                }
            ],
            "nbPages": 1,
        }

        # Run collector FIRST time
        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector:
                result1 = await collector.run(dry_run=False)

        # Verify first run stored the signal
        assert result1.signals_new == 1
        assert result1.signals_suppressed == 0

        # Create NEW collector instance (simulates second run)
        collector2 = HackerNewsCollector(store=store, lookback_days=7, min_points=10)

        # Run collector SECOND time with same data
        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector2:
                result2 = await collector2.run(dry_run=False)

        # Verify second run detected duplicate
        assert result2.signals_found == 1
        assert result2.signals_new == 0
        assert result2.signals_suppressed == 1

        # Verify only ONE signal in database
        pending_signals = await store.get_pending_signals()
        assert len(pending_signals) == 1

        await store.close()

    async def test_collector_without_store_marks_all_new(self):
        """
        GIVEN: A collector without SignalStore configured
        WHEN: run() is called
        THEN: All signals are marked as new (no deduplication)
        """
        # Create collector WITHOUT store
        collector = HackerNewsCollector(store=None, lookback_days=7, min_points=10)

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "22222",
                    "title": "Show HN: No Store Test",
                    "url": "https://nostore.test",
                    "author": "founder",
                    "points": 80,
                    "num_comments": 15,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                }
            ],
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector:
                result = await collector.run(dry_run=True)

        # Verify all signals marked as new
        assert result.signals_found == 1
        assert result.signals_new == 1
        assert result.signals_suppressed == 0

    async def test_collector_handles_multiple_signals(self):
        """
        GIVEN: Multiple HN posts returned from API
        WHEN: Collector runs
        THEN: All signals are stored correctly
        """
        # Create in-memory SignalStore
        store = SignalStore(":memory:")
        await store.initialize()

        collector = HackerNewsCollector(store=store, lookback_days=7, min_points=10)

        # Mock HTTP response with multiple posts
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "objectID": "33333",
                    "title": "Show HN: Startup Alpha",
                    "url": "https://alpha.com",
                    "author": "founder1",
                    "points": 150,
                    "num_comments": 40,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                },
                {
                    "objectID": "44444",
                    "title": "Show HN: Startup Beta",
                    "url": "https://beta.com",
                    "author": "founder2",
                    "points": 200,
                    "num_comments": 60,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                },
                {
                    "objectID": "55555",
                    "title": "Show HN: Startup Gamma",
                    "url": "https://gamma.com",
                    "author": "founder3",
                    "points": 100,
                    "num_comments": 30,
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "_tags": ["story", "show_hn"],
                },
            ],
            "nbPages": 1,
        }

        with patch('httpx.AsyncClient.get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with collector:
                result = await collector.run(dry_run=False)

        # Verify all signals stored
        assert result.signals_found == 3
        assert result.signals_new == 3
        assert result.signals_suppressed == 0

        # Verify each signal is in the database
        alpha_signals = await store.get_signals_for_company("domain:alpha.com")
        beta_signals = await store.get_signals_for_company("domain:beta.com")
        gamma_signals = await store.get_signals_for_company("domain:gamma.com")

        assert len(alpha_signals) == 1
        assert len(beta_signals) == 1
        assert len(gamma_signals) == 1

        await store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
