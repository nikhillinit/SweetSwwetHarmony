# Phase 1b Findings & Decisions

## Requirements

From `task_plan_v1.1.md` Phase 1b spec:
- Git-style preview -> commit workflow for safer, faster publishing
- `publish batch create --from approved` → gathers approved ReviewItems
- `publish batch preview <batch_id>` → shows diff ("3 new, 7 updates")
- `publish batch commit <batch_id>` → guarded Notion write
- `publish batch abort <batch_id>` → drops draft without Notion writes
- Idempotency keys: skip already-published via canonical_key
- Wire into `DELIVERY_MODE=batch_publish`
- Document compensation semantics (best-effort, not atomic across Notion + DB)
- Optional `publish_pre_images` table for manual recovery

## Research Findings

### Existing Infrastructure (what we build on)
- **delivery_policy.py** (130 lines): `DeliveryIntent.BATCH_PUSH` already defined, `batch_publish` mode already allows it
- **review_store.py** (249 lines): ReviewItem state machine with `approved -> publish_queued -> published` transitions already implemented
- **notion_pusher.py** (738 lines): `process_single_prospect(canonical_key, intent)` is the reusable push interface
- **audit_log**: Schema at v27, pattern: `(action_type, entity_type, entity_id, actor, details, created_at)`
- **merge_cascade.py**: Shows `transaction_immediate()` + `tx` parameter pattern for atomic operations
- **thin_file_manager.py**: Shows paginated sweep + promotion pattern we can follow

### Key Gaps Found
1. **No `publish_queued -> approved` transition**: abort revert needs this (Task 3 adds it)
2. **No `publish_batches` table**: Need migration v31
3. **No batch CLI subcommands**: `run_pipeline.py` has `pipeline` and `triage` but no `publish`
4. **Compensation semantics**: Notion writes are not atomic — batch commit is best-effort with per-item error tracking

### Migration Registration Pattern
```
signal_store.py line 59: import V31_BATCH_PUBLISH_DDL
signal_store.py line 73: CURRENT_SCHEMA_VERSION = 31
signal_store.py line 1710: 31: V31_BATCH_PUBLISH_DDL,
```

### CLI Pattern
- `run_pipeline.py` uses `subparsers.add_parser()` for top-level commands
- Each command can have nested `add_subparsers()` (see `pipeline`, `triage`)
- Dispatch via `if args.command == "publish":`
- All commands receive `args` with `--db` from top-level parser

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| `publish` as top-level subcommand (not nested under `pipeline`) | Cleaner UX: `run_pipeline.py publish create` is shorter and more intuitive than `pipeline batch-publish create` |
| Batch ID format: `batch-YYYYMMDD-HHMMSS` | Human-readable, sortable, deterministic from timestamp |
| Abort uses direct UPDATE (bypasses state machine) | Emergency operation; state machine revert (publish_queued -> approved) added to VALID_TRANSITIONS instead |
| Decided to add `publish_queued -> approved` to VALID_TRANSITIONS | Cleaner than raw SQL bypass; abort is a legitimate workflow, not a hack |
| `commit_batch` dry_run checks delivery policy? | No — dry_run skips the policy check since no actual writes happen. Allows previewing commit in staging_only mode |
| Per-item error tracking in `batch_items` | Unlike atomic DB transactions, Notion writes can fail partially. Need per-item status to know what succeeded |
| Skip `publish_pre_images` table (Task 1b.9) | Marked optional in spec. audit_log + batch_items provide sufficient recovery info. Can add later if needed |
| BatchPublisher receives `store` parameter (no direct construction) | Follows governance lint pattern — no `SignalStore()` construction in workflow files |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| (none yet — plan phase) | |

## Resources

- `workflows/delivery_policy.py` — DeliveryIntent.BATCH_PUSH, assert_notion_write_allowed()
- `storage/review_store.py` — create_review_item, update_review_status, get_review_queue
- `workflows/notion_pusher.py:286` — process_single_prospect(canonical_key, intent)
- `storage/merge_cascade.py` — transaction_immediate() + atomic operation pattern
- `storage/migrations/v30_pipeline_identity_stats.py` — migration template
- `task_plan_v1.1.md:187-223` — Phase 1b spec
