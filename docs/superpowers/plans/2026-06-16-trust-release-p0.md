# Trust Release P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the corrected Trust Release P0 critical path — protect the recovered signals.db, reconcile the strategy/sprint docs to live `main`, prove `process --dry-run` immutability, and revalidate the thesis gate (F6) — orchestrating the routable slices through Hermes.

**Architecture:** Six independently-committable workstreams. The safety-critical, deterministic git work (DB de-track) and doc edits are done **directly**; F6 runs as the **registered `hermes task thesis-eval`** wrapper (plan-only → dry-run → execute); the open-ended path-guard refactor is the candidate for `hermes run --execute` routing to an executor. Every Hermes mutation/billed step is preceded by a non-mutating dry-run.

**Tech Stack:** Python 3.11, SQLite (WAL), pytest, git (worktrees), Hermes routing harness (`ops.cli hermes`), Gemini (paid tier) for the thesis eval executor.

---

## Why this plan corrects the original (verified against live `main`, 2026-06-16)

| Original plan said | Live finding | Correction baked into this plan |
|---|---|---|
| P0-2: merge PR #271 | `origin/main` **and** local `main` at `275cded` = #271 **merged** | W2: **verify** landed, don't merge |
| P0-3: build the dry-run harness | PR #188 (`5c67b91`) merged; `tests/integration/test_process_dry_run_readonly.py` + `tests/support/db_snapshot.py` **exist** | W3: **verify coverage + close/rescope #187** |
| Appendix B: live DB *is* the 4-row file | **Working tree = 612 rows** (`81f8ab47…`, 9.76 MB, schema 53, integrity ok); **committed HEAD blob = 4-row** (`465a7255…`, 1,466,368 B); `assume-unchanged` (`h` flag) masks the divergence | W0: recovery already happened; **protect it** (de-track + clear the flag) — and it is **live-hazardous today** |
| Restore the 612-row baseline | Already restored (working tree) | W0: drop the restore step; keep only protection |
| `thesis-eval` not in registry | `ThesisEvalTask` **is** registered on `main`; `active-sprint.md` omits it | W2/W4: refresh sprint doc; run F6 via `hermes task thesis-eval` |

**The single most urgent item is W0.** A stray `git checkout <branch>` / `git reset --hard` / `git stash` / fresh clone will silently overwrite the recovered 612-row working tree with the committed 4-row blob, and `git status` currently reports "clean" because of the `assume-unchanged` bit. Do W0 first.

---

## File map

- **W0** modifies: git index/HEAD for `signals.db` (de-track), `.gitignore` (verify only — `signals.db` already at line 54). Creates: `signals.db.backup-pre-detrack-20260616` (local, gitignored).
- **W1** modifies: `docs/plans/2026-06-15-trust-release/00-strategy.md` (on branch `claude/affectionate-davinci-2g8wjg`).
- **W2** modifies: `docs/claude/active-sprint.md`.
- **W3** verifies: `tests/integration/test_process_dry_run_readonly.py`, `tests/support/db_snapshot.py`; may create a lane test if a gap is found.
- **W4** runs: `hermes task thesis-eval`; writes `artifacts/thesis_diagnostics/pr-gate.json` + ledger; modifies `docs/evals/thesis-golden-gate-baseline.md` (F6 status).
- **W5** creates: `storage/db_paths.py` + `tests/storage/test_db_paths.py`; modifies primary entry points (`workflows/pipeline.py:253`, `ops/scheduler.py:608,636`); reframes `scripts/red-team-hybrid/freshness_watchdog.py` doc only.

---

## Execution mechanism per workstream (the "via Hermes" mapping)

| WS | Mechanism | Rationale |
|----|-----------|-----------|
| W-pre, W0 | **Direct** | Env-prep + git de-track must be deterministic; no registered Hermes task exists, and delegating index/HEAD surgery on the production DB to an LLM executor is unsafe. |
| W1, W2 | **Direct** | Exact doc edits; content known. |
| W3 | **Direct** (pytest) | Verification of already-landed code. |
| W4 (F6) | **`hermes task thesis-eval`** | Registered, ledger-backed, gated wrapper. The clean Hermes-orchestrated slice. |
| W5 (guard) | **`hermes run --execute`** (optional) | Open-ended multi-site refactor; good fit for executor routing + Claude apply/verify. Falls back to direct if preferred. |

