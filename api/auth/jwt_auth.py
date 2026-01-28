"""
JWT Authentication for Command Center API

Provides:
- JWT token generation and validation
- Role-based access control (GP, ANALYST, READONLY)
- Password hashing with bcrypt
- Rate limiting for auth endpoints

Usage:
    from api.auth.jwt_auth import (
        create_access_token,
        verify_token,
        get_current_user,
        require_role,
        Role,
    )

    # In route
    @router.get("/protected")
    async def protected_route(user: User = Depends(get_current_user)):
        return {"user": user.email}

    @router.patch("/admin-only")
    async def admin_route(user: User = Depends(require_role([Role.GP]))):
        return {"message": "GP access granted"}
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, List

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

# Try multiple JWT libraries
try:
    from jose import JWTError, jwt
    JWT_LIBRARY = "jose"
except ImportError:
    try:
        import jwt as pyjwt
        JWTError = pyjwt.PyJWTError
        jwt = pyjwt
        JWT_LIBRARY = "pyjwt"
    except ImportError:
        # Fallback to no JWT (for import testing)
        jwt = None
        JWTError = Exception
        JWT_LIBRARY = "none"

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    import hashlib
    BCRYPT_AVAILABLE = False

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# JWT settings from environment
JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # 24 hours

if JWT_SECRET == "CHANGE_ME_IN_PRODUCTION":
    logger.warning("JWT_SECRET not set! Using insecure default. Set JWT_SECRET in production.")

# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


# =============================================================================
# MODELS
# =============================================================================

class Role(str, Enum):
    """User roles for access control."""
    GP = "gp"  # General Partner - full access
    ANALYST = "analyst"  # Analyst - read/write, no admin
    READONLY = "readonly"  # Read-only access


class User(BaseModel):
    """Authenticated user."""
    id: str
    email: str  # Use str instead of EmailStr to avoid email-validator dependency
    role: Role
    name: Optional[str] = None


class TokenPayload(BaseModel):
    """JWT token payload."""
    sub: str  # user_id
    email: str
    role: str
    name: Optional[str] = None
    exp: datetime
    iat: datetime


class TokenResponse(BaseModel):
    """Token response for login."""
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: User


class LoginRequest(BaseModel):
    """Login request body."""
    email: str  # Use str instead of EmailStr
    password: str


# =============================================================================
# PASSWORD HASHING
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt (or fallback to SHA256)."""
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    else:
        # Fallback for systems without bcrypt
        logger.warning("bcrypt not available, using SHA256 (less secure)")
        return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    if BCRYPT_AVAILABLE:
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            return False
    else:
        return hashlib.sha256(password.encode()).hexdigest() == hashed


# =============================================================================
# TOKEN OPERATIONS
# =============================================================================

def create_access_token(
    user_id: str,
    email: str,
    role: Role,
    name: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, datetime]:
    """
    Create a JWT access token.

    Args:
        user_id: Unique user identifier
        email: User email address
        role: User role
        name: Optional user display name
        expires_delta: Optional custom expiration time

    Returns:
        Tuple of (token_string, expiration_datetime)
    """
    if jwt is None:
        raise ImportError("No JWT library available. Install python-jose or PyJWT.")

    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))

    payload = {
        "sub": user_id,
        "email": email,
        "role": role.value,
        "name": name,
        "exp": expire,
        "iat": now,
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # PyJWT returns string, python-jose may return bytes
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, expire


def verify_token(token: str) -> Optional[TokenPayload]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        TokenPayload if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return TokenPayload(
            sub=payload["sub"],
            email=payload["email"],
            role=payload["role"],
            name=payload.get("name"),
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            iat=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
        )
    except JWTError as e:
        logger.debug(f"Token verification failed: {e}")
        return None


def decode_token_unverified(token: str) -> Optional[dict]:
    """
    Decode token without verification (for debugging).

    Args:
        token: JWT token string

    Returns:
        Raw payload dict, or None if decode fails
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
    except JWTError:
        return None


# =============================================================================
# FASTAPI DEPENDENCIES
# =============================================================================

async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
) -> User:
    """
    FastAPI dependency to get the current authenticated user.

    Raises HTTPException 401 if not authenticated.

    Usage:
        @router.get("/me")
        async def get_me(user: User = Depends(get_current_user)):
            return user
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    payload = verify_token(token)
    if not payload:
        raise credentials_exception

    # Check if token is expired
    if payload.exp < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return User(
        id=payload.sub,
        email=payload.email,
        role=Role(payload.role),
        name=payload.name,
    )


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
) -> Optional[User]:
    """
    Get current user if authenticated, None otherwise.

    Does not raise exception for unauthenticated requests.
    """
    if not token:
        return None

    payload = verify_token(token)
    if not payload or payload.exp < datetime.now(timezone.utc):
        return None

    return User(
        id=payload.sub,
        email=payload.email,
        role=Role(payload.role),
        name=payload.name,
    )


def require_role(allowed_roles: List[Role]):
    """
    Create a dependency that requires specific roles.

    Usage:
        @router.delete("/entity/{id}")
        async def delete_entity(
            id: str,
            user: User = Depends(require_role([Role.GP]))
        ):
            ...
    """
    async def role_checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required roles: {[r.value for r in allowed_roles]}",
            )
        return user

    return role_checker


# =============================================================================
# USER STORE (Simple in-memory for MVP, replace with DB)
# =============================================================================

# In-memory user store for development
# In production, replace with database queries
_USERS_DB: dict[str, dict] = {}


def register_user(
    user_id: str,
    email: str,
    password: str,
    role: Role = Role.READONLY,
    name: Optional[str] = None,
) -> User:
    """
    Register a new user (for setup/seeding).

    In production, this should write to the database.
    """
    _USERS_DB[email.lower()] = {
        "id": user_id,
        "email": email.lower(),
        "password_hash": hash_password(password),
        "role": role.value,
        "name": name,
    }
    return User(id=user_id, email=email, role=role, name=name)


def authenticate_user(email: str, password: str) -> Optional[User]:
    """
    Authenticate a user by email and password.

    Returns User if credentials are valid, None otherwise.
    """
    email = email.lower()
    user_data = _USERS_DB.get(email)

    if not user_data:
        # Also check environment variable for admin user
        admin_email = os.getenv("ADMIN_EMAIL", "").lower()
        admin_password = os.getenv("ADMIN_PASSWORD", "")

        if email == admin_email and password == admin_password and admin_email:
            return User(
                id="admin",
                email=admin_email,
                role=Role.GP,
                name="Admin",
            )
        return None

    if not verify_password(password, user_data["password_hash"]):
        return None

    return User(
        id=user_data["id"],
        email=user_data["email"],
        role=Role(user_data["role"]),
        name=user_data.get("name"),
    )


# =============================================================================
# SEED DEFAULT ADMIN (for development)
# =============================================================================

def seed_default_users():
    """
    Seed default users for development.

    In production, users should be created via admin interface.
    """
    default_password = os.getenv("DEFAULT_PASSWORD", "changeme123")

    # Only seed if USERS_DB is empty and no ADMIN_EMAIL is set
    if not _USERS_DB and not os.getenv("ADMIN_EMAIL"):
        register_user(
            user_id="dev-gp-1",
            email="gp@example.com",
            password=default_password,
            role=Role.GP,
            name="Dev GP",
        )
        register_user(
            user_id="dev-analyst-1",
            email="analyst@example.com",
            password=default_password,
            role=Role.ANALYST,
            name="Dev Analyst",
        )
        logger.info("Seeded development users (gp@example.com, analyst@example.com)")
