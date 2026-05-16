# Process Dry-Run Read-Only - RALPLAN-DR

Status: Draft for consensus approval
Date: 2026-05-15
Branch target: `fix/process-dry-run-readonly`
Issue title: `process --dry-run mutates signal_processing and thesis_classifications`

## Scope

Make `python run_pipeline.py process --dry-run` fully read-only for persistent
tables. The command may still:

- read from SQLite,
- compute classifications, gating, and simulated routing decisions,
- call read-only external APIs when already part of evaluation,
- print or log what would have happened.

It must not mutate any persistent table in the target DB.

This slice includes the CLI entrypoint, process-wide helper/store
initialization against `config.db_path`, read-only barriers, focused
regressions, feature-matrix copied-DB verification, and a gated DB repair
runbook for rows already touched by prior dry runs.

## Guardrails

- Failing tests first.
- Broad fix, not only the existing auto-push/no-connector branch.
- Add a defensive process-wide read-only mode plus store/helper write barriers
  so future processing regressions fail closed.
- Do not mutate the live DB as part of implementation verification.
- Do not run live DB repair before a timestamped backup and row export.
- Keep this branch focused on `process --dry-run`; do not redesign collector
  dry-run semantics unless a shared helper is needed for correctness.

## Repo Grounding

- [run_pipeline.py](C:/dev/Harmonic/run_pipeline.py:552) eagerly
  calls `await pipeline.initialize()` before
  `process_pending(dry_run=args.dry_run)`.
- [workflows/pipeline.py](C:/dev/Harmonic/workflows/pipeline.py:542)
  initializes `SignalStore` without any read-only mode and
  [warms suppression sync](C:/dev/Harmonic/workflows/pipeline.py:895)
  with `sync(dry_run=False)`.
- [workflows/pipeline.py](C:/dev/Harmonic/workflows/pipeline.py:600) opens
  `EntityResolutionStore(db_path=self.config.db_path)` in the same process
  path, and [workflows/pipeline.py](C:/dev/Harmonic/workflows/pipeline.py:607)
  opens `FounderStore(db_path=self.config.db_path)` with no shared process
  access mode today.
- [workflows/pipeline.py](C:/dev/Harmonic/workflows/pipeline.py:1200)
  runs `_begin_run_tracking(...)` and `_end_run_tracking(...)` even for
  process dry-run.
- [workflows/run_manager.py](C:/dev/Harmonic/workflows/run_manager.py:82)
  writes `run_history` directly via `db.execute(...); commit()`, bypassing
  `SignalStore.transaction()`.
- [workflows/pipeline.py](C:/dev/Harmonic/workflows/pipeline.py:1767)
  still writes on several dry-run paths:
  exception rejection, suppression rejection, thesis persistence/status
  updates, confidence ledger, exit prediction, investor matching, verification
  reject, shadow/entity artifacts, functional schema persistence, boilerplate
  and thesis shadow logs, and `asset_to_lead` links.
- [workflows/pipeline.py](C:/dev/Harmonic/workflows/pipeline.py:1923)
  already runs claim-fact extraction during process dry-run, but claim-fact
  persistence is expected to remain blocked.
- [storage/signal_store.py](C:/dev/Harmonic/storage/signal_store.py:1989)
  opens SQLite in normal read-write mode, applies migrations, and uses
  [transaction()](C:/dev/Harmonic/storage/signal_store.py:2051) as
  the main write choke point today.
- [storage/entity_resolution.py](C:/dev/Harmonic/storage/entity_resolution.py:56)
  owns `asset_to_lead` writes in the same DB path when entity resolution is
  enabled, using its own helper connection path.
- [storage/founder_store.py](C:/dev/Harmonic/storage/founder_store.py:421)
  owns founder migration and founder table writes in the same DB path when
  founder scoring is enabled, using its own helper connection path.
- [storage/entity_identity_store.py](C:/dev/Harmonic/storage/entity_identity_store.py:98)
  writes Phase G identity tables through `SignalStore` transaction boundaries
  rather than an independent helper connection.
- [tests/workflows/test_pipeline_dry_run.py](C:/dev/Harmonic/tests/workflows/test_pipeline_dry_run.py:579)
  still encodes the old assumption that suppressed dry-run signals are rejected.

