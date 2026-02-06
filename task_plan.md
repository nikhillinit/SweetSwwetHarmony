# Task Plan: Ops Layer Phase 3 — Scheduled Pipeline Runs & Advanced Dashboards

## Goal
Add scheduled, automated discovery pipeline runs with idempotent execution, CLI controls, API endpoints, and advanced Streamlit dashboards for monitoring pipeline health, costs, and scheduling.

## Current Phase
Phase 3

## Architecture Decisions
| Decision | Rationale |
|----------|-----------|
| No APScheduler/Celery | Follow existing cron-friendly pattern from DigestScheduler |
| Outbox-based job queue | Reuse notion_outbox table with new event_type `pipeline_run` |
| Sync ops/storage.py for scheduler state | Scheduler config + history tables in ops DB (not signal_store) |
| `CREATE TABLE IF NOT EXISTS` for new tables | Match existing ops/storage.py migration pattern (no versioned migrations) |
| Cron expressions via `croniter` | Lightweight, well-maintained, pip-installable |
| No daemon loop | User triggers via Windows Task Scheduler / cron / manual CLI |

## Phases

### Phase 1: Pipeline Scheduler Core Module
- [x] Create `ops/scheduler.py` with `ScheduleConfig` dataclass and `PipelineScheduler` class
- [x] Add `pipeline_schedules` and `pipeline_run_history` tables to `ops/storage.py`
- [x] Implement: create_schedule, list_schedules, get_schedule, pause/resume, delete
- [x] Implement: enqueue_run (idempotent), execute_run, record_history
- [x] Add `croniter` to requirements.txt
- [x] Write `tests/ops/test_scheduler.py` (TDD: RED first)
- **Status:** COMPLETE (37 tests, 164 ops tests green)

### Phase 2: Scheduler CLI Commands
- [x] Add `schedule` subparser group to `ops/cli.py`
- [x] Implement: `schedule add`, `schedule list`, `schedule status`, `schedule run`, `schedule pause`, `schedule resume`, `schedule history`, `schedule delete`
- [x] ASCII-safe output (Windows console)
- [x] Write `tests/ops/test_scheduler_cli.py`
- **Status:** COMPLETE (28 tests, 192 ops tests green)

### Phase 3: Scheduler API Endpoints
- [x] Create `api/routers/scheduler.py` with CRUD + trigger endpoints
- [x] Pydantic request/response models (ScheduleCreateRequest, ScheduleResponse, MessageResponse, TriggerResponse)
- [x] Register router in `api/app.py`
- [x] Write `tests/api/test_scheduler_endpoints.py` (25 tests)
- **Status:** COMPLETE (25 tests, 31 API tests green, 192 ops tests green)

### Phase 4: Advanced Dashboards
- [x] Add generic `get/post/put/delete` methods to `dashboard/api_client.py`
- [x] Create `dashboard/views/scheduler.py` — schedule management + run history
- [x] Create `dashboard/views/cost_analysis.py` — cost attribution, forecasting
- [x] Enhance `dashboard/views/ops_health.py` — cost summary, collector breakdown
- [x] Register new pages in `dashboard/app.py` and `dashboard/views/__init__.py`
- [x] Write tests: test_api_client.py (+6), test_scheduler_view.py (14), test_cost_analysis_view.py (11), test_ops_health_enhanced.py (4)
- **Status:** COMPLETE (35 new tests, 52 dashboard tests green, 31 API tests green, 192 ops tests green)

### Phase 5: Advanced Monitoring Rules
- [ ] Add scheduler-aware alert rules to `ops/monitoring/alerts.py`
  - `schedule_missed` — scheduled run didn't execute on time
  - `schedule_failed` — run completed with errors
  - `cost_budget_exceeded` — daily/monthly cost over threshold
  - `collector_degradation` — success rate declining
- [ ] Update `OpsMetricsSnapshot` with scheduler fields
- [ ] Write `tests/ops/test_scheduler_alerts.py`
- **Status:** pending

### Phase 6: Integration Testing & Hardening
- [ ] End-to-end test: create schedule → enqueue → execute → verify history
- [ ] Test Windows Task Scheduler integration (document setup)
- [ ] Run full test suite — confirm no regressions
- [ ] Update MEMORY.md, docs
- **Status:** pending

## Key Questions
1. Should scheduled runs use `run_pipeline.py` subprocess or direct Python import?
   → **Decision**: Direct import via `workflows/pipeline.py` (avoids subprocess overhead)
2. Where to store schedule configs — DB or config file?
   → **Decision**: DB (`pipeline_schedules` table) — runtime modifiable via CLI/API
3. Retry strategy on failure?
   → **Decision**: Record failure, skip to next scheduled slot. Manual `schedule run` for immediate retry.
4. Cost tracking granularity?
   → **Decision**: Per-run cost from `extraction_runs.estimated_cost`. Collector breakdown via pipeline stats.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | | |

## Notes
- DigestScheduler (`distribution/scheduler.py`) is the blueprint — idempotent, outbox-based, cron-friendly
- ops/cli.py already has `maint`, `docker`, `monitor` subparser groups — add `schedule` as 4th
- ops/storage.py uses `CREATE TABLE IF NOT EXISTS` — no versioned migrations needed
- 4124 tests passing as baseline (checkpoint: `full-suite-4124-green`)
