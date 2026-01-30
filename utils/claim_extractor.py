"""
Claim Extractor for Phase G Sprint 2

Extracts structured claim facts from ConsolidatedSignal objects.
Maps source authority to tiers and produces ClaimFact objects for storage.

Predicates Extracted:
- company_name: Official company name
- founding_date: When the company was founded
- location: Primary company location
- industry: Industry or sector
- funding_raised: Total funding raised
- website: Company website URL

Usage:
    extractor = ClaimExtractor()
    facts = extractor.extract(
        consolidated_signal,
        entity_id="abc123",
        canonical_key="domain:acme.ai"
    )

    async with store.transaction_immediate() as tx:
        for fact in facts:
            await claim_fact_store.save_fact(fact, tx)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from storage.claim_fact_store import ClaimFact, source_to_tier, authority_to_tier
from utils.merge_policy import SOURCE_AUTHORITY, DEFAULT_AUTHORITY

if TYPE_CHECKING:
    from utils.signal_consolidator import ConsolidatedSignal

logger = logging.getLogger(__name__)


# =============================================================================
# PREDICATE CONFIGURATION
# =============================================================================

# Predicates to extract from ConsolidatedSignal
EXTRACTABLE_PREDICATES = [
    "company_name",
    "founding_date",
    "location",
    "industry",
    "funding_raised",
    "website"
]

# Mapping of raw_data fields to predicates
FIELD_TO_PREDICATE: Dict[str, str] = {
    # Company name variants
    "company_name": "company_name",
    "name": "company_name",
    "legal_name": "company_name",
    "organization_name": "company_name",

    # Founding date variants
    "founding_date": "founding_date",
    "founded_date": "founding_date",
    "registered_date": "founding_date",
    "incorporation_date": "founding_date",
    "created_date": "founding_date",
    "founded_on": "founding_date",
    "started_at": "founding_date",
    "founding_year": "founding_date",

    # Location variants
    "location": "location",
    "region": "location",
    "country": "location",
    "hq_country": "location",
    "hq_city": "location",
    "headquarters": "location",
    "city": "location",
    "state": "location",

    # Industry variants
    "industry": "industry",
    "sector": "industry",
    "category": "industry",
    "vertical": "industry",
    "business_type": "industry",

    # Funding variants
    "funding_raised": "funding_raised",
    "total_funding": "funding_raised",
    "funding_total": "funding_raised",
    "raised_amount": "funding_raised",
    "amount_raised": "funding_raised",

    # Website variants
    "website": "website",
    "website_url": "website",
    "homepage": "website",
    "url": "website",
    "domain": "website",
}


# =============================================================================
# CLAIM EXTRACTOR
# =============================================================================

class ClaimExtractor:
    """
    Extracts claim facts from consolidated signals.

    Produces ClaimFact objects ready for storage in the bi-temporal claim store.
    """

    def __init__(self):
        """Initialize the claim extractor."""
        pass

    def extract(
        self,
        consolidated: ConsolidatedSignal,
        entity_id: str,
        canonical_key: str
    ) -> List[ClaimFact]:
        """
        Extract claim facts from a consolidated signal.

        Args:
            consolidated: The ConsolidatedSignal to extract from
            entity_id: Stable entity identifier
            canonical_key: Primary canonical key for this entity

        Returns:
            List of ClaimFact objects ready for storage
        """
        facts: List[ClaimFact] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # Determine valid_from from consolidated signal
        valid_from = consolidated.latest_detected_at.isoformat()

        # Get contributing signal IDs
        signal_ids = consolidated.contributing_signal_ids

        # Calculate average source tier from source_apis
        avg_tier = self._calculate_average_tier(consolidated.source_apis)

        # Extract from consolidated fields
        if consolidated.company_name:
            facts.append(self._create_fact(
                entity_id=entity_id,
                predicate="company_name",
                value=consolidated.company_name,
                source_tier=avg_tier,
                confidence=0.8,  # High confidence for consolidated name
                valid_from=valid_from,
                observed_at=now_iso,
                signal_ids=signal_ids,
                canonical_key=canonical_key
            ))

        if consolidated.founding_date:
            facts.append(self._create_fact(
                entity_id=entity_id,
                predicate="founding_date",
                value=consolidated.founding_date.isoformat(),
                source_tier=avg_tier,
                confidence=0.7,
                valid_from=valid_from,
                observed_at=now_iso,
                signal_ids=signal_ids,
                canonical_key=canonical_key
            ))

        # Extract from merged_raw_data
        raw_data_facts = self._extract_from_raw_data(
            consolidated.merged_raw_data,
            entity_id,
            valid_from,
            now_iso,
            signal_ids,
            canonical_key,
            consolidated.source_apis
        )
        facts.extend(raw_data_facts)

        # Deduplicate facts by predicate (keep first/highest priority)
        seen_predicates: set = set()
        unique_facts: List[ClaimFact] = []
        for fact in facts:
            if fact.predicate not in seen_predicates:
                seen_predicates.add(fact.predicate)
                unique_facts.append(fact)

        logger.debug(
            f"Extracted {len(unique_facts)} facts for entity {entity_id} "
            f"from {len(signal_ids)} signals"
        )

        return unique_facts

    def extract_batch(
        self,
        consolidated_signals: List[ConsolidatedSignal],
        entity_id_map: Dict[str, str]
    ) -> Dict[str, List[ClaimFact]]:
        """
        Extract claim facts from multiple consolidated signals.

        Args:
            consolidated_signals: List of ConsolidatedSignal objects
            entity_id_map: Dict mapping canonical_key -> entity_id

        Returns:
            Dict mapping entity_id -> List[ClaimFact]
        """
        result: Dict[str, List[ClaimFact]] = {}

        for consolidated in consolidated_signals:
            entity_id = entity_id_map.get(consolidated.canonical_key)
            if not entity_id:
                logger.warning(
                    f"No entity_id for canonical_key: {consolidated.canonical_key}"
                )
                continue

            facts = self.extract(
                consolidated,
                entity_id=entity_id,
                canonical_key=consolidated.canonical_key
            )

            if facts:
                result[entity_id] = facts

        return result

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _extract_from_raw_data(
        self,
        raw_data: Dict[str, Any],
        entity_id: str,
        valid_from: str,
        observed_at: str,
        signal_ids: List[int],
        canonical_key: str,
        source_apis: List[str]
    ) -> List[ClaimFact]:
        """Extract facts from merged raw data."""
        facts: List[ClaimFact] = []

        # Track which predicates we've already extracted
        extracted: set = set()

        for field, predicate in FIELD_TO_PREDICATE.items():
            if predicate in extracted:
                continue

            value = raw_data.get(field)
            if value is None:
                continue

            # Skip empty strings and lists
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue

            # Determine source tier
            tier = self._calculate_average_tier(source_apis)

            # Determine confidence based on field type
            confidence = self._field_confidence(predicate)

            facts.append(self._create_fact(
                entity_id=entity_id,
                predicate=predicate,
                value=value,
                source_tier=tier,
                confidence=confidence,
                valid_from=valid_from,
                observed_at=observed_at,
                signal_ids=signal_ids,
                canonical_key=canonical_key
            ))

            extracted.add(predicate)

        return facts

    def _create_fact(
        self,
        entity_id: str,
        predicate: str,
        value: Any,
        source_tier: int,
        confidence: float,
        valid_from: str,
        observed_at: str,
        signal_ids: List[int],
        canonical_key: str
    ) -> ClaimFact:
        """Create a ClaimFact with proper JSON encoding."""
        # Encode value as JSON
        value_json = json.dumps(value)

        return ClaimFact(
            entity_id=entity_id,
            predicate=predicate,
            value_json=value_json,
            source_tier=source_tier,
            confidence=confidence,
            valid_from=valid_from,
            observed_at=observed_at,
            supporting_signal_ids=signal_ids,
            source_canonical_key=canonical_key
        )

    def _calculate_average_tier(self, source_apis: List[str]) -> int:
        """Calculate average authority tier from source APIs."""
        if not source_apis:
            return 5  # Lowest tier

        total_authority = 0.0
        for source in source_apis:
            authority = SOURCE_AUTHORITY.get(source, DEFAULT_AUTHORITY)
            total_authority += authority

        avg_authority = total_authority / len(source_apis)
        return authority_to_tier(avg_authority)

    def _field_confidence(self, predicate: str) -> float:
        """
        Determine confidence for a predicate.

        Some predicates are more reliable than others.
        """
        # High confidence predicates (usually from official sources)
        high_confidence = {"company_name", "website", "founding_date"}

        # Medium confidence predicates
        medium_confidence = {"location", "industry"}

        # Lower confidence predicates (often estimated)
        low_confidence = {"funding_raised"}

        if predicate in high_confidence:
            return 0.8
        elif predicate in medium_confidence:
            return 0.6
        elif predicate in low_confidence:
            return 0.5
        else:
            return 0.5  # Default


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def extract_claims(
    consolidated: ConsolidatedSignal,
    entity_id: str,
    canonical_key: str
) -> List[ClaimFact]:
    """
    Convenience function to extract claims from a consolidated signal.

    Args:
        consolidated: The ConsolidatedSignal to extract from
        entity_id: Stable entity identifier
        canonical_key: Primary canonical key

    Returns:
        List of ClaimFact objects
    """
    extractor = ClaimExtractor()
    return extractor.extract(consolidated, entity_id, canonical_key)
