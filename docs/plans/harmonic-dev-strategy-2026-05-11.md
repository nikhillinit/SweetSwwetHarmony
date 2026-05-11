# Harmonic Discovery Engine - Development Strategy 2026-05-11

## 1. Strategic Context

**Project:** Harmonic Discovery Engine, Python VC deal-sourcing engine
**Repo:** `C:\dev\Harmonic`
**Current repo head at planning time:** `fa87071`
**Gate A execution head:** `c3c190e`
**Document status:** Gate A verified 2026-05-11; downstream work remains sequenced by the gates below
**Goal:** Restore full deal-sourcing capability without building on stale baseline, schema, or liveness assumptions.

### 1.1 Current Runtime Evidence

| Evidence | Value | Execution meaning |
|---|---:|---|
| Live DB `signals` rows | 614 | Current baseline evidence, not a hard future invariant |
| `backups/signals-20260511-030832.db` `signals` rows | 612 | Explicit comparison backup |
| `backups/signals-20260404-072102.db` `signals` rows | 612 | Explicit comparison backup |
| Live and inspected backup `PRAGMA user_version` | 0 | Header drift only; not schema truth |
| Live and inspected backup `MAX(schema_migrations.version)` | 53 | Runtime schema evidence |
| `storage/signal_store.py` `CURRENT_SCHEMA_VERSION` | 53 | Runtime schema contract |
| Current thesis filter path | `workflows/pipeline.py` -> `utils.thesis_filter` | Production path is known |
| Current Phase G architecture | custom `PhaseGEntityResolver` | Splink/DuckDB is future-option only |

### 1.2 Runtime Schema Truth

Runtime compatibility is defined by:

```text
MAX(schema_migrations.version) == storage.signal_store.CURRENT_SCHEMA_VERSION == 53
```

`PRAGMA user_version=0` is observed header drift. It must not be treated as the canonical schema version, migration truth, or restore compatibility gate.

### 1.3 Hard Constraints

- Do not write to production `signals.db` during planning or proof work.
- Schema preflight remains mandatory before Notion operations.
- All external access continues through the internal MCP boundary.
- SQLite stays on local filesystem storage for Phase 5.2 durability work.
- Device-availability gaps are ambiguous liveness/freshness gaps, not negative evidence that sources were empty or collector logic failed.
- Repo runbooks and external Obsidian vault notes are separate artifacts.

### 1.4 Decision Log

| ID | Decision | Status | Rationale |
|---|---|---|---|
| D1 | Declare baseline from live/backup evidence, not fixed 612-row text | Passed Gate A 2026-05-11 | Live DB is 614 rows; inspected backups are 612 rows |
| D2 | Start thesis-filter survivor analysis from `utils.thesis_filter` | Current runtime fact | `workflows/pipeline.py` imports and uses this path |
| D3 | Treat Phase G as current custom `PhaseGEntityResolver` | Current runtime fact | Splink/DuckDB remains a possible future ADR option only |
| D4 | DtACI adaptive lookback remains future IOF candidate | Deferred | Corpus-level calibration is blocked until Gate A passes |
| D5 | `tach` over `import-linter` remains future enforcement candidate | Deferred | Do not imply current enforcement until a repo PR adds it |

### 1.5 Gate A Execution Record

**Status:** Passed on 2026-05-11 at repo head `c3c190e`.

**Execution note:** The planning-time head was `fa87071`; execution happened after the strategy branch advanced to `c3c190e`. Runtime facts were rechecked at execution time, so downstream work should treat `c3c190e` or a freshly synced equivalent as the verified Gate A baseline.

**Read-only evidence:**

| Check | Result |
|---|---|
| `signals.db` | 614 `signals` rows, `PRAGMA user_version=0`, `MAX(schema_migrations.version)=53` |
| `backups/signals-20260511-030832.db` | 612 `signals` rows, `PRAGMA user_version=0`, `MAX(schema_migrations.version)=53` |
| `backups/signals-20260404-072102.db` | 612 `signals` rows, `PRAGMA user_version=0`, `MAX(schema_migrations.version)=53` |
| Runtime schema contract | `storage/signal_store.py` declares `CURRENT_SCHEMA_VERSION = 53` |
| Thesis-filter survivor path | `workflows/pipeline.py` imports and instantiates `utils.thesis_filter.ThesisFilter` |
| Phase G current path | `workflows/pipeline.py` imports and instantiates `utils.phase_g_entity_resolver.PhaseGEntityResolver` |
| External-vault path hygiene | No repo-path assumptions found in this strategy document |

