"""Active Hunter — sandbox execution engine for pattern-driven deal sourcing.

Execution flow:
1. Zombie recovery on startup
2. Create run via run_manager
3. For each HunterQuery:
   a. Reserve budget
   b. Execute collector search
   c. Score and store results
   d. Settle budget
4. Complete or fail run

Isolation: This module NEVER writes to the signals table.
The only path to signals is via workflows/hunter_promotion.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from intelligence.query_generator import HunterQuery
from storage.hunter_result_store import (
    BudgetExhausted,
    check_and_reserve_budget,
    check_historical_canonical,
    create_query,
    create_result,
    recover_stale_queries,
    settle_budget,
    update_query_status,
)
from utils.instrumentation import metrics
from workflows.run_manager import RunType, create_run, start_run, complete_run, fail_run

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Feature flag
HUNTER_ENABLEMENT_MODES = {"disabled", "shadow", "active"}

# Default inter-query delay (seconds)
DEFAULT_QUERY_INTERVAL = 2


def get_hunter_enablement() -> str:
    """Get current hunter enablement mode from environment."""
    mode = os.environ.get("HUNTER_ENABLEMENT", "disabled")
    if mode not in HUNTER_ENABLEMENT_MODES:
        logger.warning("Invalid HUNTER_ENABLEMENT=%s, defaulting to disabled", mode)
        return "disabled"
    return mode


async def execute_hunter_run(
    store: "SignalStore",
    queries: List[HunterQuery],
    *,
    collector_fn: Optional[Callable] = None,
    actor_id: str = "system",
    dry_run: bool = False,
    min_query_interval: float = DEFAULT_QUERY_INTERVAL,
) -> Dict[str, Any]:
    """Execute a full hunter run.

    Args:
        store: Initialized SignalStore
        queries: List of HunterQuery objects to execute
        collector_fn: Async callable(collector, query_text) -> List[dict]
            Each dict must have: company_name, source_api, and optionally:
            canonical_key, confidence, raw_data
        actor_id: Who initiated this run
        dry_run: If True, create queries but don't execute
        min_query_interval: Seconds between queries

    Returns:
        Run summary dict with stats.
    """
    enablement = get_hunter_enablement()
    if enablement == "disabled":
        return {"status": "disabled", "message": "HUNTER_ENABLEMENT is disabled"}

    # 1. Zombie recovery
    zombies = await recover_stale_queries(store)
    if zombies:
        logger.info("Recovered %d zombie queries", zombies)

    # 2. Create run
    run_record = await create_run(
        store,
        run_type=RunType.HUNTER.value,
        actor_id=actor_id,
        inputs_summary={"query_count": len(queries), "dry_run": dry_run},
    )
    run_id = run_record.id
    await start_run(store, run_id)

    metrics.increment("hunter.run.started")

    budget_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = {
        "run_id": run_id,
        "total_queries": len(queries),
        "executed": 0,
        "skipped": 0,
        "failed": 0,
        "total_results": 0,
        "already_known": 0,
        "new_results": 0,
        "zombies_recovered": zombies,
    }

    try:
        for i, hq in enumerate(queries):
            # 3a. Create query record
            qid = await create_query(
                store,
                run_id=run_id,
                collector=hq.collector,
                query_text=hq.query_text,
                query_type=hq.query_type,
                source_pattern=hq.source_pattern,
                inputs_hash=hq.inputs_hash,
                cost_units_reserved=hq.estimated_cost,
                timeout_seconds=hq.timeout_seconds,
            )

            if dry_run:
                await update_query_status(store, qid, "skipped")
                stats["skipped"] += 1
                continue

            # 3b. Reserve budget
            try:
                await check_and_reserve_budget(
                    store, hq.collector, budget_date,
                    hq.estimated_cost, run_id=run_id, query_id=qid,
                )
            except BudgetExhausted as exc:
                logger.warning("Budget exhausted for %s: %s", hq.collector, exc)
                await update_query_status(store, qid, "skipped")
                metrics.increment("hunter.query.skipped")
                stats["skipped"] += 1
                continue

            # 3c. Execute
            await update_query_status(store, qid, "executing")

            try:
                if collector_fn is None:
                    raw_results = []
                else:
                    raw_results = await asyncio.wait_for(
                        collector_fn(hq.collector, hq.query_text),
                        timeout=hq.timeout_seconds,
                    )

                # 3d. Process results
                results_count = 0
                for raw in (raw_results or []):
                    company_name = raw.get("company_name", "")
                    canonical_key = raw.get("canonical_key") or ""
                    source_api = raw.get("source_api", hq.collector)
                    raw_data = raw.get("raw_data", raw)

                    # Normalize blank/Unknown company names
                    if not company_name or company_name.strip().lower() == "unknown":
                        company_name = ""

                    # Skip results without identity
                    if not canonical_key:
                        logger.debug(
                            "Skipping result with no canonical_key: %s",
                            (raw_data or {}).get("title", "(no title)"),
                        )
                        metrics.increment("hunter.result.skip_no_identity")
                        continue

                    # Cross-run history check (90-day TTL)
                    if canonical_key:
                        prior_status = await check_historical_canonical(
                            store, canonical_key,
                        )
                        if prior_status:
                            logger.debug(
                                "Skipping %s: prior status %s",
                                canonical_key, prior_status,
                            )
                            metrics.increment("hunter.result.history_suppressed")
                            continue

                    # Check "already known" (READ-ONLY against signals)
                    is_known = False
                    if canonical_key:
                        cursor = await store._db.execute(
                            "SELECT 1 FROM signals WHERE canonical_key = ? LIMIT 1",
                            (canonical_key,),
                        )
                        is_known = (await cursor.fetchone()) is not None

                    await create_result(
                        store,
                        run_id=run_id,
                        query_id=qid,
                        company_name=company_name,
                        source_api=source_api,
                        raw_data=raw_data if isinstance(raw_data, dict) else {"data": raw_data},
                        canonical_key=canonical_key,
                        confidence_score=raw.get("confidence"),
                        already_known=is_known,
                    )
                    results_count += 1
                    if is_known:
                        stats["already_known"] += 1
                    else:
                        stats["new_results"] += 1

                # 3e. Settle budget
                actual_cost = hq.estimated_cost  # In v1, actual == estimated
                await settle_budget(
                    store, hq.collector, budget_date, qid,
                    actual_cost, hq.estimated_cost, run_id=run_id,
                )

                # 3f. Update query
                await update_query_status(
                    store, qid, "completed", results_count=results_count,
                    cost_units_final=actual_cost,
                )
                stats["executed"] += 1
                stats["total_results"] += results_count
                metrics.increment("hunter.query.success")

            except asyncio.TimeoutError:
                await update_query_status(
                    store, qid, "failed",
                    error_message=f"Timeout after {hq.timeout_seconds}s",
                )
                stats["failed"] += 1
                metrics.increment("hunter.query.timeout")

            except Exception as exc:
                logger.error("Query %d failed: %s", qid, exc)
                await update_query_status(
                    store, qid, "failed", error_message=str(exc)[:500],
                )
                stats["failed"] += 1
                metrics.increment("hunter.query.error")

            # Inter-query delay
            if i < len(queries) - 1 and min_query_interval > 0:
                await asyncio.sleep(min_query_interval)

        # 4. Complete run
        await complete_run(store, run_id, result=stats)
        metrics.increment("hunter.run.completed")

    except Exception as exc:
        logger.error("Hunter run %s failed: %s", run_id, exc)
        await fail_run(store, run_id, error_message=str(exc)[:500])
        metrics.increment("hunter.run.failed")
        stats["error"] = str(exc)

    return stats
