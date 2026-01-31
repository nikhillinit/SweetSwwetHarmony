"""
ATS (Applicant Tracking System) Embed Discovery

Detects embedded job boards from Greenhouse, Lever, Ashby, and Workable
in company career page HTML. Extracts board IDs and generates direct API URLs.

Usage:
    detector = ATSSignatureDetector()
    result = detector.detect(html)
    if result:
        # result.provider == ATSProvider.GREENHOUSE
        # result.board_id == "acmecorp"
        # result.api_url == "https://boards-api.greenhouse.io/v1/boards/acmecorp/jobs"

This enables the content pipeline to call ATS APIs directly instead of
scraping HTML, providing cleaner and more reliable job data.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)


class ATSProvider(Enum):
    """Supported ATS providers."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    WORKABLE = "workable"


# API URL templates for each provider
API_URLS = {
    ATSProvider.GREENHOUSE: "https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs",
    ATSProvider.LEVER: "https://api.lever.co/v0/postings/{board_id}",
    ATSProvider.ASHBY: "https://api.ashbyhq.com/posting-api/job-board/{board_id}",
    ATSProvider.WORKABLE: "https://apply.workable.com/{board_id}",
}


@dataclass
class ATSDiscoveryResult:
    """Result of ATS embed detection."""

    provider: ATSProvider
    board_id: str
    api_url: str
    embed_url: Optional[str] = None
    confidence: float = 0.9

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "provider": self.provider.value,
            "board_id": self.board_id,
            "api_url": self.api_url,
            "embed_url": self.embed_url,
            "confidence": self.confidence,
        }

    def __eq__(self, other: object) -> bool:
        """Compare results."""
        if not isinstance(other, ATSDiscoveryResult):
            return NotImplemented
        return (
            self.provider == other.provider
            and self.board_id == other.board_id
            and self.api_url == other.api_url
        )


@dataclass
class ATSPattern:
    """Pattern for detecting ATS embeds."""

    provider: ATSProvider
    pattern: Pattern[str]
    board_id_group: int = 1  # Capture group index for board_id
    confidence: float = 0.9


