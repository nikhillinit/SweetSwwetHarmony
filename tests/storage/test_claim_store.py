"""
Tests for the Claim Store (KG-Lite in SQLite).

Tests cover:
- Predicate management
- Extraction storage
- Claim storage with evidence
- Current claims view
- Conflict detection
- Explanation generation
"""

import pytest
import asyncio
import os
from datetime import datetime, timezone

from storage.signal_store import SignalStore
from storage.claim_store import (
    ClaimStore,
    Claim,
    ClaimExtraction,
    ClaimWithEvidence,
    Predicate,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
async def store():
    """Create a test signal store with claim ledger."""
    db_path = "test_claim_store.db"
    signal_store = SignalStore(db_path)
    await signal_store.initialize()
    yield signal_store
    await signal_store.close()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
async def claim_store(store):
    """Create a claim store wrapper."""
    return ClaimStore(store)


# =============================================================================
# PREDICATE TESTS
# =============================================================================

class TestPredicates:
    """Tests for predicate management."""

    async def test_predicates_seeded(self, claim_store):
        """Initial predicates should be seeded."""
        predicates = await claim_store.get_all_predicates()
        assert len(predicates) >= 10
        names = [p.name for p in predicates]
        assert "problem_solved" in names
        assert "target_customer" in names
        assert "business_model" in names

    async def test_get_predicate(self, claim_store):
        """Can get a specific predicate."""
        pred = await claim_store.get_predicate("target_customer")
        assert pred is not None
        assert pred.name == "target_customer"
        assert pred.display_name == "Target Customer"
        assert pred.data_type == "text"

    async def test_get_predicate_not_found(self, claim_store):
        """Returns None for unknown predicate."""
        pred = await claim_store.get_predicate("nonexistent")
        assert pred is None

    async def test_add_custom_predicate(self, claim_store):
        """Can add custom predicates."""
        await claim_store.add_predicate(
            name="custom_metric",
            display_name="Custom Metric",
            data_type="numeric",
            units="users",
            description="A custom metric for testing",
        )

        pred = await claim_store.get_predicate("custom_metric")
        assert pred is not None
        assert pred.display_name == "Custom Metric"
        assert pred.data_type == "numeric"
        assert pred.units == "users"


# =============================================================================
# EXTRACTION TESTS
# =============================================================================

class TestExtractions:
    """Tests for extraction storage."""

    async def test_save_extraction(self, claim_store):
        """Can save an extraction."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:test.com",
            extractor_name="test_extractor",
            raw_text="We serve enterprise customers",
            predicate_hint="target_customer",
            source_snippet="We serve enterprise customers...",
            source_url="https://test.com/about",
        )
        assert ext_id > 0

    async def test_get_extraction(self, claim_store):
        """Can retrieve an extraction by ID."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:test.com",
            extractor_name="test_extractor",
            raw_text="B2B SaaS companies",
        )

        ext = await claim_store.get_extraction(ext_id)
        assert ext is not None
        assert ext.id == ext_id
        assert ext.entity_key == "domain:test.com"
        assert ext.raw_text == "B2B SaaS companies"

    async def test_get_extractions_for_entity(self, claim_store):
        """Can get all extractions for an entity."""
        # Save multiple extractions
        await claim_store.save_extraction(
            entity_key="domain:multi.com",
            extractor_name="extractor1",
            raw_text="Text 1",
        )
        await claim_store.save_extraction(
            entity_key="domain:multi.com",
            extractor_name="extractor2",
            raw_text="Text 2",
        )

        extractions = await claim_store.get_extractions_for_entity("domain:multi.com")
        assert len(extractions) == 2

    async def test_extraction_with_offsets(self, claim_store):
        """Extractions can include character offsets."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:test.com",
            extractor_name="test_extractor",
            raw_text="enterprise SaaS",
            source_snippet="We focus on enterprise SaaS customers",
            start_offset=12,
            end_offset=27,
        )

        ext = await claim_store.get_extraction(ext_id)
        assert ext.start_offset == 12
        assert ext.end_offset == 27


# =============================================================================
# CLAIM TESTS
# =============================================================================

class TestClaims:
    """Tests for claim storage."""

    async def test_save_claim(self, claim_store):
        """Can save a claim."""
        claim_id = await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="Enterprise SaaS",
            confidence=0.8,
        )
        assert claim_id > 0

    async def test_get_claim(self, claim_store):
        """Can retrieve a claim by ID."""
        claim_id = await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="SMBs",
            confidence=0.7,
        )

        claim = await claim_store.get_claim(claim_id)
        assert claim is not None
        assert claim.entity_key == "domain:test.com"
        assert claim.predicate == "target_customer"
        assert claim.value == "SMBs"
        assert claim.confidence == 0.7
        assert claim.status == "active"

    async def test_save_claim_with_evidence(self, claim_store):
        """Claims can link to extractions."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:test.com",
            extractor_name="test",
            raw_text="Evidence text",
        )

        claim_id = await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="Test value",
            confidence=0.8,
            extraction_ids=[ext_id],
        )

        evidence = await claim_store.get_evidence_for_claim(claim_id)
        assert len(evidence) == 1
        assert evidence[0].id == ext_id

    async def test_claim_upsert_increases_confidence(self, claim_store):
        """Saving same claim again increases confidence if higher."""
        # First save with low confidence
        await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="Same value",
            confidence=0.5,
        )

        # Save again with higher confidence
        claim_id = await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="Same value",
            confidence=0.9,
        )

        claim = await claim_store.get_claim(claim_id)
        assert claim.confidence == 0.9  # Should take higher

    async def test_get_claims_for_entity(self, claim_store):
        """Can get all claims for an entity."""
        await claim_store.save_claim(
            entity_key="domain:multi.com",
            predicate="target_customer",
            value="Value 1",
            confidence=0.8,
        )
        await claim_store.save_claim(
            entity_key="domain:multi.com",
            predicate="business_model",
            value="SaaS",
            confidence=0.7,
        )

        claims = await claim_store.get_claims_for_entity("domain:multi.com")
        assert len(claims) == 2
        predicates = [c.predicate for c in claims]
        assert "target_customer" in predicates
        assert "business_model" in predicates


