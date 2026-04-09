---
phase: 01-move-0-prep-liveness-prep
plan: 03
subsystem: governance / docs
tags: [docs, governance, bias-audit, withdrawal, GOV-01]
requirements: [GOV-01]
wave: A
dependency_graph:
  requires:
    - .planning/phases/01-move-0-prep-liveness-prep/1-CONTEXT.md (D-14, D-25, D-26)
    - .planning/phases/01-move-0-prep-liveness-prep/1-RESEARCH.md §"GOV-01"
    - docs/plans/2026-04-06-lob-progress-eval/bias-audit.md (read-only audit source of truth)
  provides:
    - artifacts/gov-01/grep_9pct_sweep.txt (D-26 audit evidence)
    - dated 9% withdrawal caveats in 05-holdout-cohort-design.md and 06-tier-2-recall-eval.md
    - rewritten §11 of 06-tier-2-recall-eval.md (D-25 REWRITE compliance)
  affects:
    - hard rubric gate 7 (Phase 1 verification)
    - GOV-01 closure (REQUIREMENTS.md)
    - SUB-01 charter rollup verification (Wave C dependency)
tech-stack:
  added: []
  patterns:
    - dated strikethrough caveat (D-14 pattern)
    - section REWRITE for load-bearing claims (D-25 pattern)
    - artifact-as-audit-evidence (D-26 pattern)
key-files:
  created:
    - artifacts/gov-01/grep_9pct_sweep.txt
    - .planning/phases/01-move-0-prep-liveness-prep/01-03-SUMMARY.md
  modified:
    - docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md
    - docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md
decisions:
  - "Followed RESEARCH.md Finding 3: D-25's 4-line scope is correct; bias-audit.md hits are read-only references that GOV-01 cites, not edits"
  - "Used the literal D-14/RESEARCH.md caveat skeleton verbatim for Tasks 2 and 3 — no improvisation on withdrawal language"
  - "Task 3 commit accepted size overrun (+53 -20 lines) per plan acceptance criteria; D-25 explicitly mandates REWRITE which is inherently larger than strikethrough"
metrics:
  duration_minutes: ~25
  tasks_completed: 3
  tasks_total: 3
  files_created: 2
  files_modified: 2
  commits: 3
  completed_date: 2026-04-08
---

# Phase 1 Plan 01-03: GOV-01 9% pipeline precision withdrawal — Summary

**One-liner:** Closes GOV-01 by wrapping the four D-25 9% pipeline precision claims in dated `Withdrawn 2026-04-08 (GOV-01)` caveats, fully rewriting §11 of `06-tier-2-recall-eval.md` to frame Tier-2 recall as the primary substrate-quality metric, and committing the wider D-26 grep sweep as audit evidence — all without touching protected paths.

## Tasks Executed

| # | Task | Status | Commit | Files Touched |
|---|------|--------|--------|---------------|
| 1 | Run wider 9% grep per D-26, classify, commit as audit evidence | DONE | `ac1313a` | `artifacts/gov-01/grep_9pct_sweep.txt` (new, +134 lines) |
| 2 | Add dated caveats to `05-holdout-cohort-design.md:44` and `06-tier-2-recall-eval.md:74` | DONE | `3d62d63` | both red-team-hybrid docs (+10 -3 lines) |
| 3 | Rewrite §11 of `06-tier-2-recall-eval.md` per D-25 (REWRITE not strike) | DONE | `d0dceeb` | `06-tier-2-recall-eval.md` (+53 -20 lines) |

All three tasks committed atomically per D-16. Each commit cites GOV-01 in the message body, references the D-25 / D-26 decisions, and uses `--no-verify` per the parallel-executor parent message (orchestrator validates hooks once after the wave completes).

## Deliverables

### 1. Audit evidence artifact (Task 1)

**File:** `artifacts/gov-01/grep_9pct_sweep.txt`

