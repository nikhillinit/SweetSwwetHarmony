"""
Notion Pusher

Pushes qualified signals to Notion Inbox database.
Uses inbox_connector for API access with rate limiting.

Workflow:
1. Signal passes filter pipeline (LLM_REVIEW or LLM_AUTO_APPROVE)
2. Pusher creates Notion page with classification data
3. Updates signal status to 'in_notion'
4. Returns Notion page ID
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .inbox_connector import NotionInboxConnector

if TYPE_CHECKING:
    from ..storage.consumer_store import ConsumerStore, StoredSignal
    from ..thesis_filter.pipeline import FilterResult
    from ..thesis_filter.llm_classifier import ThesisClassification

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class PushResult:
    """Result of pushing a signal to Notion."""
    signal_id: int
    success: bool
    notion_page_id: Optional[str] = None
    error: Optional[str] = None
    skipped_reason: Optional[str] = None


# =============================================================================
# NOTION PUSHER
# =============================================================================

class NotionPusher:
    """
    Pushes qualified signals to Notion Inbox.

    Handles:
    - Duplicate detection (already in Notion)
    - Page creation with classification data
    - Status updates in local store

    Usage:
        pusher = NotionPusher(store)

        # Push a single signal
        result = await pusher.push_signal(signal, filter_result)

        # Push multiple signals
        results = await pusher.push_batch(signals_with_results)
    """

    def __init__(
        self,
        store: Optional[ConsumerStore] = None,
        connector: Optional[NotionInboxConnector] = None,
    ):
        """
        Initialize pusher.

        Args:
            store: ConsumerStore for status updates
            connector: NotionInboxConnector (created if not provided)
        """
        self.store = store
        self._connector = connector

    @property
    def connector(self) -> NotionInboxConnector:
        """Lazy-load connector."""
        if self._connector is None:
            self._connector = NotionInboxConnector()
        return self._connector

    async def push_signal(
        self,
        signal: StoredSignal,
        filter_result: FilterResult,
    ) -> PushResult:
        """
        Push a single signal to Notion.

        Args:
            signal: StoredSignal from database
            filter_result: FilterResult from thesis filter

        Returns:
            PushResult with outcome
        """
        # Check if should push
        if not filter_result.passed:
            return PushResult(
                signal_id=signal.id,
                success=False,
                skipped_reason=f"Filter rejected: {filter_result.reason}",
            )

        # Check for duplicates in Notion
        existing_page = await self.connector.page_exists(signal.id)
        if existing_page:
            return PushResult(
                signal_id=signal.id,
                success=False,
                skipped_reason="Already in Notion",
                notion_page_id=existing_page,
            )

        # Extract classification data
        classification = filter_result.classification
        name = self._get_display_name(signal, classification)
        category = filter_result.category or "other"
        score = filter_result.score or 0.0
        rationale = filter_result.reason
        key_signals = classification.key_signals if classification else []

        try:
            # Create Notion page
            page_id = await self.connector.create_page(
                name=name,
                source=signal.source_api,
                signal_id=signal.id,
                url=signal.url,
                thesis_score=score,
                category=category,
                rationale=rationale,
                key_signals=key_signals,
            )

            # Update local store
            if self.store:
                await self.store.update_signal_status(
                    signal_id=signal.id,
                    status="in_notion",
                    notion_page_id=page_id,
                )

            logger.info(f"Pushed signal {signal.id} to Notion: {page_id}")

            return PushResult(
                signal_id=signal.id,
                success=True,
                notion_page_id=page_id,
            )

        except Exception as e:
            logger.error(f"Failed to push signal {signal.id}: {e}")
            return PushResult(
                signal_id=signal.id,
                success=False,
                error=str(e),
            )

    async def push_batch(
        self,
        signals_with_results: List[tuple[StoredSignal, FilterResult]],
    ) -> List[PushResult]:
        """
        Push multiple signals to Notion.

        Processes sequentially to respect rate limits.

        Args:
            signals_with_results: List of (signal, filter_result) tuples

        Returns:
            List of PushResults
        """
        results = []

        for signal, filter_result in signals_with_results:
            result = await self.push_signal(signal, filter_result)
            results.append(result)

        # Summary logging
        success_count = sum(1 for r in results if r.success)
        skip_count = sum(1 for r in results if r.skipped_reason)
        error_count = sum(1 for r in results if r.error)

        logger.info(
            f"Push batch complete: {success_count} pushed, "
            f"{skip_count} skipped, {error_count} errors"
        )

        return results

    def _get_display_name(
        self,
        signal: StoredSignal,
        classification: Optional[ThesisClassification],
    ) -> str:
        """Get display name for Notion page."""
        # Prefer extracted company name
        if signal.extracted_company_name:
            return signal.extracted_company_name

        # Try classification
        if classification and classification.company_name:
            return classification.company_name

        # Fall back to title
        if signal.title:
            # Clean up common prefixes
            title = signal.title
            for prefix in ["Show HN:", "TM:", "[Product]", "[Launch]"]:
                if title.startswith(prefix):
                    title = title[len(prefix):].strip()
            return title[:100]

        return "Unknown Company"


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def push_signal_to_notion(
    signal: StoredSignal,
    filter_result: FilterResult,
    store: Optional[ConsumerStore] = None,
) -> PushResult:
    """
    Convenience function to push a signal to Notion.

    Args:
        signal: StoredSignal from database
        filter_result: FilterResult from thesis filter
        store: Optional ConsumerStore for status updates

    Returns:
        PushResult
    """
    pusher = NotionPusher(store)
    return await pusher.push_signal(signal, filter_result)
