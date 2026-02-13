# Wave 5 — Drift + Hardening + UAT: Findings

**Created:** 2026-02-10
**Updated:** 2026-02-10

---

## F1: Existing Drift Infrastructure (Strong Foundation)

### What Exists
- **`monitoring/drift_detector.py`** (313 lines) — Version-compatible canary drift analysis. Compares current vs baseline canary runs with matching `golden_set_hash`/`config_hash`. Generates 5 alert types: `pass_rate_drop`, `individual_drift`, `archetype_regression`, `pass_rate_improvement`, `archetype_improvement`. Severity: critical (>15% drop), warning (>5%), info (improvement). Stores alerts to `canary_drift_alerts` table.
- **`monitoring/canary_checker.py`** (714 lines) — Full canary framework: golden set definition, stratified re-scoring, CLI (run/status/history). Wave 0 scaffold + Wave 2 extensions.
- **`monitoring/label_definitions.py`** (149 lines) — 3-layer label taxonomy (operator/outcome/gold).
- **`api/routers/canary.py`** (352 lines) — 4 endpoints: GET /status, GET /runs, POST /run, GET /drift-alerts. Includes concurrency guard and drift detection on each run.
- **`canary_drift_alerts`** table (v38) — already stores alerts with `status`, `acknowledged_by`, `acknowledged_at`.

### What's Missing (Wave 5 must create)
- **`monitoring/spc_monitor.py`** — Statistical Process Control engine for time-series metrics (FP rate, collector yield, quarantine regret, confidence calibration).
- **`monitoring/alert_escalation.py`** — Severity classification, ack/snooze workflow, MTTA tracking.
- **`monitoring/drift_recommendations.py`** — Automated remediation suggestions citing evidence.
- **`dashboard/views/drift_monitoring.py`** — Altair trend charts, alert timeline, canary status, recommendations.
- CLI integration for `drift check|canary|alerts` in `run_pipeline.py`.

### Assessment
Wave 2 built excellent point-in-time drift detection (canary run → compare → alert). Wave 5 needs to add the *time-series* dimension (SPC), *workflow* dimension (escalation/ack/snooze), and *actionability* dimension (recommendations).

---

## F2: Database Schema — No New Migration Needed for Core SPC

The `canary_drift_alerts` table (v38) already has:
- `alert_type`, `severity`, `status`, `acknowledged_by`, `acknowledged_at`
- Status values include `open` (new alerts default to this)

What's **potentially** needed (new migration v41):
- `spc_metrics` table — time-series metric snapshots for SPC charts
- `alert_escalation_log` — escalation events (who was notified, when, channel)
- Add `snoozed_until`, `escalation_level` columns to `canary_drift_alerts`

Alternatively, some SPC data could be derived from existing `canary_runs` history + `signal_quality_metrics` without new tables.

**Decision (D1):** Row-based metric/segment model (`metric_name`, `segment_type`, `segment_key`, `value`, `n`) with UNIQUE composite key. Reviews converged that JSON "God row" is opaque and unqueryable. See plan v2 for full schema.

---

## F3: Existing Test Coverage for Monitoring

| File | Tests | Coverage |
|------|-------|----------|
| `tests/monitoring/test_canary_checker.py` | ~12 | Golden set, re-scoring, verdicts |
| `tests/monitoring/test_drift_detector.py` | ~22 | 5 alert types, severity thresholds, no-baseline |
| `tests/monitoring/test_canary_stratified.py` | ~25 | Stratified golden sets, archetype buckets |
| `tests/monitoring/test_label_definitions.py` | ~5 | Label taxonomy |
| `tests/api/test_canary_router.py` | ~16 | API endpoints, concurrency guard |

Wave 5 should add ~80-100 new tests across SPC, escalation, recommendations, dashboard, integration.

---

## F4: Cross-Phase Integration Test Gap

Existing integration tests:
- `test_wave4_cross_phase.py` (511 lines) — Wave 4 cross-phase coverage
- `test_merge_rollback.py` (460 lines) — Merge rollback scenarios

Roadmap specifies two new integration suites:
- **Suite M1:** Triage → ACH → Drift (signal flows through full read path)
- **Suite M2:** Hunter → Triage → ACH → Entity Merge → Drift (full write path)

These require orchestrating across 5+ modules. Need to determine: mock boundaries vs real DB vs fixture factory approach.

---

## F5: Performance/SLO Test Baseline

Existing: `tests/performance/test_phase1a_slos.py` (485 lines, 13 tests).

Wave 5 needs SLO tests for:
- Canary run latency (<5s for 50-signal golden set)
- SPC metric computation (<1s)
- Drift detection + alert store (<500ms)
- Dashboard view render (<2s)
- Cross-phase suite total latency

---

## F6: CLI Integration Gap

`run_pipeline.py` has 25+ subcommands but no `drift` subcommand. The canary checker has its own standalone CLI (`python -m monitoring.canary_checker`) but isn't integrated into the main CLI.

Wave 5 task 4e.8 calls for: `run_pipeline.py drift check|canary|alerts`.

---

## F7: Dashboard Technology — Altair for Charts

Roadmap specifies Altair trend charts for drift monitoring dashboard. Need to verify:
- Altair is already a dependency (it comes with Streamlit)
- Chart patterns: time-series line charts (pass rate over time), alert frequency bar charts, calibration reliability diagrams

---

## F8: UAT / 4g Scope Consideration

4g tasks are stakeholder-facing:
- 4g.1: Checkpoint demo — structured walkthrough
- 4g.2: Feedback collection + refinement
- 4g.3: Documentation — operator guide, architecture overview

These are not code-heavy. Plan should budget time but keep implementation focus on 4e + 4f.

---

## F9: Existing Runbooks

Only `docs/runbooks/migration-rollback.md` exists. Wave 5 (4f.5) needs:
- Incident runbooks (alert triage, canary failure investigation)
- Canary release policy
- Drift remediation playbook
