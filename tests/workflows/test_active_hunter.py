"""Tests for Active Hunter — sandbox execution engine."""

import asyncio
import json
import os
import pytest

from storage.signal_store import SignalStore
from storage.hunter_result_store import get_queries_for_run, get_results_for_run
from workflows.active_hunter import execute_hunter_run, get_hunter_enablement
from intelligence.query_generator import HunterQuery
from utils.instrumentation import metrics


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_ENABLEMENT", "shadow")
    monkeypatch.setenv("HUNTER_MAX_DAILY_QUERIES", "50")
    monkeypatch.setenv("HUNTER_MAX_DAILY_COST_UNITS", "100")
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def reset_metrics():
    metrics.reset()
    yield


def _make_queries(n=1, collector="github"):
    return [
        HunterQuery(
            collector=collector,
            query_text=f"health food query {i}",
            inputs_hash=f"hash_{i}",
        )
        for i in range(n)
    ]


async def _mock_collector(collector, query_text):
    """Mock collector returning 2 results."""
    return [
        {
            "company_name": "HealthSnacks Inc",
            "canonical_key": "domain:healthsnacks.ai",
            "source_api": collector,
            "confidence": 0.8,
            "raw_data": {"url": "https://github.com/healthsnacks", "description": "healthy snacks"},
        },
        {
            "company_name": "FitApp Co",
            "canonical_key": "domain:fitapp.io",
            "source_api": collector,
            "confidence": 0.7,
            "raw_data": {"url": "https://github.com/fitapp", "description": "fitness app"},
        },
    ]


async def _mock_timeout_collector(collector, query_text):
    """Mock collector that times out."""
    await asyncio.sleep(10)
    return []


async def _mock_empty_collector(collector, query_text):
    return []


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HUNTER_ENABLEMENT", raising=False)
        assert get_hunter_enablement() == "disabled"

    def test_shadow_mode(self, monkeypatch):
        monkeypatch.setenv("HUNTER_ENABLEMENT", "shadow")
        assert get_hunter_enablement() == "shadow"

    def test_invalid_falls_to_disabled(self, monkeypatch):
        monkeypatch.setenv("HUNTER_ENABLEMENT", "invalid")
        assert get_hunter_enablement() == "disabled"

    @pytest.mark.asyncio
    async def test_disabled_returns_immediately(self, store, monkeypatch):
        monkeypatch.setenv("HUNTER_ENABLEMENT", "disabled")
        result = await execute_hunter_run(store, _make_queries())
        assert result["status"] == "disabled"


