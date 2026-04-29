"""Phase 2 Day 5 — Router-config status contract (Pydantic v2).

This module is the **single source of truth** for the data contract emitted by
``scripts/write_router_config_status.py`` to ``state/router_config_status.json``.

Three nearby names are deliberately distinct; the difference is load-bearing:

* ``state/conformal_calibration.json`` — Day 4 *artifact* (input to Day 5).
* ``state/router_config_status.json`` — Day 5 *status* (output of Day 5).
  Readiness scope is ``human_review_only``; it never routes traffic.
* ``candidate_router_threshold_config`` — embedded *candidate* inside the
  status when not blocked. Marked ``activation="manual_review_required"``
  and ``production_routing_enabled=False`` so a copy of the dict cannot
  silently activate routing somewhere else. There is no standalone
  ``router_threshold_config.json`` file in v1.

Reserved promotion drift codes (``promotion_prompt_version_drift``,
``promotion_scoring_path_drift``, ``promotion_runtime_threshold_incompatible``)
are owned by the **future router-application gate** and are deliberately
absent from ``BlockingReasonV1`` / ``WarningReasonV1``. Pydantic Literal enums
make accidental emission a ``ValidationError`` at construction time.

Score-binding expectations are pinned to Day 4's ``scripts.recalibrate_conformal``
module-level constants via a dedicated test (see ``tests/scripts/
test_write_router_config_status.py``); if Day 4 reworks the ``SCORE_VERSION_POLICY``
wording, the pin test fires and a human decides whether to update Day 5 or
treat the change as drift.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Reason-code Literal enums (v1 — accidental emission of reserved promotion
# codes raises ValidationError at construction time)
# ---------------------------------------------------------------------------

BlockingReasonV1 = Literal[
    "artifact_missing",
    "invalid_artifact_schema",
    "wrong_score_binding_semantics",
    "unsupported_calibration_mode",
    "unsupported_stratified_thresholds",
    "no_cutoff_available",
    "precision_below_target",
    "artifact_too_stale",
    "insufficient_label_support",
    "label_support_unavailable",
]

WarningReasonV1 = Literal[
    "precision_band_below_target",
    "calibration_target_mismatch",
    "score_binding_metadata_drift",
]

# These three codes are RESERVED for the future router-application gate.
# They MUST NOT appear in either Literal alias above. The deny-list test in
# the test suite asserts the intersection is empty.
RESERVED_PROMOTION_CODES = frozenset(
    {
        "promotion_prompt_version_drift",
        "promotion_scoring_path_drift",
        "promotion_runtime_threshold_incompatible",
    }
)


# ---------------------------------------------------------------------------
# Score-binding expectations — values pinned to Day 4 source-of-truth via
# tests/scripts/test_write_router_config_status.py::TestScoreBindingPin.
#
# If Day 4 (scripts/recalibrate_conformal.py) ever rewords its constants, the
# pin test fires; the human decides whether to update these or treat as drift.
# ---------------------------------------------------------------------------

EXPECTED_SCORE_TABLE = "signals"
EXPECTED_SCORE_COLUMN = "confidence"
EXPECTED_SEMANTIC_NAME = "signal_stored_confidence"
EXPECTED_SCORE_DIRECTION = "higher_is_more_confident"
EXPECTED_DECISION_RULE = "accept_if_score_gte_threshold"
EXPECTED_SCORE_PRODUCER = "signal_generation_pipeline"
EXPECTED_SCORE_VERSION = "mixed_or_unknown"
EXPECTED_SCORE_VERSION_POLICY = (
    "signals.confidence in the calibration set may span multiple scoring-"
    "logic versions; this artifact treats the column as a single "
    "distribution. Day 5+ consumers MUST refuse to apply this cut-off if "
    "the active scoring logic has changed since calibration (compare via "
    "the active_thesis_prompt_version + git.commit fields)."
)

# Semantic vs metadata classification. Mismatch on a SEMANTIC field blocks
# (the score source identity is wrong); mismatch on a METADATA field warns
# (the score was produced differently but still over the same column).
SEMANTIC_SCORE_BINDING_FIELDS = (
    "table",
    "column",
    "semantic_name",
    "score_direction",
    "decision_rule",
)
METADATA_SCORE_BINDING_FIELDS = (
    "producer",
    "version",
    "version_policy",
)


# ---------------------------------------------------------------------------
# Day 5 policy constants
# ---------------------------------------------------------------------------

POLICY_VERSION = "router_config_status_policy.v1"
DEFAULT_TARGET_PRECISION = 0.90
DEFAULT_MAX_AGE_DAYS = 7
SUPPORT_FLOOR_TP_PLUS_FP = 30
SUPPORT_FLOOR_TP = 10
SUPPORT_SCOPE = "input.calibration_label_breakdown"
DIGEST_FLOAT_PRECISION_DECIMALS = 6


# ---------------------------------------------------------------------------
# Stratified-threshold preflight keys — checked on the RAW parsed dict before
# Pydantic validation, because Day4CalibrationArtifactV1 uses extra='ignore'
# (so unknown keys would be silently dropped otherwise).
# ---------------------------------------------------------------------------

STRATIFIED_THRESHOLD_KEYS_TOP_LEVEL = (
    "per_stratum_cutoffs",
    "stratum_cutoffs",
    "cutoffs_by_stratum",
)
STRATIFIED_THRESHOLD_KEYS_NESTED = (
    ("chosen_cutoff", "per_stratum_cutoffs"),
    ("chosen_cutoff", "cutoffs_by_stratum"),
    ("bootstrap", "per_stratum_cutoffs"),
    ("cv", "per_stratum_cutoffs"),
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ScoreBindingV1(BaseModel):
    """Mirror of Day 4 artifact's score_binding (all 8 fields)."""

    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    semantic_name: str
    score_direction: str
    decision_rule: str
    producer: str
    version: str
    version_policy: str


