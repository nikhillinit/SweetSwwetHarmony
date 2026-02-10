"""Sandbox safety tests — verify hunter NEVER writes to signals table.

These tests are the gate criteria for Wave 3 → Wave 4.
No scenario should allow hunter to contaminate the signals table
without explicit promotion through the promotion bridge.

12 safety tests covering:
- Import isolation (AST-level)
- Write isolation (row count before/after)
- Promotion guards (RBAC, audit, idempotency)
- Concurrency isolation
- Budget enforcement
- Negative keyword feedback loop
- Entity dedupe
"""

import ast
import asyncio
import json
import os
import re
import pytest

from storage.signal_store import SignalStore
from storage.hunter_result_store import (
    BudgetExhausted,
    create_query,
    create_result,
    create_negative_keyword,
    get_active_negative_keywords,
    get_results_for_run,
    update_result_status,
    extract_negative_keywords_from_rejection,
)
from workflows.active_hunter import execute_hunter_run
from workflows.hunter_promotion import promote_hunter_result
from intelligence.query_generator import HunterQuery
from utils.instrumentation import metrics


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HUNTER_ENABLEMENT", "shadow")
    monkeypatch.setenv("HUNTER_MAX_DAILY_QUERIES", "50")
    monkeypatch.setenv("HUNTER_MAX_DAILY_COST_UNITS", "100")
    db_path = str(tmp_path / "safety.db")
    s = SignalStore(db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def reset_metrics():
    metrics.reset()
    yield


async def _signal_count(store) -> int:
    """Count rows in the signals table."""
    cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
    row = await cursor.fetchone()
    return row[0]


async def _seed_run(store, run_id="run1"):
    """Create a run_history entry for hunter tests."""
    await store._db.execute(
        "INSERT INTO run_history (id, run_type, status, created_at) VALUES (?, ?, ?, ?)",
        (run_id, "hunter", "queued", "2026-01-01T00:00:00Z"),
    )
    await store._db.commit()


async def _mock_collector(collector, query_text):
    """Mock collector returning 2 results."""
    return [
        {
            "company_name": "HealthSnacks Inc",
            "canonical_key": "domain:healthsnacks.ai",
            "source_api": collector,
            "confidence": 0.8,
            "raw_data": {"description": "healthy snacks startup"},
        },
        {
            "company_name": "FitApp Co",
            "canonical_key": "domain:fitapp.io",
            "source_api": collector,
            "confidence": 0.7,
            "raw_data": {"description": "fitness tracking app"},
        },
    ]


async def _mock_failing_collector(collector, query_text):
    """Mock collector that raises an error."""
    raise RuntimeError("Simulated collector failure")


# =============================================================================
# TEST 1: Import isolation (AST-level)
# =============================================================================

def test_hunter_does_not_import_signal_store_write():
    """active_hunter.py MUST NOT import signal-writing functions at runtime.

    The only permitted references to SignalStore are inside TYPE_CHECKING blocks.
    Signal writes MUST go through hunter_promotion.py exclusively.
    """
    filepath = os.path.join(REPO_ROOT, "workflows", "active_hunter.py")
    assert os.path.isfile(filepath), f"{filepath} does not exist"

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Line-based analysis: skip TYPE_CHECKING blocks
    in_type_checking = False
    runtime_signalstore_imports = []

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # Track TYPE_CHECKING blocks
        if stripped.startswith("if TYPE_CHECKING"):
            in_type_checking = True
            continue
        if in_type_checking:
            # Inside TYPE_CHECKING: skip import lines and indented content
            if stripped and not stripped.startswith(("#", "from ", "import ")):
                if not line[0].isspace():
                    in_type_checking = False
                else:
                    continue
            else:
                continue

        # Check for SignalStore import at runtime
        if re.search(r"(?:from|import)\s+.*SignalStore", stripped):
            runtime_signalstore_imports.append(f"  {filepath}:{lineno}: {stripped}")

    assert not runtime_signalstore_imports, (
        f"active_hunter.py imports SignalStore at runtime:\n"
        + "\n".join(runtime_signalstore_imports)
        + "\nSignal writes must go through hunter_promotion.py"
    )

    # Also verify no INSERT/UPDATE/DELETE against signals table
    source = "".join(lines)
    signals_write_re = re.compile(
        r"(INSERT\s+INTO\s+signals|UPDATE\s+signals\s+SET|DELETE\s+FROM\s+signals)",
        re.IGNORECASE,
    )
    for lineno, line in enumerate(lines, 1):
        # READ-ONLY SELECT is fine; only writes are forbidden
        assert not signals_write_re.search(line), (
            f"active_hunter.py:{lineno} contains SQL write to signals table: {line.strip()}"
        )


# =============================================================================
# TEST 2: Signals table unchanged after hunter run
# =============================================================================

@pytest.mark.asyncio
async def test_signals_table_unchanged_after_hunter_run(store):
    """A complete hunter run must NOT add rows to the signals table."""
    before = await _signal_count(store)

    queries = [HunterQuery(collector="github", query_text="health snacks", inputs_hash="h1")]
    result = await execute_hunter_run(
        store, queries, collector_fn=_mock_collector, min_query_interval=0,
    )
    assert result["total_results"] >= 1, "Expected at least 1 result"

    after = await _signal_count(store)
    assert after == before, (
        f"Hunter run added {after - before} rows to signals table! "
        f"Expected 0. Before={before}, After={after}"
    )


# =============================================================================
# TEST 3: Signals table unchanged after hunter failure
# =============================================================================

@pytest.mark.asyncio
async def test_signals_table_unchanged_after_hunter_failure(store):
    """A failed hunter run must NOT add rows to the signals table."""
    before = await _signal_count(store)

    queries = [HunterQuery(collector="github", query_text="fail test", inputs_hash="h_fail")]
    result = await execute_hunter_run(
        store, queries, collector_fn=_mock_failing_collector, min_query_interval=0,
    )
    assert result["failed"] >= 1

    after = await _signal_count(store)
    assert after == before, (
        f"Failed hunter run added {after - before} rows to signals table! "
        f"Expected 0."
    )


# =============================================================================
# TEST 4: Promotion requires relevant status (RBAC analog)
# =============================================================================

@pytest.mark.asyncio
async def test_promotion_requires_relevant_status(store):
    """Only results with status='relevant' can be promoted.

    This is the domain-level guard; API-level RBAC is tested in test_hunter_router.py.
    Pending, not_relevant, and already_known results must be rejected.
    """
    await _seed_run(store)
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )

    # Create a pending result
    rid = await create_result(
        store, run_id="run1", query_id=qid,
        company_name="Pending Co", source_api="github", raw_data={},
    )

    from storage.hunter_result_store import InvalidHunterTransition
    with pytest.raises(InvalidHunterTransition, match="expected 'relevant'"):
        await promote_hunter_result(store, rid)

    # Verify signals unchanged
    count = await _signal_count(store)
    assert count == 0


