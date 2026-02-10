"""
Shadow Entity Evaluator — Read-only comparison of Phase 1a vs Phase G identity.

Runs Phase G entity resolution in shadow mode alongside production Phase 1a
identity, comparing groupings and logging disagreements. All operations are
read-only with respect to production identity tables.

Key components:
- ShadowRunConfig: Tuning knobs (max_signals, timeout, sampling)
- ShadowDisagreement / ShadowRunResult: Data classes
- run_shadow_comparison(): Core comparison logic
- store_shadow_run(): Atomic persistence (BEGIN IMMEDIATE)

Feature flag: ``shadow_entity_resolution`` in FeatureRegistry (OFF by default).
Env override: ``FEATURE_SHADOW_ENTITY_RESOLUTION=shadow``
Pipeline env: ``USE_SHADOW_ENTITY_RESOLUTION=1``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore, StoredSignal
    from storage.readonly_identity_store import ReadOnlyIdentityStore

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class ShadowRunConfig:
    """Configuration knobs for shadow entity comparison runs.

    All values can be overridden via environment variables.
    """

    max_signals_per_run: int = 500
    sample_rate: float = 1.0
    timeout_seconds: float = 30.0
    max_disagreements_stored: int = 1000
    min_similarity_threshold: float = 0.85
    max_suggestions_per_run: int = 100

    def __post_init__(self) -> None:
        if not 0.0 < self.sample_rate <= 1.0:
            raise ValueError(f"sample_rate must be in (0.0, 1.0], got {self.sample_rate}")
        if self.max_signals_per_run < 1:
            raise ValueError(f"max_signals_per_run must be >= 1, got {self.max_signals_per_run}")
        if self.timeout_seconds < 1.0:
            raise ValueError(f"timeout_seconds must be >= 1.0, got {self.timeout_seconds}")

    @classmethod
    def from_env(cls) -> ShadowRunConfig:
        """Build config from environment variables with defaults."""
        return cls(
            max_signals_per_run=int(os.getenv("SHADOW_MAX_SIGNALS", "500")),
            sample_rate=float(os.getenv("SHADOW_SAMPLE_RATE", "1.0")),
            timeout_seconds=float(os.getenv("SHADOW_TIMEOUT_SECONDS", "30")),
            max_disagreements_stored=int(os.getenv("SHADOW_MAX_DISAGREEMENTS", "1000")),
            min_similarity_threshold=float(os.getenv("SHADOW_MIN_SIMILARITY", "0.85")),
            max_suggestions_per_run=int(os.getenv("SHADOW_MAX_SUGGESTIONS", "100")),
        )


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ShadowDisagreement:
    """A single disagreement between Phase 1a and Phase G grouping."""

    signal_id: int
    canonical_key: str
    phase1a_company_id: Optional[str]
    phase_g_entity_id: Optional[str]
    phase_g_group_key: Optional[str]
    disagreement_type: str  # 'over_merge' or 'over_split'
    collector: Optional[str] = None
    confidence: Optional[float] = None
    confidence_band: Optional[str] = None
    canonical_key_type: Optional[str] = None


@dataclass
class ShadowRunResult:
    """Result of a shadow entity comparison run."""

    run_id: str = ""
    status: str = "completed"  # completed, failed, timeout, skipped
    total_signals: int = 0
    phase1a_groups: int = 0
    phase_g_groups: int = 0
    agreements: int = 0
    disagreements_count: int = 0
    agreement_rate: Optional[float] = None
    disagreements: List[ShadowDisagreement] = field(default_factory=list)
    metrics_json: Optional[str] = None
    duration_ms: float = 0.0
    inputs_hash: Optional[str] = None
    config_json: Optional[str] = None
    error_summary: Optional[str] = None
    truncated: bool = False
    truncation_reason: Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def _confidence_band(confidence: Optional[float]) -> Optional[str]:
    """Map confidence to band: high >= 0.7, medium 0.4-0.7, low < 0.4."""
    if confidence is None:
        return None
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def _canonical_key_type(canonical_key: str) -> Optional[str]:
    """Extract key type prefix from canonical_key (e.g., 'domain' from 'domain:acme.ai')."""
    if ":" in canonical_key:
        return canonical_key.split(":", 1)[0]
    return None


def compute_inputs_hash(signal_ids: List[int]) -> str:
    """Deterministic hash of sorted signal IDs for reproducibility.

    Formula: sha256(sorted([str(s.id) for s in signals]).join("\\x1f")).hexdigest()[:16]
    """
    sorted_ids = sorted(str(sid) for sid in signal_ids)
    payload = "\x1f".join(sorted_ids)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# CORE COMPARISON
# =============================================================================

async def run_shadow_comparison(
    store: "SignalStore",
    ro_identity_store: "ReadOnlyIdentityStore",
    config: Optional[ShadowRunConfig] = None,
) -> ShadowRunResult:
    """Run shadow comparison of Phase 1a vs Phase G entity groupings.

    Args:
        store: SignalStore for reading signals (and Phase 1a company_id).
        ro_identity_store: Read-only identity store for Phase G lookups.
        config: Shadow run configuration (defaults if None).

    Returns:
        ShadowRunResult with disagreements and metrics.
    """
    config = config or ShadowRunConfig()
    start_time = time.perf_counter()
    result = ShadowRunResult(
        config_json=json.dumps({
            "max_signals_per_run": config.max_signals_per_run,
            "sample_rate": config.sample_rate,
            "timeout_seconds": config.timeout_seconds,
        }),
    )

    try:
        # Fetch candidate signals (those with company_id assigned by Phase 1a)
        db = store._db
        cursor = await db.execute(
            """
            SELECT id, canonical_key, company_name, company_id, source_api, confidence
            FROM signals
            WHERE company_id IS NOT NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (config.max_signals_per_run * 2,),  # Fetch extra for sampling
        )
        rows = await cursor.fetchall()

        if not rows:
            result.status = "completed"
            result.total_signals = 0
            result.agreement_rate = 1.0
            result.duration_ms = (time.perf_counter() - start_time) * 1000
            return result

        # Compute inputs_hash over full pre-sampled universe
        all_signal_ids = [row[0] for row in rows]
        result.inputs_hash = compute_inputs_hash(all_signal_ids)

        # Deterministic sampling
        if config.sample_rate < 1.0:
            rng = random.Random(result.inputs_hash)
            rows = [r for r in rows if rng.random() < config.sample_rate]

        # Cap at max_signals_per_run
        rows = rows[: config.max_signals_per_run]
        result.total_signals = len(rows)

        if not rows:
            result.status = "completed"
            result.agreement_rate = 1.0
            result.duration_ms = (time.perf_counter() - start_time) * 1000
            return result

        # Build Phase 1a groupings: company_id → set of signal_ids
        phase1a_groups: Dict[str, set] = {}
        signal_meta: Dict[int, dict] = {}

        for row in rows:
            sig_id, ckey, cname, company_id, source_api, confidence = row
            signal_meta[sig_id] = {
                "canonical_key": ckey,
                "company_name": cname,
                "company_id": company_id,
                "source_api": source_api,
                "confidence": confidence,
            }
            if company_id:
                phase1a_groups.setdefault(company_id, set()).add(sig_id)

        result.phase1a_groups = len(phase1a_groups)

        # Build Phase G groupings via read-only lookups
        # Group signals by their canonical_key → Phase G entity resolution
        canonical_keys = list({m["canonical_key"] for m in signal_meta.values() if m["canonical_key"]})
        phase_g_map: Dict[str, str] = {}  # canonical_key → entity_id

        if canonical_keys:
            # Try strong key lookup
            strong_results = await ro_identity_store.lookup_strong_keys(canonical_keys)
            phase_g_map.update(strong_results)

            # For keys not found via strong lookup, try alias lookup
            remaining = [k for k in canonical_keys if k not in phase_g_map]
            if remaining:
                alias_results = await ro_identity_store.lookup_alias_keys(remaining)
                phase_g_map.update(alias_results)

        # Build Phase G groupings: entity_id → set of signal_ids
        phase_g_groups: Dict[str, set] = {}
        for sig_id, meta in signal_meta.items():
            ckey = meta["canonical_key"]
            entity_id = phase_g_map.get(ckey)
            if entity_id:
                phase_g_groups.setdefault(entity_id, set()).add(sig_id)

        result.phase_g_groups = len(phase_g_groups)

        # Compare groupings — classify disagreements
        disagreements: List[ShadowDisagreement] = []
        agreements = 0

        for sig_id, meta in signal_meta.items():
            ckey = meta["canonical_key"]
            p1a_id = meta["company_id"]
            pg_id = phase_g_map.get(ckey)

            if not p1a_id or not pg_id:
                # Can't compare if either side is missing
                agreements += 1
                continue

            # Get group memberships
            p1a_group = phase1a_groups.get(p1a_id, set())
            pg_group = phase_g_groups.get(pg_id, set())

            if p1a_group == pg_group:
                agreements += 1
            elif pg_group.issuperset(p1a_group) and len(pg_group) > len(p1a_group):
                # Phase G merges what Phase 1a keeps separate → over_merge
                disagreements.append(ShadowDisagreement(
                    signal_id=sig_id,
                    canonical_key=ckey,
                    phase1a_company_id=p1a_id,
                    phase_g_entity_id=pg_id,
                    phase_g_group_key=ckey,
                    disagreement_type="over_merge",
                    collector=meta.get("source_api"),
                    confidence=meta.get("confidence"),
                    confidence_band=_confidence_band(meta.get("confidence")),
                    canonical_key_type=_canonical_key_type(ckey),
                ))
            elif p1a_group.issuperset(pg_group) and len(p1a_group) > len(pg_group):
                # Phase 1a groups what Phase G separates → over_split
                disagreements.append(ShadowDisagreement(
                    signal_id=sig_id,
                    canonical_key=ckey,
                    phase1a_company_id=p1a_id,
                    phase_g_entity_id=pg_id,
                    phase_g_group_key=ckey,
                    disagreement_type="over_split",
                    collector=meta.get("source_api"),
                    confidence=meta.get("confidence"),
                    confidence_band=_confidence_band(meta.get("confidence")),
                    canonical_key_type=_canonical_key_type(ckey),
                ))
            else:
                # Groups differ but neither is a superset — counts as disagreement
                disagreements.append(ShadowDisagreement(
                    signal_id=sig_id,
                    canonical_key=ckey,
                    phase1a_company_id=p1a_id,
                    phase_g_entity_id=pg_id,
                    phase_g_group_key=ckey,
                    disagreement_type="over_merge",  # Default to over_merge for mixed
                    collector=meta.get("source_api"),
                    confidence=meta.get("confidence"),
                    confidence_band=_confidence_band(meta.get("confidence")),
                    canonical_key_type=_canonical_key_type(ckey),
                ))

        # Truncation
        actual_count = len(disagreements)
        if actual_count > config.max_disagreements_stored:
            result.truncated = True
            result.truncation_reason = f"max_disagreements_exceeded:{actual_count}"
            disagreements = disagreements[: config.max_disagreements_stored]

        result.agreements = agreements
        result.disagreements_count = actual_count
        result.disagreements = disagreements

        total_scorable = agreements + actual_count
        result.agreement_rate = (
            agreements / total_scorable if total_scorable > 0 else 1.0
        )

        # Check timeout
        elapsed = time.perf_counter() - start_time
        if elapsed > config.timeout_seconds:
            result.status = "timeout"
            result.error_summary = f"timeout_after_{elapsed:.1f}s"
        else:
            result.status = "completed"

    except Exception as e:
        result.status = "failed"
        result.error_summary = str(e)[:500]
        logger.warning("Shadow comparison failed: %s", e, exc_info=True)

    result.duration_ms = (time.perf_counter() - start_time) * 1000
    return result


