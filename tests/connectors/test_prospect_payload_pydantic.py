"""
Tests for ProspectPayload Pydantic v2 migration.

Covers:
1. Roundtrip: model_dump → model_validate preserves all fields
2. Extra fields: dropped silently, only keys logged (never values)
3. Missing optionals: defaults applied correctly
4. Pre-populated candidates: canonical_key_candidates not overwritten
5. Legacy outbox JSON compat: old serialization format still deserializes
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from connectors.notion_connector_v2 import (
    ProspectPayload,
    InvestmentStage,
)


class TestRoundtrip:
    """model_dump(mode='json') → model_validate preserves all fields."""

    def test_basic_roundtrip(self):
        original = ProspectPayload(
            discovery_id="disc-001",
            company_name="Acme Corp",
            canonical_key="domain:acme.com",
            stage=InvestmentStage.SEED,
            website="https://acme.com",
            confidence_score=0.75,
            signal_types=["funding", "launch"],
            why_now="Strong traction",
        )

        dumped = original.model_dump(mode="json")
        restored = ProspectPayload.model_validate(dumped)

        assert restored.discovery_id == original.discovery_id
        assert restored.company_name == original.company_name
        assert restored.canonical_key == original.canonical_key
        assert restored.stage == original.stage
        assert restored.website == original.website
        assert restored.confidence_score == original.confidence_score
        assert restored.signal_types == original.signal_types
        assert restored.why_now == original.why_now

    def test_roundtrip_with_all_fields(self):
        original = ProspectPayload(
            discovery_id="disc-002",
            company_name="Full Corp",
            canonical_key="domain:full.com",
            stage=InvestmentStage.SERIES_A,
            status="Source",
            website="https://full.com",
            canonical_key_candidates=["domain:full.com", "domain:full.io"],
            confidence_score=0.85,
            signal_types=["funding"],
            why_now="Series A round",
            short_description="A full company",
            sector="CPG",
            proposed_sector="CPG",
            taxonomy_status="Classified",
            founder_name="Jane Doe",
            founder_linkedin="https://linkedin.com/in/janedoe",
            location="San Francisco",
            target_raise="$5M",
            watchlists_matched=["cpg-watch"],
            external_refs={"domain": "full.com"},
            founding_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
            social_proof_score=42,
            investor_matches=[{"name": "VC Fund"}],
        )

        dumped = original.model_dump(mode="json")
        restored = ProspectPayload.model_validate(dumped)

        assert restored.founding_date is not None
        assert restored.social_proof_score == 42
        assert restored.investor_matches == [{"name": "VC Fund"}]
        assert restored.watchlists_matched == ["cpg-watch"]
        assert restored.canonical_key_candidates == ["domain:full.com", "domain:full.io"]

    def test_stage_enum_serializes_as_string(self):
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="k",
            stage=InvestmentStage.SEED,
        )
        dumped = payload.model_dump(mode="json")
        assert dumped["stage"] == "Seed"

    def test_stage_string_deserializes_to_enum(self):
        data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Seed",
        }
        payload = ProspectPayload.model_validate(data)
        assert payload.stage == InvestmentStage.SEED


class TestExtraFields:
    """Extra fields are ignored with keys-only logging."""

    def test_extra_fields_dropped(self):
        data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Pre-Seed",
            "unknown_field": "should be dropped",
            "secret_data": "should also be dropped",
        }
        payload = ProspectPayload.model_validate(data)
        assert not hasattr(payload, "unknown_field")
        assert not hasattr(payload, "secret_data")

    def test_extra_keys_logged_not_values(self, caplog):
        data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Pre-Seed",
            "secret_password": "hunter2",
        }
        with caplog.at_level(logging.DEBUG, logger="connectors.notion_connector_v2"):
            ProspectPayload.model_validate(data)

        # Key should be logged
        logged_text = " ".join(r.message for r in caplog.records)
        assert "secret_password" in logged_text

        # Value should NOT be logged
        assert "hunter2" not in logged_text


class TestMissingOptionals:
    """Missing optional fields get correct defaults."""

    def test_minimal_required_fields(self):
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="k",
            stage=InvestmentStage.PRE_SEED,
        )
        assert payload.status is None
        assert payload.website == ""
        assert payload.canonical_key_candidates == []
        assert payload.confidence_score == 0.0
        assert payload.signal_types == []
        assert payload.why_now == ""
        assert payload.investor_matches == []
        assert payload.external_refs == {}
        assert payload.founding_date is None
        assert payload.social_proof_score == 0

    def test_none_collections_normalized_to_empty(self):
        """None values for list/dict fields are normalized to empty."""
        data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Pre-Seed",
            "signal_types": None,
            "investor_matches": None,
            "external_refs": None,
            "canonical_key_candidates": None,
            "watchlists_matched": None,
        }
        payload = ProspectPayload.model_validate(data)
        assert payload.signal_types == []
        assert payload.investor_matches == []
        assert payload.external_refs == {}
        assert payload.canonical_key_candidates == []
        assert payload.watchlists_matched == []


class TestPrePopulatedCandidates:
    """Pre-populated canonical_key_candidates are not overwritten."""

    def test_provided_candidates_preserved(self):
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="domain:acme.com",
            stage=InvestmentStage.SEED,
            canonical_key_candidates=["domain:acme.com", "domain:acme.io"],
            external_refs={"domain": "different.com"},
        )
        # Should keep provided candidates, not regenerate from external_refs
        assert "domain:acme.com" in payload.canonical_key_candidates
        assert "domain:acme.io" in payload.canonical_key_candidates

    def test_candidates_generated_from_external_refs(self):
        payload = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="",
            stage=InvestmentStage.SEED,
            external_refs={"domain": "auto.com"},
        )
        # Should auto-generate candidates from external_refs
        assert len(payload.canonical_key_candidates) > 0


class TestLegacyOutboxCompat:
    """Old serialization format (without founding_date etc.) still deserializes."""

    def test_legacy_format_without_new_fields(self):
        """Simulate data from before Pydantic migration (missing founding_date, etc.)."""
        legacy_data = {
            "discovery_id": "disc-legacy",
            "company_name": "Legacy Corp",
            "canonical_key": "domain:legacy.com",
            "stage": "Seed",
            "status": "Source",
            "website": "https://legacy.com",
            "canonical_key_candidates": ["domain:legacy.com"],
            "confidence_score": 0.65,
            "signal_types": ["funding"],
            "why_now": "Old signal",
            "short_description": "A legacy company",
            "sector": None,
            "proposed_sector": None,
            "taxonomy_status": None,
            "founder_name": "",
            "founder_linkedin": "",
            "location": "",
            "target_raise": "",
            "external_refs": {},
            "watchlists_matched": [],
            "investor_matches": [],
            # Note: no founding_date, social_proof_score
        }
        payload = ProspectPayload.model_validate(legacy_data)
        assert payload.founding_date is None
        assert payload.social_proof_score == 0
        assert payload.company_name == "Legacy Corp"
        assert payload.stage == InvestmentStage.SEED

    def test_legacy_format_minimal(self):
        """Bare minimum from old outbox entries."""
        legacy_data = {
            "discovery_id": "d",
            "company_name": "c",
            "canonical_key": "k",
            "stage": "Pre-Seed",
        }
        payload = ProspectPayload.model_validate(legacy_data)
        assert payload.signal_types == []
        assert payload.investor_matches == []


class TestIdempotencyKey:
    """idempotency_key() method still works."""

    def test_idempotency_key_from_canonical_key(self):
        p = ProspectPayload(
            discovery_id="d",
            company_name="c",
            canonical_key="domain:acme.com",
            stage=InvestmentStage.SEED,
        )
        key = p.idempotency_key()
        assert len(key) == 16
        assert key.isalnum()

    def test_idempotency_key_deterministic(self):
        p1 = ProspectPayload(
            discovery_id="d1",
            company_name="c1",
            canonical_key="domain:acme.com",
            stage=InvestmentStage.SEED,
        )
        p2 = ProspectPayload(
            discovery_id="d2",
            company_name="c2",
            canonical_key="domain:acme.com",
            stage=InvestmentStage.PRE_SEED,
        )
        # Same canonical key → same idempotency key
        assert p1.idempotency_key() == p2.idempotency_key()


class TestModelConfig:
    """Model configuration is correct."""

    def test_extra_ignore(self):
        assert ProspectPayload.model_config.get("extra") == "ignore"
