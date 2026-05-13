"""Production middleware for the Discovery Engine API.

ExceptionHandlerMiddleware — catch-all for unhandled exceptions, returns clean JSON 500.
RequestIdMiddleware — injects X-Request-ID (UUID4) into every request/response.
RateLimitMiddleware — per-IP rate limiting with path-based tiers.
"""

import asyncio
import logging
import time
import uuid
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from utils.logging_config import set_request_id

logger = logging.getLogger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"

# ---------------------------------------------------------------------------
# Rate limit configuration
# ---------------------------------------------------------------------------

# Requests per 60-second sliding window, per IP
RATE_LIMIT_DEFAULT = 100  # Global default
RATE_LIMIT_WRITE = 20     # Write-heavy endpoints

# Write-heavy endpoint prefixes — stricter limit
WRITE_PREFIXES = (
    "/api/v1/triage",
    "/api/v1/batches",
    "/api/v1/actions",
    "/api/v1/hunter",
    "/api/v1/entities",  # merge_review shares /entities prefix
)

# Health/read endpoints — exempt from rate limiting
EXEMPT_PREFIXES = (
    "/health",
    "/api/v1/health",
)

WINDOW_SECONDS = 60


# ---------------------------------------------------------------------------
# In-memory sliding window rate tracker
# ---------------------------------------------------------------------------

class _RateTracker:
    """Async-safe sliding window rate counter.

    Tracks request timestamps per key (IP + tier) and counts how many
    fall within the current window.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # key -> list of timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def check_and_record(
        self,
        key: str,
        limit: int,
        now: float | None = None,
    ) -> tuple[bool, int]:
        """Check if the request is within the rate limit and record it.

        Returns (allowed, remaining) where remaining is the number of
        requests left in the current window.
        """
        now = now or time.monotonic()
        window_start = now - WINDOW_SECONDS

        async with self._lock:
            timestamps = self._requests[key]
            # Prune expired entries
            self._requests[key] = [t for t in timestamps if t > window_start]
            timestamps = self._requests[key]

            count = len(timestamps)
            if count >= limit:
                return False, 0

            timestamps.append(now)
            return True, limit - count - 1

    def reset(self) -> None:
        """Clear all tracked state (for testing)."""
        self._requests.clear()


_tracker = _RateTracker()


# ---------------------------------------------------------------------------
# Rate Limit Middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiting with path-based tiers.

    Tiers:
    - Exempt: /health, /api/v1/health — no limit
    - Write: /api/v1/triage, /batches, /actions, /hunter, /entities — 20/min
    - Default: everything else — 100/min

    Returns 429 with error envelope + Retry-After header when exceeded.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Exempt paths
        if path == "/" or any(path.startswith(p) for p in EXEMPT_PREFIXES):
            response = await call_next(request)
            # No rate limit headers for exempt endpoints
            return response

        # Determine limit tier
        if any(path.startswith(p) for p in WRITE_PREFIXES):
            limit = RATE_LIMIT_WRITE
            tier = "write"
        else:
            limit = RATE_LIMIT_DEFAULT
            tier = "default"

        # Extract client IP
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{tier}"

        allowed, remaining = await _tracker.check_and_record(key, limit)

        if not allowed:
            request_id = getattr(request.state, "request_id", None)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded: {limit} per {WINDOW_SECONDS}s",
                    "detail": None,
                    "request_id": request_id,
                },
                headers={
                    "Retry-After": str(WINDOW_SECONDS),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(WINDOW_SECONDS),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(WINDOW_SECONDS)
        return response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request/response cycle.

    - If the client sends an ``X-Request-ID`` header, it is reused.
    - Otherwise a new UUID4 is generated.
    - The ID is stored on ``request.state.request_id`` for downstream use
      and returned in the ``X-Request-ID`` response header.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)  # propagate to logging context

        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """Global catch-all for unhandled exceptions.

    Returns a clean JSON ``{"error": "Internal Server Error", "request_id": ...}``
    response with status 500.  Stack traces are logged server-side but never
    exposed to the client.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            request_id = getattr(request.state, "request_id", None)
            logger.exception(
                "Unhandled exception [request_id=%s] %s %s",
                request_id,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Server Error",
                    "request_id": request_id,
                },
                headers={_REQUEST_ID_HEADER: request_id} if request_id else {},
            )
