"""
Failure Classification for Monitoring Subsystem

Categorizes fetch failures and calculates appropriate backoff strategies
per Spec v2.4 Section 10.4.

Categories:
- transient: 5xx, timeout, connection reset, DNS temporary
- client_error: 400, 403, 404, 410
- rate_limited: 429 (use Retry-After header if available)
- ssl_error: cert expired, hostname mismatch
- content_error: response too large, encoding failure
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Optional


class FailureCategory(str, Enum):
    """Failure categories with different retry behaviors."""
    TRANSIENT = "transient"
    CLIENT_ERROR = "client_error"
    RATE_LIMITED = "rate_limited"
    SSL_ERROR = "ssl_error"
    CONTENT_ERROR = "content_error"
    UNKNOWN = "unknown"


@dataclass
class FailureCategoryConfig:
    """Configuration for a failure category."""
    max_consecutive_failures: int
    backoff_type: str  # "exponential" or "fixed"
    backoff_values_minutes: list  # For exponential: [1, 5, 15, 60, 240], for fixed: [1440]


# Default configuration per category (from Spec v2.4 Section 10.4)
DEFAULT_CATEGORY_CONFIG = {
    FailureCategory.TRANSIENT: FailureCategoryConfig(
        max_consecutive_failures=10,
        backoff_type="exponential",
        backoff_values_minutes=[1, 5, 15, 60, 240],  # 1m, 5m, 15m, 1h, 4h (capped)
    ),
    FailureCategory.CLIENT_ERROR: FailureCategoryConfig(
        max_consecutive_failures=3,
        backoff_type="fixed",
        backoff_values_minutes=[1440],  # 24 hours
    ),
    FailureCategory.RATE_LIMITED: FailureCategoryConfig(
        max_consecutive_failures=5,
        backoff_type="fixed",
        backoff_values_minutes=[60],  # 1 hour (or use Retry-After)
    ),
    FailureCategory.SSL_ERROR: FailureCategoryConfig(
        max_consecutive_failures=2,
        backoff_type="fixed",
        backoff_values_minutes=[1440],  # 24 hours
    ),
    FailureCategory.CONTENT_ERROR: FailureCategoryConfig(
        max_consecutive_failures=5,
        backoff_type="fixed",
        backoff_values_minutes=[60],  # 1 hour
    ),
    FailureCategory.UNKNOWN: FailureCategoryConfig(
        max_consecutive_failures=5,
        backoff_type="fixed",
        backoff_values_minutes=[60],  # 1 hour (conservative)
    ),
}


@dataclass
class ClassifiedFailure:
    """Result of classifying a failure."""
    category: FailureCategory
    error_message: str
    retry_after_seconds: Optional[int] = None  # From Retry-After header


class FailureClassifier:
    """
    Classifies fetch failures into categories and calculates backoff.

    Usage:
        classifier = FailureClassifier()

        # Classify an error
        result = classifier.classify(status_code=503, error="Service unavailable")

        # Calculate backoff
        backoff = classifier.calculate_backoff(result.category, consecutive_failures=2)
    """

    def __init__(self, config: Optional[dict] = None):
        """
        Initialize classifier.

        Args:
            config: Optional override for category configuration
        """
        self.config = config or DEFAULT_CATEGORY_CONFIG

    def classify(
        self,
        status_code: Optional[int] = None,
        error: Optional[str] = None,
        retry_after: Optional[int] = None,
    ) -> ClassifiedFailure:
        """
        Classify a failure into a category.

        Args:
            status_code: HTTP status code (0 for connection errors)
            error: Error message string
            retry_after: Retry-After header value in seconds (if present)

        Returns:
            ClassifiedFailure with category and details
        """
        error_lower = (error or "").lower()

        # 1. Rate limited (429)
        if status_code == 429:
            return ClassifiedFailure(
                category=FailureCategory.RATE_LIMITED,
                error_message=error or "Rate limited (429)",
                retry_after_seconds=retry_after,
            )

        # 2. Client errors (4xx except 429)
        if status_code and 400 <= status_code < 500:
            return ClassifiedFailure(
                category=FailureCategory.CLIENT_ERROR,
                error_message=error or f"Client error ({status_code})",
            )

        # 3. Server errors (5xx) -> transient
        if status_code and 500 <= status_code < 600:
            return ClassifiedFailure(
                category=FailureCategory.TRANSIENT,
                error_message=error or f"Server error ({status_code})",
            )

        # 4. SSL errors (check error message)
        ssl_patterns = [
            "ssl", "certificate", "cert", "tls",
            "handshake", "hostname mismatch", "expired",
        ]
        if any(pattern in error_lower for pattern in ssl_patterns):
            return ClassifiedFailure(
                category=FailureCategory.SSL_ERROR,
                error_message=error or "SSL/TLS error",
            )

        # 5. Content errors
        content_patterns = [
            "too large", "content length", "encoding",
            "decode", "charset", "memory",
        ]
        if any(pattern in error_lower for pattern in content_patterns):
            return ClassifiedFailure(
                category=FailureCategory.CONTENT_ERROR,
                error_message=error or "Content error",
            )

        # 6. Transient network errors
        transient_patterns = [
            "timeout", "timed out", "connection",
            "reset", "refused", "dns", "resolve",
            "network", "unreachable", "temporary",
        ]
        if any(pattern in error_lower for pattern in transient_patterns):
            return ClassifiedFailure(
                category=FailureCategory.TRANSIENT,
                error_message=error or "Network error",
            )

        # 7. Status code 0 (connection failed) -> transient
        if status_code == 0:
            return ClassifiedFailure(
                category=FailureCategory.TRANSIENT,
                error_message=error or "Connection failed",
            )

        # 8. Unknown
        return ClassifiedFailure(
            category=FailureCategory.UNKNOWN,
            error_message=error or "Unknown error",
        )

    def calculate_backoff(
        self,
        category: FailureCategory,
        consecutive_failures: int,
        retry_after_seconds: Optional[int] = None,
    ) -> timedelta:
        """
        Calculate backoff duration for a failure category.

        Args:
            category: The failure category
            consecutive_failures: Number of consecutive failures (1-based)
            retry_after_seconds: Override from Retry-After header

        Returns:
            timedelta for backoff duration
        """
        # Use Retry-After if provided for rate limiting
        if category == FailureCategory.RATE_LIMITED and retry_after_seconds:
            return timedelta(seconds=retry_after_seconds)

        config = self.config.get(category, DEFAULT_CATEGORY_CONFIG[FailureCategory.UNKNOWN])

        if config.backoff_type == "exponential":
            # Exponential backoff with capped values
            idx = min(consecutive_failures - 1, len(config.backoff_values_minutes) - 1)
            idx = max(0, idx)  # Ensure non-negative
            minutes = config.backoff_values_minutes[idx]
        else:
            # Fixed backoff
            minutes = config.backoff_values_minutes[0]

        return timedelta(minutes=minutes)

    def should_deactivate(
        self,
        category: FailureCategory,
        consecutive_failures: int,
    ) -> bool:
        """
        Check if a watch should be deactivated due to repeated failures.

        Args:
            category: The failure category
            consecutive_failures: Number of consecutive failures

        Returns:
            True if watch should be deactivated
        """
        config = self.config.get(category, DEFAULT_CATEGORY_CONFIG[FailureCategory.UNKNOWN])
        return consecutive_failures >= config.max_consecutive_failures

    def get_deactivation_reason(
        self,
        category: FailureCategory,
    ) -> str:
        """Get the reason string for deactivation."""
        return f"max_failures:{category.value}"


# Convenience function for simple classification
def classify_failure(
    status_code: Optional[int] = None,
    error: Optional[str] = None,
    retry_after: Optional[int] = None,
) -> ClassifiedFailure:
    """
    Classify a failure (convenience function).

    Args:
        status_code: HTTP status code
        error: Error message
        retry_after: Retry-After header value

    Returns:
        ClassifiedFailure
    """
    return FailureClassifier().classify(status_code, error, retry_after)