## Requirements Summary

1. `process --dry-run` must not mutate any persistent table in the target DB.
2. The fix must cover initialization, run tracking, suppression warmup, all
   processing branches, and every helper that opens `config.db_path`, including
   `SignalStore`, `EntityResolutionStore`, `FounderStore`, and any same-DB
   helper stores used by process execution.
3. The barrier must fail closed if a new write slips through later.
4. Persistent observability tables are forbidden in process dry-run; simulated
   evidence must move to stdout, result payloads, or a non-persistent artifact
   sink.
5. Verification must include feature-matrix copied-DB before/after table
   comparison, not only mocks.
6. Live DB repair remains a separate gated runbook after backup/export.

## Principles

1. Dry-run means read-only for persistent state, not "mostly no Notion writes".
2. Enforce the invariant twice: explicit call-site gating plus process-wide
   helper/store barriers on every same-DB opener.
3. Prefer fail-closed behavior over best-effort logging when a write is
   attempted during process dry-run.
4. Preserve useful simulation value: compute, classify, evaluate, and report
   decisions even when persistence is blocked.
5. Keep repair work separate from code repair; prove the invariant on a copied
   DB before touching `signals.db`.

## Top Drivers

1. The current process dry-run path mutates multiple persistent tables across
   initialization, processing, and helper calls.
2. A narrow fix at the Notion branch would miss the actual regression surface
   and allow future write paths to reappear silently.
3. Prior dry-run runs may already have touched live rows, so implementation and
   repair must stay explicitly separated and evidence-backed.

## Viable Options

### Option A: Pipeline Call-Site Gating Only

Add `if not dry_run` guards around every known write path in
`run_pipeline.py` and `workflows/pipeline.py`.

Pros:

- Smallest code delta.
- Keeps most existing initialization behavior unchanged.
- Easy to reason about in the happy path.

Cons:

- Misses future helper writes unless every new call site is audited perfectly.
- Does not stop direct store writes from helper code or run tracking.
- Leaves SQLite/store semantics inconsistent with the user-facing contract.

### Option B: Dual-Layer Fix With Process-Wide Read-Only Mode And Helper/Store Barriers

Add explicit dry-run gating in the CLI/pipeline path and introduce
process-wide read-only DB access semantics for every helper that opens
`config.db_path`, plus store/SQLite/query-only barriers that block mutations
even if a helper forgets to check `dry_run`.

Pros:

- Defense in depth.
- Catches future regressions automatically.
- Creates a reusable safety contract for other read-only lanes.
- Lets tests prove both semantic behavior and fail-closed safety.

Cons:

- Requires plumbing through initialization and every same-DB helper.
- Forces a decision on how read-only initialization handles migrations/FTS
  setup and missing DBs.
- May require touching `workflows/run_manager.py` because it bypasses
  `transaction()`.

### Option C: SQLite Connection-Level Read-Only Only

Open the DB with read-only/query-only semantics and let unexpected writes fail
without adding broad pipeline guards.

Pros:

- Strong lowest-level protection.
- Low risk of silent writes.

Cons:

- Poor UX unless call sites handle dry-run simulation intentionally.
- Does not explain or control warmup/run-tracking behavior.
- Leaves logs and tests brittle because failures happen too late and too
  opaquely.

## Preferred Option

Choose Option B.

The branch should combine semantic dry-run behavior in the pipeline with a
process-wide read-only DB access mode and defensive helper/store/SQLite
barriers. That gives the user-visible behavior they asked for and closes the
helper-level ambiguity that previously allowed accidental live writes.

## Deliberate-Mode Pre-Mortem

### Scenario 1: Initialization still mutates before processing begins

Failure mode:

- `run_pipeline.py` or `DiscoveryPipeline.initialize()` opens one or more
  same-DB helpers in normal mode, runs migrations/bootstrap, or warms
  suppression cache before the dry-run barrier exists.

Mitigation:

- Move dry-run intent into initialization.
- Ensure process dry-run uses read-only store setup from the first DB open.
- Skip or read-only-wrap suppression warmup and run tracking.

### Scenario 2: Barrier looks real but direct writes bypass it

Failure mode:

- `SignalStore.transaction()` is guarded, but direct `db.execute()` callers or
  same-DB helpers such as `workflows/run_manager.py`, `EntityResolutionStore`,
  or `FounderStore` still write successfully.

