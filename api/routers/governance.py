"""Governance Router — Feature lifecycle event recording.

Endpoints:
- POST /governance/events — Record a governance event

All governance events ignore incoming actor_id and use the authenticated
OperatorContext instead.
"""

from __future__ import annotations

import logging
from typing import Annotated, Union

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from api.auth.rbac import OperatorContext, Permission, require_permission
from api.contracts import BaseResponse, error_response
from governance.contracts import (
    FeatureDemoteMetadata,
    FeaturePromoteMetadata,
    RegretCheckMetadata,
)
from governance.writer import (
    record_feature_demote,
    record_feature_promote,
    record_regret_check,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/governance", tags=["governance"])


class GovernanceEventRequest(BaseModel):
    """Request body for recording a governance event."""

    reason: str = Field(..., min_length=3, max_length=2000)
    feature_name: str = Field(..., min_length=1)
    metadata: Annotated[
        Union[FeaturePromoteMetadata, RegretCheckMetadata, FeatureDemoteMetadata],
        Field(discriminator="action_type"),
    ]

    @model_validator(mode="after")
    def enforce_feature_name_consistency(self) -> "GovernanceEventRequest":
        """Enforce that feature_name matches metadata.feature_name for promote."""
        if isinstance(self.metadata, FeaturePromoteMetadata):
            if self.metadata.feature_name != self.feature_name:
                raise ValueError(
                    f"feature_name mismatch: body says '{self.feature_name}' "
                    f"but metadata says '{self.metadata.feature_name}'"
                )
        return self


class GovernanceEventResponse(BaseModel):
    event_id: int
    action_type: str
    feature_name: str


@router.post("/events", response_model=BaseResponse[GovernanceEventResponse])
async def record_governance_event(
    body: GovernanceEventRequest,
    request: Request,
    operator: OperatorContext = Depends(
        require_permission(Permission.FEATURE_GOVERNANCE)
    ),
):
    """Record a governance event (promote/regret-check/demote).

    Actor identity is taken from the JWT, not the request body.
    """
    store = request.app.state.store

    if isinstance(body.metadata, FeaturePromoteMetadata):
        event_id = await record_feature_promote(
            store, operator,
            feature_name=body.feature_name,
            from_state=body.metadata.from_state,
            to_state=body.metadata.to_state,
            regret_due_at=body.metadata.regret_due_at,
            reason=body.reason,
            config_snapshot_hash=body.metadata.config_snapshot_hash,
            config_snapshot_flags=body.metadata.config_snapshot_flags,
        )
    elif isinstance(body.metadata, RegretCheckMetadata):
        event_id = await record_regret_check(
            store, operator,
            feature_name=body.feature_name,
            verdict=body.metadata.verdict,
            canary_verdict=body.metadata.canary_verdict,
            drift_status=body.metadata.drift_status,
            reason=body.reason,
            window_days=body.metadata.window_days,
        )
    elif isinstance(body.metadata, FeatureDemoteMetadata):
        event_id = await record_feature_demote(
            store, operator,
            feature_name=body.feature_name,
            from_state=body.metadata.from_state,
            to_state=body.metadata.to_state,
            reason=body.reason,
            rollback_ticket=body.metadata.rollback_ticket,
            incident_id=body.metadata.incident_id,
        )
    else:
        raise error_response(
            422, "validation_error", "UNKNOWN_ACTION_TYPE",
            "Unknown governance action type",
        )

    logger.info(
        "Governance event %d: %s %s by %s",
        event_id, body.metadata.action_type, body.feature_name,
        operator.actor_label,
    )

    return BaseResponse(data=GovernanceEventResponse(
        event_id=event_id,
        action_type=body.metadata.action_type,
        feature_name=body.feature_name,
    ))
