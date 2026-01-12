"""
Tech Stack Enrichment Client for SaaS Intelligence.

Detects technology stack for domains including:
- Frontend frameworks (React, Vue, Angular)
- Backend technologies (Node.js, Python, Java)
- Cloud hosting (AWS, GCP, Azure)
- Analytics tools (Google Analytics, Mixpanel)
- CMS platforms (WordPress, Webflow)

Can integrate with BuiltWith, Wappalyzer, or similar services.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class TechStackResult:
    """Tech stack analysis result for a domain."""

    domain: str
    technologies: List[str]
    categories: Dict[str, List[str]]
    analytics: List[str]
    hosting: List[str]
    cdn: Optional[str] = None
    cms: Optional[str] = None
    ecommerce: Optional[str] = None
    javascript_frameworks: Optional[List[str]] = None
    programming_languages: Optional[List[str]] = None


class TechStackClient:
    """
    Detects technology stack for domains.

    Features:
    - Rate-limited requests to avoid throttling
    - Graceful error handling
    - Support for BuiltWith API integration
    - Debug logging for all operations
    """

    RATE_LIMIT_DELAY = 1.0  # seconds between requests

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize tech stack client.

        Args:
            api_key: Optional API key for BuiltWith or similar service.
        """
        self.api_key = api_key
        self._last_request_time = 0.0
        logger.debug("TechStackClient initialized")

    async def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    async def _fetch_tech_stack(self, domain: str) -> TechStackResult:
        """
        Fetch tech stack for a domain.

        Args:
            domain: Domain to analyze.

        Returns:
            TechStackResult with detected technologies.
        """
        await self._rate_limit()

        logger.debug(f"Fetching tech stack for: {domain}")

        # Placeholder implementation - would integrate with BuiltWith or Wappalyzer
        # For now, return empty result (tests mock this method)
        return TechStackResult(
            domain=domain,
            technologies=[],
            categories={},
            analytics=[],
            hosting=[]
        )

    async def analyze(self, domain: str) -> TechStackResult:
        """
        Analyze tech stack for a domain.

        Args:
            domain: Domain to analyze.

        Returns:
            TechStackResult with detected technologies.
        """
        try:
            result = await self._fetch_tech_stack(domain)
            logger.debug(f"Analyzed tech stack for {domain}: {len(result.technologies)} technologies")
            return result
        except Exception as e:
            logger.error(f"Error analyzing {domain}: {e}")
            return TechStackResult(
                domain=domain,
                technologies=[],
                categories={},
                analytics=[],
                hosting=[]
            )

    async def analyze_batch(self, domains: List[str]) -> List[TechStackResult]:
        """
        Analyze tech stack for multiple domains.

        Args:
            domains: List of domains to analyze.

        Returns:
            List of TechStackResult objects.
        """
        results = []
        for domain in domains:
            result = await self.analyze(domain)
            results.append(result)
        return results

    def _categorize_technologies(self, technologies: List[str]) -> Dict[str, List[str]]:
        """
        Categorize technologies by type.

        Args:
            technologies: List of detected technologies.

        Returns:
            Dict mapping category names to technology lists.
        """
        # Category mappings
        frontend = ["React", "Vue", "Angular", "Svelte", "jQuery"]
        backend = ["Node.js", "Python", "Ruby", "Java", "Go", "PHP"]
        databases = ["PostgreSQL", "MySQL", "MongoDB", "Redis"]
        cloud = ["AWS", "GCP", "Azure", "Heroku", "Vercel"]

        categories: Dict[str, List[str]] = {}

        for tech in technologies:
            if tech in frontend:
                categories.setdefault("frontend", []).append(tech)
            elif tech in backend:
                categories.setdefault("backend", []).append(tech)
            elif tech in databases:
                categories.setdefault("database", []).append(tech)
            elif tech in cloud:
                categories.setdefault("cloud", []).append(tech)
            else:
                categories.setdefault("other", []).append(tech)

        return categories
