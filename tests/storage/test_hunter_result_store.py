"""Tests for HunterResultStore — CRUD, state machines, cross-run history, zombies."""

import json
import pytest

from storage.signal_store import SignalStore
from storage.hunter_result_store import (
    QUERY_TRANSITIONS,
    RESULT_TRANSITIONS,
    InvalidHunterTransition,
    StaleUpdateError,
    compute_result_dedupe_key,
    create_query,
    update_query_status,
    get_queries_for_run,
    create_result,
    get_results_for_run,
    get_result_by_id,
    update_result_status,
    check_historical_canonical,
    recover_stale_queries,
    get_active_negative_keywords,
    create_negative_keyword,
)


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    s = SignalStore(db_path)
    await s.initialize()
    # Seed a run_history entry for FK satisfaction
    await s._db.execute(
        "INSERT INTO run_history (id, run_type, status, created_at) VALUES (?, ?, ?, ?)",
        ("run1", "hunter", "queued", "2026-01-01T00:00:00Z"),
    )
    await s._db.commit()
    yield s
    await s.close()


# =============================================================================
# DEDUPE KEY
# =============================================================================


class TestDedupeKey:
    def test_strong_canonical_key(self):
        key = compute_result_dedupe_key("run1", 1, "domain:acme.ai")
        assert len(key) == 32
        # Deterministic
        assert key == compute_result_dedupe_key("run1", 1, "domain:acme.ai")

    def test_different_run_yields_different_key(self):
        k1 = compute_result_dedupe_key("run1", 1, "domain:acme.ai")
        k2 = compute_result_dedupe_key("run2", 1, "domain:acme.ai")
        assert k1 != k2

    def test_weak_name_loc_uses_fallback(self):
        k1 = compute_result_dedupe_key(
            "run1", 1, "name_loc:acme:us",
            company_name="Acme", source_api="github",
        )
        k2 = compute_result_dedupe_key(
            "run1", 1, "domain:acme.ai",
        )
        assert k1 != k2  # Different fallback path

    def test_null_canonical_uses_company_source(self):
        k = compute_result_dedupe_key(
            "run1", 1, None,
            company_name="Acme Inc", source_api="github",
        )
        assert len(k) == 32

    def test_source_id_fallback(self):
        k = compute_result_dedupe_key(
            "run1", 1, None,
            company_name=None, source_api="github",
            raw_data={"source_id": "abc123"},
        )
        assert len(k) == 32


# =============================================================================
# QUERY CRUD + STATE MACHINE
# =============================================================================


