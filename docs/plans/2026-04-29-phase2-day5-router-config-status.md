# Phase 2 Day 5 — Guarded Router Config Status Writer (Plan v2)

**Branch:** `phase2/instrumentation`
**Builds on:** Day 4 (PR #142, commits `784200e` + `9892201`); ADR-041
**Wiki:** `decisions/041-phase2-day4-conformal-recalibration-shipped`, `concepts/conformal-calibration-threshold-recipe`, `concepts/phase2-score-label-join`
**Status:** Implementation-ready. Eng review (3 rounds) + outside review integrated.
**Plan version:** v2 — amendments P0×5, P1×7, P2×5 from outside-review round 2 integrated.

---

## 1. Context

Day 4 ships `state/conformal_calibration.json` as an `artifact_type: threshold_selection` artifact with an 8-field `score_binding` over `signals.confidence`. ADR-041 names two deferred consumers; Day 5 implements consumer #1 — but reframed as a **shadow-only artifact-status writer**, not a router-config writer.

Day 5 evaluates the Day 4 artifact for **human review readiness only**. It is not a promotion gate, not a live router, and not a runtime probe. It does not read the DB, does not import live pipeline constants, and does not check live env vars. The single runtime artifact is `state/router_config_status.json`. A non-blocked status embeds a `candidate_router_threshold_config` object; no standalone production config is ever written.

Drift detection (live prompt version, scoring path, runtime threshold incompatibility) is the responsibility of the **future router-application gate**, not this writer. Reason codes for that gate are reserved here but never emitted in v1, and Pydantic Literal enums prevent accidental emission.

Day 4's live state has max attainable precision `0.154` against target `0.90` — Day 5 must emit `blocked` on the current artifact, which is the correct demo of the policy.

---

## 2. Scope discipline (do-not-do list)

- ❌ No live DB reads. No `--db` flag.
- ❌ No live env reads. No `LLM_THESIS_MODE` probe.
- ❌ No imports from `workflows/pipeline.py` (including `HIGH_CONFIDENCE_THRESHOLD = 0.7`).
- ❌ No `prompt_version_drift` / `high_confidence_threshold_drift` / `llm_thesis_mode_drift` blocking reasons. Reserved with `promotion_` prefix for the future router-application gate; absent from Day 5 Literal enums.
- ❌ No mutation to `VerificationGate`, governance state, Notion routing, or pipeline behavior.
- ❌ No standalone `state/router_threshold_config.json`.
- ❌ No fake audit fields (`reviewed_by`, `reviewed_at`, `review_decision`).
- ❌ No ADR (defer until live routing or promotion design).
- ❌ No governance registration / `record_feature_promote()`.
- ❌ No `schemas/` directory; no `jsonschema` dep. Pydantic is the single contract source.

---

## 3. Files

### New

| File | Purpose | Approx LOC |
|---|---|---|
| `verification/router_threshold_config.py` | Pydantic v2 contract models, Literal reason-code enums, frozen score-binding expectations, policy snapshot constants | ~220 |
| `verification/router_threshold_policy.py` | Non-production stub (`raw_signal_passes_threshold`) | ~30 |
| `scripts/write_router_config_status.py` | Status writer CLI; raw-dict preflight + Pydantic validation + reason accumulation + atomic write | ~320 |
| `tests/scripts/test_write_router_config_status.py` | Per-reason fixtures, payload-shape tests, hard-stop test, summary-extractor test, atomic-write, import-guards, promotion-code enum guard | ~750 |

### Modified

| File | Change |
|---|---|
| `.gitignore` | Add `state/router_config_status.json` and `state/router_config_status.json.tmp` |
| `.planning/PROJECT.md` | Vocabulary note (no line numbers — use file + symbol) + human-review handoff note |

### Not modified (regression-protected)

`workflows/pipeline.py`, `verification/verification_gate_v2.py`, `scripts/recalibrate_conformal.py`, `tests/scripts/test_recalibrate_conformal.py`.

---

## 4. Pydantic models (`verification/router_threshold_config.py`)

Module docstring distinguishes the three "config / status / candidate" names and lists the `promotion_*` codes as explicitly reserved for future work (so a future grep for the names finds the source-of-truth doc, not silent drift).

### 4.1 Reason-code Literal enums

```python
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
```

The Literal aliases prevent accidental emission of reserved promotion codes (`promotion_prompt_version_drift`, `promotion_scoring_path_drift`, `promotion_runtime_threshold_incompatible`) by making them type errors at construction time. No grep guard required.

### 4.2 Score-binding expectations (all 8 fields explicit)

```python
EXPECTED_SCORE_TABLE          = "signals"
EXPECTED_SCORE_COLUMN         = "confidence"
EXPECTED_SEMANTIC_NAME        = "signal_stored_confidence"
EXPECTED_SCORE_DIRECTION      = "higher_is_more_confident"
EXPECTED_DECISION_RULE        = "accept_if_score_gte_threshold"
EXPECTED_SCORE_PRODUCER       = "signal_generation_pipeline"  # matches Day 4 SCORE_PRODUCER constant
EXPECTED_SCORE_VERSION        = "mixed_or_unknown"            # matches Day 4 SCORE_VERSION constant
EXPECTED_SCORE_VERSION_POLICY = "consumers_must_refuse_on_active_scoring_path_change"  # matches Day 4 SCORE_VERSION_POLICY constant
```

> Implementation note: the 3 producer/version/version_policy literals MUST be sourced verbatim from `scripts/recalibrate_conformal.py`'s `SCORE_PRODUCER` / `SCORE_VERSION` / `SCORE_VERSION_POLICY` constants. A test pins them to the Day 4 source-of-truth constants (see §9 "score-binding-pin test"). If Day 4 ever rewords its `SCORE_VERSION_POLICY` string, the pin test fires; the human decides whether to update Day 5's constant or treat as drift.

**Split policy** — semantic vs metadata:

| Field | Class | Reason code on mismatch |
|---|---|---|
| `table` | semantic | `wrong_score_binding_semantics` (block) |
| `column` | semantic | `wrong_score_binding_semantics` (block) |
| `semantic_name` | semantic | `wrong_score_binding_semantics` (block) |
| `score_direction` | semantic | `wrong_score_binding_semantics` (block) |
| `decision_rule` | semantic | `wrong_score_binding_semantics` (block) |
| `producer` | metadata | `score_binding_metadata_drift` (warn) |
| `version` | metadata | `score_binding_metadata_drift` (warn) |
| `version_policy` | metadata | `score_binding_metadata_drift` (warn) |

Rationale: `table` and `column` define the score source — they cannot be metadata. Producer/version/version_policy are descriptive of how the score was produced; a change there should surface for human review but not block.

### 4.3 Pydantic models

```python
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
    model_config = ConfigDict(extra="forbid")
    path: str                       # path passed to --artifact
    sha256: Optional[str] = None    # SHA256 of raw artifact bytes (None if file unreadable)
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
    """Embedded only when status is warn or ready. Self-defensive in case copied elsewhere."""
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
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["router_config_status.v1"]
    status: Literal["blocked", "warn", "ready"]
    blocking_reasons: list[BlockingReasonV1] = Field(default_factory=list)
    warning_reasons: list[WarningReasonV1] = Field(default_factory=list)
    reason_payloads: dict[str, dict[str, Any]] = Field(default_factory=dict)  # keyed by reason code; shapes documented in §6.3
    # Top-level forensic identity (always present when artifact is parseable):
    artifact_reference: ArtifactReferenceV1
    calibration_semantic_digest: Optional[str] = None  # present when artifact parses + score_binding read; None on artifact_missing / invalid_artifact_schema
    candidate_router_threshold_config: Optional[CandidateRouterThresholdConfigV1] = None
    # Required scope-discipline metadata:
    readiness_scope: Literal["human_review_only"]
    runtime_effective: Literal[False]
    may_route: Literal[False]
    future_router_validation_required: Literal[True]
    policy: PolicySnapshotV1
    evaluated_at: str    # ISO 8601 UTC

class Day4CalibrationArtifactV1(BaseModel):
    """Input model. Day 5 reads only the fields below; other artifact fields are ignored
    via extra='ignore' SO THAT new Day-4 additions don't break Day 5. The unsupported-
    stratified-thresholds preflight runs on the RAW dict before Pydantic, so this 'ignore'
    does NOT mask new stratification keys."""
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
```

### 4.4 Constants

```python
POLICY_VERSION                  = "router_config_status_policy.v1"
DEFAULT_TARGET_PRECISION        = 0.90
DEFAULT_MAX_AGE_DAYS            = 7
SUPPORT_FLOOR_TP_PLUS_FP        = 30
SUPPORT_FLOOR_TP                = 10
SUPPORT_SCOPE                   = "input.calibration_label_breakdown"
DIGEST_FLOAT_PRECISION_DECIMALS = 6

# Forbidden top-level / nested keys for unsupported_stratified_thresholds preflight.
# Checked on the raw parsed dict BEFORE Pydantic validation (Day4CalibrationArtifactV1
# uses extra='ignore', which would otherwise silently drop these).
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
```

---

## 5. CLI (`scripts/write_router_config_status.py`)

```text
write-router-config-status [--artifact PATH] [--out PATH] [--max-age-days N]
                           [--target-precision FLOAT] [--now ISO8601_UTC]

  --artifact          default state/conformal_calibration.json
  --out               default state/router_config_status.json
  --max-age-days      default 7  (integer days)
  --target-precision  default 0.90 (Day 5 policy, NOT the artifact's target)
  --now               default current UTC; ISO 8601 UTC string for deterministic tests
                      (used for both evaluated_at and freshness boundary computation)
```

Exit codes:
- `0` — status file written (any of `blocked` / `warn` / `ready`)
- `1` — writer/IO failure: unwritable output dir, IO error during write
- `2` — argparse failure (invalid CLI args; argparse default behavior, not customized)
- (no other exit codes — Day 5 never errors on data quality)

Atomic write: write to `<out>.tmp`, then `os.replace(<out>.tmp, <out>)`. Create parent directory if absent.

`--dry-run` is **not** added in v1. A temp `--out` path covers the no-write CI use case. If a future iteration needs it: same exit codes, prints JSON to stdout, no file write.

---

## 6. Reason codes

### 6.1 Blocking codes (v1)

| Code | When |
|---|---|
| `artifact_missing` | `--artifact` path does not exist (hard stop — no other reasons accumulated) |
| `invalid_artifact_schema` | JSON parse fails OR Pydantic ValidationError on `Day4CalibrationArtifactV1` OR digest payload contains NaN/Inf (hard stop — no other reasons accumulated) |
| `wrong_score_binding_semantics` | Any of the 5 semantic fields (`table`, `column`, `semantic_name`, `score_direction`, `decision_rule`) differs from expected |
| `unsupported_calibration_mode` | `mode == "cv"` (Day 4's CV path doesn't record `calibration_label_breakdown`) |
| `unsupported_stratified_thresholds` | Any of the keys listed in `STRATIFIED_THRESHOLD_KEYS_*` are present in the raw artifact (checked **before** Pydantic) |
| `no_cutoff_available` | `chosen_cutoff.value` is `None` or absent |
| `precision_below_target` | `bootstrap.precision_at_cutoff.p50 < --target-precision` |
| `artifact_too_stale` | `now - generated_at > --max-age-days` (boundary: equal is NOT stale) |
| `insufficient_label_support` | `TP + FP < 30` or `TP < 10` (from `input.calibration_label_breakdown`) |
| `label_support_unavailable` | `input.calibration_label_breakdown` empty/missing on a `mode=bootstrap` artifact |

### 6.2 Warning codes (v1)

| Code | When |
|---|---|
| `precision_band_below_target` | `p50 >= target` but `p5 < target` |
| `calibration_target_mismatch` | Artifact's `target_precision` differs from Day 5 policy `--target-precision` |
| `score_binding_metadata_drift` | Any of the 3 metadata fields (`producer`, `version`, `version_policy`) differs from expected (per-field expected/observed in payload) |

### 6.3 Reserved for the future router-application gate (NEVER emitted in v1)

| Code | Future trigger |
|---|---|
| `promotion_prompt_version_drift` | Live `thesis_classifications.prompt_version` differs from artifact provenance |
| `promotion_scoring_path_drift` | Live LLM mode / score column semantics differ from artifact provenance |
| `promotion_runtime_threshold_incompatible` | Live `HIGH_CONFIDENCE_THRESHOLD` differs from artifact `scoring_provenance.high_confidence_threshold_at_run_time` |

These are absent from `BlockingReasonV1` / `WarningReasonV1`; emitting one is a Pydantic ValidationError. A test asserts the deny-list intersection with the live enums is empty (see §9 "promotion-codes guard"). No grep needed.

### 6.4 Reason payload shapes

`reason_payloads[reason_code]` is `Optional[dict[str, Any]]`. Documented minimum shapes:

| Reason | Payload keys |
|---|---|
| `precision_below_target` | `observed_p50`, `target_precision` |
| `precision_band_below_target` | `observed_p50`, `observed_p5`, `target_precision` |
| `insufficient_label_support` | `observed_tp`, `observed_fp`, `floor_tp`, `floor_tp_plus_fp` |
| `artifact_too_stale` | `artifact_generated_at`, `evaluated_at`, `max_age_days` |
| `calibration_target_mismatch` | `artifact_target_precision`, `policy_target_precision` |
| `wrong_score_binding_semantics` | per-mismatched-field map: `{ "table": {"expected": "...", "observed": "..."}, ... }` |
| `score_binding_metadata_drift` | per-mismatched-field map (same shape) |
| `unsupported_stratified_thresholds` | `offending_keys: list[str]` (dotted paths, e.g. `["bootstrap.per_stratum_cutoffs"]`) |
| `unsupported_calibration_mode` | `observed_mode: str` |
| `no_cutoff_available` | `observed_value: Any` (typically `null`) |
| `label_support_unavailable` | `observed_breakdown: dict` (whatever was found, including `{}`) |
| `artifact_missing` | `path: str` |
| `invalid_artifact_schema` | `error_kind: Literal["json_parse" | "pydantic" | "non_finite_float"]`, `detail: str` (truncated to 500 chars) |

Tests assert each payload shape per fixture (see §9).

---

## 7. Reason-accumulator order (revised — hard stops on artifact integrity)

```text
1. --artifact path missing
   → blocked + artifact_missing
   → STOP (no other reasons)

2. JSON parse fails
   → blocked + invalid_artifact_schema (error_kind=json_parse)
   → STOP

3. Raw-dict preflight (operates on parsed dict, BEFORE Pydantic):
   3a. Stratified-threshold keys present
       → blocked + unsupported_stratified_thresholds
       → continue (this is detectable independent of Pydantic shape)
   3b. Any digest-relevant float is NaN/Inf
       → blocked + invalid_artifact_schema (error_kind=non_finite_float)
       → STOP

4. Day4CalibrationArtifactV1.model_validate fails
   → blocked + invalid_artifact_schema (error_kind=pydantic)
   → STOP

5. Accumulate (artifact is structurally valid from here):
   5a. wrong_score_binding_semantics  (block)
   5b. score_binding_metadata_drift   (warn)
   5c. unsupported_calibration_mode   (block, if mode=cv)
   5d. no_cutoff_available            (block)
   5e. artifact_too_stale             (block)
   5f. label_support_unavailable      (block, if mode=bootstrap and breakdown missing)
   5g. insufficient_label_support     (block, if breakdown present and floors fail)
   5h. precision_below_target         (block)
       OR precision_band_below_target (warn, only if 5h didn't fire)
   5i. calibration_target_mismatch    (warn)

Status resolution: any blocking → "blocked"; else any warning → "warn"; else "ready".
candidate_router_threshold_config embedded only when status is "warn" or "ready".
```

The hard stops in steps 1, 2, 3b, 4 prevent emitting downstream reason codes derived from undefined / partially-constructed state. A dedicated test pins this: `test_invalid_artifact_schema_does_not_emit_secondary_reasons`.

`unsupported_stratified_thresholds` is the only step-3 reason that can coexist with later accumulation — but only because steps 4 and 5 still need to succeed for those later reasons to be safely computable. Practical answer: when `unsupported_stratified_thresholds` fires AND step 4 then fails, the final status has both reasons; that's correct (both are real, both should appear).

---

## 8. Calibration semantic digest

Field name: `calibration_semantic_digest`. SHA256 over canonical JSON of the digest payload.

**Digest payload contents:**

- `schema_version`, `artifact_type`
- `score_binding` (all 8 fields)
- `target_precision`
- `mode`
- `chosen_cutoff.value` and `chosen_cutoff.rule`
- `bootstrap.precision_at_cutoff` band (mean, p5, p50, p95) when present
- `cv.precision_at_cutoff` band when present
- `input.calibration_split_sha`, `input.train_split_sha`, `input.holdout_split_sha`
- `input.calibration_label_breakdown` when present
- `scoring_provenance` (full block)

**Excluded:** `generated_at`, `seed`, `git`.

**Float normalization:** before serialization, all finite floats in the payload are rounded to `DIGEST_FLOAT_PRECISION_DECIMALS = 6`. NaN and Infinity are not rounded; their presence triggers `invalid_artifact_schema` (`error_kind=non_finite_float`) at preflight step 3b, so the digest serializer never sees them.

**Canonical serialization:**

```python
json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
)
```

Then `hashlib.sha256(serialized.encode("utf-8")).hexdigest()`.

The digest is recorded in two places:
- **Top-level** `RouterConfigStatusV1.calibration_semantic_digest` whenever the artifact parses far enough to read `score_binding` (so the common `blocked` outputs still carry forensic identity)
- **Nested** `CandidateRouterThresholdConfigV1.calibration_semantic_digest` (same value) when the candidate is embedded

The digest is **for invalidation tracking only** in v1. The future router-application gate may compare digests across runs to decide whether a previously-approved cut-off is still valid.

---

## 9. Calibration summary extraction

Helper function in `scripts/write_router_config_status.py` (or `verification/router_threshold_config.py` if reused):

```python
def extract_calibration_summary(
    artifact: Day4CalibrationArtifactV1,
) -> CalibrationSummaryV1:
    """Map Day 4 nested artifact fields to Day 5 flat summary.

    Missing nested blocks resolve to None for the corresponding output field;
    they do NOT raise. Reason codes (no_cutoff_available, label_support_unavailable)
    are emitted by the writer based on the accumulator order, not by this helper.
    """
```

Mapping:

| Day 5 field | Day 4 source path |
|---|---|
| `mode` | `artifact.mode` |
| `target_precision` | `artifact.target_precision` |
| `chosen_cutoff_value` | `artifact.chosen_cutoff["value"]` |
| `bootstrap_p50` | `artifact.bootstrap["precision_at_cutoff"]["p50"]` |
| `bootstrap_p5` | `artifact.bootstrap["precision_at_cutoff"]["p5"]` |
| `label_breakdown_tp` | `artifact.input["calibration_label_breakdown"]["TP"]` |
| `label_breakdown_fp` | `artifact.input["calibration_label_breakdown"]["FP"]` |
| `label_breakdown_unsure` | `artifact.input["calibration_label_breakdown"]["UNSURE"]` |
| `label_breakdown_missing` | `artifact.input["calibration_label_breakdown"]["missing"]` |

Handles missing intermediate dicts via `.get()` chains; outputs `None` for any missing leaf.

---

## 10. Test plan

### Per-reason fixtures (in `tests/scripts/test_write_router_config_status.py`)

| Fixture | Expected status | Expected reasons | Hard stop? |
|---|---|---|---|
| Missing `--artifact` file | `blocked` | `artifact_missing` | YES |
| Malformed JSON | `blocked` | `invalid_artifact_schema` (error_kind=json_parse) | YES |
| NaN in `bootstrap.precision_at_cutoff.p50` | `blocked` | `invalid_artifact_schema` (error_kind=non_finite_float) | YES |
| Infinity in `chosen_cutoff.value` | `blocked` | `invalid_artifact_schema` (error_kind=non_finite_float) | YES |
| Pydantic ValidationError (e.g. missing required field, wrong type) | `blocked` | `invalid_artifact_schema` (error_kind=pydantic) | YES |
| `per_stratum_cutoffs` at top level | `blocked` | `unsupported_stratified_thresholds` (offending_keys) — may co-occur with later reasons if Pydantic also fails | NO |
| `chosen_cutoff.per_stratum_cutoffs` nested | `blocked` | `unsupported_stratified_thresholds` | NO |
| `bootstrap.per_stratum_cutoffs` nested | `blocked` | `unsupported_stratified_thresholds` | NO |
| Wrong `score_binding.column = "score"` | `blocked` | `wrong_score_binding_semantics` (payload: column expected/observed) | NO |
| Wrong `score_binding.score_direction` | `blocked` | `wrong_score_binding_semantics` | NO |
| Wrong `score_binding.decision_rule` | `blocked` | `wrong_score_binding_semantics` | NO |
| Wrong `score_binding.table = "signal_v2"` | `blocked` | `wrong_score_binding_semantics` | NO |
| Wrong `score_binding.semantic_name` | `blocked` | `wrong_score_binding_semantics` | NO |
| Drifted `score_binding.producer = "manual_run"` | `warn` (no other warnings) | `score_binding_metadata_drift` (per-field payload) | NO |
| Drifted `score_binding.version` | `warn` | `score_binding_metadata_drift` | NO |
| Drifted `score_binding.version_policy` | `warn` | `score_binding_metadata_drift` | NO |
| Both semantic + metadata mismatch | `blocked` | both reasons present | NO |
| `mode="cv"` artifact | `blocked` | `unsupported_calibration_mode` | NO |
| `chosen_cutoff.value=null` | `blocked` | `no_cutoff_available` | NO |
| `now - generated_at = 8 days` | `blocked` | `artifact_too_stale` | NO |
| `now - generated_at = 7 days` exactly | not stale | (boundary check) | NO |
| `mode=bootstrap`, `calibration_label_breakdown={}` | `blocked` | `label_support_unavailable` | NO |
| `TP=5, FP=40` (TP < 10) | `blocked` | `insufficient_label_support` | NO |
| `TP=15, FP=10` (TP+FP < 30) | `blocked` | `insufficient_label_support` | NO |
| `p50 < target` | `blocked` | `precision_below_target` | NO |
| `p50 >= target`, `p5 < target` | `warn` | `precision_band_below_target` | NO |
| `p50 >= target`, `p5 >= target`, all gates pass | `ready` | (none) | NO |
| Artifact target=0.85, policy target=0.90 | `warn` | `calibration_target_mismatch` | NO |
| Multi-reason fixture (low support + low precision) | `blocked` | both reasons present | NO |

Hard-stop dedicated test:

```python
test_invalid_artifact_schema_does_not_emit_secondary_reasons
# Fixture: malformed JSON. Asserts blocking_reasons == ["invalid_artifact_schema"]
# AND warning_reasons == [] AND candidate_router_threshold_config is None.
```

### Status metadata tests

| Assertion |
|---|
| Every status JSON has `readiness_scope == "human_review_only"` |
| Every status JSON has `runtime_effective is False` |
| Every status JSON has `may_route is False` |
| Every status JSON has `future_router_validation_required is True` |
| Every status JSON has `policy.version == "router_config_status_policy.v1"` |
| Every status JSON has `policy.target_precision == --target-precision` |
| Every status JSON has `policy.max_age_days == --max-age-days` |
| Every status JSON has `policy.support_floor_tp == 10` and `policy.support_floor_tp_plus_fp == 30` |
| `evaluated_at` matches `--now` when supplied; valid ISO 8601 UTC otherwise |
| `artifact_reference.path` matches `--artifact` |
| `artifact_reference.sha256` matches SHA256 of raw bytes (when artifact exists) |
| `calibration_semantic_digest` is present at top level when artifact parses far enough to read `score_binding` |
| `calibration_semantic_digest` is None on `artifact_missing` |
| `calibration_semantic_digest` is None on `invalid_artifact_schema` (json_parse) |

### Candidate self-defensive guards

| Assertion |
|---|
| When status is `ready` or `warn`, `candidate_router_threshold_config.activation == "manual_review_required"` |
| When status is `ready` or `warn`, `candidate_router_threshold_config.production_routing_enabled is False` |
| When status is `blocked`, `candidate_router_threshold_config is None` |
| Top-level `calibration_semantic_digest` equals nested digest when both are present |

### Digest tests

| Assertion |
|---|
| `generated_at` change does not affect digest |
| `seed` change does not affect digest |
| `git` block change does not affect digest |
| `score_binding.column` change DOES affect digest |
| `chosen_cutoff.value` change DOES affect digest |
| `scoring_provenance.active_thesis_prompt_version` change DOES affect digest |
| `test_digest_stable_across_float_noise`: artifact A vs A' with `0.6500001` vs `0.6500009` → identical digest (6-decimal rounding) |
| Two Python processes / same artifact → identical digest (determinism) |
| `allow_nan=False` raises before producing a digest with NaN/Inf — surfaced as `invalid_artifact_schema` not as a stack trace |

### Calibration summary extractor

```python
test_extract_calibration_summary_handles_missing_nested_blocks
# Fixtures: no bootstrap block → bootstrap_p50/p5 are None.
#           no input.calibration_label_breakdown → all label_breakdown_* are None.
#           chosen_cutoff missing 'value' key → chosen_cutoff_value is None.
# Helper never raises on missing nested fields; reason codes drive the user-visible failure.
```

### CLI / IO tests

| Assertion |
|---|
| Atomic write: `state/router_config_status.json.tmp` does not persist after a successful run |
| Atomic write: on simulated write failure mid-rename, `state/router_config_status.json` is unchanged (or absent) |
| Output dir auto-created when absent |
| Unwritable output → exit 1 |
| Invalid CLI flag (e.g. `--bogus`) → exit 2 (argparse default; not customized) |
| Missing CLI value (e.g. `--target-precision` with no number) → exit 2 |
| `state/router_config_status.json` and `state/router_config_status.json.tmp` are in `.gitignore` |
| `--now 2026-04-28T12:00:00Z` produces deterministic `evaluated_at` |
| `--now` is used for freshness boundary, not just `evaluated_at` |
| Boundary: `now == generated_at + max_age_days` is NOT stale; `now > generated_at + max_age_days` IS stale |

### Score-binding pin test

| Assertion |
|---|
| Day 5's `EXPECTED_SCORE_PRODUCER` equals `scripts.recalibrate_conformal.SCORE_PRODUCER` (imported at test time) |
| Day 5's `EXPECTED_SCORE_VERSION` equals `scripts.recalibrate_conformal.SCORE_VERSION` |
| Day 5's `EXPECTED_SCORE_VERSION_POLICY` equals `scripts.recalibrate_conformal.SCORE_VERSION_POLICY` |
| Day 5's `EXPECTED_SCORE_TABLE` equals `scripts.recalibrate_conformal.SCORE_TABLE` |
| Day 5's `EXPECTED_SCORE_COLUMN` equals `scripts.recalibrate_conformal.SCORE_COLUMN` |
| Day 5's `EXPECTED_SEMANTIC_NAME` equals `scripts.recalibrate_conformal.SCORE_SEMANTIC_NAME` |
| Day 5's `EXPECTED_SCORE_DIRECTION` equals `scripts.recalibrate_conformal.SCORE_DIRECTION` |
| Day 5's `EXPECTED_DECISION_RULE` equals `scripts.recalibrate_conformal.DECISION_RULE` |

(If Day 4 ever adds a 9th score_binding field via its `_assemble_artifact` helper, `Day4CalibrationArtifactV1.score_binding` will fail validation under `extra="forbid"` and surface as `invalid_artifact_schema`. That is the desired Day 5 behavior — don't silently swallow Day-4 contract changes.)

### Regression / import-guard tests

| Assertion |
|---|
| `verification.verification_gate_v2.VerificationGate` thresholds unchanged (snapshot test against current values) |
| AST scan: `verification.router_threshold_policy` does NOT import `PushDecision` |
| AST scan: `workflows.pipeline` does NOT import anything from `verification.router_threshold_policy` |
| AST scan: `workflows.pipeline` does NOT import anything from `verification.router_threshold_config` |
| All existing Day 4 conformal tests in `tests/scripts/test_recalibrate_conformal.py` still pass (don't pin the count) |

### Promotion-codes guard (enum-based, not grep)

```python
RESERVED_PROMOTION_CODES = frozenset({
    "promotion_prompt_version_drift",
    "promotion_scoring_path_drift",
    "promotion_runtime_threshold_incompatible",
})

# These are documented but reserved; v1 must not emit them.
def test_reserved_promotion_codes_not_in_v1_enums():
    blocking = set(get_args(BlockingReasonV1))
    warning  = set(get_args(WarningReasonV1))
    assert RESERVED_PROMOTION_CODES.isdisjoint(blocking)
    assert RESERVED_PROMOTION_CODES.isdisjoint(warning)
```

This replaces the substring-grep guard from plan v1 (which would false-positive match `promotion_prompt_version_drift` against `prompt_version_drift`).

### Pydantic model contract tests

| Assertion |
|---|
| `RouterConfigStatusV1.model_validate(model.model_dump())` round-trips losslessly for blocked / warn / ready fixtures |
| `CandidateRouterThresholdConfigV1.model_validate(model.model_dump())` round-trips losslessly |
| `Day4CalibrationArtifactV1.model_validate(<live Day 4 fixture>)` succeeds |
| `model_dump(mode="json")` of any `RouterConfigStatusV1` produces only JSON-serializable types |
| `model_dump(mode="json")` is deterministic across two calls |
| Status file written to disk parses back to a valid `RouterConfigStatusV1` |
| Removing any required field from a status dict makes `RouterConfigStatusV1.model_validate` raise `ValidationError` (one representative test) |

**Estimated test count:** ~50 new tests. (Number is an estimate; the durable assertion is "every fixture in §10 is covered by at least one test.")

### Policy stub tests

| Assertion |
|---|
| `raw_signal_passes_threshold(0.7, config_with_threshold_0.7) is True` (>= boundary) |
| `raw_signal_passes_threshold(0.69, config_with_threshold_0.7) is False` |
| Confidence < 0 raises `ValueError` |
| Confidence > 1 raises `ValueError` |
| Confidence is `NaN` raises `ValueError` |

---

## 11. `.planning/PROJECT.md` vocabulary + handoff note

References use `file:symbol` (not `file:line`) so they don't go stale.

```markdown
## Phase 2 confidence-score vocabulary

| Term | What it is | Where defined |
|---|---|---|
| `signals.confidence` | REAL column, in [0,1], per-signal raw confidence at storage time | `storage/signal_store.py` (column declaration in the signals-table CREATE) |
| `score_binding.semantic_name = "signal_stored_confidence"` | Locked semantic name for `signals.confidence` in the Day 4 artifact contract | `scripts/recalibrate_conformal.py:SCORE_SEMANTIC_NAME` |
| `ConfidenceBreakdown.overall` | Aggregate confidence after VerificationGate fusion (LLM + structural + reputation). NOT the same as `signals.confidence`. | `verification/verification_gate_v2.py:ConfidenceBreakdown` |
| `state/conformal_calibration.json` | Day 4 artifact (`artifact_type=threshold_selection`) with `score_binding` and `chosen_cutoff` over `signals.confidence` | `scripts/recalibrate_conformal.py` (gitignored) |
| `state/router_config_status.json` | Day 5 status writer output (`readiness_scope=human_review_only`). Embeds `candidate_router_threshold_config` only when not blocked. NEVER a production routing config. | `scripts/write_router_config_status.py` (gitignored) |
| `calibration_semantic_digest` | SHA256 over the semantic content of the Day 4 artifact (excludes `generated_at`, `seed`, `git`; floats normalized to 6 decimals; `allow_nan=False`). | `verification/router_threshold_config.py` |
| Reserved promotion drift codes | `promotion_prompt_version_drift`, `promotion_scoring_path_drift`, `promotion_runtime_threshold_incompatible` — owned by the future router-application gate. NEVER emitted by Day 5; Pydantic Literal enums prevent accidental emission. | reserved in `verification/router_threshold_config.py` module docstring |

## Day 5 human-review handoff (current)

Day 5 human review currently means manual inspection of `state/router_config_status.json`
by the engineer/operator running Phase 2. No approval, rejection, or promotion decision
is persisted by Day 5. Future router-application/gating work owns review persistence and
promotion. The Day 3 dashboard may later surface this status, but Day 5 does not notify
Slack, Notion, or governance systems.
```

---

## 12. Verification (run before merge)

```bash
# 1. Day 5 against current live Day 4 artifact (regenerate if absent)
python scripts/recalibrate_conformal.py --target-precision 0.90 || true
test -f state/conformal_calibration.json
python scripts/write_router_config_status.py --target-precision 0.90
python -m json.tool state/router_config_status.json | head -60
# Expected on current live data: status="blocked";
# blocking_reasons includes precision_below_target (live max attainable ~0.154) AND
# insufficient_label_support (TP=3 < 10 floor);
# readiness_scope="human_review_only"; runtime_effective=false; may_route=false;
# calibration_semantic_digest present at top level (artifact parsed); candidate is None.

# 2. Test suite
pytest tests/scripts/test_write_router_config_status.py -v
pytest tests/scripts/test_recalibrate_conformal.py -v          # all Day 4 conformal tests still pass
pytest tests/verification/ -v                                  # VerificationGate snapshot

# 3. Promotion-codes guard (enum-based, not grep)
pytest tests/scripts/test_write_router_config_status.py::test_reserved_promotion_codes_not_in_v1_enums -v

# 4. Import guards (AST scan; complement to the unit tests)
python -c "import workflows.pipeline; import sys; assert 'router_threshold_policy' not in sys.modules"
python -c "import verification.router_threshold_policy as p; assert 'PushDecision' not in dir(p)"

# 5. Atomic-write sanity
ls state/router_config_status.json.tmp 2>&1 | grep -q "No such file" && echo "ok: no stale tmp"

# 6. Deterministic --now
python scripts/write_router_config_status.py --now 2026-04-28T12:00:00Z
python -m json.tool state/router_config_status.json | grep evaluated_at
# Expected: "evaluated_at": "2026-04-28T12:00:00Z"
```

---

## 13. Worktree parallelization

Single PR, single workstream. Estimated total effort with CC: ~60–75 min.

| Step | Files | Parallel? |
|---|---|---|
| 1. Pydantic models + Literal enums + constants | `verification/router_threshold_config.py` | — |
| 2. Policy stub | `verification/router_threshold_policy.py` | parallel with 1 |
| 3. CLI writer + summary extractor + digest helper | `scripts/write_router_config_status.py` | sequential after 1 |
| 4. Tests | `tests/scripts/test_write_router_config_status.py` | sequential after 3 |
| 5. `.gitignore` + `.planning/PROJECT.md` | trivial | parallel anywhere |

---

## 14. Out of scope (deferred — explicit)

- Live drift detection (prompt version, scoring path, runtime threshold) → **future router-application gate**
- ADR for Day 5 → defer until live routing or promotion design
- Governance registration / `record_feature_promote()`
- Day 3 dashboard `conformal_calibration` block → ADR-041 deferred consumer #2
- Cross-machine promotion of `state/conformal_calibration.json` to a tracked artifact
- JSON Schemas (`schemas/*.v1.json`) and `jsonschema` dep → dropped; Pydantic is the single contract source
- `--dry-run` flag → not in v1 (temp `--out` covers the no-write case)
- Per-stratum / Mondrian conformal → Day 4 constraint 3 forbids it

---

## 15. Required-reviews status

| Review | Round | Outcome |
|---|---|---|
| `/plan-eng-review` (Claude) | 1 | DONE_WITH_CONCERNS → revisions adopted in v1 |
| Outside review (user, round 1) | 1 | Approve after revision → drift-checks reversed, scope reduced to artifact-only |
| Outside review (user, round 2 — this revision) | 2 | Approve with amendments → P0×5 + P1×7 + P2×5 integrated → **implementation-ready** |

**Net:** core architecture stable since round 1. Round 2 sharpens contract precision (score-binding split, Literal enums, hard-stop sequencing, raw-dict preflight ordering, digest float normalization, top-level forensic identity, candidate self-defense).

**Plan v2 is implementation-ready.** Implementer should follow §3 (files), §4 (models), §6 (reasons), §7 (accumulator order), §10 (tests) literally.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | DONE | round 1: 2 important + 1 critical-gap + 5 minor; round 2 (user outside review): scope-discipline reversal of drift checks, JSON schemas dropped; round 3 (this revision): P0×5 + P1×7 + P2×5 contract-precision amendments integrated |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | n/a | no UI surface |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG REVIEWED — **implementation-ready.** All 17 amendments from outside-review round 2 integrated into the plan with explicit ordering, payload shapes, and Literal-enum guards. No open decisions.