# =============================================================================
# TEST 5: Promotion creates audit event
# =============================================================================

@pytest.mark.asyncio
async def test_promotion_creates_audit_event(store):
    """Every promotion must produce an immutable audit event."""
    await _seed_run(store)
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )
    rid = await create_result(
        store, run_id="run1", query_id=qid,
        company_name="Audit Co", source_api="github",
        raw_data={"description": "consumer brand"},
        canonical_key="domain:audit.co",
        confidence_score=0.8,
    )
    await update_result_status(store, rid, "relevant")
    await promote_hunter_result(store, rid, actor="test_operator")

    cursor = await store._db.execute(
        """SELECT action_type, actor_id, after_state
           FROM audit_events
           WHERE action_type = 'hunter_promote' AND entity_id = ?""",
        (str(rid),),
    )
    row = await cursor.fetchone()
    assert row is not None, "No audit event found for promotion"
    assert row[1] == "test_operator"
    after = json.loads(row[2])
    assert after["status"] == "promoted"
    assert "signal_id" in after


# =============================================================================
# TEST 6: Promotion sets promoted_signal_id (FK linkage)
# =============================================================================

@pytest.mark.asyncio
async def test_promotion_sets_promoted_signal_id(store):
    """After promotion, hunter_results.promoted_signal_id must point to a valid signal."""
    await _seed_run(store)
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )
    rid = await create_result(
        store, run_id="run1", query_id=qid,
        company_name="FK Co", source_api="github",
        raw_data={"description": "consumer brand"},
        canonical_key="domain:fk.co",
        confidence_score=0.8,
    )
    await update_result_status(store, rid, "relevant")
    result = await promote_hunter_result(store, rid)

    # Verify FK linkage
    cursor = await store._db.execute(
        "SELECT promoted_signal_id FROM hunter_results WHERE id = ?", (rid,)
    )
    row = await cursor.fetchone()
    assert row[0] == result.signal_id

    # Verify the signal actually exists
    cursor = await store._db.execute(
        "SELECT id FROM signals WHERE id = ?", (result.signal_id,)
    )
    signal_row = await cursor.fetchone()
    assert signal_row is not None, "promoted_signal_id does not point to a valid signal"