class CalibrationSummaryV1(BaseModel):
    """Flat summary extracted from Day 4's nested artifact for human review."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["bootstrap", "cv"]
    target_precision: float
    chosen_cutoff_value: Optional[float]
    bootstrap_p50: Optional[float]
    bootstrap_p5: Optional[float]
    label_breakdown_tp: Optional[int]
    label_breakdown_fp: Optional[int]
    label_breakdown_unsure: Optional[int]
    label_breakdown_missing: Optional[int]


class ArtifactReferenceV1(BaseModel):
    """Forensic identity of the Day 4 artifact this status was computed from."""

    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: Optional[str] = None
    generated_at: Optional[str] = None
    artifact_type: Optional[str] = None


class PolicySnapshotV1(BaseModel):
    """Frozen copy of the Day 5 policy values used for THIS evaluation."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["router_config_status_policy.v1"]
    target_precision: float
    max_age_days: int
    support_floor_tp_plus_fp: int
    support_floor_tp: int
    support_scope: Literal["input.calibration_label_breakdown"]


class CandidateRouterThresholdConfigV1(BaseModel):
    """Embedded only when status is warn or ready.

    Self-defensive: if a copy of this dict ends up somewhere it shouldn't,
    ``activation`` and ``production_routing_enabled`` make it inert. There is
    no standalone ``state/router_threshold_config.json`` file in v1.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["candidate_router_threshold_config.v1"]
    activation: Literal["manual_review_required"]
    production_routing_enabled: Literal[False]
    threshold_value: float
    score_binding: ScoreBindingV1
    calibration_semantic_digest: str
    calibration_summary: CalibrationSummaryV1
    artifact_reference: ArtifactReferenceV1


class RouterConfigStatusV1(BaseModel):
    """Top-level Day 5 status output.

    Always parseable: even on ``artifact_missing`` or ``invalid_artifact_schema``,
    every required scope-discipline field is populated. The candidate is None
    when status is ``blocked``; the top-level digest is None when the artifact
    couldn't be parsed far enough to read ``score_binding``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["router_config_status.v1"]
    status: Literal["blocked", "warn", "ready"]
    blocking_reasons: list[BlockingReasonV1] = Field(default_factory=list)
    warning_reasons: list[WarningReasonV1] = Field(default_factory=list)
    # Keyed by reason code; per-reason payload shapes are documented in plan §6.4.
    reason_payloads: dict[str, dict[str, Any]] = Field(default_factory=dict)

    artifact_reference: ArtifactReferenceV1
    calibration_semantic_digest: Optional[str] = None
    candidate_router_threshold_config: Optional[CandidateRouterThresholdConfigV1] = None

    # Required scope-discipline metadata (Literal-frozen).
    readiness_scope: Literal["human_review_only"]
    runtime_effective: Literal[False]
    may_route: Literal[False]
    future_router_validation_required: Literal[True]

    policy: PolicySnapshotV1
    evaluated_at: str  # ISO 8601 UTC


class Day4CalibrationArtifactV1(BaseModel):
    """Input model — Day 5 reads only the fields below; other artifact fields
    are ignored via ``extra='ignore'`` so that NEW Day 4 additions don't break
    Day 5.

    The ``unsupported_stratified_thresholds`` preflight runs on the RAW dict
    BEFORE this model validates, so ``extra='ignore'`` does NOT mask new
    stratification keys.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int
    artifact_type: Literal["threshold_selection"]
    generated_at: str
    mode: Literal["bootstrap", "cv"]
    target_precision: float
    score_binding: ScoreBindingV1
    input: dict[str, Any]
    scoring_provenance: dict[str, Any]  # opaque to Day 5
    bootstrap: Optional[dict[str, Any]] = None
    cv: Optional[dict[str, Any]] = None
    chosen_cutoff: dict[str, Any]
