"""Evidence family classification for signals.

Maps (signal_type, source_api) → evidence_family for convergence KPI.

Families:
  - developer:     GitHub activity, repos, research papers
  - regulatory:    SEC filings, incorporations, patents
  - web_presence:  Domain registrations, company profiles
  - hiring:        Job postings, LinkedIn jobs
  - public_buzz:   News, Product Hunt, HN, press releases

Invariant #4: Unknown signal types MUST return "unknown", never silently
default to "public_buzz".
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Primary mapping: signal_type → family
_SIGNAL_TYPE_FAMILIES: dict[str, str] = {
    # developer
    "github_spike": "developer",
    "github_activity": "developer",
    "new_repo": "developer",
    "commit_spike": "developer",
    "org_created": "developer",
    "research_paper": "developer",
    # regulatory
    "funding_event": "regulatory",
    "incorporation": "regulatory",
    "patent_filing": "regulatory",
    "crunchbase_funding": "regulatory",
    # web_presence
    "crunchbase_company": "web_presence",
    "domain_registration": "web_presence",
    "linkedin_company": "web_presence",
    # hiring
    "hiring_signal": "hiring",
    "linkedin_job_posting": "hiring",
    # public_buzz
    "product_hunt_launch": "public_buzz",
    "hacker_news_mention": "public_buzz",
    "news_mention": "public_buzz",
    "funding_announcement": "public_buzz",
    "product_launch": "public_buzz",
    "press_release": "public_buzz",
    "funding_news": "public_buzz",
    "feedback_request": "public_buzz",
    "hunter_discovery": "public_buzz",
}

# Source-API overrides for ambiguous signal_types
# Key: (signal_type, source_api) → family
_SOURCE_API_OVERRIDES: dict[tuple[str, str], str] = {
    ("funding_announcement", "sec_edgar"): "regulatory",
    ("funding_event", "news_api"): "public_buzz",
    ("funding_event", "rss_feeds"): "public_buzz",
}


def get_family(signal_type: str, source_api: str) -> str:
    """Classify a signal into an evidence family.

    Returns one of: developer, regulatory, web_presence, hiring,
    public_buzz, unknown.

    Never returns "public_buzz" for unmapped types — returns "unknown"
    with a warning log (invariant #4).
    """
    # Check source-API override first (more specific)
    override = _SOURCE_API_OVERRIDES.get((signal_type, source_api))
    if override is not None:
        return override

    # Check primary mapping
    family = _SIGNAL_TYPE_FAMILIES.get(signal_type)
    if family is not None:
        return family

    # Unknown — log structured warning, never silently map to public_buzz
    logger.warning(
        "Unknown signal_type for evidence family classification: "
        "signal_type=%s source_api=%s → returning 'unknown'",
        signal_type,
        source_api,
    )
    return "unknown"


# All valid family values (for validation)
VALID_FAMILIES = frozenset({
    "developer", "regulatory", "web_presence", "hiring", "public_buzz", "unknown",
})