Mitigation:

- Audit direct store connection writes in the process path.
- Either route them through the guarded layer or skip them entirely on
  process dry-run.
- Add tests that specifically cover run tracking and any direct helpers.

### Scenario 3: Mock tests pass while copied-DB verification still mutates

Failure mode:

- Unit tests only assert that a few mocked methods were not called, while some
  real table still changes during a full CLI dry run on a scratch copy.

Mitigation:

- Add a copied-DB regression that snapshots persistent tables before/after.
- Treat that copied-DB comparison as the blunt acceptance gate for the branch.

## Expanded Test Plan

### Unit

- [tests/workflows/test_pipeline_dry_run.py](C:/dev/Harmonic/tests/workflows/test_pipeline_dry_run.py:1)
  - Replace the suppressed-signal expectation so `dry_run=True` stays
    simulated/log-only.
  - Add failing coverage for:
    - exception path not calling `mark_rejected`;
    - thesis `REJECTED`, `HELD`, and qualified paths not persisting status or
      classification rows during dry run;
    - verification `REJECT` not mutating status during dry run;
    - process dry-run not creating or updating `run_history` rows at the
      pipeline branch level;
    - dry-run path not persisting exit prediction, investor matches, or
      confidence ledger.
- [tests/workflows/test_run_manager.py](C:/dev/Harmonic/tests/workflows/test_run_manager.py:1)
  - Only add helper-level readonly coverage here if a new run-tracking wrapper
    or guard helper is introduced.
- New focused storage test, preferably
  `tests/storage/test_signal_store_readonly.py`
  - Assert read-only initialization works against an existing DB.
  - Assert write methods raise a dedicated read-only error or equivalent
    failure.
  - Assert query methods still work.
- New helper-store focused tests, preferably:
  - `tests/storage/test_entity_resolution_readonly.py`
  - `tests/storage/test_founder_store_readonly.py`
  - Assert process-scope read-only mode blocks `asset_to_lead`, founder
    migration, and founder table writes while preserving read paths.

### Integration

- New copied-DB regression, preferably under
  `tests/integration/test_process_dry_run_readonly.py`
  - Define the canonical proof entrypoint as a parametrized integration test:
    `tests/integration/test_process_dry_run_readonly.py::test_process_dry_run_preserves_all_persistent_tables[<lane>]`.
  - Define a dedicated snapshot helper for that test and for manual proof:
    `python -m tests.support.db_snapshot compare-dry-run --db-path <scratch> --command "<cmd>"`.
  - Run the real process-dry-run path against copied DBs with targeted feature
    lanes:
    `baseline`,
    `claim_facts`,
    `functional_schema`,
    `entities`,
    `phase_g_identity_resolution`,
    `shadow_entity_resolution`,
    `exit_predictor`,
    `investor_matching`,
    `founder_scoring`,
    `combined_high_risk`.
  - Compare row counts and deterministic hashes for all persistent user tables
    returned by `sqlite_master`.
  - Minimum must-match tables when present:
    `signal_processing`, `thesis_classifications`, `confidence_ledger`,
    `run_history`, `claim_facts`, `exit_predictions`,
    `investor_matches`, `notion_outbox`, suppression cache tables,
    `shadow_log`, `functional_schemas`, `asset_to_lead`,
    Phase G identity tables (`entity_aliases`, `entity_key_aliases`,
    `entity_blocking_index`, `entity_migrations`), founder tables
    (`founders`, `founder_experiences`, `founder_signals`,
    `founder_schema_migrations`), and shadow-entity artifact tables.
  - `audit_log` / `audit_events` are not expected to change on
    `process_pending`; if present, the whole-DB before/after snapshots must
    still confirm no change.

### E2E / CLI

- Run:
  `python run_pipeline.py process --dry-run --db-path <scratch-copy>`
- Assert:
  - command exits successfully;
  - simulation output is produced;
  - no persistent table changes.

### Observability

- Verify dry-run logs still show simulated routing, thesis, suppression,
  shadow/entity, and schema outcomes without claiming persistence.
- Route former persistent shadow/classification evidence to stdout, returned
  stats/result payloads, or an explicitly non-persistent artifact sink.