class TestQueryCRUD:
    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github",
            query_text="health food startup", inputs_hash="h1",
        )
        assert qid > 0
        queries = await get_queries_for_run(store, "run1")
        assert len(queries) == 1
        assert queries[0]["status"] == "pending"
        assert queries[0]["collector"] == "github"

    @pytest.mark.asyncio
    async def test_valid_transitions(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        await update_query_status(store, qid, "executing")
        await update_query_status(store, qid, "completed", results_count=5)
        queries = await get_queries_for_run(store, "run1")
        assert queries[0]["status"] == "completed"
        assert queries[0]["results_count"] == 5

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        with pytest.raises(InvalidHunterTransition):
            await update_query_status(store, qid, "completed")  # pending -> completed invalid

    @pytest.mark.asyncio
    async def test_skip_from_pending(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        await update_query_status(store, qid, "skipped")
        queries = await get_queries_for_run(store, "run1", status="skipped")
        assert len(queries) == 1

    @pytest.mark.asyncio
    async def test_failed_with_error(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        await update_query_status(store, qid, "executing")
        await update_query_status(store, qid, "failed", error_message="API timeout")
        queries = await get_queries_for_run(store, "run1")
        assert queries[0]["error_message"] == "API timeout"


# =============================================================================
# RESULT CRUD + STATE MACHINE
# =============================================================================


class TestResultCRUD:
    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Acme", source_api="github",
            raw_data={"url": "https://github.com/acme"},
            canonical_key="domain:acme.ai",
        )
        assert rid > 0
        results = await get_results_for_run(store, "run1")
        assert len(results) == 1
        assert results[0]["company_name"] == "Acme"
        assert results[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_result_by_id(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Test", source_api="github",
            raw_data={"x": 1},
        )
        result = await get_result_by_id(store, rid)
        assert result is not None
        assert result["company_name"] == "Test"

    @pytest.mark.asyncio
    async def test_already_known_sets_status(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Known Co", source_api="github",
            raw_data={"url": "test"}, already_known=True,
        )
        result = await get_result_by_id(store, rid)
        assert result["status"] == "already_known"

    @pytest.mark.asyncio
    async def test_valid_result_transitions(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Test", source_api="github", raw_data={},
        )
        await update_result_status(store, rid, "relevant", operator_feedback="Looks good")
        result = await get_result_by_id(store, rid)
        assert result["status"] == "relevant"
        assert result["operator_feedback"] == "Looks good"
        assert result["reviewed_at"] is not None

    @pytest.mark.asyncio
    async def test_invalid_result_transition(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Test", source_api="github", raw_data={},
        )
        with pytest.raises(InvalidHunterTransition):
            await update_result_status(store, rid, "promoted")  # pending -> promoted invalid

    @pytest.mark.asyncio
    async def test_optimistic_concurrency(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Test", source_api="github", raw_data={},
        )
        with pytest.raises(StaleUpdateError):
            await update_result_status(
                store, rid, "relevant",
                expected_updated_at="1999-01-01T00:00:00Z",
            )


# =============================================================================
# CROSS-RUN HISTORY
# =============================================================================


class TestCrossRunHistory:
    @pytest.mark.asyncio
    async def test_no_history(self, store):
        result = await check_historical_canonical(store, "domain:new.ai")
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_terminal_status(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name="Old Co", source_api="github", raw_data={},
            canonical_key="domain:old.ai",
        )
        await update_result_status(store, rid, "not_relevant")

        result = await check_historical_canonical(store, "domain:old.ai")
        assert result == "not_relevant"

    @pytest.mark.asyncio
    async def test_null_canonical_returns_none(self, store):
        result = await check_historical_canonical(store, "")
        assert result is None


# =============================================================================
# ZOMBIE RECOVERY
# =============================================================================


class TestZombieRecovery:
    @pytest.mark.asyncio
    async def test_recovers_stale_executing(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        await update_query_status(store, qid, "executing")
        # Manually backdate the executed_at
        db = store._db
        await db.execute(
            "UPDATE hunter_queries SET executed_at = '2020-01-01T00:00:00Z' WHERE id = ?",
            (qid,),
        )
        await db.commit()

        count = await recover_stale_queries(store, cutoff_minutes=1)
        assert count == 1

        queries = await get_queries_for_run(store, "run1")
        assert queries[0]["status"] == "failed"
        assert "Zombie" in queries[0]["error_message"]

    @pytest.mark.asyncio
    async def test_does_not_recover_recent(self, store):
        qid = await create_query(
            store, run_id="run1", collector="github", query_text="test",
        )
        await update_query_status(store, qid, "executing")

        count = await recover_stale_queries(store, cutoff_minutes=60)
        assert count == 0


# =============================================================================
# NEGATIVE KEYWORDS
# =============================================================================


class TestNegativeKeywords:
    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        nk_id = await create_negative_keyword(
            store, keyword="Blockchain", collector="github", source="manual",
        )
        assert nk_id is not None
        keywords = await get_active_negative_keywords(store, collector="github")
        assert len(keywords) == 1
        assert keywords[0]["keyword"] == "blockchain"  # lowercased

    @pytest.mark.asyncio
    async def test_duplicate_returns_none(self, store):
        await create_negative_keyword(store, keyword="crypto", source="manual")
        result = await create_negative_keyword(store, keyword="crypto", source="manual")
        assert result is None

    @pytest.mark.asyncio
    async def test_global_keywords_included(self, store):
        await create_negative_keyword(store, keyword="b2b", source="manual")
        keywords = await get_active_negative_keywords(store, collector="github")
        assert any(k["keyword"] == "b2b" for k in keywords)