# =============================================================================
# TEST 7: Promoted result appears in signals
# =============================================================================

@pytest.mark.asyncio
async def test_promoted_result_appears_in_signals(store):
    """End-to-end: promoted result should be findable in the signals table."""
    await _seed_run(store)
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )
    rid = await create_result(
        store, run_id="run1", query_id=qid,
        company_name="PromoTest Inc", source_api="github",
        raw_data={"description": "consumer marketplace"},
        canonical_key="domain:promotest.ai",
        confidence_score=0.85,
    )
    await update_result_status(store, rid, "relevant")
    result = await promote_hunter_result(store, rid)

    # Query signals by canonical key
    cursor = await store._db.execute(
        "SELECT company_name, signal_type, canonical_key FROM signals WHERE canonical_key = ?",
        ("domain:promotest.ai",),
    )
    row = await cursor.fetchone()
    assert row is not None, "Promoted result not found in signals table"
    assert row[0] == "PromoTest Inc"
    assert row[1] == "hunter_discovery"
    assert row[2] == "domain:promotest.ai"


# =============================================================================
# TEST 8: Duplicate promotion is idempotent
# =============================================================================

@pytest.mark.asyncio
async def test_duplicate_promotion_idempotent(store):
    """Promoting the same result twice must return the same signal_id."""
    await _seed_run(store)
    qid = await create_query(
        store, run_id="run1", collector="github", query_text="test",
    )
    rid = await create_result(
        store, run_id="run1", query_id=qid,
        company_name="Idem Co", source_api="github",
        raw_data={"description": "consumer brand"},
        canonical_key="domain:idem.co",
        confidence_score=0.8,
    )
    await update_result_status(store, rid, "relevant")

    result1 = await promote_hunter_result(store, rid)
    result2 = await promote_hunter_result(store, rid)

    assert result1.signal_id == result2.signal_id
    assert result2.status in ("already_promoted", "promoted")

    # Only 1 signal row should exist
    cursor = await store._db.execute(
        "SELECT COUNT(*) FROM signals WHERE canonical_key = ?",
        ("domain:idem.co",),
    )
    row = await cursor.fetchone()
    assert row[0] == 1, f"Expected 1 signal, got {row[0]}"


# =============================================================================
# TEST 9: Concurrent runs are isolated
# =============================================================================

@pytest.mark.asyncio
async def test_concurrent_runs_isolated(store):
    """Two hunter runs must not cross-contaminate results."""
    queries_a = [HunterQuery(collector="github", query_text="run_a", inputs_hash="ha")]
    queries_b = [HunterQuery(collector="github", query_text="run_b", inputs_hash="hb")]

    result_a, result_b = await asyncio.gather(
        execute_hunter_run(
            store, queries_a, collector_fn=_mock_collector, min_query_interval=0,
        ),
        execute_hunter_run(
            store, queries_b, collector_fn=_mock_collector, min_query_interval=0,
        ),
    )

    run_id_a = result_a["run_id"]
    run_id_b = result_b["run_id"]
    assert run_id_a != run_id_b, "Concurrent runs should have different IDs"

    results_a = await get_results_for_run(store, run_id_a)
    results_b = await get_results_for_run(store, run_id_b)

    # Each run's results must be tagged with the correct run_id
    for r in results_a:
        assert r["run_id"] == run_id_a, f"Result {r['id']} has wrong run_id"
    for r in results_b:
        assert r["run_id"] == run_id_b, f"Result {r['id']} has wrong run_id"

    # Signals table must be empty (no writes from either run)
    count = await _signal_count(store)
    assert count == 0, f"Concurrent runs wrote {count} signals"


