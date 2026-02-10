"""Tests for Hunter Budget Manager — ledger-backed accounting."""

import json
import pytest

from storage.signal_store import SignalStore
from storage.hunter_result_store import (
    BudgetExhausted,
    check_and_reserve_budget,
    settle_budget,
    get_budget_summary,
)


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_MAX_DAILY_QUERIES", "5")
    monkeypatch.setenv("HUNTER_MAX_DAILY_COST_UNITS", "10")
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    await s._db.execute(
        "INSERT INTO run_history (id, run_type, status, created_at) VALUES (?, ?, ?, ?)",
        ("run1", "hunter", "queued", "2026-01-01T00:00:00Z"),
    )
    await s._db.commit()
    yield s
    await s.close()


class TestBudgetReservation:
    @pytest.mark.asyncio
    async def test_reserve_under_budget(self, store):
        result = await check_and_reserve_budget(
            store, "github", "2026-01-01", 1.0, run_id="run1",
        )
        assert result is True
        summary = await get_budget_summary(store, "2026-01-01")
        assert summary["collectors"]["github"]["queries_executed"] == 1
        assert summary["global"]["cost_units"] == 1.0

    @pytest.mark.asyncio
    async def test_reserve_multiple(self, store):
        for i in range(3):
            await check_and_reserve_budget(
                store, "github", "2026-01-01", 1.0, run_id="run1",
            )
        summary = await get_budget_summary(store, "2026-01-01")
        assert summary["collectors"]["github"]["queries_executed"] == 3
        assert summary["global"]["cost_units"] == 3.0

    @pytest.mark.asyncio
    async def test_reserve_at_query_limit_raises(self, store):
        # Cap is 5 queries
        for i in range(5):
            await check_and_reserve_budget(
                store, "github", "2026-01-01", 1.0, run_id="run1",
            )
        with pytest.raises(BudgetExhausted, match="query cap"):
            await check_and_reserve_budget(
                store, "github", "2026-01-01", 1.0, run_id="run1",
            )

    @pytest.mark.asyncio
    async def test_reserve_at_cost_limit_raises(self, store):
        # Cost cap is 10, try to reserve 11 in one shot
        with pytest.raises(BudgetExhausted, match="cost cap"):
            await check_and_reserve_budget(
                store, "github", "2026-01-01", 11.0, run_id="run1",
            )

    @pytest.mark.asyncio
    async def test_global_cost_cap_across_collectors(self, store):
        # Reserve 6 from github, then try 5 from hacker_news (total 11 > 10 cap)
        await check_and_reserve_budget(store, "github", "2026-01-01", 6.0, run_id="run1")
        with pytest.raises(BudgetExhausted, match="cost cap"):
            await check_and_reserve_budget(store, "hacker_news", "2026-01-01", 5.0, run_id="run1")

    @pytest.mark.asyncio
    async def test_next_day_budget_resets(self, store):
        # Fill up day 1
        for i in range(5):
            await check_and_reserve_budget(store, "github", "2026-01-01", 1.0, run_id="run1")
        # Day 2 should be fresh
        result = await check_and_reserve_budget(store, "github", "2026-01-02", 1.0, run_id="run1")
        assert result is True


class TestBudgetSettlement:
    @pytest.mark.asyncio
    async def test_settlement_adjusts_cost(self, store):
        await check_and_reserve_budget(store, "github", "2026-01-01", 2.0, run_id="run1")
        # Actual cost was 3.0, 1.0 more than estimated
        await settle_budget(store, "github", "2026-01-01", 1, 3.0, 2.0, run_id="run1")
        summary = await get_budget_summary(store, "2026-01-01")
        assert abs(summary["global"]["cost_units"] - 3.0) < 0.01

    @pytest.mark.asyncio
    async def test_settlement_no_change_when_equal(self, store):
        await check_and_reserve_budget(store, "github", "2026-01-01", 2.0, run_id="run1")
        await settle_budget(store, "github", "2026-01-01", 1, 2.0, 2.0, run_id="run1")
        summary = await get_budget_summary(store, "2026-01-01")
        assert abs(summary["global"]["cost_units"] - 2.0) < 0.01

    @pytest.mark.asyncio
    async def test_overrun_logs_audit_event(self, store):
        # Cost cap is 10, reserve 5, then settle with 7 (delta=2, >10% of cap=1.0)
        await check_and_reserve_budget(store, "github", "2026-01-01", 5.0, run_id="run1")
        await settle_budget(store, "github", "2026-01-01", 1, 7.0, 5.0, run_id="run1")
        # Check audit event exists
        cursor = await store._db.execute(
            "SELECT action_type FROM audit_events WHERE action_type = 'hunter_budget_overrun'"
        )
        row = await cursor.fetchone()
        assert row is not None


class TestLedgerAppendOnly:
    @pytest.mark.asyncio
    async def test_transactions_are_append_only(self, store):
        await check_and_reserve_budget(store, "github", "2026-01-01", 1.0, run_id="run1")
        await check_and_reserve_budget(store, "github", "2026-01-01", 2.0, run_id="run1")

        cursor = await store._db.execute(
            "SELECT delta_queries, delta_cost, reason FROM hunter_budget_transactions ORDER BY id"
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, 1.0, "reserve")
        assert rows[1] == (1, 2.0, "reserve")

    @pytest.mark.asyncio
    async def test_settlement_appends_transaction(self, store):
        await check_and_reserve_budget(store, "github", "2026-01-01", 2.0, run_id="run1")
        await settle_budget(store, "github", "2026-01-01", 1, 3.0, 2.0, run_id="run1")

        cursor = await store._db.execute(
            "SELECT reason FROM hunter_budget_transactions ORDER BY id"
        )
        rows = await cursor.fetchall()
        reasons = [r[0] for r in rows]
        assert reasons == ["reserve", "settle"]
