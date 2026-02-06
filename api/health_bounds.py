"""Bounded query parameters for health endpoints.

Centralizes validation so all health endpoints share the same limits.
FastAPI returns 422 automatically when bounds are violated.
"""

from fastapi import Query


class BoundedParams:
    """Factory methods for bounded Query() parameters."""

    @staticmethod
    def hours(default: int = 24) -> int:
        return Query(default=default, ge=1, le=720, description="Time window in hours (max 30 days)")

    @staticmethod
    def limit(default: int = 100) -> int:
        return Query(default=default, ge=1, le=1000, description="Maximum results to return")

    @staticmethod
    def window_hours(default: int = 24) -> int:
        return Query(default=default, ge=1, le=720, description="Metrics window in hours")

    @staticmethod
    def history_days(default: int = 0) -> int:
        return Query(default=default, ge=0, le=365, description="History days to include")
