# Phase 2 Day 3: Strategy Dashboard Generator

Date: 2026-04-28
Status: draft - awaiting approval before build

## Scope

Read-only generator that materializes five status blocks into a target Markdown
strategy/dashboard page via marker-based idempotent injection. Source data lives
in `signals.db` (read-only), `state/collectors.json` (when present or as the
tracked empty baseline `{}`), `state/holdout_ids.json` (when present),
`state/gp_workload.jsonl` (when present), and configurable split-summary
artifacts. The generator never writes to `signals.db`, never mutates `state/*`,
and never depends on GitHub API access.

## Why now, not later

PR #140 merged 2026-04-28T07:52. `build_health_report` now exposes
`override_active`, `override_active_count`, `override_active_collectors`.
Evaluation splits are deterministic with post-split invariants.
GP workload logger separates raw-review minutes from useful-label minutes.
Day 3 turns those signals into a single operator-facing surface.

## Non-goals

- Live `signals.db` migration or schema change
- Wiki/Obsidian sync, Slack/email notification
- New CI gates
- Embedding dashboard generation in pipeline run paths
- Real-time refresh or cron job
- Committing the generated dashboard target as part of this Day 3 PR

## Deliverables

- `scripts/generate_strategy_dashboard.py` - the read-only generator
- `tests/scripts/test_generate_strategy_dashboard.py` - contract suite
- No source-tree changes outside `scripts/` + `tests/scripts/`
- Tests use fixture Markdown files or temp directories; the PR does not commit
  the generated dashboard page.

## CLI

```
generate_strategy_dashboard.py
  --target <path>             [required] markdown file with the 5 markers
  --init                      create missing markers (else missing -> exit 4)
  --db <path>                 default: $DISCOVERY_DB_PATH or signals.db
  --schema-contract <path>    default: .omx/wave6/live_schema_contract.json
  --split-summary <path>      default: state/evaluation_splits_summary.json; missing -> warn
  --holdout-ids <path>        default: state/holdout_ids.json; missing -> warn
  --gp-workload-jsonl <path>  default: state/gp_workload.jsonl; missing -> warn
  --as-of <iso-date-or-datetime>
                              optional; default: current UTC time; anchors rolling windows
  --output <path>             optional; default: in-place rewrite of --target;
                              when set, leaves --target unchanged
```

`--target` intentionally has no default. A caller may use a project convention
such as `.omx/wave6/strategy.md`, but the tool must not bake in that path.

Exit codes:
- 0 success
- 2 contract violation (schema contract failure, duplicate/partial/reversed
  markers, unknown dashboard markers, or readiness-strict split invariant
  failure)
- 3 IO error
- 4 missing marker without `--init`

## Failure-mode policy

The dashboard generator favors successful offline rendering with explicit WARN
blocks over hard failure. Optional artifacts that are missing, malformed,
stale, or empty produce WARN evidence and do not prevent other blocks from
rendering. Required schema contract violations render a FAIL readiness gate and
return exit 2 after the output is produced when writing is otherwise possible.

All generated blocks are deterministic for fixed inputs and a fixed `--as-of`
value. Missing optional files, malformed optional JSON/JSONL, or bad JSONL
lines never mutate source artifacts and never call GitHub APIs.

Writes are atomic: render to a temporary sibling file, flush/fsync when
practical, then replace the target. On failure, the original target is left
unchanged. `--output` writes the generated document to that path and leaves
`--target` untouched.

## Marker contract

HTML comment markers, one pair per block:

```
<!-- harmonic:dashboard:collector_matrix:start -->
...generated content...
<!-- harmonic:dashboard:collector_matrix:end -->
```

Block names: `collector_matrix`, `evaluation_split_integrity`,
`holdout_metrics`, `gp_workload_capacity`, `phase2_readiness_guardrails`.

Each block must have exactly one start marker and exactly one end marker.
Duplicate markers, reversed marker order, partial marker pairs, nested
dashboard markers, or unknown `harmonic:dashboard:` block names are contract
violations and exit 2.

`--init` appends missing marker pairs in canonical block order at the end of
the target document. Existing valid marker pairs are preserved. Partial marker
pairs remain contract violations even with `--init`.

Idempotency invariants:
- Same inputs + same `--as-of` -> byte-identical output
- Re-running on a generated file produces zero diff
- Content outside markers is never modified

## Block contracts

