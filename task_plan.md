# Task Plan -- Post-Window Next Steps

**Branch:** TBD (per phase)
**Date:** 2026-03-24
**Goal:** Unblock LLM thesis activation for HN FP reduction, clean up working tree, and prepare for Step 4B.

**Current state:** Governance promoted LLM_THESIS_MODE shadow->active in audit_events. Runtime .env still shadow. Phase 2 complete: `--source-api` filter implemented in storage, pipeline, and CLI (pending commit).

---

## Skill Invocation Map

| Phase | Skill | When | Why |
|-------|-------|------|-----|
| 2 (start) | `/superpowers:test-driven-development` | Before writing any code | RED-GREEN-REFACTOR for source_api filter |
| 2 (end) | `/superpowers:verification-before-completion` | Before committing Phase 2 | Evidence that tests pass |
| 3 (T3.2) | `/llm-application-dev:llm-evaluation` | When reviewing dry-run results | Structured evaluation framework |
| 4 (end) | `/superpowers:verification-before-completion` | Before committing .env flip | DB evidence before commit |
| 5 (start) | `/thinking-frameworks-skills:kill-criteria-exit-ramps` | Before regret check | Objective go/no-go for Step 4B |

---

## Phase 1: Commit Working Tree Cleanup
**Status:** `complete`
**Priority:** P0 (prerequisite for all other work)
**Effort:** 5 min

Source: `git status --porcelain` at 2026-03-24T18:00Z -- 16 modified, 63 deleted, 84 untracked. All deletions verified safe (no dangling imports).

### Tasks
- [x] T1.1: Delete `IMPLEMENTATION_PLAN.md` -- `rm -f`, confirmed gone
- [x] T1.2: Commit deletions (64 files) -- `e82a6d8`
- [x] T1.3: Commit modifications (13 files: hunter.py, feature-activation.md, graph_cli.py, test files, plan-verification.md) -- `4895826`
- [x] T1.4: Verify pytest passes -- `python -m pytest tests/ -x -q`: 1,337 passed, 1 pre-existing flaky (test_ach_matrix_view, passes in isolation)

**Decision taken:** Split into 2 commits (deletions, then modifications). Conventional commit style.

**Out of scope (intentionally not staged):**
- `.claude/settings.local.json` -- auto-generated local permissions, not project code. Should stay in `.gitignore` or remain unstaged.
- `findings.md`, `progress.md`, `task_plan.md` -- active working documents, not ready for commit.
- 84 untracked files -- mix of scratch scripts, datasets, skill definitions, and session artifacts. Not in scope for Phase 1. Will be addressed if/when they become blocking.

---

## Phase 2: Add source_api Filtering (Storage + CLI)
**Status:** `complete`
**Priority:** P0 (unblocks Phase 3)
**Effort:** 25 min (TDD)

### Skill Gates
```
START -> invoke /superpowers:test-driven-development
  | RED: write failing test (T2.1)
  | GREEN: implement filter (T2.2-T2.5)
  | REFACTOR: clean up if needed
  | RED: write CLI integration test (T2.6)
  | GREEN: wire CLI flag
END -> invoke /superpowers:verification-before-completion
  | Evidence: pytest output showing all tests pass
  | Commit only after verification
```

### Why
Cannot selectively process HN signals without this. Mixed queue blocks LLM activation.

**RED TEAM FINDING:** `process` CLI has no `--collectors` or `--source-api` flag (verified via `--help`). Neither does `thesis-classify-batch`. Phase 2 must add filtering at BOTH the storage and CLI layers.

### Tasks
- [x] T2.1: Write failing test -- `get_pending_signals(source_api='hacker_news')` returns only HN
- [x] T2.2: Add optional `source_api` param to `get_pending_signals()` in `signal_store.py:2781`
- [x] T2.3: Verify GREEN -- 9/9 storage tests pass (3 new + 6 existing)
- [x] T2.4: Add `--source-api` flag to `process` subcommand in `run_pipeline.py`
- [x] T2.5: Thread `source_api` through 3 functions in `workflows/pipeline.py`:
  - `process_pending(dry_run, source_api)` at line 1196
  - `_process_signals_stage(dry_run, source_api)` at line 1638
  - `get_pending_signals(limit, source_api)` call at line 1673
- [x] T2.6: Write pipeline threading test -- verifies source_api threads from process_pending to get_pending_signals (2 tests)
- [x] T2.7: Run `/superpowers:verification-before-completion` -- 9/9 storage, 2/2 pipeline threading, 41/41 CRUD pass