---

## W-pre: Environment prerequisite (Direct)

**Files:** none (environment only)

- [ ] **Step 1: Confirm the dependency gap**

Run: `python -c "import aiosqlite"`
Expected: `ModuleNotFoundError: No module named 'aiosqlite'` (confirms the gap that blocks storage/test code).

- [ ] **Step 2: Create and populate a venv**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -U pip
.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt
```

- [ ] **Step 3: Verify deps import**

Run: `.venv/Scripts/python -c "import aiosqlite, pytest; print('deps OK')"`
Expected: `deps OK`

- [ ] **Step 4: Use `.venv/Scripts/python` for every subsequent command in this plan.** (`.venv/` is already in `.gitignore`; no commit.)

---

## W0: Protect the recovered signals.db (Direct, do FIRST)

**Files:**
- Backup (create, local-only): `signals.db.backup-pre-detrack-20260616`
- Modify: git index + HEAD (de-track `signals.db`)
- Verify: `.gitignore:54` (already ignores `signals.db`)

**Branch:** operate in the **main checkout** (the live 612-row file and the `assume-unchanged` flag live here), on a fresh branch.

- [ ] **Step 1: Back up the recovered DB before touching anything**

```bash
cp signals.db signals.db.backup-pre-detrack-20260616
sha256sum signals.db signals.db.backup-pre-detrack-20260616
```
Expected: both hashes equal `ab1ca9c8cf13d0b69ee4c49a4ac808e3da3ab242ac69612be759d0e81d4c2b51`.

- [ ] **Step 2: Capture the pre-state (evidence)**

```bash
git ls-files -v signals.db          # expect: "h signals.db" (assume-unchanged set)
git cat-file -s HEAD:signals.db     # expect: 1466368 (the 4-row blob)
python -c "import sqlite3;c=sqlite3.connect('file:signals.db?mode=ro&immutable=1',uri=True);print('working-tree rows:',c.execute('select count(*) from signals').fetchone()[0])"
```
Expected: `h signals.db`, `1466368`, `working-tree rows: 612`.

- [ ] **Step 3: Create the branch**

```bash
git switch -c hardening/signals-db-detrack
```

- [ ] **Step 4: Clear assume-unchanged, then untrack (keep the working-tree file)**

```bash
git update-index --no-assume-unchanged signals.db
git rm --cached signals.db
```
Note: `--cached` removes it from the index/HEAD-to-be only; the 612-row working-tree file is preserved.

- [ ] **Step 5: Confirm `.gitignore` already covers it (no edit expected)**

Run: `grep -n '^signals.db$' .gitignore`
Expected: `54:signals.db`

- [ ] **Step 6: Verify the working-tree DB is intact and now untracked**

```bash
git status --short signals.db          # expect: nothing (ignored + untracked)
python -c "import sqlite3;c=sqlite3.connect('file:signals.db?mode=ro&immutable=1',uri=True);print('rows after detrack:',c.execute('select count(*) from signals').fetchone()[0])"
```
Expected: empty status line; `rows after detrack: 612`.

- [ ] **Step 7: Commit the de-track**

```bash
git add -A
git commit -m "fix(durability): untrack signals.db so git cannot clobber the live DB (#149)

The committed blob was the 4-row truncated incident file (465a7255, 1466368 B)
while the working tree holds the recovered 612-row DB; assume-unchanged masked
the divergence so git reported clean. Untracking removes the poisoned blob from
HEAD; the 612-row file is preserved as an ignored working-tree file.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: Prove a checkout can no longer clobber the DB**

```bash
git stash list   # expect empty
git switch main && git switch hardening/signals-db-detrack   # round-trip
python -c "import sqlite3;c=sqlite3.connect('file:signals.db?mode=ro&immutable=1',uri=True);print('rows after checkout round-trip:',c.execute('select count(*) from signals').fetchone()[0])"
```
Expected: `rows after checkout round-trip: 612` (was the whole point — pre-fix this could have reverted to 4).

> ⚠️ **HIGH-STAKES GATE:** Steps 4–7 mutate git history for the production DB. Confirm with the operator before Step 7 commit. Other branches still tracking the 4-row blob remain a hazard until they are also de-tracked or deleted (note in PR body).

---

## W1: Apply the review deltas to the strategy doc (Direct)

**Files:**
- Modify: `docs/plans/2026-06-15-trust-release/00-strategy.md`

