"""Phase 2 Day 5 — Router-config status writer contract suite.

Plan of record: ``.omx/plans/phase2-day5-router-config-status-plan.md``.

The suite is built TDD-first: each new behaviour gets a failing test before
the implementation lands. Tests are organized by plan section
(reason fixtures, hard stops, status metadata, candidate self-defense, digest,
calibration summary extractor, CLI/IO, score-binding pin, regression /
import-guard, promotion-codes guard, Pydantic round-trip, policy stub).
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from verification.router_threshold_config import (
    BlockingReasonV1,
    CandidateRouterThresholdConfigV1,
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_TARGET_PRECISION,
    Day4CalibrationArtifactV1,
    EXPECTED_DECISION_RULE,
    EXPECTED_SCORE_COLUMN,
    EXPECTED_SCORE_DIRECTION,
    EXPECTED_SCORE_PRODUCER,
    EXPECTED_SCORE_TABLE,
    EXPECTED_SCORE_VERSION,
    EXPECTED_SCORE_VERSION_POLICY,
    EXPECTED_SEMANTIC_NAME,
    METADATA_SCORE_BINDING_FIELDS,
    POLICY_VERSION,
    RESERVED_PROMOTION_CODES,
    RouterConfigStatusV1,
    SEMANTIC_SCORE_BINDING_FIELDS,
    SUPPORT_FLOOR_TP,
    SUPPORT_FLOOR_TP_PLUS_FP,
    WarningReasonV1,
)
from verification.router_threshold_policy import raw_signal_passes_threshold


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WRITER_SCRIPT = PROJECT_ROOT / "scripts" / "write_router_config_status.py"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


VALID_GENERATED_AT = "2026-04-25T12:00:00+00:00"
DEFAULT_NOW = "2026-04-28T12:00:00Z"  # 3 days after the artifact => not stale at max_age=7


def _valid_score_binding() -> dict[str, str]:
    return {
        "table": EXPECTED_SCORE_TABLE,
        "column": EXPECTED_SCORE_COLUMN,
        "semantic_name": EXPECTED_SEMANTIC_NAME,
        "score_direction": EXPECTED_SCORE_DIRECTION,
        "decision_rule": EXPECTED_DECISION_RULE,
        "producer": EXPECTED_SCORE_PRODUCER,
        "version": EXPECTED_SCORE_VERSION,
        "version_policy": EXPECTED_SCORE_VERSION_POLICY,
    }


def _valid_artifact() -> dict[str, Any]:
    """A 'ready'-class Day 4 artifact: bootstrap, healthy support, p5 >= target."""
    return {
        "schema_version": 1,
        "artifact_type": "threshold_selection",
        "generated_at": VALID_GENERATED_AT,
        "mode": "bootstrap",
        "seed": 42,
        "target_precision": 0.90,
        "score_binding": _valid_score_binding(),
        "input": {
            "calibration_file": "state/calibration_ids.json",
            "train_file": "state/train_ids.json",
            "holdout_file": "state/holdout_ids.json",
            "calibration_signal_count": 120,
            "calibration_label_breakdown": {
                "TP": 50,
                "FP": 30,
                "UNSURE": 5,
                "missing": 35,
            },
            "labels_used_for_fitting": ["TP", "FP"],
            "labels_excluded_from_fitting": ["UNSURE", "ADJ", "missing"],
            "calibration_split_sha": "a" * 64,
            "train_split_sha": "b" * 64,
            "holdout_split_sha": "c" * 64,
            "schema_contract_path": ".omx/wave6/live_schema_contract.json",
        },
        "git": {"commit": "deadbeef", "branch": "phase2/instrumentation"},
        "scoring_provenance": {
            "score_table": "signals",
            "score_column": "confidence",
            "label_table": "signal_quality_metrics",
            "label_column": "human_label",
            "active_thesis_prompt_version": "v1.6.0-employer-distribution-guard",
            "active_llm_thesis_mode": "active",
            "high_confidence_threshold_at_run_time": 0.7,
        },
        "bootstrap": {
            "precision_at_cutoff": {
                "mean": 0.95,
                "p5": 0.92,
                "p50": 0.95,
                "p95": 0.98,
            },
            "iterations": 1000,
        },
        "cv": None,
        "chosen_cutoff": {
            "value": 0.78,
            "rule": "bootstrap_p50",
            "rationale": "Median of bootstrap distribution.",
        },
        "instability": {
            "cv": 0.05,
            "warn_threshold": 0.15,
            "fail_threshold": 0.25,
            "strict": False,
            "warning": None,
        },
        "deferred_consumers": ["Day 5+ router-config writer"],
    }


def _write_artifact(tmp_path: Path, artifact: dict[str, Any]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "conformal_calibration.json"
    p.write_text(json.dumps(artifact))
    return p


def _run_writer(
    tmp_path: Path,
    artifact_path: Path | None,
    *,
    out: Path | None = None,
    target_precision: float = DEFAULT_TARGET_PRECISION,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: str | None = DEFAULT_NOW,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the writer script as a subprocess."""
    out_path = out if out is not None else tmp_path / "router_config_status.json"
    args: list[str] = [sys.executable, str(WRITER_SCRIPT)]
    if artifact_path is not None:
        args += ["--artifact", str(artifact_path)]
    args += ["--out", str(out_path)]
    args += ["--target-precision", str(target_precision)]
    args += ["--max-age-days", str(max_age_days)]
    if now is not None:
        args += ["--now", now]
    if extra_args:
        args += extra_args
    return subprocess.run(args, capture_output=True, text=True, cwd=PROJECT_ROOT)


