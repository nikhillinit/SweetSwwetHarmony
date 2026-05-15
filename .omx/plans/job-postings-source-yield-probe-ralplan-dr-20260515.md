# Job Postings Source-Yield Probe - RALPLAN-DR

Status: consensus approved for execution handoff
Created: 2026-05-15
Task: bounded per-source yield probe for `greenhouse_jobs` and `ashby_jobs`
Context snapshot: `.omx/context/job-postings-source-yield-probe-20260515T155025Z.md`
Suggested branch: `feat/job-postings-source-yield-probe`

## Scope

Plan the next narrow slice after the Council stress test:

- explain where freshness disappears for `greenhouse_jobs` and `ashby_jobs`;
- keep the slice read-only by default;
- prefer a dedicated probe artifact/script over scheduler, keepalive, or
  collector redesign.

The probe must be explicit about fidelity. By default it is a
source-isolated diagnostic, not proof of what the live collector would have
saved. To keep its findings interpretable against runtime behavior, it must
also report the collector's first-match ordering context for each domain
(`runtime_first_match` and `would_short_circuit`).

The probe entrypoint must be async:

- CLI uses `asyncio.run(main())`.
- Fetch/normalize functions are async.
- The probe instantiates a throwaway `JobPostingsCollector(domains=[],
  store=None, asset_store=None, http=<shared CollectorHttpClient>)`.
- The probe calls `_check_greenhouse()` and `_check_ashby()` directly. It must
  not call `run()` or `check_domain()`.

Runtime-order fields are narrowly defined for this slice:

- `runtime_order_scope`: always `greenhouse_ashby_only` unless a later plan
  explicitly widens the probe.
- `runtime_first_match`: the first normalized source for the domain within the
  production order up to Ashby: Greenhouse, then Ashby. Lever and Workable must
  not be probed in this slice except in a separately approved wider mode.
- `would_short_circuit`: for an Ashby candidate, `true` only when Greenhouse
  also normalizes for the same domain and would have returned first in
  `JobPostingsCollector.check_domain()`; for a Greenhouse candidate it is
  `false`; when the probe cannot establish the ordering, report `unknown`, not
  `false`.

This slice is diagnosis-only. It must not redesign keepalive semantics, mutate
live DB state during planning, mutate Notion, touch DB recovery, or broaden
into general `job_postings` architecture work.

## Explicit File Boundaries

Preferred implementation surface:

- `scripts/red-team-hybrid/job_postings_source_yield_probe.py` (new)
- `tests/scripts/test_job_postings_source_yield_probe.py` (new)

Read-only inputs the probe may consume:

- `collectors/job_postings.py`
- `collectors/base.py`
- `storage/signal_store.py`
- `utils/evidence_key.py`
- `workflows/pipeline.py`
- `ops/collector_health.py`
- `ops/collector_heartbeat.py`
- `state/collectors.json`
- `signals.db` via SQLite read-only mode
- optional existing keepalive composite/watchdog artifact under
  `artifacts/keepalive/` as read-only context only

Allowed only if the probe cannot stay self-contained:

- `collectors/job_postings.py` for extraction of a pure normalization helper
- `collectors/base.py` for a pure non-runtime diagnostic helper with no
  behavior change

Do not touch:

- `scripts/red-team-hybrid/install_keepalive_task.ps1`
- `scripts/red-team-hybrid/keepalive_verdict.py`
- `scripts/red-team-hybrid/keepalive_monitor_ping.py`
- `scripts/red-team-hybrid/freshness_watchdog.py`
- `workflows/notion_pusher.py`
- `connectors/notion_connector_v2.py`
- `storage/migrations/*`
- `restore_db.py`
- `signals.db` in write mode
- `state/collectors.json` as a writer target
- `artifacts/keepalive/` as a probe output target
- generated keepalive `.cmd` files

## Evidence Reviewed

