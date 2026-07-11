from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Pattern, Sequence

from ..cli_errors import MISSING_BINARY_HINT

if TYPE_CHECKING:
    from .adapters import ExecutorResult
    from .config import RoutingConfig

FAILURE_SPAWN_ERROR = "spawn_error"
FAILURE_RATE_LIMITED = "rate_limited"
FAILURE_TIMEOUT = "timeout"
FAILURE_NONZERO_EXIT = "nonzero_exit"

_TIMEOUT_HINTS = ("timed out", "timeout")
_SPAWN_HINTS = (
    "no such file",
    "not recognized as an internal or external command",
    "cannot find the file",
    "winerror 2",
    "enoent",
    "executable not found",
    # Shared wrapper contract (integrations.cli_errors.MISSING_BINARY_ERROR):
    # all three production wrappers report a missing binary with this wording.
    MISSING_BINARY_HINT.lower(),
)

_RELATIVE_RETRY = re.compile(
    r"(?:retry\s*after|try\s*again\s*in)\s*:?\s*(\d+(?:\.\d+)?)\s*"
    r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|[smh]\b)?",
    re.IGNORECASE,
)
_ABSOLUTE_RETRY = re.compile(
    r"(?:resets?\s*(?:at|on)|reset\s*time)\s*:?\s*([^\r\n;]+)",
    re.IGNORECASE,
)


def classify_execution(
    result: "ExecutorResult",
    signatures: Sequence[str | Pattern[str]] = (),
) -> str | None:
    """Classify a failed executor result; ``None`` means success."""
    if result.success:
        return None

    text = "\n".join(part for part in (result.error, result.content) if part)
    for signature in signatures:
        if isinstance(signature, re.Pattern):
            if signature.search(text):
                return FAILURE_RATE_LIMITED
        elif re.search(re.escape(str(signature)), text, re.IGNORECASE):
            return FAILURE_RATE_LIMITED

    lower = text.lower()
    if any(hint in lower for hint in _TIMEOUT_HINTS):
        return FAILURE_TIMEOUT
    if any(hint in lower for hint in _SPAWN_HINTS):
        return FAILURE_SPAWN_ERROR
    return FAILURE_NONZERO_EXIT


def parse_retry_after(message: str, *, now: datetime) -> datetime | None:
    """Extract an explicit retry-after hint from provider output, if any."""
    text = str(message or "")

    relative = _RELATIVE_RETRY.search(text)
    if relative:
        amount = float(relative.group(1))
        unit = (relative.group(2) or "seconds").lower()
        if unit.startswith("h"):
            scale = 3600.0
        elif unit.startswith("m"):
            scale = 60.0
        else:
            scale = 1.0
        return now + timedelta(seconds=amount * scale)

    absolute = _ABSOLUTE_RETRY.search(text)
    if absolute:
        candidate = absolute.group(1).strip()
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return parsed if parsed > now else None

    return None


def compile_rate_limit_signatures(
    config: "RoutingConfig",
) -> dict[str, list[Pattern[str]]]:
    """Compile per-executor rate-limit signature regexes; empty when disabled."""
    if not config.rate_limits.enabled:
        return {}
    compiled: dict[str, list[Pattern[str]]] = {}
    for executor_name, patterns in config.rate_limits.signatures.items():
        compiled[executor_name] = [
            re.compile(pattern, re.IGNORECASE) for pattern in patterns
        ]
    return compiled
