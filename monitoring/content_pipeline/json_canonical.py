"""
JSON Canonicalization Utilities.

Provides deterministic JSON output through:
- Sorted keys for consistent ordering
- Volatile key detection and removal (timestamps, build IDs, session tokens)
- Configurable patterns for custom volatile key definitions

Used to compare JSON snapshots without false positives from runtime values.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# Default patterns for volatile keys (case-insensitive)
# These patterns match keys that change between requests/builds
DEFAULT_VOLATILE_PATTERNS: List[str] = [
    # Timestamps
    r"timestamp",
    r"created_?at",
    r"updated_?at",
    r"modified_?at",
    r"last_?modified",
    r"date$",
    r"_time$",
    # Build/deployment identifiers
    r"build_?id",
    r"deployment_?id",
    r"^version$",
    r"app_?version",
    r"release_?id",
    # Session/auth tokens
    r"session_?id",
    r"csrf_?token",
    r"^nonce$",
    r"^token$",
    r"access_?token",
    r"refresh_?token",
    r"auth_?token",
    # Request tracking
    r"request_?id",
    r"trace_?id",
    r"correlation_?id",
    r"span_?id",
    # Next.js runtime flags
    r"^__N_SSG$",
    r"^__N_SSP$",
    r"^__N_RSC$",
    r"^rsc$",
    r"^__flight",
    # Cache/etag
    r"^etag$",
    r"^cache_?key$",
    # Random/generated
    r"^uuid$",
    r"^guid$",
    r"random",
    r"^hash$",
]


@dataclass
class CanonicalizeOptions:
    """Options for JSON canonicalization."""

    # Whether to remove volatile keys
    remove_volatile: bool = True

    # Custom patterns for volatile key detection (replaces defaults if set)
    volatile_patterns: Optional[List[str]] = None

    # Indentation for output (None = compact, 2 = pretty)
    indent: Optional[int] = None

    # Whether to ensure ASCII output
    ensure_ascii: bool = False


def is_volatile_key(
    key: str,
    patterns: Optional[List[str]] = None,
) -> bool:
    """
    Check if a key matches volatile patterns.

    Volatile keys are those that change between requests/builds,
    such as timestamps, build IDs, and session tokens.

    Args:
        key: The key name to check
        patterns: Optional custom patterns (uses DEFAULT_VOLATILE_PATTERNS if not provided)

    Returns:
        True if the key matches a volatile pattern
    """
    if patterns is None:
        patterns = DEFAULT_VOLATILE_PATTERNS

    for pattern in patterns:
        if re.search(pattern, key, re.IGNORECASE):
            return True

    return False


def remove_volatile_keys(
    data: Any,
    patterns: Optional[List[str]] = None,
) -> Any:
    """
    Recursively remove volatile keys from a data structure.

    Walks through dicts and lists, removing keys that match
    volatile patterns while preserving all other data.

    Args:
        data: The data structure to process (dict, list, or primitive)
        patterns: Optional custom patterns for volatile key detection

    Returns:
        Copy of data with volatile keys removed
    """
    if isinstance(data, dict):
        return {
            k: remove_volatile_keys(v, patterns)
            for k, v in data.items()
            if not is_volatile_key(k, patterns)
        }
    elif isinstance(data, list):
        return [remove_volatile_keys(item, patterns) for item in data]
    else:
        return data


def canonicalize_json(
    data: Union[str, Dict[str, Any]],
    options: Optional[CanonicalizeOptions] = None,
) -> str:
    """
    Produce canonical JSON output.

    Canonicalization includes:
    1. Parsing JSON string input (if provided as string)
    2. Removing volatile keys (if remove_volatile=True)
    3. Sorting all keys alphabetically
    4. Producing deterministic JSON output

    Args:
        data: JSON data (dict) or JSON string
        options: Canonicalization options

    Returns:
        Canonical JSON string

    Example:
        >>> canonicalize_json({"z": 1, "a": 2, "buildId": "abc"})
        '{"a": 2, "z": 1}'
    """
    if options is None:
        options = CanonicalizeOptions()

    # Parse string input if needed
    if isinstance(data, str):
        data = json.loads(data)

    # Remove volatile keys if enabled
    if options.remove_volatile:
        patterns = options.volatile_patterns  # May be None, which uses defaults
        data = remove_volatile_keys(data, patterns)

    # Serialize with sorted keys
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=options.ensure_ascii,
        indent=options.indent,
        separators=(", ", ": ") if options.indent is None else None,
    )