# =============================================================================
# PERSISTENCE
# =============================================================================

async def store_shadow_run(
    store: "SignalStore",
    result: ShadowRunResult,
) -> int:
    """Persist a shadow run and its disagreements atomically.

    Uses BEGIN IMMEDIATE for all-or-nothing commit. On any insert failure,
    the entire run is rolled back (no partial shadow data).

    Also manages the run_history lifecycle:
        create_run → start_run → complete_run/fail_run

    Returns:
        The shadow_entity_runs.id of the inserted row.
    """
    from workflows.run_manager import (
        RunType, create_run, start_run, complete_run, fail_run,
    )

    now = datetime.now(timezone.utc).isoformat()

    # Step 1: run_history lifecycle
    run_record = await create_run(
        store,
        run_type=RunType.ENTITY_RESOLUTION.value,
        inputs_hash=result.inputs_hash,
    )
    result.run_id = run_record.id
    await start_run(store, run_record.id)

    # Step 2: Atomic insert of shadow_entity_runs + shadow_disagreements
    db = store._db
    shadow_run_id: int = -1

    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(
            """
            INSERT INTO shadow_entity_runs (
                run_id, status, total_signals, phase1a_groups, phase_g_groups,
                agreements, disagreements, agreement_rate,
                metrics_json, duration_ms, inputs_hash, config_json,
                error_summary, truncated, truncation_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_record.id,
                result.status,
                result.total_signals,
                result.phase1a_groups,
                result.phase_g_groups,
                result.agreements,
                result.disagreements_count,
                result.agreement_rate,
                result.metrics_json,
                result.duration_ms,
                result.inputs_hash,
                result.config_json,
                result.error_summary,
                1 if result.truncated else 0,
                result.truncation_reason,
                now,
            ),
        )
        shadow_run_id = cursor.lastrowid

        # Insert disagreements
        for d in result.disagreements:
            await db.execute(
                """
                INSERT INTO shadow_disagreements (
                    shadow_run_id, signal_id, canonical_key,
                    phase1a_company_id, phase_g_entity_id, phase_g_group_key,
                    disagreement_type, collector, confidence, confidence_band,
                    canonical_key_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shadow_run_id,
                    d.signal_id,
                    d.canonical_key,
                    d.phase1a_company_id,
                    d.phase_g_entity_id,
                    d.phase_g_group_key,
                    d.disagreement_type,
                    d.collector,
                    d.confidence,
                    d.confidence_band,
                    d.canonical_key_type,
                    now,
                ),
            )

        await db.execute("COMMIT")
    except Exception:
        try:
            await db.execute("ROLLBACK")
        except Exception:
            pass
        # Still complete the run_history lifecycle
        await fail_run(store, run_record.id, error_message="shadow_persistence_failed")
        raise

    # Step 3: Complete run_history
    if result.status in ("completed", "timeout"):
        await complete_run(
            store,
            run_record.id,
            result={
                "total_signals": result.total_signals,
                "agreements": result.agreements,
                "disagreements": result.disagreements_count,
                "agreement_rate": result.agreement_rate,
                "status": result.status,
            },
        )
    else:
        await fail_run(
            store,
            run_record.id,
            error_message=result.error_summary or "shadow_run_failed",
        )

    return shadow_run_id


