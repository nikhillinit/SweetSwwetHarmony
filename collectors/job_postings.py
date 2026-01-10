"""
Job Postings Collector - Strongest validation signal for funded startups.

when_to_use: When you have a domain and need to verify the company is actively
  hiring. Hiring = funded. Check Greenhouse, Lever, Ashby, and Workable ATS platforms.

API: Greenhouse Board API, Lever Postings API, Ashby Public API, Workable Widget API
Cost: FREE (all public APIs)
Rate Limits: Generous - no authentication required
Signal Strength: VERY HIGH (0.7-0.95)

Validation Results (Press On Ventures Portfolio):
  - 10Beauty: Greenhouse (job-boards.greenhouse.io/10beauty)
  - Cofertility: Greenhouse (job-boards.greenhouse.io/cofertility)
  - Jacob Bars: Workable (apply.workable.com/eatjacob)

Coverage: Would catch 6/7 consumer-thesis funded deals (86%)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import httpx

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import RetryConfig
from storage.signal_store import SignalStore
from verification.verification_gate_v2 import Signal, VerificationStatus

if TYPE_CHECKING:
    from storage.source_asset_store import SourceAssetStore

logger = logging.getLogger(__name__)


# =============================================================================
# ATS API ENDPOINTS
# =============================================================================

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
LEVER_API = "https://api.lever.co/v0/postings"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board"
WORKABLE_CAREERS_URL = "https://apply.workable.com"

# Engineering role keywords for classification
ENGINEERING_KEYWORDS = frozenset([
    "engineer", "developer", "software", "sre", "devops", "backend",
    "frontend", "fullstack", "full-stack", "full stack", "platform",
    "infrastructure", "data scientist", "machine learning", "ml engineer",
    "ios", "android", "mobile", "security", "cloud", "architect"
])


# =============================================================================
# DATETIME PARSING UTILITIES
# =============================================================================

def _parse_dt(value: Any) -> Optional[datetime]:
    """
    Parse common ATS timestamp formats.

    Handles:
    - ISO 8601 strings: '2026-01-09T12:34:56Z', '2026-01-09T12:34:56+00:00'
    - Epoch seconds: 1736422496
    - Epoch milliseconds: 1736422496000

    Args:
        value: Raw timestamp from API response

    Returns:
        Timezone-aware datetime or None if unparseable
    """
    if value is None:
        return None

    # Handle numeric timestamps (epoch)
    if isinstance(value, (int, float)):
        v = float(value)
        try:
            # Heuristic: >1e12 suggests milliseconds, >1e9 suggests seconds
            if v > 1e12:
                return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc)
            if v > 1e9:
                return datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
        return None

    # Handle string timestamps
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            # Handle 'Z' suffix (ISO 8601 UTC indicator)
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass

        # Try common formats
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    return None


def _calculate_posting_age_days(posted_at: Optional[datetime]) -> Optional[int]:
    """Calculate days since posting, or None if unknown."""
    if not posted_at:
        return None
    try:
        delta = datetime.now(timezone.utc) - posted_at
        return max(0, delta.days)
    except Exception:
        return None


# =============================================================================
# JOB POSTING SIGNAL DATACLASS
# =============================================================================

@dataclass
class JobPostingSignal:
    """
    Represents a job posting signal from any ATS platform.

    This is an intermediate representation before conversion to the
    verification gate Signal format.
    """
    company_name: str
    company_domain: str
    ats_platform: str  # greenhouse, lever, ashby, workable
    total_positions: int
    engineering_positions: int
    sample_titles: List[str] = field(default_factory=list)
    job_url: str = ""
    departments: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    oldest_posting_at: Optional[datetime] = None
    newest_posting_at: Optional[datetime] = None
    raw_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Deterministic, JSON-safe representation for SourceAssetStore change detection.

        CRITICAL: All lists must be sorted and datetimes serialized to ISO format
        to prevent false "changes" from ordering differences.

        Returns:
            Stable dictionary suitable for JSON serialization and hashing
        """
        # Normalize domain
        domain = self.company_domain.lower().replace("www.", "").strip()

        # Calculate oldest posting age for ghost job detection
        oldest_age = _calculate_posting_age_days(self.oldest_posting_at)

        return {
            "company_name": self.company_name.strip(),
            "company_domain": domain,
            "ats_platform": self.ats_platform,
            "total_positions": int(self.total_positions),
            "engineering_positions": int(self.engineering_positions),
            # Stable sorting - slice AFTER sort for determinism
            "sample_titles": sorted([t.strip() for t in self.sample_titles if t])[:10],
            "departments": sorted([d.strip() for d in self.departments if d])[:25],
            "locations": sorted([loc.strip() for loc in self.locations if loc])[:25],
            "job_url": self.job_url,
            # ISO format for datetime stability
            "oldest_posting_at": self.oldest_posting_at.isoformat() if self.oldest_posting_at else None,
            "newest_posting_at": self.newest_posting_at.isoformat() if self.newest_posting_at else None,
            # Include age for change detection (ghost job tracking)
            "oldest_posting_age_days": oldest_age,
            # Stable snapshot from collector methods
            "raw_snapshot": self.raw_snapshot,
        }

    def calculate_signal_score(self) -> float:
        """
        Calculate confidence score for hiring signal.

        Base score is high (0.7) because hiring = funded.
        Boosted by position count and engineering ratio.
        Penalized by stale postings (ghost job dampener).

        Returns:
            Confidence score between 0.0 and 1.0
        """
        base = 0.70

        # Boost for position count (more hiring = more signal)
        if self.total_positions >= 10:
            base += 0.15
        elif self.total_positions >= 5:
            base += 0.10
        elif self.total_positions >= 2:
            base += 0.05

        # Engineering-heavy = tech company (stronger thesis fit)
        if self.total_positions > 0:
            eng_ratio = self.engineering_positions / self.total_positions
            if eng_ratio >= 0.5:
                base += 0.10
            elif eng_ratio >= 0.25:
                base += 0.05

        # Ghost Job Dampener: penalize stale postings
        # Jobs posted 90+ days ago without updates suggest fake/stale listings
        oldest_age = _calculate_posting_age_days(self.oldest_posting_at)
        if oldest_age is not None:
            if oldest_age > 180:
                base *= 0.50  # Severe penalty - likely ghost jobs
                logger.debug(f"Ghost job dampener (severe): {self.company_domain} ({oldest_age} days)")
            elif oldest_age > 90:
                base *= 0.70  # Significant penalty
                logger.debug(f"Ghost job dampener: {self.company_domain} ({oldest_age} days)")
            elif oldest_age > 60:
                base *= 0.85  # Mild penalty

        return min(base, 1.0)

    def to_signal(self) -> Signal:
        """
        Convert to verification gate Signal format.

        Follows BaseCollector patterns:
        - canonical_key in raw_data
        - canonical_key_candidates for multi-source resolution
        - Deterministic source_response_hash

        Returns:
            Signal object compatible with verification gate
        """
        confidence = self.calculate_signal_score()
        domain = self.company_domain.lower().replace("www.", "").strip()

        # Build stable signal ID
        signal_id_base = f"job_{self.ats_platform}_{domain.replace('.', '_')}"
        signal_hash = hashlib.sha256(signal_id_base.encode()).hexdigest()[:12]

        # Deterministic response hash for deduplication
        response_hash = hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]

        # Canonical key candidates for entity resolution
        canonical_key = f"domain:{domain}"
        canonical_key_candidates = [
            canonical_key,
            f"company_name:{self.company_name.lower().strip()}",
        ]

        # Add ATS-specific identifier
        if self.raw_snapshot.get("board_id"):
            canonical_key_candidates.append(
                f"ats_board:{self.ats_platform}:{self.raw_snapshot['board_id']}"
            )

        return Signal(
            id=f"hiring_signal_{signal_hash}",
            signal_type="hiring_signal",
            confidence=confidence,
            source_api=f"{self.ats_platform}_jobs",
            source_url=self.job_url,
            source_response_hash=response_hash,
            detected_at=self.newest_posting_at or datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=[f"{self.ats_platform}_jobs"],
            raw_data={
                # Required by BaseCollector pattern
                "canonical_key": canonical_key,
                "canonical_key_candidates": canonical_key_candidates,
                # Signal-specific data
                "company_name": self.company_name,
                "company_domain": domain,
                "ats_platform": self.ats_platform,
                "total_positions": self.total_positions,
                "engineering_positions": self.engineering_positions,
                "sample_titles": sorted([t for t in self.sample_titles if t])[:5],
                "departments": sorted([d for d in self.departments if d])[:5],
                "locations": sorted([loc for loc in self.locations if loc])[:5],
                "oldest_posting_age_days": _calculate_posting_age_days(self.oldest_posting_at),
            }
        )


