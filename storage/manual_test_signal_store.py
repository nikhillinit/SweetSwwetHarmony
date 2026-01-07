"""
Manual tests for SignalStore

This is a standalone test script (not run by pytest).

Run with:
    python storage/manual_test_signal_store.py
"""

import asyncio
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from storage.signal_store import (
    SignalStore,
    StoredSignal,
    SuppressionEntry,
    signal_store,
)


async def test_initialization():
    """Test database initialization and migrations."""
    print("Testing initialization...")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"

        store = SignalStore(db_path)
        await store.initialize()

        # Check that tables exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        assert "signals" in tables
        assert "signal_processing" in tables
        assert "suppression_cache" in tables
        assert "schema_migrations" in tables

        await store.close()

    print("  PASS: Initialization works")


async def test_save_and_retrieve_signal():
    """Test saving and retrieving signals."""
    print("Testing save and retrieve...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Save a signal
            signal_id = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={"repo": "acme/ml", "stars": 1500}
            )

            assert signal_id > 0

            # Retrieve it
            signal = await store.get_signal(signal_id)
            assert signal is not None
            assert signal.signal_type == "github_spike"
            assert signal.canonical_key == "domain:acme.ai"
            assert signal.company_name == "Acme Inc"
            assert signal.confidence == 0.85
            assert signal.raw_data["repo"] == "acme/ml"
            assert signal.processing_status == "pending"

    print("  PASS: Save and retrieve works")


async def test_duplicate_detection():
    """Test duplicate detection."""
    print("Testing duplicate detection...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Initially not a duplicate
            assert not await store.is_duplicate("domain:acme.ai")

            # Save a signal
            await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={}
            )

            # Now it is a duplicate
            assert await store.is_duplicate("domain:acme.ai")

            # Different canonical key is not a duplicate
            assert not await store.is_duplicate("domain:other.com")

    print("  PASS: Duplicate detection works")


async def test_pending_signals():
    """Test getting pending signals."""
    print("Testing pending signals...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Save multiple signals
            id1 = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={}
            )

            id2 = await store.save_signal(
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:beta.io",
                company_name="Beta Corp",
                confidence=0.95,
                raw_data={}
            )

            # Get pending signals
            pending = await store.get_pending_signals()
            assert len(pending) == 2

            # Filter by type
            gh_pending = await store.get_pending_signals(signal_type="github_spike")
            assert len(gh_pending) == 1
            assert gh_pending[0].signal_type == "github_spike"

            # Mark one as pushed
            await store.mark_pushed(id1, "notion-123")

            # Now only one pending
            pending = await store.get_pending_signals()
            assert len(pending) == 1
            assert pending[0].id == id2

    print("  PASS: Pending signals works")


async def test_mark_pushed():
    """Test marking signals as pushed."""
    print("Testing mark pushed...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            signal_id = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={}
            )

            # Initially pending
            signal = await store.get_signal(signal_id)
            assert signal.processing_status == "pending"
            assert signal.notion_page_id is None

            # Mark as pushed
            await store.mark_pushed(
                signal_id,
                "notion-abc-123",
                metadata={"status": "Source"}
            )

            # Check updated
            signal = await store.get_signal(signal_id)
            assert signal.processing_status == "pushed"
            assert signal.notion_page_id == "notion-abc-123"
            assert signal.processed_at is not None

    print("  PASS: Mark pushed works")


async def test_mark_rejected():
    """Test marking signals as rejected."""
    print("Testing mark rejected...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            signal_id = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.15,
                raw_data={}
            )

            # Mark as rejected
            await store.mark_rejected(
                signal_id,
                "Low confidence score",
                metadata={"confidence": 0.15}
            )

            # Check updated
            signal = await store.get_signal(signal_id)
            assert signal.processing_status == "rejected"
            assert signal.error_message == "Low confidence score"
            assert signal.processed_at is not None

    print("  PASS: Mark rejected works")


async def test_get_signals_for_company():
    """Test getting all signals for a company."""
    print("Testing get signals for company...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Save multiple signals for same company
            await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.85,
                raw_data={}
            )

            await store.save_signal(
                signal_type="domain_registration",
                source_api="whois",
                canonical_key="domain:acme.ai",
                company_name="Acme Inc",
                confidence=0.9,
                raw_data={}
            )

            # Save signal for different company
            await store.save_signal(
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:beta.io",
                company_name="Beta Corp",
                confidence=0.95,
                raw_data={}
            )

            # Get signals for Acme
            acme_signals = await store.get_signals_for_company("domain:acme.ai")
            assert len(acme_signals) == 2
            assert all(s.canonical_key == "domain:acme.ai" for s in acme_signals)

            # Get signals for Beta
            beta_signals = await store.get_signals_for_company("domain:beta.io")
            assert len(beta_signals) == 1

    print("  PASS: Get signals for company works")


