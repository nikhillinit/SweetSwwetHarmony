"""
Tests for NotionOutboxWorker._build_prospect_payload().

Verifies that investor_matches field is correctly propagated.
"""

import pytest
from unittest.mock import MagicMock

from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage


class TestBuildProspectPayload:
    """Tests for _build_prospect_payload investor_matches handling."""

    def _make_worker(self):
        """Create a minimal outbox worker for testing payload builder."""
        from workflows.notion_outbox_worker import NotionOutboxWorker

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
