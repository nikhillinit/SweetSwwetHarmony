---
phase: 01-move-0-prep-liveness-prep
plan: 04
subsystem: governance-docs
tags: [docs, governance, preconditions, freshness, liv-03, gov-03]
requirements: [LIV-03, GOV-03]
wave: 1
dependency_graph:
  requires: [LIV-02 (freshness_watchdog.py shipped in 4efe8cf)]
  provides:
    - "LIV-03 freshness precondition contract for 2026-04-18 Step 4B regret check"
    - "GOV-03 gate-contract spec for all governance gates (Phase 1 docs-only)"
    - "Phase 2 hand-off spec for governance/cli.py + state_policies.py + audit_events schema work"
    - "Phase 1 → Phase 2 handoff input #4 per CONTEXT.md D-31"
  affects:
    - "2026-04-18 Step 4B regret check (gives reviewer single discovery point + abort decision tree)"
    - "R19 closure (gate-time freshness verification — closes the silent-failure mode at the gate layer)"
    - "R20 interim mitigation (removes analyst from freshness critical path for governance gates per D-29)"
tech_stack:
  added: []
  patterns:
    - "two-file consolidation: one document hosts two related REQs (LIV-03 + GOV-03) per D-24"
    - "dated callout / explicit version-stamped status block at file head"
    - "exit-code semantics table with explicit no-fail-open posture (per T-04-02 mitigation)"
key_files:
  created:
    - "docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md (205 lines, 8 sections)"
  modified: []
decisions:
  - "Used the literal RESEARCH.md skeleton (lines 604-702) expanded with the operational-collectors list, threshold-discrepancy explanation, and explicit error-path subsection in §5 — all per the plan's <action> block. Single-file two-REQ consolidation honored per D-24."
  - "Reworded the second occurrence of '3 of the 4 operational collectors' (now 'three of these four collectors') to satisfy the acceptance criterion that the canonical phrase appears exactly once. Both sentences came from the literal action block; the rewording preserves meaning without changing the contract."
metrics:
  duration_minutes: 7
  completed_at: "2026-04-08T04:18Z"
  files_changed: 1
  insertions: 205
  commits: 1
---

# Phase 1 Plan 04: Step 4B Preconditions + Freshness Gate Contract Summary

**One-liner:** Shipped the LIV-03 + GOV-03 contract doc — a single 205-line, 8-section spec at `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` that the 2026-04-18 Step 4B regret reviewer can run, interpret, and act on without analyst involvement, closing R19 at the gate layer and providing the R20 interim mitigation surface for the governance lane.

## What Shipped

A new file at `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` covering:

| Section | Content |
| ------- | ------- |
| §1. The contract | freshness < 5 days for ≥3 of 4 operational collectors (`hacker_news`, `arxiv`, `rss_feeds`, `news_api`) over prior 7 days, before any governance gate evaluates its primary condition |
| §2. Verification command | `python scripts/red-team-hybrid/freshness_watchdog.py --json --threshold-hours 120` with exit-code semantics table (0=proceed / 1=postpone / 2=hard-escalate, no fail-open) |
| §3. Required precondition input format | Literal JSON schema mirroring `freshness_watchdog.py:render_json()` — every gate evaluator must capture this blob in `audit_events` |
| §4. Blocking vs advisory behavior | Per-gate table: Step 4B regret check is BLOCKING in Phase 1 + Phase 2; canary/drift advisory in Phase 1, blocking after Phase 2 ships the wrapper |
| §5. Failure escalation path | Auto-postpone on rc=1 with 3-cycle cap then human review; rc=2 immediately escalates with no auto-postpone (no fail-open) |
| §6. Gates this contract applies to | Step 4B regret check (primary), canary, drift, future state-promotion gates |
| §7. Phase 2 implementation hand-off | Exact code changes deferred to post-2026-04-19: `governance/cli.py --require-freshness`, `governance/state_policies.py freshness_precondition` field, `audit_events.precondition_audit_json` column |
| §8. Link back to R19 and R20 | Closes R19 root cause at gate layer; mitigates R20 by removing analyst from the freshness critical path |

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| 1 | Create 14-step4b-preconditions.md with LIV-03 + GOV-03 contract | `27d3408` | docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md |

## Acceptance Criteria — All Pass

