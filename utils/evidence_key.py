"""Evidence-key computation for idempotent signal dedup.

evidence_key = sha256(source_api + "\\x1f" + normalize_url(source_url))[:32]

The key is stable across pipeline runs because it depends only on the
source URL (which identifies the evidence) and the source API (which
namespaces it). Volatile fields like detected_at, source_response_hash,
and star counts are intentionally excluded.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger(__name__)

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "gclid", "ref", "source", "campaign_id", "mc_cid", "mc_eid",
})


def normalize_url(url: str) -> str:
    """Normalize URL for evidence_key computation.

    1. Strip whitespace
    2. Default scheme to https if missing
    3. Lowercase scheme + host (NOT path — paths can be case-sensitive)
    4. Strip www. prefix from host
    5. Remove trailing slash from path (unless path is just "/")
    6. Remove fragment (#...)
    7. Remove tracking query params (utm_*, fbclid, gclid, etc.)
    8. Sort remaining query params for deterministic ordering
    9. Reassemble: scheme://host/path[?sorted_query]

    Returns empty string if URL is empty/None/invalid.
    """
    if not url or not isinstance(url, str):
        return ""

    url = url.strip()
    if not url:
        return ""

    # Default scheme to https if missing
    if "://" not in url and not url.startswith("//"):
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return ""

    # Strip www. prefix
    if host.startswith("www."):
        host = host[4:]

    # Preserve port if non-standard
    port = parsed.port
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        netloc = f"{host}:{port}"
    else:
        netloc = host

    # Path: remove trailing slash (unless root)
    path = parsed.path
    if path and path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Query params: strip tracking, sort remainder
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in sorted(query_params.items())
        if k.lower() not in TRACKING_PARAMS
    }
    query = urlencode(filtered, doseq=True) if filtered else ""

    # Reassemble without fragment
    return urlunparse((scheme, netloc, path, "", query, ""))


def compute_evidence_key(source_api: str, source_url: str) -> str:
    """Compute evidence_key hash.

    Returns sha256(source_api + "\\x1f" + normalize_url(source_url))[:32]
    Returns empty string if source_url is empty/None.

    NOTE: source_response_hash is intentionally EXCLUDED from the key.
    It's volatile (payload drift from stars, timestamps, etc.) and would
    recreate the same failure mode as the detected_at bug.
    """
    if not source_url:
        return ""

    normalized = normalize_url(source_url)
    if not normalized:
        return ""

    payload = source_api + "\x1f" + normalized
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def extract_source_url_from_raw_data(raw_data: Dict[str, Any]) -> str:
    """Extract source URL from raw_data dict.

    Looks for source_url in:
    1. raw_data["_provenance"]["source_url"] (provenance block)
    2. raw_data["url"] (fallback for news_api/rss_feeds)

    Returns empty string if no source_url found.
    Used by save_signal() fallback and backfill script.
    """
    if not isinstance(raw_data, dict):
        return ""

    # Try provenance block first
    prov = raw_data.get("_provenance")
    if isinstance(prov, dict):
        url = prov.get("source_url", "")
        if url:
            return url

    # Fallback: top-level "url" key (news_api, rss_feeds)
    return raw_data.get("url", "")
