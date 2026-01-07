"""
TriggerGate: Deterministic signal filtering before LLM classification.

This module implements the first stage of two-stage signal gating:
1. TriggerGate (deterministic, free) - filters 80%+ of changes
2. LLMClassifier (semantic, ~$0.02/call) - classifies triggered signals

Only signals that pass the trigger gate are sent to the LLM for classification,
reducing costs by 80-90%.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional
import difflib


class ChangeType(Enum):
    """Types of changes that can trigger classification."""
    DESCRIPTION_CHANGE = "description_change"
    DOMAIN_CHANGE = "domain_change"
    NAME_CHANGE = "name_change"
    KEYWORD_SWAP = "keyword_swap"
    NO_CHANGE = "no_change"


@dataclass
class TriggerResult:
    """Result of trigger gate evaluation."""
    should_trigger: bool
    change_types: List[ChangeType] = field(default_factory=list)
    trigger_reason: Optional[str] = None
    change_magnitude: float = 0.0


class TriggerGate:
    """
    Deterministic filter that decides whether a signal change warrants LLM classification.

    Triggers on:
    - Description changed meaningfully (>threshold% difference)
    - Homepage/domain changed
    - Pivot keywords appeared (enterprise, b2b, api, etc.)

    Does NOT trigger on:
    - First observation (no baseline)
    - Minor changes below threshold
    - Identical snapshots
    """

    DEFAULT_PIVOT_KEYWORDS = [
        "enterprise", "b2b", "platform", "api", "saas",
        "pivot", "rebrand", "acquired", "shutdown", "deprecated",
        "discontinued", "sunsetting", "closed"
    ]

    def __init__(
        self,
        description_threshold: float = 0.2,
        pivot_keywords: Optional[List[str]] = None
    ):
        """
        Initialize TriggerGate.

        Args:
            description_threshold: Minimum change ratio to trigger (0.0-1.0).
                                   0.2 means 20% change required.
            pivot_keywords: Keywords that indicate a pivot when they appear.
        """
        self.description_threshold = description_threshold
        self.pivot_keywords = pivot_keywords or self.DEFAULT_PIVOT_KEYWORDS

    def should_classify(
        self,
        old_snapshot: Dict[str, Any],
        new_snapshot: Dict[str, Any]
    ) -> TriggerResult:
        """
        Determine if the change between snapshots warrants classification.

        Args:
            old_snapshot: Previous snapshot of the entity
            new_snapshot: Current snapshot of the entity

        Returns:
            TriggerResult with should_trigger=True if classification needed
        """
        # No baseline = no trigger (first observation)
        if not old_snapshot:
            return TriggerResult(
                should_trigger=False,
                change_types=[ChangeType.NO_CHANGE],
                trigger_reason="No baseline snapshot for comparison"
            )

        changes: List[ChangeType] = []
        reasons: List[str] = []
        max_magnitude: float = 0.0

        # Check description change
        desc_result = self._check_description_change(old_snapshot, new_snapshot)
        if desc_result:
            changes.append(desc_result[0])
            reasons.append(desc_result[1])
            max_magnitude = max(max_magnitude, desc_result[2])

        # Check domain/homepage change
        domain_result = self._check_domain_change(old_snapshot, new_snapshot)
        if domain_result:
            changes.append(domain_result[0])
            reasons.append(domain_result[1])
            max_magnitude = max(max_magnitude, domain_result[2])

        # Check for pivot keywords
        keyword_result = self._check_pivot_keywords(old_snapshot, new_snapshot)
        if keyword_result:
            changes.append(keyword_result[0])
            reasons.append(keyword_result[1])
            max_magnitude = max(max_magnitude, keyword_result[2])

        # No changes detected
        if not changes:
            return TriggerResult(
                should_trigger=False,
                change_types=[ChangeType.NO_CHANGE]
            )

        return TriggerResult(
            should_trigger=True,
            change_types=changes,
            trigger_reason="; ".join(reasons),
            change_magnitude=max_magnitude
        )

    def _check_description_change(
        self,
        old: Dict[str, Any],
        new: Dict[str, Any]
    ) -> Optional[tuple]:
        """Check if description changed significantly."""
        old_desc = old.get("description", "") or ""
        new_desc = new.get("description", "") or ""

        # Need both descriptions to compare
        if not old_desc or not new_desc:
            return None

        # Calculate similarity ratio
        ratio = difflib.SequenceMatcher(None, old_desc, new_desc).ratio()
        change_pct = 1 - ratio

        if change_pct > self.description_threshold:
            return (
                ChangeType.DESCRIPTION_CHANGE,
                f"Description changed {change_pct:.0%}",
                change_pct
            )

        return None

    def _check_domain_change(
        self,
        old: Dict[str, Any],
        new: Dict[str, Any]
    ) -> Optional[tuple]:
        """Check if domain/homepage changed."""
        old_domain = old.get("homepage") or old.get("domain") or old.get("website")
        new_domain = new.get("homepage") or new.get("domain") or new.get("website")

        # Need both domains to compare
        if not old_domain or not new_domain:
            return None

        # Normalize for comparison
        old_normalized = self._normalize_domain(old_domain)
        new_normalized = self._normalize_domain(new_domain)

        if old_normalized != new_normalized:
            return (
                ChangeType.DOMAIN_CHANGE,
                f"Domain changed: {old_domain} -> {new_domain}",
                1.0  # Domain changes are high severity
            )

        return None

    def _check_pivot_keywords(
        self,
        old: Dict[str, Any],
        new: Dict[str, Any]
    ) -> Optional[tuple]:
        """Check if pivot keywords appeared in new description."""
        old_desc = (old.get("description", "") or "").lower()
        new_desc = (new.get("description", "") or "").lower()

        if not new_desc:
            return None

        new_keywords = []
        for keyword in self.pivot_keywords:
            if keyword in new_desc and keyword not in old_desc:
                new_keywords.append(keyword)

        if new_keywords:
            return (
                ChangeType.KEYWORD_SWAP,
                f"Pivot keywords detected: {', '.join(new_keywords)}",
                0.8  # Keyword swaps are moderately severe
            )

        return None

    def _normalize_domain(self, domain: str) -> str:
        """Normalize domain for comparison."""
        domain = domain.lower().strip()
        # Remove protocol
        for prefix in ["https://", "http://", "www."]:
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        # Remove trailing slash
        domain = domain.rstrip("/")
        return domain
