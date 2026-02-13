# Wave 5 — Drift + Hardening + UAT Implementation Plan

**Created:** 2026-02-10
**Revision:** v1 (initial)
**Depends on:** Wave 4 (COMPLETE, PR #39, 155 tests)
**Duration:** ~49h across 3 phases
**Branch:** `feature/wave5-drift-hardening`
**Related:** `docs/plans/2026-02-09-phase4-full-roadmap.md` (Wave 5 spec), `docs/plans/wave5-findings.md`

---

## Context

Wave 5 is the final wave of the v1.1.3 phased roadmap. Waves 0-4 are COMPLETE (1708+ tests, 40 migrations, merged to main). This wave delivers:
- **4e** — SPC-lite drift monitoring engine + canary expansion + alert workflow + dashboard
- **4f** — Migration downgrade tests, cross-phase integration suites, SLO baselines, runbooks
- **4g** — UAT checkpoint demo, feedback collection, documentation

The existing canary framework (Wave 0 scaffold + Wave 2 baseline) provides the foundation:
- `monitoring/canary_checker.py` (714 lines) — golden set, stratified re-scoring, CLI
- `monitoring/drift_detector.py` (313 lines) — 5 alert types, version-compatible comparison
- `api/routers/canary.py` (352 lines) — 4 endpoints, concurrency guard
- `canary_runs` + `canary_drift_alerts` tables (v38)
- `ops/quality/stats.py` — FP rate queries (compute on-the-fly)

**Key architectural decision:** Build SPC on a daily aggregation table (`quality_metrics_daily`) rather than scanning `signal_quality_metrics` on every check — 100x faster for 90-day baselines.

---

## Phase Structure

| Phase | Focus | Tasks | Est. | Tests |
|-------|-------|-------|------|-------|
| **W5-A** | SPC engine + canary expansion + alert workflow | W5.1–W5.6 | 14h | ~41 |
| **W5-B** | Dashboard + recommendations + CLI + integration | W5.7–W5.11 | 16h | ~40 |
| **W5-C** | Perf baselines + E2E + runbooks + docs | W5.12–W5.17 | 12h | ~17 |

---

## Phase W5-A: SPC Engine + Canary Expansion + Alert Workflow

### W5.1: Migration v41 — Drift monitoring DDL
**Status:** `pending`
**Create:** `storage/migrations/v41_drift_monitoring.py`
**Test:** `tests/storage/test_v41_migration.py` (8 tests)
**Modify:** `storage/signal_store.py` (register v41)

DDL:
1. `quality_metrics_daily` — pre-aggregated FP/TP rates per day. Columns: `metric_date` (UNIQUE), `overall_fp_rate`, `overall_tp_count`, `overall_fp_count`, `overall_labeled`, `collector_breakdown_json`, `archetype_breakdown_json`, `confidence_band_breakdown_json`, `created_at`.
2. Recreate `canary_drift_alerts` to add `'snoozed'` to CHECK constraint + `snoozed_until TEXT`, `snooze_count INTEGER DEFAULT 0`, `drift_category TEXT CHECK(... IN ('data_drift','concept_drift','model_drift'))`. Uses CREATE→INSERT→DROP→RENAME (SQLite can't ALTER CHECK).

### W5.2: Daily aggregation engine
**Status:** `pending`
**Create:** `monitoring/daily_aggregator.py`
**Test:** `tests/monitoring/test_daily_aggregator.py` (6 tests)
**Depends on:** W5.1

Functions:
- `aggregate_daily_metrics(store, date)` — query `signal_quality_metrics` + `signals` for date, compute breakdowns, store to `quality_metrics_daily`
- `backfill_daily_metrics(store, days=90)` — backfill historical data, skip existing dates

### W5.3: SPC-lite engine
**Status:** `pending`
**Create:** `monitoring/spc_monitor.py`
**Test:** `tests/monitoring/test_spc_monitor.py` (8 tests)
**Depends on:** W5.2

`SPCMonitor` class:
- `compute_control_limits(store, metric, lookback_days=30)` → mean, UCL (mean+3σ), LCL (mean-3σ)
- `check_metric(store, metric, current_value)` → list of `SPCAlert`
- `detect_trends(store, metric, window=7)` → monotonic trend detection (7+ consecutive)
- `compute_calibration_curve(store, bins=10)` → observed vs expected TP rate per bin, ECE

Metrics: `overall_fp_rate`, `collector_yield`, `quarantine_regret`, `confidence_calibration`.

### W5.4: Expand canary stratification + separate drift types
**Status:** `pending`
**Modify:** `monitoring/canary_checker.py`, `monitoring/drift_detector.py`
**Extend:** `tests/monitoring/test_canary_stratified.py`, `tests/monitoring/test_drift_detector.py` (7 tests)
**Depends on:** W5.1

- Recency bucket stratification (30/60/90 days) in `build_stratified_golden_set()`
- `drift_category` classification in `DriftAlert`: pass_rate_drop→concept_drift, individual_drift→model_drift, archetype_regression→data_drift
- Store `drift_category` via updated `store_drift_alerts()`

### W5.5: Alert escalation workflow
**Status:** `pending`
**Create:** `monitoring/alert_escalation.py`
**Test:** `tests/monitoring/test_alert_escalation.py` (7 tests)
**Depends on:** W5.1

Functions:
- `acknowledge_alert(store, alert_id, operator, reason)` — open→acknowledged + audit event
- `snooze_alert(store, alert_id, operator, hours)` — →snoozed, set snoozed_until, snooze_count++
- `resolve_alert(store, alert_id, operator, resolution)` — →resolved + audit event
- `auto_reopen_expired_snoozes(store)` — snoozed past deadline→open
- `compute_mtta(store, lookback_days=30)` — mean time to acknowledge (mean, p50, p95)

### W5.6: Alert API endpoints
**Status:** `pending`
**Modify:** `api/routers/canary.py`
**Extend:** `tests/api/test_canary_router.py` (5 tests)
**Depends on:** W5.5

New endpoints:
- `POST /canary/drift-alerts/{id}/acknowledge` (Permission.CANARY_RUN)
- `POST /canary/drift-alerts/{id}/snooze` (Permission.CANARY_RUN)
- `POST /canary/drift-alerts/{id}/resolve` (Permission.CANARY_RUN)
- `GET /canary/drift-alerts/stats` (Permission.VIEW) — MTTA, open/ack/snoozed/resolved counts

**Phase W5-A gate:** v41 applied, SPC detects out-of-control metric, alert ack/snooze/resolve tested, ~41 tests pass.

---

## Phase W5-B: Dashboard + Recommendations + CLI + Integration

### W5.7: Drift recommendation engine
**Status:** `pending`
**Create:** `monitoring/drift_recommendations.py`
**Test:** `tests/monitoring/test_drift_recommendations.py` (5 tests)

`generate_recommendations(store, lookback_days=7)` → list of `DriftRecommendation`.
Pattern detection:
- Repeated archetype_regression → "Expand golden set for {archetype}"
- pass_rate_drop + specific collector → "Investigate {collector}"
- Sustained FP rate trend → "Adjust confidence threshold"
- High calibration error → "Recalibrate scoring model"

### W5.8: Drift dashboard view
**Status:** `pending`
**Create:** `dashboard/views/drift_monitoring.py`
**Test:** `tests/dashboard/test_drift_monitoring_view.py` (6 tests)
**Modify:** `dashboard/app.py` (wire nav entry)
**Depends on:** W5.3, W5.5, W5.7

4 tabs (Altair charts, following `dashboard/views/ops_health.py` pattern):
1. **SPC Charts** — FP rate, collector yield over time (faceted line charts with UCL/LCL bands)
2. **Canary Status** — Latest run, pass rate, stratification breakdown
3. **Alert Timeline** — Open/ack/snoozed/resolved with action buttons
4. **Recommendations** — Priority-sorted cards with evidence + action templates

### W5.9: CLI integration — `drift` subcommand
**Status:** `pending`
**Modify:** `run_pipeline.py`
**Create:** `tests/cli/test_drift_commands.py` (7 tests)

Subcommands (2-level argparse, following `hunter` pattern at line ~2280):
- `drift check` — Run SPC checks on latest daily metrics
- `drift canary` — Trigger canary run
- `drift alerts` — List open drift alerts
- `drift ack <id>` — Acknowledge alert
- `drift snooze <id> --hours 24` — Snooze alert
- `drift resolve <id> --reason "..."` — Resolve alert
- `drift recommend` — Generate recommendations
- `drift calibrate` — Show calibration curve

### W5.10: Migration downgrade tests
**Status:** `pending`
**Create:** `tests/storage/test_migration_downgrade.py` (10 tests)

Test v41→v40, v40→v39, v39→v38, v38→v37, v37→v36, v36→v35 rollback paths.
Verify: table drops, data preservation, index recreation, FK integrity.

### W5.11: Cross-phase integration suites
**Status:** `pending`
**Create:** `tests/integration/test_cross_phase_m1.py` (6 tests)
**Create:** `tests/integration/test_cross_phase_m2.py` (6 tests)

**Suite M1** (Triage → ACH → Drift): signal creation → triage → approve → ACH build → canary run → drift check → alert.
**Suite M2** (Hunter → Triage → ACH → Merge → Drift): hunter generate → run → promote → triage → merge → drift → recommendation.

**Phase W5-B gate:** Dashboard renders 4 tabs, CLI commands work, downgrade tests pass, M1+M2 pass, cumulative ~81 tests.

---

## Phase W5-C: Perf + E2E + Docs + UAT

### W5.12: Load/perf baselines
**Status:** `pending`
**Create:** `tests/performance/test_wave5_slos.py` (6 tests)

SLOs: drift check <2s, alert listing <500ms, daily aggregation <5s, recommendation <3s, calibration <2s, dashboard <3s.
10x data fixture: 500 signals, 300 labeled, 50 canary runs, 200 drift alerts.

### W5.13: E2E drift workflow test
**Status:** `pending`
**Create:** `tests/e2e/test_drift_workflow.py` (8 tests)

FastAPI TestClient + Streamlit mock (no Playwright). Full lifecycle: trigger canary → detect drift → view alerts → ack → snooze → resolve → verify audit trail + RBAC enforcement.

### W5.14: Feature guard for drift monitoring
**Status:** `pending`
**Modify:** `workflows/feature_guards.py`, `utils/config_validator.py`
**Extend:** `tests/workflows/test_feature_guards.py` (3 tests)

Add `WriteFeature.DRIFT_MONITORING = "drift_monitoring"` + `DRIFT_MONITORING_ENABLED` env var.
Default: `disabled`. Modes: `disabled`, `active`.

### W5.15: Incident runbooks
**Status:** `pending`
**Create:**
- `docs/runbooks/drift-escalation.md`
- `docs/runbooks/canary-failure.md`
- `docs/runbooks/spc-out-of-control.md`

### W5.16: Operator guide + architecture overview
**Status:** `pending`
**Create:**
- `docs/operator-guide.md` — Triage, batch publish, hunter, merge, drift, ACH workflows
- `docs/architecture-overview.md` — System context, components, data flow, key patterns

### W5.17: UAT checkpoint + feedback
**Status:** `pending` (non-code)

Demo walkthrough + feedback collection → refinement mini-sprint.

**Phase W5-C gate:** SLOs met, E2E passes, docs reviewed, cumulative ~98 tests, 1708 existing tests green.

---

## Dependency Graph

```
W5.1 (v41 migration)
  ├─► W5.2 (daily aggregator) ─► W5.3 (SPC engine) ─┐
  ├─► W5.4 (canary + drift types)                    │
  ├─► W5.5 (alert escalation) ─► W5.6 (alert API)    │
  │                                                    ▼
  │   ┌─── W5.7 (recommendations) ◄── W5.4           W5.8 (dashboard) ◄── W5.5, W5.7
  │   │                                               │
  │   └─────────────────────────────────────────────── W5.9 (CLI) ◄── W5.3, W5.5, W5.7
  │
  └─► W5.10 (downgrade tests) — independent
      W5.11 (cross-phase) — depends on all W5-A
      W5.12-W5.17 — depends on W5-B complete
```

**Critical path:** W5.1 → W5.2 → W5.3 → W5.8 → W5.9 → W5.11

---

## Reusable Patterns

| Pattern | Source | Reuse In |
|---------|--------|----------|
| Altair faceted charts | `dashboard/views/ops_health.py:405-434` | W5.8 |
| 2-level argparse | `run_pipeline.py:2280-2293` | W5.9 |
| WriteFeature guard | `workflows/feature_guards.py:31-141` | W5.14 |
| FP rate time-window query | `ops/quality/stats.py:30-113` | W5.2 |
| DDL + table recreation | `v40_merge_lifecycle.py` | W5.1 |
| Dashboard mock | `tests/dashboard/test_triage_views.py` | W5.8 tests |
| Cursor pagination | `api/pagination.py` | W5.6 |
| Audit event recording | `storage/audit_events.py:record_event()` | W5.5 |

---

## Verification

```bash
# Phase W5-A tests
pytest tests/storage/test_v41_migration.py tests/monitoring/test_daily_aggregator.py tests/monitoring/test_spc_monitor.py tests/monitoring/test_alert_escalation.py -v

# Phase W5-B tests
pytest tests/monitoring/test_drift_recommendations.py tests/dashboard/test_drift_monitoring_view.py tests/cli/test_drift_commands.py tests/storage/test_migration_downgrade.py tests/integration/test_cross_phase_m1.py tests/integration/test_cross_phase_m2.py -v

# Phase W5-C tests
pytest tests/performance/test_wave5_slos.py tests/e2e/test_drift_workflow.py -v

# Full regression
pytest tests/monitoring/ tests/api/test_canary_router.py tests/workflows/test_feature_guards.py -v

# CLI smoke test
python run_pipeline.py drift check --db signals.db
python run_pipeline.py drift alerts --db signals.db
```

---

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |
