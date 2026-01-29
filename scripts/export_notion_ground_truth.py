#!/usr/bin/env python3
"""
Export Notion ground truth data for thesis classification evaluation.

Queries Notion database for companies with known outcomes (Funded, Passed, Tracking)
and exports them as a labeled JSONL dataset for Inspect AI evaluation.

Usage:
    python scripts/export_notion_ground_truth.py
    python scripts/export_notion_ground_truth.py --output datasets/thesis_ground_truth.jsonl
    python scripts/export_notion_ground_truth.py --min-examples 100 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from connectors.notion_connector_v2 import NotionConnector, DealStatus
from connectors.notion_transport import NotionTransport

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class GroundTruthSample:
    """A labeled sample for thesis classification evaluation."""
    input: str  # Company description formatted for LLM
    target: str  # QUALIFIED, HELD, or REJECTED
    id: str  # Unique identifier
    metadata: Dict[str, Any]  # Additional context

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# =============================================================================
# LABEL MAPPING
# =============================================================================

# Map Notion statuses to ground truth labels
STATUS_TO_LABEL = {
    # Positive outcomes → QUALIFIED (thesis fit confirmed by investment decision)
    DealStatus.FUNDED.value: "QUALIFIED",
    DealStatus.COMMITTED.value: "QUALIFIED",
    DealStatus.DILIGENCE.value: "QUALIFIED",  # In diligence = passed initial filter

    # Negative outcomes → REJECTED (thesis fit rejected)
    DealStatus.PASSED.value: "REJECTED",
    DealStatus.LOST.value: "REJECTED",  # Lost = was interested but didn't win

    # Pipeline stages → depends on thesis_fit score
    DealStatus.SOURCE.value: None,  # Needs manual review
    DealStatus.INITIAL_MEETING.value: None,  # Needs manual review
    DealStatus.TRACKING.value: None,  # Use thesis_fit to determine
}


# =============================================================================
# EXPORT CLASS
# =============================================================================

class NotionGroundTruthExporter:
    """
    Exports Notion companies as labeled ground truth for thesis evaluation.

    Ground truth labels:
    - QUALIFIED: Companies we invested in or seriously considered (Funded, Committed, Diligence)
    - REJECTED: Companies we explicitly passed on (Passed, Lost)
    - HELD: Companies still being tracked (Tracking with thesis_fit < 0.3)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("NOTION_API_KEY")
        self.database_id = database_id or os.environ.get("NOTION_DATABASE_ID")

        if not self.api_key or not self.database_id:
            raise ValueError(
                "Missing NOTION_API_KEY or NOTION_DATABASE_ID environment variables"
            )

        self.transport = NotionTransport(api_key=self.api_key)
        self.connector = NotionConnector(
            api_key=self.api_key,
            database_id=self.database_id,
            transport=self.transport,
        )

    async def export(
        self,
        min_examples: int = 100,
        include_tracking: bool = True,
        tracking_threshold: float = 0.3,
    ) -> List[GroundTruthSample]:
        """
        Export ground truth samples from Notion.

        Args:
            min_examples: Minimum number of examples to export
            include_tracking: Whether to include Tracking companies as HELD
            tracking_threshold: Thesis fit threshold for HELD classification

        Returns:
            List of GroundTruthSample objects
        """
        samples: List[GroundTruthSample] = []

        # Statuses to query
        statuses_to_query = [
            DealStatus.FUNDED.value,
            DealStatus.COMMITTED.value,
            DealStatus.DILIGENCE.value,
            DealStatus.PASSED.value,
            DealStatus.LOST.value,
        ]

        if include_tracking:
            statuses_to_query.append(DealStatus.TRACKING.value)

        logger.info(f"Querying Notion for statuses: {statuses_to_query}")

        # Query each status
        for status in statuses_to_query:
            pages = await self._query_by_status(status)
            logger.info(f"Found {len(pages)} companies with status '{status}'")

            for page in pages:
                sample = self._page_to_sample(page, status, tracking_threshold)
                if sample:
                    samples.append(sample)

        logger.info(f"Total samples exported: {len(samples)}")

        # Warn if below minimum
        if len(samples) < min_examples:
            logger.warning(
                f"Only {len(samples)} samples exported, below minimum of {min_examples}. "
                "Consider adding more labeled data in Notion."
            )

        return samples

    async def _query_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Query Notion for pages with a specific status."""
        all_results: List[Dict[str, Any]] = []
        has_more = True
        start_cursor = None

        while has_more:
            payload: Dict[str, Any] = {
                "filter": {
                    "property": "Status",
                    "select": {"equals": status}
                },
                "page_size": 100
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor

            response = await self.transport.post(
                f"/databases/{self.database_id}/query",
                json=payload,
            )

            all_results.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        return all_results

    def _page_to_sample(
        self,
        page: Dict[str, Any],
        status: str,
        tracking_threshold: float,
    ) -> Optional[GroundTruthSample]:
        """Convert a Notion page to a GroundTruthSample."""
        props = page.get("properties", {})
        page_id = page["id"]

        # Extract company information
        company_name = self._extract_title(props.get("Company Name", {}))
        description = self._extract_text(props.get("Short Description", {}))
        website = props.get("Website", {}).get("url", "")
        sector = self._extract_select(props.get("Sector", {}))
        location = self._extract_text(props.get("Location", {}))
        confidence_score = props.get("Confidence Score", {}).get("number")
        signal_types = self._extract_multi_select(props.get("Signal Types", {}))
        why_now = self._extract_text(props.get("Why Now", {}))

        # Determine label
        label = STATUS_TO_LABEL.get(status)

        # For Tracking, use threshold to determine HELD
        if status == DealStatus.TRACKING.value:
            if confidence_score is not None and confidence_score < tracking_threshold:
                label = "HELD"
            else:
                # Skip high-confidence tracking companies (unclear ground truth)
                return None

        if not label:
            # Skip companies with unclear ground truth
            return None

        if not company_name:
            # Skip companies without names
            return None

        # Build input string (what the classifier sees)
        input_parts = [f"Company: {company_name}"]

        if description:
            input_parts.append(f"Description: {description}")

        if sector:
            input_parts.append(f"Sector: {sector}")

        if location:
            input_parts.append(f"Location: {location}")

        if signal_types:
            input_parts.append(f"Signals: {', '.join(signal_types)}")

        if why_now:
            input_parts.append(f"Why Now: {why_now}")

        if website:
            input_parts.append(f"Website: {website}")

        input_text = "\n".join(input_parts)

        # Build metadata
        metadata = {
            "notion_id": page_id,
            "actual_outcome": status,
            "company_name": company_name,
            "signal_types": signal_types,
            "confidence_score": confidence_score,
            "sector": sector,
            "website": website,
            "exported_at": datetime.utcnow().isoformat(),
        }

        return GroundTruthSample(
            input=input_text,
            target=label,
            id=f"notion_{page_id.replace('-', '')}",
            metadata=metadata,
        )

    @staticmethod
    def _extract_title(prop: Dict) -> str:
        """Extract text from Notion title property."""
        title = prop.get("title", [])
        if title:
            return title[0].get("text", {}).get("content", "")
        return ""

    @staticmethod
    def _extract_text(prop: Dict) -> Optional[str]:
        """Extract text from Notion rich_text property."""
        rich_text = prop.get("rich_text", [])
        if rich_text:
            return rich_text[0].get("text", {}).get("content", "")
        return None

    @staticmethod
    def _extract_select(prop: Dict) -> Optional[str]:
        """Extract value from Notion select property."""
        select = prop.get("select")
        if select:
            return select.get("name")
        return None

    @staticmethod
    def _extract_multi_select(prop: Dict) -> List[str]:
        """Extract values from Notion multi_select property."""
        multi_select = prop.get("multi_select", [])
        return [item.get("name") for item in multi_select if item.get("name")]


# =============================================================================
# CLI
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Export Notion ground truth for thesis classification evaluation"
    )
    parser.add_argument(
        "--output", "-o",
        default="datasets/thesis_ground_truth.jsonl",
        help="Output JSONL file path"
    )
    parser.add_argument(
        "--min-examples",
        type=int,
        default=100,
        help="Minimum number of examples to export"
    )
    parser.add_argument(
        "--no-tracking",
        action="store_true",
        help="Exclude Tracking companies from export"
    )
    parser.add_argument(
        "--tracking-threshold",
        type=float,
        default=0.3,
        help="Thesis fit threshold for HELD classification"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print samples without writing to file"
    )

    args = parser.parse_args()

    try:
        exporter = NotionGroundTruthExporter()
        samples = await exporter.export(
            min_examples=args.min_examples,
            include_tracking=not args.no_tracking,
            tracking_threshold=args.tracking_threshold,
        )

        if args.dry_run:
            print(f"\n{'='*60}")
            print(f"DRY RUN - Would export {len(samples)} samples")
            print(f"{'='*60}\n")

            # Show label distribution
            label_counts = {}
            for sample in samples:
                label_counts[sample.target] = label_counts.get(sample.target, 0) + 1

            print("Label distribution:")
            for label, count in sorted(label_counts.items()):
                print(f"  {label}: {count}")

            print(f"\nSample examples:")
            for sample in samples[:3]:
                print(f"\n--- {sample.metadata['company_name']} ---")
                print(f"Target: {sample.target}")
                print(f"Input:\n{sample.input[:200]}...")
        else:
            # Write to file
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                for sample in samples:
                    f.write(json.dumps(sample.to_dict()) + "\n")

            print(f"Exported {len(samples)} samples to {output_path}")

            # Show label distribution
            label_counts = {}
            for sample in samples:
                label_counts[sample.target] = label_counts.get(sample.target, 0) + 1

            print("\nLabel distribution:")
            for label, count in sorted(label_counts.items()):
                print(f"  {label}: {count}")

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Export failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
