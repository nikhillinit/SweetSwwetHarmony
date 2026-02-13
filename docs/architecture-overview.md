# Architecture Overview

## System Purpose

The Discovery Engine is a consumer deal-sourcing system for Press On Ventures. It collects signals from multiple sources, filters for thesis fit, deduplicates, scores, and routes qualified prospects to a Notion CRM.

## High-Level Architecture

```
                    +-----------------+
                    |   Collectors    |  (16 sources: GitHub, SEC, news, etc.)
                    +-----------------+
                            |
                            v
                    +-----------------+
                    | Signal Pipeline |  (run_pipeline.py → workflows/pipeline.py)
                    +-----------------+
                            |
              +-------------+-------------+
              |             |             |
              v             v             v
      +------------+  +-----------+  +----------+
      | Thesis     |  | Canonical |  | Quality  |
      | Classifier |  | Identity  |  | Scoring  |
      +------------+  +-----------+  +----------+
              |             |             |
              +-------------+-------------+
                            |
                            v
                    +-----------------+
                    |  Review Queue   |  (triage → approve/reject/defer)
                    +-----------------+
                            |
                            v
                    +-----------------+
                    |  Notion Pusher  |  (routing: Source/Tracking/Hold)
                    +-----------------+
                            |
                            v
                    +-----------------+
                    |   Notion CRM    |
                    +-----------------+
```

## Data Layer

### SQLite Database (`signals.db`)

Single-file database with WAL mode. 41 migrations (v0-v41).

**Core tables:**
- `signals` — Raw signals from collectors
- `suppression_cache` — Notion-synced suppression list
- `pipeline_runs` — Pipeline execution history
- `thesis_classifications` — LLM + keyword classification results
- `signal_quality_metrics` — TP/FP labels and quality scores

**Identity tables (Phase G):**
- `entity_aliases` — Canonical identity aliases
- `entity_migrations` — Entity merge history
- `entity_key_aliases` — Key alias mappings

**Review/triage tables:**
- `review_items` — Triage queue with state machine
- `company_files` — Thin/promoted company files
- `merge_suggestions` — Dedup merge suggestions
- `merge_proposals` — Approved merge lifecycle

**Monitoring tables:**
- `canary_runs` — Golden set re-scoring runs
- `canary_drift_alerts` — Drift alert state machine
- `quality_metrics_daily` — Row-based daily metrics (SPC)
- `audit_events` — Immutable audit trail
- `run_history` — Generic async workflow tracking

## Key Subsystems

### 1. Collectors (`collectors/`)

16 independent collectors, each targeting a different data source. Collectors produce signals with a confidence score (0.0-1.0) and canonical key for deduplication.

### 2. Thesis Classification (`utils/thesis_matcher.py` + LLM)

Two-stage filtering:
1. **Keyword pre-filter**: Fast regex-based category matching
2. **LLM classification** (Gemini): Deeper analysis when keyword confidence is ambiguous

Controlled by `LLM_THESIS_MODE`: `off` / `shadow` / `active`

### 3. Canonical Identity (`utils/canonical_keys.py`)

Deduplication via canonical keys: `domain:acme.ai`, `ch:12345678`, `gh:org-name`. Supports stealth companies without domains via `name_loc:` fallback. Phase G entity identity system provides advanced merge/alias resolution.

### 4. Review Queue (`storage/review_store.py`)

State machine: `pending → approved → publish_queued → published` or `pending → rejected` or `pending → deferred`.

### 5. Confidence Routing

```
HIGH (0.7+) + multi-source → Status: "Source"
MEDIUM (0.4-0.7)           → Status: "Tracking"
LOW (<0.4)                 → Hold for batch review
Hard kill signal           → Reject
```

### 6. Monitoring & Quality

**Canary system** (`monitoring/canary_checker.py`): Re-scores golden set to detect scoring drift. Produces pass/fail verdicts with stratified breakdowns.

**SPC monitor** (`monitoring/spc_monitor.py`): Statistical process control on daily quality metrics. Detects out-of-control conditions using 3-sigma (n>=30) or Wilson interval (n<30) bounds.

**Daily aggregator** (`monitoring/daily_aggregator.py`): Computes daily metrics (FP rate, collector volume, quarantine regret, calibration ECE) into `quality_metrics_daily`.

**Alert escalation** (`monitoring/alert_escalation.py`): State machine for drift alerts: open → acknowledged → resolved, with snooze support.

**Drift recommendations** (`monitoring/drift_recommendations.py`): Advisory pattern detection from recent alerts.

### 7. API Layer (`api/`)

FastAPI-based REST API with:
- RBAC (viewer/operator/admin roles)
- Error envelope (`api/contracts.py`)
- Cursor-based pagination (`api/pagination.py`)
- Idempotency keys for mutations

### 8. Dashboard (`dashboard/`)

Streamlit dashboard with 7 views: Ops Health, Triage (Fast/Deep), Batch Publish, Hunter Sandbox, ACH Matrix, Drift Monitoring.

## Feature Flags

All write operations are gated behind feature flags that default to `disabled`. This enables safe rollout:

```
DRIFT_MONITORING_ENABLED=disabled|active
MERGE_WRITES_ENABLED=disabled|shadow|active
BULK_TRIAGE_ENABLED=disabled|active
HUNTER_PROMOTE_ENABLED=disabled|active
DELIVERY_MODE=staging_only|manual_publish|batch_publish|auto_publish
```

Attempting a write when the feature is disabled returns HTTP 423 (`FeatureDisabledError`).

## Migration Strategy

Schema migrations are forward-only with tested downgrade paths. Each migration file contains both `upgrade()` and `downgrade()` functions. Version tracked in `schema_migrations` table.

Current: v41 (drift monitoring DDL).

## Test Architecture

~1700+ tests organized by concern:
- `tests/storage/` — Storage layer, migrations
- `tests/workflows/` — Pipeline, feature guards
- `tests/monitoring/` — SPC, canary, alerts, recommendations
- `tests/api/` — API router tests
- `tests/dashboard/` — Streamlit view tests
- `tests/cli/` — CLI parser tests
- `tests/integration/` — Cross-phase integration suites
- `tests/performance/` — SLO baselines (report-only)
- `tests/e2e/` — End-to-end workflow tests