### Implementation
**Storage layer** (~10 lines in `signal_store.py:2777`):
```python
async def get_pending_signals(
    self,
    limit: Optional[int] = None,
    signal_type: Optional[str] = None,
    source_api: Optional[str] = None,  # NEW
) -> List[StoredSignal]:
    ...
    if source_api:
        query += " AND s.source_api = ?"
        params.append(source_api)
```

**CLI layer** (~5 lines in `run_pipeline.py`):
```python
process_parser.add_argument("--source-api", help="Filter pending by source_api")
# In process handler:
await pipeline.process_pending(source_api=args.source_api)
```

---

## Phase 3: HN-Only Filtered Run with Temporary Threshold Override
**Status:** `pending`
**Priority:** P1 (core HN FP fix)
**Effort:** 20 min

### Why
HN signals have keyword_score=0 -> LLM always skipped at threshold 0.2. Must lower to 0.0 to let LLM classify HN signals.

### Safety Constraint
In active mode, thesis reject/hold paths call `mark_rejected()` and `update_signal_status()` BEFORE the dry-run Notion guard (`pipeline.py:2096-2143`). `--dry-run` only prevents Notion push, not processing state mutations. Therefore: use a **scratch DB copy** for the first active-mode run, not the production signals.db.

### Tasks
- [ ] T3.1: Copy signals.db to scratch: `cp signals.db signals_hn_scratch.db`
- [ ] T3.2: Run HN-only process on scratch DB with env override (PowerShell):
  ```powershell
  $env:THESIS_SKIP_LLM_BELOW="0.0"
  $env:LLM_THESIS_MODE="active"
  python run_pipeline.py process --source-api hacker_news --db-path signals_hn_scratch.db --dry-run
  ```
- [ ] T3.3: Review results -- apply evaluation framework below
- [ ] T3.4: If evaluation passes AND scratch results look safe, run on production signals.db (still with --dry-run)
- [ ] T3.5: Check FP rate:
  ```powershell
  python -m ops.cli quality --db signals.db stats --days 7
  ```
- [ ] T3.6: Decide: make THESIS_SKIP_LLM_BELOW=0.0 permanent or keep as run-time override?

### Evaluation Framework (T3.3)
Small-sample qualitative validation (28 signals, no ground truth labels):

| Check | Method | Pass Criteria | Kill Criteria |
|-------|--------|---------------|---------------|
| **Rejection rate** | Count LLM REJECT vs ACCEPT | >80% rejected (baseline: 98.7% FP) | <50% rejected (LLM not helping) |
| **Reason quality** | Spot-check 5 rejection reasons | Reasons cite B2B/non-consumer/non-startup | Generic/hallucinated reasons |
| **TP-loss check** | Query thesis_classifications for Wildex/FlightDeepResearch-like patterns | LLM would ACCEPT both TP patterns | Either TP pattern rejected |
| **Accept review** | Manually review ALL LLM ACCEPT signals | Accepted signals plausibly consumer/thesis-fit | Obvious FPs accepted |
| **Baseline delta** | Compare to keyword-only (0% useful for HN) | Any LLM classification > 0% = improvement | LLM adds no signal over keyword |

**Note:** 28 signals is too small for statistical significance. This is a go/no-go qualitative gate, not a quantitative benchmark. Accumulate 3-5 days of data post-activation for regression detection.

### Systems Thinking Notes
- **Archetype**: Shifting the Burden -- HOLD (symptomatic) prevented LLM (fundamental) from being tested
- **Highest-leverage move**: Phase 3 dry-run = information flow (Meadows level 6) -- breaks R1 vicious cycle
- **Watch for**: Delay risk (need 3-5 days of data), TP overcorrection, compensating loops
- **R1 vicious cycle**: HN->keyword_score=0->LLM skipped->HELD->queue grows->blocks activation->LLM stays shadow

---

## Phase 4: Flip LLM_THESIS_MODE to Active in .env
**Status:** `pending`
**Priority:** P1 (after Phase 3 validates)
**Effort:** 5 min

### Skill Gates
```
END -> invoke /superpowers:verification-before-completion
  | Evidence: thesis_classifications rows exist for HN signals
  | Evidence: LLM_THESIS_MODE reads as 'active' in runtime
  | Commit only after verification
```

### Preconditions
- Phase 3 evaluation passes on scratch DB
- Phase 3 production dry-run confirms no unexpected state mutations
- Pending queue is isolated via --source-api (Phase 2 complete)

