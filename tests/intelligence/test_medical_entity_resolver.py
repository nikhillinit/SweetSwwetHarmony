"""Tests for medical entity resolver with SciSpacy integration."""
import pytest
from intelligence.medical_entity_resolver import (
    MedicalEntity,
    MedicalEntityResolver,
    ResolvedHealthEntity,
)


class TestMedicalEntityResolverBasics:
    """Test basic MedicalEntityResolver functionality."""

    def test_resolver_exists(self):
        """MedicalEntityResolver should exist and be instantiable without loading model."""
        resolver = MedicalEntityResolver(load_model=False)
        assert resolver is not None

    def test_medical_entity_dataclass(self):
        """MedicalEntity should be a proper dataclass with required fields."""
        entity = MedicalEntity(
            text="diabetes",
            label="DISEASE",
            cui="C0011849",
            confidence=0.95
        )
        assert entity.text == "diabetes"
        assert entity.label == "DISEASE"
        assert entity.cui == "C0011849"
        assert entity.confidence == 0.95

    def test_medical_entity_optional_cui(self):
        """MedicalEntity should allow None for cui."""
        entity = MedicalEntity(
            text="some treatment",
            label="TREATMENT",
            cui=None,
            confidence=0.8
        )
        assert entity.cui is None


class TestCompanyNameNormalization:
    """Test company name normalization functionality."""

    def test_removes_inc_suffix(self):
        """_normalize_company_name should remove Inc suffix."""
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver._normalize_company_name("Acme Health Inc")
        assert "inc" not in result.lower()

    def test_removes_llc_suffix(self):
        """_normalize_company_name should remove LLC suffix."""
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver._normalize_company_name("Acme Health LLC")
        assert "llc" not in result.lower()

    def test_removes_corp_suffix(self):
        """_normalize_company_name should remove Corp suffix."""
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver._normalize_company_name("Acme Health Corp")
        assert "corp" not in result.lower()

    def test_lowercases_name(self):
        """_normalize_company_name should lowercase the name."""
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver._normalize_company_name("Acme HEALTH")
        assert result == result.lower()

    def test_matching_normalized_names(self):
        """Different variations of same company should normalize to same value."""
        resolver = MedicalEntityResolver(load_model=False)
        name1 = resolver._normalize_company_name("Acme Health Inc")
        name2 = resolver._normalize_company_name("Acme Health LLC")
        name3 = resolver._normalize_company_name("acme health")
        assert name1 == name2 == name3


class TestEntityIdGeneration:
    """Test entity ID generation functionality."""

    def test_generates_entity_id(self):
        """_generate_entity_id should return a non-empty string ID."""
        resolver = MedicalEntityResolver(load_model=False)
        entity_id = resolver._generate_entity_id("Acme Health")
        assert isinstance(entity_id, str)
        assert len(entity_id) > 0

    def test_same_normalized_name_same_id(self):
        """Same normalized name should produce same entity ID."""
        resolver = MedicalEntityResolver(load_model=False)
        id1 = resolver._generate_entity_id("Acme Health Inc")
        id2 = resolver._generate_entity_id("Acme Health LLC")
        id3 = resolver._generate_entity_id("acme health")
        assert id1 == id2 == id3


class TestResolvedHealthEntity:
    """Test resolve method and ResolvedHealthEntity dataclass."""

    def test_resolve_returns_entity(self):
        """resolve should return a ResolvedHealthEntity."""
        resolver = MedicalEntityResolver(load_model=False)
        result = resolver.resolve(
            content="FDA-cleared wearable for heart monitoring",
            company_name="HeartWatch Inc"
        )
        assert isinstance(result, ResolvedHealthEntity)
        assert result.company_name == "HeartWatch Inc"
        assert isinstance(result.entity_id, str)
        assert len(result.entity_id) > 0
        assert isinstance(result.normalized_name, str)
        assert isinstance(result.medical_concepts, list)
        assert isinstance(result.medical_entities, list)

    def test_uses_existing_entity_id(self):
        """resolve should use existing_entity_id when provided."""
        resolver = MedicalEntityResolver(load_model=False)
        existing_id = "existing-entity-123"
        result = resolver.resolve(
            content="Some health content",
            company_name="TestCo",
            existing_entity_id=existing_id
        )
        assert result.entity_id == existing_id