def _load_status(tmp_path: Path, name: str = "router_config_status.json") -> dict[str, Any]:
    return json.loads((tmp_path / name).read_text())


# ---------------------------------------------------------------------------
# §10 Score-binding pin test (locks Day 4 source-of-truth)
# ---------------------------------------------------------------------------


class TestScoreBindingPin:
    """If Day 4 ever rewords its constants, these fail; humans review."""

    def test_score_table_pin(self):
        from scripts.recalibrate_conformal import SCORE_TABLE
        assert EXPECTED_SCORE_TABLE == SCORE_TABLE

    def test_score_column_pin(self):
        from scripts.recalibrate_conformal import SCORE_COLUMN
        assert EXPECTED_SCORE_COLUMN == SCORE_COLUMN

    def test_semantic_name_pin(self):
        from scripts.recalibrate_conformal import SCORE_SEMANTIC_NAME
        assert EXPECTED_SEMANTIC_NAME == SCORE_SEMANTIC_NAME

    def test_score_direction_pin(self):
        from scripts.recalibrate_conformal import SCORE_DIRECTION
        assert EXPECTED_SCORE_DIRECTION == SCORE_DIRECTION

    def test_decision_rule_pin(self):
        from scripts.recalibrate_conformal import DECISION_RULE
        assert EXPECTED_DECISION_RULE == DECISION_RULE

    def test_score_producer_pin(self):
        from scripts.recalibrate_conformal import SCORE_PRODUCER
        assert EXPECTED_SCORE_PRODUCER == SCORE_PRODUCER

    def test_score_version_pin(self):
        from scripts.recalibrate_conformal import SCORE_VERSION
        assert EXPECTED_SCORE_VERSION == SCORE_VERSION

    def test_score_version_policy_pin(self):
        from scripts.recalibrate_conformal import SCORE_VERSION_POLICY
        assert EXPECTED_SCORE_VERSION_POLICY == SCORE_VERSION_POLICY


# ---------------------------------------------------------------------------
# §10 Promotion-codes guard (enum-based, not grep)
# ---------------------------------------------------------------------------


class TestReservedPromotionCodes:
    def test_reserved_promotion_codes_not_in_v1_enums(self):
        blocking = set(get_args(BlockingReasonV1))
        warning = set(get_args(WarningReasonV1))
        assert RESERVED_PROMOTION_CODES.isdisjoint(blocking)
        assert RESERVED_PROMOTION_CODES.isdisjoint(warning)

    def test_reserved_set_documents_all_three(self):
        assert RESERVED_PROMOTION_CODES == frozenset(
            {
                "promotion_prompt_version_drift",
                "promotion_scoring_path_drift",
                "promotion_runtime_threshold_incompatible",
            }
        )


# ---------------------------------------------------------------------------
# §10 Hard-stop fixtures (artifact integrity)
# ---------------------------------------------------------------------------