Captures the literal output of `grep -rn "9%" docs/ .planning/ CLAUDE.md` (108 matches), annotated inline with one of three categories:

| Category | Count | Meaning |
|----------|-------|---------|
| EDIT | 7 | D-25 target lines (the 4 file positions, including 3 lines inside §11 that get removed by the rewrite) |
| READ-ONLY | 94 | bias-audit source of truth (4), withdrawal-framed planning state files (PROJECT/STATE/REQUIREMENTS/ROADMAP), this plan file's quoted skeletons, verifier plan, RESEARCH/CONTEXT references |
| UNRELATED | 7 | substring false positives — 89% similarity scores, 88.9% precision (different metric), 98.69% HN FP rate, 99% JSON validity |

**RESEARCH.md Finding 3 confirmed:** scope is exactly D-25's 4 lines. No new EDIT-class hits discovered. The matrix is logged in the artifact header so any future reviewer can verify the decision in one read.

### 2. Dated caveats (Task 2)

#### `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` line 44

The "9% precision metric (per LOB.txt evaluation) is on signals" sentence is now wrapped in `~~strikethrough~~`, followed by a 6-line blockquote `Withdrawn 2026-04-08 (GOV-01)` callout that:
- cites `docs/plans/2026-04-06-lob-progress-eval/bias-audit.md` as the audit source
- explains the opportunistic-FP-sample mechanism that produced the bias
- states actual pipeline precision is unknown
- preserves the (still-correct) "companies that turn into meetings/fundings" framing

The leading sentence `to classify individual signals.` is preserved as instructed by the plan.

#### `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` line 74

The "Sanity check against existing ~9% number" fragment in the metric table row is wrapped in `~~strikethrough~~`, followed inline by a `Withdrawn 2026-04-08 (GOV-01):` caveat that states "use precision-on-pushed as a directional indicator only, not as a baseline comparison" and points to `bias-audit.md`. The metric row itself (the metric is still valid) and the rest of the table are unchanged.

### 3. §11 REWRITE (Task 3)

`docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` lines 265-285 (old §11 "The relationship to the existing 9% precision number") replaced with a new §11 structured as:

```
## 11. Why "9% precision" is not a valid baseline

  Withdrawn 2026-04-08 (GOV-01) callout citing bias-audit.md
  3-bullet selection-bias mechanism explanation

### Tier-2 recall is the primary substrate-quality metric
  4 bullets: hold-out split / once per move / Wilson CIs mandatory /
             do-not-iterate rule (and why iterating reproduces the
             original failure mode)

### What the precision/recall dichotomy still tells us
  preserves the precision-vs-recall lever distinction from the original §11
  but framed around UNKNOWN current precision instead of "9%"

  Final pointer to bias-audit.md for full reasoning
```

§10 (CI integration) and §12 (Open questions) verified intact via grep before commit. The blank-line + `---` separators on either side of §11 preserved.

## Acceptance Criteria — All PASS

### Task 1 (D-26 wider sweep)
- `test -f artifacts/gov-01/grep_9pct_sweep.txt` -> EXISTS
- Sweep file contains the 3 D-25 EDIT-class target lines
- Sweep file contains the 4 READ-ONLY bias-audit.md lines (57, 61, 78, 182)
- Each match annotated with EDIT / READ-ONLY / UNRELATED
- No new EDIT-class matches beyond D-25 (sweep matches RESEARCH.md Finding 3 matrix exactly)