**Gate result:** Gate A is green for documentation, read-only diagnostics, and the next gated implementation slice. Runner changes, keepalive changes, Indiegogo work, live Phase G activation, and corpus-level calibration remain subject to their later gate criteria.

## 2. Dependency Graph And Execution Order

Gate A is the controlling execution gate. It must pass before any operational expansion or corpus-level calibration.

```text
Gate A: Runtime Source Of Truth
  - 3.1 Baseline and salvage declaration
  - 3.2 Thesis-filter survivor confirmation on live path
  - 3.6 Schema compatibility and header-drift wording
  - 3.4 Phase G read-only diagnostics may run in parallel

P1: Core Pipeline Health
  - 3.3 Thesis-filter behavior fixes, after 3.2 and Gate A
  - 3.4 Phase G read-only diagnostic, no activation

P2: Infrastructure Base
  - 3.5 Always-on runner, blocked by Gate A
  - 3.6 Schema compatibility follow-through, starts in Gate A

P3: Quality Gates
  - 3.7 Gold set expansion, blocked by Gate A and P1
  - 3.8 IOF window calibration, blocked by Gate A and corpus growth
  - 3.9 Collapse `mark_held()` divergence, after Gate A

P4: New Signal Sources
  - 3.10 Indiegogo Phase 1 collector, blocked by Gate A and runner/liveness readiness

P5: Governance And Documentation
  - 3.11 KeepAlive / freeze-drill governance, blocked by Gate A for live changes
  - 3.12 Speculative-tool-adoption process
  - 3.13 Rater-count policy
  - 3.14 T1.2 ADR and lab replacement, contingent
```

### 2.1 Gate A Matrix

Allowed before Gate A passes:

- Thesis-filter survivor analysis, read-only.
- Phase G diagnostics, read-only.
- Repo/vault documentation prep tied to those read-only lanes.

Blocked until Gate A passes:

- Runner changes.
- Keepalive changes.
- Indiegogo work.
- Live Phase G activation.
- Corpus-level calibration.

## 3. Detailed Work Items

### 3.1 Gate A - Baseline And Salvage Declaration

**Goal:** Replace the stale fixed 612-row baseline with a measured baseline declaration.

**Evidence to preserve:**

- Live `signals.db`: 614 rows in `signals`.
- `backups/signals-20260511-030832.db`: 612 rows in `signals`.
- `backups/signals-20260404-072102.db`: 612 rows in `signals`.
- All three inspected DBs: `PRAGMA user_version=0`.
- All three inspected DBs: `MAX(schema_migrations.version)=53`.

**Execution rule:** This is a declaration and proof step. It must not mutate `signals.db`.

**Acceptance criteria:**

- Baseline language uses the measured 614-row live fact and named 612-row backups.
- No text treats `PRAGMA user_version` as canonical schema truth.
- Runtime compatibility is documented as `schema_migrations` plus `CURRENT_SCHEMA_VERSION`.
- Gate A pass/fail is explicitly recorded in the repo strategy document.

### 3.2 Gate A - Thesis-Filter Survivor Confirmation

**Goal:** Ground thesis-filter follow-up work in the production path that actually runs.

**Current fact:** `workflows/pipeline.py` imports and uses `utils.thesis_filter`.

**Execution rule:** This lane may run before Gate A passes only as read-only analysis. Behavior-changing fixes wait until survivor confirmation and Gate A are green.

**Acceptance criteria:**

- Analysis starts from `workflows/pipeline.py` -> `utils.thesis_filter`.
- Any losing or legacy paths are described as migration/deprecation candidates, not production truth.
- No corpus-level KPI claims are made from unstable baseline evidence.

### 3.3 P1 - Thesis-Filter Behavior Fixes

**Goal:** Reduce over-rejection only after the live path is confirmed.

**Candidate issues to validate before patching:**

- Whether the active LLM skip threshold blocks useful rescue.
- Whether negative keyword handling over-rejects thesis-adjacent signals.

**Acceptance criteria:**

