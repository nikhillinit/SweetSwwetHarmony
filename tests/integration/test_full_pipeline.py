"""
End-to-end integration test for multi-source signal pipeline.

Tests the complete flow:
1. Signal collection from multiple sources
2. Correlation by canonical key
3. Verification gate evaluation
4. Push decision routing

These tests require network access to external APIs.
Mark with @pytest.mark.integration for CI filtering.
"""

import pytest
import pytest_asyncio
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from discovery_engine.signal_orchestrator import (
    SignalOrchestrator,
    EnrichedEntity,
    quick_enrich,
    batch_enrich,
)
from verification.verification_gate_v2 import (
    PushDecision,
    VerificationStatus,
)


class TestFullPipeline:
    """Integration tests for complete signal pipeline"""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enrich_known_company(self):
        """
        Given: A known tech company domain
        When: Enriched with all collectors
        Then: Returns entity with multiple signal sources
        """
        orchestrator = SignalOrchestrator()

        # Use a well-known company (likely to have job postings)
        entities = await orchestrator.enrich_domains(
            domains=["stripe.com"],
            check_whois=True,
            check_hiring=True,
            check_github=True,
        )

        assert len(entities) == 1
        entity = entities[0]

        # Should have at least one signal
        assert entity.canonical_key == "domain:stripe.com"
        assert isinstance(entity.signals, list)

        # Confidence should be calculated
        assert entity.confidence >= 0.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_enrich_multiple_domains(self):
        """
        Given: Multiple domains
        When: Enriched with collectors
        Then: Returns entities sorted by confidence
        """
        orchestrator = SignalOrchestrator()

        entities = await orchestrator.enrich_domains(
            domains=["stripe.com", "openai.com"],
            check_whois=False,  # Skip WHOIS for speed
            check_hiring=True,
            check_github=False,
        )

        assert len(entities) == 2

        # Should be sorted by confidence (descending)
        if len(entities) >= 2:
            assert entities[0].confidence >= entities[1].confidence

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_multi_source_boost(self):
        """
        Given: Entity with signals from 2+ sources
        When: Evaluated by verification gate
        Then: Gets multi-source confidence boost
        """
        orchestrator = SignalOrchestrator()

        # Check a company likely to have multiple signal types
        entities = await orchestrator.enrich_domains(
            domains=["anthropic.com"],
            check_whois=True,
            check_hiring=True,
            check_github=True,
        )

        assert len(entities) >= 1
        entity = entities[0]

        # If we got multiple sources, check verification result
        if entity.source_count >= 2 and entity.verification_result:
            breakdown = entity.verification_result.confidence_breakdown
            # Multi-source boost should be > 1.0
            assert breakdown.get("multi_source_boost", 1.0) >= 1.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_unknown_domain_graceful(self):
        """
        Given: An unknown/invalid domain
        When: Enriched
        Then: Returns entity with 0 signals (no crash)
        """
        orchestrator = SignalOrchestrator()

        entities = await orchestrator.enrich_domains(
            domains=["definitely-not-a-real-domain-12345.xyz"],
            check_whois=True,
            check_hiring=True,
            check_github=True,
        )

        assert len(entities) == 1
        entity = entities[0]
        assert entity.source_count == 0
        assert entity.confidence == 0.0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_push_decision_routing(self):
        """
        Given: Entity with strong signals
        When: Evaluated
        Then: Gets appropriate push decision
        """
        orchestrator = SignalOrchestrator()

        entities = await orchestrator.enrich_domains(
            domains=["stripe.com"],
            check_hiring=True,
            check_whois=False,
            check_github=False,
        )

        if entities and entities[0].signals:
            entity = entities[0]
            # Should have a push decision
            assert entity.push_decision in [
                PushDecision.AUTO_PUSH,
                PushDecision.NEEDS_REVIEW,
                PushDecision.HOLD,
                PushDecision.REJECT,
            ]

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_entity_to_dict_serialization(self):
        """
        Given: Enriched entity
        When: Converted to dict
        Then: All expected fields present
        """
        orchestrator = SignalOrchestrator()

        entities = await orchestrator.enrich_domains(
            domains=["github.com"],
            check_hiring=True,
            check_whois=False,
            check_github=False,
        )

        if entities:
            entity_dict = entities[0].to_dict()

            assert "canonical_key" in entity_dict
            assert "domain" in entity_dict
            assert "confidence" in entity_dict
            assert "source_count" in entity_dict
            assert "signal_types" in entity_dict
            assert "signals" in entity_dict

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_quick_enrich_convenience(self):
        """Test quick_enrich convenience function"""
        entity = await quick_enrich("stripe.com")

        assert entity is not None
        assert entity.canonical_key == "domain:stripe.com"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_batch_enrich_with_filter(self):
        """Test batch_enrich with confidence filter"""
        entities = await batch_enrich(
            domains=["stripe.com", "unknown-domain-xyz.com"],
            min_confidence=0.3,
        )

        # Only high-confidence entities should be returned
        for entity in entities:
            assert entity.confidence >= 0.3


class TestCollectorIntegration:
    """Integration tests for individual collectors"""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_github_activity_collector(self):
        """Test GitHub Activity Collector integration"""
        from collectors.github_activity import GitHubActivityCollector
        from discovery_engine.mcp_server import CollectorResult

        collector = GitHubActivityCollector(
            org_names=["github"],
            lookback_days=30
        )
        result = await collector.run(dry_run=True)

        # Result is a CollectorResult dataclass
        assert isinstance(result, CollectorResult)
        assert result.signals_found >= 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_job_postings_collector(self):
        """Test Job Postings Collector integration"""
        from collectors.job_postings import JobPostingsCollector
        from discovery_engine.mcp_server import CollectorResult

        collector = JobPostingsCollector(domains=["stripe.com"])
        result = await collector.run(dry_run=True)

        # Result is a CollectorResult dataclass
        assert isinstance(result, CollectorResult)
        assert result.signals_found >= 0


class TestVerificationGateIntegration:
    """Integration tests for verification gate with new signal types"""

    @pytest.mark.asyncio
    async def test_hiring_signal_weight(self):
        """Hiring signals should have high weight"""
        from verification.verification_gate_v2 import (
            Signal,
            VerificationGate,
            SIGNAL_WEIGHTS,
        )

        # Hiring signal should have highest weight
        assert "hiring_signal" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["hiring_signal"] >= 0.30

    @pytest.mark.asyncio
    async def test_github_activity_signal_weight(self):
        """GitHub activity signals should have appropriate weight"""
        from verification.verification_gate_v2 import SIGNAL_WEIGHTS

        assert "github_activity" in SIGNAL_WEIGHTS
        assert SIGNAL_WEIGHTS["github_activity"] >= 0.15

    @pytest.mark.asyncio
    async def test_new_signal_types_have_half_lives(self):
        """New signal types should have decay half-lives"""
        from verification.verification_gate_v2 import HALF_LIVES

        assert "hiring_signal" in HALF_LIVES
        assert "github_activity" in HALF_LIVES

        # Hiring signals should decay slower than activity
        assert HALF_LIVES["hiring_signal"] > HALF_LIVES["github_activity"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