- `.omx/context/job-postings-source-yield-probe-20260515T155025Z.md`
- `collectors/job_postings.py`
- `collectors/base.py`
- `workflows/pipeline.py`
- `ops/collector_health.py`
- `ops/collector_heartbeat.py`
- `scripts/red-team-hybrid/freshness_watchdog.py`
- `scripts/red-team-hybrid/keepalive_verdict.py`
- `storage/signal_store.py`
- `utils/evidence_key.py`
- `.omx/plans/keepalive-composite-verdict-ralplan-dr-20260514.md`
- `.omx/plans/scheduler-admin-rbac-ralplan-dr-20260513.md`

Repo-grounded facts carried into this plan:

- `collectors/job_postings.py` emits `source_api=f"{ats_platform}_jobs"`, so
  the diagnosis unit is per ATS source, not the umbrella collector.
- `ops/collector_health.py` already encodes
  `job_postings -> (greenhouse_jobs, ashby_jobs, lever_jobs)`.
- `collectors/base.py` currently collapses same-run dedup, DB duplicate, and
  Notion suppression into one `signals_suppressed` counter.
- `workflows/pipeline.py` records `rows_inserted_this_iter` from
  `result.signals_new`.
- `freshness_watchdog.py` proves freshness from `signals.created_at`, which is
  the right outcome metric but not enough to explain source-specific yield loss.
- `storage/signal_store.py` is the authority for duplicate and suppression
  semantics: `initialize()` is not safe for a read-only probe because it runs
  the normal migration/init path; `is_duplicate()` checks evidence key first,
  then exact identity fallback; `check_suppression()` reads unexpired
  `suppression_cache` rows.
- `utils/evidence_key.py` is the authority for evidence-key computation:
  `sha256(source_api + "\x1f" + normalize_url(source_url))[:32]`.

## Principles

1. Diagnose at the source-API seam because `greenhouse_jobs` and `ashby_jobs`
   are the failing proof units.
2. Keep the first follow-up read-only and reproducible: inspect configured
   domains, normalized candidates, and suppression paths without changing live
   scheduler, DB, or Notion state.
3. Preserve existing keepalive semantics; the probe explains yield, it does not
   reclassify liveness.
4. Prefer a narrow artifact that separates evidence collection from any later
   runtime instrumentation decision.
5. Make suppression attribution explicit enough to distinguish no-candidate,
   pre-save suppression, and post-normalization duplicate paths.

## Top Decision Drivers

1. The current failure is source-specific: freshness disappears at
   `greenhouse_jobs` and `ashby_jobs`, not at the keepalive/scheduler layer.
2. Existing runtime counters are too coarse because `signals_suppressed`
   aggregates multiple branches and does not explain where the three suppressed
   rows came from.
3. The next slice must stay low-blast-radius and implementation-ready, which
   favors a read-only probe before any collector or pipeline instrumentation.

## Viable Options

### Option A: Standalone Read-Only Probe Script

Build `scripts/red-team-hybrid/job_postings_source_yield_probe.py` that:

- loads configured job domains or explicit domain inputs;
- fetches/normalizes Greenhouse and Ashby candidates using existing collector
  code paths through an async throwaway `JobPostingsCollector`;
- uses a single shared `httpx.AsyncClient` wrapped in `CollectorHttpClient` for
  live API calls, with `store=None` and no persistence path;
- calls `_check_greenhouse(board_id, domain)` and `_check_ashby(board_id,
  domain)` independently for each generated board ID; it must not call
  `check_domain()` because that method short-circuits and would hide Ashby
  candidates behind Greenhouse matches;
- treats the probe unit as an aggregated signal candidate per
  `(domain, source_api)`, matching the collector's one-signal-per-domain
  behavior rather than one result per posting;
- defaults to `source_isolated` diagnosis and reports runtime ordering context:
  `runtime_first_match` and `would_short_circuit`;
- attributes each candidate to one of a bounded set of stages:
  `fetched`, `normalized`, `same_run_duplicate`, `db_duplicate`,
  `notion_suppressed`, `would_insert`, `fetch_error`, `parse_error`;
- mirrors DB duplicate and suppression-cache checks through raw SQLite
  `mode=ro` queries or a tiny read-only facade; it must not instantiate the
  normal `SignalStore.initialize()` path;
