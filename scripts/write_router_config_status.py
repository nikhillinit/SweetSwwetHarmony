"""Phase 2 Day 5 — Guarded router-config status writer.

Reads ``state/conformal_calibration.json`` (Day 4 artifact), evaluates the
Day 5 status policy against it, and writes ``state/router_config_status.json``
for human review. Never reads the DB, never reads env vars (other than the
default for ``--artifact``), and never imports from ``workflows.pipeline``.

Plan of record: ``.omx/plans/phase2-day5-router-config-status-plan.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# Bootstrap project root so this script can import sibling modules when
# invoked directly (mirrors scripts/recalibrate_conformal.py).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from pydantic import ValidationError  # noqa: E402

from verification.router_threshold_config import (  # noqa: E402
    ArtifactReferenceV1,
    CalibrationSummaryV1,
    CandidateRouterThresholdConfigV1,
    DEFAULT_MAX_AGE_DAYS,
    DEFAULT_TARGET_PRECISION,
    DIGEST_FLOAT_PRECISION_DECIMALS,
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
    PolicySnapshotV1,
    RouterConfigStatusV1,
    SEMANTIC_SCORE_BINDING_FIELDS,
    STRATIFIED_THRESHOLD_KEYS_NESTED,
    STRATIFIED_THRESHOLD_KEYS_TOP_LEVEL,
    SUPPORT_FLOOR_TP,
    SUPPORT_FLOOR_TP_PLUS_FP,
)

DEFAULT_ARTIFACT_PATH = Path("state") / "conformal_calibration.json"
DEFAULT_OUT_PATH = Path("state") / "router_config_status.json"
EXIT_OK = 0
EXIT_IO_ERROR = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expected_score_binding_map() -> dict[str, str]:
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


def _truncate(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into a UTC-aware datetime.

    Accepts trailing ``Z`` (Python <=3.10 doesn't, so we normalize it).
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _walk_for_non_finite(value: Any, path: list[str]) -> Optional[str]:
    """Return the dotted path of the first NaN/Infinity float, or None."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return ".".join(path) if path else "<root>"
        return None
    if isinstance(value, dict):
        for k, v in value.items():
            res = _walk_for_non_finite(v, path + [str(k)])
            if res is not None:
                return res
        return None
    if isinstance(value, list):
        for i, v in enumerate(value):
            res = _walk_for_non_finite(v, path + [f"[{i}]"])
            if res is not None:
                return res
        return None
    return None


def _stratification_offending_keys(raw: dict[str, Any]) -> list[str]:
    """Return offending stratification keys present in the raw artifact dict."""
    offending: list[str] = []
    for key in STRATIFIED_THRESHOLD_KEYS_TOP_LEVEL:
        if isinstance(raw, dict) and key in raw:
            offending.append(key)
    for parent, child in STRATIFIED_THRESHOLD_KEYS_NESTED:
        outer = raw.get(parent) if isinstance(raw, dict) else None
        if isinstance(outer, dict) and child in outer:
            offending.append(f"{parent}.{child}")
    return offending


# ---------------------------------------------------------------------------
# Calibration semantic digest
# ---------------------------------------------------------------------------


def _normalize_floats(value: Any) -> Any:
    """Round all finite floats to DIGEST_FLOAT_PRECISION_DECIMALS for digest stability.

    NaN / Infinity are preserved unchanged here; the caller is expected to have
    already rejected them via the non-finite preflight (so allow_nan=False
    serialization later cannot raise in practice).
    """
    if isinstance(value, float):
        if math.isfinite(value):
            return round(value, DIGEST_FLOAT_PRECISION_DECIMALS)
        return value
    if isinstance(value, dict):
        return {k: _normalize_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_floats(v) for v in value]
    return value


