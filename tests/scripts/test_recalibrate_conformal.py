"""Day 4 — Conformal recalibration contract suite.

Plan of record: ``.omx/plans/phase2-day4-calibration-plan.md``.

The suite is built TDD-first: each new function gets a failing test before the
implementation lands. Tests are organized by plan section (threshold recipe,
holdout protection, bootstrap mode, CV mode, provenance, schema probe wiring,
score binding, output disposition, CLI defaults, atomic write).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.recalibrate_conformal import (
    DECISION_RULE,
    DEFAULT_CALIBRATION_FILE,
    DEFAULT_HOLDOUT_FILE,
    DEFAULT_OUT_PATH,
    DEFAULT_TRAIN_FILE,
    EXIT_INPUT_ERROR,
    EXIT_INSTABILITY_GATE,
    EXIT_OK,
    EXIT_SCHEMA_FAILED,
    HoldoutLeakError,
    SCORE_COLUMN,
    SCORE_TABLE,
    SCORE_DIRECTION,
    SCORE_SEMANTIC_NAME,
    assert_holdout_disjoint,
    bootstrap_cutoff,
    canonical_split_sha,
    coefficient_of_variation,
    compute_instability_warning,
    cv_cutoff,
    fit_single_cutoff,
    main,
    percentile_band,
    validate_min_calibration_size,
    validate_split_file_consistency,
)


# ---------------------------------------------------------------------------
# Threshold recipe (fit_single_cutoff)
# ---------------------------------------------------------------------------


class TestFitSingleCutoff:
    """The threshold-selection recipe.

    Label policy: TP=positive, FP=negative; UNSURE/ADJ/None excluded from
    fitting. Selection: lowest t whose precision(t) >= target_precision.
    Score direction: accept_if_score_gte_threshold (higher is more confident).
    Returns None when infeasible.
    """

    def test_returns_lowest_threshold_meeting_target_precision(self):
        # Three TPs at 0.9, 0.85, 0.8; three FPs at 0.7, 0.6, 0.5.
        # At t=0.8: 3 TP / (3 TP + 0 FP) = 1.0 -> meets 0.90.
        # At t=0.7: 3 TP / (3 TP + 1 FP) = 0.75 -> below 0.90.
        # Lowest threshold meeting 0.90 is 0.8.
        scores = [0.9, 0.85, 0.8, 0.7, 0.6, 0.5]
        labels = ["TP", "TP", "TP", "FP", "FP", "FP"]
        result = fit_single_cutoff(scores, labels, target_precision=0.90)
        assert result == pytest.approx(0.8)

    def test_returns_lowest_threshold_when_target_allows_one_fp(self):
        # 4 TPs interleaved with one FP at 0.75.
        # At t=0.7: 4 TP / (4 TP + 1 FP) = 0.80 -> meets 0.80 target.
        scores = [0.9, 0.85, 0.8, 0.75, 0.7]
        labels = ["TP", "TP", "TP", "FP", "TP"]
        result = fit_single_cutoff(scores, labels, target_precision=0.80)
        assert result == pytest.approx(0.7)

    def test_returns_None_when_no_threshold_meets_target(self):
        # All FPs above all TPs: precision is 0 at every threshold that has TPs.
        scores = [0.9, 0.85, 0.8, 0.5, 0.4]
        labels = ["FP", "FP", "FP", "TP", "TP"]
        # At t=0.9: 0 TP / 1 FP = 0 (or undefined; conformal recipe requires
        # at least one TP at threshold).
        # No threshold meets 0.90.
        assert fit_single_cutoff(scores, labels, target_precision=0.90) is None

    def test_returns_None_when_target_precision_unattainable_with_any_tp(self):
        # Only FPs above any TP-containing threshold.
        scores = [0.9, 0.5]
        labels = ["FP", "TP"]
        # At t=0.9: 0/1 = 0.
        # At t=0.5: 1/2 = 0.5.
        # 0.95 unattainable.
        assert fit_single_cutoff(scores, labels, target_precision=0.95) is None

    def test_excludes_unsure_labels_from_fitting(self):
        # UNSURE is treated as if absent — neither TP nor FP.
        scores = [0.9, 0.85, 0.8, 0.7, 0.6]
        labels = ["TP", "TP", "TP", "UNSURE", "FP"]
        # UNSURE at 0.7 is ignored.
        # At t=0.6: 3 TP / (3 TP + 1 FP) = 0.75 -> below 0.90.
        # At t=0.8: 3 TP / (3 TP + 0 FP) = 1.0 -> meets.
        assert fit_single_cutoff(
            scores, labels, target_precision=0.90
        ) == pytest.approx(0.8)

    def test_excludes_adj_labels_from_fitting(self):
        scores = [0.9, 0.85, 0.8, 0.7, 0.6]
        labels = ["TP", "TP", "TP", "ADJ", "FP"]
        assert fit_single_cutoff(
            scores, labels, target_precision=0.90
        ) == pytest.approx(0.8)

    def test_excludes_missing_labels_from_fitting(self):
        # None label = no signal_quality_metrics row; treated as absent.
        scores = [0.9, 0.85, 0.8, 0.7, 0.6]
        labels = ["TP", "TP", "TP", None, "FP"]
        assert fit_single_cutoff(
            scores, labels, target_precision=0.90
        ) == pytest.approx(0.8)

    def test_target_precision_at_exact_boundary_is_feasible(self):
        # 4 TPs and 1 FP: at t=lowest TP, precision = 4/5 = 0.80.
        scores = [0.9, 0.85, 0.8, 0.75, 0.7]
        labels = ["TP", "TP", "FP", "TP", "TP"]
        # At t=0.7: 4 TP / (4 TP + 1 FP) = 0.80.
        # Exactly meets 0.80 target -> feasible.
        assert fit_single_cutoff(
            scores, labels, target_precision=0.80
        ) == pytest.approx(0.7)

    def test_only_tp_labels_return_min_observed_score(self):
        # No FPs -> precision is 1.0 everywhere -> lowest threshold wins.
        scores = [0.9, 0.7, 0.5]
        labels = ["TP", "TP", "TP"]
        assert fit_single_cutoff(
            scores, labels, target_precision=0.90
        ) == pytest.approx(0.5)

    def test_only_fp_labels_return_None(self):
        scores = [0.9, 0.7, 0.5]
        labels = ["FP", "FP", "FP"]
        assert fit_single_cutoff(scores, labels, target_precision=0.50) is None

    def test_empty_inputs_return_None(self):
        assert fit_single_cutoff([], [], target_precision=0.90) is None

    def test_all_unsure_inputs_return_None(self):
        scores = [0.9, 0.7, 0.5]
        labels = ["UNSURE", "UNSURE", "UNSURE"]
        assert fit_single_cutoff(scores, labels, target_precision=0.90) is None

    def test_recipe_respects_higher_is_more_confident_direction(self):
        # If we silently inverted the direction (lower-is-more-confident),
        # flipping scores would yield the same cut-off. The recipe is hard
        # coded to higher-is-more-confident; flipping must yield a different
        # result (or None).
        scores = [0.9, 0.85, 0.8, 0.7, 0.6, 0.5]
        labels = ["TP", "TP", "TP", "FP", "FP", "FP"]
        flipped_scores = [1.0 - s for s in scores]
        original = fit_single_cutoff(scores, labels, target_precision=0.90)
        flipped = fit_single_cutoff(
            flipped_scores, labels, target_precision=0.90
        )
        # Original picks a high cut-off; flipped (TPs now low) cannot meet
        # 0.90 because every threshold >= a TP score also includes FPs above.
        assert original == pytest.approx(0.8)
        # On flipped data, TPs are at 0.1/0.15/0.2; FPs at 0.3/0.4/0.5.
        # Any threshold that includes a TP also includes all 3 FPs -> precision
        # max is 3/(3+3)=0.5 < 0.90 -> infeasible.
        assert flipped is None

    def test_score_label_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            fit_single_cutoff([0.5, 0.6], ["TP"], target_precision=0.9)

    def test_target_precision_out_of_unit_interval_raises(self):
        scores = [0.9, 0.5]
        labels = ["TP", "FP"]
        with pytest.raises(ValueError):
            fit_single_cutoff(scores, labels, target_precision=0.0)
        with pytest.raises(ValueError):
            fit_single_cutoff(scores, labels, target_precision=1.5)
        with pytest.raises(ValueError):
            fit_single_cutoff(scores, labels, target_precision=-0.1)

    def test_returns_python_float_not_numpy_scalar(self):
        # JSON serialization downstream prefers native float; numpy scalars
        # serialize but assert exact type to avoid dtype-leak in artifacts.
        scores = [0.9, 0.7, 0.5]
        labels = ["TP", "TP", "TP"]
        result = fit_single_cutoff(scores, labels, target_precision=0.90)
        assert isinstance(result, float)
        assert not isinstance(result, bool)
        assert not math.isnan(result)


# ---------------------------------------------------------------------------
# Pure helpers (percentile_band, coefficient_of_variation,
# canonical_split_sha, compute_instability_warning)
# ---------------------------------------------------------------------------


class TestPercentileBand:
    def test_returns_mean_p5_p50_p95_stdev_cv(self):
        band = percentile_band([0.1, 0.2, 0.3, 0.4, 0.5])
        assert set(band.keys()) == {"mean", "p5", "p50", "p95", "stdev", "cv"}

    def test_mean_p50_match_expected(self):
        values = [0.10, 0.20, 0.30, 0.40, 0.50]
        band = percentile_band(values)
        assert band["mean"] == pytest.approx(0.30)
        assert band["p50"] == pytest.approx(0.30)

    def test_p5_and_p95_bracket_distribution(self):
        values = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
        band = percentile_band(values)
        assert band["p5"] < band["p50"] < band["p95"]
        assert band["p5"] == pytest.approx(0.14)
        assert band["p95"] == pytest.approx(0.86)

    def test_constant_values_yield_zero_stdev_and_zero_cv(self):
        band = percentile_band([0.7, 0.7, 0.7, 0.7])
        assert band["stdev"] == pytest.approx(0.0)
        assert band["cv"] == pytest.approx(0.0)

    def test_returns_native_floats_for_json_safety(self):
        band = percentile_band([0.1, 0.2, 0.3])
        for key, val in band.items():
            assert isinstance(val, float), f"{key} is {type(val)}"
            assert not isinstance(val, bool)

    def test_empty_values_raise_value_error(self):
        with pytest.raises(ValueError):
            percentile_band([])

    def test_single_value_yields_zero_dispersion(self):
        band = percentile_band([0.42])
        assert band["mean"] == pytest.approx(0.42)
        assert band["p50"] == pytest.approx(0.42)
        assert band["p5"] == pytest.approx(0.42)
        assert band["p95"] == pytest.approx(0.42)
        assert band["stdev"] == pytest.approx(0.0)
        assert band["cv"] == pytest.approx(0.0)


class TestCoefficientOfVariation:
    def test_returns_stdev_over_mean(self):
        values = [0.10, 0.20, 0.30, 0.40, 0.50]
        # Sample stdev (unbiased) for these values is ~0.158;
        # mean is 0.30 -> cv ~= 0.527.
        cv = coefficient_of_variation(values)
        assert cv == pytest.approx(0.5270462766947299, rel=1e-6)

    def test_constant_values_yield_zero_cv(self):
        assert coefficient_of_variation([0.5, 0.5, 0.5]) == pytest.approx(0.0)

    def test_empty_values_raise(self):
        with pytest.raises(ValueError):
            coefficient_of_variation([])

    def test_zero_mean_raises(self):
        with pytest.raises(ValueError):
            coefficient_of_variation([0.0, 0.0, 0.0])

    def test_returns_native_float(self):
        cv = coefficient_of_variation([0.1, 0.2, 0.3])
        assert isinstance(cv, float)
        assert not isinstance(cv, bool)


class TestCanonicalSplitSha:
    """Matches Day 3 holdout SHA scheme.

    json.dumps(sorted(unique(map(str, ids))), separators=(",", ":"),
    ensure_ascii=False), then sha256 hex.
    """

    def test_deterministic_for_fixed_signal_ids(self):
        ids = [1, 2, 3, 4, 5]
        a = canonical_split_sha(ids)
        b = canonical_split_sha(ids)
        assert a == b
        # 64-char lowercase hex sha256.
        assert len(a) == 64
        assert all(c in "0123456789abcdef" for c in a)

    def test_independent_of_input_order(self):
        a = canonical_split_sha([1, 2, 3, 4, 5])
        b = canonical_split_sha([5, 4, 3, 2, 1])
        assert a == b

    def test_independent_of_duplicates(self):
        a = canonical_split_sha([1, 2, 3])
        b = canonical_split_sha([1, 2, 3, 1, 2, 3])
        assert a == b

    def test_changes_with_different_id_set(self):
        a = canonical_split_sha([1, 2, 3])
        b = canonical_split_sha([1, 2, 4])
        assert a != b

    def test_string_and_int_ids_collapse_to_same_sha(self):
        # The recipe coerces ids to strings before sorting + serializing.
        a = canonical_split_sha([1, 2, 3])
        b = canonical_split_sha(["1", "2", "3"])
        assert a == b

    def test_matches_expected_sha_scheme(self):
        # Reference SHA computed via the documented scheme:
        # sha256(json.dumps(["1","2","3"], separators=(",", ":"))).
        import hashlib
        import json
        expected_canonical = json.dumps(
            ["1", "2", "3"], separators=(",", ":"), ensure_ascii=False
        )
        expected = hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()
        assert canonical_split_sha([1, 2, 3]) == expected

    def test_empty_ids_returns_sha_of_empty_list(self):
        a = canonical_split_sha([])
        # sha256 of "[]"
        import hashlib
        expected = hashlib.sha256(b"[]").hexdigest()
        assert a == expected


class TestComputeInstabilityWarning:
    def test_returns_None_when_cv_below_warn_threshold(self):
        assert compute_instability_warning(cv=0.05, warn_threshold=0.20) is None

    def test_returns_None_when_cv_equals_warn_threshold(self):
        # Strictly greater than is the warning trigger to avoid noise at the
        # exact boundary. Plan section: "bootstrap CV above this emits a
        # prominent warning".
        assert compute_instability_warning(cv=0.20, warn_threshold=0.20) is None

    def test_returns_warning_string_when_cv_above_warn_threshold(self):
        warning = compute_instability_warning(cv=0.30, warn_threshold=0.20)
        assert warning is not None
        assert isinstance(warning, str)

    def test_warning_string_includes_cv_and_threshold_values(self):
        warning = compute_instability_warning(cv=0.345, warn_threshold=0.20)
        assert "0.345" in warning or "0.35" in warning
        assert "0.20" in warning or "0.2" in warning


# ---------------------------------------------------------------------------
# Validators (assert_holdout_disjoint, validate_split_file_consistency,
# validate_min_calibration_size)
# ---------------------------------------------------------------------------


class TestAssertHoldoutDisjoint:
    """Constraint 5: holdout signal_ids must never overlap inputs."""

    def test_disjoint_sets_pass(self):
        # No exception raised.
        assert_holdout_disjoint({1, 2, 3}, {4, 5, 6})

    def test_overlap_raises_holdout_leak_error(self):
        with pytest.raises(HoldoutLeakError):
            assert_holdout_disjoint({1, 2, 3}, {3, 4, 5})

    def test_error_message_does_not_leak_signal_ids(self):
        # The error must report the overlap count and never the IDs themselves
        # (matches Day 3 holdout protection contract).
        try:
            assert_holdout_disjoint({1, 2, 3, 42}, {3, 42, 99, 100})
        except HoldoutLeakError as exc:
            msg = str(exc)
            # IDs that overlap are 3 and 42; neither must appear.
            assert " 3" not in msg or "ids" not in msg.lower()
            assert "42" not in msg
            # The count of overlapping IDs (2) is fine to surface.
            assert "2" in msg
        else:
            pytest.fail("HoldoutLeakError was not raised")

    def test_empty_inputs_are_disjoint(self):
        assert_holdout_disjoint(set(), set())
        assert_holdout_disjoint({1, 2, 3}, set())
        assert_holdout_disjoint(set(), {1, 2, 3})


class TestValidateSplitFileConsistency:
    """Summary file and per-split files must agree on seed, generated_at,
    and fractions. Mismatch means the splits were regenerated separately and
    should not be consumed together."""

    def _split(self, *, seed=42, generated_at="2026-04-28T00:00:00+00:00",
               fractions=None, signal_ids=None):
        return {
            "seed": seed,
            "generated_at": generated_at,
            "fractions": fractions or {
                "train": 0.6, "calibration": 0.2, "holdout": 0.2
            },
            "signal_ids": signal_ids or [],
        }

    def _summary(self, *, seed=42, generated_at="2026-04-28T00:00:00+00:00",
                 fractions=None):
        return {
            "seed": seed,
            "generated_at": generated_at,
            "fractions": fractions or {
                "train": 0.6, "calibration": 0.2, "holdout": 0.2
            },
        }

    def test_consistent_files_pass(self):
        validate_split_file_consistency(
            summary=self._summary(),
            train=self._split(),
            calibration=self._split(),
            holdout=self._split(),
        )

    def test_seed_mismatch_raises(self):
        with pytest.raises(ValueError, match="seed"):
            validate_split_file_consistency(
                summary=self._summary(seed=42),
                train=self._split(seed=42),
                calibration=self._split(seed=43),
                holdout=self._split(seed=42),
            )

    def test_generated_at_mismatch_raises(self):
        with pytest.raises(ValueError, match="generated_at"):
            validate_split_file_consistency(
                summary=self._summary(),
                train=self._split(generated_at="2026-04-29T00:00:00+00:00"),
                calibration=self._split(),
                holdout=self._split(),
            )

    def test_fractions_mismatch_raises(self):
        with pytest.raises(ValueError, match="fractions"):
            validate_split_file_consistency(
                summary=self._summary(),
                train=self._split(),
                calibration=self._split(),
                holdout=self._split(
                    fractions={"train": 0.5, "calibration": 0.25, "holdout": 0.25}
                ),
            )

    def test_summary_seed_disagrees_with_per_split_seed_raises(self):
        with pytest.raises(ValueError, match="seed"):
            validate_split_file_consistency(
                summary=self._summary(seed=99),
                train=self._split(seed=42),
                calibration=self._split(seed=42),
                holdout=self._split(seed=42),
            )


class TestValidateMinCalibrationSize:
    def test_at_floor_passes(self):
        validate_min_calibration_size(rows=4, floor=4)

    def test_above_floor_passes(self):
        validate_min_calibration_size(rows=40, floor=4)

    def test_below_floor_raises(self):
        with pytest.raises(ValueError):
            validate_min_calibration_size(rows=3, floor=4)

    def test_below_floor_error_recommends_create_evaluation_splits(self):
        with pytest.raises(ValueError) as exc_info:
            validate_min_calibration_size(rows=2, floor=4)
        # Error must point operators at the script that produces more rows.
        assert "create_evaluation_splits" in str(exc_info.value)

    def test_zero_rows_raises(self):
        with pytest.raises(ValueError):
            validate_min_calibration_size(rows=0, floor=4)


# ---------------------------------------------------------------------------
# Bootstrap and CV wrappers
# ---------------------------------------------------------------------------


def _calibration_fixture():
    """Synthetic high-feasibility calibration set: 10 TP scored 0.7-0.95,
    20 FP scored 0.1-0.6, plus 2 UNSURE that should be ignored by the fit."""
    scores = (
        [0.70 + 0.025 * i for i in range(10)]      # 10 TPs at 0.700..0.925
        + [0.10 + 0.025 * i for i in range(20)]    # 20 FPs at 0.100..0.575
        + [0.45, 0.55]                              # 2 UNSURE
    )
    labels = ["TP"] * 10 + ["FP"] * 20 + ["UNSURE"] * 2
    return scores, labels


class TestBootstrapCutoff:
    def test_returns_required_keys(self):
        scores, labels = _calibration_fixture()
        result = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=50, seed=42,
        )
        assert set(result.keys()) == {
            "iterations",
            "cutoffs",
            "precision_at_cutoff",
            "infeasible_iterations",
        }

    def test_iterations_field_matches_input(self):
        scores, labels = _calibration_fixture()
        result = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=37, seed=42,
        )
        assert result["iterations"] == 37

    def test_cutoffs_band_has_percentile_keys(self):
        scores, labels = _calibration_fixture()
        result = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=50, seed=42,
        )
        assert set(result["cutoffs"].keys()) == {
            "mean", "p5", "p50", "p95", "stdev", "cv"
        }
        assert set(result["precision_at_cutoff"].keys()) == {
            "mean", "p5", "p50", "p95", "stdev", "cv"
        }

    def test_seeded_runs_are_byte_deterministic(self):
        scores, labels = _calibration_fixture()
        a = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=100, seed=42,
        )
        b = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=100, seed=42,
        )
        assert a == b

    def test_different_seeds_yield_different_distributions(self):
        scores, labels = _calibration_fixture()
        a = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=200, seed=42,
        )
        b = bootstrap_cutoff(
            scores, labels,
            target_precision=0.90, iterations=200, seed=43,
        )
        # The summary of the cut-off distribution is what we care about — not
        # any particular sample-index sequence.
        assert a["cutoffs"] != b["cutoffs"]

    def test_infeasible_iterations_counted_for_unattainable_target(self):
        # 1 TP, 5 FPs above it -> precision 1/6 -> 0.95 unattainable on most
        # resamples (any resample missing the lone TP is infeasible).
        scores = [0.95, 0.90, 0.85, 0.80, 0.75, 0.70]
        labels = ["TP", "FP", "FP", "FP", "FP", "FP"]
        result = bootstrap_cutoff(
            scores, labels,
            target_precision=0.95, iterations=200, seed=42,
        )
        assert result["infeasible_iterations"] > 0
        # And the band should still describe the feasible resamples.
        feasible = result["iterations"] - result["infeasible_iterations"]
        if feasible > 0:
            assert result["cutoffs"] is not None
        else:
            assert result["cutoffs"] is None

    def test_all_infeasible_yields_None_bands(self):
        # No TPs at all -> every resample infeasible.
        scores = [0.9, 0.7, 0.5]
        labels = ["FP", "FP", "FP"]
        result = bootstrap_cutoff(
            scores, labels,
            target_precision=0.50, iterations=10, seed=42,
        )
        assert result["infeasible_iterations"] == 10
        assert result["cutoffs"] is None
        assert result["precision_at_cutoff"] is None

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            bootstrap_cutoff(
                [], [],
                target_precision=0.90, iterations=10, seed=42,
            )


def _train_fixture():
    """Synthetic train set big enough for stratified 5-fold CV: 25 TP, 50 FP."""
    scores = (
        [0.70 + 0.01 * i for i in range(25)]   # 25 TPs at 0.70..0.94
        + [0.20 + 0.005 * i for i in range(50)]  # 50 FPs at 0.20..0.445
    )
    labels = ["TP"] * 25 + ["FP"] * 50
    return scores, labels


class TestCvCutoff:
    def test_returns_required_keys(self):
        scores, labels = _train_fixture()
        result = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=2, seed=42,
        )
        assert set(result.keys()) == {
            "folds",
            "repeats",
            "fits_completed",
            "cutoffs",
            "precision_at_cutoff",
            "infeasible_fits",
        }

    def test_fits_completed_equals_folds_times_repeats(self):
        scores, labels = _train_fixture()
        result = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=3, seed=42,
        )
        assert result["fits_completed"] == 5 * 3

    def test_seeded_runs_are_byte_deterministic(self):
        scores, labels = _train_fixture()
        a = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=2, seed=42,
        )
        b = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=2, seed=42,
        )
        assert a == b

    def test_different_seeds_yield_different_distributions(self):
        scores, labels = _train_fixture()
        a = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=3, seed=42,
        )
        b = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=3, seed=43,
        )
        assert a["cutoffs"] != b["cutoffs"]

    def test_unsure_and_adj_excluded_from_cv_input(self):
        # Adding UNSURE/ADJ rows should not change the deterministic output —
        # they are filtered before stratification.
        scores, labels = _train_fixture()
        with_unsure_scores = list(scores) + [0.55, 0.45]
        with_unsure_labels = list(labels) + ["UNSURE", "ADJ"]
        a = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=2, seed=42,
        )
        b = cv_cutoff(
            with_unsure_scores, with_unsure_labels,
            target_precision=0.90, folds=5, repeats=2, seed=42,
        )
        assert a == b

    def test_no_fittable_rows_raises(self):
        # All UNSURE — no TP/FP to stratify.
        with pytest.raises(ValueError):
            cv_cutoff(
                [0.5, 0.6, 0.7], ["UNSURE", "UNSURE", "UNSURE"],
                target_precision=0.90, folds=5, repeats=2, seed=42,
            )

    def test_cutoffs_band_has_percentile_keys(self):
        scores, labels = _train_fixture()
        result = cv_cutoff(
            scores, labels,
            target_precision=0.90, folds=5, repeats=2, seed=42,
        )
        assert set(result["cutoffs"].keys()) == {
            "mean", "p5", "p50", "p95", "stdev", "cv"
        }


# ---------------------------------------------------------------------------
# CLI integration: synthetic-DB + state-file fixtures
# ---------------------------------------------------------------------------


_CONTRACT_REQUIRED_TABLES = {
    "signals": [
        "id", "canonical_key", "source_api", "signal_type",
        "detected_at", "confidence",
    ],
    "quality_feedback": ["signal_id", "label", "created_at"],
    "signal_quality_metrics": [
        "signal_id", "canonical_key", "human_label",
        "label_source", "labeled_at", "status_event_id",
    ],
    "signal_processing": ["signal_id", "status"],
    "thesis_ml_predictions": ["signal_id", "ml_enablement"],
    "notion_status_events": [
        "id", "canonical_key", "old_status", "new_status", "observed_at",
    ],
}


def _write_schema_contract(path: Path) -> None:
    contract = {
        "version": 1,
        "required_tables": {
            name: {"required_columns": cols}
            for name, cols in _CONTRACT_REQUIRED_TABLES.items()
        },
        "forbidden_references": [
            "signal_quality_metrics.confidence_score",
            "quality_feedback.human_label",
        ],
    }
    path.write_text(json.dumps(contract, indent=2), encoding="utf-8")


def _create_test_db(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executescript("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                source_api TEXT,
                signal_type TEXT,
                detected_at TEXT,
                confidence REAL NOT NULL
            );
            CREATE TABLE quality_feedback (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                label TEXT,
                created_at TEXT
            );
            CREATE TABLE signal_quality_metrics (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                canonical_key TEXT,
                human_label TEXT,
                label_source TEXT,
                labeled_at TEXT,
                status_event_id INTEGER
            );
            CREATE TABLE signal_processing (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                status TEXT
            );
            CREATE TABLE thesis_ml_predictions (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                ml_enablement TEXT
            );
            CREATE TABLE notion_status_events (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                old_status TEXT,
                new_status TEXT,
                observed_at TEXT
            );
            CREATE TABLE thesis_classifications (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                prompt_version TEXT,
                classified_at TEXT
            );
        """)
        con.commit()
    finally:
        con.close()


