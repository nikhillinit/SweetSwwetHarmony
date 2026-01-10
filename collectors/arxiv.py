"""
ArXiv Collector - Discover academic research signals.

when_to_use: When looking for research-based startups or academics
  who might be commercializing their research. Useful for deep tech,
  AI, and biotech verticals.

API: ArXiv API (free, no auth required)
Cost: FREE
Signal Strength: LOW-MEDIUM (0.3-0.5)

ArXiv signals indicate:
1. Academic research activity in relevant areas
2. Potential for research-based startups
3. Early technical validation

Note: ArXiv signals are weak on their own but valuable when combined
with other signals (incorporation, GitHub activity, hiring).

Usage:
    collector = ArxivCollector(
        categories=["cs.AI", "cs.LG", "q-bio"],
        store=signal_store,
    )
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# ArXiv API endpoint
ARXIV_API = "http://export.arxiv.org/api/query"

# Thesis-relevant ArXiv categories
THESIS_CATEGORIES = {
    # AI Infrastructure
    "cs.AI": "Artificial Intelligence",
    "cs.LG": "Machine Learning",
    "cs.CL": "Computation and Language (NLP)",
    "cs.CV": "Computer Vision",
    "cs.DC": "Distributed Computing",
    "cs.DB": "Databases",

    # Healthtech
    "q-bio": "Quantitative Biology",
    "q-bio.QM": "Quantitative Methods",
    "physics.med-ph": "Medical Physics",
    "stat.ML": "Machine Learning (Stats)",

    # Cleantech
    "physics.ao-ph": "Atmospheric Physics",
    "cond-mat.mtrl-sci": "Materials Science",
    "physics.chem-ph": "Chemical Physics",
}


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ArxivPaper:
    """An ArXiv paper signal."""
    arxiv_id: str
    title: str
    abstract: str
    authors: List[str]
    categories: List[str]
    published_at: datetime
    updated_at: datetime
    pdf_url: str
    affiliations: List[str] = field(default_factory=list)

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength for research paper.

        Research papers are weak signals on their own.
        Scoring:
        - Base: 0.3 (published research)
        - Boost for multiple categories: +0.05
        - Boost for recent publication: +0.1
        - Boost for AI/ML categories: +0.05
        """
        base = 0.3

        # Multi-category boost
        if len(self.categories) >= 3:
            base += 0.05

        # Recency boost
        age_days = (datetime.now(timezone.utc) - self.published_at).days
        if age_days <= 30:
            base += 0.1
        elif age_days <= 90:
            base += 0.05

        # AI/ML category boost
        ai_categories = {"cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML"}
        if any(cat in ai_categories for cat in self.categories):
            base += 0.05

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal."""
        confidence = self.calculate_signal_score()

        # Create canonical key from first author (potential founder)
        first_author = self.authors[0] if self.authors else "unknown"
        author_key = re.sub(r'[^a-z0-9]', '', first_author.lower())

        signal_id = f"arxiv_{self.arxiv_id.replace('.', '_')}"
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        return Signal(
            id=f"research_paper_{signal_hash}",
            signal_type="research_paper",
            confidence=confidence,
            source_api="arxiv",
            source_url=f"https://arxiv.org/abs/{self.arxiv_id}",
            source_response_hash=hashlib.sha256(
                f"{self.arxiv_id}:{self.updated_at.isoformat()}".encode()
            ).hexdigest()[:16],
            detected_at=self.published_at,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["arxiv"],
            raw_data={
                "canonical_key": f"arxiv_author:{author_key}",
                "arxiv_id": self.arxiv_id,
                "title": self.title[:200],
                "abstract": self.abstract[:500],
                "authors": self.authors[:5],
                "categories": self.categories,
                "affiliations": self.affiliations[:3],
                "pdf_url": self.pdf_url,
            }
        )


# =============================================================================
# COLLECTOR
# =============================================================================

class ArxivCollector(BaseCollector):
    """
    Collect research papers from ArXiv.

    Discovers academic research that might indicate potential startups
    or research commercialization opportunities.

    Usage:
        collector = ArxivCollector(
            categories=["cs.AI", "cs.LG"],
            store=signal_store,
        )
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        categories: Optional[List[str]] = None,
        store: Optional[SignalStore] = None,
        lookback_days: int = 30,
        max_results: int = 100,
        keywords: Optional[List[str]] = None,
    ):
        """
        Args:
            categories: ArXiv categories to search (default: thesis-relevant)
            store: SignalStore for persistence
            lookback_days: How far back to search
            max_results: Maximum papers to fetch
            keywords: Additional keywords to filter by
        """
        super().__init__(store=store, collector_name="arxiv")
        self.categories = categories or list(THESIS_CATEGORIES.keys())
        self.lookback_days = lookback_days
        self.max_results = max_results
        self.keywords = keywords or [
            "startup", "commercialization", "industry",
            "application", "deployment", "production",
        ]

    # BaseCollector provides __aenter__ and __aexit__
    # We use _fetch_with_retry for HTTP calls with retry + rate limiting

    async def _collect_signals(self) -> List[Signal]:
        """Collect ArXiv papers as signals."""
        papers = await self._fetch_papers()

        signals = []
        for paper in papers:
            # Save raw data and detect changes
            if self.asset_store:
                is_new, changes = await self._save_asset_with_change_detection(
                    source_type=self.SOURCE_TYPE,
                    external_id=paper.arxiv_id,
                    raw_data=paper.to_dict() if hasattr(paper, 'to_dict') else vars(paper),
                )

                # Skip unchanged papers
                if not is_new and not changes:
                    logger.debug(f"Skipping unchanged ArXiv paper: {paper.arxiv_id}")
                    continue

            signals.append(paper.to_signal())

        return signals

    async def _fetch_papers(self) -> List[ArxivPaper]:
        """Fetch recent papers from ArXiv API."""
        papers: List[ArxivPaper] = []

        # Build search query
        # Search by category and optional keywords
        category_query = " OR ".join([f"cat:{cat}" for cat in self.categories[:5]])
        keyword_query = " OR ".join([f'"{kw}"' for kw in self.keywords[:5]])

        search_query = f"({category_query})"
        if self.keywords:
            search_query = f"({category_query}) AND ({keyword_query})"

        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": self.max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        try:
            # Use _fetch_with_retry for automatic retry and rate limiting
            async def fetch_arxiv():
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(ARXIV_API, params=params)
                    response.raise_for_status()
                    return response.content

            # Acquire rate limit before request
            await self.rate_limiter.acquire()
            xml_content = await self._fetch_with_retry(fetch_arxiv)

            # Parse XML response
            root = ElementTree.fromstring(xml_content)

            # Define namespaces
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }

            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)

            for entry in root.findall("atom:entry", ns):
                # Parse arxiv ID
                id_elem = entry.find("atom:id", ns)
                if id_elem is None or id_elem.text is None:
                    continue

                arxiv_id = id_elem.text.split("/abs/")[-1]

                # Parse dates
                published_elem = entry.find("atom:published", ns)
                updated_elem = entry.find("atom:updated", ns)

                try:
                    published_at = datetime.fromisoformat(
                        published_elem.text.replace("Z", "+00:00")
                    ) if published_elem is not None and published_elem.text else datetime.now(timezone.utc)

                    updated_at = datetime.fromisoformat(
                        updated_elem.text.replace("Z", "+00:00")
                    ) if updated_elem is not None and updated_elem.text else published_at
                except ValueError:
                    published_at = datetime.now(timezone.utc)
                    updated_at = published_at

                # Skip old papers
                if published_at < cutoff_date:
                    continue

                # Parse title and abstract
                title_elem = entry.find("atom:title", ns)
                title = (title_elem.text or "").strip().replace("\n", " ") if title_elem is not None else ""

                summary_elem = entry.find("atom:summary", ns)
                abstract = (summary_elem.text or "").strip().replace("\n", " ") if summary_elem is not None else ""

                # Parse authors
                authors = []
                affiliations = []
                for author_elem in entry.findall("atom:author", ns):
                    name_elem = author_elem.find("atom:name", ns)
                    if name_elem is not None and name_elem.text:
                        authors.append(name_elem.text)

                    affil_elem = author_elem.find("arxiv:affiliation", ns)
                    if affil_elem is not None and affil_elem.text:
                        affiliations.append(affil_elem.text)

                # Parse categories
                categories = []
                for cat_elem in entry.findall("atom:category", ns):
                    term = cat_elem.get("term")
                    if term:
                        categories.append(term)

                # Get PDF link
                pdf_url = ""
                for link_elem in entry.findall("atom:link", ns):
                    if link_elem.get("title") == "pdf":
                        pdf_url = link_elem.get("href", "")
                        break

                paper = ArxivPaper(
                    arxiv_id=arxiv_id,
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    categories=categories,
                    published_at=published_at,
                    updated_at=updated_at,
                    pdf_url=pdf_url,
                    affiliations=affiliations,
                )
                papers.append(paper)

        except httpx.HTTPError as e:
            logger.error(f"ArXiv HTTP error: {e}")
        except ElementTree.ParseError as e:
            logger.error(f"ArXiv XML parse error: {e}")
        except Exception as e:
            logger.exception(f"ArXiv fetch error: {e}")

        logger.info(f"Fetched {len(papers)} ArXiv papers")
        return papers


# =============================================================================
# CLI
# =============================================================================

async def main():
    """CLI for testing ArXiv collector."""
    import argparse

    parser = argparse.ArgumentParser(description="ArXiv Collector")
    parser.add_argument(
        "--categories",
        nargs="+",
        default=["cs.AI", "cs.LG"],
        help="ArXiv categories to search"
    )
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--max", type=int, default=50, help="Max results")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    collector = ArxivCollector(
        categories=args.categories,
        lookback_days=args.days,
        max_results=args.max,
    )

    result = await collector.run(dry_run=True)

    print("\n" + "=" * 60)
    print("ARXIV COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")
    print(f"Categories searched: {', '.join(args.categories)}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
