"""
Shared API contract definitions for the Discovery Engine.

Provides:
- ErrorEnvelope: Uniform error response schema
- IdempotencyKey: Header-based idempotency for mutation endpoints
- VersionedMixin: Optimistic concurrency via updated_at checks
- BaseResponse: Common wrapper for success responses

All routers MUST use these contracts for consistency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar

from fastapi import Header, HTTPException, Request, status
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# ERROR ENVELOPE
# =============================================================================

class ErrorDetail(BaseModel):
    """Structured error detail for client consumption."""

    error: str = Field(..., description="Machine-readable error code")
    code: str = Field(..., description="Application-specific error code")
    message: str = Field(..., description="Human-readable error message")
    detail: Optional[dict[str, Any]] = Field(
        default=None, description="Additional context for debugging"
    )
    request_id: Optional[str] = Field(
        default=None, description="Correlation ID from X-Request-ID header"
    )


def error_response(
    status_code: int,
    error: str,
    code: str,
    message: str,
    detail: Optional[dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> HTTPException:
    """Build a uniform HTTP error response.

    Usage:
        raise error_response(404, "not_found", "SIGNAL_NOT_FOUND",
                             "Signal 42 not found")
    """
    return HTTPException(
        status_code=status_code,
        detail=ErrorDetail(
            error=error,
            code=code,
            message=message,
            detail=detail,
            request_id=request_id,
        ).model_dump(exclude_none=True),
    )


# =============================================================================
# IDEMPOTENCY
# =============================================================================

# In-memory idempotency cache (TTL-based, cleared on restart).
# For production scale, swap with Redis or a DB table.
_IDEMPOTENCY_CACHE: dict[str, tuple[float, int, Any]] = {}
_IDEMPOTENCY_TTL_SECONDS = 3600  # 1 hour


class IdempotencyResult(BaseModel):
    """Returned when a duplicate request is detected."""

    cached: bool = True
    status_code: int
    body: Any


def check_idempotency(key: Optional[str]) -> Optional[IdempotencyResult]:
    """Check if an idempotency key has been seen before.

    Returns cached result if found and not expired, None otherwise.
    """
    if not key:
        return None

    entry = _IDEMPOTENCY_CACHE.get(key)
    if entry is None:
        return None

    ts, status_code, body = entry
    if time.time() - ts > _IDEMPOTENCY_TTL_SECONDS:
        del _IDEMPOTENCY_CACHE[key]
        return None

    return IdempotencyResult(status_code=status_code, body=body)


def store_idempotency(key: Optional[str], status_code: int, body: Any) -> None:
    """Store an idempotency result for future duplicate detection."""
    if not key:
        return
    _IDEMPOTENCY_CACHE[key] = (time.time(), status_code, body)

    # Lazy eviction: remove expired entries when cache exceeds threshold
    if len(_IDEMPOTENCY_CACHE) > 10_000:
        now = time.time()
        expired = [
            k
            for k, (ts, _, _) in _IDEMPOTENCY_CACHE.items()
            if now - ts > _IDEMPOTENCY_TTL_SECONDS
        ]
        for k in expired:
            del _IDEMPOTENCY_CACHE[k]


def clear_idempotency_cache() -> None:
    """Clear the idempotency cache (for testing)."""
    _IDEMPOTENCY_CACHE.clear()


# =============================================================================
# SQLITE-BACKED IDEMPOTENCY (L2 — survives restart)
# =============================================================================

_IDEMPOTENCY_DB_TTL_SECONDS = 86400  # 24 hours


async def check_idempotency_db(
    db,
    key: str,
    route: str,
    resource_id: str,
) -> Optional[IdempotencyResult]:
    """Check SQLite for a cached idempotency result (L2 cache).

    Returns cached result if found and not expired, None otherwise.
    """
    cursor = await db.execute(
        """SELECT status_code, response_body, created_at
           FROM idempotency_keys
           WHERE key = ? AND route = ? AND resource_id = ?""",
        (key, route, resource_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    created_at = row[2]
    # Check TTL
    try:
        created_dt = datetime.fromisoformat(created_at)
        age = (datetime.now(timezone.utc) - created_dt).total_seconds()
        if age > _IDEMPOTENCY_DB_TTL_SECONDS:
            await db.execute(
                "DELETE FROM idempotency_keys WHERE key = ? AND route = ? AND resource_id = ?",
                (key, route, resource_id),
            )
            await db.commit()
            return None
    except (ValueError, TypeError):
        pass

    body = json.loads(row[1]) if row[1] else None
    return IdempotencyResult(status_code=row[0], body=body)


async def check_idempotency_conflict(
    db,
    key: str,
    route: str,
    resource_id: str,
    payload_hash: str,
) -> None:
    """Raise 409 if same idempotency key was used with a different payload.

    This prevents accidental reuse of idempotency keys across different
    mutation payloads on the same resource.
    """
    cursor = await db.execute(
        """SELECT payload_hash FROM idempotency_keys
           WHERE key = ? AND route = ? AND resource_id = ?""",
        (key, route, resource_id),
    )
    row = await cursor.fetchone()
    if row is not None and row[0] != payload_hash:
        raise error_response(
            status_code=status.HTTP_409_CONFLICT,
            error="idempotency_conflict",
            code="IDEMPOTENCY_KEY_REUSE",
            message="Idempotency key was already used with a different payload.",
            detail={"key": key, "route": route, "resource_id": resource_id},
        )


async def store_idempotency_db(
    db,
    key: str,
    route: str,
    resource_id: str,
    payload_hash: str,
    status_code: int,
    body: Any,
) -> None:
    """Persist an idempotency result to SQLite (L2 cache).

    Uses INSERT OR IGNORE to handle race conditions cleanly.
    """
    await db.execute(
        """INSERT OR IGNORE INTO idempotency_keys
           (key, route, resource_id, payload_hash, status_code, response_body, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            key,
            route,
            resource_id,
            payload_hash,
            status_code,
            json.dumps(body, default=str),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


async def cleanup_expired_idempotency(db) -> int:
    """Delete expired idempotency keys. Returns count deleted."""
    cursor = await db.execute(
        """DELETE FROM idempotency_keys
           WHERE created_at < datetime('now', '-24 hours')"""
    )
    count = cursor.rowcount
    if count > 0:
        await db.commit()
        logger.info("Cleaned up %d expired idempotency keys", count)
    return count


def get_idempotency_key(
    idempotency_key: Optional[str] = Header(
        None, alias="Idempotency-Key", description="Client-supplied idempotency key"
    ),
) -> Optional[str]:
    """FastAPI dependency to extract the Idempotency-Key header."""
    return idempotency_key


# =============================================================================
# OPTIMISTIC CONCURRENCY
# =============================================================================

class VersionedMixin(BaseModel):
    """Mixin for resources that support optimistic concurrency.

    Clients must send `updated_at` from their last read.
    The server rejects writes if the resource has changed since.
    """

    updated_at: datetime = Field(
        ..., description="Last-modified timestamp for optimistic concurrency"
    )


def check_version(
    expected: Optional[str],
    actual: Optional[str],
    resource_name: str = "resource",
) -> None:
    """Raise 409 Conflict if expected != actual version (updated_at).

    Both values are ISO 8601 strings. If expected is None, skip check.
    """
    if expected is None:
        return
    if expected != actual:
        raise error_response(
            status_code=status.HTTP_409_CONFLICT,
            error="conflict",
            code="VERSION_MISMATCH",
            message=f"The {resource_name} was modified by another user. "
            "Please refresh and retry.",
            detail={"expected": expected, "actual": actual},
        )


# =============================================================================
# BASE RESPONSE WRAPPER
# =============================================================================

class BaseResponse(BaseModel, Generic[T]):
    """Uniform success response envelope."""

    data: T
    meta: Optional[dict[str, Any]] = None


class ListMeta(BaseModel):
    """Metadata for paginated list responses."""

    total: Optional[int] = Field(
        default=None, description="Total count (omitted for large sets)"
    )
    next_cursor: Optional[str] = Field(
        default=None, description="Cursor for next page (null if last page)"
    )
    has_more: bool = Field(default=False, description="Whether more pages exist")


class ListResponse(BaseModel, Generic[T]):
    """Uniform paginated list response envelope."""

    data: list[T]
    meta: ListMeta


# =============================================================================
# REQUEST HELPERS
# =============================================================================

def feature_disabled_response(
    feature_name: str,
    env_var_hint: str,
    request_id: Optional[str] = None,
) -> HTTPException:
    """Build a 423 Locked response for a disabled write feature.

    Usage:
        raise feature_disabled_response("merge_writes", "MERGE_WRITES_ENABLED")
    """
    return HTTPException(
        status_code=423,
        detail=ErrorDetail(
            error="locked",
            code="FEATURE_DISABLED",
            message=f"Write feature '{feature_name}' is currently disabled.",
            detail={"env_var": env_var_hint, "action": f"Set {env_var_hint}=active"},
            request_id=request_id,
        ).model_dump(exclude_none=True),
    )


def get_request_id(request: Request) -> Optional[str]:
    """Extract request ID from middleware-injected state."""
    return getattr(request.state, "request_id", None)


def inputs_hash(*args: Any) -> str:
    """Deterministic hash of inputs for reproducibility tracking."""
    serialised = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:16]


def payload_fingerprint(action: str, reason: str, actor_id: str) -> str:
    """Compute a fingerprint for a triage action payload.

    Used to detect idempotency key reuse with different payloads.
    """
    return hashlib.sha256(
        f"{action}:{reason}:{actor_id}".encode()
    ).hexdigest()[:16]
