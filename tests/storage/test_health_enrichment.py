"""Tests for health enrichment storage tables."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from storage.health_enrichment import (
    ClinicalTrial,
    FDAClearance,
    HealthEnrichmentStore,
    Publication,
)


class TestHealthEnrichmentStoreBasics:
    """Test basic HealthEnrichmentStore functionality."""

    def test_store_exists(self):
        """HealthEnrichmentStore should exist and be instantiable."""
        store = HealthEnrichmentStore(":memory:")
        assert store is not None
        assert store.db_path == ":memory:"

    def test_clinical_trial_dataclass(self):
        """ClinicalTrial should be a proper dataclass with required fields."""
        trial = ClinicalTrial(
            entity_id="entity-123",
            nct_id="NCT12345678",
            title="Phase 2 Study of Drug X",
            phase="Phase 2",
            status="Recruiting",
            enrollment=100,
            conditions=["Diabetes", "Obesity"],
            start_date=date(2025, 1, 15),
            completion_date=date(2026, 6, 30),
            fetched_at=datetime(2025, 1, 10, 12, 0, 0),
        )
        assert trial.entity_id == "entity-123"
        assert trial.nct_id == "NCT12345678"
        assert trial.title == "Phase 2 Study of Drug X"
        assert trial.phase == "Phase 2"
        assert trial.status == "Recruiting"
        assert trial.enrollment == 100
        assert trial.conditions == ["Diabetes", "Obesity"]
        assert trial.start_date == date(2025, 1, 15)
        assert trial.completion_date == date(2026, 6, 30)
        assert trial.fetched_at == datetime(2025, 1, 10, 12, 0, 0)
        assert trial.id is None

    def test_fda_clearance_dataclass(self):
        """FDAClearance should be a proper dataclass with required fields."""
        clearance = FDAClearance(
            entity_id="entity-456",
            application_number="K123456",
            device_name="Smart Glucose Monitor",
            device_class="II",
            clearance_type="510k",
            decision="Substantially Equivalent",
            decision_date=date(2024, 11, 20),
            fetched_at=datetime(2025, 1, 10, 12, 0, 0),
        )
        assert clearance.entity_id == "entity-456"
        assert clearance.application_number == "K123456"
        assert clearance.device_name == "Smart Glucose Monitor"
        assert clearance.device_class == "II"
        assert clearance.clearance_type == "510k"
        assert clearance.decision == "Substantially Equivalent"
        assert clearance.decision_date == date(2024, 11, 20)
        assert clearance.fetched_at == datetime(2025, 1, 10, 12, 0, 0)
        assert clearance.id is None

    def test_publication_dataclass(self):
        """Publication should be a proper dataclass with required fields."""
        pub = Publication(
            entity_id="entity-789",
            pmid="12345678",
            title="Novel AI Approach for Drug Discovery",
            authors="Smith J, Jones A, Brown B",
            journal="Nature Medicine",
            pub_date=date(2024, 8, 15),
            citation_count=42,
            fetched_at=datetime(2025, 1, 10, 12, 0, 0),
        )
        assert pub.entity_id == "entity-789"
        assert pub.pmid == "12345678"
        assert pub.title == "Novel AI Approach for Drug Discovery"
        assert pub.authors == "Smith J, Jones A, Brown B"
        assert pub.journal == "Nature Medicine"
        assert pub.pub_date == date(2024, 8, 15)
        assert pub.citation_count == 42
        assert pub.fetched_at == datetime(2025, 1, 10, 12, 0, 0)
        assert pub.id is None


class TestHealthEnrichmentStoreInit:
    """Test HealthEnrichmentStore initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self):
        """initialize() should create all required tables and indexes."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        # Check tables exist by querying sqlite_master
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]

        assert "health_clinical_trials" in tables
        assert "health_fda_clearances" in tables
        assert "health_publications" in tables

        # Check indexes exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        indexes = [row[0] for row in await cursor.fetchall()]

        assert "idx_trials_entity" in indexes
        assert "idx_fda_entity" in indexes
        assert "idx_pubs_entity" in indexes

        await store.close()

    @pytest.mark.asyncio
    async def test_save_and_get_trial(self):
        """Should save and retrieve clinical trials."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        trial = ClinicalTrial(
            entity_id="entity-123",
            nct_id="NCT12345678",
            title="Phase 2 Study of Drug X",
            phase="Phase 2",
            status="Recruiting",
            enrollment=100,
            conditions=["Diabetes", "Obesity"],
            start_date=date(2025, 1, 15),
            completion_date=date(2026, 6, 30),
            fetched_at=datetime(2025, 1, 10, 12, 0, 0),
        )

        trial_id = await store.save_clinical_trial(trial)
        assert trial_id is not None
        assert trial_id > 0

        # Retrieve trials for entity
        trials = await store.get_trials_for_entity("entity-123")
        assert len(trials) == 1

        retrieved = trials[0]
        assert retrieved.id == trial_id
        assert retrieved.entity_id == "entity-123"
        assert retrieved.nct_id == "NCT12345678"
        assert retrieved.title == "Phase 2 Study of Drug X"
        assert retrieved.phase == "Phase 2"
        assert retrieved.status == "Recruiting"
        assert retrieved.enrollment == 100
        assert retrieved.conditions == ["Diabetes", "Obesity"]
        assert retrieved.start_date == date(2025, 1, 15)
        assert retrieved.completion_date == date(2026, 6, 30)
        assert retrieved.fetched_at == datetime(2025, 1, 10, 12, 0, 0)

        await store.close()


