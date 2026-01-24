"""
URL Profiler - Extract structured company information from websites.

Sprint 3 MVP: Paste URL -> structured profile with evidence -> stored as claims

Usage:
    from profilers import URLProfiler
    from storage.signal_store import SignalStore

    store = SignalStore()
    await store.initialize()

    profiler = URLProfiler(signal_store=store)
    profile = await profiler.profile("https://acme.ai")

    # Profile contains:
    # - canonical_key: "domain:acme.ai"
    # - claims: List of ClaimWithEvidence
    # - source_urls: URLs that were fetched
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlparse, urljoin

import httpx

if TYPE_CHECKING:
    from storage.signal_store import SignalStore
    from storage.claim_store import ClaimStore, ClaimWithEvidence
    from storage.source_asset_store import SourceAssetStore

logger = logging.getLogger(__name__)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ExtractedField:
    """A single extracted field with evidence and confidence."""
    value: str                          # Full extracted value
    short_phrase: str                   # 3-5 word noun phrase summary
    confidence: float                   # 0.0-1.0
    evidence_snippet: Optional[str]     # Verbatim quote from source
    source_url: str                     # URL where evidence was found
    extraction_method: str = "llm"      # "llm" or "heuristic"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "short_phrase": self.short_phrase,
            "confidence": self.confidence,
            "evidence_snippet": self.evidence_snippet,
            "source_url": self.source_url,
            "extraction_method": self.extraction_method,
        }


@dataclass
class PageFetchResult:
    """Result of fetching a single page."""
    url: str
    path: str                           # Relative path (e.g., "/about")
    status_code: int
    html_content: str
    text_content: str                   # Cleaned text extracted from HTML
    fetch_time: datetime
    error: Optional[str] = None
    content_hash: Optional[str] = None  # SHA256 of content for change detection

    @property
    def success(self) -> bool:
        return self.status_code == 200 and self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "path": self.path,
            "status_code": self.status_code,
            "text_content_length": len(self.text_content),
            "fetch_time": self.fetch_time.isoformat(),
            "error": self.error,
            "content_hash": self.content_hash,
            "success": self.success,
        }


@dataclass
class ProfileExtractionResult:
    """Result of extracting structured information from fetched pages."""
    problem_solved: Optional[ExtractedField] = None
    target_customer: Optional[ExtractedField] = None
    business_model: Optional[ExtractedField] = None
    pricing_model: Optional[ExtractedField] = None
    company_name: Optional[ExtractedField] = None
    category_hints: List[str] = field(default_factory=list)

    # Metadata
    extraction_method: str = "llm"      # Primary method used
    extraction_time_ms: int = 0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    @property
    def fields_extracted(self) -> int:
        """Count of non-None extracted fields."""
        count = 0
        if self.problem_solved:
            count += 1
        if self.target_customer:
            count += 1
        if self.business_model:
            count += 1
        if self.pricing_model:
            count += 1
        if self.company_name:
            count += 1
        if self.category_hints:
            count += 1
        return count

    @property
    def is_complete(self) -> bool:
        """Check if essential fields are extracted."""
        return self.problem_solved is not None and self.target_customer is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem_solved": self.problem_solved.to_dict() if self.problem_solved else None,
            "target_customer": self.target_customer.to_dict() if self.target_customer else None,
            "business_model": self.business_model.to_dict() if self.business_model else None,
            "pricing_model": self.pricing_model.to_dict() if self.pricing_model else None,
            "company_name": self.company_name.to_dict() if self.company_name else None,
            "category_hints": self.category_hints,
            "extraction_method": self.extraction_method,
            "extraction_time_ms": self.extraction_time_ms,
            "fields_extracted": self.fields_extracted,
            "is_complete": self.is_complete,
        }


@dataclass
class CompanyProfile:
    """Complete company profile with claims and metadata."""
    canonical_key: str                  # e.g., "domain:acme.ai"
    domain: str                         # e.g., "acme.ai"
    claims: List["ClaimWithEvidence"] = field(default_factory=list)
    extraction_result: Optional[ProfileExtractionResult] = None
    source_urls: List[str] = field(default_factory=list)
    pages_fetched: List[PageFetchResult] = field(default_factory=list)
    profile_complete: bool = False
    last_profiled_at: Optional[datetime] = None
    error: Optional[str] = None

    @property
    def claim_count(self) -> int:
        return len(self.claims)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_key": self.canonical_key,
            "domain": self.domain,
            "claim_count": self.claim_count,
            "source_urls": self.source_urls,
            "pages_fetched": [p.to_dict() for p in self.pages_fetched],
            "profile_complete": self.profile_complete,
            "last_profiled_at": self.last_profiled_at.isoformat() if self.last_profiled_at else None,
            "error": self.error,
            "extraction_result": self.extraction_result.to_dict() if self.extraction_result else None,
        }


# =============================================================================
# URL UTILITIES
# =============================================================================

def parse_base_url(url: str) -> str:
    """
    Extract base URL (scheme + netloc) from a URL.

    Args:
        url: Full URL (e.g., "https://acme.ai/about?ref=123")

    Returns:
        Base URL (e.g., "https://acme.ai")

    Examples:
        >>> parse_base_url("https://acme.ai/about")
        'https://acme.ai'
        >>> parse_base_url("http://www.example.com:8080/path")
        'http://www.example.com:8080'
    """
    parsed = urlparse(url)

    # Ensure scheme is present
    if not parsed.scheme:
        # Default to https
        parsed = urlparse(f"https://{url}")

    return f"{parsed.scheme}://{parsed.netloc}"


def extract_domain(url: str) -> str:
    """
    Extract clean domain from URL (without www prefix).

    Args:
        url: URL string

    Returns:
        Clean domain (e.g., "acme.ai")

    Examples:
        >>> extract_domain("https://www.acme.ai/about")
        'acme.ai'
        >>> extract_domain("http://sub.domain.example.com")
        'sub.domain.example.com'
    """
    parsed = urlparse(url)

    # Handle missing scheme
    if not parsed.netloc:
        parsed = urlparse(f"https://{url}")

    domain = parsed.netloc.lower()

    # Remove port if present
    if ":" in domain:
        domain = domain.split(":")[0]

    # Remove www prefix
    if domain.startswith("www."):
        domain = domain[4:]

    return domain


def generate_canonical_key(url: str) -> str:
    """
    Generate canonical key for deduplication.

    Args:
        url: URL string

    Returns:
        Canonical key (e.g., "domain:acme.ai")

    Examples:
        >>> generate_canonical_key("https://www.acme.ai/about")
        'domain:acme.ai'
        >>> generate_canonical_key("acme.ai")
        'domain:acme.ai'
    """
    domain = extract_domain(url)
    return f"domain:{domain}"


def normalize_path(path: str) -> str:
    """
    Normalize a URL path for consistency.

    Args:
        path: URL path (e.g., "/about/", "about", "/About")

    Returns:
        Normalized path (e.g., "/about")

    Examples:
        >>> normalize_path("about")
        '/about'
        >>> normalize_path("/About/")
        '/about'
        >>> normalize_path("")
        '/'
    """
    if not path:
        return "/"

    # Ensure leading slash
    if not path.startswith("/"):
        path = "/" + path

    # Remove trailing slash (except for root)
    if path != "/" and path.endswith("/"):
        path = path[:-1]

    # Lowercase
    path = path.lower()

    return path


def build_page_urls(base_url: str, paths: List[str]) -> List[str]:
    """
    Build full URLs from base URL and paths.

    Args:
        base_url: Base URL (e.g., "https://acme.ai")
        paths: List of paths (e.g., ["/", "/about", "/pricing"])

    Returns:
        List of full URLs
    """
    urls = []
    for path in paths:
        normalized = normalize_path(path)
        full_url = urljoin(base_url, normalized)
        urls.append(full_url)
    return urls


def hash_content(content: str) -> str:
    """
    Generate SHA256 hash of content for change detection.

    Args:
        content: Text content

    Returns:
        First 16 characters of SHA256 hash
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# URL PROFILER
# =============================================================================