### Tasks
- [ ] T4.1: Change `.env` LLM_THESIS_MODE=shadow -> LLM_THESIS_MODE=active
- [ ] T4.2: Run isolated HN processing (process only, NOT full -- no need to re-collect):
  ```powershell
  python run_pipeline.py process --source-api hacker_news --dry-run
  ```
- [ ] T4.3: Verify LLM classification appears in thesis_classifications table
- [ ] T4.4: Run `/superpowers:verification-before-completion` -- confirm DB evidence, commit

---

## Phase 5: Regret Check Preparation (2026-03-30)
**Status:** `pending`
**Priority:** P2 (due in 6 days)
**Effort:** 15 min

### Skill Gates
```
START -> invoke /thinking-frameworks-skills:kill-criteria-exit-ramps
  | Define: objective go/no-go thresholds for Step 4B
  | Define: rollback trigger conditions
  | Define: "extend observation" criteria
```

### Tasks
- [ ] T5.1: Invoke `/thinking-frameworks-skills:kill-criteria-exit-ramps` -- define success/fail/extend criteria
- [ ] T5.2: Verify promotion exists:
  ```powershell
  python -c "import sqlite3; c=sqlite3.connect('signals.db').cursor(); c.execute('SELECT entity_id, metadata FROM audit_events WHERE action_type=''feature_promote'' AND entity_id=''LLM_THESIS_MODE'''); print(c.fetchone())"
  ```
- [ ] T5.3: Run overdue check on 2026-03-30:
  ```powershell
  python -m monitoring.feature_gate overdue --db signals.db --json
  ```
- [ ] T5.4: Review FP rate delta:
  ```powershell
  python -m ops.cli quality --db signals.db stats --days 7
  ```
- [ ] T5.5: Apply kill criteria from T5.1 -- proceed to Step 4B, extend observation, or rollback

### Proposed Kill Criteria (to be refined by skill in T5.1)
| Outcome | Condition | Action |
|---------|-----------|--------|
| **GO** | HN FP rate drops >30pp AND zero TP loss AND no regressions on other sources | Proceed to Step 4B (MERGE_WRITES_ENABLED) |
| **EXTEND** | HN FP rate drops but <30pp, OR insufficient data (<50 signals processed) | Extend observation 7 more days |
| **ROLLBACK** | TP loss detected, OR FP rate unchanged/worse, OR regressions on other sources | Revert LLM_THESIS_MODE to shadow |

---

## Phase 6: Orphaned Test Coverage (Deferred)
**Status:** `deferred`
**Priority:** P3
**Effort:** 2-4 hours

21 test files deleted for active modules. Coverage gap exists for:
- BaseCollector run logic
- Codex/Kimi/Maestro integrations
- Quality ops CLI (labels, patterns, stats)
- Circuit breaker, claim extractor, slack notifier

**Decision:** Defer until after Step 4B promotion. Focus on activation path first.

---

## Decisions Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Source-api filter before threshold change | Isolate HN processing without affecting other sources | 2026-03-24 |
| HN-only filtered run + temp threshold override | Zero code risk beyond Phase 2, validates LLM before committing | 2026-03-24 |
| Defer orphaned test coverage | Activation path is higher priority | 2026-03-24 |
| Add TP-loss check to Phase 3 | Systems analysis: compensating loop risk | 2026-03-24 |
| Phase 3 dry-run is highest-leverage | Meadows level 6 (information flow) breaks R1 vicious cycle | 2026-03-24 |
| Expand Phase 2 to include CLI flag | Red team: `process` has no `--source-api` flag | 2026-03-24 |
| Scratch DB for first active-mode run | Code review: active mode mutates processing state before dry-run Notion guard | 2026-03-24 |
| Delete IMPLEMENTATION_PLAN.md | Superseded genesis doc, all 5 review findings confirmed | 2026-03-24 |
| PowerShell syntax for all commands | Environment is Windows/PowerShell, not bash | 2026-03-24 |
| `--db` on parent `quality` not subcommand | Verified via `ops.cli quality --help` | 2026-03-24 |
| T2.5 explicit 3-function threading path | pipeline.py has process_pending->_process_signals_stage->get_pending_signals chain | 2026-03-24 |
| T4.2 uses `process` not `full` | `full` re-collects; after .env flip we only need to process existing pending | 2026-03-24 |

---

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| (none yet) | -- | -- |
