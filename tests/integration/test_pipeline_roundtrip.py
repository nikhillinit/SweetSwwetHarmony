"""Block 2.1: E2E Pipeline Roundtrip Tests.

Tests the full lifecycle: collect → store → dedupe → verify → push (mocked) → suppress.
Validates that the pipeline stages compose correctly end-to-end.
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore, SuppressionEntry
from verification.verification_gate_v2 import (
    Signal,
    VerificationGate,
    PushDecision,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(monkeypatch):
    """Fresh SignalStore with temp DB, env vars cleaned."""
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)
    monkeypatch.delenv("V2_ENABLEMENT", raising=False)
    monkeypatch.delenv("ML_ENABLEMENT", raising=False)
    monkeypatch.setenv("DELIVERY_MODE", "staging_only")
    monkeypatch.setenv("LLM_THESIS_MODE", "off")

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


def _make_signal(
    signal_id: str = "domain:acme.com",
    signal_type: str = "funding_event",
    source_api: str = "sec_edgar",
    confidence: float = 0.75,
    raw_data: dict = None,
) -> Signal:
    """Helper to build a Signal dataclass.

    Note: Signal.id is the identifier (often canonical_key in practice).
    """
    return Signal(
        id=signal_id,
        signal_type=signal_type,
        source_api=source_api,
        confidence=confidence,
        detected_at=datetime.now(timezone.utc),
        raw_data=raw_data or {"description": f"Test signal {signal_id}"},
    )


async def _add_suppression(store, canonical_key, notion_page_id):
    """Helper to add a suppression cache entry."""
    entry = SuppressionEntry(
        canonical_key=canonical_key,
        notion_page_id=notion_page_id,
        status="Source",
        company_name=f"Test ({canonical_key})",
    )
    await store.update_suppression_cache([entry])


class FakeCollector(BaseCollector):
    """Controllable collector for integration tests."""

    def __init__(self, signals=None, **kwargs):
        kwargs.setdefault("collector_name", "fake")
        super().__init__(**kwargs)
        self._fake_signals = signals or []

    async def _collect_signals(self):
        return list(self._fake_signals)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineRoundtrip:
    """E2E roundtrip tests exercising collect → store → verify lifecycle."""

    @pytest.mark.asyncio
    async def test_full_roundtrip_dryrun(self, store):
        """FakeCollector → store (dry-run) → verify: no crash, correct counts."""
        signals = [
            _make_signal("domain:startup-a.com", "funding_event", "sec_edgar", 0.8),
            _make_signal("domain:startup-b.com", "github_spike", "github", 0.6),
        ]
        collector = FakeCollector(signals=signals, store=store)
        result = await collector.run(dry_run=True)

        assert result.status == CollectorStatus.DRY_RUN
        assert result.signals_found == 2
        assert result.dry_run is True
        assert result.error_message is None

    @pytest.mark.asyncio
    async def test_dedup_filters_duplicate_canonical_keys(self, store):
        """Duplicate canonical keys should be suppressed on second collector run."""
        sig = _make_signal("domain:dupe-co.com", "funding_event", "sec_edgar")
        collector1 = FakeCollector(signals=[sig], store=store)
        r1 = await collector1.run(dry_run=False)
        assert r1.signals_new >= 1

        # Second run: same signal id/canonical key
        collector2 = FakeCollector(signals=[sig], store=store)
        r2 = await collector2.run(dry_run=False)
        assert r2.signals_suppressed >= 1
        assert r2.signals_new == 0

    @pytest.mark.asyncio
    async def test_suppression_cache_blocks_known_companies(self, store):
        """Signals with suppressed canonical keys should be blocked."""
        key = "domain:already-in-notion.com"
        await _add_suppression(store, key, "notion-page-abc")

        sig = _make_signal(key, "github_spike", "github", 0.7)
        collector = FakeCollector(signals=[sig], store=store)
        result = await collector.run(dry_run=False)

        assert result.signals_suppressed >= 1
        assert result.signals_new == 0

    @pytest.mark.asyncio
    async def test_multi_source_higher_confidence_than_single(self, store):
        """Multi-source signals produce higher confidence than single-source."""
        gate = VerificationGate()

        single = [_make_signal("s-1", "funding_event", "sec_edgar", 0.8)]
        multi = [
            _make_signal("m-1", "funding_event", "sec_edgar", 0.8),
            _make_signal("m-2", "hiring_signal", "job_postings", 0.85),
            _make_signal("m-3", "github_spike", "github", 0.7),
        ]

        r_single = gate.evaluate(single)
        r_multi = gate.evaluate(multi)

        # Multi-source should produce higher confidence due to convergence boost
        assert r_multi.confidence_score > r_single.confidence_score
        # Multi-source should be MULTI_SOURCE verification
        assert r_multi.verification_status == VerificationStatus.MULTI_SOURCE

    @pytest.mark.asyncio
    async def test_single_source_tracking(self, store):
        """Single source, medium confidence → NEEDS_REVIEW or HOLD."""
        signals = [
            _make_signal("sig-single", "github_spike", "github", 0.55),
        ]

        gate = VerificationGate()
        result = gate.evaluate(signals)

        # Single source with moderate confidence should route to review or hold
        assert result.decision in (PushDecision.NEEDS_REVIEW, PushDecision.HOLD)

    @pytest.mark.asyncio
    async def test_notion_push_decision_status_mapping(self, store):
        """Verify the PushDecision → Notion status mapping constants."""
        gate = VerificationGate()

        # Verify the gate's status mapping constants are correct
        assert gate.auto_push_status == "Source"
        assert gate.needs_review_status == "Tracking"

        # NEEDS_REVIEW with medium signals → "Tracking"
        med_signals = [
            _make_signal("m-1", "github_spike", "github", 0.5),
            _make_signal("m-2", "news_mention", "news_api", 0.45),
        ]
        r_med = gate.evaluate(med_signals)
        # With medium confidence, should suggest "Tracking" or "" (hold)
        assert r_med.suggested_status in ("Tracking", "")

    @pytest.mark.asyncio
    async def test_hard_kill_signal_rejected(self, store):
        """company_dissolved signal → REJECT decision."""
        signals = [
            _make_signal("dead-co-sig", "company_dissolved", "sec_edgar", 0.9),
        ]

        gate = VerificationGate()
        result = gate.evaluate(signals)

        assert result.decision == PushDecision.REJECT

    @pytest.mark.asyncio
    async def test_rerun_no_repush_suppression(self, store):
        """After suppression, re-running the collector with the same key is blocked."""
        key = "domain:one-time-push.com"

        # First run: save the signal
        sig = _make_signal(key, "funding_event", "sec_edgar", 0.8)
        collector1 = FakeCollector(signals=[sig], store=store)
        r1 = await collector1.run(dry_run=False)
        assert r1.signals_new >= 1

        # Simulate push to Notion (add suppression)
        await _add_suppression(store, key, "notion-page-xyz")

        # Second run: should be suppressed
        collector2 = FakeCollector(signals=[sig], store=store)
        r2 = await collector2.run(dry_run=False)
        assert r2.signals_suppressed >= 1
        assert r2.signals_new == 0


class TestRobustness:
    """Forensic Phase 2: Tests for suppression durability, dedup, gate safety."""

    @pytest.mark.asyncio
    async def test_suppression_persists_across_store_reopen(self, store, monkeypatch):
        """Suppression cache entries survive store close + reopen cycle."""
        key = "domain:durable-suppress.com"
        await _add_suppression(store, key, "notion-page-persist")

        # Get DB path before closing
        db_path = store.db_path

        # Close and reopen
        await store.close()
        store2 = SignalStore(db_path=db_path)
        await store2.initialize()

        try:
            sig = _make_signal(key, "funding_event", "sec_edgar", 0.8)
            collector = FakeCollector(signals=[sig], store=store2)
            result = await collector.run(dry_run=False)

            assert result.signals_suppressed >= 1
            assert result.signals_new == 0
        finally:
            await store2.close()

    @pytest.mark.asyncio
    async def test_two_collectors_same_domain_deduped(self, store):
        """Two collectors emitting the same canonical key: second run deduped."""
        key = "domain:shared-discovery.com"
        sig_a = _make_signal(key, "funding_event", "sec_edgar", 0.8)
        sig_b = _make_signal(key, "github_spike", "github", 0.6)

        collector1 = FakeCollector(signals=[sig_a], store=store,
                                   collector_name="collector_a")
        r1 = await collector1.run(dry_run=False)
        assert r1.signals_new >= 1

        collector2 = FakeCollector(signals=[sig_b], store=store,
                                   collector_name="collector_b")
        r2 = await collector2.run(dry_run=False)
        # Same canonical key → suppressed as duplicate
        assert r2.signals_suppressed >= 1

    @pytest.mark.asyncio
    async def test_dryrun_does_not_add_suppression(self, store):
        """Dry-run should not create suppression cache entries."""
        key = "domain:dryrun-test.com"
        sig = _make_signal(key, "funding_event", "sec_edgar", 0.8)

        collector = FakeCollector(signals=[sig], store=store)
        await collector.run(dry_run=True)

        # Now a real run should still succeed (not suppressed)
        collector2 = FakeCollector(signals=[sig], store=store)
        result = await collector2.run(dry_run=False)
        assert result.signals_new >= 1

    @pytest.mark.asyncio
    async def test_raw_data_json_fidelity_roundtrip(self, store):
        """Complex nested raw_data survives store → retrieve unchanged."""
        raw = {"nested": {"deep": [1, "two", 3.0]}, "unicode": "\u2603", "empty": {}}
        signal_id = await store.save_signal(
            signal_type="funding_event",
            source_api="sec_edgar",
            canonical_key="domain:json-fidelity.com",
            company_name="JSON Co",
            confidence=0.8,
            raw_data=raw,
        )
        assert signal_id is not None

        db = store._db
        cursor = await db.execute(
            "SELECT raw_data FROM signals WHERE id = ?", (signal_id,)
        )
        row = await cursor.fetchone()
        stored = json.loads(row[0])
        assert stored == raw

    @pytest.mark.asyncio
    async def test_suppression_update_idempotent(self, store):
        """Calling update_suppression_cache twice with same entries is idempotent."""
        key = "domain:idemp.com"
        entry = SuppressionEntry(
            canonical_key=key, notion_page_id="np-idemp",
            status="Source", company_name="Idemp Co",
        )
        await store.update_suppression_cache([entry])
        await store.update_suppression_cache([entry])

        # Should still be exactly one row
        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM suppression_cache WHERE canonical_key = ?", (key,)
        )
        count = (await cursor.fetchone())[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_gate_exception_does_not_lose_stored_signals(self, store):
        """If VerificationGate crashes, signals already stored remain intact."""
        key = "domain:gate-crash.com"
        sig = _make_signal(key, "funding_event", "sec_edgar", 0.8)

        # Store the signal first
        collector = FakeCollector(signals=[sig], store=store)
        result = await collector.run(dry_run=False)
        assert result.signals_new >= 1

        # Simulate a gate crash — signals should still be in DB
        gate = VerificationGate()
        try:
            gate.evaluate(None)  # Force a crash
        except (TypeError, AttributeError):
            pass  # Expected

        # Signal still in DB
        db = store._db
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signals WHERE canonical_key = ?", (key,)
        )
        count = (await cursor.fetchone())[0]
        assert count >= 1