- Unit tests cover each behavior change.
- Dry-run processing evidence is captured without mutating production data.
- Rejection-rate claims are framed as provisional until corpus/liveness are stable.

### 3.4 P1 - Phase G Read-Only Diagnostic

**Goal:** Diagnose current Phase G behavior without changing architecture or activating live writes.

**Current fact:** Phase G currently uses the custom `PhaseGEntityResolver`.

**Execution rule:** Splink/DuckDB is not the active direction. It may appear only as a future option after current diagnostics complete.

**Acceptance criteria:**

- Diagnostic starts from the current `PhaseGEntityResolver` path.
- No plan step requires Splink/DuckDB as current architecture.
- No production telemetry or activation change ships before Gate A passes.

### 3.5 P2 - Always-On Runner

**Goal:** Eliminate laptop-dependent collection after runtime truth is stable.

**Blocked by:** Gate A.

**Architecture guardrails:**

- Dedicated host or equivalent always-on runner.
- Local-filesystem SQLite only.
- WAL/archive and off-host ledger decisions must align with the Phase 5.2 durability plan.
- Host-availability evidence is availability evidence only; it is not source-channel evidence.

**Acceptance criteria:**

- Repo runbook destination is `docs/runbooks/always-on-runner.md` if a runbook is added.
- Runner executes scheduled collection without human intervention.
- Restore and liveness evidence are documented before live promotion.

### 3.6 Gate A / P2 - Schema Compatibility And Header Drift

**Goal:** Make the schema contract explicit and stop treating header drift as migration truth.

**Current contract:**

```text
MAX(schema_migrations.version) == CURRENT_SCHEMA_VERSION == 53
```

**Acceptance criteria:**

- Strategy doc states that `PRAGMA user_version=0` is header drift only.
- Compatibility checks and restore docs point to `schema_migrations` plus `CURRENT_SCHEMA_VERSION`.
- Any future header-normalization task is separate from compatibility proof.

### 3.7 P3 - Grow Gold Set To 275 Signals

**Goal:** Power conformal calibration and agreement tracking.

**Blocked by:** Gate A, thesis-filter confirmation, and stable collection/liveness evidence.

**Acceptance criteria:**

- 275 labeled signals with 2-3 raters per signal.
- Kappa and PABAK reported together.
- Calibration commands use the real script path under `scripts/` when invoked.

### 3.8 P3 - IOF Window Calibration Sweep

**Goal:** Replace fixed-window assumptions only after the corpus is stable enough.

**Blocked by:** Gate A and meaningful corpus growth.

**Acceptance criteria:**

- Sweep compares explicit windows.
- Success metric is precision at k against known-good labels.
- No IOF decision is made from host-unavailable collection gaps.

### 3.9 P3 - Collapse `mark_held()` Divergence

**Goal:** Reduce held-state write-path divergence.

**Blocked by:** Gate A.

**Acceptance criteria:**

- Existing call sites are mapped before implementation.
- A single canonical dispatcher is selected.
- Enforcement tooling is added only if the repo adopts it in the same PR or a follow-up PR.

### 3.10 P4 - Indiegogo Phase 1 Collector

**Goal:** Add a high-moat marginal source after runtime and liveness foundations are stable.

**Blocked by:** Gate A and runner/liveness readiness.

**Acceptance criteria:**

- Collector follows the project collector framework.
- Dry-run output is measured without overstating coverage.
- New source metrics distinguish host availability from source emptiness.

### 3.11 P5 - KeepAlive And Freeze-Drill Governance

**Goal:** Re-enable live liveness guarantees only after the runtime truth gate passes.

**Blocked by:** Gate A for live changes.

**Execution rule:** The exact `HarmonicKeepAlive` status must be reverified before any live change. Existing freeze-drill evidence shows task activity, so do not infer status from stale docs alone.

**Acceptance criteria:**

- Freshness source of record remains `signals.created_at`.
- File/runner witnesses are availability evidence only.
- Device-unavailable windows are classified as unknown collection opportunity.

### 3.12 P5 - Speculative-Tool-Adoption Process

**Goal:** Prevent future tool-adoption work from entering the roadmap without primary-source validation.

**Acceptance criteria:**

- Add or update a repo-local process document if needed.
- Tool claims cite primary docs or direct repository evidence.
- Interesting-but-unvalidated tools stay out of implementation lanes.