| Check | Expected | Actual | Pass |
| ----- | -------- | ------ | ---- |
| `test -f docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` | exit 0 | exit 0 | ✓ |
| `wc -l` of new file | ≥ 90 | 205 | ✓ |
| `grep -c "^## 1\. The contract"` | 1 | 1 | ✓ |
| `grep -c "^## 2\. Verification command"` | 1 | 1 | ✓ |
| `grep -c "^## 3\. Required precondition input format"` | 1 | 1 | ✓ |
| `grep -c "^## 4\. Blocking vs advisory behavior"` | 1 | 1 | ✓ |
| `grep -c "^## 5\. Failure escalation path"` | 1 | 1 | ✓ |
| `grep -c "^## 6\. Gates this contract applies to"` | 1 | 1 | ✓ |
| `grep -c "^## 7\. Phase 2 implementation hand-off"` | 1 | 1 | ✓ |
| `grep -c "^## 8\. Link back to R19 and R20"` | 1 | 1 | ✓ |
| `grep "freshness_watchdog.py --json --threshold-hours 120"` | ≥ 1 | 1 | ✓ |
| `grep "exit code.*2"` (error-escalation path specified) | ≥ 1 | 4 | ✓ |
| `grep "LIV-03"` | ≥ 1 | 5 | ✓ |
| `grep "GOV-03"` | ≥ 1 | 3 | ✓ |
| `grep "R19"` | ≥ 2 | 8 | ✓ |
| `grep "R20"` | ≥ 2 | 5 | ✓ |
| `grep "3 of the 4 operational collectors"` (≥3 threshold present) | exactly 1 | 1 | ✓ |
| `bash scripts/red-team-hybrid/check_protected_paths.sh` rc | 0 | 0 | ✓ |
| Files modified under `governance/` or `storage/migrations/` | 0 | 0 | ✓ |

## Hard Rubric Gate Status (Phase 1 Rubric §"Hard gates", item 4)

> **Gate 4:** `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` exists and specifies LIV-03 preconditions + GOV-03 gate contract

**Status:** ✓ GREEN — file shipped, both REQs satisfied in one coherent narrative per D-24.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan acceptance criterion conflict resolved without changing contract semantics**

- **Found during:** Acceptance criteria run after writing the literal `<action>` block
- **Issue:** The plan's `<action>` block contains the phrase "3 of the 4 operational collectors" twice (once in §1 line 134 of the plan, once in §1 line 144). The plan's `<acceptance_criteria>` requires `grep -c "3 of the 4 operational collectors"` returns **exactly 1 match**. Writing the literal action text produced 2 matches and failed the criterion.
- **Fix:** Reworded the second occurrence (the "if fewer than..." sentence in §1) from "3 of the 4 operational collectors" to "three of these four collectors" — preserves meaning, keeps the canonical "≥3 of 4" threshold language present exactly once at the contract definition.
- **Files modified:** `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` (line 25)
- **Commit:** included in `27d3408` (single-task atomic commit)
- **Impact on contract:** None. Both phrasings express the same threshold (≥3 of 4 fresh collectors required).

### Authentication Gates

None — pure file-creation task.

## Threat Surface Scan

No new threat surface introduced. Pure docs-only deliverable per D-20/D-21. The plan's `<threat_model>` listed:

- **T-04-01 Tampering** (implicit code edits creeping in) — mitigated by zero protected-path edits (verified by `git show --pretty=format: --name-only HEAD` returning only the docs file)
- **T-04-02 DoS via fail-open on exit code 2** — mitigated by §2 + §5 explicitly specifying rc=2 as HARD ESCALATE
- **T-04-03 Repudiation via silent indefinite postponement** — mitigated by §5 capping auto-postpone at 3 cycles
- **T-04-04 EoP via "just quickly add" governance code** — mitigated by D-21 + `check_protected_paths.sh`

## Known Stubs

None. The contract doc is a complete spec; the only "stubs" are the explicit Phase 2 hand-off items in §7 which are intentional and explicitly scoped out per D-20/D-21.

## Files Changed

**Created (1):**
- `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` (205 lines)

**Modified (0):**
- (none)

**Protected paths touched:** 0
- `collectors/` — 0
- `workflows/` — 0
- `governance/` — 0
- `monitoring/` — 0
- `connectors/` — 0
- `storage/migrations/` — 0

## Commits

| Hash | Message |
| ---- | ------- |
| `27d3408` | docs(01-04): Step 4B preconditions + freshness gate contract (LIV-03 + GOV-03) |

## Self-Check: PASSED

- ✓ Created file `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` (verified by `Read` tool, 205 lines, header matches, all 8 sections present)
- ✓ Commit `27d3408` exists (verified by `git rev-parse HEAD` → `27d3408336fe1c3b58c8cbb9efbb4139173b1d3d`)
- ✓ Commit touches exactly 1 file in an allowed path (verified by `git show --pretty=format: --name-only HEAD` → single line `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md`)
- ✓ `bash scripts/red-team-hybrid/check_protected_paths.sh` rc=0 verified pre-commit AND post-commit
- ✓ All 17 acceptance criteria from the plan pass (table above)
- ✓ Both REQs (LIV-03 + GOV-03) satisfied by the same single-file commit per D-24 two-file-consolidation pattern
- ✓ Worktree base reset to expected `9bbabf83d86f6aff2edf9be29a9c3dcd8da4b8ea` before execution; merge-base verified equal post-reset
