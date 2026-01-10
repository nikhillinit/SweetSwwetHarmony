"""
Job Postings Collector - Strongest validation signal

when_to_use: When you have a domain and need to verify the company is actively
  hiring. Hiring = funded. Check Greenhouse and Lever ATS platforms.

API: Greenhouse Job Board API, Lever Postings API
Cost: FREE
Signal Strength: VERY HIGH (0.7-0.95)

Hiring signals are among the strongest indicators of:
1. The company exists and is active
2. The company has funding/revenue
3. The company is growing

Usage:
    collector = JobPostingsCollector(domains=["anthropic.com", "stripe.com"])
    result = await collector.run(dry_run=True)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)

# ATS API endpoints
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
LEVER_API = "https://api.lever.co/v0/postings"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class JobPostingSignal:
    """Job posting signal - strongest validation"""
    company_name: str
    company_domain: str
    ats_platform: str  # greenhouse, lever
    total_positions: int
    engineering_positions: int
    sample_titles: List[str] = field(default_factory=list)
    job_url: str = ""
    departments: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    posted_at: Optional[datetime] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def calculate_signal_score(self) -> float:
        """
        Hiring is always a strong signal.

        Scoring:
        - Base: 0.7 (hiring = definitely real company)
        - Boost for many positions: up to +0.15
        - Boost for engineering-heavy: +0.1
        """
        base = 0.7  # Hiring = strong baseline

        # Boost for position count
        if self.total_positions >= 10:
            base += 0.15
        elif self.total_positions >= 5:
            base += 0.1
        elif self.total_positions >= 2:
            base += 0.05

        # Engineering-heavy = tech company
        if self.total_positions > 0:
            eng_ratio = self.engineering_positions / self.total_positions
            if eng_ratio >= 0.5:
                base += 0.1
            elif eng_ratio >= 0.25:
                base += 0.05

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """Convert to verification gate Signal"""
        confidence = self.calculate_signal_score()
        domain = self.company_domain.replace("www.", "").lower()

        # Create unique signal ID
        signal_id = f"job_{self.ats_platform}_{domain.replace('.', '_')}"
        signal_hash = hashlib.sha256(signal_id.encode()).hexdigest()[:12]

        return Signal(
            id=f"hiring_signal_{signal_hash}",
            signal_type="hiring_signal",
            confidence=confidence,
            source_api=f"{self.ats_platform}_jobs",
            source_url=self.job_url,
            source_response_hash=hashlib.sha256(
                str(self.raw_data).encode()
            ).hexdigest()[:16],
            detected_at=self.posted_at or datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=[f"{self.ats_platform}_jobs"],
            raw_data={
                "canonical_key": f"domain:{domain}",
                "company_name": self.company_name,
                "company_domain": self.company_domain,
                "ats_platform": self.ats_platform,
                "total_positions": self.total_positions,
                "engineering_positions": self.engineering_positions,
                "sample_titles": self.sample_titles[:5],
                "departments": self.departments[:5],
                "locations": self.locations[:5],
            }
        )


# =============================================================================
# COLLECTOR
# =============================================================================

class JobPostingsCollector(BaseCollector):
    """
    Check if companies are hiring via ATS APIs.

    Supports:
    - Greenhouse Job Board API
    - Lever Postings API

    Both APIs are free, public, and don't require authentication.

    Usage:
        collector = JobPostingsCollector(domains=["anthropic.com", "stripe.com"])
        result = await collector.run(dry_run=True)
    """

    def __init__(
        self,
        domains: List[str],
        store: Optional[SignalStore] = None,
        retry_config: Optional[RetryConfig] = None,
        timeout: float = 30.0,
    ):
        """
        Args:
            domains: List of company domains to check for hiring
            store: Optional SignalStore for persistence
            retry_config: Configuration for retry behavior
            timeout: HTTP request timeout in seconds
        """
        super().__init__(
            store=store,
            collector_name="job_postings",
            retry_config=retry_config,
            api_name="job_postings",  # Unlimited rate (public APIs)
        )
        self.domains = domains
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *args):
        if self.client:
            await self.client.aclose()

    async def check_domain(self, domain: str) -> Optional[JobPostingSignal]:
        """
        Check if domain has job postings on Greenhouse or Lever.

        Tries both platforms and returns first match.

        Args:
            domain: Company domain (e.g., "stripe.com")

        Returns:
            JobPostingSignal if found, None otherwise
        """
        # Extract potential board ID from domain
        # e.g., "stripe.com" -> "stripe"
        # e.g., "acme-ai.com" -> try "acme-ai", "acmeai", "acme"
        board_ids = self._generate_board_ids(domain)

        for board_id in board_ids:
            # Try Greenhouse first (more common)
            signal = await self._check_greenhouse(board_id, domain)
            if signal:
                return signal

            # Try Lever
            signal = await self._check_lever(board_id, domain)
            if signal:
                return signal

            # Small delay between attempts
            await asyncio.sleep(0.1)

        return None

    def _generate_board_ids(self, domain: str) -> List[str]:
        """
        Generate possible board IDs from domain.

        Companies often use their domain name as their board ID,
        but variations exist.
        """
        # Extract base name from domain
        base = domain.split(".")[0].lower()

        candidates = [base]

        # If contains hyphen, also try without
        if "-" in base:
            candidates.append(base.replace("-", ""))

        # If contains underscore, also try without
        if "_" in base:
            candidates.append(base.replace("_", ""))

        # Common variations
        candidates.extend([
            f"{base}hq",
            f"{base}-careers",
            f"{base}careers",
        ])

        # Deduplicate while preserving order
        seen = set()
        return [x for x in candidates if not (x in seen or seen.add(x))]

    async def _check_greenhouse(
        self,
        board_id: str,
        domain: str
    ) -> Optional[JobPostingSignal]:
        """
        Check Greenhouse for jobs.

        Greenhouse Job Board API:
        GET https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs
        """
        try:
            url = f"{GREENHOUSE_API}/{board_id}/jobs"

            # Use BaseCollector's retry and rate-limiting
            async def do_request():
                response = await self.client.get(url)
                if response.status_code != 200:
                    # Return None for non-200 status (not found, etc.)
                    # Don't raise - this is expected behavior
                    return None
                return response.json()

            data = await self._fetch_with_retry(do_request)

            if data is None:
                return None

            jobs = data.get("jobs", [])

            if not jobs:
                return None

            # Count engineering positions
            eng_keywords = [
                "engineer", "developer", "software", "sre", "devops",
                "backend", "frontend", "fullstack", "infrastructure"
            ]
            eng_count = sum(
                1 for j in jobs
                if any(kw in j.get("title", "").lower() for kw in eng_keywords)
            )

            # Extract departments
            departments = list(set(
                j.get("departments", [{}])[0].get("name", "")
                for j in jobs
                if j.get("departments")
            ))

            # Extract locations
            locations = list(set(
                j.get("location", {}).get("name", "")
                for j in jobs
                if j.get("location")
            ))

            # Sample titles
            sample_titles = [j.get("title", "") for j in jobs[:5]]

            # Get first job URL for reference
            first_job = jobs[0]
            job_url = first_job.get("absolute_url", "")

            return JobPostingSignal(
                company_name=board_id.title(),
                company_domain=domain,
                ats_platform="greenhouse",
                total_positions=len(jobs),
                engineering_positions=eng_count,
                sample_titles=sample_titles,
                departments=departments,
                locations=locations,
                job_url=job_url,
                raw_data={
                    "board_id": board_id,
                    "job_count": len(jobs),
                    "meta": data.get("meta", {}),
                },
            )

        except httpx.HTTPError as e:
            logger.debug(f"Greenhouse HTTP error for {board_id}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Greenhouse check failed for {board_id}: {e}")
            return None

    async def _check_lever(
        self,
        company_id: str,
        domain: str
    ) -> Optional[JobPostingSignal]:
        """
        Check Lever for jobs.

        Lever Postings API:
        GET https://api.lever.co/v0/postings/{company_id}
        """
        try:
            url = f"{LEVER_API}/{company_id}"

            # Use BaseCollector's retry and rate-limiting
            async def do_request():
                response = await self.client.get(url)
                if response.status_code != 200:
                    # Return None for non-200 status (not found, etc.)
                    # Don't raise - this is expected behavior
                    return None
                return response.json()

            jobs = await self._fetch_with_retry(do_request)

            if jobs is None or not jobs:
                return None

            # Count engineering positions
            eng_keywords = [
                "engineer", "developer", "software", "sre", "devops",
                "backend", "frontend", "fullstack", "infrastructure"
            ]
            eng_count = sum(
                1 for j in jobs
                if any(kw in j.get("text", "").lower() for kw in eng_keywords)
            )

            # Extract categories (departments)
            departments = list(set(
                j.get("categories", {}).get("department", "")
                for j in jobs
                if j.get("categories", {}).get("department")
            ))

            # Extract locations
            locations = list(set(
                j.get("categories", {}).get("location", "")
                for j in jobs
                if j.get("categories", {}).get("location")
            ))

            # Sample titles
            sample_titles = [j.get("text", "") for j in jobs[:5]]

            # Get first job URL for reference
            first_job = jobs[0]
            job_url = first_job.get("hostedUrl", "")

            return JobPostingSignal(
                company_name=company_id.title(),
                company_domain=domain,
                ats_platform="lever",
                total_positions=len(jobs),
                engineering_positions=eng_count,
                sample_titles=sample_titles,
                departments=departments,
                locations=locations,
                job_url=job_url,
                raw_data={
                    "company_id": company_id,
                    "job_count": len(jobs),
                },
            )

        except httpx.HTTPError as e:
            logger.debug(f"Lever HTTP error for {company_id}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Lever check failed for {company_id}: {e}")
            return None

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect hiring signals from configured domains.

        Implements BaseCollector._collect_signals() abstract method.

        Returns:
            List of Signal objects for companies with active job postings
        """
        signals: List[Signal] = []

        for domain in self.domains:
            try:
                # Normalize domain
                clean_domain = domain.lower().strip()
                if clean_domain.startswith("http"):
                    from urllib.parse import urlparse
                    clean_domain = urlparse(clean_domain).netloc

                clean_domain = clean_domain.replace("www.", "")

                if not clean_domain:
                    continue

                # Use retry logic from BaseCollector
                posting_signal = await self.check_domain(clean_domain)

                if posting_signal:
                    # Save raw data and detect changes
                    if self.asset_store:
                        is_new, changes = await self._save_asset_with_change_detection(
                            source_type=self.SOURCE_TYPE,
                            external_id=f"{clean_domain}_{posting_signal.ats_platform}",
                            raw_data=posting_signal.to_dict() if hasattr(posting_signal, 'to_dict') else vars(posting_signal),
                        )

                        # Skip unchanged job postings
                        if not is_new and not changes:
                            logger.debug(f"Skipping unchanged job posting for: {clean_domain}")
                            continue

                    signals.append(posting_signal.to_signal())
                    logger.info(
                        f"Found {posting_signal.total_positions} jobs at "
                        f"{clean_domain} via {posting_signal.ats_platform}"
                    )

                # Rate limit courtesy between domains
                await asyncio.sleep(0.2)

            except Exception as e:
                error_msg = f"{domain}: {e}"
                self._errors.append(error_msg)
                logger.warning(f"Error checking {domain}: {e}")

        return signals


# =============================================================================
# CLI / TESTING
# =============================================================================

async def main():
    """CLI entry point for testing"""
    import argparse

    parser = argparse.ArgumentParser(description="Job Postings Collector")
    parser.add_argument(
        "domains",
        nargs="*",
        help="Domains to check (e.g., anthropic.com stripe.com)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    domains = args.domains or ["anthropic.com", "stripe.com", "openai.com"]

    print(f"Checking domains: {domains}")

    collector = JobPostingsCollector(domains=domains)
    result = await collector.run(dry_run=True)

    print("\n" + "=" * 60)
    print("JOB POSTINGS COLLECTOR RESULTS")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    if result.error_message:
        print(f"Errors: {result.error_message}")

    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
