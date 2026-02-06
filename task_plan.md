# Task Plan: Ops Layer Phase 5 — Advanced Monitoring Rules

## Goal
Extend the ops monitoring layer with configurable alert rules (DB-stored, JSON DSL), scheduler-aware alerts, metric history persistence for trend detection, rule CRUD (CLI + API + dashboard), and backward-compatible integration with the existing 8 builtin rules.

## Current Phase
Phase 5

## Context: Existing Baseline (Phases 0-4 Complete)

| Component | File | Key Details |
|-----------|------|-------------|
| AlertRule + AlertEngine | `ops/monitoring/alerts.py` | 8 hardcoded default rules, `evaluate(snapshot)`, `AlertRule.fingerprint` |
| OpsMetricsSnapshot | `ops/monitoring/metrics.py` | 14-field frozen dataclass, `OpsMetricsCollector.collect()` in single read_transaction |
| OpsAlertNotifier | `ops/monitoring/notifier.py` | Slack + audit_log dedup, cooldown, retry |
| OpsStorage | `ops/storage.py` | SQLite/WAL, `transaction()` / `read_transaction()`, `_create_ops_tables_fallback()` |
| PipelineScheduler | `ops/scheduler.py` | ScheduleConfig, RunRecord, cron-based, idempotent |
| CLI | `ops/cli.py` | `maint`, `docker`, `monitor`, `schedule` subparser groups |
| API `/health/ops` | `api/routers/health.py` | Snapshot + alerts via `run_in_threadpool` |
| API `/api/v1/schedules` | `api/routers/scheduler.py` | 9 CRUD + trigger endpoints |
| Dashboard | `dashboard/views/ops_health.py` | Streamlit: overview, alerts, cost, collector breakdown |
| Dashboard | `dashboard/views/scheduler.py` | Schedule management + run history |
| Dashboard | `dashboard/views/cost_analysis.py` | Cost tracking + forecasting |

### Current Test Counts
- 192 ops tests, 31 API tests, 52 dashboard tests — 275 total, all green

### What Phase 5 Adds (Limitations to Fix)
1. **Rules are hardcoded** — cannot add/edit/disable without code changes
2. **No composite rules** — cannot express "A AND B"
3. **No trend detection** — cannot detect "metric increasing over N runs"
4. **No metric history** — snapshots are ephemeral
5. **No scheduler-aware alerts** — missed/failed runs go unnoticed
6. **Dashboard shows alerts but cannot manage rules** — read-only

## Phases

### Phase 1: Research & Architecture Design
- [ ] Audit existing alert/metric code for extension points
- [ ] Define new DB schema for rules, metric history, rule evaluations
- [ ] Design JSON DSL for rule conditions (field/op/value + composites + trends)
- [ ] Map integration points: CLI commands, API endpoints, dashboard tabs
- [ ] Document architecture in findings.md
- **Status:** in_progress

### Phase 2: Schema & Storage Layer (TDD)
- [x] Add `alert_rules` table (name, condition_json, severity, enabled, component, message_template, created_at, updated_at)
- [x] Add `metric_snapshots` table (timestamp, snapshot_json) for trend queries
- [x] Add `alert_evaluations` table (rule_name, fired_at, snapshot_id, resolved_at, fingerprint)
- [x] Tables via `CREATE TABLE IF NOT EXISTS` in `_create_ops_tables_fallback()`
- [x] CRUD methods on OpsStorage: create_alert_rule, update_alert_rule, delete_alert_rule, list_alert_rules, get_alert_rule
- [x] Metric history: save_metric_snapshot, get_metric_snapshots(hours=N), purge_old_snapshots
- [x] Alert evaluations: record_alert_evaluation, get_alert_evaluations, resolve_alert_evaluation
- [x] 43 RED tests → all GREEN, 0 regressions (318 total)
- **Status:** COMPLETE

### Phase 3: Advanced Rule Engine (TDD)
- [x] JSON DSL condition evaluator: `{"field": "total_cost_24h", "op": ">", "value": 5.0}`
- [x] Composite conditions: `{"all": [c1, c2]}`, `{"any": [c1, c2]}`, `{"not": c1}`
- [x] Trend conditions: `{"trend": {"field": "...", "direction": "increasing", "window": 3}}`
- [x] Scheduler-aware metrics: `collect_scheduler_metrics()` → enriches snapshot with `active_schedules`, `missed_schedules`, `failed_runs_24h`
- [x] Custom rules reference scheduler fields via standard JSON DSL (e.g. `{"field": "failed_runs_24h", "op": ">", "value": 0}`)
- [x] Refactor AlertEngine: `load_custom_rules(storage)`, `evaluate_all(snapshot, storage)` merges builtins + custom
- [x] Rule evaluation audit trail → `alert_evaluations` table (with snapshot_id FK)
- [x] Backward compat: `evaluate()` unchanged, 8 builtin rules untouched
- [x] New file: `ops/monitoring/rule_evaluator.py` — pure-function JSON DSL evaluator
- [x] 48 RED tests → all GREEN, 0 regressions (366 total: 283 ops + 31 API + 52 dashboard)
- **Status:** COMPLETE