# =============================================================================
# CURRENT CLAIMS VIEW TESTS
# =============================================================================

class TestCurrentClaims:
    """Tests for the current_claims view."""

    async def test_current_claims_returns_highest_confidence(self, claim_store):
        """Current claims view returns only the highest confidence claim."""
        # Add two claims for same entity/predicate
        await claim_store.save_claim(
            entity_key="domain:conflict.com",
            predicate="target_customer",
            value="Low confidence answer",
            confidence=0.4,
        )
        await claim_store.save_claim(
            entity_key="domain:conflict.com",
            predicate="target_customer",
            value="High confidence answer",
            confidence=0.9,
        )

        current = await claim_store.get_current_claims(entity_key="domain:conflict.com")
        # Should only return one claim per predicate
        target_claims = [c for c in current if c.predicate == "target_customer"]
        assert len(target_claims) == 1
        assert target_claims[0].value == "High confidence answer"
        assert target_claims[0].confidence == 0.9

    async def test_current_claims_filter_by_predicate(self, claim_store):
        """Can filter current claims by predicate."""
        await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="Customer",
            confidence=0.8,
        )
        await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="business_model",
            value="SaaS",
            confidence=0.8,
        )

        current = await claim_store.get_current_claims(
            entity_key="domain:test.com",
            predicate="target_customer",
        )
        assert len(current) == 1
        assert current[0].predicate == "target_customer"


# =============================================================================
# CONFLICT DETECTION TESTS
# =============================================================================