- computes evidence keys using `utils.evidence_key.compute_evidence_key()` or a
  byte-for-byte equivalent helper imported from that module;
- emits JSON plus terminal summary to stdout by default, with optional
  caller-directed `--out` output.

Pros:

- Best match for the requested bounded diagnostic slice.
- Keeps runtime behavior unchanged.
- Produces reusable evidence for later implementation or incident review.
- Makes source-level yield visible without coupling to the daily scheduler path.

Cons:

- May require careful extraction of pure helper logic from `job_postings.py`
  if current methods are too collector-stateful.
- "Would insert" remains an offline inference unless every suppression branch is
  faithfully modeled.
- Findings are counterfactual unless interpreted with the reported runtime
  first-match / short-circuit context.
- Source-specific reuse depends on private collector methods; the dependency is
  acceptable for a diagnostic script but must stay isolated to the probe file.

### Option B: Add Runtime Instrumentation Inside `job_postings` / `BaseCollector`

Add per-source counters or detailed suppression telemetry to normal collector
execution, then rerun `job_postings`.

Pros:

- Highest fidelity to the live execution path.
- Could attribute suppression at the exact branch where it occurs.

Cons:

- Broadens the slice into collector/runtime behavior change.
- Risks conflating diagnosis with production instrumentation design.
- Pulls on scheduler/keepalive execution again, which Council explicitly
  deprioritized.

### Option C: Manual SQL Plus Artifact Triage Only

Use keepalive artifacts, `state/collectors.json`, and ad hoc SQLite queries
without writing a dedicated probe.

Pros:

- Zero code changes.
- Fastest short-term operator investigation.

Cons:

- Weak repeatability.
- Cannot explain pre-save normalization loss or same-run dedup accurately.
- Leaves no reusable tool for the next incident or for regression testing.

## Recommended Decision

Choose Option A.

Implement a standalone read-only probe script first. Keep it independent from
the scheduler/keepalive path, model the source-specific branches explicitly,
and make stdout the default output. If an artifact is needed, require the caller
to provide `--out`; do not make `artifacts/keepalive/` the canonical or default
probe destination.

Do not add production collector instrumentation in this slice unless the
standalone probe proves insufficient.

## ADR Snapshot

Decision:

- Use a standalone read-only per-source probe as the next diagnostic slice for
  `greenhouse_jobs` and `ashby_jobs`.
- Keep the probe outside the normal scheduler/keepalive execution path.
- Treat `signals.created_at` freshness as an outcome check, and use the probe
  to explain the branch where yield disappears before insertion.
- Treat probe findings as source-isolated by default, with explicit runtime
  ordering context so they can be reconciled against live collector behavior.

Drivers:

- Source-specific failure requires source-specific attribution.
- Existing counters collapse distinct suppression reasons.
- Low-blast-radius diagnosis is the fastest safe path after Council.

Alternatives considered:

- Runtime instrumentation in `collectors/job_postings.py` or `collectors/base.py`
- Manual SQL/artifact-only investigation

Why chosen:

- Narrowest path that creates repeatable evidence without mutating live state or
  reopening keepalive policy work.
- Safer than runtime instrumentation while still making the fidelity boundary
  visible to the operator.

Consequences:

- The first implementation lane is diagnostic, not corrective.
- The probe may need a small pure-helper extraction from
  `collectors/job_postings.py`.
- The probe must not use `SignalStore.initialize()` because that path can apply
  migrations; duplicate and suppression checks must be mirrored through
  read-only SQLite access.
- The probe is bound to the repo's duplicate contract in
  `storage/signal_store.py` and evidence-key contract in `utils/evidence_key.py`;
  future changes there may require updating the probe tests.
- A second slice may still be needed if the probe shows that live execution
  diverges from the modeled suppression branches.

Follow-ups:

- If the probe identifies a single suppression branch, open a narrow fix plan
  for that branch only.
- If the probe cannot faithfully model live behavior, promote a separate
  instrumentation slice with explicit runtime boundaries.

## Implementation Data Flow

```text
CLI args / env
  |
  v
resolve domains
  |-- --domains-file
  |-- --domains
  |-- JOB_POSTING_DOMAINS
  `-- fail fast
  |
  v