### Phase 4: CLI Commands (TDD)
- [x] `monitor rules list` — show all rules (builtin + custom, with enabled status)
- [x] `monitor rules add --name X --condition '{"field":"...","op":">","value":5}' --severity warning`
- [x] `monitor rules enable/disable <id>`
- [x] `monitor rules delete <id>`
- [x] `monitor rules test <id>` — dry-run evaluate single rule against current snapshot
- [x] `monitor snapshots --hours 24` — metric snapshot history summary
- [x] ASCII-safe output, Windows console compatible
- [x] 22 RED tests → all GREEN, 0 regressions (388 total: 305 ops + 31 API + 52 dashboard)
- **Status:** COMPLETE

### Phase 5: API Endpoints (TDD)
- [x] `GET /health/ops/rules` — list all rules
- [x] `POST /health/ops/rules` — create custom rule (validate condition JSON)
- [x] `GET /health/ops/rules/{id}` — get rule details + evaluation history
- [x] `PUT /health/ops/rules/{id}` — update rule (condition, severity, enabled)
- [x] `DELETE /health/ops/rules/{id}` — delete custom rule (cannot delete builtins)
- [x] `GET /health/ops/history` — metric snapshot history (with time range filter)
- [x] Pydantic request/response models (RuleCreateRequest, RuleUpdateRequest, RuleDetailResponse)
- [x] `_validate_condition()` — recursive JSON DSL validator for simple/composite/trend
- [x] `_get_ops_storage()` helper for DI
- [x] 21 RED tests → all GREEN, 0 regressions (409 total: 305 ops + 52 API + 52 dashboard)
- **Status:** COMPLETE

### Phase 6: Dashboard Integration (TDD)
- [x] "Alert Rules" tab in ops_health page — list rules, toggle enable/disable, create new
- [x] "Metric History" tab — Altair faceted line charts for key metrics over time
- [x] "Evaluation Log" tab — alert evaluation timeline (when rules fired, when resolved)
- [x] 4-tab layout: OVERVIEW, ALERT RULES, METRIC HISTORY, EVALUATION LOG
- [x] Write RED tests (22 Streamlit mock tests), then GREEN
- [x] 22 new tests, 431 total (305 ops + 52 API + 74 dashboard), 0 regressions
- **Status:** COMPLETE

### Phase 7: Verification & Cleanup
- [x] Run full ops test suite → expect 0 regressions ✓ 305 passed
- [x] Run API + dashboard tests → expect 0 regressions ✓ 52 + 74 passed
- [x] Verify 8 builtin rules fire identically to Phase 2 ✓ 13/13 passed
- [x] Update MEMORY.md with Phase 5 completion state ✓
- [x] Update progress.md with final test counts ✓
- [ ] PR-ready: clean branch, meaningful commit
- **Status:** COMPLETE (pending commit + PR)

## Key Questions
1. Should custom rules override builtins or coexist? → **Coexist** (builtins always present; custom rules additive; builtins can be individually disabled via DB flag)
2. Rule condition format? → **JSON DSL** (serializable, no eval(), safe for API/DB)
3. Metric history retention? → **30 days default**, `OPS_METRIC_RETENTION_DAYS` env var
4. Trend rules minimum data points? → **3 snapshots** before trend rule fires
5. Full snapshot or key metrics in history? → **Full snapshot JSON** (enables future analytics, ~2KB/row)
6. Where do scheduler-aware rules get schedule data? → **OpsMetricsSnapshot extended** with scheduler fields (or separate query in rule evaluator)

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| JSON DSL for conditions | Serializable to SQLite, no eval() risk, user-editable via API |
| Coexist builtins + custom | Prevents accidental loss of baseline monitoring |
| 30-day metric retention | Balances storage (~60KB/day) vs. trend needs |
| Min 3 snapshots for trends | Prevents false positives on sparse data |
| Separate alert_evaluations table | Per-rule history without overloading audit_log |
| CREATE TABLE IF NOT EXISTS | Follows existing ops/storage.py migration pattern |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|

## Notes
- Never nest `transaction()` calls — use `read_transaction()` for reads
- Windows console: `sys.stdout.reconfigure(errors='replace')` in CLI
- Streamlit mocks: `sys.modules['streamlit'] = MagicMock()` pattern
- FK constraints in standalone tests: `PRAGMA foreign_keys = OFF`
- Test baseline: 192 ops + 31 API + 52 dashboard = 275 total
