"""
Tests for gold set manager.

Sprint 6: Evaluation & Calibration.
"""

import pytest
import tempfile
from pathlib import Path

from utils.gold_set_manager import (
    GoldSetManager,
    GoldSetCompany,
    GoldSetLabel,
    GoldSetInvestorLabel,
    GoldSetStats,
)


# =============================================================================
# DATA CLASS TESTS
# =============================================================================

class TestDataClasses:
    """Tests for data classes."""

    def test_gold_set_company_to_dict(self):
        """GoldSetCompany converts to dict correctly."""
        company = GoldSetCompany(
            canonical_key="domain:test.com",
            company_name="Test Co",
            category="core_sector",
            id=1,
        )
        d = company.to_dict()
        assert d["canonical_key"] == "domain:test.com"
        assert d["category"] == "core_sector"

    def test_gold_set_label_to_dict(self):
        """GoldSetLabel converts to dict correctly."""
        label = GoldSetLabel(
            company_id=1,
            predicate="sector",
            label_type="exact",
            annotator="alice",
            gold_value="fintech",
        )
        d = label.to_dict()
        assert d["predicate"] == "sector"
        assert d["label_type"] == "exact"

    def test_gold_set_investor_label_to_dict(self):
        """GoldSetInvestorLabel converts to dict correctly."""
        label = GoldSetInvestorLabel(
            company_id=1,
            investor_id="investor:test_vc",
            relevance="relevant",
            annotator="bob",
        )
        d = label.to_dict()
        assert d["investor_id"] == "investor:test_vc"
        assert d["relevance"] == "relevant"

    def test_gold_set_stats_defaults(self):
        """GoldSetStats has correct defaults."""
        stats = GoldSetStats()
        assert stats.total_companies == 0
        assert stats.by_category == {}
        assert stats.annotators == []


# =============================================================================
# GOLD SET MANAGER TESTS
# =============================================================================

