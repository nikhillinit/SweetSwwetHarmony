"""
Lightweight in-process instrumentation for the Discovery Engine.

Provides:
- Latency counters per endpoint/operation
- Lock wait time tracking
- Action failure rate metrics
- Thread-safe, zero-dependency (no Prometheus/StatsD required)
- Snapshot export for health endpoints

Usage:
    from utils.instrumentation import metrics

    with metrics.timer("triage.list"):
        results = await fetch_triage_list()

    metrics.increment("triage.approve.success")
    metrics.increment("triage.approve.failure")

    snapshot = metrics.snapshot()
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class _TimerStats:
    """Accumulated timing statistics for a named operation."""

    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0

    def record(self, elapsed_ms: float) -> None:
        self.count += 1
        self.total_ms += elapsed_ms
        self.min_ms = min(self.min_ms, elapsed_ms)
        self.max_ms = max(self.max_ms, elapsed_ms)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total_ms": round(self.total_ms, 2),
            "avg_ms": round(self.avg_ms, 2),
            "min_ms": round(self.min_ms, 2) if self.min_ms != float("inf") else 0,
            "max_ms": round(self.max_ms, 2),
        }


class Metrics:
    """Thread-safe in-process metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._timers: dict[str, _TimerStats] = defaultdict(_TimerStats)

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a named counter."""
        with self._lock:
            self._counters[name] += amount

    def get_count(self, name: str) -> int:
        """Read a counter value."""
        with self._lock:
            return self._counters.get(name, 0)

    @contextmanager
    def timer(self, name: str):
        """Context manager that records elapsed time for a named operation.

        Usage:
            with metrics.timer("db.query"):
                await db.execute(...)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            with self._lock:
                self._timers[name].record(elapsed_ms)

    def record_timing(self, name: str, elapsed_ms: float) -> None:
        """Manually record a timing measurement."""
        with self._lock:
            self._timers[name].record(elapsed_ms)

    def get_timer(self, name: str) -> dict[str, Any]:
        """Get timer stats for a named operation."""
        with self._lock:
            stats = self._timers.get(name)
            return stats.to_dict() if stats else _TimerStats().to_dict()

    def snapshot(self) -> dict[str, Any]:
        """Export a snapshot of all metrics for health/diagnostics endpoints."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "timers": {
                    name: stats.to_dict()
                    for name, stats in self._timers.items()
                },
            }

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        with self._lock:
            self._counters.clear()
            self._timers.clear()


# Module-level singleton
metrics = Metrics()