### 3.13 P5 - Rater-Count Policy

**Goal:** Clarify the 275 signals by 2-3 raters policy.

**Acceptance criteria:**

- Gold-set size, rater count, reporting metrics, and refresh policy are documented.
- Any external vault summary is synchronized separately from repo runbooks.

### 3.14 P5 - T1.2 ADR And Lab Replacement

**Goal:** Decide whether a scientist-soak or lab-replacement path is actually needed.

**Contingency:** Execute only if the underlying ADR commits to a soak pattern for the read path.

**Acceptance criteria:**

- If repo decision records are created, use `docs/decisions/` and create that directory explicitly in the PR.
- If no soak is needed, close this item as no-op.

## 4. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---:|---:|---|
| Stale repo-doc wording causes wrong execution sequencing | High | High | Full replacement of this strategy doc, not incremental patching |
| Schema header drift gets misread as migration drift | High | High | Runtime truth is `schema_migrations` plus `CURRENT_SCHEMA_VERSION` |
| External-vault path assumptions create broken repo references | Medium | Medium | Repo docs use repo-local paths only; vault sync is separate |
| Gate A delays visible operational progress | Medium | Medium | Allow read-only thesis-filter and Phase G diagnostics in parallel |
| Post-incident corpus is semantically degraded | Medium | High | Block corpus calibration and activation until Gate A passes |
| Device gaps are misread as collector failure | Medium | High | Classify unavailable host windows as unknown collection opportunity |

## 5. Success Metrics

| Metric | Current evidence | Target | Gate |
|---|---:|---|---|
| Runtime schema compatibility | `schema_migrations=53`, `CURRENT_SCHEMA_VERSION=53` | Explicitly documented and verified | Gate A |
| Header drift classification | `PRAGMA user_version=0` | Documented as drift only | Gate A |
| Live DB baseline | 614 `signals` rows | Declared as current measured baseline | Gate A |
| Backup comparison baseline | 612 `signals` rows in two named backups | Documented comparison evidence | Gate A |
| Thesis path certainty | `workflows/pipeline.py` -> `utils.thesis_filter` | Confirmed before behavior fixes | Gate A |
| Phase G architecture certainty | custom `PhaseGEntityResolver` | Confirmed before activation or redesign | Gate A |
| Runner/keepalive readiness | blocked | Open only after Gate A | P2/P5 |
| Corpus calibration readiness | blocked | Open only after Gate A plus stable corpus | P3 |

## 6. Verification Commands

Run from `C:\dev\Harmonic` in PowerShell.

### 6.1 Repo State

```powershell
git rev-parse --short HEAD
git status --short docs/plans/harmonic-dev-strategy-2026-05-11.md
```

Pass condition:

- `git rev-parse --short HEAD` returns the head recorded for the current execution. For this Gate A execution, it returned `c3c190e`; the planning-time head remains `fa87071`.
- The strategy file is shown as untracked or modified according to the current packaging state.

### 6.2 Schema And Row Counts

```powershell
python -c "import sqlite3; p=r'signals.db'; c=sqlite3.connect(f'file:{p}?mode=ro', uri=True); cur=c.cursor(); rows=cur.execute('select count(*) from signals').fetchone()[0]; uv=cur.execute('pragma user_version').fetchone()[0]; mv=cur.execute('select max(version) from schema_migrations').fetchone()[0]; print({'path': p, 'rows': rows, 'user_version': uv, 'max_schema_migrations_version': mv})"
```

Pass condition: rows `614`, `user_version` `0`, max schema migrations version `53`.

```powershell
python -c "import sqlite3; p=r'backups/signals-20260511-030832.db'; c=sqlite3.connect(f'file:{p}?mode=ro', uri=True); cur=c.cursor(); rows=cur.execute('select count(*) from signals').fetchone()[0]; uv=cur.execute('pragma user_version').fetchone()[0]; mv=cur.execute('select max(version) from schema_migrations').fetchone()[0]; print({'path': p, 'rows': rows, 'user_version': uv, 'max_schema_migrations_version': mv})"
```

Pass condition: rows `612`, `user_version` `0`, max schema migrations version `53`.