asyncio.run(main())
  |
  v
shared httpx.AsyncClient -> CollectorHttpClient
  |
  v
throwaway JobPostingsCollector(store=None, domains=[])
  |
  v
for each domain, sequentially:
  |
  +--> generate board IDs
  |
  +--> _check_greenhouse(board_id, domain)
  |
  +--> _check_ashby(board_id, domain)
  |
  `--> never check_domain(), never run()
          |
          v
aggregated candidate per (domain, source_api)
          |
          v
read-only attribution
  |
  +--> same-run duplicate
  +--> signals.evidence_key duplicate
  +--> exact tuple duplicate
  +--> suppression_cache unexpired hit
  `--> would_insert
          |
          v
stdout JSON / summary, optional caller-provided --out
```

## Execution Tasks

### Task 1: Define Probe Contract And Inputs

Files:

- `scripts/red-team-hybrid/job_postings_source_yield_probe.py`
- `tests/scripts/test_job_postings_source_yield_probe.py`

Actions:

- Define CLI inputs for:
  - `--domains-file` or `--domains`
  - fallback to `JOB_POSTING_DOMAINS` only for live runs when neither domain
    input is provided; fail fast with a clear message if all are empty
  - note that the probe uses `JOB_POSTING_DOMAINS`, not the collector CLI's
    `DOMAINS`, to avoid unintended coupling to ad hoc collector test runs
  - `--sources greenhouse,ashby`
  - `--db signals.db`
  - `--state state/collectors.json`
  - `--keepalive-artifact <path>` as optional read-only context
  - `--fixture-dir <path>` for deterministic no-network probe runs
  - `--max-domains <n>` defaulting to 20 for live runs; fail fast with a clear
    message when the resolved domain set exceeds the limit, and allow explicit
    override with a higher value
  - `--mode source-isolated|runtime-mirror` if implementation can support both;
    otherwise default to source-isolated and always report runtime ordering
    context
  - `--json`
  - `--out <path>`
- Require SQLite read-only mode when DB access is used.
- Use the Windows-safe SQLite URI pattern:
  `sqlite3.connect("file:signals.db?mode=ro", uri=True)` for repo-relative DBs;
  for absolute paths, URI-encode the resolved path and keep `uri=True`.
  Missing DB files must fail with a helpful message instead of creating a file.
- Keep Notion in inference-only mode; never call connectors.
- Forbid `artifacts/keepalive/` as the default output location.

Acceptance:

- CLI contract is explicit and test-covered.
- Probe defaults to read-only behavior and safe local output.
- Probe output states whether counts are source-isolated or runtime-mirrored.
- Probe has one canonical domain resolution order:
  `--domains-file`, then `--domains`, then `JOB_POSTING_DOMAINS`, then fail.
- Tests assert SQLite opens with `uri=True` and `mode=ro`, and that an attempted
  write raises `sqlite3.OperationalError`.

### Task 2: Reuse Or Extract Pure Source-Normalization Helpers

Files:

- `collectors/job_postings.py` only if unavoidable
- `scripts/red-team-hybrid/job_postings_source_yield_probe.py`
- tests above

Actions:

- Reuse existing Greenhouse/Ashby fetch + normalization logic through direct
  async calls to `_check_greenhouse()` and `_check_ashby()` on a throwaway
  collector instance.
- Do not call `check_domain()` or `run()`; both are production collector control
  flows, and `check_domain()` short-circuits on the first ATS match.
- Keep the private-method dependency behind a small probe-local adapter such as
  `fetch_source_candidate(collector, source, board_id, domain)`.
- If extraction is needed, extract only pure helper functions with no runtime
  side effects and no collector behavior change.
- Normalize to the aggregated signal shape the collector would save for one
  `(domain, source_api)` candidate; do not report individual postings as if
  each were a persisted signal.
- Keep Lever and other ATS sources out of scope. Do not use them for runtime
  first-match explanation in this slice; only Greenhouse can short-circuit
  Ashby for the scoped proof units.

Acceptance:

- Probe can produce normalized candidate records for Greenhouse and Ashby from
  deterministic fixtures.
- Probe execution is async end to end and the CLI entrypoint uses
  `asyncio.run(main())`.
- Candidate records include `domain`, `source_api`, `evidence_key`, canonical
  identity fields, `runtime_order_scope`, `runtime_first_match`, and
  `would_short_circuit`.
- No scheduler/keepalive behavior changes are introduced.

### Task 3: Add Suppression Attribution Model

Files:

- `scripts/red-team-hybrid/job_postings_source_yield_probe.py`
- optional pure helper in `collectors/base.py` only if self-contained reuse is
  materially cleaner
- tests above

Actions:

- Mirror the suppression branches visible from repo contracts:
  - same-run identity duplicate
  - DB duplicate by evidence-key fast path and exact identity fallback
  - Notion suppression cache hit through read-only `suppression_cache` lookup
  - unsuppressed candidate that would reach `save_signal`
- Use raw SQLite `mode=ro` queries or a minimal read-only facade that mirrors
  `SignalStore.is_duplicate()` and `SignalStore.check_suppression()` semantics.
- Mirror the exact duplicate authority:
  - compute `evidence_key` from `utils.evidence_key.compute_evidence_key`;
  - when evidence key exists, first query `signals.evidence_key = ?`;
  - then mirror the exact tuple fallback:
    `canonical_key`, `signal_type`, `source_api`, `detected_at`;
  - use blanket canonical-key duplicate checks only if the candidate lacks the
    tuple fields and document that as legacy fallback.
- Mirror the exact suppression-cache authority:
  - query `suppression_cache` by `canonical_key`;
  - require `expires_at > now`;
  - never refresh the cache or call Notion.
- Treat `state/collectors.json` as heartbeat evidence only, not as a suppression
  source of truth.
- Report counts and representative examples per source API.

Acceptance:

- Output distinguishes at least:
  `normalized_candidates`, `same_run_duplicates`, `db_duplicates`,
  `notion_suppressed`, `would_insert`, `errors`.
- The probe never increments runtime counters or writes to live stores.
- Tests prove the normal writable `SignalStore.initialize()` path is not used.
- Tests prove evidence-key fast path, exact tuple fallback, and unexpired
  suppression-cache lookup separately.

### Task 4: Produce Operator-Facing Report

Files:

- `scripts/red-team-hybrid/job_postings_source_yield_probe.py`
- tests above

Actions:

- Emit a compact per-source summary and a machine-readable JSON payload.
- Include enough evidence to answer:
  - were live postings fetched?
  - were candidates normalized?
  - would Greenhouse/Ashby runtime ordering have selected a different source
    first within this slice's `greenhouse_ashby_only` scope?
  - which suppression branch consumed them?
  - were any candidates left that would have inserted?
- Include snapshot context when available from keepalive/state inputs without
  rewriting those files.
- Default to stdout; write files only when `--out` is explicitly supplied.

Acceptance:

- A single run can explain whether yield disappeared before normalization,
  during duplicate checks, or only at DB freshness proof.
- The report is not stored under `artifacts/keepalive/` by default.

### Task 5: Validate With Fixtures And Read-Only Dry Runs

Files:

- `tests/scripts/test_job_postings_source_yield_probe.py`
- probe-specific API JSON fixtures under
  `tests/fixtures/job_postings_source_yield_probe/`

Actions:

- Add deterministic tests for:
  - Greenhouse candidate normalizes and lands in `would_insert`
  - Ashby candidate normalizes and lands in `db_duplicate`
  - async CLI boundary through `asyncio.run(main())`
  - direct `_check_greenhouse()` / `_check_ashby()` source-isolated calls, with
    `check_domain()` unused
  - runtime first-match / short-circuit context
  - domain resolution order, including `JOB_POSTING_DOMAINS` fallback
  - same-run duplicate attribution
  - suppression-cache attribution through read-only DB access
  - evidence-key duplicate fast path
  - exact tuple duplicate fallback
  - blanket canonical-key legacy fallback when tuple fields are unavailable
  - expired suppression-cache miss
  - `--max-domains` guard
  - no-postings / parse-failure branch
  - HTTP error branch: 404, timeout, and 5xx
  - JSON output shape
  - read-only DB open mode
- Use probe-local API JSON fixtures. Greenhouse fixtures should be raw Board API
  JSON responses; Ashby fixtures should be raw Posting API JSON responses.
  Existing monitoring fixtures under
  `tests/monitoring/fixtures/content_pipeline/ats/` are HTML embed fixtures and
  are not suitable for API normalization tests.
- Run a local dry probe against fixtures before any live-domain run.

Acceptance:

- The probe is repeatable without live network access.
- The result schema is stable enough for future incident comparison.
- Fixture tests prove aggregated signal-level output rather than per-posting
  persistence claims.

## Code Path Coverage Plan

```text
CODE PATH COVERAGE
===========================
[+] scripts/red-team-hybrid/job_postings_source_yield_probe.py
    |
    +-- main() / CLI
    |   +-- domain resolution: file -> arg -> JOB_POSTING_DOMAINS -> fail
    |   +-- source validation: greenhouse, ashby only
    |   +-- max-domain guard for accidental long live runs
    |   +-- read-only DB URI validation with uri=True and mode=ro
    |   `-- stdout JSON, terminal summary, optional --out
    |
    +-- fetch candidates
    |   +-- async throwaway JobPostingsCollector
    |   +-- _check_greenhouse(), not check_domain()
    |   +-- _check_ashby(), not check_domain()
    |   +-- no jobs / parse failure
    |   `-- HTTP 404, timeout, 5xx
    |
    +-- attribution
    |   +-- same-run duplicate
    |   +-- evidence_key duplicate
    |   +-- exact tuple duplicate
    |   +-- blanket canonical_key legacy fallback
    |   +-- unexpired suppression_cache hit
    |   +-- expired / missing suppression_cache miss
    |   `-- would_insert
    |
    +-- runtime ordering
    |   +-- Greenhouse first
    |   +-- Ashby first
    |   +-- would_short_circuit = true
    |   +-- would_short_circuit = false
    |   `-- would_short_circuit = unknown
    |
    `-- report
        +-- per-source counts
        +-- representative examples
        +-- JSON datetime serialization
        `-- clear failure messages