class TestGoldSetManager:
    """Tests for GoldSetManager class."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def manager(self, store):
        """Create manager with store."""
        return GoldSetManager(store)

    @pytest.mark.asyncio
    async def test_add_company(self, manager):
        """Can add a company to gold set."""
        company_id = await manager.add_company(
            canonical_key="domain:test.com",
            company_name="Test Co",
            category="core_sector",
            annotator_1="alice",
        )
        assert company_id > 0

    @pytest.mark.asyncio
    async def test_add_company_invalid_category(self, manager):
        """Invalid category raises error."""
        with pytest.raises(ValueError, match="Invalid category"):
            await manager.add_company(
                canonical_key="domain:test.com",
                company_name="Test Co",
                category="invalid_category",
            )

    @pytest.mark.asyncio
    async def test_get_company(self, manager):
        """Can retrieve a company."""
        await manager.add_company(
            canonical_key="domain:test.com",
            company_name="Test Co",
            category="core_sector",
        )

        company = await manager.get_company("domain:test.com")
        assert company is not None
        assert company.company_name == "Test Co"
        assert company.category == "core_sector"

    @pytest.mark.asyncio
    async def test_get_company_not_found(self, manager):
        """Returns None for non-existent company."""
        company = await manager.get_company("domain:nonexistent.com")
        assert company is None

    @pytest.mark.asyncio
    async def test_list_companies(self, manager):
        """Can list all companies."""
        await manager.add_company("domain:a.com", "Company A", "core_sector")
        await manager.add_company("domain:b.com", "Company B", "hard_negative")
        await manager.add_company("domain:c.com", "Company C", "core_sector")

        companies = await manager.list_companies()
        assert len(companies) == 3

    @pytest.mark.asyncio
    async def test_list_companies_by_category(self, manager):
        """Can filter companies by category."""
        await manager.add_company("domain:a.com", "Company A", "core_sector")
        await manager.add_company("domain:b.com", "Company B", "hard_negative")
        await manager.add_company("domain:c.com", "Company C", "core_sector")

        companies = await manager.list_companies(category="core_sector")
        assert len(companies) == 2
        assert all(c.category == "core_sector" for c in companies)

    @pytest.mark.asyncio
    async def test_delete_company(self, manager):
        """Can delete a company."""
        await manager.add_company("domain:delete.com", "Delete Me", "ambiguous")

        deleted = await manager.delete_company("domain:delete.com")
        assert deleted is True

        company = await manager.get_company("domain:delete.com")
        assert company is None

    @pytest.mark.asyncio
    async def test_add_label(self, manager):
        """Can add a label to a company."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        label_id = await manager.add_label(
            company_id=company.id,
            predicate="sector",
            label_type="exact",
            annotator="alice",
            gold_value="fintech",
        )
        assert label_id > 0

    @pytest.mark.asyncio
    async def test_add_label_invalid_predicate(self, manager):
        """Invalid predicate raises error."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        with pytest.raises(ValueError, match="Invalid predicate"):
            await manager.add_label(
                company_id=company.id,
                predicate="invalid_predicate",
                label_type="exact",
                annotator="alice",
            )

    @pytest.mark.asyncio
    async def test_add_label_invalid_label_type(self, manager):
        """Invalid label_type raises error."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        with pytest.raises(ValueError, match="Invalid label_type"):
            await manager.add_label(
                company_id=company.id,
                predicate="sector",
                label_type="invalid_type",
                annotator="alice",
            )

    @pytest.mark.asyncio
    async def test_get_labels(self, manager):
        """Can retrieve labels for a company."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        await manager.add_label(company.id, "sector", "exact", "alice", "fintech")
        await manager.add_label(company.id, "stage", "partial", "alice", "seed")

        labels = await manager.get_labels(company.id)
        assert len(labels) == 2

    @pytest.mark.asyncio
    async def test_get_labels_by_predicate(self, manager):
        """Can filter labels by predicate."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        await manager.add_label(company.id, "sector", "exact", "alice", "fintech")
        await manager.add_label(company.id, "stage", "partial", "alice", "seed")

        labels = await manager.get_labels(company.id, predicate="sector")
        assert len(labels) == 1
        assert labels[0].predicate == "sector"

    @pytest.mark.asyncio
    async def test_add_investor_label(self, manager, store):
        """Can add an investor label."""
        # Create investor first
        await store.save_investor(
            investor_id="investor:test_vc",
            name="Test VC",
            source="curated_json",
        )

        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        label_id = await manager.add_investor_label(
            company_id=company.id,
            investor_id="investor:test_vc",
            relevance="relevant",
            annotator="bob",
        )
        assert label_id > 0

    @pytest.mark.asyncio
    async def test_add_investor_label_invalid_relevance(self, manager):
        """Invalid relevance raises error."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        with pytest.raises(ValueError, match="Invalid relevance"):
            await manager.add_investor_label(
                company_id=company.id,
                investor_id="investor:test_vc",
                relevance="invalid_relevance",
                annotator="bob",
            )

    @pytest.mark.asyncio
    async def test_get_stats(self, manager):
        """Can get gold set statistics."""
        await manager.add_company("domain:a.com", "A", "core_sector", annotator_1="alice")
        await manager.add_company("domain:b.com", "B", "hard_negative", annotator_1="bob")

        company_a = await manager.get_company("domain:a.com")
        await manager.add_label(company_a.id, "sector", "exact", "alice", "fintech")

        stats = await manager.get_stats()
        assert stats.total_companies == 2
        assert stats.by_category["core_sector"] == 1
        assert stats.by_category["hard_negative"] == 1
        assert stats.total_labels == 1
        assert "alice" in stats.annotators
        assert "bob" in stats.annotators


class TestGoldSetImportExport:
    """Tests for import/export functionality."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def manager(self, store):
        """Create manager with store."""
        return GoldSetManager(store)

    @pytest.mark.asyncio
    async def test_export_import_json(self, manager):
        """Can export and import JSON."""
        # Add test data
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")
        await manager.add_label(company.id, "sector", "exact", "alice", "fintech")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gold_set.json"

            # Export
            count = await manager.export_to_json(path)
            assert count == 1
            assert path.exists()

            # Clear and reimport
            await manager.delete_company("domain:test.com")
            assert await manager.get_company("domain:test.com") is None

            # Import
            count = await manager.import_from_json(path)
            assert count == 1

            # Verify
            company = await manager.get_company("domain:test.com")
            assert company is not None
            assert company.company_name == "Test Co"

            labels = await manager.get_labels(company.id)
            assert len(labels) == 1
            assert labels[0].gold_value == "fintech"

    @pytest.mark.asyncio
    async def test_export_import_csv(self, manager):
        """Can export and import CSV."""
        # Add test data
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")
        await manager.add_label(company.id, "sector", "exact", "alice", "fintech")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gold_set.csv"

            # Export
            count = await manager.export_to_csv(path)
            assert count == 1
            assert path.exists()
            assert path.with_suffix(".labels.csv").exists()

            # Clear and reimport
            await manager.delete_company("domain:test.com")

            # Import
            count = await manager.import_from_csv(path)
            assert count == 1

            # Verify
            company = await manager.get_company("domain:test.com")
            assert company is not None

            labels = await manager.get_labels(company.id)
            assert len(labels) == 1


class TestGoldSetUpsert:
    """Tests for upsert behavior."""

    @pytest.fixture
    async def store(self):
        """Create in-memory store."""
        from storage.signal_store import SignalStore
        store = SignalStore(":memory:")
        await store.initialize()
        yield store
        await store.close()

    @pytest.fixture
    async def manager(self, store):
        """Create manager with store."""
        return GoldSetManager(store)

    @pytest.mark.asyncio
    async def test_company_upsert(self, manager):
        """Adding same company updates instead of duplicating."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        await manager.add_company("domain:test.com", "Test Co Updated", "hard_negative")

        companies = await manager.list_companies()
        assert len(companies) == 1
        assert companies[0].company_name == "Test Co Updated"
        assert companies[0].category == "hard_negative"

    @pytest.mark.asyncio
    async def test_label_upsert(self, manager):
        """Adding same label updates instead of duplicating."""
        await manager.add_company("domain:test.com", "Test Co", "core_sector")
        company = await manager.get_company("domain:test.com")

        await manager.add_label(company.id, "sector", "exact", "alice", "fintech")
        await manager.add_label(company.id, "sector", "partial", "alice", "healthtech")

        labels = await manager.get_labels(company.id, predicate="sector")
        assert len(labels) == 1
        assert labels[0].label_type == "partial"
        assert labels[0].gold_value == "healthtech"