- Add a distinctive warning/error path for blocked writes so future regressions
  are diagnosable instead of silent.
- Preserve the feature-matrix copied-DB snapshot/hash report as the acceptance
  artifact for human review.

## Implementation Plan

### Step 1: Lock in failing tests first

Files:

- `tests/workflows/test_pipeline_dry_run.py`
- `tests/workflows/test_run_manager.py`
- `tests/storage/test_signal_store_readonly.py` (new)
- `tests/storage/test_entity_resolution_readonly.py` (new)
- `tests/storage/test_founder_store_readonly.py` (new)
- `tests/integration/test_process_dry_run_readonly.py` (new)
- `tests/support/db_snapshot.py` (new)

Actions:

- Update the stale suppressed-signal expectation to the stricter invariant.
- Add targeted failing tests for every currently known mutation path in the
  process dry-run surface, including helper-store writes.
- Assert `run_history` non-mutation in `tests/workflows/test_pipeline_dry_run.py`
  and in the integration copied-DB proof, not only in `test_run_manager.py`.
- Add a feature-matrix copied-DB immutability regression that fails against the
  current code.

Acceptance:

- At least one workflow-level dry-run test fails on current `main`.
- The feature-matrix copied-DB regression fails on current `main`.
- The failures clearly prove mutation, not only logging differences.

### Step 2: Plumb dry-run intent into initialization and entrypoints

Files:

- `run_pipeline.py`
- `workflows/pipeline.py`
- `storage/entity_resolution.py`
- `storage/founder_store.py`
- `storage/entity_identity_store.py`

Actions:

- Remove or refactor the eager `await pipeline.initialize()` call in the
  process CLI path so dry-run intent is known before the first DB open.
- Extend `DiscoveryPipeline.initialize(...)` and/or `process_pending(...)` to
  establish a shared process-scope DB access mode before any helper opens
  `config.db_path`.
- Apply that same process-scope mode to `SignalStore`,
  `EntityResolutionStore`, `FounderStore`, and any other same-DB helper store
  initialized by the process path.
- Distinguish helper types explicitly:
  - independent same-DB helper connections: `EntityResolutionStore`,
    `FounderStore`;
  - same-DB writes routed through `SignalStore` transactions:
    `EntityIdentityStore`.
- Skip process-run tracking on dry run, or route it into a purely in-memory/log
  path.
- Skip suppression warmup persistence during process dry-run. Either do not run
  it, or run only a read-only fetch/report path that does not sync into SQLite.

Acceptance:

- No process dry-run helper opens `config.db_path` in normal mutating mode
  before the read-only contract is established.
- `run_history` is not mutated during process dry-run.
- Suppression warmup no longer writes to local tables during process dry-run.

### Step 3: Add the defensive SignalStore/SQLite write barrier

Files:

- `storage/signal_store.py`
- `workflows/run_manager.py`
- `storage/entity_resolution.py`
- `storage/founder_store.py`
- `storage/entity_identity_store.py`

Actions:

- Add explicit process-scope `read_only` or `allow_writes` semantics to
  `SignalStore`.
- Add matching read-only semantics to `EntityResolutionStore`,
  `FounderStore`, and any other helper that opens the same DB path during
  process execution.
- Ensure `EntityIdentityStore` inherits the same no-write contract through
  `SignalStore` transaction boundaries or an equivalent caller-side gate rather
  than treating it as an independent connection owner.
- In read-only mode, require an existing DB, avoid directory/bootstrap writes,
  and enable a DB-level write barrier such as SQLite read-only/query-only mode.
- Add a dedicated exception for write attempts in read-only mode.
- Guard `transaction()` and any other central write helpers accordingly.
- Audit direct `self._db.execute(...); commit()` callers used by process
  dry-run, especially `run_manager`, and route them through the same contract or
  skip them when dry-run is active.

Acceptance:

- No same-DB helper opened for process dry-run can mutate any persistent table
  even if a caller forgets to check `dry_run`.
- The failure mode is explicit and testable.
- Query paths used by process dry-run still function.

### Step 4: Hard-gate all known process-path writes

Files:

- `workflows/pipeline.py`
- `workflows/suppression_sync.py` if needed by the warmup path

Actions:

