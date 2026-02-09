"""Anti-pattern proposal governance workflow.

Provides:
- propose_from_patterns: Convert detected FP patterns into proposals (status=proposed).
- list_proposals: Query proposals with optional status filter.
- review_proposal: Transition a proposal to approved/rejected/expired.
- expire_stale_proposals: Auto-expire proposals older than N days.

Uses anti_pattern_proposals table (v33 migration).

Phase 3 — case-law + exemplars.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EXPIRY_DAYS = int(os.environ.get("ANTI_PATTERN_EXPIRY_DAYS", "30"))


@dataclass
class Proposal:
    id: int
    pattern_type: str
    pattern_key: str
    description: str
    proposed_action: str
    evidence: str
    confidence: float
    status: str
    proposed_by: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[str]
    review_notes: Optional[str]
    created_at: str
    expires_at: Optional[str]


def _pattern_to_proposal_fields(pattern: Dict[str, Any]) -> Dict[str, Any]:
    """Map a detect_patterns output dict to proposal INSERT fields."""
    ptype = pattern.get("type", "unknown")

    # Build pattern_key from type-specific fields
    if ptype == "source_api_fp_rate":
        pkey = pattern.get("source_api", "unknown")
    elif ptype == "source_api_category_fp_rate":
        pkey = f"{pattern.get('source_api', '')}:{pattern.get('thesis_category', '')}"
    elif ptype == "duplicate_fp_description":
        pkey = pattern.get("normalized_description", "")[:100]
    elif ptype == "fp_temporal_hotspot":
        pkey = f"{pattern.get('source_api', '')}:hour_{pattern.get('hour_utc', '')}"
    elif ptype == "weak_canonical_keys_in_fp":
        pkey = "name_loc_overrepresented"
    else:
        pkey = json.dumps(pattern)[:100]

    description = pattern.get("recommendation", f"Detected {ptype} pattern")
    fp_rate = pattern.get("fp_rate", pattern.get("share", 0.0))

    proposed_action = json.dumps({
        "type": ptype,
        "recommendation": pattern.get("recommendation", ""),
    })

    evidence = json.dumps({k: v for k, v in pattern.items() if k != "recommendation"})

    return {
        "pattern_type": ptype,
        "pattern_key": pkey,
        "description": description,
        "proposed_action": proposed_action,
        "evidence": evidence,
        "confidence": min(fp_rate, 1.0),
    }


def propose_from_patterns(
    conn: sqlite3.Connection,
    patterns: List[Dict[str, Any]],
    proposed_by: str = "system",
) -> int:
    """Insert proposals from detected FP patterns.

    Skips patterns that already have an active proposal (same pattern_type + pattern_key).

    Returns count of new proposals created.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = (datetime.now(timezone.utc) + timedelta(days=EXPIRY_DAYS)).isoformat()
    created = 0

    for pattern in patterns:
        fields = _pattern_to_proposal_fields(pattern)
        try:
            conn.execute(
                """INSERT INTO anti_pattern_proposals
                (pattern_type, pattern_key, description, proposed_action,
                 evidence, confidence, status, proposed_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)""",
                (
                    fields["pattern_type"],
                    fields["pattern_key"],
                    fields["description"],
                    fields["proposed_action"],
                    fields["evidence"],
                    fields["confidence"],
                    proposed_by,
                    now_iso,
                    expires_iso,
                ),
            )
            created += 1
        except sqlite3.IntegrityError:
            # Active proposal already exists (unique partial index)
            logger.debug("Skipping duplicate proposal: %s/%s", fields["pattern_type"], fields["pattern_key"])
            continue

    conn.commit()
    return created


def list_proposals(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Proposal]:
    """List proposals with optional status filter."""
    if status:
        cursor = conn.execute(
            "SELECT * FROM anti_pattern_proposals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM anti_pattern_proposals ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    columns = [d[0] for d in cursor.description]
    return [Proposal(**dict(zip(columns, row))) for row in cursor.fetchall()]


def review_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
    action: str,
    reviewed_by: str = "human",
    review_notes: Optional[str] = None,
) -> bool:
    """Transition a proposal to approved/rejected/expired.

    Returns True if transition was applied, False if proposal not found or already decided.
    """
    if action not in ("approved", "rejected", "expired"):
        raise ValueError(f"Invalid action: {action}. Must be approved/rejected/expired.")

    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """UPDATE anti_pattern_proposals
        SET status = ?, reviewed_by = ?, reviewed_at = ?, review_notes = ?
        WHERE id = ? AND status = 'proposed'""",
        (action, reviewed_by, now_iso, review_notes, proposal_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def expire_stale_proposals(conn: sqlite3.Connection) -> int:
    """Auto-expire proposals past their expires_at date."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """UPDATE anti_pattern_proposals
        SET status = 'expired', reviewed_by = 'system', reviewed_at = ?
        WHERE status = 'proposed' AND expires_at IS NOT NULL AND expires_at < ?""",
        (now_iso, now_iso),
    )
    conn.commit()
    return cursor.rowcount