class TestRunLifecycle:
    @pytest.mark.asyncio
    async def test_full_run_with_results(self, store):
        queries = _make_queries(1)
        result = await execute_hunter_run(
            store, queries, collector_fn=_mock_collector, min_query_interval=0,
        )
        assert result["executed"] == 1
        assert result["total_results"] == 2
        assert result["new_results"] == 2
        assert "run_id" in result

    @pytest.mark.asyncio
    async def test_dry_run_skips_execution(self, store):
        queries = _make_queries(2)
        result = await execute_hunter_run(
            store, queries, dry_run=True, min_query_interval=0,
        )
        assert result["skipped"] == 2
        assert result["executed"] == 0

    @pytest.mark.asyncio
    async def test_run_completion_recorded(self, store):
        queries = _make_queries(1)
        result = await execute_hunter_run(
            store, queries, collector_fn=_mock_empty_collector, min_query_interval=0,
        )
        run_id = result["run_id"]
        cursor = await store._db.execute(
            "SELECT status FROM run_history WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == "completed"


class TestAlreadyKnownDetection:
    @pytest.mark.asyncio
    async def test_already_known_from_signals(self, store):
        # Pre-seed a signal
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("spike", "github", "domain:healthsnacks.ai", "HealthSnacks",
             0.8, "{}", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        await store._db.commit()

        queries = _make_queries(1)
        result = await execute_hunter_run(
            store, queries, collector_fn=_mock_collector, min_query_interval=0,
        )
        assert result["already_known"] >= 1


class TestCrossRunHistory:
    @pytest.mark.asyncio
    async def test_history_suppressed(self, store):
        # First run creates results
        queries1 = [HunterQuery(collector="github", query_text="q1", inputs_hash="h1")]
        await execute_hunter_run(
            store, queries1, collector_fn=_mock_collector, min_query_interval=0,
        )

        # Mark a result as not_relevant
        from storage.hunter_result_store import update_result_status
        results = await get_results_for_run(store, (await store._db.execute(
            "SELECT id FROM run_history ORDER BY created_at DESC LIMIT 1"
        )).fetchone[0] if False else "dummy")  # noqa

        # Simpler: directly mark via SQL
        cursor = await store._db.execute(
            "SELECT id FROM hunter_results WHERE canonical_key = 'domain:healthsnacks.ai' LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            await update_result_status(store, row[0], "not_relevant")

            # Second run should suppress
            queries2 = [HunterQuery(collector="github", query_text="q2", inputs_hash="h2")]
            result2 = await execute_hunter_run(
                store, queries2, collector_fn=_mock_collector, min_query_interval=0,
            )
            # At least one result suppressed
            snapshot = metrics.snapshot()
            suppressed = snapshot["counters"].get("hunter.result.history_suppressed", 0)
            assert suppressed >= 1


class TestBudgetSkip:
    @pytest.mark.asyncio
    async def test_budget_exhaustion_skips(self, store, monkeypatch):
        monkeypatch.setenv("HUNTER_MAX_DAILY_QUERIES", "1")
        queries = _make_queries(3)
        result = await execute_hunter_run(
            store, queries, collector_fn=_mock_empty_collector, min_query_interval=0,
        )
        assert result["executed"] == 1
        assert result["skipped"] == 2


class TestQueryTimeout:
    @pytest.mark.asyncio
    async def test_timeout_marks_failed(self, store):
        queries = [HunterQuery(
            collector="github", query_text="test", inputs_hash="h_timeout",
            timeout_seconds=1,
        )]
        result = await execute_hunter_run(
            store, queries, collector_fn=_mock_timeout_collector, min_query_interval=0,
        )
        assert result["failed"] == 1


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_emitted(self, store):
        queries = _make_queries(1)
        await execute_hunter_run(
            store, queries, collector_fn=_mock_empty_collector, min_query_interval=0,
        )
        snapshot = metrics.snapshot()
        assert snapshot["counters"].get("hunter.run.started", 0) >= 1
        assert snapshot["counters"].get("hunter.run.completed", 0) >= 1


class TestIdentityGuard:
    """Tests for hunter pre-insert identity guard (skip on empty canonical_key)."""

    @pytest.mark.asyncio
    async def test_skip_on_empty_canonical_key(self, store):
        """Results with no canonical_key should be skipped (not inserted)."""

        async def _no_key_collector(collector, query_text):
            return [
                {
                    "company_name": "Some Article Title",
                    "source_api": "news_api",
                    "canonical_key": "",  # Empty — should skip
                    "confidence": 0.5,
                    "raw_data": {"title": "Some Article Title"},
                },
            ]

        queries = _make_queries(1)
        result = await execute_hunter_run(
            store, queries, collector_fn=_no_key_collector, min_query_interval=0,
        )

        # Result should have 0 new results (skipped due to no identity)
        assert result["new_results"] == 0

        # Verify skip metric was incremented
        snapshot = metrics.snapshot()
        assert snapshot["counters"].get("hunter.result.skip_no_identity", 0) >= 1

    @pytest.mark.asyncio
    async def test_unknown_company_name_normalized(self, store):
        """'Unknown' company_name should be normalized to empty string."""

        async def _unknown_collector(collector, query_text):
            return [
                {
                    "company_name": "Unknown",
                    "source_api": "hacker_news",
                    "canonical_key": "domain:example.com",
                    "confidence": 0.5,
                    "raw_data": {"title": "Test"},
                },
            ]

        queries = _make_queries(1)
        result = await execute_hunter_run(
            store, queries, collector_fn=_unknown_collector, min_query_interval=0,
        )

        # Should insert (has canonical_key) but with empty company name
        assert result["new_results"] == 1

        # Verify the stored result has empty company_name
        results = await get_results_for_run(store, result["run_id"])
        assert results[0]["company_name"] == ""

    @pytest.mark.asyncio
    async def test_valid_key_empty_name_still_inserts(self, store):
        """Valid canonical_key with empty company_name should still insert."""

        async def _key_only_collector(collector, query_text):
            return [
                {
                    "company_name": "",
                    "source_api": "hacker_news",
                    "canonical_key": "domain:acme.com",
                    "confidence": 0.5,
                    "raw_data": {"title": "Acme discussion thread"},
                },
            ]

        queries = _make_queries(1)
        result = await execute_hunter_run(
            store, queries, collector_fn=_key_only_collector, min_query_interval=0,
        )

        # Should insert — canonical_key is the identity anchor
        assert result["new_results"] == 1