def _digest_payload_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Build the digest payload from the RAW artifact dict (post-preflight).

    Excludes ``generated_at``, ``seed``, ``git``. Floats normalized to 6 decimals.
    """
    bootstrap_block = raw.get("bootstrap") or {}
    cv_block = raw.get("cv") or {}
    chosen = raw.get("chosen_cutoff") or {}
    inp = raw.get("input") or {}

    payload: dict[str, Any] = {
        "schema_version": raw.get("schema_version"),
        "artifact_type": raw.get("artifact_type"),
        "score_binding": raw.get("score_binding"),
        "target_precision": raw.get("target_precision"),
        "mode": raw.get("mode"),
        "chosen_cutoff": {
            "value": chosen.get("value"),
            "rule": chosen.get("rule"),
        },
        "input": {
            "calibration_split_sha": inp.get("calibration_split_sha"),
            "train_split_sha": inp.get("train_split_sha"),
            "holdout_split_sha": inp.get("holdout_split_sha"),
        },
        "scoring_provenance": raw.get("scoring_provenance"),
    }

    if "precision_at_cutoff" in bootstrap_block:
        payload["bootstrap_precision_at_cutoff"] = {
            k: bootstrap_block["precision_at_cutoff"].get(k)
            for k in ("mean", "p5", "p50", "p95")
        }
    if "precision_at_cutoff" in cv_block:
        payload["cv_precision_at_cutoff"] = {
            k: cv_block["precision_at_cutoff"].get(k)
            for k in ("mean", "p5", "p50", "p95")
        }

    if "calibration_label_breakdown" in inp:
        payload["input"]["calibration_label_breakdown"] = inp[
            "calibration_label_breakdown"
        ]

    return payload


def compute_calibration_semantic_digest(raw: dict[str, Any]) -> str:
    """SHA256 over canonical JSON of the digest payload (floats rounded to 6dp)."""
    payload = _normalize_floats(_digest_payload_from_raw(raw))
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Calibration summary extractor
# ---------------------------------------------------------------------------


def extract_calibration_summary(
    artifact: Day4CalibrationArtifactV1,
) -> CalibrationSummaryV1:
    """Map Day 4 nested artifact fields to Day 5 flat summary.

    Missing nested blocks resolve to None. This helper never raises on
    missing leaves; reason codes (no_cutoff_available, label_support_unavailable)
    are emitted by the writer based on accumulator order, not by this helper.
    """
    bootstrap = artifact.bootstrap or {}
    boot_pac = bootstrap.get("precision_at_cutoff") if isinstance(bootstrap, dict) else None
    boot_pac = boot_pac if isinstance(boot_pac, dict) else {}

    breakdown = artifact.input.get("calibration_label_breakdown") or {}
    if not isinstance(breakdown, dict):
        breakdown = {}

    chosen = artifact.chosen_cutoff if isinstance(artifact.chosen_cutoff, dict) else {}

    def _intnone(x: Any) -> Optional[int]:
        if x is None:
            return None
        if isinstance(x, bool):
            return None
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    def _floatnone(x: Any) -> Optional[float]:
        if x is None:
            return None
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        return v

    return CalibrationSummaryV1(
        mode=artifact.mode,
        target_precision=artifact.target_precision,
        chosen_cutoff_value=_floatnone(chosen.get("value")),
        bootstrap_p50=_floatnone(boot_pac.get("p50")),
        bootstrap_p5=_floatnone(boot_pac.get("p5")),
        label_breakdown_tp=_intnone(breakdown.get("TP")) if "TP" in breakdown else None,
        label_breakdown_fp=_intnone(breakdown.get("FP")) if "FP" in breakdown else None,
        label_breakdown_unsure=_intnone(breakdown.get("UNSURE"))
        if "UNSURE" in breakdown
        else None,
        label_breakdown_missing=_intnone(breakdown.get("missing"))
        if "missing" in breakdown
        else None,
    )


# ---------------------------------------------------------------------------
# Status accumulator
# ---------------------------------------------------------------------------


def _build_artifact_reference(
    artifact_path: Path,
    raw: Optional[dict[str, Any]],
    sha256: Optional[str],
) -> ArtifactReferenceV1:
    return ArtifactReferenceV1(
        path=str(artifact_path),
        sha256=sha256,
        generated_at=raw.get("generated_at") if isinstance(raw, dict) else None,
        artifact_type=raw.get("artifact_type") if isinstance(raw, dict) else None,
    )


def _policy_snapshot(target_precision: float, max_age_days: int) -> PolicySnapshotV1:
    return PolicySnapshotV1(
        version=POLICY_VERSION,
        target_precision=target_precision,
        max_age_days=max_age_days,
        support_floor_tp_plus_fp=SUPPORT_FLOOR_TP_PLUS_FP,
        support_floor_tp=SUPPORT_FLOOR_TP,
        support_scope="input.calibration_label_breakdown",
    )


def _empty_status_skeleton(
    *,
    artifact_path: Path,
    raw: Optional[dict[str, Any]],
    sha256: Optional[str],
    target_precision: float,
    max_age_days: int,
    evaluated_at: str,
    digest: Optional[str] = None,
) -> dict[str, Any]:
    """Build the dict skeleton for RouterConfigStatusV1 (filled later)."""
    return {
        "schema_version": "router_config_status.v1",
        "status": "blocked",
        "blocking_reasons": [],
        "warning_reasons": [],
        "reason_payloads": {},
        "artifact_reference": _build_artifact_reference(
            artifact_path, raw, sha256
        ).model_dump(mode="json"),
        "calibration_semantic_digest": digest,
        "candidate_router_threshold_config": None,
        "readiness_scope": "human_review_only",
        "runtime_effective": False,
        "may_route": False,
        "future_router_validation_required": True,
        "policy": _policy_snapshot(target_precision, max_age_days).model_dump(
            mode="json"
        ),
        "evaluated_at": evaluated_at,
    }


def _resolve_status(blocking: list[str], warning: list[str]) -> str:
    if blocking:
        return "blocked"
    if warning:
        return "warn"
    return "ready"


def evaluate(
    *,
    artifact_path: Path,
    target_precision: float,
    max_age_days: int,
    now: datetime,
) -> dict[str, Any]:
    """Run the accumulator and return a fully-populated status dict."""
    evaluated_at = now.isoformat().replace("+00:00", "Z")

    # Step 1: artifact exists?
    if not artifact_path.exists():
        skeleton = _empty_status_skeleton(
            artifact_path=artifact_path,
            raw=None,
            sha256=None,
            target_precision=target_precision,
            max_age_days=max_age_days,
            evaluated_at=evaluated_at,
        )
        skeleton["blocking_reasons"] = ["artifact_missing"]
        skeleton["reason_payloads"] = {
            "artifact_missing": {"path": str(artifact_path)}
        }
        skeleton["status"] = "blocked"
        return skeleton

    raw_bytes = artifact_path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # Step 2: JSON parse?
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        skeleton = _empty_status_skeleton(
            artifact_path=artifact_path,
            raw=None,
            sha256=sha256,
            target_precision=target_precision,
            max_age_days=max_age_days,
            evaluated_at=evaluated_at,
        )
        skeleton["blocking_reasons"] = ["invalid_artifact_schema"]
        skeleton["reason_payloads"] = {
            "invalid_artifact_schema": {
                "error_kind": "json_parse",
                "detail": _truncate(str(e)),
            }
        }
        skeleton["status"] = "blocked"
        return skeleton

    if not isinstance(raw, dict):
        skeleton = _empty_status_skeleton(
            artifact_path=artifact_path,
            raw=None,
            sha256=sha256,
            target_precision=target_precision,
            max_age_days=max_age_days,
            evaluated_at=evaluated_at,
        )
        skeleton["blocking_reasons"] = ["invalid_artifact_schema"]
        skeleton["reason_payloads"] = {
            "invalid_artifact_schema": {
                "error_kind": "json_parse",
                "detail": "top-level value is not a JSON object",
            }
        }
        skeleton["status"] = "blocked"
        return skeleton

    # Step 3a: stratified-threshold preflight (does NOT hard-stop on its own)
    blocking: list[str] = []
    warning: list[str] = []
    payloads: dict[str, dict[str, Any]] = {}
    offending = _stratification_offending_keys(raw)
    if offending:
        blocking.append("unsupported_stratified_thresholds")
        payloads["unsupported_stratified_thresholds"] = {
            "offending_keys": offending
        }

    # Step 3b: non-finite floats? (hard stop)
    nf_path = _walk_for_non_finite(raw, [])
    if nf_path is not None:
        skeleton = _empty_status_skeleton(
            artifact_path=artifact_path,
            raw=raw,
            sha256=sha256,
            target_precision=target_precision,
            max_age_days=max_age_days,
            evaluated_at=evaluated_at,
        )
        # Preserve any earlier reasons accumulated in step 3a.
        all_blocking = blocking + ["invalid_artifact_schema"]
        all_payloads = dict(payloads)
        all_payloads["invalid_artifact_schema"] = {
            "error_kind": "non_finite_float",
            "detail": _truncate(f"non-finite float at {nf_path}"),
        }
        skeleton["blocking_reasons"] = all_blocking
        skeleton["reason_payloads"] = all_payloads
        skeleton["status"] = "blocked"
        # Digest must NOT be computed when non-finite floats are present.
        return skeleton

    # Step 4: Pydantic validation. Compute the digest from raw FIRST (so the
    # blocked output still carries forensic identity if Pydantic then fails).
    try:
        digest = compute_calibration_semantic_digest(raw)
    except (TypeError, ValueError):
        digest = None

    try:
        artifact = Day4CalibrationArtifactV1.model_validate(raw)
    except ValidationError as e:
        skeleton = _empty_status_skeleton(
            artifact_path=artifact_path,
            raw=raw,
            sha256=sha256,
            target_precision=target_precision,
            max_age_days=max_age_days,
            evaluated_at=evaluated_at,
            digest=digest if "score_binding" in raw else None,
        )
        all_blocking = blocking + ["invalid_artifact_schema"]
        all_payloads = dict(payloads)
        all_payloads["invalid_artifact_schema"] = {
            "error_kind": "pydantic",
            "detail": _truncate(str(e)),
        }
        skeleton["blocking_reasons"] = all_blocking
        skeleton["reason_payloads"] = all_payloads
        skeleton["status"] = "blocked"
        return skeleton

    # Step 5: artifact is structurally valid. Accumulate remaining reasons.
    expected_sb = _expected_score_binding_map()
    sb = artifact.score_binding.model_dump()

    # 5a: wrong_score_binding_semantics
    semantic_mismatches: dict[str, dict[str, str]] = {}
    for field in SEMANTIC_SCORE_BINDING_FIELDS:
        if sb.get(field) != expected_sb[field]:
            semantic_mismatches[field] = {
                "expected": expected_sb[field],
                "observed": sb.get(field),
            }
    if semantic_mismatches:
        blocking.append("wrong_score_binding_semantics")
        payloads["wrong_score_binding_semantics"] = semantic_mismatches

    # 5b: score_binding_metadata_drift
    metadata_mismatches: dict[str, dict[str, str]] = {}
    for field in METADATA_SCORE_BINDING_FIELDS:
        if sb.get(field) != expected_sb[field]:
            metadata_mismatches[field] = {
                "expected": expected_sb[field],
                "observed": sb.get(field),
            }
    if metadata_mismatches:
        warning.append("score_binding_metadata_drift")
        payloads["score_binding_metadata_drift"] = metadata_mismatches

    # 5c: unsupported_calibration_mode
    if artifact.mode == "cv":
        blocking.append("unsupported_calibration_mode")
        payloads["unsupported_calibration_mode"] = {"observed_mode": "cv"}

    # 5d: no_cutoff_available
    chosen = artifact.chosen_cutoff if isinstance(artifact.chosen_cutoff, dict) else {}
    chosen_value = chosen.get("value")
    if chosen_value is None:
        blocking.append("no_cutoff_available")
        payloads["no_cutoff_available"] = {"observed_value": None}

    # 5e: artifact_too_stale
    try:
        gen_at = _parse_iso_utc(artifact.generated_at)
        if now > gen_at + timedelta(days=max_age_days):
            blocking.append("artifact_too_stale")
            payloads["artifact_too_stale"] = {
                "artifact_generated_at": artifact.generated_at,
                "evaluated_at": evaluated_at,
                "max_age_days": max_age_days,
            }
    except (ValueError, TypeError):
        # Malformed generated_at would already have failed Pydantic parsing
        # of the top-level model; we treat unparseable here as not-stale to
        # avoid double-counting.
        pass

    # 5f / 5g: label support (only for bootstrap mode — cv path was already
    # blocked by 5c, but still skip to avoid double-counting).
    breakdown_raw = artifact.input.get("calibration_label_breakdown")
    breakdown_dict = breakdown_raw if isinstance(breakdown_raw, dict) else None
    if artifact.mode == "bootstrap":
        if not breakdown_dict:
            blocking.append("label_support_unavailable")
            payloads["label_support_unavailable"] = {
                "observed_breakdown": breakdown_dict if breakdown_dict is not None else {}
            }
        else:
            tp = breakdown_dict.get("TP", 0) or 0
            fp = breakdown_dict.get("FP", 0) or 0
            try:
                tp_i = int(tp)
                fp_i = int(fp)
            except (TypeError, ValueError):
                tp_i, fp_i = 0, 0
            if tp_i < SUPPORT_FLOOR_TP or (tp_i + fp_i) < SUPPORT_FLOOR_TP_PLUS_FP:
                blocking.append("insufficient_label_support")
                payloads["insufficient_label_support"] = {
                    "observed_tp": tp_i,
                    "observed_fp": fp_i,
                    "floor_tp": SUPPORT_FLOOR_TP,
                    "floor_tp_plus_fp": SUPPORT_FLOOR_TP_PLUS_FP,
                }

    # 5h: precision_below_target / precision_band_below_target
    bootstrap = artifact.bootstrap if isinstance(artifact.bootstrap, dict) else {}
    pac = bootstrap.get("precision_at_cutoff") if isinstance(bootstrap, dict) else None
    pac = pac if isinstance(pac, dict) else {}
    p50 = pac.get("p50")
    p5 = pac.get("p5")
    if isinstance(p50, (int, float)) and not isinstance(p50, bool):
        if p50 < target_precision:
            blocking.append("precision_below_target")
            payloads["precision_below_target"] = {
                "observed_p50": p50,
                "target_precision": target_precision,
            }
        elif (
            isinstance(p5, (int, float))
            and not isinstance(p5, bool)
            and p5 < target_precision
        ):
            warning.append("precision_band_below_target")
            payloads["precision_band_below_target"] = {
                "observed_p50": p50,
                "observed_p5": p5,
                "target_precision": target_precision,
            }

    # 5i: calibration_target_mismatch
    if artifact.target_precision != target_precision:
        warning.append("calibration_target_mismatch")
        payloads["calibration_target_mismatch"] = {
            "artifact_target_precision": artifact.target_precision,
            "policy_target_precision": target_precision,
        }

    # Build candidate iff not blocked.
    status = _resolve_status(blocking, warning)
    candidate: Optional[dict[str, Any]] = None
    if status != "blocked" and chosen_value is not None and digest is not None:
        try:
            summary = extract_calibration_summary(artifact)
            candidate_model = CandidateRouterThresholdConfigV1(
                schema_version="candidate_router_threshold_config.v1",
                activation="manual_review_required",
                production_routing_enabled=False,
                threshold_value=float(chosen_value),
                score_binding=artifact.score_binding,
                calibration_semantic_digest=digest,
                calibration_summary=summary,
                artifact_reference=_build_artifact_reference(
                    artifact_path, raw, sha256
                ),
            )
            candidate = candidate_model.model_dump(mode="json")
        except (ValidationError, TypeError, ValueError):
            # Construction of the candidate must not crash the writer; fall
            # back to blocked with a synthetic invalid_artifact_schema reason.
            blocking.append("invalid_artifact_schema")
            payloads.setdefault(
                "invalid_artifact_schema",
                {
                    "error_kind": "pydantic",
                    "detail": "candidate construction failed",
                },
            )
            status = "blocked"
            candidate = None

    skeleton = _empty_status_skeleton(
        artifact_path=artifact_path,
        raw=raw,
        sha256=sha256,
        target_precision=target_precision,
        max_age_days=max_age_days,
        evaluated_at=evaluated_at,
        digest=digest,
    )
    skeleton["blocking_reasons"] = blocking
    skeleton["warning_reasons"] = warning
    skeleton["reason_payloads"] = payloads
    skeleton["candidate_router_threshold_config"] = candidate
    skeleton["status"] = status
    return skeleton


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _atomic_write_json(out_path: Path, payload: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except (AttributeError, OSError):
                pass
        os.replace(tmp_path, out_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="write-router-config-status",
        description=(
            "Phase 2 Day 5 — emit state/router_config_status.json from "
            "the Day 4 conformal_calibration.json artifact for human review. "
            "Shadow-only; never routes traffic."
        ),
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=DEFAULT_ARTIFACT_PATH,
        help="Path to Day 4 artifact (default: state/conformal_calibration.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_PATH,
        help="Path to write the status file (default: state/router_config_status.json)",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Maximum artifact age in days before status is blocked as stale (default: 7)",
    )
    parser.add_argument(
        "--target-precision",
        type=float,
        default=DEFAULT_TARGET_PRECISION,
        help="Day 5 policy precision target (default: 0.90)",
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="ISO-8601 UTC timestamp to use for evaluated_at and freshness boundary",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if args.now is not None:
        now = _parse_iso_utc(args.now)
    else:
        now = datetime.now(timezone.utc)

    payload = evaluate(
        artifact_path=args.artifact,
        target_precision=args.target_precision,
        max_age_days=args.max_age_days,
        now=now,
    )

    # Validate the payload against our own contract before writing.
    RouterConfigStatusV1.model_validate(payload)

    try:
        _atomic_write_json(args.out, payload)
    except OSError as e:
        print(f"ERROR: failed to write {args.out}: {e}", file=sys.stderr)
        return EXIT_IO_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
