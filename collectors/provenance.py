"""
Provenance helpers for collectors.

Standardizes provenance fields across all collectors to ensure
every downstream decision is traceable to what was observed, when,
where, and how confident we are.

Required provenance fields (Glass.AI principles):
- source_url: Direct link to evidence
- retrieved_at: Timestamp of retrieval
- source_response_hash: SHA256 hash for audit trail
- query_params: What was queried (endpoint, parameters)

Usage:
    from collectors.provenance import create_provenance, hash_response

    provenance = create_provenance(
        source_url="https://api.github.com/repos/owner/repo",
        response_data=api_response,
        endpoint="/repos/{owner}/{repo}",
        query_params={"state": "all"}
    )

    # Add to raw_data
    raw_data = {
        **provenance,
        "company_name": "Acme Inc",
        ...
    }
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# URL patterns indicating a container/search endpoint rather than
# a specific evidence page. Signals with these URLs produce weak
# evidence_keys (many articles share the same search URL).
CONTAINER_URL_PATTERNS = [
    "/search?", "/api/v", "/feed", "/rss",
    "api.github.com/search", "gnews.io/api",
]


def hash_response(data: Any) -> str:
    """
    Create a deterministic hash of API response data.

    Uses SHA256 and sorts keys for consistency.

    Args:
        data: Response data (dict, list, or primitive)

    Returns:
        First 16 characters of SHA256 hash
    """
    if data is None:
        return ""

    try:
        # Normalize to JSON string with sorted keys
        if isinstance(data, (dict, list)):
            serialized = json.dumps(data, sort_keys=True, default=str)
        else:
            serialized = str(data)

        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
    except Exception:
        # Fallback: hash the string representation
        return hashlib.sha256(str(data).encode()).hexdigest()[:16]


def create_provenance(
    source_url: str,
    response_data: Any = None,
    endpoint: Optional[str] = None,
    query_params: Optional[Dict[str, Any]] = None,
    retrieved_at: Optional[datetime] = None,
    source_response_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create standardized provenance fields for a signal.

    Args:
        source_url: Direct URL to the evidence/source
        response_data: Raw API response (used for hashing if hash not provided)
        endpoint: API endpoint used (e.g., "/repos/{owner}/{repo}")
        query_params: Query parameters sent to API
        retrieved_at: Timestamp of retrieval (defaults to now)
        source_response_hash: Pre-computed hash (computed from response_data if not provided)

    Returns:
        Dict with standardized provenance fields
    """
    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc)

    if source_response_hash is None and response_data is not None:
        source_response_hash = hash_response(response_data)

    provenance = {
        "_provenance": {
            "source_url": source_url,
            "retrieved_at": retrieved_at.isoformat(),
            "source_response_hash": source_response_hash or "",
            "endpoint": endpoint or "",
            "query_params": query_params or {},
        }
    }

    return provenance


def extract_provenance(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract provenance from raw_data.

    Args:
        raw_data: Signal raw_data dict

    Returns:
        Provenance dict or empty dict if not found
    """
    return raw_data.get("_provenance", {})


def has_provenance(raw_data: Dict[str, Any]) -> bool:
    """
    Check if raw_data has complete provenance.

    Args:
        raw_data: Signal raw_data dict

    Returns:
        True if required provenance fields are present
    """
    prov = extract_provenance(raw_data)
    return bool(
        prov.get("source_url") and
        prov.get("retrieved_at") and
        prov.get("source_response_hash")
    )


def validate_provenance(raw_data: Dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate provenance fields and return any issues.

    Args:
        raw_data: Signal raw_data dict

    Returns:
        (is_valid, list of issues)
    """
    issues = []
    prov = extract_provenance(raw_data)

    if not prov:
        return False, ["Missing _provenance block"]

    if not prov.get("source_url"):
        issues.append("Missing source_url")
    if not prov.get("retrieved_at"):
        issues.append("Missing retrieved_at")
    if not prov.get("source_response_hash"):
        issues.append("Missing source_response_hash")

    return len(issues) == 0, issues


def warn_if_container_url(source_url: str) -> Optional[str]:
    """Check if source_url looks like a container/search endpoint.

    Container URLs (search pages, API endpoints, RSS feeds) produce
    weak evidence_keys because many distinct articles share the same
    URL. This is a warning, not a hard reject — the signal is still
    saved, but the evidence_key may collide.

    Returns a warning string if suspicious, None if OK.
    """
    if not source_url:
        return None
    for pattern in CONTAINER_URL_PATTERNS:
        if pattern in source_url:
            warning = f"source_url appears to be a container/search URL: {source_url[:80]}"
            logger.warning(warning)
            return warning
    return None


class ProvenanceBuilder:
    """
    Builder for constructing provenance across multiple API calls.

    Useful when a signal requires data from multiple endpoints.

    Usage:
        builder = ProvenanceBuilder(base_url="https://api.github.com")
        builder.add_source("/repos/owner/repo", repo_response)
        builder.add_source("/users/owner", user_response)
        provenance = builder.build()
    """

    def __init__(self, base_url: str):
        """
        Initialize builder.

        Args:
            base_url: Base URL for all API calls
        """
        self.base_url = base_url.rstrip("/")
        self.sources: list[Dict[str, Any]] = []
        self.started_at = datetime.now(timezone.utc)

    def add_source(
        self,
        endpoint: str,
        response_data: Any,
        query_params: Optional[Dict[str, Any]] = None,
    ) -> "ProvenanceBuilder":
        """
        Add a source to the provenance chain.

        Args:
            endpoint: API endpoint (e.g., "/repos/owner/repo")
            response_data: Raw API response
            query_params: Query parameters used

        Returns:
            self for chaining
        """
        self.sources.append({
            "url": f"{self.base_url}{endpoint}",
            "endpoint": endpoint,
            "query_params": query_params or {},
            "response_hash": hash_response(response_data),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        })
        return self

    def build(self) -> Dict[str, Any]:
        """
        Build the final provenance dict.

        Returns:
            Provenance dict with all sources
        """
        if not self.sources:
            return create_provenance(source_url="", response_data=None)

        # Primary source is the first one added
        primary = self.sources[0]

        # Combine all response hashes for the overall hash
        combined_hash = hash_response([s["response_hash"] for s in self.sources])

        return {
            "_provenance": {
                "source_url": primary["url"],
                "retrieved_at": self.started_at.isoformat(),
                "source_response_hash": combined_hash,
                "endpoint": primary["endpoint"],
                "query_params": primary["query_params"],
                "sources": self.sources,  # Full chain for audit
            }
        }
