"""Tests for NotionOutboxWorker payload building and drain semantics."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage
from monitoring.events import EventType
from storage.signal_store import SignalStore
from workflows.notion_outbox_worker import NotionOutboxWorker


class TestBuildProspectPayload:
    """Tests for _build_prospect_payload investor_matches handling."""

    def _make_worker(self):
        """Create a minimal outbox worker for testing payload builder."""
        worker = NotionOutboxWorker.__new__(NotionOutboxWorker)
        # Minimal state — only _build_prospect_payload is needed
        return worker

    def test_preserves_provided_investor_matches(self):
        """Provided investor_matches should be preserved in payload."""
        worker = self._make_worker()
        matches = [
            {"investor_id": "inv-1", "score": 0.9, "name": "Fund A"},
            {"investor_id": "inv-2", "score": 0.7, "name": "Fund B"},
        ]
        data = {
            "discovery_id": "d-123",
            "company_name": "Acme",
            "canonical_key": "domain:acme.com",
            "investor_matches": matches,
        }

        payload = worker._build_prospect_payload(data)
        assert payload.investor_matches == matches
        assert len(payload.investor_matches) == 2

    def test_missing_key_defaults_to_empty_list(self):
        """Missing investor_matches key should default to empty list."""
        worker = self._make_worker()
        data = {
            "discovery_id": "d-456",
            "company_name": "TestCo",
            "canonical_key": "domain:testco.com",
            # No investor_matches key
        }

        payload = worker._build_prospect_payload(data)
        assert payload.investor_matches == []

    def test_none_value_defaults_to_empty_list(self):
        """investor_matches=None should be treated as empty list."""
        worker = self._make_worker()
        data = {
            "discovery_id": "d-789",
            "company_name": "NullCo",
            "canonical_key": "domain:nullco.com",
            "investor_matches": None,
        }

        payload = worker._build_prospect_payload(data)
        assert payload.investor_matches == []

    def test_round_trip_all_fields_preserved(self):
        """Serialized ProspectPayload -> dict -> _build_prospect_payload preserves fields."""
        worker = self._make_worker()

        original = ProspectPayload(
            discovery_id="d-rt",
            company_name="RoundTrip Inc",
            canonical_key="domain:roundtrip.com",
            stage=InvestmentStage.SEED,
            status="Tracking",
            website="https://roundtrip.com",
            canonical_key_candidates=["domain:roundtrip.com"],
            confidence_score=0.65,
            signal_types=["github_spike"],
            why_now="Growing fast",
            short_description="Consumer marketplace",
            sector="Consumer Marketplaces",
            founder_name="Jane Doe",
            founder_linkedin="https://linkedin.com/in/janedoe",
            location="New York, NY",
            target_raise="$5M",
            external_refs={"github": "roundtrip/app"},
            watchlists_matched=["consumer-cpg"],
            investor_matches=[
                {"investor_id": "inv-x", "score": 0.85, "name": "Investor X"},
            ],
        )

        # Simulate serialization to dict (as done in pipeline.py)
        data = {
            "discovery_id": original.discovery_id,
            "company_name": original.company_name,
            "canonical_key": original.canonical_key,
            "stage": original.stage.value,
            "status": original.status,
            "website": original.website,
            "canonical_key_candidates": original.canonical_key_candidates,
            "confidence_score": original.confidence_score,
            "signal_types": original.signal_types,
            "why_now": original.why_now,
            "short_description": original.short_description,
            "sector": original.sector,
            "founder_name": original.founder_name,
            "founder_linkedin": original.founder_linkedin,
            "location": original.location,
            "target_raise": original.target_raise,
            "external_refs": original.external_refs,
            "watchlists_matched": original.watchlists_matched,
            "investor_matches": original.investor_matches,
        }

        rebuilt = worker._build_prospect_payload(data)

        assert rebuilt.discovery_id == original.discovery_id
        assert rebuilt.company_name == original.company_name
        assert rebuilt.canonical_key == original.canonical_key
        assert rebuilt.confidence_score == original.confidence_score
        assert rebuilt.investor_matches == original.investor_matches
        assert rebuilt.signal_types == original.signal_types
        assert rebuilt.watchlists_matched == original.watchlists_matched
        assert rebuilt.external_refs == original.external_refs


class FakeOutboxStore:
    """Small fake that models claim/finalize without a live database."""

    def __init__(self, entries_by_type=None):
        self.entries_by_type = {
            event_type: list(entries)
            for event_type, entries in (entries_by_type or {}).items()
        }
        self.claim_calls = []
        self.finalize_calls = []
        self.mark_pushed_calls = []
        self.shadow_calls = []

    async def claim_due_outbox(
        self,
        *,
        event_type: str,
        limit: int,
        stale_processing_ttl_minutes: int = 30,
    ):
        self.claim_calls.append({
            "event_type": event_type,
            "limit": limit,
            "stale_processing_ttl_minutes": stale_processing_ttl_minutes,
        })
        entries = self.entries_by_type.get(event_type, [])
        claimed = entries[:limit]
        self.entries_by_type[event_type] = entries[limit:]
        return claimed

    async def finalize_outbox(
        self,
        outbox_id: int,
        success: bool,
        error: str | None = None,
        backoff_seconds: float = 60.0,
    ):
        self.finalize_calls.append({
            "outbox_id": outbox_id,
            "success": success,
            "error": error,
            "backoff_seconds": backoff_seconds,
        })

    async def mark_pushed(self, *, signal_id, notion_page_id, metadata=None):
        self.mark_pushed_calls.append({
            "signal_id": signal_id,
            "notion_page_id": notion_page_id,
            "metadata": metadata,
        })

    async def log_shadow_computation(self, **kwargs):
        self.shadow_calls.append(kwargs)


def _notion_push_entry(outbox_id: int = 1):
    return {
        "id": outbox_id,
        "event_type": EventType.NOTION_PUSH.value,
        "attempts": 0,
        "payload": {
            "prospect": {
                "discovery_id": "disc-1",
                "company_name": "Acme",
                "canonical_key": "domain:acme.com",
            },
            "signal_ids": [101],
            "metadata": {"source": "test"},
        },
    }


def _notion_push_payload(signal_id: int, company_name: str):
    return {
        "prospect": {
            "discovery_id": f"disc-{signal_id}",
            "company_name": company_name,
            "canonical_key": f"domain:{company_name.lower()}.example",
        },
        "signal_ids": [signal_id],
        "metadata": {"source": "temp-db-fixture"},
    }


def _profile_update_entry(
    outbox_id: int = 2,
    *,
    row_event_type: str = EventType.PROFILE_UPDATE_REQUESTED.value,
):
    return {
        "id": outbox_id,
        "event_type": row_event_type,
        "attempts": 0,
        "payload": {
            "event_type": EventType.PROFILE_UPDATE_REQUESTED.value,
            "version": 1,
            "watch_id": 10,
            "snapshot_id": 20,
            "diff_id": 30,
            "trigger": "high_severity",
            "canonical_key": "domain:acme.com",
            "url": "https://acme.com",
        },
    }


class TestNotionOutboxWorkerDrain:
    """Drain tests prove the claim/finalize relay contract."""

    @pytest.mark.asyncio
    async def test_drain_success_claims_and_finalizes_notion_push(self):
        store = FakeOutboxStore({
            EventType.NOTION_PUSH.value: [_notion_push_entry()],
        })
        notion = MagicMock()
        notion.upsert_prospect = AsyncMock(
            return_value={"status": "created", "page_id": "page-1"}
        )
        worker = NotionOutboxWorker(signal_store=store, notion_connector=notion)

        stats = await worker.drain(limit=10)

        assert stats["processed"] == 1
        assert stats["sent"] == 1
        assert stats["created"] == 1
        assert store.claim_calls[0]["event_type"] == EventType.NOTION_PUSH.value
        assert store.finalize_calls == [{
            "outbox_id": 1,
            "success": True,
            "error": None,
            "backoff_seconds": 60.0,
        }]
        assert store.mark_pushed_calls == [{
            "signal_id": 101,
            "notion_page_id": "page-1",
            "metadata": {"source": "test"},
        }]
        notion.upsert_prospect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drain_failure_finalizes_for_retry_with_backoff(self):
        store = FakeOutboxStore({
            EventType.NOTION_PUSH.value: [_notion_push_entry()],
        })
        notion = MagicMock()
        notion.upsert_prospect = AsyncMock(side_effect=RuntimeError("notion down"))
        worker = NotionOutboxWorker(signal_store=store, notion_connector=notion)
        worker._compute_backoff = lambda attempts: 12.5

        stats = await worker.drain(limit=10)

        assert stats["processed"] == 1
        assert stats["failed"] == 1
        assert store.finalize_calls == [{
            "outbox_id": 1,
            "success": False,
            "error": "notion down",
            "backoff_seconds": 12.5,
        }]
        assert store.mark_pushed_calls == []

    @pytest.mark.asyncio
    async def test_drain_claim_contract_prevents_duplicate_processing(self):
        store = FakeOutboxStore({
            EventType.NOTION_PUSH.value: [_notion_push_entry()],
        })
        notion = MagicMock()
        notion.upsert_prospect = AsyncMock(
            return_value={"status": "updated", "page_id": "page-1"}
        )
        worker = NotionOutboxWorker(signal_store=store, notion_connector=notion)

        first = await worker.drain(limit=10)
        second = await worker.drain(limit=10)

        assert first["processed"] == 1
        assert second["processed"] == 0
        assert notion.upsert_prospect.await_count == 1
        assert len(store.finalize_calls) == 1

    @pytest.mark.asyncio
    async def test_drain_claims_and_finalizes_profile_update_events(self):
        store = FakeOutboxStore({
            EventType.PROFILE_UPDATE_REQUESTED.value: [_profile_update_entry()],
        })
        notion = MagicMock()
        notion.upsert_prospect = AsyncMock()
        worker = NotionOutboxWorker(signal_store=store, notion_connector=notion)
        worker._handle_profile_update = AsyncMock()

        stats = await worker.drain(limit=10)

        assert stats["processed"] == 1
        assert stats["sent"] == 1
        assert stats["profile_updates"] == 1
        assert [call["event_type"] for call in store.claim_calls] == [
            EventType.NOTION_PUSH.value,
            EventType.PROFILE_UPDATE_REQUESTED.value,
        ]
        assert store.finalize_calls == [{
            "outbox_id": 2,
            "success": True,
            "error": None,
            "backoff_seconds": 60.0,
        }]
        worker._handle_profile_update.assert_awaited_once()
        notion.upsert_prospect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_prefers_payload_event_type_for_legacy_rows(self):
        store = FakeOutboxStore({
            EventType.NOTION_PUSH.value: [
                _profile_update_entry(row_event_type=EventType.NOTION_PUSH.value),
            ],
        })
        notion = MagicMock()
        notion.upsert_prospect = AsyncMock()
        worker = NotionOutboxWorker(signal_store=store, notion_connector=notion)
        worker._handle_profile_update = AsyncMock()

        stats = await worker.drain(limit=10)

        assert stats["processed"] == 1
        assert stats["profile_updates"] == 1
        assert store.claim_calls[0]["event_type"] == EventType.NOTION_PUSH.value
        worker._handle_profile_update.assert_awaited_once()
        notion.upsert_prospect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_real_temp_db_proves_claim_finalize_retry_and_no_duplicate(
        self,
        tmp_path,
    ):
        store = SignalStore(db_path=tmp_path / "signals.db")
        await store.initialize()
        try:
            first_signal_id = await store.save_signal(
                signal_type="github_repo",
                source_api="github",
                canonical_key="domain:acme.example",
                company_name="Acme",
                confidence=0.8,
                raw_data={"url": "https://github.com/acme/app"},
            )
            await store.enqueue_notion_write(
                idempotency_key="success-1",
                payload=_notion_push_payload(first_signal_id, "Acme"),
                event_type=EventType.NOTION_PUSH.value,
            )

            notion = MagicMock()
            notion.upsert_prospect = AsyncMock(
                return_value={"status": "created", "page_id": "page-success"}
            )
            worker = NotionOutboxWorker(signal_store=store, notion_connector=notion)

            success_stats = await worker.drain(limit=10)
            duplicate_success_stats = await worker.drain(limit=10)

            assert success_stats["processed"] == 1
            assert success_stats["sent"] == 1
            assert success_stats["created"] == 1
            assert duplicate_success_stats["processed"] == 0
            assert notion.upsert_prospect.await_count == 1

            cursor = await store._db.execute(
                """
                SELECT o.status, p.status, p.notion_page_id
                FROM notion_outbox AS o
                JOIN signal_processing AS p ON p.signal_id = ?
                WHERE o.idempotency_key = ?
                """,
                (first_signal_id, "success-1"),
            )
            row = await cursor.fetchone()
            assert row == ("sent", "pushed", "page-success")

            retry_signal_id = await store.save_signal(
                signal_type="github_repo",
                source_api="github",
                canonical_key="domain:retry.example",
                company_name="RetryCo",
                confidence=0.7,
                raw_data={"url": "https://github.com/retry/app"},
            )
            await store.enqueue_notion_write(
                idempotency_key="retry-1",
                payload=_notion_push_payload(retry_signal_id, "RetryCo"),
                event_type=EventType.NOTION_PUSH.value,
            )

            failing_notion = MagicMock()
            failing_notion.upsert_prospect = AsyncMock(
                side_effect=RuntimeError("notion down")
            )
            failing_worker = NotionOutboxWorker(
                signal_store=store,
                notion_connector=failing_notion,
            )
            failing_worker._compute_backoff = lambda attempts: 60.0

            failure_stats = await failing_worker.drain(limit=10)

            assert failure_stats["processed"] == 1
            assert failure_stats["failed"] == 1
            assert failing_notion.upsert_prospect.await_count == 1

            cursor = await store._db.execute(
                """
                SELECT status, attempts, last_error, next_attempt_at
                FROM notion_outbox
                WHERE idempotency_key = ?
                """,
                ("retry-1",),
            )
            status, attempts, last_error, next_attempt_at = await cursor.fetchone()
            assert status == "pending"
            assert attempts == 1
            assert last_error == "notion down"
            assert next_attempt_at is not None

            due_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            await store._db.execute(
                """
                UPDATE notion_outbox
                SET next_attempt_at = ?
                WHERE idempotency_key = ?
                """,
                (due_at, "retry-1"),
            )
            await store._db.commit()

            retry_notion = MagicMock()
            retry_notion.upsert_prospect = AsyncMock(
                return_value={"status": "updated", "page_id": "page-retry"}
            )
            retry_worker = NotionOutboxWorker(
                signal_store=store,
                notion_connector=retry_notion,
            )

            retry_stats = await retry_worker.drain(limit=10)
            duplicate_retry_stats = await retry_worker.drain(limit=10)

            assert retry_stats["processed"] == 1
            assert retry_stats["sent"] == 1
            assert retry_stats["updated"] == 1
            assert duplicate_retry_stats["processed"] == 0
            assert retry_notion.upsert_prospect.await_count == 1

            cursor = await store._db.execute(
                """
                SELECT o.status, o.attempts, p.status, p.notion_page_id
                FROM notion_outbox AS o
                JOIN signal_processing AS p ON p.signal_id = ?
                WHERE o.idempotency_key = ?
                """,
                (retry_signal_id, "retry-1"),
            )
            row = await cursor.fetchone()
            assert row == ("sent", 1, "pushed", "page-retry")
        finally:
            await store.close()
