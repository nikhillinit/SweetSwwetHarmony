"""Base utilities for shadow collectors.

Provides:
  - ShadowCollectorResult dataclass
  - persist_item helper that writes a single discovery to shadow_signals
  - rate-budget bookkeeping primitives

This is intentionally lightweight — shadow collectors are not first-class
production collectors. They share no inheritance with collectors/base.py and
deliberately do not implement the BaseCollector protocol so the cross-cutting
production CI lints (which assume production-collector contracts) do not
apply to them.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from analytics.shadow_sidecar import ShadowSidecar

logger = logging.getLogger(__name__)


@dataclass
class ShadowCollectorResult:
    """Outcome of one shadow collector run."""

    collector: str
    run_id: str
    items_collected: int = 0
    items_persisted: int = 0
    started_at: str = ""
    completed_at: str = ""
    notes: List[str] = field(default_factory=list)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id(collector: str) -> str:
    return f"{collector}-{uuid.uuid4().hex[:12]}"


def persist_shadow_signal(
    sidecar: ShadowSidecar,
    *,
    collector: str,
    canonical_key: Optional[str],
    company_name: Optional[str],
    confidence: float,
    raw_data: Dict[str, Any],
) -> int:
    """Write a single shadow signal row. Returns the row id."""
    now = utcnow_iso()
    return sidecar.shadow_write(
        """
        INSERT INTO shadow_signals
            (shadow_collector, canonical_key, company_name, confidence,
             raw_data, detected_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collector,
            canonical_key,
            company_name,
            float(confidence),
            json.dumps(raw_data, separators=(",", ":")),
            now,
            now,
        ),
    )


class RateBudget:
    """Simple wall-clock token-bucket rate limiter for shadow collectors.

    Not as sophisticated as the production rate_limiter — but sufficient for
    shadow collection that runs in supervised batches and must not bust an
    external API quota.

    Usage::

        budget = RateBudget(max_per_hour=2000)
        for item in items:
            budget.acquire()
            do_something(item)
    """

    def __init__(self, max_per_hour: int) -> None:
        if max_per_hour <= 0:
            raise ValueError("max_per_hour must be positive")
        self.max_per_hour = max_per_hour
        self._min_interval = 3600.0 / max_per_hour
        self._last_call: Optional[float] = None
        self._call_count = 0

    def acquire(self) -> None:
        now = time.monotonic()
        if self._last_call is not None:
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.monotonic()
        self._call_count += 1

    @property
    def call_count(self) -> int:
        return self._call_count