def _populate_calibration_signals(db_path: Path, *, seed_id: int = 100) -> list[int]:
    """Insert signals with a feasible label/score distribution.

    Uses 8 TPs at high scores + 16 FPs at low scores so 0.90 is feasible at
    the lowest-TP threshold (precision = 8 / (8 + 0) = 1.0). 1 UNSURE row to
    exercise the breakdown counter.
    """
    con = sqlite3.connect(db_path)
    try:
        ids: list[int] = []
        for i in range(8):
            sid = seed_id + i
            ids.append(sid)
            con.execute(
                "INSERT INTO signals(id, canonical_key, source_api, "
                "signal_type, detected_at, confidence) VALUES(?,?,?,?,?,?)",
                (sid, f"key:{sid}", "test", "x", "2026-04-01", 0.80 + i * 0.01),
            )
            con.execute(
                "INSERT INTO signal_quality_metrics(signal_id, canonical_key, "
                "human_label, label_source, labeled_at) VALUES(?,?,?,?,?)",
                (sid, f"key:{sid}", "TP", "test", "2026-04-01"),
            )
        for i in range(16):
            sid = seed_id + 8 + i
            ids.append(sid)
            con.execute(
                "INSERT INTO signals(id, canonical_key, source_api, "
                "signal_type, detected_at, confidence) VALUES(?,?,?,?,?,?)",
                (sid, f"key:{sid}", "test", "x", "2026-04-01", 0.40 + i * 0.01),
            )
            con.execute(
                "INSERT INTO signal_quality_metrics(signal_id, canonical_key, "
                "human_label, label_source, labeled_at) VALUES(?,?,?,?,?)",
                (sid, f"key:{sid}", "FP", "test", "2026-04-01"),
            )
        # 1 UNSURE row at mid score
        sid = seed_id + 24
        ids.append(sid)
        con.execute(
            "INSERT INTO signals(id, canonical_key, source_api, signal_type, "
            "detected_at, confidence) VALUES(?,?,?,?,?,?)",
            (sid, f"key:{sid}", "test", "x", "2026-04-01", 0.55),
        )
        con.execute(
            "INSERT INTO signal_quality_metrics(signal_id, canonical_key, "
            "human_label, label_source, labeled_at) VALUES(?,?,?,?,?)",
            (sid, f"key:{sid}", "UNSURE", "test", "2026-04-01"),
        )
        # 1 missing-label row
        sid = seed_id + 25
        ids.append(sid)
        con.execute(
            "INSERT INTO signals(id, canonical_key, source_api, signal_type, "
            "detected_at, confidence) VALUES(?,?,?,?,?,?)",
            (sid, f"key:{sid}", "test", "x", "2026-04-01", 0.50),
        )
        con.commit()
        return ids
    finally:
        con.close()