class ATSSignatureDetector:
    """
    Detects ATS embed signatures in HTML.

    Searches for known patterns from Greenhouse, Lever, Ashby, and Workable
    and extracts the board/company identifier to construct direct API URLs.

    Detection priority (in order):
    1. Greenhouse - most common for tech startups
    2. Lever - common for mid-stage companies
    3. Ashby - popular with YC companies
    4. Workable - SMB/consumer companies

    Usage:
        detector = ATSSignatureDetector()
        result = detector.detect(html)
        if result:
            print(f"Found {result.provider.value}: {result.board_id}")
            print(f"API URL: {result.api_url}")
    """

    def __init__(self) -> None:
        """Initialize detector with compiled patterns."""
        self._patterns = self._compile_patterns()

    def _compile_patterns(self) -> List[ATSPattern]:
        """
        Compile regex patterns for ATS detection.

        Patterns are ordered by detection priority.
        """
        patterns = []

        # === Greenhouse patterns ===
        # Script embed: boards.greenhouse.io/embed/job_board/js?for=COMPANY
        patterns.append(
            ATSPattern(
                provider=ATSProvider.GREENHOUSE,
                pattern=re.compile(
                    r'boards\.greenhouse\.io/embed/job_board/js\?for=([a-zA-Z0-9_-]+)',
                    re.IGNORECASE,
                ),
                confidence=0.95,
            )
        )
        # Iframe embed: boards.greenhouse.io/embed/job_board?for=COMPANY
        patterns.append(
            ATSPattern(
                provider=ATSProvider.GREENHOUSE,
                pattern=re.compile(
                    r'boards\.greenhouse\.io/embed/job_board\?for=([a-zA-Z0-9_-]+)',
                    re.IGNORECASE,
                ),
                confidence=0.95,
            )
        )
        # Direct board link: boards.greenhouse.io/COMPANY or job-boards.greenhouse.io/COMPANY
        patterns.append(
            ATSPattern(
                provider=ATSProvider.GREENHOUSE,
                pattern=re.compile(
                    r'(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)(?:/|(?:\?|$|"))',
                    re.IGNORECASE,
                ),
                confidence=0.90,
            )
        )

        # === Lever patterns ===
        # jobs.lever.co/COMPANY (with optional path/query)
        patterns.append(
            ATSPattern(
                provider=ATSProvider.LEVER,
                pattern=re.compile(
                    r'jobs\.lever\.co/([a-zA-Z0-9_-]+)(?:/|(?:\?|$|"))',
                    re.IGNORECASE,
                ),
                confidence=0.90,
            )
        )

        # === Ashby patterns ===
        # jobs.ashbyhq.com/COMPANY
        patterns.append(
            ATSPattern(
                provider=ATSProvider.ASHBY,
                pattern=re.compile(
                    r'jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)(?:/|(?:\?|$|"))',
                    re.IGNORECASE,
                ),
                confidence=0.90,
            )
        )

        # === Workable patterns ===
        # workable.com/embed/init/COMPANY
        patterns.append(
            ATSPattern(
                provider=ATSProvider.WORKABLE,
                pattern=re.compile(
                    r'workable\.com/embed/init/([a-zA-Z0-9_-]+)',
                    re.IGNORECASE,
                ),
                confidence=0.95,
            )
        )
        # apply.workable.com/COMPANY
        patterns.append(
            ATSPattern(
                provider=ATSProvider.WORKABLE,
                pattern=re.compile(
                    r'apply\.workable\.com/([a-zA-Z0-9_-]+)(?:/|(?:\?|$|"))',
                    re.IGNORECASE,
                ),
                confidence=0.90,
            )
        )

        return patterns

    def detect(self, html: str) -> Optional[ATSDiscoveryResult]:
        """
        Detect ATS embed in HTML.

        Searches for known ATS patterns and returns the first match.
        Patterns are checked in priority order (Greenhouse first).

        Args:
            html: HTML content to analyze

        Returns:
            ATSDiscoveryResult if ATS detected, None otherwise
        """
        if not html or not html.strip():
            return None

        for ats_pattern in self._patterns:
            match = ats_pattern.pattern.search(html)
            if match:
                board_id = match.group(ats_pattern.board_id_group)
                # Clean up board_id (remove trailing slashes, normalize)
                board_id = board_id.rstrip("/").strip()
                if not board_id:
                    continue

                # Skip common false positives
                if board_id.lower() in {"embed", "js", "api", "v1", "v0"}:
                    continue

                api_url = API_URLS[ats_pattern.provider].format(board_id=board_id)
                embed_url = match.group(0)

                logger.debug(
                    "Detected %s embed: board_id=%s",
                    ats_pattern.provider.value,
                    board_id,
                )

                return ATSDiscoveryResult(
                    provider=ats_pattern.provider,
                    board_id=board_id,
                    api_url=api_url,
                    embed_url=embed_url,
                    confidence=ats_pattern.confidence,
                )

        return None

    def detect_all(self, html: str) -> List[ATSDiscoveryResult]:
        """
        Detect all ATS embeds in HTML.

        Unlike detect(), this returns all found ATS providers.

        Args:
            html: HTML content to analyze

        Returns:
            List of ATSDiscoveryResult for all detected providers
        """
        if not html or not html.strip():
            return []

        results: List[ATSDiscoveryResult] = []
        seen_providers: set[ATSProvider] = set()

        for ats_pattern in self._patterns:
            if ats_pattern.provider in seen_providers:
                continue

            match = ats_pattern.pattern.search(html)
            if match:
                board_id = match.group(ats_pattern.board_id_group)
                board_id = board_id.rstrip("/").strip()
                if not board_id or board_id.lower() in {"embed", "js", "api", "v1", "v0"}:
                    continue

                api_url = API_URLS[ats_pattern.provider].format(board_id=board_id)

                results.append(
                    ATSDiscoveryResult(
                        provider=ats_pattern.provider,
                        board_id=board_id,
                        api_url=api_url,
                        embed_url=match.group(0),
                        confidence=ats_pattern.confidence,
                    )
                )
                seen_providers.add(ats_pattern.provider)

        return results


# Module-level singleton for convenience
_detector: Optional[ATSSignatureDetector] = None


def get_detector() -> ATSSignatureDetector:
    """Get or create the singleton ATSSignatureDetector instance."""
    global _detector
    if _detector is None:
        _detector = ATSSignatureDetector()
    return _detector


def detect_ats(html: str) -> Optional[ATSDiscoveryResult]:
    """
    Convenience function to detect ATS embed in HTML.

    Uses the singleton detector instance.

    Args:
        html: HTML content to analyze

    Returns:
        ATSDiscoveryResult if ATS detected, None otherwise
    """
    return get_detector().detect(html)