- Add explicit no-write dry-run behavior for the current mutation sites:
  - exception fallback rejection;
  - suppression-hit rejection;
  - thesis classification persistence;
  - thesis rejected/held status updates;
  - confidence ledger persistence;
  - claim-fact persistence;
  - exit prediction storage;
  - investor match persistence;
  - notion outbox persistence;
  - verification reject persistence;
  - functional schema persistence;
  - boilerplate/thesis shadow log persistence;
  - `asset_to_lead` link creation and other same-DB entity-resolution writes;
  - Phase G identity writes to `entity_aliases`, `entity_key_aliases`,
    `entity_blocking_index`, and `entity_migrations`;
  - founder migration and founder table writes;
  - shadow entity `store_shadow_run`, `store_skipped_shadow_run`, and merge
    suggestion writes.
- Preserve simulation output and result payloads so dry-run remains useful.
- Route former persistent evidence surfaces to stdout, returned stats/result
  payloads, or a non-persistent artifact sink.
- Make any helper that truly requires persistence unavailable in process
  dry-run rather than silently mutating.

Acceptance:

- Every known mutation site in the grounded evidence, including same-DB helper
  writes, is either guarded by `if not dry_run` or blocked by the barrier.
- Process dry-run still returns meaningful routing/stat summary output.

### Step 5: Prove feature-matrix full-table immutability on copied DBs

Files:

- `tests/integration/test_process_dry_run_readonly.py`
- `tests/support/db_snapshot.py`

Actions:

- Implement `tests.support.db_snapshot` with a concrete entrypoint:
  `python -m tests.support.db_snapshot compare-dry-run --db-path <scratch> --command "<cmd>"`.
- Have that helper record per-table row counts and a stable hash for each
  persistent user table in `sqlite_master`, then fail if any persistent table
  changes after the command completes.
- Run the real dry-run process path against copied DBs or targeted enabled-
  feature fixtures that exercise optional mutation lanes.
- Minimum targeted matrix:
  - baseline dry-run processing;
  - `use_claim_facts=True`;
  - `use_functional_schema=True`;
  - `use_entities=True`;
  - `use_phase_g_identity_resolution=True`;
  - `use_shadow_entity_resolution=True`;
  - `use_exit_predictor=True`;
  - `use_investor_matching=True`;
  - `use_founder_scoring=True`;
  - one combined high-risk fixture covering overlapping helper initializers.
- Express the canonical automated proof as a parametrized pytest family:
  `pytest tests/integration/test_process_dry_run_readonly.py -q -k "preserves_all_persistent_tables"`.
- Name the required lane IDs explicitly in the test:
  `[baseline]`,
  `[claim_facts]`,
  `[functional_schema]`,
  `[entities]`,
  `[phase_g_identity_resolution]`,
  `[shadow_entity_resolution]`,
  `[exit_predictor]`,
  `[investor_matching]`,
  `[founder_scoring]`,
  `[combined_high_risk]`.
- Compare the entire persistent-table snapshot before/after for each fixture.
- Assert `run_history` is unchanged in each lane as part of the whole-DB
  snapshot proof, and also assert it directly in the lane result checks where
  helpful.

Acceptance:

- The feature-matrix copied-DB regression passes only when no persistent table
  changes.
- The test output identifies any changed table directly if the invariant
  regresses later.

### Step 6: Prepare, but do not execute, the live DB repair lane

Files:

- plan/runbook only; no source mutation required for this step

Actions:

- Keep live DB repair out of the code-change branch.
- Document the exact repair gates below so the later operator pass is bounded.

Acceptance:

- The implementation branch proves the invariant on a scratch DB.
- Live DB repair remains a separate explicit operator action.

## Blunt Acceptance Criteria

1. `python run_pipeline.py process --dry-run --db-path <scratch-copy>` does not
   change any persistent table in the scratch DB.
2. Process dry-run no longer mutates `signal_processing`.
3. Process dry-run no longer mutates `thesis_classifications`.
4. Process dry-run no longer mutates `confidence_ledger`.
5. Process dry-run no longer mutates `run_history`.
6. Process dry-run no longer mutates `claim_facts`.
7. Process dry-run no longer mutates `exit_predictions`.
8. Process dry-run no longer mutates `investor_matches`.
9. Process dry-run no longer mutates `notion_outbox`.
10. Process dry-run no longer mutates suppression cache tables.
11. Process dry-run no longer mutates `shadow_log`.
12. Process dry-run no longer mutates `functional_schemas`.
13. Process dry-run no longer mutates `asset_to_lead`.
14. Process dry-run no longer mutates Phase G identity tables
    (`entity_aliases`, `entity_key_aliases`, `entity_blocking_index`,
    `entity_migrations`).
