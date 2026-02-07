# ML Classification Improvements — Integrated Review & Implementation

**Date:** 2026-02-07
**Status:** Implemented (Phase 1)
**Branch:** claude/review-ml-classification-J9Ewi

## Problem Statement

The keyword matcher has catastrophic 4% recall (POSITIVE mean=0.046, NEGATIVE mean=0.056).
Score distributions are nearly identical, making threshold-only tuning futile.
An ML classifier is needed as a false-negative rescue layer.

## Three-Review Consensus

Three independent reviews of the original plan were synthesized. These items had
**unanimous agreement** and were all implemented:

| Issue | Review 1 | Review 2 | Review 3 | Resolution |
|-------|----------|----------|----------|------------|
| Training/serving skew risk | Yes (critical) | — | Yes | `utils/ml_text_builder.py` shared helper |
| ML init blocked by v2 early return | Yes (bug) | — | — | ML init BEFORE v2 early return |
| Ambiguous band [0.05, 0.4) too narrow | Yes | Yes | — | Widened: no lower bound on keyword score |
| Arbitrary `*0.8` scaling | Yes | — | Yes | Removed: `max(keyword_score, ml_prob)` |
| Model versioning like policy_hash | Yes | Yes | Yes | `model_id` = SHA-256 of file, 16 chars |
| Threshold config coupling risk | — | — | Yes (critical) | Split: `MatchingThresholdConfig` + `WorkflowThresholdConfig` |
| Convenience function model reload | Yes (perf) | — | — | Module-level singleton `_default_matcher` |
| Circuit breaker + latency budget | — | Yes | Yes | 5-failure breaker + 500ms latency warning |
| ML predictions table needs indexing | — | Yes | Yes | 4 indexes on thesis_ml_predictions |

## Deliberately Deferred Items

| Item | Reason |
|------|--------|
| LLM probability calibration | <200 labeled LLM outputs |
| StructuredFeatureExtractor / FeatureUnion | Phase 2 after text-only baseline proves value |
| Category-specific calibrators | <150 samples per category |
| Model drift detection (KL-divergence) | Phase 2 after shadow data collection |
| Canary/A/B deployment | Premature until shadow→live promotion |

## Files Modified

| File | Change |
|------|--------|
| `utils/runtime_controls.py` | Added `ml_enablement`, `ml_model_path`, ML resolution + properties |
| `utils/thesis_matcher.py` | ML init, `_maybe_apply_ml()`, `_attach_ml_shadow_diff()`, `_rebuild_fit_with_ml_rescue()`, `_compute_ml_score()`, singleton `_default_matcher` |
| `utils/thesis_filter.py` | Added `ml_shadow` to `ThesisFilterResult`, wired extraction in both return paths |
| `storage/migrations/quality_tables.py` | Added `thesis_ml_predictions` table with 4 indexes |
| `requirements.txt` | Added `scikit-learn>=1.3.0`, `joblib>=1.3.0` |

## New Files

| File | Purpose |
|------|---------|
| `utils/ml_text_builder.py` | Shared text builder (prevents training/serving skew) |
| `utils/ml_thesis_model.py` | MLThesisModel wrapper (TF-IDF + LogisticRegression) |
| `utils/threshold_config.py` | Split threshold configs (Matching vs Workflow) |
| `scripts/threshold_analysis.py` | Threshold sweep diagnostic script |
| `scripts/train_thesis_model.py` | ML model training with cross-validation |
| `tests/utils/test_ml_text_builder.py` | 23 tests |
| `tests/utils/test_ml_thesis_model.py` | 15 tests |
| `tests/utils/test_thesis_matcher_ml_shadow.py` | 18 tests |
| `tests/utils/test_threshold_config.py` | 8 tests |
| `tests/utils/test_runtime_controls_ml.py` | 21 tests |

## Test Results

- **85 new tests**: All passing
- **115 existing tests**: All passing (zero regressions)
- **Total**: 200 tests green

## Quick Commands

```bash
# Run ML-related tests
python -m pytest tests/utils/test_ml_text_builder.py tests/utils/test_ml_thesis_model.py tests/utils/test_thesis_matcher_ml_shadow.py tests/utils/test_threshold_config.py tests/utils/test_runtime_controls_ml.py -v

# Threshold analysis
python scripts/threshold_analysis.py --ground-truth datasets/thesis_ground_truth.jsonl

# Train ML model
python scripts/train_thesis_model.py --ground-truth datasets/thesis_ground_truth.jsonl --out models/thesis_classifier.joblib --category-analysis

# Run pipeline with ML shadow mode
ML_ENABLEMENT=shadow python run_pipeline.py full --collectors github --dry-run

# Disable ML (rollback)
ML_ENABLEMENT=disabled python run_pipeline.py full --collectors github
```

## Architecture Decision: Append-After Pattern

All three reviews endorsed the "append-after" approach over restructuring `score()`:

```
score() existing flow:
  empty text → return (no ML)
  domain blacklisted → return (no ML, hard rejection)
  compute v1
  if v2 disabled → return _maybe_apply_ml(fit_v1)    ← ML applied here
  compute v2
  if shadow → return _maybe_apply_ml(fit_v1)          ← ML applied here
  if live → return _maybe_apply_ml(fit_v2)             ← ML applied here
```

ML operates as a post-processing rescue layer, not a feature generator within
the keyword matching flow. This preserves the proven v1/v2 control flow exactly.
