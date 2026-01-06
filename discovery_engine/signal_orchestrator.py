"""
Signal Orchestrator - Multi-source correlation engine

when_to_use: When you need to enrich a domain or entity with signals from
  multiple sources and get a unified confidence score.

Core pattern:
1. Accept input (domains, usernames)
2. Run collectors in parallel
3. Correlate by canonical key
4. Route through verification gate

This orchestrator coordinates:
- GitHub Activity Collector (repo creation, activity spikes)
- Job Postings Collector (Greenhouse, Lever)
- Domain WHOIS Collector (registration freshness)

Usage:
    orchestrator = SignalOrchestrator()
    entities = await orchestrator.enrich_domains(
        domains=["anthropic.com", "stripe.com"],
        check_whois=True,
        check_hiring=True,
        check_github=True,
    )

    for entity in entities:
        print(f"{entity.canonical_key}: {entity.confidence:.2f}")
        print(f"  Sources: {entity.source_count}")
        print(f"  Signal types: {entity.signal_types}")
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from verification.verification_gate_v2 import (
    Signal,
    VerificationGate,
    VerificationResult,
    PushDecision,
)

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class EnrichedEntity:
    """Entity with correlated multi-source signals"""
    canonical_key: str
    domain: Optional[str] = None
    company_name: Optional[str] = None
    signals: List[Signal] = field(default_factory=list)
    verification_result: Optional[VerificationResult] = None

    @property
    def source_count(self) -> int:
        """Number of distinct data sources"""
        return len({s.source_api for s in self.signals})

    @property
    def signal_types(self) -> Set[str]:
        """Set of unique signal types"""
        return {s.signal_type for s in self.signals}

    @property
    def confidence(self) -> float:
        """Overall confidence score from verification gate"""
        if self.verification_result:
            return self.verification_result.confidence_score
        return 0.0

    @property
    def push_decision(self) -> Optional[PushDecision]:
        """Push decision from verification gate"""
        if self.verification_result:
            return self.verification_result.decision
        return None

    @property
    def suggested_status(self) -> str:
        """Suggested Notion status"""
        if self.verification_result:
            return self.verification_result.suggested_status
        return ""

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            "canonical_key": self.canonical_key,
            "domain": self.domain,
            "company_name": self.company_name,
            "source_count": self.source_count,
            "signal_types": list(self.signal_types),
            "confidence": round(self.confidence, 3),
            "push_decision": self.push_decision.value if self.push_decision else None,
            "suggested_status": self.suggested_status,
            "signals": [
                {
                    "id": s.id,
                    "type": s.signal_type,
                    "source": s.source_api,
                    "confidence": round(s.confidence, 3),
                }
                for s in self.signals
            ],
        }


# =============================================================================
# SIGNAL ORCHESTRATOR
# =============================================================================

class SignalOrchestrator:
    """
    Coordinate multi-source signal collection and correlation.

    Runs collectors in parallel, correlates signals by canonical key,
    and routes through the verification gate.

    Usage:
        orchestrator = SignalOrchestrator()
        entities = await orchestrator.enrich_domains(["anthropic.com"])

        for entity in entities:
            if entity.push_decision == PushDecision.AUTO_PUSH:
                print(f"High confidence: {entity.domain}")
    """

    def __init__(
        self,
        github_token: Optional[str] = None,
        strict_mode: bool = False,
    ):
        """
        Args:
            github_token: GitHub API token for activity collector
            strict_mode: If True, require 2+ sources for any push
        """
        self.github_token = github_token or os.environ.get("GITHUB_TOKEN")
        self.gate = VerificationGate(strict_mode=strict_mode)

    async def enrich_domains(
        self,
        domains: List[str],
        check_whois: bool = True,
        check_hiring: bool = True,
        check_github: bool = True,
    ) -> List[EnrichedEntity]:
        """
        Enrich domains with multi-source signals.

        Runs collectors in parallel, correlates by canonical key,
        and returns entities sorted by confidence (highest first).

        Args:
            domains: List of domains to enrich
            check_whois: Run WHOIS/RDAP collector
            check_hiring: Run job postings collector
            check_github: Run GitHub activity collector

        Returns:
            List of EnrichedEntity sorted by confidence (descending)
        """
        # Initialize entities
        entities: Dict[str, EnrichedEntity] = {}
        for domain in domains:
            clean = domain.lower().replace("www.", "").strip()
            if not clean:
                continue
            key = f"domain:{clean}"
            entities[key] = EnrichedEntity(
                canonical_key=key,
                domain=clean,
            )

        # Collect tasks
        tasks = []
        if check_whois:
            tasks.append(self._collect_whois(domains))
        if check_hiring:
            tasks.append(self._collect_hiring(domains))
        if check_github:
            tasks.append(self._collect_github(domains))

        if not tasks:
            return list(entities.values())

        # Run collectors in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Correlate signals to entities
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Collector error: {result}")
                continue

            if not isinstance(result, list):
                continue

            for signal in result:
                if not isinstance(signal, Signal):
                    continue

                # Get canonical key from signal
                key = signal.raw_data.get("canonical_key", "")
                if key in entities:
                    entities[key].signals.append(signal)

                    # Extract company name if available
                    if not entities[key].company_name:
                        entities[key].company_name = signal.raw_data.get(
                            "company_name"
                        )

        # Evaluate through verification gate
        for entity in entities.values():
            if entity.signals:
                entity.verification_result = self.gate.evaluate(entity.signals)

        # Sort by confidence (highest first)
        sorted_entities = sorted(
            entities.values(),
            key=lambda e: e.confidence,
            reverse=True
        )

        return sorted_entities

    async def enrich_users(
        self,
        usernames: List[str],
        check_github: bool = True,
    ) -> List[EnrichedEntity]:
        """
        Enrich GitHub users with activity signals.

        Useful for tracking known founder accounts.

        Args:
            usernames: List of GitHub usernames
            check_github: Run GitHub activity collector

        Returns:
            List of EnrichedEntity for each user
        """
        entities: Dict[str, EnrichedEntity] = {}
        for username in usernames:
            clean = username.lower().strip()
            if not clean:
                continue
            key = f"github_user:{clean}"
            entities[key] = EnrichedEntity(
                canonical_key=key,
                company_name=clean,
            )

        if check_github:
            try:
                from collectors.github_activity import GitHubActivityCollector

                collector = GitHubActivityCollector(
                    github_token=self.github_token,
                )
                result = await collector.run(
                    usernames=usernames,
                    dry_run=True,
                )

                for signal in result.get("signals", []):
                    key = signal.raw_data.get("canonical_key", "")
                    if key in entities:
                        entities[key].signals.append(signal)

            except Exception as e:
                logger.error(f"GitHub activity collection failed: {e}")

        # Evaluate through verification gate
        for entity in entities.values():
            if entity.signals:
                entity.verification_result = self.gate.evaluate(entity.signals)

        return sorted(
            entities.values(),
            key=lambda e: e.confidence,
            reverse=True
        )

    async def _collect_whois(self, domains: List[str]) -> List[Signal]:
        """Run WHOIS/RDAP collector"""
        try:
            from collectors.domain_whois import DomainWhoisCollector

            collector = DomainWhoisCollector()
            result = await collector.run(domains=domains, dry_run=True)

            # The collector returns CollectorResult, need to convert
            # signals from the internal structure
            signals = []
            if hasattr(result, 'signals'):
                signals = result.signals
            elif isinstance(result, dict) and 'signals' in result:
                signals = result['signals']

            return signals if isinstance(signals, list) else []

        except ImportError:
            logger.warning("DomainWhoisCollector not available")
            return []
        except Exception as e:
            logger.error(f"WHOIS collection failed: {e}")
            return []

    async def _collect_hiring(self, domains: List[str]) -> List[Signal]:
        """Run job postings collector"""
        try:
            from collectors.job_postings import JobPostingsCollector

            collector = JobPostingsCollector()
            result = await collector.run(domains=domains, dry_run=True)

            return result.get("signals", [])

        except ImportError:
            logger.warning("JobPostingsCollector not available")
            return []
        except Exception as e:
            logger.error(f"Hiring collection failed: {e}")
            return []

    async def _collect_github(self, domains: List[str]) -> List[Signal]:
        """
        Run GitHub collector.

        Derives org names from domains (e.g., "stripe.com" -> "stripe")
        """
        try:
            from collectors.github_activity import GitHubActivityCollector

            # Extract org names from domains
            orgs = []
            for d in domains:
                # Extract base name: "stripe.com" -> "stripe"
                base = d.split(".")[0].lower()
                if base and base not in orgs:
                    orgs.append(base)

            if not orgs:
                return []

            collector = GitHubActivityCollector(
                github_token=self.github_token,
            )
            result = await collector.run(org_names=orgs, dry_run=True)

            return result.get("signals", [])

        except ImportError:
            logger.warning("GitHubActivityCollector not available")
            return []
        except Exception as e:
            logger.error(f"GitHub collection failed: {e}")
            return []


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def quick_enrich(domain: str) -> Optional[EnrichedEntity]:
    """
    Quick single-domain enrichment.

    Convenience function for interactive use.

    Args:
        domain: Domain to enrich

    Returns:
        EnrichedEntity or None
    """
    orchestrator = SignalOrchestrator()
    entities = await orchestrator.enrich_domains([domain])
    return entities[0] if entities else None


async def batch_enrich(
    domains: List[str],
    min_confidence: float = 0.0,
) -> List[EnrichedEntity]:
    """
    Batch domain enrichment with filtering.

    Args:
        domains: List of domains to enrich
        min_confidence: Minimum confidence threshold

    Returns:
        List of EnrichedEntity above threshold
    """
    orchestrator = SignalOrchestrator()
    entities = await orchestrator.enrich_domains(domains)

    if min_confidence > 0:
        entities = [e for e in entities if e.confidence >= min_confidence]

    return entities


# =============================================================================
# CLI / TESTING
# =============================================================================

async def main():
    """CLI entry point for testing"""
    import argparse

    parser = argparse.ArgumentParser(description="Signal Orchestrator")
    parser.add_argument(
        "domains",
        nargs="*",
        help="Domains to enrich (e.g., anthropic.com stripe.com)"
    )
    parser.add_argument("--no-whois", action="store_true", help="Skip WHOIS")
    parser.add_argument("--no-hiring", action="store_true", help="Skip hiring")
    parser.add_argument("--no-github", action="store_true", help="Skip GitHub")
    parser.add_argument("--debug", action="store_true", help="Enable debug")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    domains = args.domains or ["anthropic.com", "stripe.com"]

    print(f"Enriching domains: {domains}")

    orchestrator = SignalOrchestrator()
    entities = await orchestrator.enrich_domains(
        domains=domains,
        check_whois=not args.no_whois,
        check_hiring=not args.no_hiring,
        check_github=not args.no_github,
    )

    print("\n" + "=" * 60)
    print("SIGNAL ORCHESTRATOR RESULTS")
    print("=" * 60)

    for entity in entities:
        print(f"\n{entity.domain or entity.canonical_key}:")
        print(f"  Confidence: {entity.confidence:.3f}")
        print(f"  Sources: {entity.source_count}")
        print(f"  Signal types: {', '.join(entity.signal_types)}")
        print(f"  Push decision: {entity.push_decision.value if entity.push_decision else 'N/A'}")
        print(f"  Suggested status: {entity.suggested_status or 'N/A'}")

        if entity.signals:
            print("  Signals:")
            for sig in entity.signals[:5]:
                print(f"    - {sig.signal_type} ({sig.source_api}): "
                      f"{sig.confidence:.2f}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