async def store_skipped_shadow_run(
    store: "SignalStore",
    reason: str = "circuit_breaker_open",
) -> int:
    """Record a skipped shadow run (circuit breaker open).

    Makes skipped runs visible in dashboard and API.

    Returns:
        The shadow_entity_runs.id of the inserted row.
    """
    from workflows.run_manager import (
        RunType, create_run, start_run, complete_run,
    )

    now = datetime.now(timezone.utc).isoformat()

    run_record = await create_run(
        store,
        run_type=RunType.ENTITY_RESOLUTION.value,
    )
    await start_run(store, run_record.id)

    db = store._db
    cursor = await db.execute(
        """
        INSERT INTO shadow_entity_runs (
            run_id, status, error_summary, created_at
        ) VALUES (?, 'skipped', ?, ?)
        """,
        (run_record.id, reason, now),
    )
    await db.commit()
    shadow_run_id = cursor.lastrowid

    await complete_run(
        store,
        run_record.id,
        result={"status": "skipped", "reason": reason},
    )

    return shadow_run_id


async def update_shadow_run_metrics(
    store: "SignalStore",
    shadow_run_id: int,
    metrics_json: str,
) -> None:
    """Attach computed metrics to an existing shadow run.

    This is Txn 2 (idempotent) — failure doesn't affect the core run state.
    """
    db = store._db
    await db.execute(
        "UPDATE shadow_entity_runs SET metrics_json = ? WHERE id = ?",
        (metrics_json, shadow_run_id),
    )
    await db.commit()