class TestFDAClearanceStorage:
    """Test FDA clearance storage operations."""

    @pytest.mark.asyncio
    async def test_save_and_get_fda_clearance(self):
        """Should save and retrieve FDA clearances."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        clearance = FDAClearance(
            entity_id="entity-456",
            application_number="K123456",
            device_name="Smart Glucose Monitor",
            device_class="II",
            clearance_type="510k",
            decision="Substantially Equivalent",
            decision_date=date(2024, 11, 20),
            fetched_at=datetime(2025, 1, 10, 12, 0, 0),
        )

        clearance_id = await store.save_fda_clearance(clearance)
        assert clearance_id is not None
        assert clearance_id > 0

        # Retrieve clearances for entity
        clearances = await store.get_fda_clearances_for_entity("entity-456")
        assert len(clearances) == 1

        retrieved = clearances[0]
        assert retrieved.id == clearance_id
        assert retrieved.entity_id == "entity-456"
        assert retrieved.application_number == "K123456"
        assert retrieved.device_name == "Smart Glucose Monitor"
        assert retrieved.device_class == "II"
        assert retrieved.clearance_type == "510k"
        assert retrieved.decision == "Substantially Equivalent"
        assert retrieved.decision_date == date(2024, 11, 20)
        assert retrieved.fetched_at == datetime(2025, 1, 10, 12, 0, 0)

        await store.close()


class TestPublicationStorage:
    """Test publication storage operations."""

    @pytest.mark.asyncio
    async def test_save_and_get_publication(self):
        """Should save and retrieve publications."""
        store = HealthEnrichmentStore(":memory:")
        await store.initialize()

        pub = Publication(
            entity_id="entity-789",
            pmid="12345678",
            title="Novel AI Approach for Drug Discovery",
            authors="Smith J, Jones A, Brown B",
            journal="Nature Medicine",
            pub_date=date(2024, 8, 15),
            citation_count=42,
            fetched_at=datetime(2025, 1, 10, 12, 0, 0),
        )

        pub_id = await store.save_publication(pub)
        assert pub_id is not None
        assert pub_id > 0

        # Retrieve publications for entity
        publications = await store.get_publications_for_entity("entity-789")
        assert len(publications) == 1

        retrieved = publications[0]
        assert retrieved.id == pub_id
        assert retrieved.entity_id == "entity-789"
        assert retrieved.pmid == "12345678"
        assert retrieved.title == "Novel AI Approach for Drug Discovery"
        assert retrieved.authors == "Smith J, Jones A, Brown B"
        assert retrieved.journal == "Nature Medicine"
        assert retrieved.pub_date == date(2024, 8, 15)
        assert retrieved.citation_count == 42
        assert retrieved.fetched_at == datetime(2025, 1, 10, 12, 0, 0)

        await store.close()
