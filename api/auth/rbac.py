"""
Role-Based Access Control (RBAC) for the Discovery Engine API.

Maps the existing JWT Role enum (GP, ANALYST, READONLY) to logical
permission levels used by Wave 0+ features:

    viewer   → READONLY  (read/search/comment)
    operator → ANALYST   (approve/reject/defer/promote)
    admin    → GP        (merge/publish/bulk actions)

Provides:
- Permission enum and role→permission mapping
- require_permission() dependency for endpoint-level enforcement
- OperatorContext: signed operator identity for all state-changing actions
- audit-ready operator identity extraction from JWT
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.auth.jwt_auth import Role, User, get_current_user

logger = logging.getLogger(__name__)


# =============================================================================
# PERMISSIONS
# =============================================================================

class Permission(str, Enum):
    """Logical permissions for feature gates."""

    # Read-only
    VIEW = "view"
    SEARCH = "search"
    EXPORT = "export"

    # Operator
    TRIAGE_APPROVE = "triage_approve"
    TRIAGE_REJECT = "triage_reject"
    TRIAGE_DEFER = "triage_defer"
    HUNTER_RUN = "hunter_run"
    HUNTER_PROMOTE = "hunter_promote"
    CANARY_RUN = "canary_run"

    # Admin
    BATCH_COMMIT = "batch_commit"
    ENTITY_MERGE = "entity_merge"
    BULK_TRIAGE = "bulk_triage"
    PUBLISH = "publish"
    MANAGE_USERS = "manage_users"


# Role → granted permissions
_ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.READONLY: {
        Permission.VIEW,
        Permission.SEARCH,
        Permission.EXPORT,
    },
    Role.ANALYST: {
        Permission.VIEW,
        Permission.SEARCH,
        Permission.EXPORT,
        Permission.TRIAGE_APPROVE,
        Permission.TRIAGE_REJECT,
        Permission.TRIAGE_DEFER,
        Permission.HUNTER_RUN,
        Permission.HUNTER_PROMOTE,
        Permission.CANARY_RUN,
    },
    Role.GP: set(Permission),  # GP gets everything
}


def has_permission(role: Role, permission: Permission) -> bool:
    """Check if a role grants a specific permission."""
    return permission in _ROLE_PERMISSIONS.get(role, set())


def get_permissions(role: Role) -> set[Permission]:
    """Get all permissions granted to a role."""
    return _ROLE_PERMISSIONS.get(role, set()).copy()


# =============================================================================
# FASTAPI DEPENDENCIES
# =============================================================================

def require_permission(permission: Permission):
    """FastAPI dependency that enforces a specific permission.

    Usage:
        @router.post("/triage/{id}/approve")
        async def approve(
            id: str,
            operator: OperatorContext = Depends(
                require_permission(Permission.TRIAGE_APPROVE)
            ),
        ):
            ...
    """

    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> OperatorContext:
        if not has_permission(user.role, permission):
            logger.warning(
                "Permission denied: user=%s role=%s permission=%s",
                user.email,
                user.role.value,
                permission.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "forbidden",
                    "code": "INSUFFICIENT_PERMISSION",
                    "message": f"Permission '{permission.value}' required. "
                    f"Your role '{user.role.value}' does not grant it.",
                    "required_permission": permission.value,
                    "user_role": user.role.value,
                },
            )
        return OperatorContext.from_request(user, request)

    return checker


def require_any_permission(*permissions: Permission):
    """FastAPI dependency requiring at least one of the listed permissions."""

    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> OperatorContext:
        for perm in permissions:
            if has_permission(user.role, perm):
                return OperatorContext.from_request(user, request)

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "code": "INSUFFICIENT_PERMISSION",
                "message": "None of the required permissions granted.",
                "required_permissions": [p.value for p in permissions],
                "user_role": user.role.value,
            },
        )

    return checker


# =============================================================================
# OPERATOR CONTEXT
# =============================================================================

class OperatorContext(BaseModel):
    """Signed operator identity for all state-changing actions.

    Carried through service calls and persisted in audit events.
    """

    user_id: str = Field(..., description="Authenticated user ID")
    email: str = Field(..., description="Authenticated user email")
    role: Role = Field(..., description="User role at time of action")
    name: Optional[str] = Field(default=None, description="Display name")
    request_id: Optional[str] = Field(
        default=None, description="Correlation ID from X-Request-ID"
    )

    @classmethod
    def from_request(cls, user: User, request: Request) -> "OperatorContext":
        """Build operator context from authenticated user + request."""
        return cls(
            user_id=user.id,
            email=user.email,
            role=user.role,
            name=user.name,
            request_id=getattr(request.state, "request_id", None),
        )

    @classmethod
    def system(cls, subsystem: str = "pipeline") -> "OperatorContext":
        """Build operator context for automated/system actions."""
        return cls(
            user_id=f"system:{subsystem}",
            email=f"{subsystem}@system.internal",
            role=Role.GP,
            name=f"System ({subsystem})",
        )

    @property
    def actor_label(self) -> str:
        """Short label for audit log 'actor' column."""
        return self.email or self.user_id
