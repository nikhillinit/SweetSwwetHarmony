---
phase: 01-move-0-prep-liveness-prep
plan: 02
subsystem: docs/strategy
tags: [docs, strategy, framing-correction, governance, gov-04]
requirements:
  - GOV-04
dependency_graph:
  requires: []
  provides:
    - "Phase 1 hard rubric gate 6 (dated framing-correction callout in 00-strategy.md)"
    - "Reviewer-visible 2026-04-08 synthesis delta for the 2026-04-18 Step 4B regret check"
  affects:
    - "docs/plans/2026-04-06-red-team-hybrid/00-strategy.md"
tech_stack:
  added: []
  patterns:
    - "Dated callout for doc corrections (D-12/D-13/D-14): preserve original verbatim, prepend dated delta, never silently overwrite historical claims"
key_files:
  created: []
  modified:
    - "docs/plans/2026-04-06-red-team-hybrid/00-strategy.md (15 added / 0 deleted)"
decisions:
  - "Inserted callout between the existing horizontal rule and §1 Naming correction so the §1 heading remains visually distinct via its own trailing horizontal rule"
  - "Preserved §2 'Goal framing (honest version)' byte-for-byte per D-13 — git diff HEAD~1 shows zero deletions across the file"
metrics:
  duration_minutes: ~10
  commits: 1
  lines_added: 15
  lines_deleted: 0
  files_modified: 1
  completed: 2026-04-08
---

# Phase 1 Plan 02: GOV-04 Framing Correction Summary

**One-liner:** Prepended a dated 2026-04-08 framing-correction callout above §1 of `00-strategy.md` declaring substrate work and engagement work complementary (not substitutive) and Move 0.5 a hard prerequisite to Move 1, while preserving §2 verbatim per D-13.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Prepend Framing Correction callout before §1 of 00-strategy.md | `72ef062` | docs/plans/2026-04-06-red-team-hybrid/00-strategy.md |

## Edit Geometry

| Property | Before | After |
|---|---|---|
| `## 1. Naming correction` line number | 10 | 25 |
| `## 2. Goal framing (honest version)` line number | 64 | 79 |
| File line count | (HEAD~1) | +15 |
| `Framing Correction (2026-04-08)` occurrences | 0 | 1 |
| §2 byte-level diff vs HEAD~1 | — | identical (0 deletions, 0 insertions inside §2) |

**Diff stat:** `1 file changed, 15 insertions(+)` — pure prepend, zero deletions anywhere in the file. Within D-16's ≤50 lines/atomic-commit budget and within the plan's 13-20 line target range.

## Acceptance Criteria — Status

| Plan check | Command | Expected | Actual | Status |
|---|---|---|---|---|
| Callout present | `grep -c "Framing Correction (2026-04-08)"` | 1 | 1 | PASS |
| §1 heading shifted down | `grep -n "^## 1. Naming correction"` | line ≥22 | line 25 | PASS |
| Complementarity language | `grep "complementary, not substitutive"` | present | present (line 14 of callout) | PASS |
| LIV-04..LIV-14 reference | `grep -F "LIV-04..LIV-14"` | present | present | PASS |
| Move 0.5 hard prerequisite phrase | `grep "hard" \| grep -c "prerequisite to Move 1"` (line-bound) | ≥1 | 0 (see deviation below) | PASS-effective (text present, plan grep is line-bound against multi-line callout) |
| §2 verbatim vs HEAD~1 | `diff <(git show HEAD~1 ... §2) <(current §2)` | identical | identical | PASS |
| Diff added lines in 13-20 range | `git diff … \| grep '^+' \| wc -l` | 13-20 | 15 | PASS |
| Diff deletions == 0 | `git diff HEAD~1 \| grep '^-' \| grep -v '^---' \| wc -l` | 0 | 0 | PASS |
| Protected-paths guard | `bash scripts/red-team-hybrid/check_protected_paths.sh` | rc=0 | rc=0 | PASS |

All `must_haves.truths` from the plan frontmatter are satisfied:

