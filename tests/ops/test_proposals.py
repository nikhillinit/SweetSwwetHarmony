"""Tests for ops/quality/proposals.py — Phase 3 Task 3.11."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ops.quality.proposals import (
    propose_from_patterns,
    list_proposals,
    review_proposal,
    expire_stale_proposals,
    _pattern_to_proposal_fields,
    Proposal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bootstrap_db() -> sqlite3.Connection:
    """Create in-memory DB with anti_pattern_proposals table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS anti_pattern_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            description TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            evidence TEXT NOT NULL,
            confidence REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK(status IN ('proposed', 'approved', 'rejected', 'expired', 'applied')),
            proposed_by TEXT NOT NULL DEFAULT 'system',
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_notes TEXT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            expires_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_one_active
            ON anti_pattern_proposals(pattern_type, pattern_key)
            WHERE status IN ('proposed', 'approved', 'applied');
        CREATE INDEX IF NOT EXISTS idx_proposals_status ON anti_pattern_proposals(status);
    """)
    return conn


SAMPLE_PATTERNS = [
    {
        "type": "source_api_fp_rate",
        "source_api": "github",
        "fp": 15,
        "tp": 3,
        "fp_rate": 0.833,
        "window_days": 30,
        "recommendation": "Investigate source parsing.",
    },
    {
        "type": "source_api_category_fp_rate",
        "source_api": "github",
        "thesis_category": "crypto",
        "fp": 10,
        "tp": 1,
        "fp_rate": 0.909,
        "window_days": 30,
        "recommendation": "Tighten category routing.",
    },
    {
        "type": "weak_canonical_keys_in_fp",
        "fp_count": 12,
        "fp_total": 20,
        "share": 0.60,
        "recommendation": "Strengthen canonical keys.",
    },
]


# ---------------------------------------------------------------------------
# Unit tests — field mapping
# ---------------------------------------------------------------------------

class TestPatternToProposalFields:
    def test_source_api_fp_rate(self):
        f = _pattern_to_proposal_fields(SAMPLE_PATTERNS[0])
        assert f["pattern_type"] == "source_api_fp_rate"
        assert f["pattern_key"] == "github"
        assert f["confidence"] == 0.833

    def test_source_category(self):
        f = _pattern_to_proposal_fields(SAMPLE_PATTERNS[1])
        assert f["pattern_type"] == "source_api_category_fp_rate"
        assert f["pattern_key"] == "github:crypto"

    def test_weak_keys(self):
        f = _pattern_to_proposal_fields(SAMPLE_PATTERNS[2])
        assert f["pattern_type"] == "weak_canonical_keys_in_fp"
        assert f["pattern_key"] == "name_loc_overrepresented"
        assert f["confidence"] == 0.60

    def test_evidence_is_json(self):
        f = _pattern_to_proposal_fields(SAMPLE_PATTERNS[0])
        parsed = json.loads(f["evidence"])
        assert parsed["fp"] == 15


# ---------------------------------------------------------------------------
# Integration tests — propose + list + review
# ---------------------------------------------------------------------------

class TestProposeFromPatterns:
    def test_creates_proposals(self):
        conn = _bootstrap_db()
        created = propose_from_patterns(conn, SAMPLE_PATTERNS)
        assert created == 3
        proposals = list_proposals(conn)
        assert len(proposals) == 3
        assert all(p.status == "proposed" for p in proposals)

    def test_skips_duplicates(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS)
        created = propose_from_patterns(conn, SAMPLE_PATTERNS)
        assert created == 0  # All skipped due to unique partial index

    def test_empty_patterns(self):
        conn = _bootstrap_db()
        created = propose_from_patterns(conn, [])
        assert created == 0


class TestListProposals:
    def test_filter_by_status(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS)
        review_proposal(conn, 1, "approved")
        proposed = list_proposals(conn, status="proposed")
        approved = list_proposals(conn, status="approved")
        assert len(proposed) == 2
        assert len(approved) == 1

    def test_limit(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS)
        proposals = list_proposals(conn, limit=1)
        assert len(proposals) == 1


class TestReviewProposal:
    def test_approve(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS[:1])
        ok = review_proposal(conn, 1, "approved", reviewed_by="operator", review_notes="Looks correct")
        assert ok is True
        proposals = list_proposals(conn)
        assert proposals[0].status == "approved"
        assert proposals[0].reviewed_by == "operator"
        assert proposals[0].review_notes == "Looks correct"

    def test_reject(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS[:1])
        ok = review_proposal(conn, 1, "rejected")
        assert ok is True
        proposals = list_proposals(conn)
        assert proposals[0].status == "rejected"

    def test_already_decided(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS[:1])
        review_proposal(conn, 1, "approved")
        ok = review_proposal(conn, 1, "rejected")
        assert ok is False  # Already decided

    def test_not_found(self):
        conn = _bootstrap_db()
        ok = review_proposal(conn, 999, "approved")
        assert ok is False

    def test_invalid_action(self):
        conn = _bootstrap_db()
        with pytest.raises(ValueError, match="Invalid action"):
            review_proposal(conn, 1, "invalid_action")

    def test_allows_new_proposal_after_rejection(self):
        conn = _bootstrap_db()
        propose_from_patterns(conn, SAMPLE_PATTERNS[:1])
        review_proposal(conn, 1, "rejected")
        # Now can propose again (rejected != active in partial index)
        created = propose_from_patterns(conn, SAMPLE_PATTERNS[:1])
        assert created == 1


class TestExpireStaleProposals:
    def test_expires_past_due(self):
        conn = _bootstrap_db()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        conn.execute(
            """INSERT INTO anti_pattern_proposals
            (pattern_type, pattern_key, description, proposed_action, evidence,
             confidence, status, proposed_by, expires_at)
            VALUES ('test', 'test_key', 'desc', '{}', '{}', 0.5, 'proposed', 'system', ?)""",
            (past,),
        )
        conn.commit()
        expired = expire_stale_proposals(conn)
        assert expired == 1
        proposals = list_proposals(conn)
        assert proposals[0].status == "expired"

    def test_does_not_expire_future(self):
        conn = _bootstrap_db()
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        conn.execute(
            """INSERT INTO anti_pattern_proposals
            (pattern_type, pattern_key, description, proposed_action, evidence,
             confidence, status, proposed_by, expires_at)
            VALUES ('test', 'test_key', 'desc', '{}', '{}', 0.5, 'proposed', 'system', ?)""",
            (future,),
        )
        conn.commit()
        expired = expire_stale_proposals(conn)
        assert expired == 0
