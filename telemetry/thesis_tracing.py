"""
Provider-agnostic tracing helpers for thesis-path instrumentation.

Phase 0 implementation goals:
- no-op by default
- zero vendor dependencies
- env-driven backend selection
- safe redaction defaults
- lightweight in-memory tracer for tests
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_BACKENDS = {"noop", "langsmith", "phoenix"}


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse a boolean-ish env var value."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_trace_backend(value: Optional[str] = None) -> str:
    """Return the normalized trace backend, defaulting invalid values to noop."""
    backend = (value if value is not None else os.environ.get("LLM_TRACE_BACKEND", "noop")).strip().lower()
    if backend not in _VALID_BACKENDS:
        logger.warning("Invalid LLM_TRACE_BACKEND=%r, falling back to 'noop'", backend)
        return "noop"
    return backend


def should_include_raw_traces(value: Optional[str] = None) -> bool:
    """Whether raw prompt/response content may be sent to external traces."""
    if value is None:
        value = os.environ.get("LLM_TRACE_INCLUDE_RAW")
    return _parse_bool(value, default=False)


def get_success_sample_rate(value: Optional[str] = None) -> float:
    """Return a clamped success sampling rate."""
    raw = value if value is not None else os.environ.get("LLM_TRACE_SUCCESS_SAMPLE_RATE", "0.10")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid LLM_TRACE_SUCCESS_SAMPLE_RATE=%r, falling back to 0.10",
            raw,
        )
        return 0.10
    return max(0.0, min(1.0, parsed))


def should_sample_success(
    sample_rate: Optional[float] = None,
    *,
    random_value: Optional[float] = None,
) -> bool:
    """Determine whether a successful trace should be captured."""
    rate = get_success_sample_rate(str(sample_rate)) if sample_rate is not None else get_success_sample_rate()
    bucket = random.random() if random_value is None else random_value
    return bucket < rate


def summarize_text_payload(
    text: Optional[str],
    *,
    include_raw: bool = False,
    preview_chars: int = 120,
) -> dict[str, Any]:
    """Return a redaction-safe summary of a text payload."""
    if text is None:
        return {"present": False}

    normalized = text.strip()
    payload = {
        "present": True,
        "chars": len(normalized),
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }
    if include_raw:
        payload["preview"] = normalized[:preview_chars]
    return payload


def redact_error_message(message: Optional[str], *, max_chars: int = 200) -> str:
    """Clamp an error message to a safe length."""
    if not message:
        return ""
    return str(message).strip()[:max_chars]


@dataclass
class ThesisTraceSpan:
    """Internal span representation used by the default tracer implementations."""

    name: str
    started_at: float = field(default_factory=time.perf_counter)
    attributes: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False


class NoopThesisTracer:
    """Default tracer: does nothing, but preserves the tracing interface."""

    backend = "noop"

    def start_span(self, name: str, **attrs: Any) -> ThesisTraceSpan:
        span = ThesisTraceSpan(name=name)
        if attrs:
            span.attributes.update(attrs)
        return span

    def annotate(self, span: ThesisTraceSpan, **attrs: Any) -> None:
        span.attributes.update(attrs)

    def record_error(
        self,
        span: ThesisTraceSpan,
        *,
        error_kind: str,
        message: str,
        **attrs: Any,
    ) -> None:
        span.errors.append(
            {
                "error_kind": error_kind,
                "message": redact_error_message(message),
                **attrs,
            }
        )

    def finish(self, span: ThesisTraceSpan, **attrs: Any) -> None:
        span.finished = True
        span.attributes.update(attrs)


class MemoryThesisTracer(NoopThesisTracer):
    """In-memory tracer for tests and local debug assertions."""

    backend = "memory"

    def __init__(self) -> None:
        self.finished_spans: list[ThesisTraceSpan] = []

    def finish(self, span: ThesisTraceSpan, **attrs: Any) -> None:
        super().finish(span, **attrs)
        self.finished_spans.append(span)


def create_thesis_tracer(backend: Optional[str] = None) -> NoopThesisTracer:
    """
    Create a tracer for the configured backend.

    Vendor-specific backends are intentionally deferred; they currently map to
    the no-op implementation so the initial rollout has zero external deps.
    """
    resolved = get_trace_backend(backend)
    if resolved in {"noop", "langsmith", "phoenix"}:
        return NoopThesisTracer()
    return NoopThesisTracer()