# =============================================================================
# JOB POSTINGS COLLECTOR
# =============================================================================

class JobPostingsCollector(BaseCollector):
    """
    Check if companies are hiring via public ATS APIs.

    Supports:
    - Greenhouse (most common, used by 10Beauty, Cofertility)
    - Lever (common for mid-stage)
    - Ashby (favored by YC/seed startups)
    - Workable (used by Jacob Bars)

    Usage:
        collector = JobPostingsCollector(
            domains=["anthropic.com", "openai.com"],
            store=signal_store,
            asset_store=asset_store,
        )
        async with collector:
            result = await collector.run(dry_run=False)

    Signal Strength: VERY HIGH (0.7-0.95)
    - Hiring = funded
    - Multiple positions = growth stage
    - Engineering-heavy = tech company
    """

    def __init__(
        self,
        domains: List[str],
        store: Optional[SignalStore] = None,
        asset_store: Optional["SourceAssetStore"] = None,
        retry_config: Optional[RetryConfig] = None,
        timeout: float = 30.0,
    ):
        """
        Args:
            domains: List of company domains to check for job postings
            store: Optional SignalStore for signal persistence
            asset_store: Optional SourceAssetStore for change detection
            retry_config: Retry configuration (default: 3 retries with backoff)
            timeout: HTTP request timeout in seconds
        """
        super().__init__(
            store=store,
            collector_name="job_postings",
            retry_config=retry_config or RetryConfig(max_retries=3, backoff_base=2.0),
            api_name="job_postings",
            asset_store=asset_store,
        )
        self.domains = domains
        self.timeout = timeout

    async def check_domain(self, domain: str) -> Optional[JobPostingSignal]:
        """
        Check all ATS platforms for job postings at the given domain.

        Tries each platform in order of prevalence:
        1. Greenhouse (most common)
        2. Ashby (YC/seed favorites)
        3. Lever (mid-stage)
        4. Workable (SMB/consumer)

        Args:
            domain: Company domain (e.g., "anthropic.com")

        Returns:
            JobPostingSignal if found, None otherwise
        """
        board_ids = self._generate_board_ids(domain)

        for board_id in board_ids:
            # 1. Greenhouse (most common for tech startups)
            signal = await self._check_greenhouse(board_id, domain)
            if signal:
                return signal

            # 2. Ashby (high value for YC/early stage)
            signal = await self._check_ashby(board_id, domain)
            if signal:
                return signal

            # 3. Lever
            signal = await self._check_lever(board_id, domain)
            if signal:
                return signal

            # 4. Workable
            signal = await self._check_workable(board_id, domain)
            if signal:
                return signal

            # Small delay between board_id attempts
            await asyncio.sleep(0.05)

        return None

    def _generate_board_ids(self, domain: str) -> List[str]:
        """
        Generate candidate board IDs from a domain.

        ATS platforms use company slugs that may differ from domains.
        This generates likely candidates to try.

        Examples:
            "10beauty.co" -> ["10beauty", "10beautyhq", "10beauty-careers"]
            "jacob-bar.com" -> ["jacob-bar", "jacobbar", "jacob", "jacob-barhq"]

        Args:
            domain: Company domain

        Returns:
            List of candidate board IDs to try
        """
        # Extract base name from domain
        base = domain.split(".")[0].lower().strip()
        if not base:
            return []

        candidates = [base]

        # Handle hyphenated names
        if "-" in base:
            # "jacob-bar" -> "jacobbar", "jacob"
            candidates.append(base.replace("-", ""))
            parts = base.split("-")
            if parts[0]:
                candidates.append(parts[0])

        # Handle underscored names
        if "_" in base:
            candidates.append(base.replace("_", ""))
            parts = base.split("_")
            if parts[0]:
                candidates.append(parts[0])

        # Common ATS slug patterns
        candidates.extend([
            f"{base}hq",
            f"{base}-careers",
            f"{base}careers",
            f"{base}-jobs",
            f"{base}jobs",
        ])

        # Deduplicate while preserving order
        seen = set()
        return [x for x in candidates if x and not (x in seen or seen.add(x))]

    async def _check_greenhouse(
        self, board_id: str, domain: str
    ) -> Optional[JobPostingSignal]:
        """
        Check Greenhouse Board API for job postings.

        API: https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs
        Rate Limit: Generous (no auth required)

        Args:
            board_id: Greenhouse board identifier
            domain: Original company domain

        Returns:
            JobPostingSignal if jobs found, None otherwise
        """
        url = f"{GREENHOUSE_API}/{board_id}/jobs"

        try:
            data = await self._http_get(url, timeout=self.timeout)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None  # Board doesn't exist
            logger.debug(f"Greenhouse error for {board_id}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Greenhouse request failed for {board_id}: {e}")
            return None

        if not data or not isinstance(data, dict):
            return None

        jobs = data.get("jobs", [])
        if not jobs:
            return None

        # Build stable snapshot for change detection
        job_ids = sorted([str(j.get("id", "")) for j in jobs if j.get("id")])[:500]
        stable_snapshot = {
            "board_id": board_id,
            "job_count": len(jobs),
            "job_ids_hash": hashlib.md5(json.dumps(job_ids).encode()).hexdigest(),
            "job_ids_sample": job_ids[:20],
        }

        return self._build_signal(
            jobs=jobs,
            board_id=board_id,
            domain=domain,
            platform="greenhouse",
            raw_snapshot=stable_snapshot,
        )

    async def _check_lever(
        self, company_id: str, domain: str
    ) -> Optional[JobPostingSignal]:
        """
        Check Lever Postings API for job postings.

        API: https://api.lever.co/v0/postings/{company_id}
        Rate Limit: Generous (no auth required)

        Args:
            company_id: Lever company identifier
            domain: Original company domain

        Returns:
            JobPostingSignal if jobs found, None otherwise
        """
        url = f"{LEVER_API}/{company_id}"

        try:
            jobs = await self._http_get(url, timeout=self.timeout)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.debug(f"Lever error for {company_id}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Lever request failed for {company_id}: {e}")
            return None

        if not jobs or not isinstance(jobs, list):
            return None

        # Stable snapshot
        job_ids = sorted([str(j.get("id", "")) for j in jobs if j.get("id")])[:500]
        stable_snapshot = {
            "company_id": company_id,
            "job_count": len(jobs),
            "job_ids_hash": hashlib.md5(json.dumps(job_ids).encode()).hexdigest(),
            "job_ids_sample": job_ids[:20],
        }

        return self._build_signal(
            jobs=jobs,
            board_id=company_id,
            domain=domain,
            platform="lever",
            raw_snapshot=stable_snapshot,
        )

    async def _check_ashby(
        self, board_id: str, domain: str
    ) -> Optional[JobPostingSignal]:
        """
        Check Ashby Public API for job postings.

        Ashby is favored by YC companies and early-stage startups.
        API: https://api.ashbyhq.com/posting-api/job-board/{board_id}

        Args:
            board_id: Ashby board identifier
            domain: Original company domain

        Returns:
            JobPostingSignal if jobs found, None otherwise
        """
        url = f"{ASHBY_API}/{board_id}"
        params = {"includeCompensation": "true"}

        try:
            data = await self._http_get(url, params=params, timeout=self.timeout)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.debug(f"Ashby error for {board_id}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Ashby request failed for {board_id}: {e}")
            return None

        if not data or not isinstance(data, dict):
            return None

        jobs = data.get("jobs", [])
        if not jobs:
            return None

        # Normalize Ashby response to common format
        normalized_jobs = []
        job_ids = []

        for j in jobs:
            job_id = str(j.get("id", ""))
            if job_id:
                job_ids.append(job_id)

            # Map Ashby fields to common structure
            location = j.get("location")
            if isinstance(location, str):
                location = {"name": location}
            elif location is None:
                location = {}

            normalized_jobs.append({
                "title": j.get("title", ""),
                "location": location,
                "departments": [{"name": j.get("department") or j.get("team", "")}],
                "absolute_url": j.get("jobUrl", ""),
                "published_at": j.get("publishedAt"),
                "updated_at": j.get("updatedAt"),
            })

        job_ids.sort()
        stable_snapshot = {
            "board_id": board_id,
            "job_count": len(jobs),
            "job_ids_hash": hashlib.md5(json.dumps(job_ids).encode()).hexdigest(),
            "job_ids_sample": job_ids[:20],
        }

        return self._build_signal(
            jobs=normalized_jobs,
            board_id=board_id,
            domain=domain,
            platform="ashby",
            raw_snapshot=stable_snapshot,
        )

    async def _check_workable(
        self, board_id: str, domain: str
    ) -> Optional[JobPostingSignal]:
        """
        Check Workable for job postings.

        Workable is used by SMB/consumer companies (e.g., Jacob Bars).
        We check the careers page HTML for job listings.

        URL Pattern: https://apply.workable.com/{company}/

        Args:
            board_id: Workable company identifier
            domain: Original company domain

        Returns:
            JobPostingSignal if jobs found, None otherwise
        """
        # Workable doesn't have a clean JSON API, so we check the careers page
        # and look for job listing indicators
        url = f"{WORKABLE_CAREERS_URL}/{board_id}"

        try:
            # Use a raw HTTP request since this returns HTML, not JSON
            async def fetch_workable():
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url, follow_redirects=True)
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    return response.text

            html = await self._fetch_with_retry(fetch_workable)
        except Exception as e:
            logger.debug(f"Workable request failed for {board_id}: {e}")
            return None

        if not html:
            return None

        # Look for job listing indicators in the HTML
        # Workable pages have specific patterns we can detect
        job_indicators = [
            'data-ui="job-opening"',
            'class="job-opening"',
            'whr-item',
            'job-listing',
        ]

        has_jobs = any(indicator in html for indicator in job_indicators)
        if not has_jobs:
            return None

        # Count approximate jobs from HTML patterns
        job_count = html.count('data-ui="job-opening"') or html.count('whr-item')
        if job_count == 0:
            job_count = 1  # At least one job detected

        # Extract job titles if possible (simple regex-free approach)
        sample_titles = []
        # Look for common title patterns in Workable HTML
        for marker in ['<h3 class="job-title">', '<span class="whr-title">']:
            if marker in html:
                start_idx = 0
                while len(sample_titles) < 10:
                    idx = html.find(marker, start_idx)
                    if idx == -1:
                        break
                    start = idx + len(marker)
                    end = html.find('<', start)
                    if end > start:
                        title = html[start:end].strip()
                        if title and len(title) < 200:
                            sample_titles.append(title)
                    start_idx = end

        # Build stable snapshot
        content_hash = hashlib.md5(html.encode()).hexdigest()
        stable_snapshot = {
            "board_id": board_id,
            "job_count": job_count,
            "content_hash": content_hash[:16],
            "detected_via": "html_parsing",
        }

        # Count engineering roles
        eng_count = sum(
            1 for title in sample_titles
            if any(kw in title.lower() for kw in ENGINEERING_KEYWORDS)
        )

        return JobPostingSignal(
            company_name=board_id.replace("-", " ").title(),
            company_domain=domain,
            ats_platform="workable",
            total_positions=job_count,
            engineering_positions=eng_count,
            sample_titles=sample_titles,
            job_url=url,
            departments=[],
            locations=[],
            oldest_posting_at=None,  # Can't reliably extract from HTML
            newest_posting_at=None,
            raw_snapshot=stable_snapshot,
        )

    def _build_signal(
        self,
        jobs: List[Dict[str, Any]],
        board_id: str,
        domain: str,
        platform: str,
        raw_snapshot: Dict[str, Any],
    ) -> JobPostingSignal:
        """
        Build a JobPostingSignal from a list of jobs.

        Common processing for Greenhouse, Lever, and Ashby responses.

        Args:
            jobs: List of job dictionaries from ATS API
            board_id: ATS board/company identifier
            domain: Original company domain
            platform: ATS platform name
            raw_snapshot: Stable snapshot for change detection

        Returns:
            JobPostingSignal with extracted metadata
        """
        eng_count = 0
        locations: set[str] = set()
        departments: set[str] = set()
        sample_titles: list[str] = []
        oldest_posting: Optional[datetime] = None
        newest_posting: Optional[datetime] = None

        for i, job in enumerate(jobs):
            # Extract title
            title = job.get("title", "") or job.get("text", "")
            if i < 10 and title:
                sample_titles.append(title.strip())

            # Count engineering roles
            if title and any(kw in title.lower() for kw in ENGINEERING_KEYWORDS):
                eng_count += 1

            # Extract location
            loc = job.get("location")
            if isinstance(loc, dict):
                loc_name = loc.get("name", "")
            elif isinstance(loc, str):
                loc_name = loc
            else:
                loc_name = ""
            if loc_name:
                locations.add(loc_name.strip())

            # Extract department
            depts = job.get("departments") or job.get("categories") or []
            if isinstance(depts, list):
                for d in depts:
                    if isinstance(d, dict):
                        dept_name = d.get("name") or d.get("department") or d.get("team")
                        if dept_name:
                            departments.add(dept_name.strip())
            elif isinstance(depts, dict):
                dept_name = depts.get("department") or depts.get("team") or depts.get("name")
                if dept_name:
                    departments.add(dept_name.strip())

            # Parse timestamps for ghost job detection
            for ts_field in ["published_at", "updated_at", "created_at", "createdAt"]:
                ts_value = job.get(ts_field)
                dt = _parse_dt(ts_value)
                if dt:
                    if oldest_posting is None or dt < oldest_posting:
                        oldest_posting = dt
                    if newest_posting is None or dt > newest_posting:
                        newest_posting = dt
                    break  # Use first valid timestamp

        # Extract job URL
        job_url = ""
        if jobs:
            first_job = jobs[0]
            job_url = (
                first_job.get("absolute_url")
                or first_job.get("hostedUrl")
                or first_job.get("jobUrl")
                or first_job.get("url")
                or ""
            )

        return JobPostingSignal(
            company_name=board_id.replace("-", " ").title(),
            company_domain=domain,
            ats_platform=platform,
            total_positions=len(jobs),
            engineering_positions=eng_count,
            sample_titles=sample_titles,
            departments=list(departments),
            locations=list(locations),
            job_url=job_url,
            oldest_posting_at=oldest_posting,
            newest_posting_at=newest_posting,
            raw_snapshot=raw_snapshot,
        )

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect hiring signals for all configured domains.

        This method is called by BaseCollector.run() and should return
        all signals found. Storage/deduplication is handled by BaseCollector.

        Returns:
            List of Signal objects for verification gate
        """
        signals: List[Signal] = []

        for domain in self.domains:
            try:
                # Normalize domain
                clean_domain = domain.lower().replace("www.", "").strip()
                if not clean_domain:
                    continue

                # Check all ATS platforms
                job_signal = await self.check_domain(clean_domain)

                if job_signal:
                    logger.info(
                        f"Found {job_signal.total_positions} jobs at {clean_domain} "
                        f"via {job_signal.ats_platform}"
                    )
                    signals.append(job_signal.to_signal())

                # Rate limiting between domains
                await asyncio.sleep(0.2)

            except Exception as e:
                logger.warning(f"Error checking domain {domain}: {e}")
                self._errors.append(f"Domain {domain}: {str(e)}")

        return signals


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

async def main():
    """
    CLI entry point for testing the collector.

    Usage:
        python -m collectors.job_postings

    Or with specific domains:
        DOMAINS="anthropic.com,openai.com" python -m collectors.job_postings
    """
    import argparse

    parser = argparse.ArgumentParser(description="Job Postings Collector")
    parser.add_argument(
        "--domains",
        type=str,
        default=os.environ.get("DOMAINS", "anthropic.com,openai.com,stripe.com"),
        help="Comma-separated list of domains to check",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Don't persist signals (default: True)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Parse domains
    domains = [d.strip() for d in args.domains.split(",") if d.strip()]

    print(f"\n{'=' * 60}")
    print("JOB POSTINGS COLLECTOR")
    print(f"{'=' * 60}")
    print(f"Domains: {', '.join(domains)}")
    print(f"Dry run: {args.dry_run}")
    print()

    # Run collector
    collector = JobPostingsCollector(domains=domains)
    result = await collector.run(dry_run=args.dry_run)

    # Print results
    print(f"\n{'=' * 60}")
    print("RESULTS")
    print(f"{'=' * 60}")
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"Signals new: {result.signals_new}")
    print(f"Signals suppressed: {result.signals_suppressed}")
    print(f"Duration: {result.duration_seconds:.2f}s")

    if result.error_message:
        print(f"Error: {result.error_message}")

    # Print signal details
    if result.signals_found > 0:
        print(f"\n{'=' * 60}")
        print("SIGNAL DETAILS")
        print(f"{'=' * 60}")
        for sig in result.signals[:10]:  # Limit output
            raw = sig.raw_data or {}
            print(f"\n  Company: {raw.get('company_name', 'Unknown')}")
            print(f"  Domain: {raw.get('company_domain', 'Unknown')}")
            print(f"  Platform: {raw.get('ats_platform', 'Unknown')}")
            print(f"  Positions: {raw.get('total_positions', 0)}")
            print(f"  Engineering: {raw.get('engineering_positions', 0)}")
            print(f"  Confidence: {sig.confidence:.2f}")
            if raw.get("sample_titles"):
                print(f"  Sample titles: {', '.join(raw['sample_titles'][:3])}")


if __name__ == "__main__":
    asyncio.run(main())
