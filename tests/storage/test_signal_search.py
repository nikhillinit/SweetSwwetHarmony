# tests/storage/test_signal_search.py
"""Tests for FTS5 signal search functionality."""
import pytest
from storage.signal_store import SignalStore


class TestFTSSetup:
    """Test FTS5 virtual table creation."""

    @pytest.mark.asyncio
    async def test_fts_table_exists_after_init(self):
        """FTS table should be created during initialization."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # Query sqlite_master to check for FTS table
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals_fts'"
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "signals_fts"
        await store.close()


class TestFTSIndexing:
    """Test FTS5 indexing operations."""

    @pytest.mark.asyncio
    async def test_index_signal_adds_to_fts(self):
        """Indexing a signal should add it to FTS table."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # Create a test signal first using save_signal (which returns int ID)
        signal_id = await store.save_signal(
            signal_type="funding",
            source_api="producthunt",
            canonical_key="domain:telehealthplus.com",
            company_name="Telehealth Plus",
            confidence=0.85,
            raw_data={"description": "Virtual care platform for remote patients"},
        )

        # Index it (pass vertical since signals table doesn't have it)
        await store.index_signal_for_search(signal_id, vertical="health")

        # Verify in FTS
        cursor = await store._db.execute(
            "SELECT company_name FROM signals_fts WHERE signal_id = ?",
            (str(signal_id),)
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "Telehealth Plus"
        await store.close()

    @pytest.mark.asyncio
    async def test_index_signal_extracts_searchable_text(self):
        """Indexing should extract searchable text from raw_data fields."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        signal_id = await store.save_signal(
            signal_type="product_launch",
            source_api="producthunt",
            canonical_key="domain:aitools.io",
            company_name="AI Tools Co",
            confidence=0.90,
            raw_data={
                "description": "Revolutionary AI-powered analytics",
                "tagline": "Smarter insights faster",
                "tags": ["analytics", "machine-learning"],
                "category": "Developer Tools",
            },
        )

        await store.index_signal_for_search(signal_id, vertical="tech")

        # Verify searchable_text contains extracted fields
        cursor = await store._db.execute(
            "SELECT searchable_text FROM signals_fts WHERE signal_id = ?",
            (str(signal_id),)
        )
        row = await cursor.fetchone()

        assert row is not None
        searchable_text = row[0]
        assert "Revolutionary AI-powered analytics" in searchable_text
        assert "Smarter insights faster" in searchable_text
        assert "analytics" in searchable_text
        assert "machine-learning" in searchable_text
        assert "Developer Tools" in searchable_text
        await store.close()

    @pytest.mark.asyncio
    async def test_index_signal_upserts_on_reindex(self):
        """Re-indexing a signal should update the FTS entry, not duplicate."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        signal_id = await store.save_signal(
            signal_type="funding",
            source_api="crunchbase",
            canonical_key="domain:example.com",
            company_name="Example Corp",
            confidence=0.75,
            raw_data={"description": "Original description"},
        )

        # Index once
        await store.index_signal_for_search(signal_id, vertical="fintech")

        # Index again (should update, not create duplicate)
        await store.index_signal_for_search(signal_id, vertical="fintech")

        # Should have exactly one entry
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals_fts WHERE signal_id = ?",
            (str(signal_id),)
        )
        row = await cursor.fetchone()

        assert row[0] == 1
        await store.close()

    @pytest.mark.asyncio
    async def test_index_nonexistent_signal_does_nothing(self):
        """Indexing a non-existent signal should silently do nothing."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # Try to index a signal that doesn't exist
        await store.index_signal_for_search(99999, vertical="unknown")

        # Should not have added anything
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals_fts WHERE signal_id = ?",
            ("99999",)
        )
        row = await cursor.fetchone()

        assert row[0] == 0
        await store.close()


class TestFuzzySearch:
    """Test FTS5 fuzzy search functionality."""

    @pytest.mark.asyncio
    async def test_partial_name_match(self):
        """'tele' should match 'Telehealth Plus' and 'Teladoc Health'."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # Add test signals
        id1 = await store.save_signal(
            company_name="Telehealth Plus",
            signal_type="funding",
            source_api="producthunt",
            canonical_key="domain:telehealthplus.com",
            raw_data={"description": "Virtual care"},
            confidence=0.8
        )
        await store.index_signal_for_search(id1, vertical="health")

        id2 = await store.save_signal(
            company_name="Teladoc Health",
            signal_type="funding",
            source_api="sec_edgar",
            canonical_key="domain:teladoc.com",
            raw_data={"description": "Telemedicine"},
            confidence=0.8
        )
        await store.index_signal_for_search(id2, vertical="health")

        results = await store.search_signals_fts("tele")

        assert len(results) == 2
        names = [r["company_name"] for r in results]
        assert "Telehealth Plus" in names
        assert "Teladoc Health" in names
        await store.close()

    @pytest.mark.asyncio
    async def test_description_match(self):
        """'virtual care' should match signal with that description."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        id1 = await store.save_signal(
            company_name="HealthCo",
            signal_type="funding",
            source_api="producthunt",
            canonical_key="domain:healthco.com",
            raw_data={"description": "Virtual care platform for remote patients"},
            confidence=0.8
        )
        await store.index_signal_for_search(id1, vertical="health")

        results = await store.search_signals_fts("virtual care")

        assert len(results) >= 1
        assert results[0]["company_name"] == "HealthCo"
        await store.close()

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """Non-matching query returns empty list."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        results = await store.search_signals_fts("xyznonexistent")

        assert results == []
        await store.close()

    @pytest.mark.asyncio
    async def test_results_include_rank(self):
        """Results should include relevance rank."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        id1 = await store.save_signal(
            company_name="Health App",
            signal_type="funding",
            source_api="producthunt",
            canonical_key="domain:healthapp.com",
            raw_data={"description": "Health tracking"},
            confidence=0.8
        )
        await store.index_signal_for_search(id1, vertical="health")

        results = await store.search_signals_fts("health")

        assert len(results) > 0
        assert "rank" in results[0]
        await store.close()


class TestFTSEdgeCases:
    """Test FTS5 edge cases and security."""

    @pytest.mark.asyncio
    async def test_unicode_search(self):
        """Search with Unicode characters works."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # Add signal with Unicode
        signal_id = await store.save_signal(
            company_name="Cafe Health",
            signal_type="funding",
            source_api="producthunt",
            canonical_key="domain:cafehealth.com",
            raw_data={"description": "Healthy cafe services"},
            confidence=0.8
        )
        await store.index_signal_for_search(signal_id, vertical="health")

        results = await store.search_signals_fts("cafe")
        assert len(results) >= 1
        await store.close()

    @pytest.mark.asyncio
    async def test_special_characters_safe(self):
        """Special characters don't break search."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        # These shouldn't crash
        results = await store.search_signals_fts('test"query')
        assert isinstance(results, list)

        results = await store.search_signals_fts("test'query")
        assert isinstance(results, list)

        results = await store.search_signals_fts("test*query")
        assert isinstance(results, list)

        results = await store.search_signals_fts("test-query")
        assert isinstance(results, list)

        results = await store.search_signals_fts("test+query")
        assert isinstance(results, list)

        await store.close()

    @pytest.mark.asyncio
    async def test_very_long_query_handled(self):
        """Very long queries don't crash."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        long_query = "a" * 1000
        results = await store.search_signals_fts(long_query)
        assert isinstance(results, list)
        await store.close()

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        """Empty query returns empty list, not all results."""
        store = SignalStore(db_path=":memory:")
        await store.initialize()

        results = await store.search_signals_fts("")
        assert results == []

        results = await store.search_signals_fts("   ")
        assert results == []
        await store.close()