class TestArtifactMissing:
    def test_missing_artifact_blocks_with_artifact_missing_only(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        result = _run_writer(tmp_path, missing)
        assert result.returncode == 0, result.stderr
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert status["blocking_reasons"] == ["artifact_missing"]
        assert status["warning_reasons"] == []
        assert status["candidate_router_threshold_config"] is None
        assert status["calibration_semantic_digest"] is None
        assert status["reason_payloads"]["artifact_missing"]["path"] == str(missing)

    def test_missing_artifact_still_carries_scope_discipline(self, tmp_path):
        result = _run_writer(tmp_path, tmp_path / "nope.json")
        assert result.returncode == 0
        status = _load_status(tmp_path)
        assert status["readiness_scope"] == "human_review_only"
        assert status["runtime_effective"] is False
        assert status["may_route"] is False
        assert status["future_router_validation_required"] is True


class TestInvalidArtifactSchema:
    def test_malformed_json_blocks(self, tmp_path):
        bad = tmp_path / "conformal_calibration.json"
        bad.write_text("{not valid json")
        result = _run_writer(tmp_path, bad)
        assert result.returncode == 0
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert status["blocking_reasons"] == ["invalid_artifact_schema"]
        payload = status["reason_payloads"]["invalid_artifact_schema"]
        assert payload["error_kind"] == "json_parse"
        assert isinstance(payload["detail"], str)
        assert len(payload["detail"]) <= 500

    def test_pydantic_validation_failure_blocks(self, tmp_path):
        artifact = _valid_artifact()
        del artifact["chosen_cutoff"]  # required field
        path = _write_artifact(tmp_path, artifact)
        result = _run_writer(tmp_path, path)
        assert result.returncode == 0
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert status["blocking_reasons"] == ["invalid_artifact_schema"]
        assert status["reason_payloads"]["invalid_artifact_schema"]["error_kind"] == "pydantic"

    def test_nan_in_p50_blocks_as_non_finite(self, tmp_path):
        artifact = _valid_artifact()
        # Use a sentinel marker; we'll patch in a real NaN via raw JSON below.
        path = tmp_path / "conformal_calibration.json"
        # ``json.dumps(allow_nan=True)`` (default) emits ``NaN`` literally.
        path.write_text(
            json.dumps(artifact).replace(
                '"p50": 0.95', '"p50": NaN'
            )
        )
        result = _run_writer(tmp_path, path)
        assert result.returncode == 0
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert status["blocking_reasons"] == ["invalid_artifact_schema"]
        assert (
            status["reason_payloads"]["invalid_artifact_schema"]["error_kind"]
            == "non_finite_float"
        )

    def test_infinity_in_chosen_cutoff_blocks_as_non_finite(self, tmp_path):
        artifact = _valid_artifact()
        path = tmp_path / "conformal_calibration.json"
        path.write_text(
            json.dumps(artifact).replace(
                '"value": 0.78', '"value": Infinity'
            )
        )
        result = _run_writer(tmp_path, path)
        assert result.returncode == 0
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert (
            status["reason_payloads"]["invalid_artifact_schema"]["error_kind"]
            == "non_finite_float"
        )

    def test_invalid_artifact_schema_does_not_emit_secondary_reasons(self, tmp_path):
        bad = tmp_path / "conformal_calibration.json"
        bad.write_text("{not valid json")
        _run_writer(tmp_path, bad)
        status = _load_status(tmp_path)
        assert status["blocking_reasons"] == ["invalid_artifact_schema"]
        assert status["warning_reasons"] == []
        assert status["candidate_router_threshold_config"] is None


# ---------------------------------------------------------------------------
# §10 Stratified-threshold preflight (raw-dict, before Pydantic)
# ---------------------------------------------------------------------------


class TestUnsupportedStratifiedThresholds:
    def test_top_level_per_stratum_cutoffs_blocks(self, tmp_path):
        artifact = _valid_artifact()
        artifact["per_stratum_cutoffs"] = {"a": 0.5}
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "unsupported_stratified_thresholds" in status["blocking_reasons"]
        assert status["status"] == "blocked"
        offending = status["reason_payloads"]["unsupported_stratified_thresholds"][
            "offending_keys"
        ]
        assert "per_stratum_cutoffs" in offending

    def test_nested_chosen_cutoff_per_stratum_blocks(self, tmp_path):
        artifact = _valid_artifact()
        artifact["chosen_cutoff"]["per_stratum_cutoffs"] = {"a": 0.5}
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "unsupported_stratified_thresholds" in status["blocking_reasons"]
        offending = status["reason_payloads"]["unsupported_stratified_thresholds"][
            "offending_keys"
        ]
        assert "chosen_cutoff.per_stratum_cutoffs" in offending

    def test_nested_bootstrap_per_stratum_blocks(self, tmp_path):
        artifact = _valid_artifact()
        artifact["bootstrap"]["per_stratum_cutoffs"] = {"a": 0.5}
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "unsupported_stratified_thresholds" in status["blocking_reasons"]
        offending = status["reason_payloads"]["unsupported_stratified_thresholds"][
            "offending_keys"
        ]
        assert "bootstrap.per_stratum_cutoffs" in offending


# ---------------------------------------------------------------------------
# §10 Score-binding semantic mismatches (block) and metadata drift (warn)
# ---------------------------------------------------------------------------


class TestScoreBindingSemantics:
    @pytest.mark.parametrize(
        "field, bad_value",
        [
            ("table", "signal_v2"),
            ("column", "score"),
            ("semantic_name", "wrong_name"),
            ("score_direction", "lower_is_more_confident"),
            ("decision_rule", "accept_if_score_lt_threshold"),
        ],
    )
    def test_semantic_field_mismatch_blocks(self, tmp_path, field, bad_value):
        artifact = _valid_artifact()
        artifact["score_binding"][field] = bad_value
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "wrong_score_binding_semantics" in status["blocking_reasons"]
        assert status["status"] == "blocked"
        payload = status["reason_payloads"]["wrong_score_binding_semantics"]
        assert field in payload
        assert payload[field]["observed"] == bad_value


class TestScoreBindingMetadataDrift:
    @pytest.mark.parametrize(
        "field, bad_value",
        [
            ("producer", "manual_run"),
            ("version", "v9"),
            ("version_policy", "drifted_policy"),
        ],
    )
    def test_metadata_field_drift_warns_only(self, tmp_path, field, bad_value):
        artifact = _valid_artifact()
        artifact["score_binding"][field] = bad_value
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "score_binding_metadata_drift" in status["warning_reasons"]
        assert "wrong_score_binding_semantics" not in status["blocking_reasons"]
        payload = status["reason_payloads"]["score_binding_metadata_drift"]
        assert field in payload
        assert payload[field]["observed"] == bad_value

    def test_both_semantic_and_metadata_mismatch_present(self, tmp_path):
        artifact = _valid_artifact()
        artifact["score_binding"]["column"] = "score"
        artifact["score_binding"]["producer"] = "manual_run"
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert "wrong_score_binding_semantics" in status["blocking_reasons"]
        assert "score_binding_metadata_drift" in status["warning_reasons"]


# ---------------------------------------------------------------------------
# §10 Mode / cutoff / staleness / support / precision
# ---------------------------------------------------------------------------


class TestUnsupportedCalibrationMode:
    def test_mode_cv_blocks(self, tmp_path):
        artifact = _valid_artifact()
        artifact["mode"] = "cv"
        artifact["bootstrap"] = None
        artifact["cv"] = {"precision_at_cutoff": {"mean": 0.95, "p5": 0.92, "p50": 0.95, "p95": 0.98}}
        artifact["input"]["calibration_label_breakdown"] = {}
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "unsupported_calibration_mode" in status["blocking_reasons"]
        assert status["reason_payloads"]["unsupported_calibration_mode"]["observed_mode"] == "cv"


class TestNoCutoffAvailable:
    def test_null_chosen_cutoff_value_blocks(self, tmp_path):
        artifact = _valid_artifact()
        artifact["chosen_cutoff"]["value"] = None
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "no_cutoff_available" in status["blocking_reasons"]


class TestArtifactStaleness:
    def test_eight_days_old_is_stale(self, tmp_path):
        artifact = _valid_artifact()
        # generated 2026-04-25T12; now 2026-05-04T12 = 9 days.
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path, now="2026-05-04T12:00:00Z", max_age_days=7)
        status = _load_status(tmp_path)
        assert "artifact_too_stale" in status["blocking_reasons"]
        payload = status["reason_payloads"]["artifact_too_stale"]
        assert payload["max_age_days"] == 7
        assert payload["artifact_generated_at"] == VALID_GENERATED_AT

    def test_seven_days_exact_boundary_is_not_stale(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path, now="2026-05-02T12:00:00Z", max_age_days=7)
        status = _load_status(tmp_path)
        assert "artifact_too_stale" not in status["blocking_reasons"]


