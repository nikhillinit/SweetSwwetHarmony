"""Day 4 — Conformal recalibration script (read-only).

Fits a single overall conformal cut-off on ``state/calibration_ids.json`` with
bootstrap confidence intervals (default), or via repeated K-fold stratified
cross-validation on ``state/train_ids.json`` only (``--fallback-cv`` /
``--mode cv``). The holdout split (``state/holdout_ids.json``) is loaded only
to verify input/holdout disjointness and never used for fitting.

Plan of record: ``.omx/plans/phase2-day4-calibration-plan.md``.

The script is strictly read-only: it never modifies ``signals.db``,
``state/{train,calibration,holdout}_ids.json``,
``state/evaluation_splits_summary.json``, or ``state/collectors.json``. The
output artifact at ``state/conformal_calibration.json`` is gitignored.

Exit codes:

* ``0`` — success (artifact written, or ``--dry-run`` summary printed).
* ``1`` — input or contract failure (split files missing/malformed; calibration
  size below ``--min-calibration-size`` floor; holdout disjointness violated;
  required CLI args missing; output path unwritable; infeasible base set).
* ``2`` — schema-probe failure.
* ``3`` — instability gate exceeded (only with ``--strict-instability``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

# Bootstrap project root so this script can import sibling scripts when run
# directly (mirrors scripts/create_evaluation_splits.py).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Plan section "Score binding": the cut-off applies to signals.confidence
# (REAL NOT NULL, storage/signal_store.py:100). The alternative column
# referenced in live_schema_contract.json's forbidden_references block is
# deliberately NOT used here; the literal forbidden string never appears in
# this file (lint enforced by tests/scripts/test_recalibrate_conformal.py).
SCORE_TABLE = "signals"
SCORE_COLUMN = "confidence"
SCORE_SEMANTIC_NAME = "signal_stored_confidence"
SCORE_DIRECTION = "higher_is_more_confident"
DECISION_RULE = "accept_if_score_gte_threshold"

# Score producer + version policy (added in review): historical
# signals.confidence values may have been written by multiple scoring-logic
# versions over the lifetime of the DB. The artifact records that fact
# explicitly so Day 5+ consumers can refuse to apply this cut-off when the
# active scoring path has changed since calibration.
SCORE_PRODUCER = "signal_generation_pipeline"
SCORE_VERSION = "mixed_or_unknown"
SCORE_VERSION_POLICY = (
    "signals.confidence in the calibration set may span multiple scoring-"
    "logic versions; this artifact treats the column as a single "
    "distribution. Day 5+ consumers MUST refuse to apply this cut-off if "
    "the active scoring logic has changed since calibration (compare via "
    "the active_thesis_prompt_version + git.commit fields)."
)

# Plan section "Score-label join": signal_quality_metrics.human_label is the
# canonical labels table for Phase 2 (NOT quality_feedback.label).
LABEL_TABLE = "signal_quality_metrics"
LABEL_COLUMN = "human_label"

# Frozen at 0.7 in workflows/pipeline.py:2410 (non-goal: do not modify it).
HIGH_CONFIDENCE_THRESHOLD_AT_RUN_TIME = 0.7

DEFAULT_DB_PATH = "signals.db"
DEFAULT_CONTRACT_PATH = Path(".omx") / "wave6" / "live_schema_contract.json"
DEFAULT_STATE_DIR = Path("state")
DEFAULT_CALIBRATION_FILE = DEFAULT_STATE_DIR / "calibration_ids.json"
DEFAULT_TRAIN_FILE = DEFAULT_STATE_DIR / "train_ids.json"
DEFAULT_HOLDOUT_FILE = DEFAULT_STATE_DIR / "holdout_ids.json"
DEFAULT_SUMMARY_FILE = DEFAULT_STATE_DIR / "evaluation_splits_summary.json"
DEFAULT_OUT_PATH = DEFAULT_STATE_DIR / "conformal_calibration.json"

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_SCHEMA_FAILED = 2
EXIT_INSTABILITY_GATE = 3

# Labels included in the fit. Everything else (UNSURE, ADJ, None / missing
# signal_quality_metrics row) is excluded from the precision calculation but
# still counted in input.calibration_label_breakdown for provenance.
_FITTING_LABELS = frozenset({"TP", "FP"})


class HoldoutLeakError(Exception):
    """Raised when input signal_ids overlap holdout signal_ids."""


def fit_single_cutoff(
    scores: Sequence[float],
    labels: Sequence[Optional[str]],
    target_precision: float,
) -> Optional[float]:
    """Return the lowest score threshold whose precision >= target_precision.

    Threshold recipe (see plan section "Threshold recipe"):

    * Label policy: ``TP`` is positive, ``FP`` is negative. ``UNSURE``,
      ``ADJ``, and ``None`` (missing label) are excluded from the precision
      calculation. The recipe is hard-coded to
      ``accept_if_score_gte_threshold`` (higher is more confident).
    * Selection rule: scan all unique observed scores in ascending order.
      For each candidate threshold ``t`` compute
      ``precision(t) = TP@(score>=t) / (TP@(score>=t) + FP@(score>=t))``.
      Choose the lowest ``t`` whose ``precision(t) >= target_precision``.
      Lowest-meets-target maximizes recall while honoring the precision
      floor — the standard split-conformal calibration recipe.
    * Returns ``None`` when no threshold reaches ``target_precision``
      (caller distinguishes base-set infeasibility from resample
      infeasibility — see ``run_bootstrap_mode`` and ``run_cv_mode``).
    """
    if len(scores) != len(labels):
        raise ValueError(
            f"scores and labels must be the same length: "
            f"{len(scores)} vs {len(labels)}"
        )
    if not (0.0 < target_precision < 1.0):
        raise ValueError(
            f"target_precision must be in the open interval (0, 1); "
            f"got {target_precision!r}"
        )

    # Filter to rows the recipe consumes. Excluded labels are dropped here;
    # provenance counts (UNSURE / ADJ / missing) are recorded by the caller.
    eligible: list[tuple[float, str]] = [
        (float(s), lab)
        for s, lab in zip(scores, labels)
        if lab in _FITTING_LABELS
    ]
    if not eligible:
        return None

    # Scan unique observed scores in ascending order. Lowest threshold meeting
    # target_precision wins; the loop returns on first match.
    candidates = sorted({score for score, _ in eligible})
    for t in candidates:
        tp_above = 0
        fp_above = 0
        for score, lab in eligible:
            if score >= t:
                if lab == "TP":
                    tp_above += 1
                else:  # lab == "FP"
                    fp_above += 1
        denom = tp_above + fp_above
        if denom == 0 or tp_above == 0:
            continue
        precision = tp_above / denom
        if precision >= target_precision:
            return float(t)
    return None


def percentile_band(values: Sequence[float]) -> dict[str, float]:
    """Summarize a distribution as mean / p5 / p50 / p95 / stdev / cv.

    Stdev is the sample (unbiased, ``ddof=1``) standard deviation; CV is the
    sample stdev divided by the mean. With a single value, both reduce to
    ``0.0``. All return values are native ``float`` so the artifact JSON is
    free of numpy dtypes.
    """
    if len(values) == 0:
        raise ValueError("percentile_band requires at least one value")
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    p5 = float(np.percentile(arr, 5))
    p50 = float(np.percentile(arr, 50))
    p95 = float(np.percentile(arr, 95))
    if arr.size <= 1:
        stdev = 0.0
        cv = 0.0
    else:
        stdev = float(arr.std(ddof=1))
        cv = stdev / mean if mean != 0 else 0.0
    return {
        "mean": mean,
        "p5": p5,
        "p50": p50,
        "p95": p95,
        "stdev": stdev,
        "cv": cv,
    }


def coefficient_of_variation(values: Sequence[float]) -> float:
    """Return the sample coefficient of variation (``stdev / mean``).

    Uses sample (unbiased, ``ddof=1``) standard deviation. Raises
    ``ValueError`` for an empty input or zero mean — both make CV undefined.
    Constant non-zero values yield ``0.0``.
    """
    if len(values) == 0:
        raise ValueError("coefficient_of_variation requires at least one value")
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if mean == 0:
        raise ValueError(
            "coefficient_of_variation is undefined for zero-mean inputs"
        )
    if arr.size <= 1:
        return 0.0
    stdev = float(arr.std(ddof=1))
    return stdev / mean


def canonical_split_sha(signal_ids: Iterable[object]) -> str:
    """Hash a set of signal_ids deterministically.

    Matches the Day 3 holdout SHA scheme: coerce to ``str``, dedupe, sort
    lexicographically, serialize as ``json.dumps(..., separators=(",", ":"),
    ensure_ascii=False)``, then ``sha256`` hex.

    Order-independent and dedupe-independent — the same set of underlying
    IDs always produces the same SHA, so the artifact's ``*_split_sha`` is
    a stable handle for downstream consumers.
    """
    string_ids = sorted({str(sid) for sid in signal_ids})
    canonical = json.dumps(
        string_ids, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_instability_warning(
    cv: float, warn_threshold: float
) -> Optional[str]:
    """Return a human-facing warning string when ``cv > warn_threshold``.

    Returns ``None`` when the CV is at or below the warn threshold (no
    warning at the exact boundary, to keep noise out of the dashboard). The
    warning text includes both the observed CV and the threshold so the
    dashboard reader sees the exact magnitude of the instability.
    """
    if cv > warn_threshold:
        return (
            f"Bootstrap CV {cv:.3f} exceeds warn threshold "
            f"{warn_threshold:.2f} — calibration cut-off is unstable; "
            f"interpret the percentile band, not just the median."
        )
    return None


def assert_holdout_disjoint(
    input_ids: Iterable[object],
    holdout_ids: Iterable[object],
) -> None:
    """Raise :class:`HoldoutLeakError` when ``input_ids`` overlaps holdout.

    Constraint 5 of the Day 4 plan: the calibration script must never let
    holdout signal_ids reach a fitting code path. This validator runs in
    pre-flight, before any DB read, and the error message reports only the
    overlap *count* — never the IDs themselves (matches the Day 3 holdout
    protection contract).
    """
    input_set = {str(sid) for sid in input_ids}
    holdout_set = {str(sid) for sid in holdout_ids}
    overlap = input_set & holdout_set
    if overlap:
        raise HoldoutLeakError(
            f"input set overlaps holdout by {len(overlap)} signal_ids; "
            f"refusing to proceed (constraint 5 — holdout protection)"
        )


def validate_split_file_consistency(
    *,
    summary: Mapping[str, object],
    train: Mapping[str, object],
    calibration: Mapping[str, object],
    holdout: Mapping[str, object],
) -> None:
    """Assert summary + per-split files agree on seed, generated_at, fractions.

    Mismatch indicates the splits were regenerated separately (e.g., one file
    was committed while the others were left stale). The artifact's three
    ``*_split_sha`` fields would then be inconsistent provenance, so the
    script refuses to continue. Caller maps the raised ``ValueError`` to
    exit code 1.
    """
    seeds = {
        "summary": summary.get("seed"),
        "train": train.get("seed"),
        "calibration": calibration.get("seed"),
        "holdout": holdout.get("seed"),
    }
    if len({s for s in seeds.values()}) != 1:
        raise ValueError(
            f"split-file seed mismatch across summary/train/calibration/holdout: "
            f"{seeds}; re-run scripts/create_evaluation_splits.py"
        )
    timestamps = {
        "summary": summary.get("generated_at"),
        "train": train.get("generated_at"),
        "calibration": calibration.get("generated_at"),
        "holdout": holdout.get("generated_at"),
    }
    if len({t for t in timestamps.values()}) != 1:
        raise ValueError(
            f"split-file generated_at mismatch: {timestamps}; "
            f"re-run scripts/create_evaluation_splits.py"
        )
    fractions = {
        "summary": summary.get("fractions"),
        "train": train.get("fractions"),
        "calibration": calibration.get("fractions"),
        "holdout": holdout.get("fractions"),
    }
    # Compare via canonical JSON because dict equality on nested floats is
    # fragile but json.dumps(sort_keys=True) is byte-stable.
    canonical_fractions = {
        name: json.dumps(f, sort_keys=True) if isinstance(f, dict) else None
        for name, f in fractions.items()
    }
    if len(set(canonical_fractions.values())) != 1:
        raise ValueError(
            f"split-file fractions mismatch: {fractions}; "
            f"re-run scripts/create_evaluation_splits.py"
        )


def _precision_at_threshold(
    scores: Sequence[float],
    labels: Sequence[Optional[str]],
    threshold: float,
) -> float:
    """Compute precision over TP/FP rows at ``threshold``.

    Used by ``bootstrap_cutoff`` and ``cv_cutoff`` to record
    ``precision_at_cutoff`` for each fitted threshold. UNSURE / ADJ /
    missing labels are excluded, matching ``fit_single_cutoff``.
    """
    tp_above = 0
    fp_above = 0
    for s, lab in zip(scores, labels):
        if s >= threshold:
            if lab == "TP":
                tp_above += 1
            elif lab == "FP":
                fp_above += 1
    denom = tp_above + fp_above
    if denom == 0:
        return 0.0
    return tp_above / denom


def bootstrap_cutoff(
    scores: Sequence[float],
    labels: Sequence[Optional[str]],
    *,
    target_precision: float,
    iterations: int,
    seed: int,
) -> dict[str, object]:
    """Bootstrap the calibration cut-off distribution.

    Draws ``iterations`` resamples (with replacement) from
    ``(scores, labels)`` using a seeded ``numpy.random.default_rng``. For each
    resample, fits a single cut-off via :func:`fit_single_cutoff` and records
    the in-sample precision at that cut-off. Resamples where no threshold
    reaches ``target_precision`` are counted under ``infeasible_iterations``
    and skipped from the percentile band.

    Caller is responsible for the feasibility floor check (10% of
    ``iterations``); this function returns the raw distribution.
    """
    if len(scores) == 0:
        raise ValueError("bootstrap_cutoff requires at least one row")
    if len(scores) != len(labels):
        raise ValueError(
            "scores and labels must be the same length: "
            f"{len(scores)} vs {len(labels)}"
        )
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1; got {iterations}")

    rng = np.random.default_rng(seed)
    n = len(scores)
    cutoffs: list[float] = []
    precisions: list[float] = []
    infeasible = 0
    for _ in range(iterations):
        idx = rng.integers(0, n, size=n)
        s_sample = [float(scores[i]) for i in idx]
        l_sample = [labels[i] for i in idx]
        cut = fit_single_cutoff(
            s_sample, l_sample, target_precision=target_precision
        )
        if cut is None:
            infeasible += 1
            continue
        cutoffs.append(cut)
        precisions.append(_precision_at_threshold(s_sample, l_sample, cut))
    return {
        "iterations": iterations,
        "cutoffs": percentile_band(cutoffs) if cutoffs else None,
        "precision_at_cutoff": (
            percentile_band(precisions) if precisions else None
        ),
        "infeasible_iterations": infeasible,
    }


def cv_cutoff(
    scores: Sequence[float],
    labels: Sequence[Optional[str]],
    *,
    target_precision: float,
    folds: int,
    repeats: int,
    seed: int,
) -> dict[str, object]:
    """Repeated stratified K-fold cross-validation of the calibration cut-off.

    Filters input to TP/FP rows (UNSURE/ADJ/missing are excluded, matching
    ``fit_single_cutoff``), then runs
    ``sklearn.model_selection.RepeatedStratifiedKFold`` with
    ``random_state=seed``. Each (train_idx, val_idx) split fits a single
    cut-off on the training portion via :func:`fit_single_cutoff`. The
    held-out fold is not consumed; we record only the in-sample precision at
    the fitted cut-off. Folds where no threshold reaches ``target_precision``
    are counted under ``infeasible_fits``.

    ``fits_completed = folds * repeats`` is recorded explicitly so callers
    do not have to multiply.
    """
    from sklearn.model_selection import RepeatedStratifiedKFold

    if len(scores) != len(labels):
        raise ValueError(
            "scores and labels must be the same length: "
            f"{len(scores)} vs {len(labels)}"
        )

    eligible_pairs = [
        (float(s), lab)
        for s, lab in zip(scores, labels)
        if lab in _FITTING_LABELS
    ]
    if not eligible_pairs:
        raise ValueError(
            "cv_cutoff requires at least one TP/FP row in the input"
        )
    s_arr = np.array([p[0] for p in eligible_pairs], dtype=float)
    l_arr = np.array([p[1] for p in eligible_pairs])

    skf = RepeatedStratifiedKFold(
        n_splits=folds, n_repeats=repeats, random_state=seed
    )
    cutoffs: list[float] = []
    precisions: list[float] = []
    infeasible = 0
    for train_idx, _val_idx in skf.split(s_arr, l_arr):
        s_train = s_arr[train_idx].tolist()
        l_train = l_arr[train_idx].tolist()
        cut = fit_single_cutoff(
            s_train, l_train, target_precision=target_precision
        )
        if cut is None:
            infeasible += 1
            continue
        cutoffs.append(cut)
        precisions.append(_precision_at_threshold(s_train, l_train, cut))
    return {
        "folds": folds,
        "repeats": repeats,
        "fits_completed": folds * repeats,
        "cutoffs": percentile_band(cutoffs) if cutoffs else None,
        "precision_at_cutoff": (
            percentile_band(precisions) if precisions else None
        ),
        "infeasible_fits": infeasible,
    }


def validate_min_calibration_size(*, rows: int, floor: int) -> None:
    """Assert calibration row count meets ``floor`` (default 4).

    Bootstrap on 3 rows is meaningless: every resample collapses to the same
    multiset and the percentile band degenerates. Caller maps the raised
    ``ValueError`` to exit code 1, surfacing the recipe pointer to operators.
    """
    if rows < floor:
        raise ValueError(
            f"calibration set has {rows} rows; minimum is {floor}. "
            f"Re-run scripts/create_evaluation_splits.py after applying "
            f"more labels (--min-calibration-size to override)."
        )


# ---------------------------------------------------------------------------
# CLI glue: split-file loading, DB read, artifact build, atomic write.
# ---------------------------------------------------------------------------


def _load_split_file(path: Path) -> dict[str, Any]:
    """Load a state/*_ids.json or evaluation_splits_summary.json file."""
    if not path.exists():
        raise FileNotFoundError(
            f"split file missing: {path} — run "
            f"scripts/create_evaluation_splits.py --seed 42"
        )
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"split file {path} must be a JSON object")
    return data


def _read_scores_and_labels(
    db_path: Path,
    signal_ids: Sequence[int],
) -> tuple[list[float], list[Optional[str]], dict[str, int]]:
    """Read score + label rows for ``signal_ids`` (read-only DB).

    Single LEFT JOIN query, parameterized with the mode-relevant signal_id
    list. ``LEFT JOIN`` (not inner) so missing labels (no
    ``signal_quality_metrics`` row) return ``label = NULL`` and are excluded
    from fitting per the threshold recipe — but counted under
    ``input.calibration_label_breakdown.missing`` for provenance.

    Returns ``(scores, labels, label_breakdown)`` ordered by ``signal_id``.
    """
    if not signal_ids:
        return [], [], {"TP": 0, "FP": 0, "UNSURE": 0, "ADJ": 0, "missing": 0}

    placeholders = ",".join("?" for _ in signal_ids)
    query = (
        f"SELECT s.id AS signal_id, "
        f"       s.{SCORE_COLUMN} AS score, "
        f"       sqm.{LABEL_COLUMN} AS label "
        f"FROM {SCORE_TABLE} s "
        f"LEFT JOIN {LABEL_TABLE} sqm "
        f"  ON sqm.signal_id = s.id "
        f"WHERE s.id IN ({placeholders}) "
        f"ORDER BY s.id"
    )
    con = sqlite3.connect(
        f"file:{Path(db_path).resolve()}?mode=ro", uri=True
    )
    try:
        rows = con.execute(query, list(signal_ids)).fetchall()
    finally:
        con.close()

    if len(rows) != len(set(signal_ids)):
        raise ValueError(
            f"signal_id count mismatch: split file references "
            f"{len(set(signal_ids))} unique IDs but DB returned "
            f"{len(rows)} rows. The DB has been reset since splits were "
            f"generated; re-run scripts/create_evaluation_splits.py."
        )

    scores: list[float] = []
    labels: list[Optional[str]] = []
    breakdown = {"TP": 0, "FP": 0, "UNSURE": 0, "ADJ": 0, "missing": 0}
    for _sid, score, label in rows:
        if score is None:
            raise ValueError(
                f"NULL score in {SCORE_TABLE}.{SCORE_COLUMN}; the recipe "
                f"assumes the column is a probability in [0, 1]."
            )
        score_f = float(score)
        if not (0.0 <= score_f <= 1.0):
            raise ValueError(
                f"score {score_f} outside [0, 1] in {SCORE_TABLE}."
                f"{SCORE_COLUMN}; the recipe assumes a probability."
            )
        scores.append(score_f)
        labels.append(label)
        if label is None:
            breakdown["missing"] += 1
        elif label in breakdown:
            breakdown[label] += 1
        else:
            # Unknown label — record under missing for safety; fit_single_cutoff
            # will exclude it.
            breakdown["missing"] += 1
    return scores, labels, breakdown


def _capture_git_provenance() -> dict[str, Optional[object]]:
    """Best-effort git provenance — null on any failure (e.g., shallow checkout)."""
    def _run(args: list[str]) -> Optional[str]:
        try:
            out = subprocess.run(
                args,
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if out.returncode == 0:
                return out.stdout.strip() or None
        except Exception:
            pass
        return None

    commit = _run(["git", "rev-parse", "HEAD"])
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    status = _run(["git", "status", "--porcelain"])
    dirty = bool(status) if status is not None else None
    return {"commit": commit, "branch": branch, "dirty": dirty}


def _capture_active_thesis_prompt_version(
    db_path: Path, signal_ids: Sequence[int]
) -> Optional[str]:
    """Read the most recent thesis_classifications.prompt_version for any
    signal in the input split. Best-effort: returns None when the table or
    column is absent, or when no rows match.
    """
    if not signal_ids:
        return None
    try:
        con = sqlite3.connect(
            f"file:{Path(db_path).resolve()}?mode=ro", uri=True
        )
    except sqlite3.Error:
        return None
    try:
        try:
            placeholders = ",".join("?" for _ in signal_ids)
            row = con.execute(
                f"SELECT prompt_version FROM thesis_classifications "
                f"WHERE signal_id IN ({placeholders}) "
                f"  AND prompt_version IS NOT NULL "
                f"ORDER BY classified_at DESC LIMIT 1",
                list(signal_ids),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row and row[0]:
            return str(row[0])
        return None
    finally:
        con.close()


def _atomic_write_json(target: Path, payload: Mapping[str, object]) -> None:
    """Atomically write JSON to ``target`` via temp sibling + ``os.replace``.

    On any failure during write, fsync, or rename, the original ``target``
    is left unchanged and the temp file is cleaned up. Mirrors the Day 3
    dashboard pattern (``scripts/generate_strategy_dashboard.py:1026``).
    """
    target = Path(target)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except (AttributeError, OSError):
                pass
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _build_artifact(
    *,
    mode: str,
    seed: int,
    target_precision: float,
    args: argparse.Namespace,
    summary: Mapping[str, Any],
    train: Mapping[str, Any],
    calibration: Mapping[str, Any],
    holdout: Mapping[str, Any],
    label_breakdown: Mapping[str, int],
    bootstrap_result: Optional[dict],
    cv_result: Optional[dict],
    chosen_cutoff_value: float,
    instability_warning: Optional[str],
    instability_cv: float,
    db_path: Path,
    fit_signal_ids: Sequence[int],
) -> dict[str, Any]:
    """Assemble the threshold_selection artifact payload."""
    git_block = _capture_git_provenance()
    chosen_rule = "bootstrap_p50" if mode == "bootstrap" else "cv_p50"
    return {
        "schema_version": 1,
        "artifact_type": "threshold_selection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "seed": seed,
        "target_precision": target_precision,
        "score_binding": {
            "table": SCORE_TABLE,
            "column": SCORE_COLUMN,
            "semantic_name": SCORE_SEMANTIC_NAME,
            "score_direction": SCORE_DIRECTION,
            "decision_rule": DECISION_RULE,
            "producer": SCORE_PRODUCER,
            "version": SCORE_VERSION,
            "version_policy": SCORE_VERSION_POLICY,
        },
        "input": {
            "calibration_file": str(args.calibration_file),
            "train_file": str(args.train_file),
            "holdout_file": str(args.holdout_file),
            "calibration_signal_count": len(calibration.get("signal_ids", [])),
            "calibration_label_breakdown": dict(label_breakdown)
            if mode == "bootstrap"
            else {},
            "labels_used_for_fitting": ["TP", "FP"],
            "labels_excluded_from_fitting": ["UNSURE", "ADJ", "missing"],
            "calibration_split_sha": canonical_split_sha(
                calibration.get("signal_ids", [])
            ),
            "train_split_sha": canonical_split_sha(
                train.get("signal_ids", [])
            ),
            "holdout_split_sha": canonical_split_sha(
                holdout.get("signal_ids", [])
            ),
            "schema_contract_path": str(args.schema_contract),
        },
        "git": git_block,
        "scoring_provenance": {
            "score_table": SCORE_TABLE,
            "score_column": SCORE_COLUMN,
            "label_table": LABEL_TABLE,
            "label_column": LABEL_COLUMN,
            "active_thesis_prompt_version": _capture_active_thesis_prompt_version(
                db_path, fit_signal_ids
            ),
            "active_llm_thesis_mode": os.environ.get("LLM_THESIS_MODE"),
            "high_confidence_threshold_at_run_time": HIGH_CONFIDENCE_THRESHOLD_AT_RUN_TIME,
        },
        "bootstrap": bootstrap_result,
        "cv": cv_result,
        "chosen_cutoff": {
            "value": chosen_cutoff_value,
            "rule": chosen_rule,
            "rationale": (
                "Median (p50) of the bootstrap/CV cut-off distribution. "
                "Consumers should read the percentile band, not just the "
                "point estimate (constraint 4)."
            ),
        },
        "instability": {
            "cv": instability_cv,
            "warn_threshold": args.instability_cv_warn,
            "fail_threshold": args.instability_cv_fail,
            "strict": bool(args.strict_instability),
            "warning": instability_warning,
        },
        "deferred_consumers": [
            "Day 5+ router-config writer",
            "Day 3 dashboard new block (when promoted out of deferred)",
        ],
    }


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="recalibrate-conformal",
        description=(
            "Day 4 — fit a single overall conformal cut-off via bootstrap "
            "(default) or repeated stratified K-fold CV on train. The "
            "holdout split is never read except to verify input/holdout "
            "disjointness."
        ),
    )
    parser.add_argument(
        "--calibration-file",
        type=Path,
        default=DEFAULT_CALIBRATION_FILE,
    )
    parser.add_argument(
        "--train-file",
        type=Path,
        default=DEFAULT_TRAIN_FILE,
    )
    parser.add_argument(
        "--holdout-file",
        type=Path,
        default=DEFAULT_HOLDOUT_FILE,
        help="Required for the disjointness validator (constraint 5).",
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=DEFAULT_SUMMARY_FILE,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("DISCOVERY_DB_PATH", DEFAULT_DB_PATH)),
    )
    parser.add_argument(
        "--schema-contract",
        type=Path,
        default=DEFAULT_CONTRACT_PATH,
    )
    parser.add_argument(
        "--mode",
        choices=["bootstrap", "cv"],
        default="bootstrap",
    )
    parser.add_argument(
        "--fallback-cv",
        action="store_true",
        help="Boolean alias for --mode cv (matches constraint 2 language).",
    )
    parser.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=1000,
    )
    parser.add_argument("--cv-folds", type=int, default=5)
    parser.add_argument("--cv-repeats", type=int, default=10)
    parser.add_argument("--min-calibration-size", type=int, default=4)
    parser.add_argument("--target-precision", type=float, default=0.90)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--instability-cv-warn", type=float, default=0.20)
    parser.add_argument("--instability-cv-fail", type=float, default=0.50)
    parser.add_argument("--strict-instability", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    # CLI argument validation that argparse does not cover.
    if not (100 <= args.bootstrap_iterations <= 100_000):
        sys.stderr.write(
            f"--bootstrap-iterations must be in [100, 100000]; "
            f"got {args.bootstrap_iterations}\n"
        )
        return EXIT_INPUT_ERROR
    if args.cv_folds < 2:
        sys.stderr.write(f"--cv-folds must be >= 2; got {args.cv_folds}\n")
        return EXIT_INPUT_ERROR
    if args.cv_repeats < 1:
        sys.stderr.write(f"--cv-repeats must be >= 1; got {args.cv_repeats}\n")
        return EXIT_INPUT_ERROR
    if not (0.0 < args.target_precision < 1.0):
        sys.stderr.write(
            f"--target-precision must be in (0, 1); "
            f"got {args.target_precision}\n"
        )
        return EXIT_INPUT_ERROR

    mode = "cv" if (args.fallback_cv or args.mode == "cv") else "bootstrap"

    # Schema preflight (exit 2 before any DB row read).
    try:
        from scripts.inspect_live_schema import (
            inspect_database,
            load_contract,
        )
        contract = load_contract(args.schema_contract)
        inspection = inspect_database(args.db, contract)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"schema-probe failure: {exc}\n")
        return EXIT_SCHEMA_FAILED
    if not inspection.get("ok"):
        sys.stderr.write(
            f"schema contract violation: missing_tables="
            f"{inspection.get('missing_tables')} "
            f"missing_columns={inspection.get('missing_columns')}\n"
        )
        return EXIT_SCHEMA_FAILED

    # Load all four split artifacts (load-all-for-provenance, fit-by-mode).
    try:
        summary = _load_split_file(args.summary_file)
        train = _load_split_file(args.train_file)
        calibration = _load_split_file(args.calibration_file)
        holdout = _load_split_file(args.holdout_file)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"input failure: {exc}\n")
        return EXIT_INPUT_ERROR

    try:
        validate_split_file_consistency(
            summary=summary,
            train=train,
            calibration=calibration,
            holdout=holdout,
        )
    except ValueError as exc:
        sys.stderr.write(f"input failure: {exc}\n")
        return EXIT_INPUT_ERROR

    # Holdout disjointness — runs BEFORE any DB read or fit, so even a misuse
    # cannot smuggle holdout IDs into the score arrays.
    holdout_ids = holdout.get("signal_ids", [])
    try:
        assert_holdout_disjoint(
            calibration.get("signal_ids", []), holdout_ids
        )
        assert_holdout_disjoint(train.get("signal_ids", []), holdout_ids)
    except HoldoutLeakError as exc:
        sys.stderr.write(f"holdout protection: {exc}\n")
        return EXIT_INPUT_ERROR

    # Pick fit-input by mode.
    if mode == "bootstrap":
        try:
            validate_min_calibration_size(
                rows=len(calibration.get("signal_ids", [])),
                floor=args.min_calibration_size,
            )
        except ValueError as exc:
            sys.stderr.write(f"input failure: {exc}\n")
            return EXIT_INPUT_ERROR
        fit_signal_ids = list(calibration.get("signal_ids", []))
    else:
        fit_signal_ids = list(train.get("signal_ids", []))
        if not fit_signal_ids:
            sys.stderr.write(
                "input failure: train split is empty in --mode cv. "
                "Re-run scripts/create_evaluation_splits.py.\n"
            )
            return EXIT_INPUT_ERROR

    # Read scores + labels (LEFT JOIN, read-only).
    try:
        scores, labels, label_breakdown = _read_scores_and_labels(
            args.db, fit_signal_ids
        )
    except ValueError as exc:
        sys.stderr.write(f"input failure: {exc}\n")
        return EXIT_INPUT_ERROR

    # Base-set feasibility check (constraint: infeasible base set is exit 1,
    # not silent fall-through).
    base_cutoff = fit_single_cutoff(
        scores, labels, target_precision=args.target_precision
    )
    if base_cutoff is None:
        # Compute max attainable precision so the operator sees the gap
        # between target and reality rather than only "no threshold reached".
        eligible = [
            (s, lab) for s, lab in zip(scores, labels) if lab in _FITTING_LABELS
        ]
        max_p = 0.0
        if eligible:
            for t in sorted({s for s, _ in eligible}):
                tp = sum(1 for s, lab in eligible if s >= t and lab == "TP")
                fp = sum(1 for s, lab in eligible if s >= t and lab == "FP")
                if tp > 0 and tp + fp > 0:
                    max_p = max(max_p, tp / (tp + fp))
        sys.stderr.write(
            f"input failure: no threshold reaches target precision "
            f"{args.target_precision} on the base "
            f"{'calibration' if mode == 'bootstrap' else 'train'} set "
            f"(max attainable precision: {max_p:.3f}). "
            f"Lower --target-precision or re-run "
            f"scripts/create_evaluation_splits.py after applying more "
            f"labels.\n"
        )
        return EXIT_INPUT_ERROR

    # Fit-by-mode.
    bootstrap_result: Optional[dict] = None
    cv_result: Optional[dict] = None
    if mode == "bootstrap":
        bootstrap_result = bootstrap_cutoff(
            scores, labels,
            target_precision=args.target_precision,
            iterations=args.bootstrap_iterations,
            seed=args.seed,
        )
        feasible = (
            bootstrap_result["iterations"]
            - bootstrap_result["infeasible_iterations"]
        )
        if feasible < max(1, int(0.10 * bootstrap_result["iterations"])):
            sys.stderr.write(
                f"input failure: only {feasible} feasible bootstrap "
                f"resamples out of {bootstrap_result['iterations']} "
                f"(<10% floor). Lower --target-precision or apply more "
                f"labels.\n"
            )
            return EXIT_INPUT_ERROR
        cutoffs_band = bootstrap_result["cutoffs"]
    else:
        cv_result = cv_cutoff(
            scores, labels,
            target_precision=args.target_precision,
            folds=args.cv_folds,
            repeats=args.cv_repeats,
            seed=args.seed,
        )
        feasible = (
            cv_result["fits_completed"] - cv_result["infeasible_fits"]
        )
        if feasible < max(1, int(0.10 * cv_result["fits_completed"])):
            sys.stderr.write(
                f"input failure: only {feasible} feasible CV folds out "
                f"of {cv_result['fits_completed']} (<10% floor).\n"
            )
            return EXIT_INPUT_ERROR
        cutoffs_band = cv_result["cutoffs"]

    chosen_cutoff_value = float(cutoffs_band["p50"])
    instability_cv = float(cutoffs_band["cv"])
    instability_warning = compute_instability_warning(
        instability_cv, args.instability_cv_warn
    )
    if instability_warning is not None:
        sys.stderr.write(f"WARNING: {instability_warning}\n")
    if (
        args.strict_instability
        and instability_cv > args.instability_cv_fail
    ):
        sys.stderr.write(
            f"strict instability gate: bootstrap CV {instability_cv:.3f} "
            f"exceeds fail threshold {args.instability_cv_fail:.2f}; "
            f"refusing to write artifact (exit 3).\n"
        )
        return EXIT_INSTABILITY_GATE

    artifact = _build_artifact(
        mode=mode,
        seed=args.seed,
        target_precision=args.target_precision,
        args=args,
        summary=summary,
        train=train,
        calibration=calibration,
        holdout=holdout,
        label_breakdown=label_breakdown,
        bootstrap_result=bootstrap_result,
        cv_result=cv_result,
        chosen_cutoff_value=chosen_cutoff_value,
        instability_warning=instability_warning,
        instability_cv=instability_cv,
        db_path=args.db,
        fit_signal_ids=fit_signal_ids,
    )

    if args.dry_run:
        sys.stdout.write(
            f"[dry-run] mode={mode} cutoff={chosen_cutoff_value:.4f} "
            f"cv={instability_cv:.4f} (no artifact written)\n"
        )
        return EXIT_OK

    try:
        _atomic_write_json(args.out, artifact)
    except OSError as exc:
        sys.stderr.write(f"output failure: {exc}\n")
        return EXIT_INPUT_ERROR

    if instability_warning is not None:
        sys.stderr.write(f"WARNING: {instability_warning}\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
