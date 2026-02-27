"""Canonical key v2 builder — domain-first identity for convergence KPI.

Produces a canonical_key_v2 that is domain-first (via tldextract), with
fallback to name_loc keys, and synthetic unlinked_buzz keys for public_buzz
signals that lack both domain and plausible company name.

Never raises — returns (None, None, reasons) on exception.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy-loaded tldextract
_tldextract = None

# Stopwords for is_plausible_company_name (single-token filter)
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "it", "its", "this", "that", "these", "those", "he", "she", "they",
    "we", "you", "i", "me", "my", "our", "your", "his", "her", "their",
    "not", "no", "nor", "so", "if", "then", "else", "when", "where",
    "how", "what", "which", "who", "whom", "why", "all", "each", "every",
    "both", "few", "more", "most", "some", "any", "new", "old", "big",
    "small", "long", "short", "high", "low", "good", "bad", "great",
    "first", "last", "next", "other", "much", "many", "just", "also",
    "about", "up", "out", "into", "over", "after", "before", "between",
    "under", "above", "below", "here", "there", "now", "today", "news",
    "update", "report", "article", "source", "via", "per", "etc",
    "unknown", "none", "null", "na", "n/a", "tbd",
})


def _get_tldextract():
    """Lazy-load tldextract with offline mode (no network requests)."""
    global _tldextract
    if _tldextract is None:
        import tldextract
        _tldextract = tldextract.TLDExtract(suffix_list_urls=())
    return _tldextract


def _slug(s: str) -> str:
    """Lowercase, keep [a-z0-9], collapse separators to '-'.

    Matches the _slug() function in utils/canonical_keys.py.
    """
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def extract_target_domain(raw_data: Dict[str, Any]) -> Optional[str]:
    """Extract a plausible target company domain from raw signal data.

    Tries these fields in order: company_url, company_domain, website,
    homepage_url, url. Returns the registered domain (e.g. 'acme.ai')
    or None if no valid domain found or domain is a publisher.
    """
    from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS

    url_fields = ["company_url", "company_domain", "website", "homepage_url", "url"]

    for field in url_fields:
        value = raw_data.get(field)
        if not value or not isinstance(value, str):
            continue

        value = value.strip()
        if not value:
            continue

        try:
            ext = _get_tldextract()
            result = ext(value)
            if result.domain and result.suffix:
                domain = f"{result.domain}.{result.suffix}"
                if domain.lower() not in NEWS_PUBLISHER_DOMAINS:
                    return domain.lower()
        except Exception:
            continue

    return None


def is_plausible_company_name(name: str) -> bool:
    """Check if a string is a plausible company name per Appendix A.

    Rules:
    - 2 <= len(stripped) <= 60
    - Contains at least one letter [A-Za-z]
    - Token count in [1, 6]
    - Single-token names must not be in STOPWORDS
    - Must not be a news publisher domain
    """
    from utils.canonical_keys import NEWS_PUBLISHER_DOMAINS

    if not name or not isinstance(name, str):
        return False

    stripped = name.strip()
    if len(stripped) < 2 or len(stripped) > 60:
        return False

    if not re.search(r"[A-Za-z]", stripped):
        return False

    tokens = stripped.split()
    if len(tokens) < 1 or len(tokens) > 6:
        return False

    if len(tokens) == 1 and tokens[0].lower() in _STOPWORDS:
        return False

    # Reject if the name looks like a publisher domain
    if stripped.lower() in NEWS_PUBLISHER_DOMAINS:
        return False

    return True


def build_canonical_key_v2(
    raw_data: Any,
    source_api: str,
    signal_type: str,
    canonical_key: str,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Build a domain-first canonical key v2.

    Returns: (key_or_none, key_type, reasons[])
      - key_type is one of: "domain", "name_loc", "unlinked_buzz", None
      - reasons collects diagnostic info for audit

    Never raises.
    """
    reasons: List[str] = []

    try:
        # Parse raw_data if it's a JSON string
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                reasons.append("raw_data is not valid JSON")
                raw_data = {}

        if not isinstance(raw_data, dict):
            raw_data = {}
            reasons.append("raw_data is not a dict")

        # Step 1: Try domain extraction
        domain = extract_target_domain(raw_data)
        if domain:
            reasons.append(f"domain extracted: {domain}")
            return (f"domain:{domain}", "domain", reasons)

        # Step 1b: DNS probe fallback — use dns_probe_domain if standard
        # extraction found nothing and this signal was DNS-promoted
        if not domain:
            dns_domain = raw_data.get("dns_probe_domain")
            if dns_domain and isinstance(dns_domain, str) and raw_data.get("dns_promoted"):
                domain = dns_domain.strip().lower()
                if domain:
                    reasons.append(f"domain from dns_probe: {domain}")
                    return (f"domain:{domain}", "domain", reasons)

        # Step 2: Try name_loc from company_name
        company_name = raw_data.get("company_name", "")
        if not company_name and canonical_key:
            # Try to extract company name from canonical_key if it's name_loc
            if canonical_key.startswith("name_loc:"):
                # Reuse existing name_loc key
                reasons.append(f"reusing existing name_loc key: {canonical_key}")
                return (canonical_key, "name_loc", reasons)

        if company_name and is_plausible_company_name(company_name):
            # Build name_loc key using _slug pattern
            name = _slug(company_name)
            if name:
                key = f"name_loc:{name}"
                reasons.append(f"name_loc from company_name: {company_name}")
                return (key, "name_loc", reasons)

        # Fallback: check if existing canonical_key is name_loc
        if canonical_key and canonical_key.startswith("name_loc:"):
            reasons.append(f"reusing existing name_loc key: {canonical_key}")
            return (canonical_key, "name_loc", reasons)

        # Step 3: Synthetic key for public_buzz signals
        from verification.evidence_families import get_family

        family = get_family(signal_type, source_api)
        if family == "public_buzz" and canonical_key:
            digest = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:16]
            key = f"name_loc:unlinked_buzz_{digest}"
            reasons.append(f"synthetic unlinked_buzz for public_buzz signal")
            return (key, "unlinked_buzz", reasons)

        reasons.append("no domain, no plausible name, not public_buzz")
        return (None, None, reasons)

    except Exception as exc:
        logger.warning("canonical_key_v2 build failed: %s", exc, exc_info=True)
        reasons.append(f"exception: {exc}")
        return (None, None, reasons)
