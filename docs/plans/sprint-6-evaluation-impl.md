# Task Plan: Sprint 6 - Evaluation & Calibration

## Goal
Build evaluation framework with gold set management, metrics tracking, and drift detection alerts.

## Current Phase
COMPLETE

## Phases

### Phase 1: Gold Set Manager
- [x] Create `utils/gold_set_manager.py`
- [x] Implement `GoldSetManager` class with CRUD operations
- [x] Add import/export from JSON/CSV
- [x] Add annotation helpers (annotator workflow)
- **Status:** complete
- **Tests:** 23 passing

### Phase 2: Evaluation Runner
- [x] Create `utils/evaluation_runner.py`
- [x] Implement `EvaluationRunner` class
- [x] Compute extraction metrics (F1, precision, recall)
- [x] Compute similarity metrics (top-k recall)
- [x] Compute investor match metrics
- [x] Store evaluation runs to DB
- **Status:** complete
- **Tests:** 25 passing

### Phase 3: Drift Detector
- [x] Create `utils/drift_detector.py`
- [x] Implement threshold checking logic
- [x] Support consecutive run detection (confidence collapse)
- [x] Generate drift alerts
- [x] Slack integration for red alerts
- **Status:** complete
- **Tests:** 25 passing

### Phase 4: CLI Integration
- [x] Add `run_pipeline.py evaluate` command
- [x] Add `run_pipeline.py gold-set` command group
- [x] Gold set subcommands: list, stats, export, import
- **Status:** complete

### Phase 5: Testing
- [x] Unit tests for GoldSetManager (23 tests)
- [x] Unit tests for EvaluationRunner (25 tests)
- [x] Unit tests for DriftDetector (25 tests)
- [x] Integration tests included in each test file
- **Status:** complete
- **Total tests:** 73 passing

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| JSON gold set format | Easy to version control, human-readable |
| Separate evaluation runs table | Track metrics over time for trend analysis |
| Threshold-based drift detection | Simple, interpretable, tunable |
| Slack alerts for red severity | Immediate notification for critical regressions |
| Metric prefix with category (extraction_f1) | Avoid name collisions, explicit naming |

## Drift Detection Thresholds

| Alert Type | Metric | Threshold | Severity |
|------------|--------|-----------|----------|
| extraction_f1_drop | F1 vs baseline | > 5 points | RED |
| abstention_rate_spike | Abstention rate | > 25% | RED |
| abstention_rate_increase | Abstention vs baseline | > 8 points | YELLOW |
| top10_recall_drop | Recall vs baseline | > 7 points | RED |
| top10_recall_absolute | Recall | < 60% | RED |
| confidence_collapse | Median confidence | < 55% (3x) | RED |

## Gold Set Categories

| Category | Description | Target Count |
|----------|-------------|--------------|
| core_sector | Clear thesis fit (CPG, health tech, travel) | 40 |
| long_tail | Edge cases, niche sectors | 20 |
| ambiguous | Borderline thesis fit | 20 |
| hard_negative | Clear non-fit (B2B, crypto, enterprise) | 20 |

## Files Created/Modified
- `utils/gold_set_manager.py` (NEW)
- `utils/evaluation_runner.py` (NEW)
- `utils/drift_detector.py` (NEW)
- `tests/utils/test_gold_set_manager.py` (NEW)
- `tests/utils/test_evaluation_runner.py` (NEW)
- `tests/utils/test_drift_detector.py` (NEW)
- `run_pipeline.py` (MODIFIED) - Added CLI commands

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Metric name mismatch (f1 vs extraction_f1) | 1 | Prefixed metrics with category name in flattening logic |
| GoldSetStats.investor_labels missing | 1 | Changed to stats.total_investor_labels |
