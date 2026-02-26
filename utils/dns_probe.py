"""
DNS Probe — generate plausible domain candidates from company names
and verify them via async DNS resolution.

Phase 1: candidate-only (results stored in raw_data, no canonical key promotion).

Usage:
    from utils.dns_probe import generate_domain_candidates, dns_probe_company

    candidates = generate_domain_candidates("Borden Cheese")
    domain = await dns_probe_company("Borden Cheese")
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from typing import Optional

from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS
from utils.company_name_extractor import LEGAL_SUFFIXES, _BLOCKED_DOMAIN_SUFFIXES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TLDS = (".com", ".io", ".co", ".ai", ".net", ".app")

_FIRST_TOKEN_STOPLIST = frozenset({
    "national", "united", "global", "american", "first", "new",
    "general", "great", "standard", "best", "prime", "royal", "classic",
})

_MAX_CANDIDATES = 12

# Module-level DNS cache (injectable via `cache` param on public functions)
_dns_cache: dict[str, bool] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NON_ALPHA_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    """Lowercase alpha-numeric only (no separators)."""
    return _NON_ALPHA_RE.sub("", name.lower())


def _hyphen_slug(name: str) -> str:
    """Lowercase, non-alnum collapsed to single hyphen, stripped."""
    s = _NON_ALPHA_RE.sub("-", name.lower()).strip("-")
    return s


def _is_blocked(domain: str) -> bool:
    """Reject domain if it matches NEWS_PUBLISHER_DOMAINS or _BLOCKED_DOMAIN_SUFFIXES."""
    dl = domain.lower()
    for pub in NEWS_PUBLISHER_DOMAINS:
        pub = pub.lstrip(".")
        if dl == pub or dl.endswith("." + pub):
            return True
    for suffix in _BLOCKED_DOMAIN_SUFFIXES:
        suffix = suffix.lstrip(".")
        if dl == suffix or dl.endswith("." + suffix):
            return True
    return False


# ---------------------------------------------------------------------------
# 1. Candidate generation
# ---------------------------------------------------------------------------


def generate_domain_candidates(company_name: str) -> list[str]:
    """
    Generate plausible domain candidates from a company name.

    Strategy:
      - Strip trailing legal suffixes (Inc, LLC, etc.)
      - Build slug variants: full-concat, first-token, hyphenated
      - TLD-first ordering: all slug variants x .com, then x .io, ...
      - Filter out publisher/blocked domains
      - Cap at 12 candidates
    """
    if not company_name or not company_name.strip():
        return []

    name = company_name.strip()

    # Strip trailing legal suffixes
    tokens = name.split()
    while tokens and tokens[-1].lower().rstrip(".,") in LEGAL_SUFFIXES:
        tokens.pop()
    if not tokens:
        return []

    cleaned = " ".join(tokens)

    # Must contain at least one letter
    if not any(c.isalpha() for c in cleaned):
        return []

    # Must be at least 2 chars after cleanup
    if len(cleaned.strip()) < 2:
        return []

    # Build slug variants
    slugs: list[str] = []

    full = _slug(cleaned)
    if full:
        slugs.append(full)

    # First-token variant (only if multi-word after suffix strip)
    if len(tokens) > 1:
        first_raw = tokens[0]
        first = _slug(first_raw)
        if first and first != full:
            # Guard: length >= 4 and not in stoplist
            allow = False
            if len(first) >= 4 and first not in _FIRST_TOKEN_STOPLIST:
                allow = True
            # Exception: contains digit
            elif any(c.isdigit() for c in first):
                allow = True
            # Exception: 2-3 chars AND all-caps in original casing
            elif 2 <= len(first_raw) <= 3 and first_raw.isupper():
                allow = True

            if allow:
                slugs.append(first)

    # Hyphenated variant (only if multi-word)
    if len(tokens) > 1:
        hyph = _hyphen_slug(cleaned)
        if hyph and hyph != full and "-" in hyph:
            slugs.append(hyph)

    # Compose: TLD-first ordering (all slugs x .com, then all slugs x .io, ...)
    candidates: list[str] = []
    seen: set[str] = set()
    for tld in _TLDS:
        for s in slugs:
            domain = f"{s}{tld}"
            if domain in seen:
                continue
            seen.add(domain)
            if _is_blocked(domain):
                continue
            candidates.append(domain)
            if len(candidates) >= _MAX_CANDIDATES:
                return candidates

    return candidates


# ---------------------------------------------------------------------------
# 2. Async DNS probe
# ---------------------------------------------------------------------------


async def probe_domain(
    domain: str,
    timeout: float = 1.0,
    cache: dict[str, bool] | None = None,
) -> bool:
    """
    Probe a single domain via DNS (getaddrinfo).

    Returns True if the domain resolves, False otherwise.
    Results are cached in the module-level _dns_cache (or injected cache).
    """
    c = cache if cache is not None else _dns_cache
    if domain in c:
        return c[domain]

    loop = asyncio.get_running_loop()
    try:
        await asyncio.wait_for(
            loop.getaddrinfo(domain, None),
            timeout=timeout,
        )
        c[domain] = True
        return True
    except (socket.gaierror, OSError, asyncio.TimeoutError):
        c[domain] = False
        return False


# ---------------------------------------------------------------------------
# 3. Company-level probe (concurrent, priority-ordered)
# ---------------------------------------------------------------------------


async def dns_probe_company(
    company_name: str,
    max_attempts: int = 4,
    cache: dict[str, bool] | None = None,
) -> str | None:
    """
    Generate domain candidates and probe top `max_attempts` concurrently.

    Returns the first resolving domain by candidate priority order, or None.
    """
    t0 = time.monotonic()
    candidates = generate_domain_candidates(company_name)
    to_probe = candidates[:max_attempts]

    if not to_probe:
        logger.info(
            "dns_probe company=%s tried=0 result=none elapsed_ms=0",
            company_name,
        )
        return None

    results = await asyncio.gather(
        *(probe_domain(d, cache=cache) for d in to_probe)
    )

    # Pick winner by candidate priority order (not first-to-resolve)
    winner = None
    for domain, resolved in zip(to_probe, results):
        if resolved:
            winner = domain
            break

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "dns_probe company=%s tried=%d result=%s elapsed_ms=%d",
        company_name,
        len(to_probe),
        winner or "none",
        elapsed_ms,
    )
    return winner
