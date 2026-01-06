"""
USPTO Patent Collector - Discover patent filings.

when_to_use: When looking for companies with IP/patent activity.
  Patent filings indicate R&D investment and potential defensible
  technology moats.

API: USPTO Patent Database (PatentsView API)
Cost: FREE
Signal Strength: MEDIUM (0.4-0.6)

Patent signals indicate:
1. R&D investment
2. Potential technical innovation
3. Defensible IP moat
4. Often precedes commercialization

Usage:
    collector = USPTOCollector(
        keywords=["machine learning", "neural network"],
        store=signal_store,
    )
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# USPTO PatentsView API
PATENTSVIEW_API = "https://api.patentsview.org/patents/query"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class PatentFiling:
    """A USPTO patent filing signal."""
    patent_id: str
    patent_number: str
    title: str
    abstract: str
    filing_date: datetime
    grant_date: Optional[datetime]
    inventors: List[Dict[str, str]]
    assignees: List[Dict[str, str]]
    cpc_codes: List[str] = field(default_factory=list)
    citations_count: int = 0

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength for patent filing.

        Scoring:
        - Base: 0.4 (patent filing = real R&D)
        - Boost for granted patent: +0.1
        - Boost for citations: up to +0.1
        - Boost for recent filing: +0.05
        - Boost for AI/ML CPC codes: +0.05
        """
        base = 0.4

        # Granted boost
        if self.grant_date:
            base += 0.1

        # Citation boost
        if self.citations_count >= 20:
            base += 0.1
        elif self.citations_count >= 5:
            base += 0.05

        # Recency boost
        if self.filing_date:
            age_days = (datetime.now(timezone.utc) - self.filing_date).days
            if age_days <= 365:
                base += 0.05

        # AI/ML CPC codes boost
        ai_cpc_prefixes = ["G06N", "G06F18", "G16H"]  # AI, ML, health informatics
        if any(
            code.startswith(prefix)
            for code in self.cpc_codes
            for prefix in ai_cpc_prefixes
        ):
            base += 0.05

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal."""
        confidence = self.calculate_signal_score()

        # Get assignee for canonical key
        assignee_name = ""
        if self.assignees:
            assignee_name = self.assignees[0].get("organization", "")
            if not assignee_name:
                assignee_name = self.assignees[0].get("name_first", "") + " " + self.assignees[0].get("name_last", "")

        assignee_key = assignee_name.lower().replace(" ", "_").replace(",", "")[:50]

        signal_id = f"uspto_{self.patent_id}"
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        return Signal(
            id=f"patent_filing_{signal_hash}",
            signal_type="patent_filing",
            confidence=confidence,
            source_api="uspto",
            source_url=f"https://patentsview.org/patent/{self.patent_number}",
            source_response_hash=hashlib.sha256(
                f"{self.patent_id}:{self.title}".encode()
            ).hexdigest()[:16],
            detected_at=self.filing_date or datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["uspto"],
            raw_data={
                "canonical_key": f"patent_assignee:{assignee_key}" if assignee_key else f"patent:{self.patent_number}",
                "company_name": assignee_name,
                "patent_number": self.patent_number,
                "title": self.title[:200],
                "abstract": self.abstract[:500] if self.abstract else "",
                "inventors": self.inventors[:3],
                "assignees": self.assignees[:2],
                "cpc_codes": self.cpc_codes[:5],
                "citations_count": self.citations_count,
                "is_granted": self.grant_date is not None,
            }
        )


# =============================================================================
# COLLECTOR
# =============================================================================

class USPTOCollector(BaseCollector):
    """
    Collect patent filings from USPTO PatentsView.

    Discovers patent filings that might indicate innovative startups
    or research commercialization.

    Usage:
        collector = USPTOCollector(
            keywords=["machine learning", "neural network"],
            store=signal_store,
        )
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        keywords: Optional[List[str]] = None,
        cpc_codes: Optional[List[str]] = None,
        store: Optional[SignalStore] = None,
        lookback_days: int = 90,
        max_results: int = 100,
    ):
        """
        Args:
            keywords: Keywords to search in patent titles/abstracts
            cpc_codes: CPC classification codes to filter by
            store: SignalStore for persistence
            lookback_days: How far back to search
            max_results: Maximum patents to fetch
        """
        super().__init__(store=store, collector_name="uspto")
        self.keywords = keywords or [
            "artificial intelligence",
            "machine learning",
            "neural network",
            "deep learning",
            "natural language processing",
        ]
        self.cpc_codes = cpc_codes or [
            "G06N",  # Computer systems based on specific computational models (AI/ML)
            "G06F18",  # Pattern recognition
            "G16H",  # Healthcare informatics
        ]
        self.lookback_days = lookback_days
        self.max_results = max_results
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    async def _collect_signals(self) -> List[Signal]:
        """Collect USPTO patents as signals."""
        patents = await self._fetch_patents()
        return [patent.to_signal() for patent in patents]

    async def _fetch_patents(self) -> List[PatentFiling]:
        """Fetch recent patents from PatentsView API."""
        patents: List[PatentFiling] = []

        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.lookback_days)

        # Build query
        # PatentsView uses a specific query format
        query = {
            "_and": [
                {
                    "_gte": {
                        "patent_date": start_date.strftime("%Y-%m-%d")
                    }
                },
            ]
        }

        # Add keyword conditions
        if self.keywords:
            keyword_conditions = []
            for kw in self.keywords[:5]:
                keyword_conditions.append({
                    "_or": [
                        {"_text_any": {"patent_title": kw}},
                        {"_text_any": {"patent_abstract": kw}},
                    ]
                })
            query["_and"].append({"_or": keyword_conditions})

        # Add CPC code conditions
        if self.cpc_codes:
            cpc_conditions = []
            for code in self.cpc_codes[:5]:
                cpc_conditions.append({
                    "_begins": {"cpc_subgroup_id": code}
                })
            query["_and"].append({"_or": cpc_conditions})

        # Request fields
        fields = [
            "patent_id",
            "patent_number",
            "patent_title",
            "patent_abstract",
            "patent_date",
            "patent_firstnamed_inventor_id",
            "patent_num_cited_by_us_patents",
        ]

        # Request options
        options = {
            "page": 1,
            "per_page": min(self.max_results, 1000),
        }

        try:
            response = await self.client.post(
                PATENTSVIEW_API,
                json={
                    "q": query,
                    "f": fields,
                    "o": options,
                },
                headers={"Content-Type": "application/json"},
            )

            if response.status_code != 200:
                logger.error(f"USPTO API error: {response.status_code}")
                logger.debug(f"Response: {response.text}")
                return patents

            data = response.json()
            patent_data = data.get("patents", [])

            if not patent_data:
                logger.info("No patents found matching criteria")
                return patents

            for p in patent_data:
                # Parse dates
                patent_date_str = p.get("patent_date", "")
                try:
                    filing_date = datetime.strptime(
                        patent_date_str, "%Y-%m-%d"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    filing_date = datetime.now(timezone.utc)

                # Extract inventors
                inventors = []
                if "inventors" in p:
                    for inv in p.get("inventors", [])[:5]:
                        inventors.append({
                            "name_first": inv.get("inventor_first_name", ""),
                            "name_last": inv.get("inventor_last_name", ""),
                            "city": inv.get("inventor_city", ""),
                            "country": inv.get("inventor_country", ""),
                        })

                # Extract assignees
                assignees = []
                if "assignees" in p:
                    for assign in p.get("assignees", [])[:3]:
                        assignees.append({
                            "organization": assign.get("assignee_organization", ""),
                            "name_first": assign.get("assignee_first_name", ""),
                            "name_last": assign.get("assignee_last_name", ""),
                            "type": assign.get("assignee_type", ""),
                        })

                # Extract CPC codes
                cpc_codes = []
                if "cpcs" in p:
                    for cpc in p.get("cpcs", [])[:10]:
                        code = cpc.get("cpc_subgroup_id", "")
                        if code:
                            cpc_codes.append(code)

                patent = PatentFiling(
                    patent_id=p.get("patent_id", ""),
                    patent_number=p.get("patent_number", ""),
                    title=p.get("patent_title", ""),
                    abstract=p.get("patent_abstract", ""),
                    filing_date=filing_date,
                    grant_date=filing_date,  # patent_date is grant date
                    inventors=inventors,
                    assignees=assignees,
                    cpc_codes=cpc_codes,
                    citations_count=p.get("patent_num_cited_by_us_patents", 0) or 0,
                )
                patents.append(patent)

        except httpx.HTTPError as e:
            logger.error(f"USPTO HTTP error: {e}")
        except Exception as e:
            logger.exception(f"USPTO fetch error: {e}")

        logger.info(f"Fetched {len(patents)} USPTO patents")
        return patents


# =============================================================================
# CLI
# =============================================================================

async def main():
    """CLI for testing USPTO collector."""
    import argparse

    parser = argparse.ArgumentParser(description="USPTO Patent Collector")
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=["machine learning", "neural network"],
        help="Keywords to search"
    )
    parser.add_argument("--days", type=int, default=90, help="Lookback days")
    parser.add_argument("--max", type=int, default=50, help="Max results")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    collector = USPTOCollector(
        keywords=args.keywords,
        lookback_days=args.days,
        max_results=args.max,
    )

    result = await collector.run(dry_run=True)

    print("\n" + "=" * 60)
    print("USPTO PATENT COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")
    print(f"Keywords: {', '.join(args.keywords)}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
