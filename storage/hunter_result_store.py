"""Hunter Result Store — dedicated repository for the Active Hunter sandbox.

Architectural boundary: hunter modules access ONLY this store, never SignalStore
for writes. The single exception is workflows/hunter_promotion.py (bridge module).

Provides:
- State machine enforcement for hunter_queries and hunter_results
- CRUD for queries, results, negative keywords
- Budget reservation/settlement (ledger-backed)
- Cross-run history suppression
- Zombie recovery for stale executing queries
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from storage.audit_events import insert_event

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


# =============================================================================
# STATE MACHINES
# =============================================================================

QUERY_TRANSITIONS: Dict[str, List[str]] = {
    "pending": ["executing", "skipped"],
    "executing": ["completed", "failed"],
    "completed": [],
    "failed": [],
    "skipped": [],
}

RESULT_TRANSITIONS: Dict[str, List[str]] = {
    "pending": ["relevant", "not_relevant", "already_known"],
    "relevant": ["promoted", "not_relevant"],
    "not_relevant": [],
    "already_known": [],
    "promoted": [],
}


class InvalidHunterTransition(Exception):
    """Raised when an invalid hunter state transition is attempted."""
    pass


class BudgetExhausted(Exception):
    """Raised when hunter budget is exhausted for the period."""
    pass


# =============================================================================
# DEDUPE KEY COMPUTATION
# =============================================================================

def compute_result_dedupe_key(
    run_id: str,
    query_id: int,
    canonical_key: Optional[str],
    company_name: Optional[str] = None,
    source_api: Optional[str] = None,
    raw_data: Optional[dict] = None,
) -> str:
    """Compute SHA256-based dedupe key with null-canonical fallback.

    Priority: canonical_key > normalized_company_name+source_api > source_api+source_id
    """
    if canonical_key and not canonical_key.startswith("name_loc:"):
        component = canonical_key
    elif company_name and source_api:
        component = f"{company_name.strip().lower()}:{source_api}"
    elif source_api and raw_data:
        source_id = raw_data.get("source_id", raw_data.get("id", ""))
        component = f"{source_api}:{source_id}"
    else:
        component = canonical_key or company_name or "unknown"

    seed = f"{run_id}:{query_id}:{component}"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


# =============================================================================
# QUERY CRUD
# =============================================================================

async def create_query(
    store: "SignalStore",
    *,
    run_id: str,
    collector: str,
    query_text: str,
    query_type: str = "pattern",
    source_pattern: Optional[str] = None,
    inputs_hash: Optional[str] = None,
    cost_units_reserved: float = 0.0,
    timeout_seconds: int = 30,
    metadata: Optional[dict] = None,
) -> int:
    """Create a hunter query in pending status. Returns query ID."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    cursor = await db.execute(
        """INSERT INTO hunter_queries
           (run_id, collector, query_text, query_type, source_pattern,
            inputs_hash, cost_units_reserved, timeout_seconds, created_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id, collector, query_text, query_type, source_pattern,
            inputs_hash, cost_units_reserved, timeout_seconds, now,
            json.dumps(metadata) if metadata else None,
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def update_query_status(
    store: "SignalStore",
    query_id: int,
    new_status: str,
    *,
    error_message: Optional[str] = None,
    results_count: Optional[int] = None,
    cost_units_final: Optional[float] = None,
) -> None:
    """Transition a query status with state machine validation."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "SELECT status FROM hunter_queries WHERE id = ?", (query_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise ValueError(f"Hunter query {query_id} not found")

    current = row[0]
    allowed = QUERY_TRANSITIONS.get(current, [])
    if new_status not in allowed:
        raise InvalidHunterTransition(
            f"Cannot transition query {query_id} from '{current}' to '{new_status}'. "
            f"Allowed: {allowed}"
        )

    fields = ["status = ?", "completed_at = ?"]
    values: list = [new_status, now]

    if new_status == "executing":
        fields = ["status = ?", "executed_at = ?"]
        values = [new_status, now]
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if results_count is not None:
        fields.append("results_count = ?")
        values.append(results_count)
    if cost_units_final is not None:
        fields.append("cost_units_final = ?")
        values.append(cost_units_final)

    values.append(query_id)
    await db.execute(
        f"UPDATE hunter_queries SET {', '.join(fields)} WHERE id = ?",
        tuple(values),
    )
    await db.commit()


