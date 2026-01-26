"""
Page State Detection for Monitoring Subsystem

Detects the state of a web page based on content patterns:
- live: Normal functioning page
- coming_soon: Placeholder/under construction
- blocked: Access denied, CAPTCHA, bot protection
- error: Server error, site down
- unknown: Cannot determine
"""

import re
from typing import Optional


# Coming soon / under construction patterns
COMING_SOON_PATTERNS = [
    r"coming\s+soon",
    r"under\s+construction",
    r"launching\s+soon",
    r"stay\s+tuned",
    r"we('|')?re\s+(building|working|launching)",
    r"site\s+(is\s+)?under\s+development",
    r"check\s+back\s+(soon|later)",
    r"something\s+(big|new|amazing)\s+is\s+coming",
    r"parked\s+domain",
    r"this\s+domain\s+(is|has\s+been)\s+(for\s+sale|parked)",
    r"buy\s+this\s+domain",
]

# Blocked / access denied patterns
BLOCKED_PATTERNS = [
    r"access\s+denied",
    r"forbidden",
    r"you\s+(don('|')?t|do\s+not)\s+have\s+permission",
    r"cloudflare",
    r"captcha",
    r"verify\s+you\s+are\s+(human|not\s+a\s+robot)",
    r"please\s+enable\s+(javascript|cookies)",
    r"bot\s+protection",
    r"rate\s+limit(ed)?",
    r"too\s+many\s+requests",
    r"security\s+check",
    r"please\s+wait\s+while\s+we\s+verify",
    r"checking\s+your\s+browser",
]

# Error patterns
ERROR_PATTERNS = [
    r"404\s*(not\s+found)?",
    r"page\s+not\s+found",
    r"this\s+page\s+(doesn('|')?t|does\s+not)\s+exist",
    r"500\s*(internal\s+server\s+error)?",
    r"503\s*(service\s+unavailable)?",
    r"502\s*(bad\s+gateway)?",
    r"site\s+(is\s+)?(down|offline|unavailable)",
    r"maintenance",
    r"temporarily\s+unavailable",
    r"we('|')?re\s+experiencing\s+(technical\s+)?(issues|difficulties|problems)",
    r"oops|something\s+went\s+wrong",
]


def detect_page_state(
    text_content: str,
    status_code: Optional[int] = None,
    text_length: Optional[int] = None,
) -> str:
    """
    Detect the state of a web page.

    Args:
        text_content: Extracted text content from the page
        status_code: HTTP status code (if available)
        text_length: Length of text content (calculated if not provided)

    Returns:
        Page state: 'live', 'coming_soon', 'blocked', 'error', or 'unknown'
    """
    if text_length is None:
        text_length = len(text_content)

    # Normalize text for pattern matching
    text_lower = text_content.lower()

    # Check status code first
    if status_code:
        if status_code == 403:
            return "blocked"
        elif status_code == 404 or status_code == 410:
            return "error"
        elif status_code >= 500:
            return "error"
        elif status_code == 429:
            return "blocked"

    # Very short content is suspicious
    if text_length < 100:
        # Check for coming soon indicators
        for pattern in COMING_SOON_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return "coming_soon"

        # Check for blocked indicators
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return "blocked"

        # Check for error indicators
        for pattern in ERROR_PATTERNS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return "error"

        # Very short with no clear indicators
        return "unknown"

    # Check patterns on longer content
    # Coming soon patterns
    coming_soon_score = 0
    for pattern in COMING_SOON_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            coming_soon_score += 1

    # Blocked patterns
    blocked_score = 0
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            blocked_score += 1

    # Error patterns
    error_score = 0
    for pattern in ERROR_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            error_score += 1

    # Determine state based on scores
    max_score = max(coming_soon_score, blocked_score, error_score)

    if max_score >= 2:
        if coming_soon_score == max_score:
            return "coming_soon"
        elif blocked_score == max_score:
            return "blocked"
        elif error_score == max_score:
            return "error"

    # Additional heuristics for live pages
    if text_length > 500:
        # Long content with substantial text is likely live
        # Check for common live page indicators
        live_indicators = [
            r"about\s+us",
            r"contact",
            r"privacy\s+policy",
            r"terms\s+(of\s+service|and\s+conditions)",
            r"sign\s+(up|in)",
            r"log\s*in",
            r"pricing",
            r"features",
            r"product",
            r"copyright",
        ]

        live_score = sum(
            1 for pattern in live_indicators
            if re.search(pattern, text_lower, re.IGNORECASE)
        )

        if live_score >= 2:
            return "live"

    # Default to live if we have substantial content and no error indicators
    if text_length > 200 and error_score == 0 and blocked_score == 0:
        return "live"

    return "unknown"


def is_significant_state_change(old_state: Optional[str], new_state: str) -> bool:
    """
    Determine if a state change is significant enough to trigger an alert.

    Args:
        old_state: Previous page state (None if first check)
        new_state: Current page state

    Returns:
        True if this is a significant state change
    """
    if old_state is None:
        # First check - significant if not live
        return new_state != "live"

    if old_state == new_state:
        return False

    # Any transition TO error or blocked is significant
    if new_state in ("error", "blocked"):
        return True

    # Transition FROM live to anything else is significant
    if old_state == "live" and new_state != "live":
        return True

    # Coming soon to live is notable but not alarming
    if old_state == "coming_soon" and new_state == "live":
        return False

    return True