# Default pages to fetch
DEFAULT_PAGES = ["/", "/about", "/pricing", "/team"]

# HTTP settings
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = "HarmonicBot/1.0 (+https://github.com/harmonic-discovery)"


class URLProfiler:
    """
    Main URL Profiler class.

    Coordinates fetching, extraction, and claim storage.
    """

    def __init__(
        self,
        signal_store: Optional["SignalStore"] = None,
        asset_store: Optional["SourceAssetStore"] = None,
        claim_store: Optional["ClaimStore"] = None,
        pages_to_fetch: Optional[List[str]] = None,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        """
        Initialize URL Profiler.

        Args:
            signal_store: Signal store for database access
            asset_store: Asset store for caching fetched pages
            claim_store: Claim store for saving extracted claims
            pages_to_fetch: List of paths to fetch (default: /, /about, /pricing, /team)
            timeout: HTTP timeout in seconds
            user_agent: User agent string for requests
        """
        self.signal_store = signal_store
        self.asset_store = asset_store
        self._claim_store = claim_store
        self.pages_to_fetch = pages_to_fetch or DEFAULT_PAGES
        self.timeout = timeout
        self.user_agent = user_agent
        self._client: Optional[httpx.AsyncClient] = None
        self._llm_extractor = None
        self._heuristic_extractor = None

    @property
    def claim_store(self) -> Optional["ClaimStore"]:
        """Lazy-load claim store from signal store if not provided."""
        if self._claim_store is None and self.signal_store is not None:
            from storage.claim_store import ClaimStore
            self._claim_store = ClaimStore(self.signal_store)
        return self._claim_store

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        )
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def profile(self, url: str, force_refresh: bool = False) -> CompanyProfile:
        """
        Profile a company from its URL.

        Args:
            url: Company website URL
            force_refresh: If True, re-fetch even if cached

        Returns:
            CompanyProfile with extracted claims and evidence
        """
        canonical_key = generate_canonical_key(url)
        domain = extract_domain(url)
        base_url = parse_base_url(url)
        now = datetime.now(timezone.utc)

        logger.info(f"Profiling {domain} (canonical_key={canonical_key})")

        # Check for existing profile if not forcing refresh
        if not force_refresh and self.claim_store:
            existing_claims = await self.claim_store.get_claims_for_entity(canonical_key)
            if existing_claims:
                logger.info(f"Found {len(existing_claims)} existing claims for {canonical_key}")
                # Could return cached profile here, but for MVP always re-fetch

        # Fetch pages
        pages = await self._fetch_pages(base_url)
        successful_pages = [p for p in pages if p.success]

        if not successful_pages:
            return CompanyProfile(
                canonical_key=canonical_key,
                domain=domain,
                pages_fetched=pages,
                error="Failed to fetch any pages",
                last_profiled_at=now,
            )

        # Store assets for diffing (if asset store available)
        if self.asset_store:
            await self._save_page_assets(canonical_key, pages)

        # Extract structured information
        extraction_result = await self._extract_profile(successful_pages)

        # Save to claim store
        claims = []
        if self.claim_store and extraction_result:
            claims = await self._save_to_claim_store(canonical_key, extraction_result)

        return CompanyProfile(
            canonical_key=canonical_key,
            domain=domain,
            claims=claims,
            extraction_result=extraction_result,
            source_urls=[p.url for p in successful_pages],
            pages_fetched=pages,
            profile_complete=extraction_result.is_complete if extraction_result else False,
            last_profiled_at=now,
        )

    async def _fetch_pages(self, base_url: str) -> List[PageFetchResult]:
        """
        Fetch multiple pages in parallel.

        Args:
            base_url: Base URL for the site

        Returns:
            List of PageFetchResult
        """
        urls = build_page_urls(base_url, self.pages_to_fetch)

        # Create client if not in context manager
        client = self._client
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            )

        try:
            tasks = [
                self._fetch_single_page(client, url, path)
                for url, path in zip(urls, self.pages_to_fetch)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Convert exceptions to PageFetchResult with error
            processed = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    processed.append(PageFetchResult(
                        url=urls[i],
                        path=self.pages_to_fetch[i],
                        status_code=0,
                        html_content="",
                        text_content="",
                        fetch_time=datetime.now(timezone.utc),
                        error=str(result),
                    ))
                else:
                    processed.append(result)

            return processed
        finally:
            if self._client is None:
                await client.aclose()

    async def _fetch_single_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        path: str,
    ) -> PageFetchResult:
        """
        Fetch a single page.

        Args:
            client: httpx client
            url: Full URL to fetch
            path: Relative path for metadata

        Returns:
            PageFetchResult
        """
        fetch_time = datetime.now(timezone.utc)

        try:
            response = await client.get(url)
            html_content = response.text

            # Extract text content
            text_content = self._extract_text_from_html(html_content)
            content_hash_val = hash_content(text_content)

            return PageFetchResult(
                url=str(response.url),  # May differ from requested URL due to redirects
                path=path,
                status_code=response.status_code,
                html_content=html_content,
                text_content=text_content,
                fetch_time=fetch_time,
                content_hash=content_hash_val,
            )
        except httpx.TimeoutException:
            return PageFetchResult(
                url=url,
                path=path,
                status_code=0,
                html_content="",
                text_content="",
                fetch_time=fetch_time,
                error="Request timed out",
            )
        except httpx.RequestError as e:
            return PageFetchResult(
                url=url,
                path=path,
                status_code=0,
                html_content="",
                text_content="",
                fetch_time=fetch_time,
                error=f"Request error: {str(e)}",
            )
        except Exception as e:
            return PageFetchResult(
                url=url,
                path=path,
                status_code=0,
                html_content="",
                text_content="",
                fetch_time=fetch_time,
                error=f"Unexpected error: {str(e)}",
            )

    def _extract_text_from_html(self, html: str) -> str:
        """
        Extract clean text from HTML.

        Uses trafilatura if available, falls back to regex stripping.

        Args:
            html: HTML content

        Returns:
            Clean text content
        """
        try:
            import trafilatura
            result = trafilatura.extract(html)
            if result:
                return result
        except ImportError:
            logger.debug("trafilatura not installed, using regex fallback")
        except Exception as e:
            logger.warning(f"trafilatura extraction failed: {e}")

        # Fallback: basic regex stripping
        # Remove script and style elements
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)

        # Decode common entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')

        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    async def _save_page_assets(
        self,
        canonical_key: str,
        pages: List[PageFetchResult],
    ) -> None:
        """
        Save fetched pages as assets for change detection.

        Args:
            canonical_key: Entity canonical key
            pages: List of fetched pages
        """
        if not self.asset_store:
            return

        for page in pages:
            if not page.success:
                continue

            try:
                from storage.source_asset_store import SourceAsset

                asset = SourceAsset(
                    source_type="url_page",
                    external_id=f"{canonical_key}:{page.path}",
                    raw_payload={
                        "html": page.html_content[:50000],  # Limit size
                        "text": page.text_content[:20000],
                        "url": page.url,
                        "content_hash": page.content_hash,
                    },
                    fetched_at=page.fetch_time,
                )
                await self.asset_store.save_asset(asset)
            except Exception as e:
                logger.warning(f"Failed to save asset for {page.url}: {e}")

    async def _extract_profile(
        self,
        pages: List[PageFetchResult],
    ) -> ProfileExtractionResult:
        """
        Extract structured profile from fetched pages.

        Uses LLM extraction with heuristic fallback.

        Args:
            pages: List of successfully fetched pages

        Returns:
            ProfileExtractionResult
        """
        # Combine text from all pages
        combined_text = self._combine_page_texts(pages)

        if not combined_text.strip():
            return ProfileExtractionResult(
                extraction_method="none",
                extraction_time_ms=0,
            )

        # Try LLM extraction first
        try:
            from profilers.extractors.llm_extractor import ProfileLLMExtractor

            if self._llm_extractor is None:
                self._llm_extractor = ProfileLLMExtractor()

            source_url = pages[0].url if pages else ""
            result = await self._llm_extractor.extract(combined_text, source_url)

            if result and result.fields_extracted > 0:
                return result
        except ImportError:
            logger.debug("LLM extractor not available")
        except Exception as e:
            logger.warning(f"LLM extraction failed: {e}")

        # Fallback to heuristic extraction
        try:
            from profilers.extractors.heuristic_extractor import ProfileHeuristicExtractor

            if self._heuristic_extractor is None:
                self._heuristic_extractor = ProfileHeuristicExtractor()

            return self._heuristic_extractor.extract(pages)
        except ImportError:
            logger.debug("Heuristic extractor not available")
        except Exception as e:
            logger.warning(f"Heuristic extraction failed: {e}")

        return ProfileExtractionResult(
            extraction_method="failed",
            extraction_time_ms=0,
        )

    def _combine_page_texts(self, pages: List[PageFetchResult]) -> str:
        """
        Combine text from multiple pages with section markers.

        Args:
            pages: List of page fetch results

        Returns:
            Combined text with section headers
        """
        sections = []

        for page in pages:
            if not page.success or not page.text_content.strip():
                continue

            path_name = page.path.strip("/") or "homepage"
            section = f"=== {path_name.upper()} PAGE ===\n{page.text_content}\n"
            sections.append(section)

        return "\n".join(sections)

    async def _save_to_claim_store(
        self,
        canonical_key: str,
        extraction: ProfileExtractionResult,
    ) -> List["ClaimWithEvidence"]:
        """
        Save extraction results to claim store.

        Args:
            canonical_key: Entity canonical key
            extraction: Extraction results

        Returns:
            List of created ClaimWithEvidence objects
        """
        if not self.claim_store:
            return []

        claims = []

        # Map extracted fields to predicates
        field_mappings = [
            ("problem_solved", extraction.problem_solved),
            ("target_customer", extraction.target_customer),
            ("business_model", extraction.business_model),
            ("pricing_model", extraction.pricing_model),
            ("company_name", extraction.company_name),
        ]

        for predicate, field_value in field_mappings:
            if field_value is None:
                continue

            try:
                # Save extraction (raw evidence)
                ext_id = await self.claim_store.save_extraction(
                    entity_key=canonical_key,
                    extractor_name="url_profiler",
                    extractor_version="1.0",
                    predicate_hint=predicate,
                    raw_text=field_value.value,
                    source_snippet=field_value.evidence_snippet,
                    source_url=field_value.source_url,
                )

                # Save claim (canonicalized)
                claim_id = await self.claim_store.save_claim(
                    entity_key=canonical_key,
                    predicate=predicate,
                    value=field_value.short_phrase,
                    confidence=field_value.confidence,
                    extraction_ids=[ext_id],
                )

                # Retrieve claim with evidence
                claim_with_evidence = await self.claim_store.get_claim_with_evidence(claim_id)
                if claim_with_evidence:
                    claims.append(claim_with_evidence)

            except Exception as e:
                logger.error(f"Failed to save claim for {predicate}: {e}")

        # Handle category hints (multi-value)
        for category in extraction.category_hints:
            try:
                ext_id = await self.claim_store.save_extraction(
                    entity_key=canonical_key,
                    extractor_name="url_profiler",
                    extractor_version="1.0",
                    predicate_hint="industry",
                    raw_text=category,
                    source_url=extraction.problem_solved.source_url if extraction.problem_solved else "",
                )

                await self.claim_store.save_claim(
                    entity_key=canonical_key,
                    predicate="industry",
                    value=category,
                    confidence=0.6,  # Category hints are lower confidence
                    extraction_ids=[ext_id],
                )
            except Exception as e:
                logger.debug(f"Failed to save category claim: {e}")

        return claims