```powershell
python -c "import sqlite3; p=r'backups/signals-20260404-072102.db'; c=sqlite3.connect(f'file:{p}?mode=ro', uri=True); cur=c.cursor(); rows=cur.execute('select count(*) from signals').fetchone()[0]; uv=cur.execute('pragma user_version').fetchone()[0]; mv=cur.execute('select max(version) from schema_migrations').fetchone()[0]; print({'path': p, 'rows': rows, 'user_version': uv, 'max_schema_migrations_version': mv})"
```

Pass condition: rows `612`, `user_version` `0`, max schema migrations version `53`.

### 6.3 Code-Path Grounding

```powershell
rg -n "CURRENT_SCHEMA_VERSION\s*=\s*53" storage/signal_store.py
rg -n "utils\.thesis_filter|thesis_filter" workflows/pipeline.py
rg -n "PhaseGEntityResolver|Splink|DuckDB" workflows storage utils
```

Pass condition:

- `CURRENT_SCHEMA_VERSION = 53` is present.
- Pipeline thesis-filter references are present.
- Current Phase G references point to `PhaseGEntityResolver`; any Splink/DuckDB references are future-option or docs-only, not current runtime requirements.

### 6.4 Draft Hygiene

```powershell
rg -n "PRAGMA user_version|w[i]ki/" docs/plans/harmonic-dev-strategy-2026-05-11.md
```

Pass condition:

- `PRAGMA user_version` appears only as header drift or verification evidence, never as canonical schema truth.
- The vault-path pattern returns no repo-path assumptions.

## 7. Docs And Vault Sync

Repo docs must use repo-local paths only.

If a repo decision record is needed, use `docs/decisions/` and create that directory explicitly in the PR that introduces the decision record. Do not imply the directory already exists.

External Obsidian vault sync is separate. If this strategy needs to be mirrored there, write a separate status/strategy note under the external vault after the repo doc is correct. Do not duplicate repo runbooks verbatim into the vault.

## 8. Execution Handoff

### 8.1 Available Agent Types

- `planner`
- `architect`
- `critic`
- `explorer`
- `reviewer`
- `docs-researcher`
- `doc-updater`
- `python-reviewer`
- `database-reviewer`
- `security-reviewer`
- `code-reviewer`
- `tdd-guide`

### 8.2 Recommended `$ralph` Handoff

```text
$ralph replace docs/plans/harmonic-dev-strategy-2026-05-11.md with the approved consensus version; preserve phase/gate structure; use only PowerShell-native verification; verify schema facts, thesis-filter path, Phase G wording, and remove wiki-path assumptions
```

Recommended lanes:

1. `doc-updater`, medium reasoning: own the full-file replacement.
2. `explorer`, low reasoning: rerun repo and DB verification commands.
3. `architect`, medium reasoning: terminal approver for gate preservation and stale-content removal.

### 8.3 Recommended `$team` Handoff

```text
$team 3 "Replace docs/plans/harmonic-dev-strategy-2026-05-11.md in full with the approved plan, preserving prior structure/gates while fixing PowerShell verification commands and stale PRAGMA/wiki wording; verify against live signals.db and explicit backups"
```

Recommended staffing:

1. `doc-updater`, medium reasoning: rewrite the strategy doc.
2. `explorer`, low reasoning: run evidence commands and compare output to required facts.
3. `architect`, medium reasoning: terminal approver for unchanged gates, repo-path hygiene, and current-vs-future architecture wording.

Team verification path:

1. Worker 1 rewrites the doc.
2. Worker 2 runs the verification commands and posts evidence.
3. Worker 3 checks gate preservation, repo-path hygiene, and future-vs-current architecture wording.
4. Leader accepts only if all acceptance criteria are satisfied.

## 9. Acceptance Criteria

- The stale untracked file content is fully replaced.
- The document is ASCII-clean and has no mojibake carryover.
- Phase structure and Gate A logic are preserved.
- The live and backup DB facts are stated exactly.
- `PRAGMA user_version=0` is described only as header drift or verification evidence.
- Runtime schema truth is `schema_migrations` plus `CURRENT_SCHEMA_VERSION`.
- Phase G is described as current custom `PhaseGEntityResolver`; Splink/DuckDB is future-option only.
- Thesis-filter work starts from `workflows/pipeline.py` using `utils.thesis_filter`.
- No repo-local external-vault path assumptions remain.
- Verification commands are PowerShell-native and use the `signals` table.