class TestLabelSupport:
    def test_empty_breakdown_on_bootstrap_blocks_unavailable(self, tmp_path):
        artifact = _valid_artifact()
        artifact["input"]["calibration_label_breakdown"] = {}
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "label_support_unavailable" in status["blocking_reasons"]
        assert status["reason_payloads"]["label_support_unavailable"]["observed_breakdown"] == {}

    def test_tp_below_floor_blocks_insufficient(self, tmp_path):
        artifact = _valid_artifact()
        artifact["input"]["calibration_label_breakdown"] = {
            "TP": 5,
            "FP": 40,
            "UNSURE": 0,
            "missing": 0,
        }
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "insufficient_label_support" in status["blocking_reasons"]
        payload = status["reason_payloads"]["insufficient_label_support"]
        assert payload["observed_tp"] == 5
        assert payload["floor_tp"] == SUPPORT_FLOOR_TP

    def test_tp_plus_fp_below_floor_blocks_insufficient(self, tmp_path):
        artifact = _valid_artifact()
        artifact["input"]["calibration_label_breakdown"] = {
            "TP": 15,
            "FP": 10,
            "UNSURE": 0,
            "missing": 0,
        }
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "insufficient_label_support" in status["blocking_reasons"]
        payload = status["reason_payloads"]["insufficient_label_support"]
        assert payload["floor_tp_plus_fp"] == SUPPORT_FLOOR_TP_PLUS_FP