```

## Acceptance Criteria

- The implementation slice is limited to the preferred file boundary unless a
  tiny pure-helper extraction is explicitly justified.
- No scheduler, keepalive policy, Notion, migration, or DB recovery files are
  changed.
- The probe can attribute yield separately for `greenhouse_jobs` and
  `ashby_jobs`.
- The probe's unit is an aggregated candidate per `(domain, source_api)`, and
  output identifies runtime first-match / short-circuit context.
- The probe reports whether source loss occurs at fetch/normalize, duplicate
  suppression, or "would insert" stage.
- Any DB interaction is read-only and test-proven as such.
- Duplicate and suppression attribution mirrors the repo contracts without
  invoking writable `SignalStore.initialize()`.
- The probe names `storage/signal_store.py` and `utils/evidence_key.py` as the
  suppression/dedup authorities and has tests pinned to those contracts.
- The probe can run against fixtures and produce a stable JSON report.

## Pre-Mortem

1. Shadow-semantics failure: the probe reports plausible counts that do not
   match live collector behavior. Mitigation: default output must state
   `source_isolated`, include `runtime_order_scope`, and mark ordering unknown
   when it cannot be proven.
2. Accidental write failure: the probe instantiates `SignalStore.initialize()`
   or writes probe output into operational keepalive surfaces. Mitigation:
   tests assert read-only SQLite access, writable store initialization is not
   called, and `artifacts/keepalive/` is forbidden as a default output target.
3. Domain-source ambiguity failure: `$ralph` runs the tool without the same
   domains used by the collector and draws a false conclusion. Mitigation:
   canonical domain resolution is documented and the live verification command
   requires either `JOB_POSTING_DOMAINS`, `--domains`, or `--domains-file`.
4. Async boundary failure: the probe accidentally calls async collector methods
   from sync code or hides coroutine errors. Mitigation: async probe functions,
   CLI `asyncio.run(main())`, and tests around the entrypoint.
5. Windows DB-open failure: read-only SQLite opens without `uri=True`, or a
   missing DB silently creates a new file. Mitigation: exact `mode=ro` URI
   pattern, missing-file error handling, and a write-attempt test that must fail.

## Expanded Test Plan

Unit:

- domain resolution order;
- evidence-key computation path;
- aggregated candidate shape per `(domain, source_api)`;
- same-run duplicate attribution;
- runtime ordering fields for Greenhouse and Ashby;
- source validation and `--max-domains` guard;
- JSON serialization for datetimes and representative examples.

Integration:

- read-only SQLite duplicate checks for evidence key and exact tuple fallback;
- read-only SQLite opens with `sqlite3.connect("file:signals.db?mode=ro",
  uri=True)` or URI-encoded equivalent for absolute paths;
- attempted write against read-only DB raises `sqlite3.OperationalError`;
- read-only suppression-cache hit with `expires_at > now`;
- expired suppression-cache entry is treated as a miss;
- fixture-backed Greenhouse and Ashby normalization without network access;
- async throwaway collector path calls `_check_greenhouse()` and `_check_ashby()`
  independently and never calls `check_domain()`;
- JSON schema stability.

Observability:

- terminal summary includes fetched, normalized, duplicate, suppressed,
  would-insert, and error counts per source API;
- JSON includes `mode`, `runtime_order_scope`, domain source, DB path, and
  optional keepalive/state context hashes or timestamps;
- live read-only probe command produces enough evidence to answer whether yield
  disappeared before normalization, during suppression, or at DB freshness
  proof.

## Verification Commands

Baseline implementation verification:

```powershell
python -m pytest tests/scripts/test_job_postings_source_yield_probe.py -q
```

If helper extraction touches shared collector code:

```powershell
python -m pytest tests/scripts/test_job_postings_source_yield_probe.py collectors/test_job_postings_refactor.py tests/integration/test_full_pipeline.py -q
```

Read-only fixture probe with explicit domains:

```powershell
python scripts/red-team-hybrid/job_postings_source_yield_probe.py --sources greenhouse,ashby --domains dual.example,ashbyonly.example --fixture-dir tests/fixtures/job_postings_source_yield_probe --max-domains 20 --json
```

Optional live-but-read-only diagnostic run after implementation approval,
using the same configured domain list as the collector:

```powershell
$domains = $env:JOB_POSTING_DOMAINS
if (-not $domains) { throw "JOB_POSTING_DOMAINS is required for this canonical live probe; pass --domains or --domains-file for a different target set." }
python scripts/red-team-hybrid/job_postings_source_yield_probe.py --sources greenhouse,ashby --domains $domains --db signals.db --state state/collectors.json --keepalive-artifact artifacts/keepalive/2026-05-15-HarmonicKeepAlive.json --json
```

Optional artifact output, caller-directed and outside keepalive by default:

```powershell
$domains = $env:JOB_POSTING_DOMAINS
if (-not $domains) { throw "JOB_POSTING_DOMAINS is required for this canonical live probe; pass --domains or --domains-file for a different target set." }
python scripts/red-team-hybrid/job_postings_source_yield_probe.py --sources greenhouse,ashby --domains $domains --db signals.db --json --out .omx/artifacts/job-postings-source-yield-probe-20260515.json
```

Windows read-only DB proof expected in tests:

```python
conn = sqlite3.connect("file:signals.db?mode=ro", uri=True)
with pytest.raises(sqlite3.OperationalError):
    conn.execute("CREATE TABLE should_not_write(id INTEGER)")