### Task 2 (dated caveats)
- `grep -c "Withdrawn 2026-04-08 (GOV-01)" 05-holdout-cohort-design.md` = **1** (PASS, exact)
- `grep -c "Withdrawn 2026-04-08 (GOV-01)" 06-tier-2-recall-eval.md` = **2** after Task 3 (PASS, ≥1 required after Task 2; +1 from §11 rewrite)
- `grep -c "~~The 9% precision metric" 05-holdout-cohort-design.md` = **1** (PASS, exact)
- `grep -c "~~Sanity check against existing ~9% number~~" 06-tier-2-recall-eval.md` = **1** (PASS, exact)
- `grep "bias-audit.md" 05-holdout-cohort-design.md` ≥ 1 hit (PASS)
- `grep "bias-audit.md" 06-tier-2-recall-eval.md` ≥ 1 hit (PASS)
- Combined Task 2 diff: +10 -3 = 13 lines (well under D-16's ≤50-line budget)

### Task 3 (§11 REWRITE)
- `grep -c '## 11. Why "9% precision" is not a valid baseline' 06-tier-2-recall-eval.md` = **1** (PASS, exact)
- `grep -c '## 11. The relationship to the existing 9% precision number' 06-tier-2-recall-eval.md` = **0** (PASS, old heading removed)
- `grep "Tier-2 recall is the primary substrate-quality metric" 06-tier-2-recall-eval.md` ≥ 1 (PASS)
- `grep "selection bias" 06-tier-2-recall-eval.md` = **2** matches in §11 (PASS, ≥1 required)
- `grep -c "## 12. Open questions" 06-tier-2-recall-eval.md` = **1** (PASS, §12 intact)
- `grep -c "## 10" 06-tier-2-recall-eval.md` = **1** (PASS, §10 intact)
- Task 3 diff: +53 -20 = 73 lines (overruns D-16's 50-line soft target; **explicitly sanctioned by Task 3 acceptance criteria** because D-25 mandates REWRITE)

### Hard rubric gate 7 (Phase 1)

**`grep -rn "9%" docs/ .planning/ CLAUDE.md` post-edit verification:**

Every match in `docs/` is one of:

| Match category | Where | Status |
|----------------|-------|--------|
| Unrelated percentages (89%, 88.9%, 98.69%, 99%) | `entity-resolution-tuning.md`, `00-ITERATION-LOG.md`, `findings.md` x2, `vc_exit_predictor/findings.md`, `hn-fp-investigation.md` | OK (substring false positives, not 9% pipeline precision) |
| Read-only bias-audit source of truth | `bias-audit.md` lines 57, 61, 78, 182 | OK (DO NOT EDIT — these record the audit itself) |
| Already-framed withdrawal | `lob-progress-eval/README.md:11` ("was selection bias, not a real metric") | OK (already withdrawn-framed) |
| Strikethrough + caveat (Task 2 outputs) | `05-holdout-cohort-design.md:44,48`; `06-tier-2-recall-eval.md:74` | OK (Task 2) |
| Inside new §11 "Why 9% precision is not a valid baseline" | `06-tier-2-recall-eval.md:265-314` | OK (Task 3) |

`.planning/` matches are all in already-withdrawn framing (PROJECT, STATE, REQUIREMENTS, ROADMAP have GOV-01 task definitions or "✓ Corrected" status), the plan file itself, the verifier plan (01-10), and CONTEXT/RESEARCH which are the source documents for D-25/D-26.

`CLAUDE.md`: 0 hits.

**Zero unwrapped 9% pipeline precision claims remain. Hard rubric gate 7 PASS.**

## Deviations from Plan

**None.** Plan executed exactly as written. The literal caveat skeletons from RESEARCH.md §"GOV-01" and CONTEXT.md D-14 / D-25 were used verbatim. The §11 rewrite copied the structure suggested in RESEARCH.md (a + b + c + d sub-bullets) and the heading naming convention from D-25 ("Why '9% precision' is not a valid baseline").

The Task 3 commit-size overrun is **not a deviation** — the plan's Task 3 acceptance criteria explicitly says "this commit may exceed the 50-line soft target per D-16 but D-25 explicitly calls for a REWRITE which is inherently larger; document in commit message". Documented in commit `d0dceeb` body.

## Authentication Gates

**None encountered.** All work was pure markdown editing in non-protected paths.

## Protected Paths Compliance

**Zero touches** to `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, `storage/migrations/`. Verified before Task 1 commit (rc=0 against the live working tree). All four touched files (`artifacts/gov-01/grep_9pct_sweep.txt`, `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md`, `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md`, this `01-03-SUMMARY.md`) are in allowed Move 0 paths (`artifacts/`, `docs/plans/`, `.planning/`).

The `--no-verify` flag was used per the parallel-executor parent prompt instruction: "PARALLEL executor — use `git commit --no-verify` on all commits. Orchestrator validates hooks once after the wave completes."

## Wider Sweep Output (D-26)

Full output captured at `artifacts/gov-01/grep_9pct_sweep.txt` (108 grep matches, fully classified). Summary below:

```
108 total matches
  7 EDIT     (D-25 targets, includes lines that the §11 REWRITE removes)
 94 READ-ONLY (audit source of truth, already-framed withdrawals,
              plan/state/roadmap records, this plan + verifier plan,
              RESEARCH/CONTEXT references)
  7 UNRELATED (89% similarity, 88.9% precision, 98.69% HN FP, 99% JSON)
```

Files actually modified by GOV-01 (matches D-25 exactly):
- `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` (line 44 paragraph)
- `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` (line 74 table row + §11 lines 265-285 REWRITE)

## Commit Trail

| Commit | Task | Files | Lines |
|--------|------|-------|-------|
| `ac1313a` | Task 1 (D-26 wider sweep audit) | 1 created | +134 |
| `3d62d63` | Task 2 (dated caveats x2) | 2 modified | +10 -3 |
| `d0dceeb` | Task 3 (§11 REWRITE per D-25) | 1 modified | +53 -20 |

Total: **3 commits, 4 files (1 new + 2 doc edits + 1 doc reused for §11), +197 -23 lines.**

## GOV-01 Closure

**GOV-01 (Withdraw the "9% pipeline precision" claim from any active doc that quotes it; replace with "actual pipeline precision unknown until random-sampled cohort exists"):** **CLOSED by this plan.**

All four D-25 target lines now carry the dated withdrawal. §11 of `06-tier-2-recall-eval.md` is rewritten around Tier-2 recall as the primary substrate-quality metric with the 9% claim explicitly retired. The wider D-26 grep sweep is committed as audit evidence. Hard rubric gate 7 verified PASS.

The orchestrator should mark `GOV-01` complete in `REQUIREMENTS.md` after wave A closes. State updates (STATE.md, ROADMAP.md) are intentionally left to the orchestrator per the parent prompt: "Do NOT update STATE.md or ROADMAP.md — the orchestrator owns those writes after all worktree agents in the wave complete."

## Self-Check: PASSED

**Files verified to exist (Read tool, post-commit):**
- `artifacts/gov-01/grep_9pct_sweep.txt` — FOUND (134 lines, 7 EDIT / 94 READ-ONLY / 7 UNRELATED annotations)
- `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` lines 43-53 — FOUND (strikethrough + Withdrawn caveat present)
- `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` line 74 — FOUND (strikethrough + Withdrawn caveat in table cell)
- `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` lines 265-318 — FOUND (new §11 with all 3 subsections; §10 at line 245 intact; §12 at line 321 intact)

**Commits verified to exist (`git log --oneline`):**
- `ac1313a` Task 1 GOV-01 wider 9% grep sweep — FOUND
- `3d62d63` Task 2 GOV-01 dated caveats on 9% precision claims — FOUND
- `d0dceeb` Task 3 GOV-01 rewrite section 11 of 06-tier-2-recall-eval.md — FOUND

**Hard gate 7 grep verification:** PASS (no unwrapped 9% pipeline precision claims remain in any active doc; only audit-source / strikethrough / caveat / new-§11 / unrelated-percentage matches remain).

**Working tree:** clean post-Task-3 commit (`git status` reports nothing to commit).

All success criteria from the plan met. Plan 01-03 closed.
