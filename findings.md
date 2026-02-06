# Findings & Decisions — Phase 5: Advanced Monitoring Rules

## Requirements
- Configurable alert rules stored in DB (not just hardcoded)
- JSON DSL for rule conditions (safe, serializable, user-editable)
- Composite rules (AND/OR/NOT)
- Trend-based rules (detect metric direction over time window)
- Scheduler-aware rules (missed runs, failed runs, cost budget)
- Metric history persistence for trend analysis
- Rule CRUD: CLI, API, and dashboard
- Full backward compat with existing 8 builtin rules

## Research Findings

### Extension Points in Existing Code

**AlertEngine (`ops/monitoring/alerts.py`)**
- Constructor: `__init__(self, rules=None)` — accepts custom rule list or falls back to `default_rules()`
- Key method: `evaluate(snapshot) -> list[Alert]` — iterates rules, calls `rule.check(snapshot)`
- AlertRule has `check: Callable[[OpsMetricsSnapshot], bool]` — Python callable, not serializable
- **Extension plan**: Add `load_custom_rules(storage)` that converts JSON DSL → AlertRule callables, merge with builtins

**OpsMetricsSnapshot (`ops/monitoring/metrics.py`)**
- 14 fields: health, extraction, facts, incidents, audit
- Missing: scheduler fields (active schedules, missed runs, last run status)
- **Extension plan**: Add `active_schedules`, `missed_schedules`, `failed_runs_24h` fields
- Frozen dataclass → must add fields with defaults for backward compat

**OpsStorage (`ops/storage.py`)**
- `_create_ops_tables_fallback()` uses `conn.executescript(...)` with `CREATE TABLE IF NOT EXISTS`
- Existing tables: user_actions, memory_facts, memory_action_state, extraction_runs, audit_log, system_health, fact_citations, pipeline_schedules, pipeline_run_history
- **Extension plan**: Add 3 tables in same `executescript()` block

**PipelineScheduler (`ops/scheduler.py`)**
- `list_schedules()`, `get_schedule()`, `get_run_history()` — can query for missed/failed
- `should_run()` computes next fire time — can detect missed windows
- Uses `croniter` for cron parsing

### New DB Schema Design

```sql
-- Custom alert rules (user-defined via CLI/API)
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    condition_json TEXT NOT NULL,  -- JSON DSL
    severity TEXT CHECK(severity IN ('critical', 'warning', 'info')) NOT NULL DEFAULT 'warning',
    component TEXT,  -- optional grouping
    message_template TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,  -- 0=disabled, 1=enabled
    is_builtin INTEGER DEFAULT 0,  -- 1=system rule, cannot delete
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Metric snapshots for trend analysis (30-day retention)
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    snapshot_json TEXT NOT NULL  -- Full OpsMetricsSnapshot.to_dict()
);
CREATE INDEX IF NOT EXISTS idx_metric_snapshots_ts ON metric_snapshots(timestamp DESC);

-- Alert evaluation history (when rules fired/resolved)
CREATE TABLE IF NOT EXISTS alert_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL,  -- matches AlertRule.name or alert_rules.name
    fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT,
    fired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    snapshot_id INTEGER,
    FOREIGN KEY(snapshot_id) REFERENCES metric_snapshots(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_evals_rule ON alert_evaluations(rule_name, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_evals_fingerprint ON alert_evaluations(fingerprint);
```

### JSON DSL Condition Format

**Simple condition:**
```json
{"field": "total_cost_24h", "op": ">", "value": 5.0}
```

**Supported operators:** `>`, `>=`, `<`, `<=`, `==`, `!=`

**Supported fields:** All OpsMetricsSnapshot fields (flat access) + nested dot notation for dicts:
- `total_cost_24h`, `extractions_24h`, `open_incidents`, `overall_health_pct`
- `health_summary.db.health_percent` (dot-path into dicts)

**Composite:**
```json
{"all": [
    {"field": "total_cost_24h", "op": ">", "value": 5.0},
    {"field": "extractions_24h", "op": "==", "value": 0}
]}
```

**Any (OR):**
```json
{"any": [
    {"field": "open_incidents", "op": ">", "value": 5},
    {"field": "overall_health_pct", "op": "<", "value": 50}
]}
```

