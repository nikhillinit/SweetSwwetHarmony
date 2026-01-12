"""
PubMed E-utilities API Client for Digital Health Intelligence.

Provides async methods to search and fetch scientific publication data from
PubMed's E-utilities API.

API Details:
- Search URL: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
- Summary URL: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi
- Free, no API key required (but key increases rate limit)
- Rate limit: 3 requests/second without key, 10 with key
- Returns JSON with retmode=json parameter

Usage:
    client = PubMedClient()

    # Search by author name
    pubs = await client.search_by_author("Smith J", max_results=10)

    # Search by affiliation
    pubs = await client.search_by_affiliation("Harvard University", max_results=10)

    # Get specific publication by PMID
    pub = await client.get_publication("12345678")
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from typing import List, Optional

import httpx

from storage.health_enrichment import Publication

logger = logging.getLogger(__name__)

# PubMed E-utilities API base URLs
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PubMedClient:
    """
    Async client for PubMed E-utilities API.

    Provides methods to search publications by author or affiliation,
    and fetch individual publication details. Implements rate limiting for
    API compliance.

    Attributes:
        api_key: Optional API key for higher rate limits (10 req/sec vs 3 req/sec).
        rate_limit: Maximum requests per second (default: 3.0).
    """

    def __init__(self, api_key: Optional[str] = None, rate_limit: float = 3.0):
        """
        Initialize the PubMed client.

        Args:
            api_key: Optional API key for higher rate limits.
            rate_limit: Maximum requests per second (default: 3.0).
        """
        self.api_key = api_key
        self.rate_limit = rate_limit
        self._semaphore = asyncio.Semaphore(1)
        self._last_request_time: Optional[float] = None
        self._min_interval = 1.0 / rate_limit if rate_limit > 0 else 0

    async def _wait_for_rate_limit(self) -> None:
        """Wait to comply with rate limiting."""
        async with self._semaphore:
            if self._last_request_time is not None:
                elapsed = asyncio.get_event_loop().time() - self._last_request_time
                if elapsed < self._min_interval:
                    await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = asyncio.get_event_loop().time()

    async def search_by_author(
        self, author_name: str, max_results: int = 10
    ) -> List[Publication]:
        """
        Search publications by author name.

        Args:
            author_name: Name of the author to search for.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of Publication objects matching the author name.
        """
        # PubMed author search uses [Author] field
        query = f"{author_name}[Author]"
        return await self._search(query, max_results)

    async def search_by_affiliation(
        self, affiliation: str, max_results: int = 10
    ) -> List[Publication]:
        """
        Search publications by affiliation/institution.

        Args:
            affiliation: Name of the affiliation/institution to search for.
            max_results: Maximum number of results to return (default: 10).

        Returns:
            List of Publication objects matching the affiliation.
        """
        # PubMed affiliation search uses [Affiliation] field
        query = f"{affiliation}[Affiliation]"
        return await self._search(query, max_results)

    async def get_publication(self, pmid: str) -> Optional[Publication]:
        """
        Get a specific publication by its PubMed ID (PMID).

        Args:
            pmid: The PubMed identifier (e.g., "12345678").

        Returns:
            Publication object if found, None otherwise.
        """
        params = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "json",
        }

        if self.api_key:
            params["api_key"] = self.api_key

        try:
            await self._wait_for_rate_limit()

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(PUBMED_ESUMMARY_URL, params=params)
                response.raise_for_status()
                data = response.json()

                result = data.get("result", {})
                uids = result.get("uids", [])

                if not uids or pmid not in uids:
                    logger.warning(f"Publication not found: {pmid}")
                    return None

                article_data = result.get(pmid, {})
                return self._parse_article(article_data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Publication not found: {pmid}")
            else:
                logger.error(f"HTTP error fetching publication {pmid}: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error fetching publication {pmid}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching publication {pmid}: {e}")
            return None

    async def _search(self, query: str, max_results: int) -> List[Publication]:
        """
        Execute a search query against the PubMed API.

        Uses a two-step process:
        1. esearch to get list of PMIDs matching the query
        2. esummary to get article details for those PMIDs

        Args:
            query: PubMed search query with field tags.
            max_results: Maximum number of results to return.

        Returns:
            List of Publication objects from search results.
        """
        try:
            # Step 1: Search for PMIDs
            pmids = await self._esearch(query, max_results)

            if not pmids:
                logger.info(f"No publications found for query: {query}")
                return []

            # Step 2: Fetch article summaries
            publications = await self._esummary(pmids)

            logger.info(f"Found {len(publications)} publications")
            return publications

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error searching publications: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Request error searching publications: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error searching publications: {e}")
            return []

    async def _esearch(self, query: str, max_results: int) -> List[str]:
        """
        Execute esearch to get list of PMIDs.

        Args:
            query: PubMed search query.
            max_results: Maximum number of results.

        Returns:
            List of PMID strings.
        """
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": min(max_results, 100),
            "retmode": "json",
        }

        if self.api_key:
            params["api_key"] = self.api_key

        await self._wait_for_rate_limit()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(PUBMED_ESEARCH_URL, params=params)
            response.raise_for_status()
            data = response.json()

            esearch_result = data.get("esearchresult", {})
            return esearch_result.get("idlist", [])

    async def _esummary(self, pmids: List[str]) -> List[Publication]:
        """
        Execute esummary to get article details for PMIDs.

        Args:
            pmids: List of PMID strings.

        Returns:
            List of Publication objects.
        """
        if not pmids:
            return []

        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "json",
        }

        if self.api_key:
            params["api_key"] = self.api_key

        await self._wait_for_rate_limit()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(PUBMED_ESUMMARY_URL, params=params)
            response.raise_for_status()
            data = response.json()

            result = data.get("result", {})
            publications = []

            for pmid in pmids:
                article_data = result.get(pmid, {})
                if article_data:
                    try:
                        publication = self._parse_article(article_data)
                        if publication:
                            publications.append(publication)
                    except Exception as e:
                        logger.warning(f"Failed to parse article {pmid}: {e}")
                        continue

            return publications

    def _parse_article(self, article_data: dict) -> Optional[Publication]:
        """
        Parse an article from the API response into a Publication dataclass.

        Args:
            article_data: Raw article data from the esummary API response.

        Returns:
            Publication object, or None if required fields are missing.
        """
        try:
            pmid = article_data.get("uid", "")

            if not pmid:
                logger.warning("Article missing PMID, skipping")
                return None

            title = article_data.get("title", "")

            # Parse authors list into comma-separated string
            authors_list = article_data.get("authors", [])
            authors = None
            if authors_list:
                author_names = [a.get("name", "") for a in authors_list if a.get("name")]
                if author_names:
                    authors = ", ".join(author_names)

            # Journal (source field)
            journal = article_data.get("source")

            # Publication date
            pub_date = self._parse_date(article_data.get("pubdate"))

            # Citation count (pmcrefcount if available)
            citation_count = article_data.get("pmcrefcount")
            if citation_count is not None:
                try:
                    citation_count = int(citation_count)
                except (ValueError, TypeError):
                    citation_count = None

            return Publication(
                entity_id="",  # Will be set when saving to storage
                pmid=pmid,
                title=title,
                authors=authors,
                journal=journal,
                pub_date=pub_date,
                citation_count=citation_count,
                fetched_at=datetime.utcnow(),
            )

        except Exception as e:
            logger.error(f"Error parsing article data: {e}")
            return None

    def _parse_date(self, date_str: Optional[str]) -> Optional[date]:
        """
        Parse a date string from the API response.

        Handles various date formats from PubMed:
        - "2024 Jan 15"
        - "2024 Jan"
        - "2024"

        Args:
            date_str: Date string from API (e.g., "2024 Jan 15", "2024 Jan", "2024").

        Returns:
            datetime.date object, or None if parsing fails.
        """
        if not date_str:
            return None

        # Try various PubMed date formats
        formats = [
            "%Y %b %d",  # "2024 Jan 15"
            "%Y %b",     # "2024 Jan"
            "%Y",        # "2024"
            "%Y-%m-%d",  # ISO format
            "%Y-%m",     # "2024-01"
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue

        # Try extracting just the year as last resort
        year_match = re.match(r"(\d{4})", date_str)
        if year_match:
            try:
                return date(int(year_match.group(1)), 1, 1)
            except ValueError:
                pass

        logger.debug(f"Unable to parse date: {date_str}")
        return None
