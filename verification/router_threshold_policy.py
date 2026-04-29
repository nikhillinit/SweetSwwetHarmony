"""Phase 2 Day 5 — Router-threshold policy stub (non-production).

Tiny pure helper that decides whether a raw signal score passes a candidate
threshold under the Day 4 score-binding contract
(``decision_rule="accept_if_score_gte_threshold"``).

This module is **not wired into the live pipeline** in v1. ``workflows.pipeline``
must not import from here; an AST guard in the Day 5 test suite enforces that.
This module also does not import ``PushDecision`` — it is a pure score gate,
not a routing decision producer.
"""

from __future__ import annotations

import math


def raw_signal_passes_threshold(confidence: float, threshold_value: float) -> bool:
    """Return True iff ``confidence >= threshold_value``.

    Both arguments are expected to be in [0.0, 1.0]. NaN / out-of-range
    confidence raises ``ValueError`` — there is no safe default.
    """
    if math.isnan(confidence):
        raise ValueError("confidence is NaN")
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(
            f"confidence out of range [0.0, 1.0]: {confidence!r}"
        )
    return confidence >= threshold_value
