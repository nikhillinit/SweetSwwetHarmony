"""
Merge Suggestion Generator — Fuzzy matching + scoring for entity merge candidates.

Key components:
- compute_pair_key(): Deterministic hash of (min_id, max_id) pair
- MergeSuggestion: Data class for merge candidates
- store_merge_suggestion(): Upsert with lifecycle-safe conflict handling
- generate_merge_suggestions(): Fuzzy + Jaro-Winkler scoring pipeline
- compute_blast_radius(): Lazy per-detail-request impact assessment
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from storage.readonly_identity_store import ReadOnlyIdentityStore
    from intelligence.shadow_entity_evaluator import ShadowRunConfig

logger = logging.getLogger(__name__)

# Cool-down period for rejected suggestions
REJECTED_COOLDOWN_DAYS = 7
# Minimum score improvement to reopen a rejected suggestion
REJECTED_REOPEN_DELTA = 0.1
# Default scoring version
SCORING_VERSION = "1.0.0"


# =============================================================================
# PAIR KEY
# =============================================================================

def compute_pair_key(a_id: str, b_id: str) -> str:
    """Deterministic pair key: SHA256 hex of sorted (min_id + \\x1f + max_id).

    Order-independent: compute_pair_key("x","y") == compute_pair_key("y","x").
    Returns exactly 64 hex characters.
    """
    sorted_ids = (min(a_id, b_id), max(a_id, b_id))
    payload = sorted_ids[0] + "\x1f" + sorted_ids[1]
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class MergeSuggestion:
    """A merge suggestion between two entities."""

    id: Optional[int] = None
    shadow_run_id: Optional[int] = None
    pair_key: str = ""
    entity_a_company_id: str = ""
    entity_b_company_id: str = ""
    entity_a_canonical_key: str = ""
    entity_b_canonical_key: str = ""
    entity_a_company_name: Optional[str] = None
    entity_b_company_name: Optional[str] = None
    match_type: str = "fuzzy_name"
    similarity_score: float = 0.0
    scoring_version: str = SCORING_VERSION
    evidence_json: str = "{}"
    status: str = "pending"
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    blast_radius_json: Optional[str] = None
    created_at: str = ""


# =============================================================================
# STORE / RETRIEVE
# =============================================================================

async def store_merge_suggestion(
    store: "SignalStore",
    suggestion: MergeSuggestion,
) -> Optional[int]:
    """Upsert a merge suggestion with lifecycle-safe conflict handling.

    Upsert policy:
    - New pair → insert pending
    - Existing pending → update score/evidence if improved
    - Existing rejected → reopen ONLY if score delta >= 0.1 AND 7-day cooldown elapsed
    - Existing approved/superseded → skip

    Returns:
        The merge_suggestions.id if inserted/updated, None if skipped.
    """
    db = store._db
    now = datetime.now(timezone.utc).isoformat()

    # Check existing
    cursor = await db.execute(
        "SELECT id, status, similarity_score, reviewed_at FROM merge_suggestions WHERE pair_key = ?",
        (suggestion.pair_key,),
    )
    existing = await cursor.fetchone()

    if existing:
        existing_id, existing_status, existing_score, reviewed_at = existing

        if existing_status in ("approved", "superseded"):
            return None  # Skip

        if existing_status == "rejected":
            # Check cooldown (Python UTC computation, per convention)
            if reviewed_at:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=REJECTED_COOLDOWN_DAYS)).isoformat()
                if reviewed_at >= cutoff:
                    return None  # Still in cooldown

            # Check score improvement
            score_delta = suggestion.similarity_score - (existing_score or 0.0)
            if score_delta < REJECTED_REOPEN_DELTA:
                return None  # Not enough improvement

            # Reopen
            await db.execute(
                """
                UPDATE merge_suggestions
                SET status = 'pending', similarity_score = ?, evidence_json = ?,
                    scoring_version = ?, shadow_run_id = ?, reviewed_by = NULL,
                    reviewed_at = NULL, blast_radius_json = NULL
                WHERE id = ?
                """,
                (suggestion.similarity_score, suggestion.evidence_json,
                 suggestion.scoring_version, suggestion.shadow_run_id, existing_id),
            )
            await db.commit()
            return existing_id

        if existing_status == "pending":
            # Update if score improved
            if suggestion.similarity_score > (existing_score or 0.0):
                await db.execute(
                    """
                    UPDATE merge_suggestions
                    SET similarity_score = ?, evidence_json = ?,
                        scoring_version = ?, shadow_run_id = ?
                    WHERE id = ?
                    """,
                    (suggestion.similarity_score, suggestion.evidence_json,
                     suggestion.scoring_version, suggestion.shadow_run_id, existing_id),
                )
                await db.commit()
            return existing_id

    # New pair → insert
    cursor = await db.execute(
        """
        INSERT INTO merge_suggestions (
            shadow_run_id, pair_key, entity_a_company_id, entity_b_company_id,
            entity_a_canonical_key, entity_b_canonical_key,
            entity_a_company_name, entity_b_company_name,
            match_type, similarity_score, scoring_version, evidence_json,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            suggestion.shadow_run_id,
            suggestion.pair_key,
            suggestion.entity_a_company_id,
            suggestion.entity_b_company_id,
            suggestion.entity_a_canonical_key,
            suggestion.entity_b_canonical_key,
            suggestion.entity_a_company_name,
            suggestion.entity_b_company_name,
            suggestion.match_type,
            suggestion.similarity_score,
            suggestion.scoring_version,
            suggestion.evidence_json,
            now,
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def get_merge_suggestion(store: "SignalStore", suggestion_id: int) -> Optional[MergeSuggestion]:
    """Fetch a single merge suggestion by ID."""
    db = store._db
    cursor = await db.execute(
        """
        SELECT id, shadow_run_id, pair_key,
               entity_a_company_id, entity_b_company_id,
               entity_a_canonical_key, entity_b_canonical_key,
               entity_a_company_name, entity_b_company_name,
               match_type, similarity_score, scoring_version, evidence_json,
               status, reviewed_by, reviewed_at, blast_radius_json, created_at
        FROM merge_suggestions WHERE id = ?
        """,
        (suggestion_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return _row_to_suggestion(row)


async def list_merge_suggestions(
    store: "SignalStore",
    *,
    status: Optional[str] = None,
    cursor_created_at: Optional[str] = None,
    cursor_id: Optional[int] = None,
    limit: int = 50,
) -> List[MergeSuggestion]:
    """List merge suggestions with cursor pagination.

    Default limit=50, max=200. Sort: created_at DESC, id DESC.
    """
    limit = min(max(limit, 1), 200)
    conditions: List[str] = []
    params: List[Any] = []

    if status:
        conditions.append("status = ?")
        params.append(status)

    if cursor_created_at and cursor_id is not None:
        conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
        params.extend([cursor_created_at, cursor_created_at, cursor_id])

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    db = store._db
    cursor = await db.execute(
        f"""
        SELECT id, shadow_run_id, pair_key,
               entity_a_company_id, entity_b_company_id,
               entity_a_canonical_key, entity_b_canonical_key,
               entity_a_company_name, entity_b_company_name,
               match_type, similarity_score, scoring_version, evidence_json,
               status, reviewed_by, reviewed_at, blast_radius_json, created_at
        FROM merge_suggestions
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_suggestion(r) for r in rows]


def _row_to_suggestion(row) -> MergeSuggestion:
    return MergeSuggestion(
        id=row[0],
        shadow_run_id=row[1],
        pair_key=row[2],
        entity_a_company_id=row[3],
        entity_b_company_id=row[4],
        entity_a_canonical_key=row[5],
        entity_b_canonical_key=row[6],
        entity_a_company_name=row[7],
        entity_b_company_name=row[8],
        match_type=row[9],
        similarity_score=row[10],
        scoring_version=row[11],
        evidence_json=row[12],
        status=row[13],
        reviewed_by=row[14],
        reviewed_at=row[15],
        blast_radius_json=row[16],
        created_at=row[17],
    )


# =============================================================================
# SCORING
# =============================================================================

def _score_pair(
    name_a: str,
    name_b: str,
    shared_domain: bool,
) -> Tuple[float, str]:
    """Score a candidate merge pair.

    Formula: 0.4*token_sort_ratio + 0.3*jaro_winkler + 0.3*(1.0 if shared_domain else 0.0)

    Returns:
        (score, match_type)
    """
    try:
        from rapidfuzz import fuzz as rf_fuzz
        from rapidfuzz.distance import JaroWinkler

        token_sort = rf_fuzz.token_sort_ratio(name_a, name_b) / 100.0
        jaro = JaroWinkler.similarity(name_a, name_b)
    except ImportError:
        # Fallback: simple equality check
        token_sort = 1.0 if name_a.lower() == name_b.lower() else 0.0
        jaro = token_sort

    domain_score = 1.0 if shared_domain else 0.0
    score = 0.4 * token_sort + 0.3 * jaro + 0.3 * domain_score

    if shared_domain:
        match_type = "shared_domain"
    else:
        match_type = "fuzzy_name"

    return round(score, 4), match_type


# =============================================================================
# GENERATION
# =============================================================================

async def generate_merge_suggestions(
    store: "SignalStore",
    ro_identity_store: "ReadOnlyIdentityStore",
    *,
    shadow_run_id: Optional[int] = None,
    config: Optional["ShadowRunConfig"] = None,
) -> int:
    """Generate merge suggestions from blocking token candidates.

    Fetches company_ids sharing blocking tokens, scores pairs using
    fuzzy + Jaro-Winkler, and persists suggestions above threshold.

    Returns:
        Number of suggestions created/updated.
    """
    from intelligence.shadow_entity_evaluator import ShadowRunConfig

    config = config or ShadowRunConfig()
    db = store._db
    count = 0

    # Get distinct company_ids with their canonical_keys and names
    cursor = await db.execute(
        """
        SELECT DISTINCT company_id, canonical_key, company_name
        FROM signals
        WHERE company_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 2000
        """,
    )
    rows = await cursor.fetchall()

    if not rows:
        return 0

    # Build lookup maps
    company_info: Dict[str, Dict[str, str]] = {}
    for company_id, ckey, cname in rows:
        if company_id not in company_info:
            company_info[company_id] = {
                "canonical_key": ckey,
                "company_name": cname or "",
            }

    # Get blocking candidates
    # Extract domain tokens from canonical keys
    tokens_to_check: List[Tuple[str, str]] = []
    for cid, info in company_info.items():
        ckey = info["canonical_key"]
        if ":" in ckey:
            prefix, value = ckey.split(":", 1)
            # Use first 3 chars of value as blocking token
            if len(value) >= 3:
                tokens_to_check.append((f"tok:first:{value[:3].lower()}", "first"))

    # Deduplicate tokens
    unique_tokens = list(set(tokens_to_check))[:500]

    if not unique_tokens:
        return 0

    candidates = await ro_identity_store.lookup_blocking_candidates(unique_tokens, limit=200)

    # Score pairs from candidates
    scored_pairs: List[MergeSuggestion] = []
    seen_pairs: set = set()

    for token_key, candidate_list in candidates.items():
        # Compare all pairs within each candidate group
        for i, c1 in enumerate(candidate_list):
            for c2 in candidate_list[i + 1:]:
                if c1.entity_id == c2.entity_id:
                    continue

                pair_key = compute_pair_key(c1.entity_id, c2.entity_id)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # Look up company info
                info_a = company_info.get(c1.entity_id, {})
                info_b = company_info.get(c2.entity_id, {})
                name_a = info_a.get("company_name", "")
                name_b = info_b.get("company_name", "")

                if not name_a or not name_b:
                    continue

                # Check shared domain
                ckey_a = info_a.get("canonical_key", "")
                ckey_b = info_b.get("canonical_key", "")
                shared_domain = (
                    ckey_a.startswith("domain:")
                    and ckey_b.startswith("domain:")
                    and ckey_a == ckey_b
                )

                score, match_type = _score_pair(name_a, name_b, shared_domain)

                if score < config.min_similarity_threshold:
                    continue

                scored_pairs.append(MergeSuggestion(
                    shadow_run_id=shadow_run_id,
                    pair_key=pair_key,
                    entity_a_company_id=c1.entity_id,
                    entity_b_company_id=c2.entity_id,
                    entity_a_canonical_key=ckey_a,
                    entity_b_canonical_key=ckey_b,
                    entity_a_company_name=name_a,
                    entity_b_company_name=name_b,
                    match_type=match_type,
                    similarity_score=score,
                    scoring_version=SCORING_VERSION,
                    evidence_json=json.dumps({
                        "blocking_token": token_key[0] if isinstance(token_key, tuple) else str(token_key),
                        "name_a": name_a,
                        "name_b": name_b,
                        "score_components": {
                            "shared_domain": shared_domain,
                        },
                    }),
                ))

    # Sort by score descending, cap at max_suggestions_per_run
    scored_pairs.sort(key=lambda s: s.similarity_score, reverse=True)
    scored_pairs = scored_pairs[: config.max_suggestions_per_run]

    # Persist
    for suggestion in scored_pairs:
        result_id = await store_merge_suggestion(store, suggestion)
        if result_id is not None:
            count += 1

    logger.info("Generated %d merge suggestions (shadow_run_id=%s)", count, shadow_run_id)
    return count


# =============================================================================
# BLAST RADIUS
# =============================================================================

async def compute_blast_radius(
    store: "SignalStore",
    a_id: str,
    b_id: str,
    timeout_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Compute merge blast radius for two entity company_ids.

    Lazy compute — called on detail endpoint, NOT at generation time.
    Hard cap: abort if total > 10000 (return {capped: true}).
    Query timeout: return {timeout: true} if exceeded.

    Returns:
        Dict with signals_a, signals_b, reviews_a, reviews_b,
        files_a, files_b, total_affected.
    """
    db = store._db
    start = time.perf_counter()

    try:
        # Count signals per entity
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = ?", (a_id,)
        )
        signals_a = (await cursor.fetchone())[0]

        if time.perf_counter() - start > timeout_seconds:
            return {"timeout": True}

        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE company_id = ?", (b_id,)
        )
        signals_b = (await cursor.fetchone())[0]

        if time.perf_counter() - start > timeout_seconds:
            return {"timeout": True}

        # Count review_items per entity
        reviews_a = 0
        reviews_b = 0
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM review_items WHERE company_id = ?", (a_id,)
            )
            reviews_a = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM review_items WHERE company_id = ?", (b_id,)
            )
            reviews_b = (await cursor.fetchone())[0]
        except Exception:
            pass  # Table may not exist in test environments

        if time.perf_counter() - start > timeout_seconds:
            return {"timeout": True}

        # Count company_files per entity
        files_a = 0
        files_b = 0
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM company_files WHERE company_id = ?", (a_id,)
            )
            files_a = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM company_files WHERE company_id = ?", (b_id,)
            )
            files_b = (await cursor.fetchone())[0]
        except Exception:
            pass  # Table may not exist in test environments

        total = signals_a + signals_b + reviews_a + reviews_b + files_a + files_b

        if total > 10000:
            return {"capped": True, "total_affected": total}

        return {
            "signals_a": signals_a,
            "signals_b": signals_b,
            "reviews_a": reviews_a,
            "reviews_b": reviews_b,
            "files_a": files_a,
            "files_b": files_b,
            "total_affected": total,
        }

    except Exception as e:
        if time.perf_counter() - start > timeout_seconds:
            return {"timeout": True}
        logger.warning("Blast radius computation failed: %s", e)
        return {"error": str(e)}
