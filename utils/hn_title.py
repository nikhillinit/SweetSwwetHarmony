"""
Shared HN Title Parsing Module

Centralizes Hacker News prefix stripping and company name extraction
from HN title bodies. Used by:
- collectors/hacker_news.py (native collector)
- run_pipeline.py (hunter dispatch)
- scripts/backfill_hunter_company_names.py (backfill)

Recognized prefixes (case-insensitive): Show, Launch, Demo, Ask.
"""

from __future__ import annotations

import re

# Regex: split on first structural delimiter (dash variants, pipe, paren, comma)
# Moved from collectors/hacker_news.py:62 (_SHOW_HN_SEP_RE)
HN_SEP_RE: re.Pattern[str] = re.compile(
    r"\s*[-\u2013\u2014|]\s*|\s*\(|\s*,\s*"
)

# Recognized HN prefixes — colon optional
_HN_PREFIX_RE = re.compile(
    r"^(show|launch|demo|ask)\s+hn\s*:?\s*", re.IGNORECASE
)


def strip_hn_prefix(title: str) -> tuple[str, str | None]:
    """Strip known HN prefix. Returns (cleaned_body, prefix_type).

    Recognized (case-insensitive): show, launch, demo, ask.
    Colon optional. Returns (original, None) if no match.
    """
    if not title:
        return ("", None)

    m = _HN_PREFIX_RE.match(title)
    if m:
        prefix_type = m.group(1).lower()
        cleaned = title[m.end():]
        return (cleaned, prefix_type)

    return (title, None)


def extract_name_from_hn_body(body: str) -> str | None:
    """Extract company name from HN title body (after prefix stripped).

    Splits on first HN_SEP_RE separator. Returns text before it.
    Returns None if no separator or empty result.
    Returns original casing — callers apply their own normalization.
    """
    if not body or not body.strip():
        return None

    m = HN_SEP_RE.search(body)
    if m:
        name = body[:m.start()].strip()
        return name if name else None

    return None
