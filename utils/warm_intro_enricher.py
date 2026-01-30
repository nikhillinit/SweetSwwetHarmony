"""
WarmIntroEnricher - Direct warm intro enrichment for investor domains.

Provides relationship enrichment for investor matches without multi-hop path finding.
Uses existing WarmIntroBoost for scoring, badges, and declined handling.

PRIVACY NOTE:
- Raw relationship data stays in private_graph.db ONLY
- Only derived indicators (score, badge, attribution, source) exposed to pipeline

Usage:
    from utils.warm_intro_enricher import WarmIntroEnricher
    from storage.relationship_store import RelationshipStore
    from utils.warm_intro_boost import WarmIntroBoost

    store = RelationshipStore("private_graph.db")
    await store.initialize()

    boost = WarmIntroBoost()
    enricher = WarmIntroEnricher(relationship_store=store, warm_intro_boost=boost)

    candidate = await enricher.enrich_investor(
        investor_domain="sequoia.com",
        user_email="user@example.com",
    )

    if candidate:
        print(f"{candidate.investor_domain}: {candidate.score:.2f} {candidate.badge}")
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from storage.relationship_store import RelationshipStore, CombinedRelationship
    from utils.warm_intro_boost import WarmIntroBoost, WarmIntroCandidate

logger = logging.getLogger(__name__)


class HasInvestorDomain(Protocol):
    """Protocol for objects with investor_domain attribute."""
    investor_domain: str


class WarmIntroEnricher:
    """
    Direct warm intro enrichment for INVESTOR domains.

    Uses existing WarmIntroBoost.build_candidate() for scoring, badges,
    and declined suppression. Does NOT implement multi-hop path finding
    (the current RelationshipStore is a star graph with only direct edges).

    Privacy boundary:
    - Raw relationship data (email hashes, intro counts) in private_graph.db
    - Only derived indicators exposed: score, badge, attribution, source
    """

    def __init__(
        self,
        relationship_store: "RelationshipStore",
        warm_intro_boost: "WarmIntroBoost",
    ):
        """
        Initialize WarmIntroEnricher.

        Args:
            relationship_store: RelationshipStore for relationship lookups
            warm_intro_boost: WarmIntroBoost for scoring and badge generation
        """
        self.store = relationship_store
        self.boost = warm_intro_boost

    async def enrich_investor(
        self,
        investor_domain: str,
        user_email: str,
    ) -> Optional["WarmIntroCandidate"]:
        """
        Get warm intro data for an INVESTOR domain.

        Args:
            investor_domain: The investor's domain (e.g., "sequoia.com")
            user_email: The user's email for relationship lookup

        Returns:
            WarmIntroCandidate if relationship exists, None otherwise.
            Uses WarmIntroBoost.build_candidate() for scoring/badge/declined handling.
        """
        combined = await self.store.get_combined_relationship(
            user_email, investor_domain
        )

        if not combined:
            logger.debug(f"No relationship found for {investor_domain}")
            return None

        # Check if there's meaningful relationship data
        if combined.gmail_score is None and combined.notion_score is None:
            logger.debug(f"No scores for {investor_domain}")
            return None

        # Check for declined status (safe default: suppress if declined)
        is_declined = combined.lp_status == "Declined" if combined.lp_status else False

        # Build attribution string
        attribution = self._build_attribution(combined)

        # Delegate to WarmIntroBoost for scoring, badges, declined handling
        candidate = self.boost.build_candidate(
            investor_domain=investor_domain,
            gmail_score=combined.gmail_score,
            notion_score=combined.notion_score,
            lp_status=combined.lp_status,
            attribution=attribution,
            notion_lp_ids=[],  # Not tracked in CombinedRelationship
            is_declined=is_declined,
            declined_at=None,  # RelationshipStore doesn't track declined_at
        )

        # If lp_status == "Declined" and declined_at unknown, candidate will be None
        # (suppressed by WarmIntroBoost trust safeguards)
        return candidate

    async def enrich_investor_matches(
        self,
        investor_matches: List[HasInvestorDomain],
        user_email: str,
    ) -> Dict[str, "WarmIntroCandidate"]:
        """
        Enrich multiple investor matches with warm intro data.

        Args:
            investor_matches: List of objects with investor_domain attribute
            user_email: User's email for relationship lookups

        Returns:
            Dict mapping investor_domain -> WarmIntroCandidate.
            Only includes investors with existing relationships.
        """
        results: Dict[str, "WarmIntroCandidate"] = {}

        for match in investor_matches:
            investor_domain = match.investor_domain

            candidate = await self.enrich_investor(investor_domain, user_email)
            if candidate:
                results[investor_domain] = candidate

        logger.info(
            f"Enriched {len(results)}/{len(investor_matches)} investor matches "
            f"with warm intro data"
        )

        return results

    def _build_attribution(self, combined: "CombinedRelationship") -> str:
        """
        Build attribution string from combined relationship data.

        Args:
            combined: CombinedRelationship from store

        Returns:
            Attribution string (e.g., "3 intros, 2 replies" or "LP: Active")
        """
        parts = []

        # Gmail attribution
        if combined.gmail_score and combined.intro_count > 0:
            parts.append(f"{combined.intro_count} intros")
        if combined.gmail_score and combined.reply_count > 0:
            parts.append(f"{combined.reply_count} replies")

        # Notion LP attribution
        if combined.lp_status and combined.lp_name:
            parts.append(f"LP: {combined.lp_name} ({combined.lp_status})")
        elif combined.lp_status:
            parts.append(f"LP: {combined.lp_status}")

        return ", ".join(parts) if parts else "relationship"
