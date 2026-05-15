"""Regression tests for SignalStore read-only dry-run support."""

from __future__ import annotations

import pytest

from storage.signal_store import ReadOnlyStoreError, SignalStore, SuppressionEntry


@pytest.mark.asyncio
async def test_read_only_signal_store_initializes_against_existing_db(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = SignalStore(db_path=db_path)
    await writable.initialize()
    await writable.close()

    readonly = SignalStore(db_path=db_path, read_only=True)
    await readonly.initialize()

    pending = await readonly.get_pending_signals()

    assert pending == []
    await readonly.close()


@pytest.mark.asyncio
async def test_read_only_signal_store_blocks_transactions(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = SignalStore(db_path=db_path)
    await writable.initialize()
    await writable.close()

    readonly = SignalStore(db_path=db_path, read_only=True)
    await readonly.initialize()

    with pytest.raises(ReadOnlyStoreError):
        async with readonly.transaction() as conn:
            await conn.execute("CREATE TABLE should_not_exist (id INTEGER)")

    await readonly.close()


@pytest.mark.asyncio
async def test_read_only_signal_store_blocks_process_write_apis(tmp_path):
    db_path = tmp_path / "signals.db"

    writable = SignalStore(db_path=db_path)
    await writable.initialize()
    signal_id = await writable.save_signal(
        signal_type="github_trending",
        source_api="github",
        canonical_key="domain:readonly.test",
        company_name="Readonly Test",
        confidence=0.8,
        raw_data={"description": "consumer wellness"},
    )
    await writable.close()

    readonly = SignalStore(db_path=db_path, read_only=True)
    await readonly.initialize()

    async def _save_confidence_ledger() -> None:
        verification_result = type(
            "VerificationResultFixture",
            (),
            {
                "decision": type("DecisionFixture", (), {"value": "hold"})(),
                "verification_status": type("StatusFixture", (), {"value": "single_source"})(),
                "confidence_score": 0.5,
                "confidence_breakdown": {
                    "overall": 0.5,
                    "base_score": 0.5,
                    "signals_contributing": 1,
                    "sources_checked": 1,
                },
                "reason": "test",
                "suggested_status": "Tracking",
                "signals_used": [],
                "sources_checked": [],
                "verification_details": [],
            },
        )()
        await readonly.save_confidence_ledger(
            canonical_key="domain:readonly.test",
            verification_result=verification_result,
            signal_ids=[signal_id],
            policy_version="test-policy",
            routing_config={"high_threshold": 0.7, "medium_threshold": 0.4},
        )

    write_calls = [
        lambda: readonly.mark_rejected(signal_id, "dry-run rejected"),
        lambda: readonly.log_shadow_computation(
            feature_name="thesis_match",
            canonical_key="domain:readonly.test",
            computed_value={"routing": "held"},
            signal_id=signal_id,
        ),
        lambda: readonly.update_suppression_cache([
            SuppressionEntry(
                canonical_key="domain:readonly.test",
                notion_page_id="page-1",
                status="Source",
            )
        ]),
        lambda: readonly.save_thesis_classification(
            signal_id=signal_id,
            canonical_key="domain:readonly.test",
            keyword_score=0.5,
        ),
        lambda: readonly.save_functional_schema({"company_id": "company-readonly"}),
        lambda: readonly.store_exit_prediction(object()),
        lambda: readonly.enqueue_notion_write("readonly-key", {"canonical_key": "domain:readonly.test"}),
        _save_confidence_ledger,
    ]

    try:
        for write_call in write_calls:
            with pytest.raises(ReadOnlyStoreError):
                await write_call()
    finally:
        await readonly.close()
