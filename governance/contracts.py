"""Governance event contracts — Pydantic models for feature lifecycle events.

These contracts enforce required metadata for governance actions:
- feature_promote: advancing a feature from shadow to active
- regret_check: periodic validation after promotion
- feature_demote: rollback or downgrade

All governance events flow through governance/writer.py which validates
against these contracts before writing to audit_events.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

from governance.state_policies import ALL_GOVERNANCE_STATES


class FeaturePromoteMetadata(BaseModel):
    """Metadata contract for feature_promote audit events.

    State validation is syntax-only (membership in ALL_GOVERNANCE_STATES).
    Semantic validation (directionality, skip-level) is in state_policies.py,
    enforced by writer.py and the API router.
    """

    action_type: Literal["feature_promote"] = "feature_promote"
    feature_name: str = Field(..., min_length=1)
    from_state: str = Field(..., description="Previous state")
    to_state: str = Field(..., description="New state")
    regret_due_at: str = Field(
        ..., description="ISO 8601 date when regret check is due"
    )
    config_snapshot_hash: str = Field(..., min_length=1)
    config_snapshot_flags: Optional[Dict[str, Any]] = None
    effective_at: Optional[str] = Field(
        default=None,
        description=(
            "Actual promotion timestamp when recording a retroactive "
            "repair for an earlier env-only activation"
        ),
    )
    repair_source: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Artifact or note that supports a retroactive repair",
    )

    @field_validator("from_state", "to_state")
    @classmethod
    def validate_states(cls, v: str) -> str:
        if v not in ALL_GOVERNANCE_STATES:
            raise ValueError(
                f"State must be one of {sorted(ALL_GOVERNANCE_STATES)}, got '{v}'"
            )
        return v


class RegretCheckMetadata(BaseModel):
    """Metadata contract for regret_check audit events."""

    action_type: Literal["regret_check"] = "regret_check"
    verdict: Literal["pass", "fail"]
    canary_verdict: Literal["pass", "fail", "no_data"]
    drift_status: Literal["in_control", "warning", "critical", "no_data"]
    window_days: int = Field(default=14, ge=1)


class FeatureDemoteMetadata(BaseModel):
    """Metadata contract for feature_demote audit events.

    State validation is syntax-only. Semantic validation in state_policies.py.
    """

    action_type: Literal["feature_demote"] = "feature_demote"
    from_state: str = Field(..., description="Previous state")
    to_state: str = Field(..., description="New state")
    rollback_ticket: Optional[str] = None
    incident_id: Optional[str] = None

    @field_validator("from_state", "to_state")
    @classmethod
    def validate_states(cls, v: str) -> str:
        if v not in ALL_GOVERNANCE_STATES:
            raise ValueError(
                f"State must be one of {sorted(ALL_GOVERNANCE_STATES)}, got '{v}'"
            )
        return v


class FeatureEvalMetadata(BaseModel):
    """Metadata contract for a feature evaluation result."""

    action_type: Literal["feature_eval_completed"] = "feature_eval_completed"
    recommendation: str  # promote/extend_shadow/kill/insufficient_data
    decision_reason: str
    n_entities_evaluated: int
    n_time_slices: int

GovernanceEventMetadata = Union[
    FeaturePromoteMetadata,
    RegretCheckMetadata,
    FeatureDemoteMetadata,
    FeatureEvalMetadata,
]

# Map action_type string -> contract class for validation in writer
_CONTRACT_MAP = {
    "feature_promote": FeaturePromoteMetadata,
    "regret_check": RegretCheckMetadata,
    "feature_demote": FeatureDemoteMetadata,
    "feature_eval_completed": FeatureEvalMetadata,
}

GOVERNANCE_ACTION_TYPES = frozenset(_CONTRACT_MAP.keys())