**Not:**
```json
{"not": {"field": "extractions_24h", "op": ">", "value": 0}}
```

**Trend:**
```json
{"trend": {"field": "total_cost_24h", "direction": "increasing", "window": 3}}
```
- `direction`: `"increasing"` or `"decreasing"`
- `window`: number of recent snapshots to compare (min 3)
- Evaluates: fetches last N snapshots, checks monotonic direction

### Integration Points

**CLI (`ops/cli.py`):**
- Add `rules` sub-subparser under `monitor`: `monitor rules list|add|enable|disable|delete|test`
- Add `monitor history` command
- Pattern: same as `schedule` subparser group

**API (`api/routers/health.py`):**
- Add endpoints under existing `/health/ops/` prefix
- `/health/ops/rules` — CRUD
- `/health/ops/history` — metric snapshots

**Dashboard (`dashboard/views/ops_health.py`):**
- Add "Alert Rules" tab alongside existing tabs
- Add "Metric History" tab with Altair charts

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| JSON DSL (not Python lambdas) | Safe serialization, no eval(), API-editable |
| Builtins coexist with custom rules | Never lose baseline monitoring |
| 30-day metric retention | ~60KB/day, covers monthly trends |
| Min 3 snapshots for trend rules | Prevents false positives on sparse data |
| Dot-notation field access | Enables nested dict access (health_summary.db.health_percent) |
| rule_name in alert_evaluations | Links to both builtin names and custom DB rules |

## Phase 6: Dashboard Integration — Research

### Existing Dashboard Patterns
1. **Streamlit mock pattern**: `sys.modules['streamlit'] = MagicMock()` at module top, configure `st.tabs()`, `st.columns()`, `st.expander()`, `st.form()` as context managers in `setup_method`
2. **API calls**: All through `APIClient.get/post/put/delete()` — mock via `@patch('dashboard.views.X.APIClient')`
3. **Altair charts**: Import `pandas` + `altair` inside function, call `st.altair_chart(chart, use_container_width=True)` — tests just verify `st.altair_chart` was called
4. **Tab pattern**: `st.tabs(["TAB1", "TAB2"])` returns list of context managers, each used with `with tab:`
5. **Error handling**: Check `metrics.get("error")` → `st.warning()`, early return

### Phase 6 Design: 3 New Tabs in ops_health.py

**Tab 1: "ALERT RULES"** (rule management)
- List all rules (GET `/health/ops/rules`) in a table
- Each rule: name, severity badge, enabled toggle, condition preview
- Toggle enable/disable (PUT `/health/ops/rules/{id}`)
- Delete button for custom rules (DELETE `/health/ops/rules/{id}`)
- "Create Rule" form: name, severity select, condition JSON textarea, message template
- POST to `/health/ops/rules`

**Tab 2: "METRIC HISTORY"** (time series)
- Fetch snapshots from GET `/health/ops/history?hours=N`
- Altair line charts for key metrics: `overall_health_pct`, `extractions_24h`, `total_cost_24h`, `open_incidents`
- Hours selector in sidebar (6h, 12h, 24h, 48h, 168h)

**Tab 3: "EVALUATION LOG"** (alert timeline)
- Show recent alert evaluations (from rule detail endpoint or new query)
- Table: rule_name, severity, message, fired_at, resolved_at
- Color-coded severity badges

### Integration Approach
- Modify `render_ops_health_page()` to use `st.tabs()` wrapping existing content + 3 new tabs
- Existing content (overview, components, alerts, extraction trends, facts, cost, collector) goes in "OVERVIEW" tab
- New functions: `_render_rules_tab()`, `_render_metric_history_tab()`, `_render_evaluation_log_tab()`
- API endpoints already exist from Phase 5

### Test Plan (TDD)
- New test file: `tests/dashboard/test_ops_rules_dashboard.py`
- ~20 tests covering:
  - `_render_rules_tab`: list empty, list with rules, toggle enable/disable, delete, create form
  - `_render_metric_history_tab`: empty, with data, chart rendered
  - `_render_evaluation_log_tab`: empty, with data, severity badges
  - `render_ops_health_page`: tabs created, error handling, all tabs render

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| (none yet) | |

---
*Update this file after every 2 view/browser/search operations*
