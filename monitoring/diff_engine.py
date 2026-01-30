"""
Diff Engine for Monitoring Subsystem

Computes differences between snapshots including:
- Content delta (text length change)
- Semantic drift (embedding-based similarity)
- State changes (page_state transitions)
- Redirect detection (host changes)

Severity is calculated as a weighted combination of these components.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from storage.embedding_store import EmbeddingStore
    from utils.embedding_generator import EmbeddingGenerator

from monitoring.models import (
    Snapshot,
    Diff,
    SeverityComponents,
    MonitoringConfig,
)
from monitoring.page_type_classifier import (
    PageTypeClassifier,
    PageClassification,
    PageType,
    classify_page,
)

logger = logging.getLogger(__name__)


@dataclass
class DiffResult:
    """Result of computing a diff between two snapshots."""
    diff: Diff
    should_trigger_profile_update: bool
    should_create_alert: bool
    trigger_reason: Optional[str] = None
    page_classification: Optional[PageClassification] = None
    why_now: Optional[str] = None


class DiffEngine:
    """
    Computes differences between snapshots with severity scoring.

    Handles cold start gracefully - returns None for semantic_drift
    when there's no baseline embedding to compare against.
    """

    def __init__(
        self,
        embedding_store: Optional["EmbeddingStore"] = None,
        embedding_generator: Optional["EmbeddingGenerator"] = None,
        config: Optional[MonitoringConfig] = None,
    ):
        """
        Initialize DiffEngine.

        Args:
            embedding_store: Store for embeddings
            embedding_generator: Generator for new embeddings
            config: Monitoring configuration
        """
        self._embedding_store = embedding_store
        self._embedding_generator = embedding_generator
        self._config = config or MonitoringConfig()
        self._page_classifier = PageTypeClassifier()

    async def compute_diff(
        self,
        old_snapshot: Optional[Snapshot],
        new_snapshot: Snapshot,
        new_text_content: str,
    ) -> DiffResult:
        """
        Compute the difference between old and new snapshots.

        Args:
            old_snapshot: Previous snapshot (None if first check)
            new_snapshot: New snapshot just taken
            new_text_content: Full text content of new page

        Returns:
            DiffResult with computed diff and action flags
        """
        # Initialize components
        components = SeverityComponents()
        has_redirect = False
        has_state_change = False
        has_text_change = False
        instant_trigger = False
        trigger_reason = None

        # 1. Check for redirect (instant trigger)
        if new_snapshot.has_redirect:
            has_redirect = True
            components.redirect = 1.0
            instant_trigger = True
            trigger_reason = "host_changed"
            logger.info(f"Detected redirect: {new_snapshot.requested_url} -> {new_snapshot.final_url}")

        # 2. Check for state change
        if old_snapshot and old_snapshot.page_state != new_snapshot.page_state:
            has_state_change = True
            components.state_change = 1.0

            # Some state changes are instant triggers
            if new_snapshot.page_state in ("error", "blocked"):
                instant_trigger = True
                trigger_reason = f"state_change_to_{new_snapshot.page_state}"
                logger.info(f"State changed: {old_snapshot.page_state} -> {new_snapshot.page_state}")

        # 3. Compute content delta
        if old_snapshot:
            old_length = old_snapshot.text_length or 0
            new_length = new_snapshot.text_length
            max_length = max(old_length, new_length, 1)
            content_delta = abs(new_length - old_length) / max_length
            components.content_delta = min(1.0, content_delta)

            if new_snapshot.content_hash != old_snapshot.content_hash:
                has_text_change = True
        else:
            # First snapshot - no content change to compare
            components.content_delta = 0.0

        # 4. Compute semantic drift
        semantic_drift = await self._compute_semantic_drift(
            old_snapshot.id if old_snapshot else None,
            new_snapshot,
            new_text_content,
        )
        components.semantic_drift = semantic_drift

        # 5. Classify page type
        page_classification = self._page_classifier.classify(
            url=new_snapshot.requested_url or "",
            text_content=new_text_content if len(new_text_content) > 100 else None,
        )

        # 6. Calculate overall severity score (with page type boost)
        severity_score = self._calculate_severity(
            components,
            instant_trigger,
            page_type_boost=page_classification.severity_boost,
        )

        # 7. Generate "why now" explanation
        why_now = self._page_classifier.get_why_now(
            page_classification.page_type,
            diff_summary=None,  # Could add diff details here
        )

        # 8. Create Diff object
        from datetime import datetime, timezone
        diff = Diff(
            watch_id=new_snapshot.watch_id,
            old_snapshot_id=old_snapshot.id if old_snapshot else None,
            new_snapshot_id=new_snapshot.id or 0,
            created_at=datetime.now(timezone.utc),
            severity_score=severity_score,
            severity_components=components,
            semantic_drift=semantic_drift,
            has_redirect=has_redirect,
            has_state_change=has_state_change,
            has_text_change=has_text_change,
            diff_summary=self._create_summary(old_snapshot, new_snapshot, components, page_classification),
        )

        # 9. Determine actions based on severity
        should_trigger = instant_trigger or severity_score >= self._config.profile_threshold
        should_alert = severity_score >= self._config.alert_threshold

        if not trigger_reason and should_trigger:
            trigger_reason = "high_severity"

        return DiffResult(
            diff=diff,
            should_trigger_profile_update=should_trigger,
            should_create_alert=should_alert,
            trigger_reason=trigger_reason,
            page_classification=page_classification,
            why_now=why_now,
        )

    async def _compute_semantic_drift(
        self,
        old_snapshot_id: Optional[int],
        new_snapshot: Snapshot,
        new_text: str,
    ) -> Optional[float]:
        """
        Compute semantic drift between old and new page content.

        Returns:
            0.0 (identical) to 1.0 (completely different), or None if not computable.
        """
        # Need both stores to compute semantic drift
        if not self._embedding_store or not self._embedding_generator:
            logger.debug("Embedding store/generator not available, skipping semantic drift")
            return None

        # Skip if text too short
        if len(new_text.strip()) < 100:
            logger.debug("Text too short for semantic drift computation")
            return None

        new_embedding_key = f"snapshot:{new_snapshot.id}"
        text_hash = hashlib.sha256(new_text.encode()).hexdigest()

        # Cold start handling: no previous snapshot
        if old_snapshot_id is None:
            # Store new embedding for future comparisons
            try:
                new_embedding = await self._embedding_generator.embed(new_text)
                await self._embedding_store.save_embedding(
                    canonical_key=new_embedding_key,
                    embedding=new_embedding,
                    source_text_hash=text_hash,
                    source_text_preview=new_text[:512],
                    embedding_kind="snapshot_v1",
                )
                logger.debug(f"Saved initial embedding for snapshot {new_snapshot.id}")
            except Exception as e:
                logger.warning(f"Failed to save initial embedding: {e}")
            return None  # Cannot compute drift without baseline

        # Try to retrieve old embedding
        old_embedding_key = f"snapshot:{old_snapshot_id}"
        try:
            old_embedding = await self._embedding_store.get_embedding(
                old_embedding_key,
                embedding_kind="snapshot_v1",
            )
        except Exception as e:
            logger.warning(f"Failed to retrieve old embedding: {e}")
            old_embedding = None

        if old_embedding is None:
            # Old snapshot exists but has no embedding (feature just enabled)
            logger.info(f"No embedding for snapshot {old_snapshot_id}. Storing new and skipping drift.")
            try:
                new_embedding = await self._embedding_generator.embed(new_text)
                await self._embedding_store.save_embedding(
                    canonical_key=new_embedding_key,
                    embedding=new_embedding,
                    source_text_hash=text_hash,
                    source_text_preview=new_text[:512],
                    embedding_kind="snapshot_v1",
                )
            except Exception as e:
                logger.warning(f"Failed to save embedding: {e}")
            return None

        # Compute new embedding
        try:
            new_embedding = await self._embedding_generator.embed(new_text)
        except Exception as e:
            logger.warning(f"Failed to generate new embedding: {e}")
            return None

        # Store new embedding
        try:
            await self._embedding_store.save_embedding(
                canonical_key=new_embedding_key,
                embedding=new_embedding,
                source_text_hash=text_hash,
                source_text_preview=new_text[:512],
                embedding_kind="snapshot_v1",
            )
        except Exception as e:
            logger.warning(f"Failed to save new embedding: {e}")

        # Compute cosine similarity
        similarity = self._cosine_similarity(old_embedding, new_embedding)

        # Drift is inverse of similarity
        drift = 1.0 - max(0.0, min(1.0, float(similarity)))

        logger.debug(f"Semantic drift: {drift:.3f} (similarity: {similarity:.3f})")
        return drift

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))

    def _calculate_severity(
        self,
        components: SeverityComponents,
        instant_trigger: bool = False,
        page_type_boost: float = 0.0,
    ) -> float:
        """
        Calculate overall severity score from components.

        Uses weighted combination of:
        - content_delta (text length change)
        - semantic_drift (embedding similarity)
        - state_change (page_state transition)
        - redirect (host change)
        - page_type_boost (extra weight for high-value pages like pricing/careers)
        """
        if instant_trigger:
            # Instant triggers get high severity
            return max(0.80, self._config.alert_threshold)

        # Weighted sum of available components
        score = 0.0
        total_weight = 0.0

        # Content delta
        if components.content_delta is not None:
            score += self._config.weight_content * components.content_delta
            total_weight += self._config.weight_content

        # Semantic drift (only if computed)
        if components.semantic_drift is not None:
            score += self._config.weight_semantic * components.semantic_drift
            total_weight += self._config.weight_semantic

        # State change
        if components.state_change > 0:
            score += self._config.weight_state * components.state_change
            total_weight += self._config.weight_state

        # Redirect
        if components.redirect > 0:
            score += self._config.weight_redirect * components.redirect
            total_weight += self._config.weight_redirect

        # Normalize by actual weights used
        if total_weight > 0:
            # Scale to use full 0-1 range based on available components
            normalized = score / total_weight
            # Apply page type boost (e.g., pricing pages get +0.15)
            boosted = normalized + page_type_boost
            return min(1.0, boosted)

        return page_type_boost  # Return boost even if no components

    def _create_summary(
        self,
        old_snapshot: Optional[Snapshot],
        new_snapshot: Snapshot,
        components: SeverityComponents,
        page_classification: Optional[PageClassification] = None,
    ) -> dict:
        """Create a summary dict of the diff."""
        old_length = old_snapshot.text_length if old_snapshot else 0
        new_length = new_snapshot.text_length

        summary = {
            "old_text_length": old_length,
            "new_text_length": new_length,
            "length_change": new_length - old_length,
            "content_delta": components.content_delta,
            "semantic_drift": components.semantic_drift,
            "state_change": components.state_change > 0,
            "old_state": old_snapshot.page_state if old_snapshot else None,
            "new_state": new_snapshot.page_state,
            "redirect": components.redirect > 0,
            "old_host": old_snapshot.final_host if old_snapshot else None,
            "new_host": new_snapshot.final_host,
        }

        # Add page classification if available
        if page_classification:
            summary["page_type"] = page_classification.page_type.value
            summary["page_type_confidence"] = page_classification.confidence
            summary["page_type_boost"] = page_classification.severity_boost

        return summary


# Convenience function for simple drift checks
async def compute_semantic_drift(
    old_snapshot_id: Optional[int],
    new_snapshot_id: int,
    new_text: str,
    embedding_store: "EmbeddingStore",
    generator: "EmbeddingGenerator",
) -> Optional[float]:
    """
    Standalone function to compute semantic drift.

    Args:
        old_snapshot_id: Previous snapshot ID (None if first check)
        new_snapshot_id: New snapshot ID
        new_text: Text content of new page
        embedding_store: Embedding store instance
        generator: Embedding generator instance

    Returns:
        Drift value (0.0-1.0) or None if not computable
    """
    from monitoring.models import Snapshot

    new_snapshot = Snapshot(id=new_snapshot_id, watch_id=0)
    engine = DiffEngine(
        embedding_store=embedding_store,
        embedding_generator=generator,
    )
    return await engine._compute_semantic_drift(old_snapshot_id, new_snapshot, new_text)