### 1. collector_matrix
Source: `ops.collector_health.aggregate_signal_counts()`,
`ops.collector_heartbeat.load_collector_state()`, and
`ops.collector_health.build_health_report()` (landed in PR #140).

`state/collectors.json` may be the tracked empty baseline `{}`. The generator
must not require locally materialized collector runtime state to be committed;
empty or partially populated state renders a warning/empty matrix rather than
raising.

Required source fields use the current `build_health_report()` shape: `name`
(rendered as `collector`), `configured_status`, `effective_status`,
`last_run_status`, `last_success_at`, `observed_signal_count` (rendered as
`signals_found_90d`), and `override_active`.

Required summary fields: `total`, `by_effective_status`, `silent_count`,
`stale_count`, `failing_count`, `override_active_count`,
`override_active_collectors`, and `warnings`.

Render: markdown table + summary line, sorted by collector name. Missing
timestamps render as `-`; missing statuses render as `unknown`. Markdown table
cells are escaped. Missing/blocked collectors surface their
`configured_status` (`disabled_missing_key`, `blocked_access`, etc.) without
false-flagging as silent, preserving the heartbeat-v2 sticky-intent contract.

### 2. evaluation_split_integrity
Source: `--split-summary` JSON, default
`state/evaluation_splits_summary.json` (intentionally untracked per commit
`02c9b28`; must warn cleanly when absent), plus sibling split artifacts
`train_ids.json`, `calibration_ids.json`, and `holdout_ids.json` when present.

Expected summary shape is the current Day 2 artifact: `schema_version`,
`generated_at`, `seed`, `fractions`, `label_source`, `total_rows`, `sizes`,
and `overall_stratification`. Per-split artifacts supply `split`,
`generated_at`, `seed`, `fractions`, `size`, `signal_ids`, and
`stratification`.

Render: counts and percentages derived from `sizes` / `total_rows`, plus the
per-split label breakdown from sibling artifacts. Missing summary or sibling
artifacts render WARN blocks and exit 0. Malformed optional JSON renders WARN
evidence and exit 0. Present artifacts with invariant violations render FAIL
in readiness evidence; strict mode may return exit 2.

Split consistency checks:
- summary and per-split artifacts agree on `generated_at`, `seed`, and
  `fractions`
- each split file has `size == len(signal_ids)`
- no duplicate IDs within a split
- no ID appears in more than one split
- union count equals `summary.total_rows`

### 3. holdout_metrics
Source: `--holdout-ids` file (default `state/holdout_ids.json`), using the
Day 2 split artifact's `signal_ids` field.

Required rendered fields: unique count, duplicate count (if non-zero), and
deterministic SHA for the protected ID set. SHA is computed over the canonical
UTF-8 JSON serialization of sorted unique IDs coerced to strings:
`json.dumps(sorted_ids, separators=(",", ":"), ensure_ascii=False)`.

Render: count + sha + explicit protection statement:
`"Holdout IDs are protected from threshold fitting and calibration. Day 4+
threshold/calibration commands must pass --holdout-file state/holdout_ids.json."`

Missing or malformed file: warning block, exit 0. Duplicate IDs are
deduplicated for SHA calculation and surfaced only as counts. **MUST NOT**
render the IDs themselves, including in error text.

### 4. gp_workload_capacity
Source: `--gp-workload-jsonl` (default `state/gp_workload.jsonl`).

The generator uses a dashboard-local streaming aggregator rather than
`ops.gp_workload.summarize_workload()`, because the existing helper reads the
whole file. Expected JSONL records follow the current Day 2 event schema:
`timestamp`, `event`, `runner`, and either `items_count` for
`review_set_generated` or `attempted`/`succeeded`/`failed` for
`labels_applied`.

Required rendered fields, kept **separate** per Day 2 contract:
- `raw_event_review_minutes_per_week` (last 28 days from `--as-of`, divided by 4)
- `useful_label_minutes_per_week` (last 28 days from `--as-of`, divided by 4)

Minute estimates use `ops.gp_workload.DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM` and
`DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM`. Rows outside the rolling 28-day window
are ignored. Malformed JSONL rows are skipped and counted in a warning line.

Render: side-by-side table with explicit note that raw review burden does not
equal useful labeling capacity.

Missing: warning block. **MUST NOT raise.** Stream-read JSONL so a long file
will not OOM the report.

### 5. phase2_readiness_guardrails
Source: composite - pure schema probe functions
(`scripts.inspect_live_schema.load_contract()` and `inspect_database()`, never
`scripts.inspect_live_schema.main()`), collector health, split integrity,
holdout protection, and gp_workload presence.

Render: traffic-light table - gate name | PASS/WARN/FAIL | evidence.

Gate rules:
- `schema_probe_passes`: PASS when the probe is OK; WARN when contract/DB load
  fails before inspection (equivalent to probe exit 3); FAIL when the live
  schema violates the contract (equivalent to exit 2).
- `collector_health_no_unexpected_silence`: PASS when no enabled collector is
  unexpectedly silent; WARN for only intentional disabled/missing-key/blocked
  collectors; FAIL for any enabled collector unexpectedly silent.
- `evaluation_splits_present_and_invariants_hold`: PASS when summary and
  sibling split artifacts are present and consistency checks pass; WARN when
  artifacts are missing or malformed; FAIL when present artifacts violate
  invariants.
- `holdout_protection_documented`: PASS when the holdout file is present, IDs
  are not rendered, and the protection statement is rendered; WARN when the
  file is missing or malformed; FAIL if IDs are rendered or the protection
  statement is absent.
- `gp_workload_logging_active`: PASS when the file is present and has recent
  valid rows in the rolling window; WARN when the file is missing or has no
  recent rows; FAIL when the file is present but all recent rows are malformed.

**MUST NOT** depend on GitHub PR/review state. Generation must succeed when
offline.

## Test contracts (`tests/scripts/test_generate_strategy_dashboard.py`)

Read-only invariants:
- `test_signals_db_mtime_unchanged_after_generation`
- `test_signals_db_size_unchanged_after_generation`
- `test_collectors_state_mtime_unchanged_after_generation`
- `test_holdout_file_mtime_unchanged_after_generation`
- `test_gp_workload_jsonl_mtime_unchanged_after_generation`
- `test_split_summary_mtime_unchanged_after_generation`
- `test_schema_probe_reports_are_not_written_by_dashboard_generation`

Idempotency:
- `test_double_generation_produces_zero_diff`
- `test_blocks_deterministic_for_fixed_inputs_and_as_of`
- `test_output_mode_does_not_modify_target`

Marker safety:
- `test_content_outside_markers_unchanged`
- `test_missing_markers_exit_4_without_init`
- `test_init_creates_missing_markers_idempotently`
- `test_no_block_duplication_on_rerun_with_init`
- `test_duplicate_marker_exits_2`
- `test_partial_marker_pair_exits_2_even_with_init`
- `test_init_appends_blocks_in_canonical_order`
- `test_unknown_dashboard_marker_exits_2`
- `test_failed_write_leaves_original_target_unchanged`

Block-specific:
- `test_collector_matrix_handles_empty_collectors_state_baseline`
- `test_collector_matrix_includes_override_active_fields`
- `test_collector_matrix_summary_includes_override_active_count`
- `test_collector_matrix_sorts_collectors_deterministically`
- `test_collector_matrix_escapes_markdown_table_cells`
- `test_disabled_or_blocked_collectors_not_counted_as_unexpected_silent`
- `test_missing_split_summary_emits_warning_block_not_exception`
- `test_malformed_split_summary_emits_warning_block_not_exception`
- `test_split_artifact_invariant_violation_surfaces_fail_evidence`
- `test_missing_holdout_file_emits_warning_block`
- `test_malformed_holdout_file_emits_warning_block_not_exception`
- `test_holdout_metrics_includes_protection_statement`
- `test_holdout_metrics_does_not_render_ids`
- `test_holdout_sha_is_order_independent`
- `test_holdout_sha_canonicalizes_string_ids`
- `test_holdout_duplicate_ids_warn_without_rendering_ids`
- `test_missing_gp_workload_emits_warning_block`
- `test_gp_workload_keeps_raw_and_useful_separate`
- `test_gp_workload_rolling_window_uses_as_of`
- `test_gp_workload_ignores_rows_outside_4_week_window`
- `test_gp_workload_streams_large_file_without_loading_all_rows`
- `test_gp_workload_malformed_rows_warn_and_do_not_abort`
- `test_phase2_readiness_does_not_call_github_api`
- `test_phase2_readiness_traffic_lights_match_underlying_state`
- `test_phase2_readiness_schema_probe_exit2_is_fail`
- `test_phase2_readiness_schema_probe_exit3_is_warn`

## Dependencies

Existing modules consumed:
- `ops.collector_health.aggregate_signal_counts`
- `ops.collector_health.build_health_report`
- `ops.collector_heartbeat.load_collector_state`
- `scripts.inspect_live_schema.load_contract`
- `scripts.inspect_live_schema.inspect_database`
- `ops.gp_workload.DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM`
- `ops.gp_workload.DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM`
- `state/collectors.json` (heartbeat-v2 state, possibly tracked empty baseline)
- `utils.db_guard.read_current_signal_count` (read-only signal count, only if
  the final render needs a total DB count)

No new modules outside `scripts/` + `tests/scripts/`.

## Implementation order (TDD)

1. Contract helpers and pure block-string builders (one pure function per block)
2. Split/holdout/workload parsers with warning-returning result objects
3. Marker discovery + idempotent injection harness
4. Atomic write / `--output` handling
5. CLI wrapper that wires the above
6. Integration tests against fixture markdown + fixture data
7. Live-data smoke test (signals.db ro, real `state/` paths)

## Risks

- **Marker collisions** if a future doc embeds the same comment pattern.
  Mitigation: exact namespace validation plus unknown marker exit 2.
- **`gp_workload.jsonl` growth** - long file should not OOM the report.
  Mitigation: dashboard-local streaming 28-day aggregator.
- **Holdout ID leakage** via dashboard. Mitigation: render count + sha only;
  explicit tests that IDs are never rendered, including error text.
- **Stale split artifacts** after a failed split run. Mitigation: sibling-file
  consistency checks (`generated_at`, `seed`, `fractions`, sizes, duplicates,
  overlap, union count) and FAIL readiness evidence when present artifacts do
  not agree.
- **Schema-probe brittleness** if `live_schema_contract.json` drifts.
  Mitigation: use pure probe functions, never `inspect_live_schema.main()`;
  traffic-light gate maps load errors to WARN and contract violations to FAIL.

## Out of scope (deferred)

- Auto-publishing the dashboard markdown to Notion/Obsidian
- Real-time refresh / cron job
- Per-rep/territory filtering
- Dashboard delta tracking / changelog generation
- Cross-PR/review-state surfacing
- Adding `--holdout-file` support to future Day 4+ calibration/threshold
  commands