```

## NOT In Scope

- Runtime instrumentation in `BaseCollector` or `job_postings.py`; open a
  second plan only if the read-only probe cannot answer the question.
- `check_domain()` reuse; it short-circuits and is the wrong shape for
  source-isolated diagnosis.
- Lever and Workable diagnosis; this slice is `greenhouse_ashby_only`.
- Scheduler, keepalive, watchdog, or verdict semantics changes.
- DB migrations, schema changes, or recovery work.
- Notion connector calls or suppression-cache refresh.
- Parallel domain fetching; sequential probing is safer for a diagnostic v1.
- Automatic probe artifact generation under `artifacts/keepalive/`.
- DB duplicate-query memoization; defer unless profiling shows a problem.

## What Already Exists

- `collectors/job_postings.py`: Greenhouse/Ashby async fetch and normalization.
  Reuse via a throwaway collector and source-specific private methods.
- `collectors/base.py`: same-run suppression order and runtime counter meaning.
  Mirror the logic read-only; do not call save paths.
- `storage/signal_store.py`: duplicate and suppression-cache contracts. Mirror
  with read-only SQLite queries.
- `utils/evidence_key.py`: evidence-key computation. Import or match exactly.
- `scripts/red-team-hybrid/freshness_watchdog.py`: freshness outcome checking.
  Treat as complementary evidence, not the probe implementation.
- `tests/monitoring/fixtures/content_pipeline/ats/`: HTML ATS discovery
  fixtures. Do not reuse for this probe's API JSON normalization tests.

## Failure Modes

| Codepath | Realistic failure | Test coverage required | User-visible behavior |
|---|---|---|---|
| DB open | DB missing or URI mode omitted | Missing-file and read-only write-failure tests | Clear error, no file creation |
| Greenhouse fetch | 404, timeout, 5xx | Fixture/mocked HTTP branches | Counted as fetch error with source context |
| Ashby fetch | Response shape drift | Parse-failure fixture | Counted as parse error, not a crash |
| Evidence key | Empty source URL | Empty-key fallback test | Falls through to tuple duplicate check |
| Suppression cache | Expired row | Expired-cache miss test | Candidate can still reach would_insert |
| Runtime ordering | Greenhouse found plus Ashby found | `would_short_circuit=true` test | Ashby marked counterfactual |
| JSON output | Datetime serialization | JSON schema test | Valid JSON or clear serialization error |

No critical silent failure is acceptable: every branch above needs either a test,
explicit error handling, or a clear operator-facing error.

## Worktree Parallelization Strategy

Sequential implementation, no parallelization opportunity.

The slice is one probe script plus one test file and fixture data. Splitting this
across worktrees would create more coordination cost than it removes.

## Sequential Staffing Guidance

### Ralph Lane 1: Executor

Mission:

- implement the standalone probe and only the smallest pure-helper extraction
  needed to keep the probe accurate.

Reasoning level:

- medium-high on source attribution, low on architecture changes.

Guardrails:

- stay inside the explicit file boundaries;
- do not add scheduler or keepalive behavior changes;
- do not write to `signals.db`, Notion, or `state/collectors.json`.

### Ralph Lane 2: Test-Engineer

Mission:

- harden fixture coverage, JSON schema assertions, and read-only DB-open proof.

Reasoning level:

- medium, with focus on branch completeness and regression resistance.

Guardrails:

- prove each suppression category with deterministic fixtures;
- avoid network-dependent tests;
- keep tests limited to the probe slice and any extracted pure helpers.

### Team Lane 3: Architect / Reviewer / Verifier

Architect:

- confirm the probe contract stays diagnostic-only and does not become shadow
  production instrumentation.

Reviewer:

- inspect whether suppression attribution faithfully mirrors repo contracts in
  `collectors/base.py` and `workflows/pipeline.py`.

Verifier:

- run the listed pytest commands plus one read-only probe invocation and check
  that the output answers the original question for both source APIs.

Suggested launch order:

1. executor
2. test-engineer
3. architect
4. reviewer
5. verifier

Team verification path:

- If executor and test-engineer agree that the probe isolates one dominant
  branch, route the next follow-up to a narrow fix plan for that branch only.
- If architect or reviewer finds that the probe cannot mirror live suppression
  semantics without runtime hooks, stop and open a second bounded RALPLAN-DR
  for instrumentation rather than widening this slice mid-flight.

## Consensus Check

This plan is implementation-ready if the intended next move is:

- a standalone read-only source-yield probe,
- scoped to `greenhouse_jobs` and `ashby_jobs`,
- with no scheduler/keepalive redesign and no live-state mutation.