15. Process dry-run no longer mutates founder tables
    (`founders`, `founder_experiences`, `founder_signals`,
    `founder_schema_migrations`).
16. Process dry-run no longer writes shadow-entity artifact or merge-suggestion
    tables when those features are enabled.
17. `audit_log` / `audit_events` are not expected to change on
    `process_pending`; if present, whole-DB before/after snapshots still show
    no change.
18. Former persistent observability evidence is emitted only through stdout,
    result payloads, or a non-persistent artifact sink.
19. A new write attempt during process dry-run fails closed through the
    process-wide helper/store/SQLite barrier.
20. Focused unit/integration/CLI verification passes on the feature matrix of
    copied DBs or targeted enabled-feature fixtures.
21. Live `signals.db` repair is not attempted until backup/export gates are
    complete.

## Risks and Mitigations

- Risk: read-only initialization breaks code that assumes migrations or FTS
  bootstrap always run.
  Mitigation: require existing DB for process dry-run and keep read-only init
  minimal; fail fast with a clear message if the DB is missing or incompatible.

- Risk: helper stores besides `SignalStore` still write during initialization.
  Mitigation: audit every same-DB initializer touched by process dry-run and
  either add read-only support or skip them for this command path. Explicitly
  include `EntityResolutionStore`, `FounderStore`, and any same-DB
  observability helper.

- Risk: process dry-run starts failing loudly because a hidden write path is
  blocked.
  Mitigation: that is desirable during rollout; convert each discovered write
  into explicit simulation behavior until the copied-DB regression is green.

- Risk: DB repair over-corrects rows that were not touched by dry run.
  Mitigation: require backup/export first and prefer confirmed candidate rows
  if no trustworthy pre-dry-run backup exists.

## Verification Steps

### Focused tests

```powershell
pytest tests/workflows/test_pipeline_dry_run.py tests/storage/test_signal_store_readonly.py tests/storage/test_entity_resolution_readonly.py tests/storage/test_founder_store_readonly.py tests/integration/test_process_dry_run_readonly.py -q
```

If a new run-tracking guard helper is introduced, add:

```powershell
pytest tests/workflows/test_run_manager.py -q
```

### Optional storage regressions if touched

```powershell
pytest tests/storage/test_signal_store_status.py tests/storage/test_thesis_classification_storage.py tests/storage/test_confidence_ledger.py tests/storage/test_signal_store_suppression.py -q
```

### Feature-matrix copied-DB proof

```powershell
pytest tests/integration/test_process_dry_run_readonly.py -q -k "preserves_all_persistent_tables"
```