async def get_queries_for_run(
    store: "SignalStore",
    run_id: str,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get all queries for a run, optionally filtered by status."""
    db = store._db
    if status:
        cursor = await db.execute(
            """SELECT id, run_id, collector, query_text, query_type, source_pattern,
                      status, results_count, cost_units_reserved, cost_units_final,
                      inputs_hash, timeout_seconds, created_at, executed_at,
                      completed_at, error_message, metadata
               FROM hunter_queries WHERE run_id = ? AND status = ?
               ORDER BY created_at ASC, id ASC""",
            (run_id, status),
        )
    else:
        cursor = await db.execute(
            """SELECT id, run_id, collector, query_text, query_type, source_pattern,
                      status, results_count, cost_units_reserved, cost_units_final,
                      inputs_hash, timeout_seconds, created_at, executed_at,
                      completed_at, error_message, metadata
               FROM hunter_queries WHERE run_id = ?
               ORDER BY created_at ASC, id ASC""",
            (run_id,),
        )
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0], "run_id": r[1], "collector": r[2], "query_text": r[3],
            "query_type": r[4], "source_pattern": r[5], "status": r[6],
            "results_count": r[7], "cost_units_reserved": r[8],
            "cost_units_final": r[9], "inputs_hash": r[10],
            "timeout_seconds": r[11], "created_at": r[12], "executed_at": r[13],
            "completed_at": r[14], "error_message": r[15],
            "metadata": json.loads(r[16]) if r[16] else None,
        }
        for r in rows
    ]


# =============================================================================
# RESULT CRUD
# =============================================================================

async def create_result(
    store: "SignalStore",
    *,
    run_id: str,
    query_id: int,
    company_name: str,
    source_api: str,
    raw_data: dict,
    canonical_key: Optional[str] = None,
    company_id: Optional[str] = None,
    confidence_score: Optional[float] = None,
    exemplar_similarity: Optional[float] = None,
    thesis_fit_score: Optional[float] = None,
    already_known: bool = False,
    status: str = "pending",
    metadata: Optional[dict] = None,
) -> int:
    """Create a hunter result with computed dedupe key. Returns result ID."""
    now = datetime.now(timezone.utc).isoformat()
    dedupe_key = compute_result_dedupe_key(
        run_id, query_id, canonical_key, company_name, source_api, raw_data,
    )

    if already_known:
        status = "already_known"

    db = store._db
    cursor = await db.execute(
        """INSERT INTO hunter_results
           (run_id, query_id, result_dedupe_key, company_name, canonical_key,
            company_id, source_api, raw_data, confidence_score, exemplar_similarity,
            thesis_fit_score, already_known, status, created_at, updated_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id, query_id, dedupe_key, company_name, canonical_key,
            company_id, source_api, json.dumps(raw_data), confidence_score,
            exemplar_similarity, thesis_fit_score, 1 if already_known else 0,
            status, now, now,
            json.dumps(metadata) if metadata else None,
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_results_for_run(
    store: "SignalStore",
    run_id: str,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Get results for a run, optionally filtered by status."""
    db = store._db
    if status:
        cursor = await db.execute(
            """SELECT id, run_id, query_id, result_dedupe_key, company_name,
                      canonical_key, company_id, source_api, raw_data,
                      confidence_score, exemplar_similarity, thesis_fit_score,
                      already_known, status, operator_feedback, promoted_signal_id,
                      created_at, reviewed_at, promoted_at, updated_at, metadata
               FROM hunter_results WHERE run_id = ? AND status = ?
               ORDER BY thesis_fit_score DESC, created_at DESC, id DESC
               LIMIT ?""",
            (run_id, status, limit),
        )
    else:
        cursor = await db.execute(
            """SELECT id, run_id, query_id, result_dedupe_key, company_name,
                      canonical_key, company_id, source_api, raw_data,
                      confidence_score, exemplar_similarity, thesis_fit_score,
                      already_known, status, operator_feedback, promoted_signal_id,
                      created_at, reviewed_at, promoted_at, updated_at, metadata
               FROM hunter_results WHERE run_id = ?
               ORDER BY thesis_fit_score DESC, created_at DESC, id DESC
               LIMIT ?""",
            (run_id, limit),
        )
    rows = await cursor.fetchall()
    return [_result_row_to_dict(r) for r in rows]


async def get_result_by_id(
    store: "SignalStore",
    result_id: int,
) -> Optional[Dict[str, Any]]:
    """Get a single result by ID."""
    db = store._db
    cursor = await db.execute(
        """SELECT id, run_id, query_id, result_dedupe_key, company_name,
                  canonical_key, company_id, source_api, raw_data,
                  confidence_score, exemplar_similarity, thesis_fit_score,
                  already_known, status, operator_feedback, promoted_signal_id,
                  created_at, reviewed_at, promoted_at, updated_at, metadata
           FROM hunter_results WHERE id = ?""",
        (result_id,),
    )
    row = await cursor.fetchone()
    return _result_row_to_dict(row) if row else None


def _result_row_to_dict(r) -> Dict[str, Any]:
    return {
        "id": r[0], "run_id": r[1], "query_id": r[2],
        "result_dedupe_key": r[3], "company_name": r[4],
        "canonical_key": r[5], "company_id": r[6], "source_api": r[7],
        "raw_data": json.loads(r[8]) if r[8] else {},
        "confidence_score": r[9], "exemplar_similarity": r[10],
        "thesis_fit_score": r[11], "already_known": bool(r[12]),
        "status": r[13], "operator_feedback": r[14],
        "promoted_signal_id": r[15], "created_at": r[16],
        "reviewed_at": r[17], "promoted_at": r[18],
        "updated_at": r[19],
        "metadata": json.loads(r[20]) if r[20] else None,
    }


async def update_result_status(
    store: "SignalStore",
    result_id: int,
    new_status: str,
    *,
    operator_feedback: Optional[str] = None,
    promoted_signal_id: Optional[int] = None,
    expected_updated_at: Optional[str] = None,
    actor: str = "system",
) -> None:
    """Transition a result status with state machine + optimistic concurrency."""
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "SELECT status, updated_at FROM hunter_results WHERE id = ?",
        (result_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise ValueError(f"Hunter result {result_id} not found")

    current_status, current_updated_at = row[0], row[1]

    # Optimistic concurrency check
    if expected_updated_at and current_updated_at != expected_updated_at:
        raise StaleUpdateError(
            f"Result {result_id} was modified. "
            f"Expected updated_at={expected_updated_at}, "
            f"got {current_updated_at}"
        )

    allowed = RESULT_TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        raise InvalidHunterTransition(
            f"Cannot transition result {result_id} from '{current_status}' "
            f"to '{new_status}'. Allowed: {allowed}"
        )

    fields = ["status = ?", "updated_at = ?"]
    values: list = [new_status, now]

    if operator_feedback is not None:
        fields.append("operator_feedback = ?")
        values.append(operator_feedback)
    if new_status in ("relevant", "not_relevant", "already_known"):
        fields.append("reviewed_at = ?")
        values.append(now)
    if new_status == "promoted":
        fields.append("promoted_at = ?")
        values.append(now)
    if promoted_signal_id is not None:
        fields.append("promoted_signal_id = ?")
        values.append(promoted_signal_id)

    values.append(result_id)
    await db.execute(
        f"UPDATE hunter_results SET {', '.join(fields)} WHERE id = ?",
        tuple(values),
    )

    # Audit event
    await insert_event(
        db,
        action_type="hunter_feedback" if new_status != "promoted" else "hunter_promote",
        entity_type="hunter_result",
        entity_id=str(result_id),
        actor_id=actor,
        before_state={"status": current_status},
        after_state={"status": new_status, "feedback": operator_feedback},
    )
    await db.commit()


class StaleUpdateError(Exception):
    """Raised on optimistic concurrency violation."""
    pass


# =============================================================================
# CROSS-RUN HISTORY
# =============================================================================

async def check_historical_canonical(
    store: "SignalStore",
    canonical_key: str,
    ttl_days: int = 90,
) -> Optional[str]:
    """Check if canonical_key has a terminal status in prior hunter results within TTL.

    Returns the terminal status if found, None otherwise.
    """
    if not canonical_key:
        return None

    db = store._db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    cursor = await db.execute(
        """SELECT status FROM hunter_results
           WHERE canonical_key = ?
           AND status IN ('not_relevant', 'already_known', 'promoted')
           AND created_at >= ?
           ORDER BY created_at DESC, id DESC
           LIMIT 1""",
        (canonical_key, cutoff),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


# =============================================================================
# ZOMBIE RECOVERY
# =============================================================================

async def recover_stale_queries(
    store: "SignalStore",
    cutoff_minutes: int = 15,
) -> int:
    """Recover stale 'executing' queries by setting them to 'failed'.

    Returns count of recovered queries.
    """
    db = store._db
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cutoff_minutes)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        """UPDATE hunter_queries
           SET status = 'failed',
               error_message = 'Zombie recovery: stale executing query',
               completed_at = ?
           WHERE status = 'executing'
           AND executed_at < ?""",
        (now, cutoff),
    )
    count = cursor.rowcount
    if count > 0:
        await insert_event(
            db,
            action_type="hunter_zombie_cleanup",
            entity_type="hunter_query",
            entity_id="batch",
            actor_id="system",
            metadata={"recovered_count": count, "cutoff_minutes": cutoff_minutes},
        )
    await db.commit()
    logger.info("Zombie recovery: %d stale queries recovered", count)
    return count


# =============================================================================
# BUDGET MANAGEMENT (LEDGER-BACKED)
# =============================================================================

async def check_and_reserve_budget(
    store: "SignalStore",
    collector: str,
    budget_date: str,
    estimated_cost: float,
    run_id: Optional[str] = None,
    query_id: Optional[int] = None,
) -> bool:
    """Atomically reserve budget for one query. Returns True if reserved.

    Raises BudgetExhausted if either per-collector or global cap is exceeded.
    Uses BEGIN IMMEDIATE for atomic reservation.
    """
    queries_cap = int(os.environ.get("HUNTER_MAX_DAILY_QUERIES", "50"))
    cost_cap = float(os.environ.get("HUNTER_MAX_DAILY_COST_UNITS", "100"))
    now = datetime.now(timezone.utc).isoformat()

    async with store.transaction_immediate() as tx:
        # Read current collector budget
        cursor = await tx.execute(
            "SELECT queries_executed, queries_cap FROM hunter_budget WHERE budget_date = ? AND collector = ?",
            (budget_date, collector),
        )
        coll_row = await cursor.fetchone()
        current_queries = coll_row[0] if coll_row else 0
        coll_cap = coll_row[1] if coll_row and coll_row[1] is not None else queries_cap

        # Read global budget
        cursor = await tx.execute(
            "SELECT cost_units, cost_cap FROM hunter_budget WHERE budget_date = ? AND collector = '__global__'",
            (budget_date,),
        )
        global_row = await cursor.fetchone()
        current_cost = global_row[0] if global_row else 0.0
        global_cap = global_row[1] if global_row and global_row[1] is not None else cost_cap

        # Check caps
        if current_queries + 1 > coll_cap:
            raise BudgetExhausted(
                f"Collector {collector} query cap exhausted: "
                f"{current_queries}/{coll_cap} on {budget_date}"
            )
        if current_cost + estimated_cost > global_cap:
            raise BudgetExhausted(
                f"Global cost cap exhausted: "
                f"{current_cost + estimated_cost:.1f}/{global_cap:.1f} on {budget_date}"
            )

        # UPSERT collector row
        await tx.execute(
            """INSERT INTO hunter_budget (budget_date, collector, queries_executed, queries_cap, created_at, updated_at)
               VALUES (?, ?, 1, ?, ?, ?)
               ON CONFLICT(budget_date, collector)
               DO UPDATE SET queries_executed = queries_executed + 1, updated_at = ?""",
            (budget_date, collector, coll_cap, now, now, now),
        )

        # UPSERT global row
        await tx.execute(
            """INSERT INTO hunter_budget (budget_date, collector, cost_units, cost_cap, created_at, updated_at)
               VALUES (?, '__global__', ?, ?, ?, ?)
               ON CONFLICT(budget_date, collector)
               DO UPDATE SET cost_units = cost_units + ?, updated_at = ?""",
            (budget_date, estimated_cost, global_cap, now, now, estimated_cost, now),
        )

        # Append ledger transaction
        await tx.execute(
            """INSERT INTO hunter_budget_transactions
               (budget_date, collector, run_id, query_id, delta_queries, delta_cost, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'reserve', ?)""",
            (budget_date, collector, run_id, query_id, 1, estimated_cost, now),
        )

    return True


async def settle_budget(
    store: "SignalStore",
    collector: str,
    budget_date: str,
    query_id: int,
    actual_cost: float,
    estimated_cost: float,
    run_id: Optional[str] = None,
) -> None:
    """Settle actual cost vs reserved. Logs overrun if significant."""
    delta = actual_cost - estimated_cost
    if abs(delta) < 0.001:
        return  # No adjustment needed

    cost_cap = float(os.environ.get("HUNTER_MAX_DAILY_COST_UNITS", "100"))
    now = datetime.now(timezone.utc).isoformat()
    db = store._db

    # Append settlement transaction
    await db.execute(
        """INSERT INTO hunter_budget_transactions
           (budget_date, collector, run_id, query_id, delta_queries, delta_cost, reason, created_at)
           VALUES (?, ?, ?, ?, 0, ?, 'settle', ?)""",
        (budget_date, collector, run_id, query_id, delta, now),
    )

    # Adjust global cost_units
    await db.execute(
        """UPDATE hunter_budget SET cost_units = cost_units + ?, updated_at = ?
           WHERE budget_date = ? AND collector = '__global__'""",
        (delta, now, budget_date),
    )

    # Check for overrun (>10% of cap)
    if delta > 0 and delta > cost_cap * 0.10:
        await insert_event(
            db,
            action_type="hunter_budget_overrun",
            entity_type="hunter_budget",
            entity_id=f"{budget_date}:{collector}",
            actor_id="system",
            metadata={
                "query_id": query_id,
                "estimated": estimated_cost,
                "actual": actual_cost,
                "delta": delta,
                "cap": cost_cap,
            },
        )

    await db.commit()


async def get_budget_summary(
    store: "SignalStore",
    budget_date: str,
) -> Dict[str, Any]:
    """Get budget summary for a date."""
    db = store._db
    cursor = await db.execute(
        """SELECT collector, queries_executed, queries_cap, cost_units, cost_cap,
                  circuit_breaker_tripped
           FROM hunter_budget WHERE budget_date = ?""",
        (budget_date,),
    )
    rows = await cursor.fetchall()
    collectors = {}
    global_info = {}
    for r in rows:
        entry = {
            "queries_executed": r[1], "queries_cap": r[2],
            "cost_units": r[3], "cost_cap": r[4],
            "circuit_breaker_tripped": bool(r[5]),
        }
        if r[0] == "__global__":
            global_info = entry
        else:
            collectors[r[0]] = entry

    return {
        "budget_date": budget_date,
        "global": global_info,
        "collectors": collectors,
    }


# =============================================================================
# NEGATIVE KEYWORDS
# =============================================================================

async def get_active_negative_keywords(
    store: "SignalStore",
    collector: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get active negative keywords, optionally filtered by collector."""
    db = store._db
    if collector:
        cursor = await db.execute(
            """SELECT id, keyword, collector, category, source,
                      source_result_id, review_required, created_at
               FROM hunter_negative_keywords
               WHERE active = 1 AND (collector IS NULL OR collector = ?)
               ORDER BY keyword""",
            (collector,),
        )
    else:
        cursor = await db.execute(
            """SELECT id, keyword, collector, category, source,
                      source_result_id, review_required, created_at
               FROM hunter_negative_keywords
               WHERE active = 1
               ORDER BY keyword""",
        )
    rows = await cursor.fetchall()
    return [
        {
            "id": r[0], "keyword": r[1], "collector": r[2], "category": r[3],
            "source": r[4], "source_result_id": r[5],
            "review_required": bool(r[6]), "created_at": r[7],
        }
        for r in rows
    ]


async def create_negative_keyword(
    store: "SignalStore",
    *,
    keyword: str,
    collector: Optional[str] = None,
    category: Optional[str] = None,
    source: str = "manual",
    source_result_id: Optional[int] = None,
    review_required: bool = False,
    metadata: Optional[dict] = None,
) -> Optional[int]:
    """Create a negative keyword. Returns ID or None if duplicate."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db

    # Check for existing keyword first (avoids exception-based flow)
    cursor = await db.execute(
        """SELECT id FROM hunter_negative_keywords
           WHERE keyword = ? AND collector IS ? AND category IS ?""",
        (keyword.lower().strip(), collector, category),
    )
    existing = await cursor.fetchone()
    if existing:
        return None

    cursor = await db.execute(
        """INSERT INTO hunter_negative_keywords
           (keyword, collector, category, source, source_result_id,
            review_required, created_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            keyword.lower().strip(), collector, category, source,
            source_result_id, 1 if review_required else 0, now,
            json.dumps(metadata) if metadata else None,
        ),
    )
    nk_id = cursor.lastrowid

    # Audit
    await insert_event(
        db,
        action_type="hunter_neg_keyword_add",
        entity_type="hunter_negative_keyword",
        entity_id=str(nk_id),
        actor_id="system",
        metadata={"keyword": keyword, "collector": collector, "source": source},
    )
    await db.commit()
    return nk_id


# =============================================================================
# NEGATIVE KEYWORD EXTRACTION (on not_relevant feedback)
# =============================================================================

# Protected vocabulary — words that can NEVER become negative keywords
_PROTECTED_VOCABULARY = frozenset({
    "health", "fitness", "food", "beauty", "travel", "marketplace",
    "wellness", "nutrition", "beverage", "skincare", "hospitality",
    "consumer", "cpg", "supplement", "restaurant", "booking",
    "shopping", "retail", "ecommerce",
})

_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "that", "this", "was", "are",
    "be", "has", "have", "had", "not", "no", "inc", "llc", "ltd", "co",
    "company", "com", "www", "http", "https",
})