**Branch:** `claude/affectionate-davinci-2g8wjg` (where the doc lives). `git fetch origin && git switch claude/affectionate-davinci-2g8wjg`.

- [ ] **Step 1: Fix Appendix B (#149 RCA) to reflect current state**

Replace the `**#149 (DB revert):**` bullet with:

```markdown
* **#149 (DB revert):** As of 2026-06-16 the **working tree** holds the recovered
  612-row DB (`sha256 ab1ca9c8…`, 9.76 MB, schema 53, integrity ok), but the
  **committed HEAD blob is still the 4-row truncated file** (`465a7255…`,
  1,466,368 B), and an `assume-unchanged` index bit was masking the divergence so
  `git status` read clean. A `git checkout`/`reset --hard`/clone would have
  silently restored the 4-row blob over the recovery. **Fix:** clear
  assume-unchanged + `git rm --cached` + commit, leaving the 612-row file as an
  ignored working-tree file (W0). The 612-row recovery is therefore **already
  done** — this milestone protects it; it does not restore it.
```

- [ ] **Step 2: Reframe P0-1 — drop the restore step**

In the P0-1 task list, delete sub-step 5 ("Bounded recovery (user-approved): restore the 612-row …") and replace with:

```markdown
5. **Recovery is already complete (612-row working tree).** This milestone only
   *protects* it (W0 de-track) and documents that the post-R19 ingest delta beyond
   612 rows is not recoverable from local candidates (regression baseline, not full
   restoration).
```

- [ ] **Step 3: Reframe P0-2 — verify, don't merge**

Replace the P0-2 `**Tasks:**` line with:

```markdown
* **Tasks:** PR #271 is **already merged** (`origin/main` = `275cded`). Verify the
  landed resolver (`scripts/ci/resolve_thesis_eval_mode.py`) and detector
  (`scripts/ci/detect_thesis_sensitive_changes.py`) behavior on `main`; document
  the landed behavior; refresh `docs/claude/active-sprint.md` (stale — rebuilt
  2026-06-02 at `ae5c573`, omits PRs #269/#270/#271 and the `thesis-eval` task).
```
And change the P0-2 heading from "Land the gate-hardening slice (merge PR #271)" to "**Confirm** the gate-hardening slice landed (PR #271)".

- [ ] **Step 4: Reframe P0-3 — verify coverage, don't build**

Replace the P0-3 `**Adopt the existing approved plan:**` paragraph's first sentence with:

```markdown
* **The harness already exists (PR #188, `5c67b91`):** `tests/support/db_snapshot.py`
  (`compare_dry_run`) and `tests/integration/test_process_dry_run_readonly.py`
  cover the entity-resolution, founder-store, `claim_facts`, and
  `shadow_entity_resolution` lanes. **Verify** lane coverage (esp. `run_manager`
  `run_history` + suppression-warmup), fill gaps, then **close or re-scope #187** —
  do not rebuild.
```

- [ ] **Step 5: Add the "Step 0: reconcile state" preamble**

Insert after section "## 2. Corrected critical path" a new subsection:

```markdown
### Step 0 — Reconcile state before any P0 work

* Repo truth: `git fetch origin main --prune`; confirm `origin/main` HEAD.
* Resolve the local `gh` 401: GitHub PR/issue state is currently unverifiable
  locally; re-auth `gh` (or confirm via browser) before triaging #149/#187/#148.
* Treat `active-sprint.md` as stale until W2 refreshes it.
```

- [ ] **Step 6: Commit**

```bash
git add docs/plans/2026-06-15-trust-release/00-strategy.md
git commit -m "docs(strategy): correct Trust Release P0 path against live main (#271/#188 landed, 612-row recovery done)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## W2: Verify #271 landed + refresh active-sprint.md (Direct)

**Files:**
- Modify: `docs/claude/active-sprint.md`

- [ ] **Step 1: Verify #271 landed and inspect the resolver/detector**

```bash
git log --oneline -1 origin/main                      # expect 275cded (#271 merge)
git show 275cded --stat | head -20
sed -n '1,40p' scripts/ci/resolve_thesis_eval_mode.py
sed -n '1,40p' scripts/ci/detect_thesis_sensitive_changes.py
```
Expected: `275cded` is the #271 merge; resolver returns `["provider doctor evidence is missing"]` when `doctor is None`; detector matches `integrations/hermes/tasks/*thesis*.py`.

- [ ] **Step 2: Confirm the registry now includes `thesis-eval`**

Run: `.venv/Scripts/python -c "from integrations.hermes.tasks.registry import registered_task_names; print('thesis-eval' in registered_task_names())"`
Expected: `True` (active-sprint.md currently omits it — the staleness this step fixes).

- [ ] **Step 3: Update the active-sprint header + Current State**

In `docs/claude/active-sprint.md`, change the rebuild anchor (lines 3–11) from `ae5c573` to the current `origin/main` HEAD (`275cded`) and add to "## Current State":

```markdown
- Refreshed 2026-06-16 to `origin/main` at `275cded` (merge of PR #271,
  thesis-golden-gate-hardening). Since the 2026-06-02 `ae5c573` rebuild, merged:
  PR #269 (thesis golden gate), PR #270 (thesis-eval Hermes CLI gate),
  PR #271 (golden-gate fail-closed hardening + included-router inventory).
- The live Hermes task registry now also includes **`thesis-eval`** (registered
  `ThesisEvalTask`) in addition to the 2026-06-02 list.
```

- [ ] **Step 4: Update the "Current Registry Evidence" task list**

Add `thesis-eval` to the live-tasks enumeration (lines 73–75) so it reads `…, restore-db, shadow-validate, suppression-sync, and thesis-eval.`

- [ ] **Step 5: Commit**

```bash
git add docs/claude/active-sprint.md
git commit -m "docs(sprint): refresh active-sprint to 275cded; add thesis-eval to registry list

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## W3: Prove process --dry-run immutability + triage #187 (Direct)

**Files:**
- Verify: `tests/integration/test_process_dry_run_readonly.py`, `tests/support/db_snapshot.py`
- Create (only if a lane gap is found): a new parametrized lane in the test above

- [ ] **Step 1: Enumerate the lanes the landed test covers**

Run: `.venv/Scripts/python -m pytest tests/integration/test_process_dry_run_readonly.py --co -q`
Expected: a list of `test_process_dry_run_preserves_all_persistent_tables[...]` params; record which lanes appear.

- [ ] **Step 2: Run the immutability proof**

Run: `.venv/Scripts/python -m pytest tests/integration/test_process_dry_run_readonly.py -v`
Expected: all PASS; each lane asserts `result.changed_tables == []`.

- [ ] **Step 3: Check the two lanes the original plan flagged as holes**

Run: `grep -nEi "run_manager|run_history|suppression|warmup" tests/integration/test_process_dry_run_readonly.py tests/support/db_snapshot.py`
- If both `run_manager`/`run_history` and suppression-warmup lanes are present and passing → coverage complete.
- If a lane is **missing**, add a parametrized case mirroring the existing `claim_facts` lane (same `compare_dry_run` snapshot/assert pattern), run it RED→GREEN, and commit.

- [ ] **Step 4: Decide #187 disposition**

- If Step 2 passes and Step 3 shows full coverage: prepare to **close #187** with the lane matrix + `pytest` transcript as evidence.
- If a gap was filled in Step 3: **re-scope #187** to the residual, attach the new test.

- [ ] **Step 5: Commit (only if Step 3 added a test)**

```bash
git add tests/integration/test_process_dry_run_readonly.py
git commit -m "test(dry-run): cover <lane> immutability lane (#187)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## W4: F6 thesis revalidation via Hermes (`hermes task thesis-eval`)

**Files:**
- Runs: `hermes task thesis-eval` (writes `artifacts/thesis_diagnostics/pr-gate.json` + ledger run dir)
- Modify (after a passing run): `docs/evals/thesis-golden-gate-baseline.md` (F6 status → done)

**Pre-req:** `GOOGLE_API_KEY` present in the shell (paid tier). This step **spends a billed Gemini call** (64 samples). `risk_level=medium`, `mutates_external_systems=False` — no production state is mutated.

- [ ] **Step 1: Plan-only (no execution, no cost)**

Run: `.venv/Scripts/python -m ops.cli hermes task thesis-eval --plan-only --json`
Expected: JSON plan with `dataset.exists=true`, `manifest.exists=true`, `benchmark.benchmark_sample_count=64`, fingerprint `536e081d…`, preflight gate `executor_supports_execute` resolvable.

- [ ] **Step 2: Preflight-only**

Run: `.venv/Scripts/python -m ops.cli hermes task thesis-eval --preflight-only --json`
Expected: all four preflight gates pass (`dataset_exists`, `manifest_exists`, `benchmark_manifest_valid`, `executor_supports_execute`).

- [ ] **Step 3: Dry-run (builds the prompt, still no executor call)**

Run: `.venv/Scripts/python -m ops.cli hermes task thesis-eval --dry-run --json`
Expected: `"dryRun": true, "mutationCommitted": false`, `sampleCount: 64`, a `promptPreview`, and a written `thesis_eval_dry_run.json` ledger artifact.

> ⚠️ **BILLED-OPERATION GATE:** Confirm with the operator before Step 4 (live Gemini eval).

- [ ] **Step 4: Execute the live eval**

Run: `.venv/Scripts/python -m ops.cli hermes task thesis-eval --execute --json --min-accuracy 0.9`
Expected: JSON with `accuracy >= 0.9`, `decision: "go"` (or the gate's pass verdict), `benchmarkFingerprint: 536e081d…`, and written `pr-gate.json` + ledger `run_record.json`.
- If it fails with `RateLimitError`: re-run; if persistent, raise retry/backoff per the strategy's P0-0 thin contingency. Do **not** mark F6 done on a rate-limited run.

- [ ] **Step 5: Record F6 result in the baseline doc**

Edit `docs/evals/thesis-golden-gate-baseline.md` — change the "## Step 6.1 re-validation (F6) - PENDING maintainer dispatch" section to record: the run date, accuracy, the 64-sample `536e081d…` fingerprint, and the ledger run path. Change "PENDING" → "COMPLETE".

- [ ] **Step 6: Commit**

```bash
git add docs/evals/thesis-golden-gate-baseline.md artifacts/thesis_diagnostics/pr-gate.json
git commit -m "eval(F6): record live 64-sample thesis golden-set revalidation (accuracy <X>)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## W5: In-tree DB guard + #148 freshness reframe

### W5a: Canonical DB path guard (new code; route via `hermes run --execute` or do directly)

**Files:**
- Create: `storage/db_paths.py`
- Test: `tests/storage/test_db_paths.py`
- Modify (primary entry points only, this PR): `workflows/pipeline.py:253`, `ops/scheduler.py:608`, `ops/scheduler.py:636`

> Context: `os.getenv("DISCOVERY_DB_PATH", "signals.db")` appears at 15+ sites. Full migration is a follow-up; this task introduces the central guard and routes the **production** entry points through it, behind a flag so CI/dev fixtures (which legitimately use an in-tree `signals.db`) are not broken.

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_db_paths.py
import os
import pytest
from storage.db_paths import resolve_canonical_db_path, InTreeDatabaseError, REPO_ROOT


def test_out_of_tree_path_resolves(monkeypatch, tmp_path):
    target = tmp_path / "signals.db"
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(target))
    monkeypatch.delenv("SIGNAL_DB_PATH", raising=False)
    assert resolve_canonical_db_path() == target.resolve()


def test_in_tree_path_fails_closed(monkeypatch):
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(REPO_ROOT / "signals.db"))
    monkeypatch.delenv("HARMONIC_ALLOW_IN_TREE_DB", raising=False)
    with pytest.raises(InTreeDatabaseError):
        resolve_canonical_db_path()


def test_in_tree_allowed_with_flag(monkeypatch):
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(REPO_ROOT / "signals.db"))
    monkeypatch.setenv("HARMONIC_ALLOW_IN_TREE_DB", "true")
    assert resolve_canonical_db_path() == (REPO_ROOT / "signals.db").resolve()


def test_resolution_order(monkeypatch, tmp_path):
    primary = tmp_path / "primary.db"
    secondary = tmp_path / "secondary.db"
    monkeypatch.setenv("DISCOVERY_DB_PATH", str(primary))
    monkeypatch.setenv("SIGNAL_DB_PATH", str(secondary))
    assert resolve_canonical_db_path() == primary.resolve()
```

- [ ] **Step 2: Run it to confirm RED**

Run: `.venv/Scripts/python -m pytest tests/storage/test_db_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.db_paths'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# storage/db_paths.py
"""Canonical signals DB path resolution with an in-tree fail-fast guard."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class InTreeDatabaseError(RuntimeError):
    """Raised when the canonical signals DB resolves inside the git working tree."""


def _is_in_tree(path: Path) -> bool:
    try:
        path.relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def resolve_canonical_db_path() -> Path:
    """Resolve the canonical signals DB path.

    Order: DISCOVERY_DB_PATH > SIGNAL_DB_PATH > "signals.db".
    Fails closed if the resolved path is inside the repo working tree, unless
    HARMONIC_ALLOW_IN_TREE_DB is truthy (CI/dev fixtures only).
    """
    raw = (
        os.getenv("DISCOVERY_DB_PATH")
        or os.getenv("SIGNAL_DB_PATH")
        or "signals.db"
    )
    path = Path(raw).expanduser().resolve()
    allow = os.getenv("HARMONIC_ALLOW_IN_TREE_DB", "").strip().lower() in {"1", "true", "yes"}
    if not allow and _is_in_tree(path):
        raise InTreeDatabaseError(
            f"canonical signals DB resolves inside the repo working tree: {path}. "
            f"Set DISCOVERY_DB_PATH outside {REPO_ROOT}, or set "
            f"HARMONIC_ALLOW_IN_TREE_DB=true for fixtures."
        )
    return path
```

- [ ] **Step 4: Run it to confirm GREEN**

Run: `.venv/Scripts/python -m pytest tests/storage/test_db_paths.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Route the production entry points through the guard**

In `workflows/pipeline.py:253`, replace `db_path=os.getenv("DISCOVERY_DB_PATH", "signals.db"),` with `db_path=str(resolve_canonical_db_path()),` and add `from storage.db_paths import resolve_canonical_db_path` at the top. Do the same at `ops/scheduler.py:608` and `:636`.

- [ ] **Step 6: Verify production entry points still import and the guard is wired**

Run: `.venv/Scripts/python -c "import workflows.pipeline, ops.scheduler; print('import OK')"`
Expected: `import OK` (no in-tree error at import time; the guard fires only at resolution call).

- [ ] **Step 7: Commit**

```bash
git add storage/db_paths.py tests/storage/test_db_paths.py workflows/pipeline.py ops/scheduler.py
git commit -m "feat(durability): central canonical DB path resolver with in-tree fail-fast guard (#149)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### W5b: #148 / news_api freshness reframe (doc-only)

**Files:**
- Modify: comment block in `scripts/red-team-hybrid/freshness_watchdog.py:73-79` is already correct (news_api intentionally excluded). No code change.

- [ ] **Step 1: Confirm news_api is intentionally non-operational**

Run: `sed -n '73,87p' scripts/red-team-hybrid/freshness_watchdog.py`
Expected: `DEFAULT_OPERATIONAL_COLLECTORS = ("hacker_news", "arxiv", "rss_feeds")` with the quota-constrained rationale comment.

- [ ] **Step 2: Triage #148 — do NOT put it on the P0 path**

Document on #148 that news_api is excluded-by-design from the watchdog default set; #148 stays at **P1-2** (extend `ops/collector_health.py` with `fresh_empty_expected`). No P0 action.

---

## Self-review

- **Spec coverage:** W0↔#149/P0-1, W1↔strategy deltas, W2↔P0-2, W3↔P0-3/#187, W4↔P1-1/F6, W5a↔P0-1 path guard, W5b↔P1-2/#148. The five review deltas are all represented (Appendix B fix W1.1; P0-2 reframe W1.3/W2; P0-3 reframe W1.4/W3; Step 0 W1.5; #148 stays W5b).
- **Placeholder scan:** no TBDs; the only `<X>`/`<lane>` tokens are runtime values (measured accuracy, the lane name found in W3.3) that cannot be known before execution — they are explicitly outputs, not unspecified steps.
- **Type consistency:** `resolve_canonical_db_path`, `InTreeDatabaseError`, `REPO_ROOT`, `HARMONIC_ALLOW_IN_TREE_DB` are named identically in test (W5.1), impl (W5.3), and wiring (W5.5).

---

## Sequencing & dependencies

1. **W-pre** → **W0** (do these first; W0 is the live-hazard fix).
2. W1, W2, W3, W5 are independent and parallelizable after W-pre.
3. W4 depends only on a valid `GOOGLE_API_KEY` (independent of the DB).
4. W4.Step4 (billed) and W0.Step7 (git history) are the two operator-gated actions.