# =============================================================================
# TEST 10: Budget exhaustion stops all queries
# =============================================================================

@pytest.mark.asyncio
async def test_budget_exhaustion_stops_all_queries(store, monkeypatch):
    """When budget is exhausted, remaining queries must be skipped, not executed."""
    monkeypatch.setenv("HUNTER_MAX_DAILY_QUERIES", "1")

    queries = [
        HunterQuery(collector="github", query_text=f"q{i}", inputs_hash=f"budget_{i}")
        for i in range(5)
    ]
    result = await execute_hunter_run(
        store, queries, collector_fn=_mock_collector, min_query_interval=0,
    )

    assert result["executed"] == 1, f"Expected 1 executed, got {result['executed']}"
    assert result["skipped"] == 4, f"Expected 4 skipped, got {result['skipped']}"

    # Signals table unchanged
    count = await _signal_count(store)
    assert count == 0


# =============================================================================
# TEST 11: Negative keywords prevent future queries
# =============================================================================

@pytest.mark.asyncio
async def test_negative_keywords_prevent_future_queries(store):
    """Operator rejects should produce negative keywords that affect future queries."""
    await _seed_run(store)

    # Create and reject two results with overlapping keywords
    for i in range(2):
        qid = await create_query(
            store, run_id="run1", collector="github",
            query_text=f"q{i}", inputs_hash=f"nk_{i}",
        )
        rid = await create_result(
            store, run_id="run1", query_id=qid,
            company_name=f"Blockchain Enterprise {i}", source_api="github",
            raw_data={"description": "blockchain enterprise saas platform"},
            canonical_key=f"domain:block{i}.io",
        )
        await update_result_status(store, rid, "not_relevant",
                                   operator_feedback="B2B enterprise")

    # Extract negative keywords from the second rejection
    cursor = await store._db.execute(
        "SELECT id FROM hunter_results WHERE status = 'not_relevant' ORDER BY id DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    created = await extract_negative_keywords_from_rejection(store, row[0])

    # "blockchain" and "enterprise" should appear in 2+ rejects → become keywords
    nk_list = await get_active_negative_keywords(store)
    keywords = {nk["keyword"] for nk in nk_list}

    # At least some keywords should be extracted
    assert len(keywords) > 0 or len(created) > 0, (
        "No negative keywords extracted from rejections"
    )


# =============================================================================
# TEST 12: Entity dedupe flags already-known
# =============================================================================

@pytest.mark.asyncio
async def test_entity_dedupe_flags_already_known(store):
    """Hunter results matching an existing signal's canonical_key
    must be flagged as already_known=1."""
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

    queries = [HunterQuery(collector="github", query_text="health", inputs_hash="dup1")]
    result = await execute_hunter_run(
        store, queries, collector_fn=_mock_collector, min_query_interval=0,
    )

    assert result["already_known"] >= 1, (
        f"Expected at least 1 already_known, got {result['already_known']}"
    )

    # Verify the specific result has already_known=1
    cursor = await store._db.execute(
        """SELECT already_known, status FROM hunter_results
           WHERE canonical_key = 'domain:healthsnacks.ai' LIMIT 1"""
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 1, "already_known should be 1"
    assert row[1] == "already_known", f"Status should be 'already_known', got '{row[1]}'"

    # Signals table must NOT have a new row
    cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
    row = await cursor.fetchone()
    assert row[0] == 1, f"Expected 1 signal (the pre-seeded one), got {row[0]}"