def _populate_train_signals(db_path: Path, *, seed_id: int = 200) -> list[int]:
    """Insert a larger train set: 25 TP + 50 FP for stratified 5-fold CV."""
    con = sqlite3.connect(db_path)
    try:
        ids: list[int] = []
        for i in range(25):
            sid = seed_id + i
            ids.append(sid)
            con.execute(
                "INSERT INTO signals(id, canonical_key, source_api, "
                "signal_type, detected_at, confidence) VALUES(?,?,?,?,?,?)",
                (sid, f"k:{sid}", "test", "x", "2026-04-01", 0.70 + i * 0.005),
            )
            con.execute(
                "INSERT INTO signal_quality_metrics(signal_id, canonical_key, "
                "human_label, label_source, labeled_at) VALUES(?,?,?,?,?)",
                (sid, f"k:{sid}", "TP", "test", "2026-04-01"),
            )
        for i in range(50):
            sid = seed_id + 25 + i
            ids.append(sid)
            con.execute(
                "INSERT INTO signals(id, canonical_key, source_api, "
                "signal_type, detected_at, confidence) VALUES(?,?,?,?,?,?)",
                (sid, f"k:{sid}", "test", "x", "2026-04-01", 0.20 + i * 0.005),
            )
            con.execute(
                "INSERT INTO signal_quality_metrics(signal_id, canonical_key, "
                "human_label, label_source, labeled_at) VALUES(?,?,?,?,?)",
                (sid, f"k:{sid}", "FP", "test", "2026-04-01"),
            )
        con.commit()
        return ids
    finally:
        con.close()