Manual lane-by-lane proof uses the same snapshot entrypoint with explicit
commands:

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
```

```powershell
Copy-Item signals.db .tmp\signals-baseline.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-baseline.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-baseline.sqlite"
```

```powershell
Copy-Item signals.db .tmp\signals-claim-facts.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-claim-facts.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-claim-facts.sqlite" --lane claim_facts
```

```powershell
Copy-Item signals.db .tmp\signals-functional-schema.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-functional-schema.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-functional-schema.sqlite" --lane functional_schema
```

```powershell
Copy-Item signals.db .tmp\signals-entities.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-entities.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-entities.sqlite" --lane entities
```

```powershell
Copy-Item signals.db .tmp\signals-phase-g.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-phase-g.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-phase-g.sqlite" --lane phase_g_identity_resolution
```

```powershell
Copy-Item signals.db .tmp\signals-shadow-entity.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-shadow-entity.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-shadow-entity.sqlite" --lane shadow_entity_resolution
```

```powershell
Copy-Item signals.db .tmp\signals-exit-predictor.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-exit-predictor.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-exit-predictor.sqlite" --lane exit_predictor
```

```powershell
Copy-Item signals.db .tmp\signals-investor-matching.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-investor-matching.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-investor-matching.sqlite" --lane investor_matching
```

```powershell
Copy-Item signals.db .tmp\signals-founder-scoring.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-founder-scoring.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-founder-scoring.sqlite" --lane founder_scoring
```

```powershell
Copy-Item signals.db .tmp\signals-combined-high-risk.sqlite
python -m tests.support.db_snapshot compare-dry-run --db-path .tmp\signals-combined-high-risk.sqlite --command "python run_pipeline.py process --dry-run --db-path .tmp\signals-combined-high-risk.sqlite" --lane combined_high_risk
```

No other snapshot command or "equivalent scripted check" is the acceptance
proof for this slice.

### Diff review

```powershell
git diff --name-only
git diff -- run_pipeline.py workflows/pipeline.py workflows/run_manager.py workflows/suppression_sync.py storage/signal_store.py storage/entity_resolution.py storage/founder_store.py storage/entity_identity_store.py tests/workflows/test_pipeline_dry_run.py tests/workflows/test_run_manager.py tests/storage/test_signal_store_readonly.py tests/storage/test_entity_resolution_readonly.py tests/storage/test_founder_store_readonly.py tests/integration/test_process_dry_run_readonly.py tests/support/db_snapshot.py
```

The Phase G table validator is not a functional prerequisite for this slice.
Only expand `_validate_phase_g_tables()` if implementation work already touches
that boundary; otherwise the copied-DB immutability proof remains the authority
for all Phase G identity tables.

## DB Repair Runbook Gates

Repair only after the code fix is merged locally and the scratch-copy proof is
green.

### Gate 1: Backup live DB first

```powershell
Copy-Item signals.db "signals.pre-dryrun-repair.2026-05-15.sqlite"
```

### Gate 2: Export candidate rows first

Export `signal_processing` and `thesis_classifications` candidate rows before
any delete/update. The timestamp boundary and candidate IDs are operator-
specific incident evidence and must come from the incident note or exported
query artifact, not from this implementation plan alone.

### Gate 3: Prefer confirmed rows if no trustworthy pre-dry-run baseline exists

If there is no verified pre-dry-run backup, do not mass-revert blindly. Start
only from candidate IDs and timestamps confirmed in the incident note or export
artifact that accompanies the repair run.

### Gate 4: Rehearse on a copy first

```powershell
Copy-Item signals.db .tmp\signals.dryrun-repair-rehearsal.sqlite
```

Apply the candidate repair logic to the copy first and verify the result before
touching `signals.db`.

### Gate 5: Live repair only after evidence review

Only then repair the confirmed dry-run-touched rows in the live DB. Keep the
repair script or SQL narrowly scoped to the exported candidate set.

## ADR

### Decision

Implement process dry-run as a true read-only execution lane by combining:

- dry-run-aware CLI/pipeline initialization,
- process-wide read-only mode for every helper that opens `config.db_path`,
- explicit no-write guards on known process-path mutations,
- defensive helper/store/SQLite read-only barriers,
- non-persistent observability replacement for former shadow/classification
  evidence,
- feature-matrix copied-DB immutability verification before any live repair.

### Observability Decision

Persistent observability tables are forbidden during process dry-run. Any
former shadow/classification/diagnostic evidence that previously landed in
tables such as `shadow_log`, shadow-entity run tables, or similar persistence
surfaces must instead be emitted through:

- stdout/log output,
- returned result/stat payloads,
- or an explicitly non-persistent artifact sink.

Dry-run observability is therefore preserved, but only through non-persistent
channels.

### Drivers

1. The current code mutates persistent tables at multiple layers during process
   dry-run.
2. User intent is explicit and blunt: compute/read/report is allowed; writes
   are not.
3. A helper-level mistake must fail closed rather than silently touching the DB.

### Alternatives Considered

- Pipeline call-site gating only.
- SQLite read-only barrier only.
- Narrow fix limited to the auto-push/no-connector branch.

### Why Chosen

It is the smallest approach that is both user-correct and regression-resistant.
Semantic gating keeps dry-run useful; process-wide helper/store barriers keep
it safe; non-persistent observability replacement preserves diagnostic value
without violating the invariant.

### Consequences

- Process dry-run will become stricter and may surface hidden write paths as
  explicit failures during development.
- Read-only initialization must reject missing/incompatible DB states instead
  of trying to bootstrap them.
- `workflows/run_manager.py` and other direct-write helpers may need small
  contract changes even though the user-facing bug lives in `process`.
- Former persistent observability evidence must move to stdout, returned
  payloads, or a non-persistent artifact sink.

### Follow-Ups

1. Consider whether `collect` or other "read-only" diagnostic lanes should
   reuse the same store barrier contract.
2. After repair, capture a short incident note with the exact repaired rows and
   evidence artifacts.
3. If dry-run write-barrier patterns generalize cleanly, document the contract
   for future pipeline helpers.

## Available-Agent-Types Roster

Approved roster for follow-up use in this repo/session:

- `planner`
- `architect`
- `tdd-guide`
- `python-reviewer`
- `code-reviewer`
- `security-reviewer`
- `build-error-resolver`
- `e2e-runner`
- `doc-updater`
- `docs-lookup`

Use only the roles above in follow-up orchestration unless the runtime confirms
additional local agent types.

## Follow-Up Staffing Guidance

### `$ralph` recommended path

Use Ralph as the default execution mode for this slice. The work is coupled
across CLI, pipeline, store, and verification, and it needs one owner keeping
the read-only invariant intact end to end.

Recommended lane split inside Ralph:

- implementation lane: `tdd-guide` at high reasoning
- evidence/regression lane: `python-reviewer` at high reasoning
- final sign-off lane: `architect` at standard or high reasoning
- optional unblock lane: `build-error-resolver` at medium reasoning if focused
  pytest/import issues stall progress

Launch hint:

```text
$ralph Implement .omx/plans/process-dry-run-readonly-ralplan-dr-20260515.md on branch fix/process-dry-run-readonly. Start with failing tests, make process --dry-run a process-wide read-only mode for every helper that opens config.db_path, add defensive helper/store/SQLite barriers, move dry-run observability to non-persistent outputs, prove feature-matrix copied-DB immutability, and leave live DB repair gated after backup/export plus incident evidence.
```

Ralph verification floor:

1. run the focused pytest command,
2. run the copied-DB CLI proof,
3. inspect the before/after table snapshot,
4. get `architect` approval before completion.

### `$team` recommended path

Use team mode only if parallelizing the coupled surfaces is worth the merge
overhead. Because current `omx team` uses one shared worker prompt, the safest
default is `tdd-guide` for all workers with explicit lane assignments.

Recommended headcount: 4 workers.

Lane allocation:

1. Worker 1: failing tests and copied-DB snapshot helper
2. Worker 2: CLI/pipeline dry-run plumbing and warmup/run-tracking changes
3. Worker 3: `SignalStore` read-only barrier and direct-write audit
4. Worker 4: verification harness, regression reruns, and diff guardrail check

Suggested reasoning levels:

- Workers 1-3: high
- Worker 4: medium/high
- leader synthesis and final sign-off: high

Launch hint:

```powershell
omx team 4:tdd-guide "Implement the approved plan in .omx/plans/process-dry-run-readonly-ralplan-dr-20260515.md on branch fix/process-dry-run-readonly. Split work into tests, process-wide DB mode plumbing, helper/store barriers, and verification. Preserve the blunt invariant: process --dry-run may compute/read/report but must not mutate any persistent table. Finish with feature-matrix copied-DB before/after table proof."
```

Team verification path:

1. Wait for all workers to reach terminal task state.
2. Leader integrates only the scoped diff.
3. Run the focused pytest command locally.
4. Run the copied-DB CLI proof locally.
5. Review `git diff --name-only` against the planned file boundary.
6. Run `architect` review after the integrated branch is green.
7. Only then shut the team down.

## Execution Handoff

Recommended execution order:

1. Create/switch to `fix/process-dry-run-readonly`.
2. Add failing workflow/storage/integration tests first.
3. Refactor the process CLI + pipeline initialization path so dry-run intent is
   known before first DB open.
4. Add the process-wide same-DB read-only mode and close helper/direct-write
   bypasses.
5. Gate every known process-path mutation and move former persistent dry-run
   evidence to non-persistent outputs.
6. Run focused pytest.
7. Run the feature-matrix copied-DB proofs.
8. Review the scoped diff.
9. Keep DB repair as a separate, backup-first follow-up.