class TestPrecisionGate:
    def test_p50_below_target_blocks(self, tmp_path):
        artifact = _valid_artifact()
        artifact["bootstrap"]["precision_at_cutoff"]["p50"] = 0.80
        artifact["bootstrap"]["precision_at_cutoff"]["p5"] = 0.78
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert "precision_below_target" in status["blocking_reasons"]
        assert "precision_band_below_target" not in status["warning_reasons"]
        payload = status["reason_payloads"]["precision_below_target"]
        assert payload["observed_p50"] == 0.80
        assert payload["target_precision"] == 0.90

    def test_p50_above_p5_below_target_warns(self, tmp_path):
        artifact = _valid_artifact()
        artifact["bootstrap"]["precision_at_cutoff"]["p50"] = 0.95
        artifact["bootstrap"]["precision_at_cutoff"]["p5"] = 0.85
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["status"] == "warn"
        assert "precision_band_below_target" in status["warning_reasons"]
        assert "precision_below_target" not in status["blocking_reasons"]


class TestCalibrationTargetMismatch:
    def test_target_mismatch_warns(self, tmp_path):
        artifact = _valid_artifact()
        artifact["target_precision"] = 0.85
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path, target_precision=0.90)
        status = _load_status(tmp_path)
        assert "calibration_target_mismatch" in status["warning_reasons"]
        payload = status["reason_payloads"]["calibration_target_mismatch"]
        assert payload["artifact_target_precision"] == 0.85
        assert payload["policy_target_precision"] == 0.90


