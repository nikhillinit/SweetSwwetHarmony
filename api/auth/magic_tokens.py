"""
Magic Token Authentication

Provides secure one-time-use tokens for email action links.
Tokens are JWT-encoded with nonce validation to prevent replay attacks.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from pydantic import BaseModel

from storage.signal_store import SignalStore


# Use env var in production, fallback for dev
SECRET_KEY = os.getenv("MAGIC_TOKEN_SECRET", "dev-secret-change-in-production")
ALGORITHM = "HS256"
DEFAULT_EXPIRY_DAYS = 7


class TokenPayload(BaseModel):
    """Decoded token payload."""
    canonical_key: str
    action: str
    nonce: str


class TokenError(Exception):
    """Token validation error."""
    pass


async def create_action_token(
    store: SignalStore,
    canonical_key: str,
    action: str,
    expires_in_days: int = DEFAULT_EXPIRY_DAYS,
) -> str:
    """
    Create a magic link token for a specific action.

    Args:
        store: SignalStore instance
        canonical_key: The company this token is for
        action: The action this token permits (track, pass, view)
        expires_in_days: Token validity period

    Returns:
        JWT-encoded token string
    """
    # Generate unique nonce
    nonce = secrets.token_urlsafe(16)

    # Reserve nonce in database (prevents replay)
    await store.reserve_token_nonce(
        nonce=nonce,
        canonical_key=canonical_key,
        action=action,
        expires_in_days=expires_in_days,
    )

    # Build JWT payload
    now = datetime.now(timezone.utc)
    payload = {
        "ck": canonical_key,
        "act": action,
        "jti": nonce,  # JWT ID = our nonce
        "iat": now.timestamp(),
        "exp": (now + timedelta(days=expires_in_days)).timestamp(),
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def peek_token(token: str) -> TokenPayload:
    """
    Validate a token WITHOUT consuming it (for confirmation pages).

    This only checks JWT validity and expiration, NOT nonce consumption.
    Use this for GET requests that show confirmation UI.

    Args:
        token: The JWT token to validate

    Returns:
        TokenPayload with canonical_key and action

    Raises:
        TokenError: If token is invalid or expired
    """
    try:
        # Decode JWT
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise TokenError(f"Invalid token: {e}")

    # Extract fields
    canonical_key = payload.get("ck")
    action = payload.get("act")
    nonce = payload.get("jti")

    if not all([canonical_key, action, nonce]):
        raise TokenError("Token missing required fields")

    return TokenPayload(
        canonical_key=canonical_key,
        action=action,
        nonce=nonce,
    )


async def consume_token(
    store: SignalStore,
    token: str,
) -> TokenPayload:
    """
    Validate and consume a magic link token (one-time use).

    Args:
        store: SignalStore instance
        token: The JWT token to validate

    Returns:
        TokenPayload with canonical_key and action

    Raises:
        TokenError: If token is invalid, expired, or already used
    """
    # First peek to validate JWT structure
    payload = peek_token(token)

    # Then atomically consume nonce (prevents replay)
    token_nonce = await store.consume_token_nonce(payload.nonce)

    if not token_nonce:
        raise TokenError("Token has already been used or is invalid")

    return payload


def create_magic_link_url(
    base_url: str,
    token: str,
) -> str:
    """
    Create a full magic link URL.

    Args:
        base_url: The API base URL (e.g., http://localhost:8000)
        token: The JWT token

    Returns:
        Full URL for the magic link
    """
    return f"{base_url}/api/v1/actions/execute?token={token}"