async def test_suppression_cache():
    """Test suppression cache operations."""
    print("Testing suppression cache...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Initially not suppressed
            suppressed = await store.check_suppression("domain:acme.ai")
            assert suppressed is None

            # Add to suppression cache
            entries = [
                SuppressionEntry(
                    canonical_key="domain:acme.ai",
                    notion_page_id="notion-123",
                    status="Source",
                    company_name="Acme Inc",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7)
                )
            ]
            count = await store.update_suppression_cache(entries)
            assert count == 1

            # Now it's suppressed
            suppressed = await store.check_suppression("domain:acme.ai")
            assert suppressed is not None
            assert suppressed.canonical_key == "domain:acme.ai"
            assert suppressed.notion_page_id == "notion-123"
            assert suppressed.status == "Source"

            # Different key not suppressed
            other = await store.check_suppression("domain:other.com")
            assert other is None

    print("  PASS: Suppression cache works")


async def test_expired_cache_cleanup():
    """Test cleaning expired cache entries."""
    print("Testing expired cache cleanup...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Add expired entry
            expired_entry = SuppressionEntry(
                canonical_key="domain:expired.com",
                notion_page_id="notion-old",
                status="Source",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1)
            )

            # Add valid entry
            valid_entry = SuppressionEntry(
                canonical_key="domain:valid.com",
                notion_page_id="notion-new",
                status="Source",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7)
            )

            await store.update_suppression_cache([expired_entry, valid_entry])

            # Expired one should not be returned
            expired = await store.check_suppression("domain:expired.com")
            assert expired is None

            # Valid one should be returned
            valid = await store.check_suppression("domain:valid.com")
            assert valid is not None

            # Clean expired
            cleaned = await store.clean_expired_cache()
            assert cleaned == 1

    print("  PASS: Expired cache cleanup works")


async def test_processing_stats():
    """Test getting processing statistics."""
    print("Testing processing stats...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Save signals with different statuses
            id1 = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:a.ai",
                company_name="A",
                confidence=0.85,
                raw_data={}
            )

            id2 = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:b.ai",
                company_name="B",
                confidence=0.85,
                raw_data={}
            )

            id3 = await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:c.ai",
                company_name="C",
                confidence=0.85,
                raw_data={}
            )

            # Mark different statuses
            await store.mark_pushed(id1, "notion-1")
            await store.mark_rejected(id2, "test reject")
            # id3 stays pending

            # Get stats
            stats = await store.get_processing_stats()
            assert stats["pending"] == 1
            assert stats["pushed"] == 1
            assert stats["rejected"] == 1

    print("  PASS: Processing stats works")


async def test_database_stats():
    """Test overall database statistics."""
    print("Testing database stats...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Add some signals
            await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:a.ai",
                company_name="A",
                confidence=0.85,
                raw_data={}
            )

            await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:b.ai",
                company_name="B",
                confidence=0.85,
                raw_data={}
            )

            await store.save_signal(
                signal_type="incorporation",
                source_api="companies_house",
                canonical_key="domain:c.ai",
                company_name="C",
                confidence=0.95,
                raw_data={}
            )

            # Add suppression entry
            await store.update_suppression_cache([
                SuppressionEntry(
                    canonical_key="domain:a.ai",
                    notion_page_id="notion-1",
                    status="Source"
                )
            ])

            # Get stats
            stats = await store.get_stats()
            assert stats["total_signals"] == 3
            assert stats["signals_by_type"]["github_spike"] == 2
            assert stats["signals_by_type"]["incorporation"] == 1
            assert stats["processing_status"]["pending"] == 3
            assert stats["active_suppression_entries"] == 1

    print("  PASS: Database stats works")


async def test_transaction_rollback():
    """Test transaction rollback on error."""
    print("Testing transaction rollback...")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with signal_store(Path(tmpdir) / "test.db") as store:
            # Save initial signal
            await store.save_signal(
                signal_type="github_spike",
                source_api="github",
                canonical_key="domain:test.ai",
                company_name="Test",
                confidence=0.85,
                raw_data={}
            )

            # Try a transaction that will fail
            try:
                async with store.transaction() as conn:
                    await conn.execute(
                        "INSERT INTO signals (signal_type, source_api, canonical_key, confidence, raw_data, detected_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("test", "test", "domain:rollback.ai", 0.5, "{}", datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat())
                    )
                    # Force an error
                    raise ValueError("Test error")
            except ValueError:
                pass

            # Check that rollback worked
            cursor = await store._db.execute(
                "SELECT COUNT(*) FROM signals WHERE canonical_key = 'domain:rollback.ai'"
            )
            count = (await cursor.fetchone())[0]
            assert count == 0

    print("  PASS: Transaction rollback works")


async def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Running SignalStore Tests")
    print("=" * 60)

    await test_initialization()
    await test_save_and_retrieve_signal()
    await test_duplicate_detection()
    await test_pending_signals()
    await test_mark_pushed()
    await test_mark_rejected()
    await test_get_signals_for_company()
    await test_suppression_cache()
    await test_expired_cache_cleanup()
    await test_processing_stats()
    await test_database_stats()
    await test_transaction_rollback()

    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