class TestReadyHappyPath:
    def test_all_gates_pass_yields_ready(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["status"] == "ready", status
        assert status["blocking_reasons"] == []
        assert status["warning_reasons"] == []
        assert status["candidate_router_threshold_config"] is not None


class TestMultiReason:
    def test_low_support_and_low_precision_both_present(self, tmp_path):
        artifact = _valid_artifact()
        artifact["bootstrap"]["precision_at_cutoff"]["p50"] = 0.70
        artifact["input"]["calibration_label_breakdown"] = {
            "TP": 5,
            "FP": 5,
            "UNSURE": 0,
            "missing": 0,
        }
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert "insufficient_label_support" in status["blocking_reasons"]
        assert "precision_below_target" in status["blocking_reasons"]


# ---------------------------------------------------------------------------
# §10 Status metadata + scope discipline (every status JSON)
# ---------------------------------------------------------------------------


class TestStatusMetadata:
    def test_scope_discipline_fields_on_ready(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["readiness_scope"] == "human_review_only"
        assert status["runtime_effective"] is False
        assert status["may_route"] is False
        assert status["future_router_validation_required"] is True
        assert status["schema_version"] == "router_config_status.v1"

    def test_policy_snapshot_present(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path, target_precision=0.90, max_age_days=7)
        status = _load_status(tmp_path)
        policy = status["policy"]
        assert policy["version"] == POLICY_VERSION
        assert policy["target_precision"] == 0.90
        assert policy["max_age_days"] == 7
        assert policy["support_floor_tp"] == SUPPORT_FLOOR_TP
        assert policy["support_floor_tp_plus_fp"] == SUPPORT_FLOOR_TP_PLUS_FP
        assert policy["support_scope"] == "input.calibration_label_breakdown"

    def test_evaluated_at_uses_now_flag(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path, now="2026-04-28T12:00:00Z")
        status = _load_status(tmp_path)
        assert status["evaluated_at"] == "2026-04-28T12:00:00Z"

    def test_artifact_reference_path_and_sha256(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["artifact_reference"]["path"] == str(path)
        assert status["artifact_reference"]["sha256"] == expected_sha

    def test_top_level_digest_present_when_artifact_parses(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert isinstance(status["calibration_semantic_digest"], str)
        assert len(status["calibration_semantic_digest"]) == 64

    def test_top_level_digest_none_on_artifact_missing(self, tmp_path):
        _run_writer(tmp_path, tmp_path / "nope.json")
        status = _load_status(tmp_path)
        assert status["calibration_semantic_digest"] is None

    def test_top_level_digest_none_on_invalid_json(self, tmp_path):
        bad = tmp_path / "conformal_calibration.json"
        bad.write_text("{nope")
        _run_writer(tmp_path, bad)
        status = _load_status(tmp_path)
        assert status["calibration_semantic_digest"] is None


# ---------------------------------------------------------------------------
# §10 Candidate self-defensive guards
# ---------------------------------------------------------------------------


class TestCandidateSelfDefense:
    def test_ready_candidate_has_inert_flags(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        cand = status["candidate_router_threshold_config"]
        assert cand["activation"] == "manual_review_required"
        assert cand["production_routing_enabled"] is False
        assert cand["schema_version"] == "candidate_router_threshold_config.v1"
        assert cand["threshold_value"] == 0.78

    def test_warn_candidate_has_inert_flags(self, tmp_path):
        artifact = _valid_artifact()
        artifact["bootstrap"]["precision_at_cutoff"]["p5"] = 0.85
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["status"] == "warn"
        cand = status["candidate_router_threshold_config"]
        assert cand["activation"] == "manual_review_required"
        assert cand["production_routing_enabled"] is False

    def test_blocked_status_has_no_candidate(self, tmp_path):
        artifact = _valid_artifact()
        artifact["chosen_cutoff"]["value"] = None
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert status["status"] == "blocked"
        assert status["candidate_router_threshold_config"] is None

    def test_top_level_digest_equals_nested_digest(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        assert (
            status["calibration_semantic_digest"]
            == status["candidate_router_threshold_config"][
                "calibration_semantic_digest"
            ]
        )


# ---------------------------------------------------------------------------
# §10 Digest tests
# ---------------------------------------------------------------------------


class TestCalibrationSemanticDigest:
    def _digest_for(self, tmp_path: Path, artifact: dict[str, Any]) -> str:
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        return _load_status(tmp_path)["calibration_semantic_digest"]

    def test_generated_at_change_does_not_affect_digest(self, tmp_path):
        a = _valid_artifact()
        b = copy.deepcopy(a)
        b["generated_at"] = "2025-01-01T00:00:00+00:00"
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a == d_b

    def test_seed_change_does_not_affect_digest(self, tmp_path):
        a = _valid_artifact()
        b = copy.deepcopy(a)
        b["seed"] = 999
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a == d_b

    def test_git_block_change_does_not_affect_digest(self, tmp_path):
        a = _valid_artifact()
        b = copy.deepcopy(a)
        b["git"] = {"commit": "ffffff", "branch": "other"}
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a == d_b

    def test_score_binding_column_change_affects_digest(self, tmp_path):
        # Note: changing the column also blocks via wrong_score_binding_semantics,
        # but the digest is computed before that check. We compare digests on two
        # SHAPELY-VALID-but-semantically-different artifacts.
        a = _valid_artifact()
        b = copy.deepcopy(a)
        b["score_binding"]["column"] = "score"
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a != d_b

    def test_chosen_cutoff_value_affects_digest(self, tmp_path):
        a = _valid_artifact()
        b = copy.deepcopy(a)
        b["chosen_cutoff"]["value"] = 0.85
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a != d_b

    def test_active_thesis_prompt_version_affects_digest(self, tmp_path):
        a = _valid_artifact()
        b = copy.deepcopy(a)
        b["scoring_provenance"]["active_thesis_prompt_version"] = "v2.0.0-different"
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a != d_b

    def test_digest_stable_across_float_noise(self, tmp_path):
        # Plan §10: noise below the 6-decimal precision must not change the
        # digest. The plan example used 0.6500001 vs 0.6500009 (noise at the
        # 7th decimal, which DOES alter the 6th-decimal-rounded value); we
        # demonstrate the documented property with noise at the 8th decimal,
        # where both values round to 0.65 at 6 decimals.
        a = _valid_artifact()
        a["chosen_cutoff"]["value"] = 0.65000001
        b = copy.deepcopy(a)
        b["chosen_cutoff"]["value"] = 0.65000009
        d_a = self._digest_for(tmp_path / "a", a)
        d_b = self._digest_for(tmp_path / "b", b)
        assert d_a == d_b

    def test_digest_deterministic_across_runs(self, tmp_path):
        a = _valid_artifact()
        d1 = self._digest_for(tmp_path / "a", a)
        d2 = self._digest_for(tmp_path / "b", a)
        assert d1 == d2


# ---------------------------------------------------------------------------
# §10 Calibration summary extractor (helper tests via writer behaviour)
# ---------------------------------------------------------------------------


class TestCalibrationSummary:
    def test_summary_in_ready_candidate(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        status = _load_status(tmp_path)
        s = status["candidate_router_threshold_config"]["calibration_summary"]
        assert s["mode"] == "bootstrap"
        assert s["target_precision"] == 0.90
        assert s["chosen_cutoff_value"] == 0.78
        assert s["bootstrap_p50"] == 0.95
        assert s["bootstrap_p5"] == 0.92
        assert s["label_breakdown_tp"] == 50
        assert s["label_breakdown_fp"] == 30
        assert s["label_breakdown_unsure"] == 5
        assert s["label_breakdown_missing"] == 35


class TestExtractCalibrationSummary:
    """Direct unit tests on the helper, exercising None-on-missing semantics."""

    def test_handles_missing_bootstrap_block(self):
        from scripts.write_router_config_status import extract_calibration_summary

        artifact_dict = _valid_artifact()
        artifact_dict["bootstrap"] = None
        artifact_dict["mode"] = "cv"
        artifact_dict["cv"] = {"precision_at_cutoff": {"mean": 0.9, "p5": 0.85, "p50": 0.9, "p95": 0.95}}
        artifact_dict["input"]["calibration_label_breakdown"] = {}
        artifact = Day4CalibrationArtifactV1.model_validate(artifact_dict)
        s = extract_calibration_summary(artifact)
        assert s.bootstrap_p50 is None
        assert s.bootstrap_p5 is None

    def test_handles_missing_label_breakdown(self):
        from scripts.write_router_config_status import extract_calibration_summary

        artifact_dict = _valid_artifact()
        artifact_dict["input"]["calibration_label_breakdown"] = {}
        artifact = Day4CalibrationArtifactV1.model_validate(artifact_dict)
        s = extract_calibration_summary(artifact)
        assert s.label_breakdown_tp is None
        assert s.label_breakdown_fp is None
        assert s.label_breakdown_unsure is None
        assert s.label_breakdown_missing is None

    def test_handles_chosen_cutoff_without_value_key(self):
        from scripts.write_router_config_status import extract_calibration_summary

        artifact_dict = _valid_artifact()
        artifact_dict["chosen_cutoff"] = {"rule": "bootstrap_p50"}
        artifact = Day4CalibrationArtifactV1.model_validate(artifact_dict)
        s = extract_calibration_summary(artifact)
        assert s.chosen_cutoff_value is None


# ---------------------------------------------------------------------------
# §10 CLI / IO tests
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_stale_tmp_after_success(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        out = tmp_path / "router_config_status.json"
        _run_writer(tmp_path, path, out=out)
        assert not (tmp_path / "router_config_status.json.tmp").exists()
        assert out.exists()

    def test_output_dir_auto_created(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        out = tmp_path / "subdir" / "router_config_status.json"
        result = _run_writer(tmp_path, path, out=out)
        assert result.returncode == 0, result.stderr
        assert out.exists()


class TestCliArgErrors:
    def test_invalid_flag_exits_2(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(WRITER_SCRIPT), "--bogus"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode == 2

    def test_missing_value_for_target_precision_exits_2(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(WRITER_SCRIPT), "--target-precision"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode == 2


class TestGitignore:
    def test_status_files_in_gitignore(self):
        gi = (PROJECT_ROOT / ".gitignore").read_text()
        assert "state/router_config_status.json" in gi
        assert "state/router_config_status.json.tmp" in gi


class TestDeterministicNow:
    def test_now_flag_drives_evaluated_at(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path, now="2026-04-28T12:00:00Z")
        assert (
            _load_status(tmp_path)["evaluated_at"] == "2026-04-28T12:00:00Z"
        )

    def test_now_flag_drives_freshness_boundary(self, tmp_path):
        # Artifact at VALID_GENERATED_AT (2026-04-25T12). With max_age_days=7,
        # now=2026-05-02T12 is exactly the boundary (NOT stale); now=2026-05-02T12:00:01
        # is just past the boundary (stale).
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)

        _run_writer(tmp_path / "ok", path, now="2026-05-02T12:00:00Z", max_age_days=7)
        ok_status = json.loads(
            (tmp_path / "ok" / "router_config_status.json").read_text()
        )
        assert "artifact_too_stale" not in ok_status["blocking_reasons"]

        _run_writer(tmp_path / "stale", path, now="2026-05-02T12:00:01Z", max_age_days=7)
        stale_status = json.loads(
            (tmp_path / "stale" / "router_config_status.json").read_text()
        )
        assert "artifact_too_stale" in stale_status["blocking_reasons"]


# ---------------------------------------------------------------------------
# §10 Regression / import-guard tests
# ---------------------------------------------------------------------------


class TestRegressionImportGuards:
    def test_verification_gate_thresholds_unchanged(self):
        from verification.verification_gate_v2 import VerificationGate

        # Snapshot the constants we care about. If any of these change, Day 5
        # is implicated and a human reviews.
        assert VerificationGate.HIGH_CONFIDENCE_THRESHOLD == 0.7
        assert VerificationGate.MEDIUM_CONFIDENCE_THRESHOLD == 0.4
        assert VerificationGate.MIN_SOURCES_FOR_AUTO_PUSH == 2
        assert VerificationGate.POLICY_VERSION == "v2.1"

    def test_router_threshold_policy_does_not_import_pushdecision(self):
        source = (PROJECT_ROOT / "verification" / "router_threshold_policy.py").read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "PushDecision", (
                        "router_threshold_policy must not import PushDecision"
                    )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "PushDecision" not in alias.name

    def test_workflows_pipeline_does_not_import_router_threshold_policy(self):
        # ``encoding='utf-8-sig'`` strips a UTF-8 BOM that pipeline.py carries.
        source = (PROJECT_ROOT / "workflows" / "pipeline.py").read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "router_threshold_policy" not in mod
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "router_threshold_policy" not in alias.name

    def test_workflows_pipeline_does_not_import_router_threshold_config(self):
        source = (PROJECT_ROOT / "workflows" / "pipeline.py").read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "router_threshold_config" not in mod
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "router_threshold_config" not in alias.name


# ---------------------------------------------------------------------------
# §10 Pydantic model contract tests
# ---------------------------------------------------------------------------


class TestPydanticContract:
    def test_round_trip_blocked(self, tmp_path):
        _run_writer(tmp_path, tmp_path / "missing.json")
        raw = json.loads((tmp_path / "router_config_status.json").read_text())
        model = RouterConfigStatusV1.model_validate(raw)
        assert RouterConfigStatusV1.model_validate(model.model_dump()) == model

    def test_round_trip_ready(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        raw = json.loads((tmp_path / "router_config_status.json").read_text())
        model = RouterConfigStatusV1.model_validate(raw)
        assert model.status == "ready"
        # JSON-mode dump round-trips losslessly.
        json_dump = model.model_dump(mode="json")
        # All values must be JSON-serializable.
        json.dumps(json_dump)
        assert RouterConfigStatusV1.model_validate(json_dump) == model

    def test_round_trip_warn(self, tmp_path):
        artifact = _valid_artifact()
        artifact["bootstrap"]["precision_at_cutoff"]["p5"] = 0.85
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        raw = json.loads((tmp_path / "router_config_status.json").read_text())
        model = RouterConfigStatusV1.model_validate(raw)
        assert model.status == "warn"

    def test_model_dump_json_deterministic(self, tmp_path):
        artifact = _valid_artifact()
        path = _write_artifact(tmp_path, artifact)
        _run_writer(tmp_path, path)
        raw = json.loads((tmp_path / "router_config_status.json").read_text())
        model = RouterConfigStatusV1.model_validate(raw)
        d1 = json.dumps(model.model_dump(mode="json"), sort_keys=True)
        d2 = json.dumps(model.model_dump(mode="json"), sort_keys=True)
        assert d1 == d2

    def test_day4_artifact_validates_on_live_shape_fixture(self):
        Day4CalibrationArtifactV1.model_validate(_valid_artifact())

    def test_removing_required_field_raises(self):
        artifact = _valid_artifact()
        path_dict = {
            "schema_version": "router_config_status.v1",
            "status": "blocked",
            "artifact_reference": {"path": "x"},
            "readiness_scope": "human_review_only",
            "runtime_effective": False,
            "may_route": False,
            "future_router_validation_required": True,
            "policy": {
                "version": POLICY_VERSION,
                "target_precision": 0.9,
                "max_age_days": 7,
                "support_floor_tp_plus_fp": 30,
                "support_floor_tp": 10,
                "support_scope": "input.calibration_label_breakdown",
            },
            # missing evaluated_at
        }
        with pytest.raises(ValidationError):
            RouterConfigStatusV1.model_validate(path_dict)


# ---------------------------------------------------------------------------
# §10 Policy stub tests
# ---------------------------------------------------------------------------


class TestRawSignalPassesThreshold:
    def test_equal_to_threshold_passes(self):
        assert raw_signal_passes_threshold(0.7, 0.7) is True

    def test_just_below_threshold_fails(self):
        assert raw_signal_passes_threshold(0.69, 0.7) is False

    def test_negative_confidence_raises(self):
        with pytest.raises(ValueError):
            raw_signal_passes_threshold(-0.1, 0.5)

    def test_above_one_confidence_raises(self):
        with pytest.raises(ValueError):
            raw_signal_passes_threshold(1.5, 0.5)

    def test_nan_confidence_raises(self):
        with pytest.raises(ValueError):
            raw_signal_passes_threshold(math.nan, 0.5)
