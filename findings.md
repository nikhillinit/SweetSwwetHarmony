# Findings & Decisions — Phase 3

## Requirements
- Automated, scheduled discovery pipeline runs (cron-friendly, idempotent)
- CLI commands for schedule management (CRUD + manual trigger)
- API endpoints for dashboard integration
- Advanced Streamlit dashboards (scheduler, cost analysis, enhanced ops)
- Scheduler-aware alerting rules

## Research Findings

### Existing Patterns to Follow
- **DigestScheduler** (`distribution/scheduler.py`): Idempotent enqueue via `notion_outbox`, claim/finalize pattern, cron-friendly (no daemon)
- **OpsStorage**: `CREATE TABLE IF NOT EXISTS` in `_create_ops_tables_fallback()`, WAL mode, `BEGIN IMMEDIATE` for writes
- **CLI structure**: `ops/cli.py` uses subparser groups (`maint`, `docker`, `monitor`) — `schedule` is the 4th
- **Metrics snapshot**: Single `read_transaction()` for consistent reads
- **Alert engine**: Rule-based evaluation against `OpsMetricsSnapshot`

### Database Schema Inventory (Ops)
- `memory_facts` (FTS5), `memory_action_state`, `extraction_runs`, `audit_log`, `system_health`, `fact_citations`, `user_actions`
- New tables needed: `pipeline_schedules`, `pipeline_run_history`

### Pipeline Integration Points
- `workflows/pipeline.py` → `DiscoveryPipeline.run(config)` is async
- `PipelineConfig` has: mode, collectors, dry_run, batch_size, max_workers
- `PipelineStats` tracks: signals_found, processed, pushed, failed, duplicates
- Can import directly (no subprocess needed)

### CLI Entry Points (ops/cli.py)
- Line 922: `subparsers = parser.add_subparsers(dest="command")`
- Existing groups: `list`, `approve`, `retire`, `list-actions`, `reset-action`, `audit-unused`, `stats`, `run-extraction`, `cleanup`, `monitor`, `maint`, `docker`
- Pattern: each subcommand calls a handler function with `(args, storage)` signature

### Outbox Table (signal_store.py)
- `notion_outbox` with `event_type`, `idempotency_key`, `status`, `payload` (JSON)
- Methods: `enqueue_notion_write()`, `claim_due_outbox()`, `finalize_outbox()`
- Event types currently: `notion_push`, `email_digest`

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Store schedules in ops DB | Runtime-modifiable via CLI/API; follows OpsStorage pattern |
| No croniter dependency (optional) | Parse cron in scheduler, but external trigger does timing |
| Direct pipeline import | Avoid subprocess overhead; share DB connection |
| Per-run cost tracking | Already in `extraction_runs.estimated_cost`; aggregate in history |
| Idempotency key: `pipeline_run:{schedule_id}:{date}` | Prevents duplicate runs on same day |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| (none yet) | |

## Resources
- Blueprint: `distribution/scheduler.py` (350 lines)
- Storage: `ops/storage.py` (lines 85-200 for table creation)
- CLI: `ops/cli.py` (lines 920-1040 for subparser pattern)
- Pipeline: `workflows/pipeline.py` (PipelineConfig, DiscoveryPipeline)
- Outbox: `storage/signal_store.py` (lines 3251-3583)

---
*Update this file after every 2 view/browser/search operations*