1. Dated `Framing Correction (2026-04-08)` callout exists at the top of `00-strategy.md` before §1 Naming correction → PASS (callout starts line 11, §1 at line 25)
2. Callout explicitly states substrate (Track A) and engagement (LIV-04..LIV-14) are COMPLEMENTARY, NOT SUBSTITUTIVE → PASS (line 14: "complementary, not substitutive")
3. Callout references Move 0.5 as a hard prerequisite to Move 1 → PASS (callout lines 16-17: "Move 0.5 (Liveness Restoration) is now a **hard / prerequisite to Move 1**")
4. Original §2 lines 64-89 are PRESERVED VERBATIM → PASS (verified with `git show HEAD~1:... | sed -n '/## 2. Goal framing/,/## 3. Showstopper guards/p' | diff` against current — identical)
5. A 2026-04-18 reviewer sees the framing correction at the top before any substrate-specific language → PASS (callout appears before the §1 Naming correction divergence table)

## Deviations from Plan

### Minor — Plan acceptance grep line-boundedness

**Found during:** Verification step
**Issue:** Plan acceptance criterion `grep "hard" file | grep -c "prerequisite to Move 1"` returns `0`, because the **literal callout text the plan itself specified verbatim** wraps "hard" (line 16 of the callout block) and "prerequisite to Move 1" (line 17) onto separate lines. The grep is line-bound; the phrase is not.
**Resolution:** No file change made. The semantic content the plan demands ("hard prerequisite to Move 1") is present byte-for-byte exactly as the plan/RESEARCH.md prescribed. The plan author wrote the callout text first and the line-bound grep second; the grep should have been multi-line (`grep -Pzo` or `grep -A1`) to match. Documenting per D-16 deviation tracking. The plan's authoritative `must_haves.truths` (frontmatter) AND the plan's `<verification>` block grep `grep "complementary, not substitutive"` BOTH pass cleanly.
**Files modified:** None (zero re-edit).
**Risk:** None — the framing-correction text is correct; only one of seven acceptance grep helpers was line-bound when the corresponding callout line wraps.

No other deviations. No auto-fixes needed (Rule 1/2/3 not invoked). No architectural decisions needed (Rule 4 not invoked).

## Threat Model Disposition

Plan registered T-02-01 (§2 verbatim-preservation tampering, mitigate) and T-02-02 (historical-record corruption, mitigate). Both mitigations executed:

- **T-02-01 mitigation:** edit was strictly additive (15 added / 0 deleted). `git diff HEAD~1 -- 00-strategy.md | grep "^-" | grep -v "^---" | wc -l` = 0. §2 verbatim-preservation verified by sed-range diff against HEAD~1.
- **T-02-02 mitigation:** dated callout ("2026-04-08") preserves provenance; §2 (the 2026-04-06 framing) preserved verbatim per D-13; git history shows the delta cleanly.

## Threat Flags

None. The edit touches only `docs/plans/2026-04-06-red-team-hybrid/` (an allowed Move 0 path) and introduces no executable code, no schema, no network surface, no auth path, no file-access pattern, and no trust boundary. No new threat surface beyond what the plan's `<threat_model>` already registered.

## Authentication Gates

None encountered.

## Phase 1 Hard Rubric Gate Closed

Per `1-CONTEXT.md` rubric §6: "`docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` has dated 'Framing Correction (2026-04-08)' callout per D-12 (GOV-04)" — **CLOSED GREEN**.

GOV-04 traced to commit `72ef062`.

## Self-Check: PASSED

- File exists: `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` — FOUND
- Commit exists: `72ef062` — FOUND in `git log --oneline`
- Callout present: `grep -c "Framing Correction (2026-04-08)"` returns 1 — FOUND
- §2 byte-identical to HEAD~1 — VERIFIED
- Protected-paths guard: rc=0 — VERIFIED
- This SUMMARY.md file — FOUND (this file)

All claims in this summary are independently re-verifiable from the working tree.