def _extract_candidate_keywords(company_name: str, raw_data: dict) -> List[str]:
    """Extract candidate negative keywords from a rejected result."""
    import re
    text = company_name or ""
    description = ""
    if isinstance(raw_data, dict):
        description = raw_data.get("description", "")
    if isinstance(description, str):
        text = f"{text} {description}"

    words = re.findall(r"[a-z]+", text.lower())
    words = [w for w in words if len(w) >= 3 and w not in _STOP_WORDS and w not in _PROTECTED_VOCABULARY]
    return list(set(words))


async def extract_negative_keywords_from_rejection(
    store: "SignalStore",
    result_id: int,
    min_occurrences: int = 2,
) -> List[str]:
    """Extract and create negative keywords from a rejected result.

    Only creates keywords that appear in 2+ rejected results (prevents single-reject overfitting).
    All auto-extracted keywords have review_required=1.

    Returns list of newly created keywords.
    """
    db = store._db

    # Get the rejected result
    result = await get_result_by_id(store, result_id)
    if not result or result["status"] != "not_relevant":
        return []

    # Extract candidate words
    candidates = _extract_candidate_keywords(result["company_name"], result["raw_data"])
    if not candidates:
        return []

    # Count occurrences across all rejected results
    cursor = await db.execute(
        """SELECT company_name, raw_data FROM hunter_results
           WHERE status = 'not_relevant'"""
    )
    rows = await cursor.fetchall()

    from collections import Counter
    all_words: Counter = Counter()
    for row in rows:
        name = row[0] or ""
        try:
            rd = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        except (ValueError, TypeError):
            rd = {}
        words = _extract_candidate_keywords(name, rd)
        all_words.update(words)

    # Only keep words that appear in min_occurrences+ rejections
    frequent = {w for w in candidates if all_words.get(w, 0) >= min_occurrences}

    created = []
    for word in sorted(frequent):
        nk_id = await create_negative_keyword(
            store,
            keyword=word,
            collector=result["source_api"],
            source="operator_reject",
            source_result_id=result_id,
            review_required=True,
        )
        if nk_id is not None:
            created.append(word)

    return created
