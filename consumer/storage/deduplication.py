"""
Content Hash Deduplication for Consumer Discovery Engine

Uses immutable source identifiers for stable fingerprinting.
Hash: SHA256(source_api|source_id)[:32]
"""

import hashlib
from typing import Dict, Any


def compute_content_hash(source_api: str, source_id: str) -> str:
    """
    Generate stable fingerprint for signal deduplication.

    Uses only immutable source identifiers (no entity names that could change).
    Returns 32-char hex string (128 bits) - collision-resistant for billions of records.

    Args:
        source_api: Source system identifier ('reddit', 'hn', 'bevnet_rss', 'uspto_tm')
        source_id: Original ID from source (immutable)

    Returns:
        32-character hex string

    Examples:
        >>> compute_content_hash("hn", "12345678")
        'a1b2c3d4e5f6...'
        >>> compute_content_hash("reddit", "abc123")
        'f1e2d3c4b5a6...'
    """
    fingerprint = f"{source_api}|{source_id}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:32]


def compute_content_hash_from_signal(signal_data: Dict[str, Any]) -> str:
    """
    Extract source identifiers from signal dict and compute hash.

    Args:
        signal_data: Dict with 'source_api' and 'source_id' keys

    Returns:
        32-character hex string
    """
    source_api = signal_data.get("source_api", "")
    source_id = signal_data.get("source_id", "")
    return compute_content_hash(source_api, source_id)


def normalize_source_id(source_api: str, raw_id: Any) -> str:
    """
    Normalize source IDs to consistent string format.

    Args:
        source_api: Source system identifier
        raw_id: Raw ID value (may be int, str, etc.)

    Returns:
        Normalized string ID
    """
    # Convert to string
    str_id = str(raw_id).strip()

    # Source-specific normalization
    if source_api == "hn":
        # HN IDs are numeric
        return str_id
    elif source_api == "reddit":
        # Reddit IDs may have prefixes (t3_ for posts)
        if str_id.startswith("t3_"):
            return str_id[3:]
        return str_id
    elif source_api == "bevnet_rss":
        # RSS GUIDs - use as-is
        return str_id
    elif source_api == "uspto_tm":
        # Trademark serial numbers
        return str_id.replace("-", "").replace(" ", "")

    return str_id
