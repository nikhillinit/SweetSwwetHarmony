"""
Authentication Router

Endpoints for user authentication:
- POST /login - Authenticate and get JWT token
- POST /logout - Invalidate session (if using sessions)
- GET /me - Get current user info
- POST /refresh - Refresh access token (future)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from api.auth.jwt_auth import (
    authenticate_user,
    create_access_token,
    get_current_user,
    get_current_user_optional,
    User,
    Role,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class LoginRequest(BaseModel):
    """Login request with email/password."""
    email: str
    password: str


class LoginResponse(BaseModel):
    """Successful login response."""
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: User


class UserResponse(BaseModel):
    """Current user information."""
    id: str
    email: str
    role: str
    name: Optional[str]


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    credentials: LoginRequest,
):
    """
    Authenticate user and return JWT token.

    Rate limited to 5 attempts per minute per IP.
    """
    user = authenticate_user(credentials.email, credentials.password)

    if not user:
        # Log failed attempt (for security monitoring)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token
    token, expires_at = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        name=user.name,
    )

    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=user,
    )


@router.post("/login/form", response_model=LoginResponse)
async def login_form(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    OAuth2 compatible login endpoint for Swagger UI.

    Uses username field for email (OAuth2 spec).
    """
    user = authenticate_user(form_data.username, form_data.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_at = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        name=user.name,
    )

    return LoginResponse(
        access_token=token,
        expires_at=expires_at,
        user=user,
    )


@router.post("/logout")
async def logout(
    user: User = Depends(get_current_user),
):
    """
    Logout current user.

    With JWT, logout is handled client-side by discarding the token.
    This endpoint is provided for completeness and could be extended
    to maintain a token blacklist if needed.
    """
    # In a stateless JWT setup, there's nothing to invalidate server-side.
    # For enhanced security, you could:
    # 1. Maintain a token blacklist in Redis
    # 2. Use short-lived tokens + refresh tokens
    # 3. Store sessions in database (migration 16 has user_sessions table)

    return {
        "success": True,
        "message": "Logged out successfully. Please discard your access token.",
    }


@router.get("/me", response_model=UserResponse)
async def get_me(
    user: User = Depends(get_current_user),
):
    """
    Get current authenticated user.

    Useful for checking if token is still valid and getting user info.
    """
    return UserResponse(
        id=user.id,
        email=user.email,
        role=user.role.value,
        name=user.name,
    )


@router.get("/check")
async def check_auth(
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Check if user is authenticated without requiring auth.

    Returns authentication status and user info if authenticated.
    Useful for frontend to check session state.
    """
    if user:
        return {
            "authenticated": True,
            "user": UserResponse(
                id=user.id,
                email=user.email,
                role=user.role.value,
                name=user.name,
            ),
        }
    else:
        return {
            "authenticated": False,
            "user": None,
        }


@router.get("/roles")
async def get_roles():
    """
    Get available roles and their descriptions.

    Public endpoint for UI display.
    """
    return {
        "roles": [
            {
                "id": Role.GP.value,
                "name": "General Partner",
                "description": "Full access to all features including admin functions",
            },
            {
                "id": Role.ANALYST.value,
                "name": "Analyst",
                "description": "Read and write access, no admin functions",
            },
            {
                "id": Role.READONLY.value,
                "name": "Read Only",
                "description": "View-only access to pipeline and entities",
            },
        ],
    }
