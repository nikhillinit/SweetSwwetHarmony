"""Tests for v51 confidence_ledger migration and SignalStore CRUD.

Covers:
- Migration DDL (table creation, constraints, upgrade from v50)
- save_confidence_ledger / get_confidence_ledger round-trips
- Breakdown kind detection (normal, hard_kill, empty_signals)
- Score semantics (gate_score vs reported_score)
- Dry-run filtering, timestamp format, identifier validation
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

from storage.signal_store import SignalStore
from storage.migrations.v51_confidence_ledger import V51_CONFIDENCE_LEDGER_DDL


# ---------------------------------------------------------------------------
# Lightweight stubs for VerificationResult (avoid importing the full gate)
# ---------------------------------------------------------------------------

class _Decision(str, Enum):
    AUTO_PUSH = "auto_push"
    NEEDS_REVIEW = "needs_review"
    HOLD = "hold"
    REJECT = "reject"


class _VStatus(str, Enum):
    UNVERIFIED = "unverified"
    SINGLE_SOURCE = "single_source"
    MULTI_SOURCE = "multi_source"
    CONFLICTING = "conflicting"
    FAILED = "failed"


@dataclass
class _VResult:
    decision: _Decision
    verification_status: _VStatus
    confidence_score: float
    confidence_breakdown: Dict[str, Any]
    reason: str
    suggested_status: str = ""
    signals_used: list = None
    sources_checked: list = None
    verification_details: list = None

    def __post_init__(self):
        if self.signals_used is None:
            self.signals_used = []
        if self.sources_checked is None:
            self.sources_checked = []
        if self.verification_details is None:
            self.verification_details = []


def _normal_breakdown(**overrides) -> Dict[str, Any]:
    """Build a realistic normal-path ConfidenceBreakdown.to_dict() output."""
    bd = {
        "overall": 0.782,
        "base_score": 0.350,
        "multi_source_boost": 1.15,
        "convergence_boost": 1.0,
        "founder_score": 0.6,
        "founder_boost": 0.08,
        "velocity_boost": 0.05,
        "momentum_score": 0.3,
        "enrichment_boost": 0.03,
        "community_sentiment_boost": 0.016,
        "raw_overall": 0.579,
        "score_recalibration_factor": 1.35,
        "policy_version": "v2.1",
        "signals_contributing": 3,
        "sources_checked": 2,
        "sources": ["sec_edgar", "job_postings"],
        "signal_details": [
            {"type": "incorporation", "contribution": 0.22, "source": "sec_edgar"},
        ],
        "calculation_method": "glass_ai_v2",
        "calculated_at": "2026-03-14T00:00:00+00:00",
    }
    bd.update(overrides)
    return bd


def _normal_result(**overrides) -> _VResult:
    """Build a normal-path VerificationResult."""
    defaults = dict(
        decision=_Decision.AUTO_PUSH,
        verification_status=_VStatus.MULTI_SOURCE,
        confidence_score=0.782,
        confidence_breakdown=_normal_breakdown(),
        reason="High confidence (0.78) with 2 sources",
        verification_details=[
            {"signal": 1, "type": "incorporation", "source": "sec_edgar"}
        ],
    )
    defaults.update(overrides)
    return _VResult(**defaults)


_ROUTING_CFG = {
    "high_threshold": 0.7,
    "medium_threshold": 0.4,
    "score_scale": 1.35,
    "strict_mode": False,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# ===========================================================================
# A. Migration tests (sync sqlite3)
# ===========================================================================

class TestMigration:
    def test_v51_creates_confidence_ledger_table(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(V51_CONFIDENCE_LEDGER_DDL)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "confidence_ledger" in tables

        cols = {r[1] for r in conn.execute("PRAGMA table_info(confidence_ledger)").fetchall()}
        for expected in (
            "id", "execution_id", "canonical_key", "company_id",
            "evaluation_origin", "is_dry_run", "breakdown_kind",
            "gate_score", "reported_score", "base_score",
            "multi_source_boost", "convergence_boost",
            "founder_boost", "velocity_boost", "enrichment_boost",
            "community_sentiment_boost", "recalibration_factor",
            "policy_version", "breakdown_schema_version",
            "signals_contributing", "sources_checked",
            "decision", "verification_status", "reason",
            "breakdown_json", "details_json", "signal_ids_json",
            "routing_config_json",
            "evaluated_at", "created_at",
        ):
            assert expected in cols, f"Missing column: {expected}"
        conn.close()

    def test_v51_upgrade_from_v50(self):
        """Applying v51 to a DB that already has tables should not lose data."""
        conn = sqlite3.connect(":memory:")
        # Simulate pre-existing table
        conn.execute("CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO signals (id) VALUES (1)")
        # Apply v51
        conn.executescript(V51_CONFIDENCE_LEDGER_DDL)
        # Pre-existing data intact
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
        # New table exists
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='confidence_ledger'"
        ).fetchone()[0] == 1
        conn.close()

    @pytest.mark.parametrize("case,values,should_reject", [
        # Invalid JSON in breakdown_json
        ("bad_breakdown_json", {"breakdown_json": "not-json"}, True),
        # Wrong JSON type: details_json must be array
        ("details_json_object", {"details_json": '{"a":1}'}, True),
        # Wrong JSON type: signal_ids_json must be array
        ("signal_ids_json_string", {"signal_ids_json": '"hello"'}, True),
        # Wrong JSON type: breakdown_json must be object
        ("breakdown_json_array", {"breakdown_json": "[1,2]"}, True),
        # Invalid decision
        ("bad_decision", {"decision": "maybe"}, True),
        # Invalid evaluation_origin
        ("bad_origin", {"evaluation_origin": "manual"}, True),
        # Invalid breakdown_kind
        ("bad_kind", {"breakdown_kind": "unknown"}, True),
        # Invalid is_dry_run
        ("bad_dry_run", {"is_dry_run": 2}, True),
        # gate_score out of range
        ("gate_score_high", {"gate_score": 1.5}, True),
        # reported_score out of range
        ("reported_score_negative", {"reported_score": -0.1}, True),
        # Negative signals_contributing
        ("negative_signals", {"signals_contributing": -1}, True),
        # Pipeline row with NULL routing_config_json
        ("pipeline_no_config", {"routing_config_json": None, "evaluation_origin": "pipeline"}, True),
        # Valid row
        ("valid_row", {}, False),
    ])
    def test_v51_check_constraints(self, case, values, should_reject):
        conn = sqlite3.connect(":memory:")
        conn.executescript(V51_CONFIDENCE_LEDGER_DDL)

        defaults = {
            "execution_id": "run1",
            "canonical_key": "domain:test.com",
            "company_id": None,
            "evaluation_origin": "pipeline",
            "is_dry_run": 0,
            "breakdown_kind": "normal",
            "gate_score": 0.5,
            "reported_score": 0.5,
            "base_score": 0.3,
            "multi_source_boost": 1.0,
            "convergence_boost": 1.0,
            "founder_boost": 0.0,
            "velocity_boost": 0.0,
            "enrichment_boost": 0.0,
            "community_sentiment_boost": 0.0,
            "recalibration_factor": 1.0,
            "policy_version": "v2.1",
            "breakdown_schema_version": "1.0",
            "signals_contributing": 2,
            "sources_checked": 1,
            "decision": "auto_push",
            "verification_status": "multi_source",
            "reason": "test",
            "breakdown_json": '{"overall": 0.5}',
            "details_json": "[]",
            "signal_ids_json": "[1,2]",
            "routing_config_json": '{"high_threshold": 0.7}',
            "evaluated_at": "2026-03-14T00:00:00+00:00",
            "created_at": "2026-03-14T00:00:00+00:00",
        }
        defaults.update(values)

        cols = ", ".join(defaults.keys())
        placeholders = ", ".join(["?"] * len(defaults))
        sql = f"INSERT INTO confidence_ledger ({cols}) VALUES ({placeholders})"

        if should_reject:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, tuple(defaults.values()))
        else:
            conn.execute(sql, tuple(defaults.values()))
            assert conn.execute("SELECT COUNT(*) FROM confidence_ledger").fetchone()[0] == 1

        conn.close()


# ===========================================================================
# B. SignalStore CRUD + Invariants (async)
# ===========================================================================

class TestSaveAndRetrieve:

    @pytest.mark.asyncio
    async def test_save_returns_positive_id(self, store):
        row_id = await store.save_confidence_ledger(
            canonical_key="domain:acme.ai",
            verification_result=_normal_result(),
            signal_ids=[1, 2, 3],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        assert isinstance(row_id, int)
        assert row_id > 0

    @pytest.mark.asyncio
    async def test_get_empty_returns_empty_list(self, store):
        rows = await store.get_confidence_ledger(canonical_key="domain:nothing.com")
        assert rows == []

    @pytest.mark.asyncio
    async def test_save_and_retrieve_roundtrip(self, store):
        vr = _normal_result()
        row_id = await store.save_confidence_ledger(
            canonical_key="domain:acme.ai",
            verification_result=vr,
            signal_ids=[10, 20],
            policy_version="v2.1",
            execution_id="exec-001",
            company_id="cid-abc",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:acme.ai")
        assert len(rows) == 1
        r = rows[0]
        assert r["id"] == row_id
        assert r["canonical_key"] == "domain:acme.ai"
        assert r["company_id"] == "cid-abc"
        assert r["execution_id"] == "exec-001"
        assert r["evaluation_origin"] == "pipeline"
        assert r["is_dry_run"] == 0
        assert r["breakdown_kind"] == "normal"
        assert pytest.approx(r["gate_score"], abs=1e-3) == 0.782
        assert pytest.approx(r["reported_score"], abs=1e-3) == 0.782
        assert pytest.approx(r["base_score"], abs=1e-3) == 0.350
        assert r["decision"] == "auto_push"
        assert r["verification_status"] == "multi_source"
        assert r["reason"] == "High confidence (0.78) with 2 sources"
        assert r["policy_version"] == "v2.1"
        # JSON columns returned as raw strings
        assert isinstance(r["breakdown_json"], str)
        assert isinstance(r["details_json"], str)
        assert isinstance(r["signal_ids_json"], str)
        assert isinstance(r["routing_config_json"], str)
        assert json.loads(r["signal_ids_json"]) == [10, 20]

    @pytest.mark.asyncio
    async def test_history_ordering(self, store):
        """3 entries for same key, verify DESC order with id tiebreaker."""
        for i in range(3):
            await store.save_confidence_ledger(
                canonical_key="domain:acme.ai",
                verification_result=_normal_result(
                    confidence_score=0.5 + i * 0.1,
                    confidence_breakdown=_normal_breakdown(overall=0.5 + i * 0.1),
                ),
                signal_ids=[i],
                policy_version="v2.1",
                routing_config=_ROUTING_CFG,
            )
        rows = await store.get_confidence_ledger(canonical_key="domain:acme.ai", limit=10)
        assert len(rows) == 3
        # Most recent (highest id) should be first
        assert rows[0]["id"] > rows[1]["id"] > rows[2]["id"]

    @pytest.mark.asyncio
    async def test_multiple_keys_isolated(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:a.com",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        await store.save_confidence_ledger(
            canonical_key="domain:b.com",
            verification_result=_normal_result(),
            signal_ids=[2],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        a_rows = await store.get_confidence_ledger(canonical_key="domain:a.com")
        b_rows = await store.get_confidence_ledger(canonical_key="domain:b.com")
        assert len(a_rows) == 1
        assert len(b_rows) == 1

    @pytest.mark.asyncio
    async def test_gate_score_vs_reported_score(self, store):
        """Verify both scores stored correctly when LLM adjustment differs."""
        vr = _normal_result(
            confidence_score=0.782,
            confidence_breakdown=_normal_breakdown(overall=0.650),
        )
        await store.save_confidence_ledger(
            canonical_key="domain:llm.ai",
            verification_result=vr,
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:llm.ai")
        assert pytest.approx(rows[0]["gate_score"], abs=1e-3) == 0.650
        assert pytest.approx(rows[0]["reported_score"], abs=1e-3) == 0.782

    @pytest.mark.asyncio
    async def test_query_by_company_id(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:x.com",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            company_id="company-123",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(company_id="company-123")
        assert len(rows) == 1
        assert rows[0]["company_id"] == "company-123"

    @pytest.mark.asyncio
    async def test_denormalized_columns_roundtrip_invariant(self, store):
        """Denormalized columns must match breakdown_json values."""
        bd = _normal_breakdown()
        await store.save_confidence_ledger(
            canonical_key="domain:inv.ai",
            verification_result=_normal_result(confidence_breakdown=bd),
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:inv.ai")
        r = rows[0]
        parsed = json.loads(r["breakdown_json"])
        assert pytest.approx(r["gate_score"], abs=1e-3) == parsed["overall"]
        assert pytest.approx(r["base_score"], abs=1e-3) == parsed["base_score"]
        assert pytest.approx(r["multi_source_boost"], abs=1e-2) == parsed["multi_source_boost"]
        assert pytest.approx(r["convergence_boost"], abs=1e-2) == parsed["convergence_boost"]
        assert pytest.approx(r["founder_boost"], abs=1e-3) == parsed["founder_boost"]
        assert pytest.approx(r["velocity_boost"], abs=1e-3) == parsed["velocity_boost"]
        assert pytest.approx(r["enrichment_boost"], abs=1e-3) == parsed["enrichment_boost"]
        assert pytest.approx(r["community_sentiment_boost"], abs=1e-3) == parsed["community_sentiment_boost"]
        assert pytest.approx(r["recalibration_factor"], abs=1e-3) == parsed["score_recalibration_factor"]

    @pytest.mark.asyncio
    async def test_dry_run_excluded_by_default(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:dry.ai",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            is_dry_run=True,
            routing_config=_ROUTING_CFG,
        )
        # Default: excluded
        rows = await store.get_confidence_ledger(canonical_key="domain:dry.ai")
        assert len(rows) == 0
        # With flag: included
        rows = await store.get_confidence_ledger(
            canonical_key="domain:dry.ai", include_dry_runs=True
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_timestamps_stored_utc(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:ts.ai",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:ts.ai")
        r = rows[0]
        assert r["evaluated_at"].endswith("+00:00")
        assert r["created_at"].endswith("+00:00")
        assert r["evaluated_at"] == r["created_at"]

    @pytest.mark.asyncio
    async def test_reason_with_quotes_and_unicode(self, store):
        reason = 'Hard kill: "dissolved" \u2014 \u4f1a\u793e\u89e3\u6563'
        vr = _normal_result(reason=reason)
        await store.save_confidence_ledger(
            canonical_key="domain:uni.co",
            verification_result=vr,
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:uni.co")
        assert rows[0]["reason"] == reason

    @pytest.mark.asyncio
    async def test_get_both_identifiers_raises(self, store):
        with pytest.raises(ValueError, match="exactly one"):
            await store.get_confidence_ledger(
                canonical_key="domain:x.com", company_id="c-1"
            )

    @pytest.mark.asyncio
    async def test_get_neither_identifier_raises(self, store):
        with pytest.raises(ValueError, match="exactly one"):
            await store.get_confidence_ledger()

    @pytest.mark.asyncio
    async def test_save_hard_kill_breakdown(self, store):
        vr = _VResult(
            decision=_Decision.REJECT,
            verification_status=_VStatus.UNVERIFIED,
            confidence_score=0.0,
            confidence_breakdown={"hard_kill": True, "kill_signal": "company_dissolved"},
            reason="Hard kill signal: company_dissolved",
            verification_details=[{"signal": 1, "type": "company_dissolved", "effect": "hard_kill"}],
        )
        row_id = await store.save_confidence_ledger(
            canonical_key="domain:dead.co",
            verification_result=vr,
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:dead.co")
        r = rows[0]
        assert r["breakdown_kind"] == "hard_kill"
        assert pytest.approx(r["gate_score"]) == 0.0
        assert r["decision"] == "reject"
        parsed = json.loads(r["breakdown_json"])
        assert parsed["hard_kill"] is True
        assert parsed["kill_signal"] == "company_dissolved"

    @pytest.mark.asyncio
    async def test_save_empty_signals_breakdown(self, store):
        vr = _VResult(
            decision=_Decision.REJECT,
            verification_status=_VStatus.UNVERIFIED,
            confidence_score=0.0,
            confidence_breakdown={},
            reason="No signals provided",
        )
        row_id = await store.save_confidence_ledger(
            canonical_key="domain:empty.co",
            verification_result=vr,
            signal_ids=[],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:empty.co")
        r = rows[0]
        assert r["breakdown_kind"] == "empty_signals"
        assert pytest.approx(r["gate_score"]) == 0.0
        assert pytest.approx(r["base_score"]) == 0.0
        assert r["signals_contributing"] == 0

    @pytest.mark.asyncio
    async def test_routing_config_stored_separately(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:cfg.ai",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:cfg.ai")
        r = rows[0]
        rc = json.loads(r["routing_config_json"])
        assert rc["high_threshold"] == 0.7
        assert rc["strict_mode"] is False
        # breakdown_json should NOT contain thresholds
        bd = json.loads(r["breakdown_json"])
        assert "_thresholds" not in bd

    @pytest.mark.asyncio
    async def test_save_zero_overall_not_special(self, store):
        """Normal-path with overall=0.0 should NOT be treated as special."""
        bd = _normal_breakdown(overall=0.0, base_score=0.05)
        vr = _normal_result(
            confidence_score=0.0,
            confidence_breakdown=bd,
        )
        await store.save_confidence_ledger(
            canonical_key="domain:zero.ai",
            verification_result=vr,
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:zero.ai")
        r = rows[0]
        assert r["breakdown_kind"] == "normal"
        assert pytest.approx(r["base_score"], abs=1e-3) == 0.05

    @pytest.mark.asyncio
    async def test_execution_id_stored(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:exec.ai",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            execution_id="abc123",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:exec.ai")
        assert rows[0]["execution_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_breakdown_kind_normal(self, store):
        await store.save_confidence_ledger(
            canonical_key="domain:norm.ai",
            verification_result=_normal_result(),
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:norm.ai")
        assert rows[0]["breakdown_kind"] == "normal"

    @pytest.mark.asyncio
    async def test_routing_config_required_for_pipeline(self, store):
        with pytest.raises(ValueError, match="routing_config"):
            await store.save_confidence_ledger(
                canonical_key="domain:no-cfg.ai",
                verification_result=_normal_result(),
                signal_ids=[1],
                policy_version="v2.1",
                evaluation_origin="pipeline",
                routing_config=None,
            )

    @pytest.mark.asyncio
    async def test_policy_version_mismatch_warning(self, store, caplog):
        bd = _normal_breakdown(policy_version="v2.0")
        vr = _normal_result(confidence_breakdown=bd)
        await store.save_confidence_ledger(
            canonical_key="domain:mismatch.ai",
            verification_result=vr,
            signal_ids=[1],
            policy_version="v2.1",
            routing_config=_ROUTING_CFG,
        )
        rows = await store.get_confidence_ledger(canonical_key="domain:mismatch.ai")
        # Gate version is authoritative
        assert rows[0]["policy_version"] == "v2.1"
        assert "policy_version_mismatch" in caplog.text
