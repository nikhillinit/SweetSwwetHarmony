"""JSON DSL rule condition evaluator.

Evaluates condition dicts against snapshot dicts (from OpsMetricsSnapshot.to_dict()).
Safe: no eval(), no exec(), pure data comparison.

Condition types:
- Simple:    {"field": "total_cost_24h", "op": ">", "value": 5.0}
- All (AND): {"all": [condition, ...]}
- Any (OR):  {"any": [condition, ...]}
- Not:       {"not": condition}
- Trend:     {"trend": {"field": "...", "direction": "increasing", "window": 3}}
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Supported comparison operators
_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def evaluate_condition(
    condition: dict,
    snapshot: dict,
    history: Optional[list] = None,
) -> bool:
    """Evaluate a JSON DSL condition against a snapshot dict.

    Args:
        condition: JSON DSL condition dict.
        snapshot: OpsMetricsSnapshot.to_dict() result (or enriched dict).
        history: List of past snapshot dicts, newest-first (for trend conditions).
    """
    if "field" in condition and "op" in condition:
        return _eval_simple(condition, snapshot)
    if "all" in condition:
        return all(
            evaluate_condition(c, snapshot, history) for c in condition["all"]
        )
    if "any" in condition:
        return any(
            evaluate_condition(c, snapshot, history) for c in condition["any"]
        )
    if "not" in condition:
        return not evaluate_condition(condition["not"], snapshot, history)
    if "trend" in condition:
        return _eval_trend(condition["trend"], history)
    raise ValueError(f"Unknown condition type: {condition}")


def condition_to_check(
    condition: dict,
    storage=None,
) -> Callable:
    """Convert a JSON DSL condition to a callable check(snapshot) -> bool.

    The returned callable accepts an OpsMetricsSnapshot (or dict) and
    returns True if the condition is met.

    For trend conditions, ``storage`` is needed to fetch metric history.
    """

    def check(snapshot):
        snapshot_dict = (
            snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot
        )
        hist = None
        if _has_trend(condition) and storage is not None:
            snaps = storage.get_metric_snapshots(hours=24 * 7, limit=20)
            hist = [s["snapshot"] for s in snaps]
        return evaluate_condition(condition, snapshot_dict, hist)

    return check


# ── Internal helpers ────────────────────────────────────────────────────


def _resolve_field(snapshot: dict, field_path: str) -> Any:
    """Resolve a dot-notation field path against a snapshot dict."""
    parts = field_path.split(".")
    current = snapshot
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _coerce_numeric(value: Any) -> Any:
    """Try to coerce a value to float for numeric comparison."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    return value


def _eval_simple(condition: dict, snapshot: dict) -> bool:
    """Evaluate a simple field/op/value condition."""
    field_val = _resolve_field(snapshot, condition["field"])
    if field_val is None:
        return False

    op = condition["op"]
    if op not in _OPS:
        raise ValueError(f"Unknown operator: {op}")

    threshold = condition["value"]
    field_val = _coerce_numeric(field_val)
    threshold = _coerce_numeric(threshold)

    return _OPS[op](field_val, threshold)


def _eval_trend(trend: dict, history: Optional[list]) -> bool:
    """Evaluate a trend condition against snapshot history."""
    if not history:
        return False

    field = trend["field"]
    direction = trend["direction"]
    window = trend.get("window", 3)

    if len(history) < window:
        return False

    # history is newest-first; take the most recent `window` entries
    recent = history[:window]
    values = []
    for snap in recent:
        val = _resolve_field(snap, field)
        if val is None:
            return False
        values.append(_coerce_numeric(val))

    # Reverse to chronological order (oldest first)
    values.reverse()

    if direction == "increasing":
        return all(values[i] < values[i + 1] for i in range(len(values) - 1))
    if direction == "decreasing":
        return all(values[i] > values[i + 1] for i in range(len(values) - 1))
    raise ValueError(f"Unknown trend direction: {direction}")


def _has_trend(condition: dict) -> bool:
    """Check if a condition tree contains any trend sub-conditions."""
    if "trend" in condition:
        return True
    if "all" in condition:
        return any(_has_trend(c) for c in condition["all"])
    if "any" in condition:
        return any(_has_trend(c) for c in condition["any"])
    if "not" in condition:
        return _has_trend(condition["not"])
    return False
