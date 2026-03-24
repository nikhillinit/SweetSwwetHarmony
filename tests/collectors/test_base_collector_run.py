"""
Tests for BaseCollector local gaps: fallback, same-run dedup,
per-signal save failure, and evidence_key pass-through.

Uses MagicMock(spec=SignalStore) for store-shaped mocks.
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from collectors.base import BaseCollector
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal


class StubCollector(BaseCollector):
    """Concrete subclass that returns pre-set signals."""

    def __init__(self, signals, **kwargs):
        super().__init__(**kwargs)
        self._canned = signals

    async def _collect_signals(self):
        return self._canned


def _make_signal(
    id_="sig1",
    signal_type="funding",
    source_api="sec_edgar",
    confidence=0.7,
    raw_data=None,
    source_url=None,
    detected_at=None,
):
    return Signal(
        id=id_,
        signal_type=signal_type,
        source_api=source_api,
        confidence=confidence,
        raw_data=raw_data or {},
        source_url=source_url,
        detected_at=detected_at or datetime(2026, 3, 19, tzinfo=timezone.utc),
    )


class TestCanonicalKeyFallback:
    """When _extract_canonical_key returns empty/None, fallback to signal.id."""

    @pytest.mark.asyncio
    async def test_fallback_to_signal_id_when_extraction_returns_empty(self):
        store = create_autospec(SignalStore, instance=True)
        store.is_duplicate = AsyncMock(return_value=False)
        store.check_suppression = AsyncMock(return_value=None)
        store.save_signal = AsyncMock(return_value=42)

        sig = _make_signal(id_="fallback-id-123", raw_data={})
        collector = StubCollector(signals=[sig], store=store, collector_name="test")

        # Monkeypatch _extract_canonical_key to return empty string
        collector._extract_canonical_key = MagicMock(return_value="")

        result = await collector.run(dry_run=False)

        # save_signal should use signal.id as canonical_key
        store.save_signal.assert_awaited_once()
        call_kwargs = store.save_signal.call_args.kwargs
        assert call_kwargs["canonical_key"] == "fallback-id-123"
        assert result.signals_new == 1


class TestSameRunDedup:
    """Identical canonical_key + signal_type + source_api within one run is suppressed."""

    @pytest.mark.asyncio
    async def test_second_signal_with_same_identity_suppressed(self):
        store = create_autospec(SignalStore, instance=True)
        store.is_duplicate = AsyncMock(return_value=False)
        store.check_suppression = AsyncMock(return_value=None)
        store.save_signal = AsyncMock(return_value=1)

        # Two signals: same canonical_key + signal_type + source_api, different detected_at, no source_url
        sig1 = _make_signal(
            id_="s1",
            signal_type="funding",
            source_api="sec_edgar",
            raw_data={"canonical_key": "domain:acme.com"},
            detected_at=datetime(2026, 3, 18, tzinfo=timezone.utc),
        )
        sig2 = _make_signal(
            id_="s2",
            signal_type="funding",
            source_api="sec_edgar",
            raw_data={"canonical_key": "domain:acme.com"},
            detected_at=datetime(2026, 3, 19, tzinfo=timezone.utc),
        )

        collector = StubCollector(signals=[sig1, sig2], store=store, collector_name="test")
        result = await collector.run(dry_run=False)

        # First saved, second suppressed via _processed_identities
        assert result.signals_new == 1
        assert result.signals_suppressed == 1
        assert store.save_signal.await_count == 1


class TestPerSignalSaveFailure:
    """Per-signal save_signal failure does not abort the batch."""

    @pytest.mark.asyncio
    async def test_partial_success_on_save_error(self):
        store = create_autospec(SignalStore, instance=True)
        store.is_duplicate = AsyncMock(return_value=False)
        store.check_suppression = AsyncMock(return_value=None)
        # First signal fails, second succeeds
        store.save_signal = AsyncMock(side_effect=[RuntimeError("db locked"), 2])

        sig1 = _make_signal(
            id_="err",
            raw_data={"canonical_key": "domain:fail.com"},
        )
        sig2 = _make_signal(
            id_="ok",
            raw_data={"canonical_key": "domain:ok.com"},
        )

        collector = StubCollector(signals=[sig1, sig2], store=store, collector_name="test")
        result = await collector.run(dry_run=False)

        assert result.status == CollectorStatus.PARTIAL_SUCCESS
        assert result.signals_new == 1
        assert result.error_message is not None
        assert "db locked" in result.error_message


class TestEvidenceKeyPassThrough:
    """evidence_key is passed through on both duplicate-check and save paths."""

    @pytest.mark.asyncio
    async def test_evidence_key_passed_when_source_url_truthy(self):
        store = create_autospec(SignalStore, instance=True)
        store.is_duplicate = AsyncMock(return_value=False)
        store.check_suppression = AsyncMock(return_value=None)
        store.save_signal = AsyncMock(return_value=99)

        sig = _make_signal(
            id_="ev",
            source_url="https://sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=1234",
            raw_data={"canonical_key": "ein:999999"},
        )

        collector = StubCollector(signals=[sig], store=store, collector_name="test")
        result = await collector.run(dry_run=False)

        # is_duplicate receives an evidence_key
        dup_kwargs = store.is_duplicate.call_args.kwargs
        assert dup_kwargs["evidence_key"] is not None

        # save_signal receives the same evidence_key
        save_kwargs = store.save_signal.call_args.kwargs
        assert save_kwargs["evidence_key"] is not None
        assert save_kwargs["evidence_key"] == dup_kwargs["evidence_key"]

        assert result.signals_new == 1