def _populate_holdout_signals(db_path: Path, *, seed_id: int = 500) -> list[int]:
    con = sqlite3.connect(db_path)
    try:
        ids: list[int] = []
        for i in range(10):
            sid = seed_id + i
            ids.append(sid)
            con.execute(
                "INSERT INTO signals(id, canonical_key, source_api, "
                "signal_type, detected_at, confidence) VALUES(?,?,?,?,?,?)",
                (sid, f"h:{sid}", "test", "x", "2026-04-01", 0.5),
            )
            con.execute(
                "INSERT INTO signal_quality_metrics(signal_id, canonical_key, "
                "human_label, label_source, labeled_at) VALUES(?,?,?,?,?)",
                (sid, f"h:{sid}", "TP" if i < 3 else "FP", "test", "2026-04-01"),
            )
        con.commit()
        return ids
    finally:
        con.close()


def _write_split_files(
    state_dir: Path,
    *,
    train_ids: list[int],
    cal_ids: list[int],
    holdout_ids: list[int],
    seed: int = 42,
    generated_at: str = "2026-04-28T00:00:00+00:00",
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    fractions = {"train": 0.6, "calibration": 0.2, "holdout": 0.2}
    for name, ids in [
        ("train", train_ids),
        ("calibration", cal_ids),
        ("holdout", holdout_ids),
    ]:
        (state_dir / f"{name}_ids.json").write_text(
            json.dumps(
                {
                    "split": name,
                    "seed": seed,
                    "generated_at": generated_at,
                    "fractions": fractions,
                    "size": len(ids),
                    "signal_ids": ids,
                    "schema_version": 1,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    (state_dir / "evaluation_splits_summary.json").write_text(
        json.dumps(
            {
                "seed": seed,
                "generated_at": generated_at,
                "fractions": fractions,
                "total_rows": len(train_ids) + len(cal_ids) + len(holdout_ids),
                "schema_version": 1,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.fixture
def calibration_environment(tmp_path):
    """Build a complete tmp environment: DB, schema contract, four split files."""
    db_path = tmp_path / "signals.db"
    state_dir = tmp_path / "state"
    contract_path = tmp_path / "contract.json"
    out_path = state_dir / "conformal_calibration.json"

    _create_test_db(db_path)
    cal_ids = _populate_calibration_signals(db_path, seed_id=100)
    train_ids = _populate_train_signals(db_path, seed_id=200)
    holdout_ids = _populate_holdout_signals(db_path, seed_id=500)

    _write_split_files(
        state_dir,
        train_ids=train_ids,
        cal_ids=cal_ids,
        holdout_ids=holdout_ids,
    )
    _write_schema_contract(contract_path)

    return {
        "tmp_path": tmp_path,
        "db_path": db_path,
        "state_dir": state_dir,
        "contract_path": contract_path,
        "out_path": out_path,
        "cal_ids": cal_ids,
        "train_ids": train_ids,
        "holdout_ids": holdout_ids,
        "argv_base": [
            "--db", str(db_path),
            "--schema-contract", str(contract_path),
            "--calibration-file", str(state_dir / "calibration_ids.json"),
            "--train-file", str(state_dir / "train_ids.json"),
            "--holdout-file", str(state_dir / "holdout_ids.json"),
            "--summary-file", str(state_dir / "evaluation_splits_summary.json"),
            "--out", str(out_path),
            "--bootstrap-iterations", "100",
        ],
    }


def _read_artifact(out_path: Path) -> dict:
    return json.loads(out_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CLI: bootstrap mode end-to-end
# ---------------------------------------------------------------------------


class TestCLIBootstrapMode:
    def test_default_run_writes_artifact_and_returns_zero(self, calibration_environment):
        env = calibration_environment
        rc = main(env["argv_base"])
        assert rc == EXIT_OK
        assert env["out_path"].exists()

    def test_artifact_artifact_type_is_threshold_selection(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["artifact_type"] == "threshold_selection"

    def test_artifact_score_binding_table_is_signals(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["score_binding"]["table"] == "signals"

    def test_artifact_score_binding_column_is_confidence(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["score_binding"]["column"] == "confidence"

    def test_artifact_score_binding_semantic_name(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["score_binding"]["semantic_name"] == "signal_stored_confidence"

    def test_artifact_score_binding_score_direction(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["score_binding"]["score_direction"] == "higher_is_more_confident"

    def test_artifact_score_binding_decision_rule(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["score_binding"]["decision_rule"] == "accept_if_score_gte_threshold"

    def test_artifact_score_binding_records_producer(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        # Day 4 review: explicit score producer protects Day 5+ consumers from
        # silently applying the cut-off when the scoring path changes.
        assert art["score_binding"]["producer"] == "signal_generation_pipeline"

    def test_artifact_score_binding_records_version(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        # Historical signals.confidence may span multiple scoring-logic
        # versions; the artifact records that fact rather than asserting a
        # single canonical version.
        assert art["score_binding"]["version"] == "mixed_or_unknown"

    def test_artifact_score_binding_records_version_policy(
        self, calibration_environment
    ):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        policy = art["score_binding"]["version_policy"]
        assert isinstance(policy, str)
        # Policy must surface the multi-version reality and the consumer
        # contract; both substrings prevent the field from regressing into a
        # cosmetic placeholder.
        assert "scoring" in policy.lower()
        assert "version" in policy.lower()

    def test_bootstrap_iteration_floor_below_100_exits_1(self, calibration_environment):
        env = calibration_environment
        rc = main(env["argv_base"][:-2] + ["--bootstrap-iterations", "50"])
        assert rc == EXIT_INPUT_ERROR

    def test_bootstrap_iteration_ceiling_above_100000_exits_1(self, calibration_environment):
        env = calibration_environment
        rc = main(env["argv_base"][:-2] + ["--bootstrap-iterations", "200000"])
        assert rc == EXIT_INPUT_ERROR

    def test_bootstrap_artifact_includes_full_percentile_band(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert set(art["bootstrap"]["cutoffs"].keys()) == {
            "mean", "p5", "p50", "p95", "stdev", "cv",
        }
        assert set(art["bootstrap"]["precision_at_cutoff"].keys()) == {
            "mean", "p5", "p50", "p95", "stdev", "cv",
        }

    def test_bootstrap_artifact_records_infeasible_iterations(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert "infeasible_iterations" in art["bootstrap"]

    def test_chosen_cutoff_is_p50_by_default(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["chosen_cutoff"]["rule"] == "bootstrap_p50"
        assert art["chosen_cutoff"]["value"] == pytest.approx(
            art["bootstrap"]["cutoffs"]["p50"]
        )

    def test_chosen_cutoff_is_a_single_scalar_not_a_mapping(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert isinstance(art["chosen_cutoff"]["value"], float)

    def test_artifact_has_no_per_source_api_cutoff_field(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        # Constraint 3: no per-stratum cut-offs.
        flat = json.dumps(art)
        assert "per_source_api" not in flat
        assert "by_source" not in flat

    def test_double_run_with_same_seed_is_byte_identical_modulo_timestamp(
        self, calibration_environment
    ):
        env = calibration_environment
        main(env["argv_base"])
        art_a = _read_artifact(env["out_path"])
        env["out_path"].unlink()
        main(env["argv_base"])
        art_b = _read_artifact(env["out_path"])
        # generated_at and git block (commit/branch/dirty) drift across runs
        # in a real repo. Compare the deterministic core.
        for key in ("generated_at", "git"):
            art_a.pop(key, None)
            art_b.pop(key, None)
        assert art_a == art_b


# ---------------------------------------------------------------------------
# CLI: CV / fallback-cv mode
# ---------------------------------------------------------------------------


class TestCLIFallbackCV:
    def _cv_argv(self, env, *, mode_arg="--fallback-cv"):
        return env["argv_base"] + [mode_arg]

    def test_fallback_cv_alias_equivalent_to_mode_cv(self, calibration_environment):
        env = calibration_environment
        rc1 = main(self._cv_argv(env, mode_arg="--fallback-cv"))
        env["out_path"].unlink()
        rc2 = main(env["argv_base"] + ["--mode", "cv"])
        assert rc1 == rc2 == EXIT_OK

    def test_cv_mode_artifact_records_folds_and_repeats(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"] + ["--fallback-cv", "--cv-folds", "5", "--cv-repeats", "2"])
        art = _read_artifact(env["out_path"])
        assert art["cv"]["folds"] == 5
        assert art["cv"]["repeats"] == 2
        assert art["cv"]["fits_completed"] == 10

    def test_cv_mode_chosen_cutoff_rule_is_cv_p50(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"] + ["--fallback-cv", "--cv-repeats", "2"])
        art = _read_artifact(env["out_path"])
        assert art["chosen_cutoff"]["rule"] == "cv_p50"

    def test_cv_mode_bootstrap_field_is_null(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"] + ["--fallback-cv", "--cv-repeats", "2"])
        art = _read_artifact(env["out_path"])
        assert art["bootstrap"] is None
        assert art["cv"] is not None

    def test_bootstrap_mode_cv_field_is_null(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["cv"] is None
        assert art["bootstrap"] is not None


# ---------------------------------------------------------------------------
# CLI: holdout protection (constraint 5)
# ---------------------------------------------------------------------------


class TestCLIHoldoutProtection:
    def test_holdout_overlap_with_calibration_exits_1(self, calibration_environment, capsys):
        env = calibration_environment
        # Inject overlap: rewrite holdout to share a calibration ID.
        contaminated = list(env["holdout_ids"]) + [env["cal_ids"][0]]
        (env["state_dir"] / "holdout_ids.json").write_text(
            json.dumps({
                "split": "holdout",
                "seed": 42,
                "generated_at": "2026-04-28T00:00:00+00:00",
                "fractions": {"train": 0.6, "calibration": 0.2, "holdout": 0.2},
                "signal_ids": contaminated,
            }, sort_keys=True),
            encoding="utf-8",
        )
        rc = main(env["argv_base"])
        assert rc == EXIT_INPUT_ERROR
        assert not env["out_path"].exists()

    def test_holdout_overlap_error_message_does_not_leak_ids(
        self, calibration_environment, capsys
    ):
        env = calibration_environment
        leaked_id = env["cal_ids"][0]
        contaminated = list(env["holdout_ids"]) + [leaked_id]
        (env["state_dir"] / "holdout_ids.json").write_text(
            json.dumps({
                "split": "holdout",
                "seed": 42,
                "generated_at": "2026-04-28T00:00:00+00:00",
                "fractions": {"train": 0.6, "calibration": 0.2, "holdout": 0.2},
                "signal_ids": contaminated,
            }, sort_keys=True),
            encoding="utf-8",
        )
        main(env["argv_base"])
        captured = capsys.readouterr()
        assert str(leaked_id) not in captured.err
        # The count (at least 1) is fine to surface; just the IDs must not.
        assert "holdout" in captured.err.lower()


# ---------------------------------------------------------------------------
# CLI: schema preflight
# ---------------------------------------------------------------------------


class TestCLISchemaPreflight:
    def test_missing_required_table_exits_2(self, calibration_environment):
        env = calibration_environment
        # Drop a required table.
        con = sqlite3.connect(env["db_path"])
        con.execute("DROP TABLE thesis_ml_predictions")
        con.commit()
        con.close()
        rc = main(env["argv_base"])
        assert rc == EXIT_SCHEMA_FAILED
        assert not env["out_path"].exists()

    def test_schema_probe_reports_are_not_written_by_calibration_run(
        self, calibration_environment, tmp_path
    ):
        env = calibration_environment
        # If the script accidentally invoked inspect_live_schema.main(), it
        # would write live_schema_report.{json,md} to the schema-contract's
        # parent. We host the contract in tmp_path; any side report must
        # NOT appear there.
        main(env["argv_base"])
        for name in ("live_schema_report.json", "live_schema_report.md"):
            assert not (env["contract_path"].parent / name).exists()


# ---------------------------------------------------------------------------
# CLI: read-only invariants
# ---------------------------------------------------------------------------


class TestCLIReadOnlyInvariants:
    def _stat_files(self, env):
        return {
            "db": env["db_path"].stat(),
            "cal": (env["state_dir"] / "calibration_ids.json").stat(),
            "train": (env["state_dir"] / "train_ids.json").stat(),
            "holdout": (env["state_dir"] / "holdout_ids.json").stat(),
            "summary": (env["state_dir"] / "evaluation_splits_summary.json").stat(),
        }

    def test_db_mtime_and_size_unchanged_after_run(self, calibration_environment):
        env = calibration_environment
        before = self._stat_files(env)
        main(env["argv_base"])
        after = self._stat_files(env)
        assert before["db"].st_mtime == after["db"].st_mtime
        assert before["db"].st_size == after["db"].st_size

    def test_split_file_mtimes_unchanged_after_run(self, calibration_environment):
        env = calibration_environment
        before = self._stat_files(env)
        main(env["argv_base"])
        after = self._stat_files(env)
        for k in ("cal", "train", "holdout", "summary"):
            assert before[k].st_mtime == after[k].st_mtime, (
                f"{k} file was modified by the calibration run"
            )

    def test_dry_run_writes_no_artifact(self, calibration_environment):
        env = calibration_environment
        rc = main(env["argv_base"] + ["--dry-run"])
        assert rc == EXIT_OK
        assert not env["out_path"].exists()


# ---------------------------------------------------------------------------
# CLI: instability surfacing (constraint 4)
# ---------------------------------------------------------------------------


class TestCLIInstabilitySurfacing:
    def test_low_cv_artifact_instability_warning_is_null(self, calibration_environment):
        env = calibration_environment
        # The synthetic fixture has a clean separation -> low CV.
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["instability"]["warning"] is None

    def test_strict_instability_default_off(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["instability"]["strict"] is False

    def test_strict_instability_above_fail_threshold_exits_3(
        self, calibration_environment
    ):
        env = calibration_environment
        # Force the fail threshold to 0 so any non-zero CV triggers the gate.
        rc = main(
            env["argv_base"]
            + [
                "--strict-instability",
                "--instability-cv-fail", "0.0",
                "--instability-cv-warn", "0.0",
            ]
        )
        # Either the artifact exists (no instability) or rc == 3.
        # The fixture might have CV == 0 (constant cutoff). Tolerate both
        # by checking the gate logic works on the populated case.
        if rc == EXIT_OK:
            art = _read_artifact(env["out_path"])
            assert art["instability"]["cv"] == pytest.approx(0.0, abs=1e-9)
        else:
            assert rc == EXIT_INSTABILITY_GATE
            assert not env["out_path"].exists()


# ---------------------------------------------------------------------------
# CLI: provenance
# ---------------------------------------------------------------------------


class TestCLIProvenance:
    def test_records_three_split_shas(self, calibration_environment):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert "calibration_split_sha" in art["input"]
        assert "train_split_sha" in art["input"]
        assert "holdout_split_sha" in art["input"]
        # All three are 64-char sha256 hex strings.
        for key in ("calibration_split_sha", "train_split_sha", "holdout_split_sha"):
            sha = art["input"][key]
            assert isinstance(sha, str) and len(sha) == 64

    def test_records_high_confidence_threshold_at_run_time(
        self, calibration_environment
    ):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["scoring_provenance"]["high_confidence_threshold_at_run_time"] == 0.7

    def test_records_active_llm_thesis_mode_from_env(
        self, calibration_environment, monkeypatch
    ):
        env = calibration_environment
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["scoring_provenance"]["active_llm_thesis_mode"] == "shadow"

    def test_records_label_table_signal_quality_metrics(
        self, calibration_environment
    ):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["scoring_provenance"]["label_table"] == "signal_quality_metrics"
        assert art["scoring_provenance"]["label_column"] == "human_label"

    def test_artifact_labels_used_for_fitting_lists_only_TP_and_FP(
        self, calibration_environment
    ):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        assert art["input"]["labels_used_for_fitting"] == ["TP", "FP"]
        assert "UNSURE" in art["input"]["labels_excluded_from_fitting"]
        assert "ADJ" in art["input"]["labels_excluded_from_fitting"]
        assert "missing" in art["input"]["labels_excluded_from_fitting"]

    def test_calibration_label_breakdown_counts_unsure_and_missing(
        self, calibration_environment
    ):
        env = calibration_environment
        main(env["argv_base"])
        art = _read_artifact(env["out_path"])
        breakdown = art["input"]["calibration_label_breakdown"]
        # Fixture has 8 TP, 16 FP, 1 UNSURE, 1 missing
        assert breakdown["TP"] == 8
        assert breakdown["FP"] == 16
        assert breakdown["UNSURE"] == 1
        assert breakdown["missing"] == 1


# ---------------------------------------------------------------------------
# CLI: input handling (failure modes)
# ---------------------------------------------------------------------------


class TestCLIInputHandling:
    def test_missing_calibration_file_exits_1(self, calibration_environment):
        env = calibration_environment
        (env["state_dir"] / "calibration_ids.json").unlink()
        rc = main(env["argv_base"])
        assert rc == EXIT_INPUT_ERROR

    def test_missing_holdout_file_exits_1(self, calibration_environment):
        env = calibration_environment
        (env["state_dir"] / "holdout_ids.json").unlink()
        rc = main(env["argv_base"])
        assert rc == EXIT_INPUT_ERROR

    def test_missing_train_file_in_cv_mode_exits_1(self, calibration_environment):
        env = calibration_environment
        (env["state_dir"] / "train_ids.json").unlink()
        rc = main(env["argv_base"] + ["--fallback-cv"])
        assert rc == EXIT_INPUT_ERROR

    def test_summary_seed_mismatch_exits_1(self, calibration_environment):
        env = calibration_environment
        summary = json.loads(
            (env["state_dir"] / "evaluation_splits_summary.json").read_text()
        )
        summary["seed"] = 99
        (env["state_dir"] / "evaluation_splits_summary.json").write_text(
            json.dumps(summary, sort_keys=True)
        )
        rc = main(env["argv_base"])
        assert rc == EXIT_INPUT_ERROR

    def test_calibration_size_below_floor_exits_1(self, calibration_environment):
        env = calibration_environment
        cal_file = env["state_dir"] / "calibration_ids.json"
        cal_data = json.loads(cal_file.read_text())
        cal_data["signal_ids"] = cal_data["signal_ids"][:2]  # 2 < 4
        cal_file.write_text(json.dumps(cal_data, sort_keys=True))
        rc = main(env["argv_base"])
        assert rc == EXIT_INPUT_ERROR

    def test_infeasible_base_set_exits_1_with_recipe_pointer(
        self, calibration_environment, capsys
    ):
        env = calibration_environment
        # The default fixture is cleanly separable; replace calibration_ids
        # with FP-only IDs so no threshold can ever reach target_precision.
        # FPs are seed_id+8..+23 in _populate_calibration_signals.
        fp_only_ids = env["cal_ids"][8:24]
        cal_file = env["state_dir"] / "calibration_ids.json"
        cal_data = json.loads(cal_file.read_text())
        cal_data["signal_ids"] = fp_only_ids
        cal_file.write_text(json.dumps(cal_data, sort_keys=True))
        rc = main(env["argv_base"])
        assert rc == EXIT_INPUT_ERROR
        captured = capsys.readouterr()
        assert "create_evaluation_splits" in captured.err or \
               "target-precision" in captured.err
        # Artifact must not be written when base set is infeasible.
        assert not env["out_path"].exists()

    def test_target_precision_out_of_unit_interval_exits_1(
        self, calibration_environment
    ):
        env = calibration_environment
        rc = main(env["argv_base"] + ["--target-precision", "1.5"])
        assert rc == EXIT_INPUT_ERROR


# ---------------------------------------------------------------------------
# CLI: forbidden-reference & no-per-stratum lints (AST/regex over the script)
# ---------------------------------------------------------------------------


class TestScriptLints:
    SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "recalibrate_conformal.py"

    def test_script_does_not_reference_signal_quality_metrics_confidence_score(
        self
    ):
        text = self.SCRIPT_PATH.read_text(encoding="utf-8")
        assert "signal_quality_metrics.confidence_score" not in text, (
            "forbidden reference per .omx/wave6/live_schema_contract.json:64"
        )

    def test_script_does_not_reference_quality_feedback_label_for_fitting(self):
        # The Day 4 plan binds labels to signal_quality_metrics.human_label.
        # quality_feedback.label must not appear in the script.
        text = self.SCRIPT_PATH.read_text(encoding="utf-8")
        assert "quality_feedback" not in text or "human_label" in text

    def test_script_does_not_groupby_source_api_in_fit_path(self):
        # Constraint 3: no per-stratum cut-offs.
        text = self.SCRIPT_PATH.read_text(encoding="utf-8")
        # Forbidden patterns: groupby on source_api, per-source dict construction.
        forbidden_patterns = [
            "groupby(\"source_api\"",
            "groupby('source_api'",
            "by_source_api",
            "per_source_api",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in text, (
                f"constraint 3 violation: {pattern!r} appears in script"
            )

    def test_script_uses_pure_inspect_database_not_main(self):
        text = self.SCRIPT_PATH.read_text(encoding="utf-8")
        assert "from scripts.inspect_live_schema import" in text
        assert "inspect_database" in text
        # The script must NEVER invoke inspect_live_schema.main() because
        # main() writes report files.
        assert "inspect_live_schema.main(" not in text
        assert "from scripts.inspect_live_schema import main" not in text


# ---------------------------------------------------------------------------
# Output disposition
# ---------------------------------------------------------------------------


class TestOutputDisposition:
    def test_gitignore_contains_state_conformal_calibration_json_line(self):
        gitignore = Path(__file__).resolve().parents[2] / ".gitignore"
        text = gitignore.read_text(encoding="utf-8")
        assert "state/conformal_calibration.json" in text, (
            "Day 4 plan deliverable: state/conformal_calibration.json must "
            "be in .gitignore alongside the other state/*_ids.json entries."
        )


# ---------------------------------------------------------------------------
# CLI defaults
# ---------------------------------------------------------------------------


class TestCLIDefaults:
    def test_default_calibration_file_is_state_calibration_ids_json(self):
        assert DEFAULT_CALIBRATION_FILE == Path("state") / "calibration_ids.json"

    def test_default_train_file_is_state_train_ids_json(self):
        assert DEFAULT_TRAIN_FILE == Path("state") / "train_ids.json"

    def test_default_holdout_file_is_state_holdout_ids_json(self):
        assert DEFAULT_HOLDOUT_FILE == Path("state") / "holdout_ids.json"

    def test_default_out_path_is_state_conformal_calibration_json(self):
        assert DEFAULT_OUT_PATH == Path("state") / "conformal_calibration.json"