class TestConflicts:
    """Tests for conflict detection."""

    async def test_detect_conflicts(self, claim_store):
        """Can detect conflicting claims."""
        await claim_store.save_claim(
            entity_key="domain:conflict.com",
            predicate="target_customer",
            value="Answer A",
            confidence=0.5,
        )
        await claim_store.save_claim(
            entity_key="domain:conflict.com",
            predicate="target_customer",
            value="Answer B",
            confidence=0.5,
        )

        conflicts = await claim_store.get_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["entity_key"] == "domain:conflict.com"
        assert conflicts[0]["predicate"] == "target_customer"
        assert conflicts[0]["claim_count"] == 2

    async def test_no_conflicts_single_claim(self, claim_store):
        """No conflicts when only one claim exists."""
        await claim_store.save_claim(
            entity_key="domain:single.com",
            predicate="target_customer",
            value="Only answer",
            confidence=0.8,
        )

        conflicts = await claim_store.get_conflicts()
        assert len(conflicts) == 0

    async def test_mark_conflict(self, claim_store):
        """Can mark a claim as conflicting."""
        claim_id = await claim_store.save_claim(
            entity_key="domain:test.com",
            predicate="target_customer",
            value="Test",
            confidence=0.5,
        )

        await claim_store.mark_conflict(claim_id, "Test conflict")

        claim = await claim_store.get_claim(claim_id)
        assert claim.status == "conflicting"
        assert claim.status_reason == "Test conflict"


# =============================================================================
# EXPLANATION TESTS
# =============================================================================

class TestExplanation:
    """Tests for claim explanation."""

    async def test_explain_claim(self, claim_store):
        """Can explain why a claim is believed."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:explain.com",
            extractor_name="test",
            raw_text="We sell to developers",
            source_snippet="We sell to developers and tech teams",
            source_url="https://explain.com/about",
        )

        await claim_store.save_claim(
            entity_key="domain:explain.com",
            predicate="target_customer",
            value="Developers",
            confidence=0.85,
            extraction_ids=[ext_id],
        )

        explanation = await claim_store.explain_claim(
            "domain:explain.com",
            "target_customer",
        )

        assert explanation is not None
        assert "target_customer" in explanation
        assert "Developers" in explanation
        assert "85%" in explanation
        assert "We sell to developers" in explanation
        assert "https://explain.com/about" in explanation

    async def test_explain_claim_not_found(self, claim_store):
        """Returns None if no claim exists."""
        explanation = await claim_store.explain_claim(
            "domain:nonexistent.com",
            "target_customer",
        )
        assert explanation is None


# =============================================================================
# STATS TESTS
# =============================================================================

class TestStats:
    """Tests for claim store statistics."""

    async def test_get_stats(self, claim_store):
        """Can get claim ledger statistics."""
        # Add some data
        await claim_store.save_claim(
            entity_key="domain:stats.com",
            predicate="target_customer",
            value="Test",
            confidence=0.8,
        )

        stats = await claim_store.get_stats()

        assert "claims_by_status" in stats
        assert "total_extractions" in stats
        assert "unique_entities" in stats
        assert "claims_by_predicate" in stats
        assert "conflict_count" in stats

        assert stats["claims_by_status"].get("active", 0) >= 1


# =============================================================================
# CLAIM WITH EVIDENCE TESTS
# =============================================================================

class TestClaimWithEvidence:
    """Tests for ClaimWithEvidence dataclass."""

    async def test_get_claim_with_evidence(self, claim_store):
        """Can get a claim with its evidence."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:full.com",
            extractor_name="test",
            raw_text="Evidence",
            source_url="https://full.com",
        )

        claim_id = await claim_store.save_claim(
            entity_key="domain:full.com",
            predicate="target_customer",
            value="Full test",
            confidence=0.9,
            extraction_ids=[ext_id],
        )

        result = await claim_store.get_claim_with_evidence(claim_id)

        assert result is not None
        assert isinstance(result, ClaimWithEvidence)
        assert result.claim.value == "Full test"
        assert len(result.extractions) == 1
        assert result.extractions[0].raw_text == "Evidence"

    async def test_claim_with_evidence_explain(self, claim_store):
        """ClaimWithEvidence.explain() works correctly."""
        ext_id = await claim_store.save_extraction(
            entity_key="domain:explain2.com",
            extractor_name="test",
            raw_text="Some evidence",
            source_snippet="Some evidence here",
            source_url="https://explain2.com",
        )

        claim_id = await claim_store.save_claim(
            entity_key="domain:explain2.com",
            predicate="business_model",
            value="SaaS",
            confidence=0.75,
            extraction_ids=[ext_id],
        )

        result = await claim_store.get_claim_with_evidence(claim_id)
        explanation = result.explain()

        assert "business_model = SaaS" in explanation
        assert "75%" in explanation
        assert "Some evidence here" in explanation
