# Hermes Deliberation Handoff — Trust Release Steelman Review
# Date: 2026-06-16
# Session HEAD: de00bb0 (merge PR #276)
# Deliberation target: Trust Release Completion Proposal
# Prior work: Codebase-validated steelman produced this session; see PLAN_BRIEF section below.

## Objective

Run a Hermes multi-reviewer deliberation (codex, kimi, gemini) against the Trust Release
steelman proposal. The panel debates weaknesses, implementation gaps, and execution risks
that neither the author nor the validating reviewer caught. Produce a `deliberation_record`
artifact, then surface any `block` or `needs_changes` verdicts before implementation work begins.

---

## Prerequisites

1. Verify providers are enabled:

   ```powershell
   python -m ops.cli hermes providers doctor
   ```

   Confirm codex, kimi, and gemini are all enabled. If any are deferred, adjust the
   `--panel` flag to the available subset.

2. Write the debate brief to disk:

   ```powershell
   # The PLAN_BRIEF is embedded in this file (see bottom section).
   # Write it to:
   $planPath = "docs/plans/2026-06-15-trust-release/deliberation-brief.md"
   ```

   The content is the PLAN_BRIEF section below. The deliberation task reads up to
   12,000 characters from the plan file.

3. Run the deliberation (dry-run first to verify routability):

   ```powershell
   python -m ops.cli hermes run --dry-run --phase production --task deliberate `
     --plan docs/plans/2026-06-15-trust-release/deliberation-brief.md `
     --panel codex,kimi,gemini `
     --rounds 1 `
     --synthesizer gemini
   ```

4. If dry-run postflight passes, execute:

   ```powershell
   python -m ops.cli hermes run --execute --phase production --task deliberate `
     --plan docs/plans/2026-06-15-trust-release/deliberation-brief.md `
     --panel codex,kimi,gemini `
     --rounds 1 `
     --synthesizer gemini `
     --ack-risk I-ACK-RISK
   ```

5. Read the output artifacts from the run directory:
   - `artifacts/<run-id>/deliberation_record.json` — machine-readable verdicts
   - `artifacts/<run-id>/deliberation.md` — human-readable summary

6. For any reviewer returning `needs_changes` or `block`, read the `concerns` and
   `requiredChanges` fields and surface them verbatim to the operator.

---

## What to Do With Results

**If all reviewers return `approve`:** Copy artifacts to
`artifacts/deliberation/trust-release-20260616/` and proceed to Milestone 0 implementation.

**If any reviewer returns `needs_changes`:** For each `required_changes` entry, either:
- Apply the correction to the steelman proposal and re-run deliberation, or
- Write a documented exception with operator sign-off before proceeding.

**If any reviewer returns `block`:** Halt implementation. Surface the blocker to the
operator; the blocking concern must be resolved at the proposal level before any
code changes begin.

---

## Deliberation Ledger Location

Hermes writes artifacts under the run directory. After the run completes:

```powershell
# Find the most recent deliberation record:
Get-ChildItem artifacts/ -Recurse -Filter "deliberation_record.json" |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Select-Object FullName
```

The `deliberation_record.json` contains each reviewer's structured JSON response,
including `contentExcerpt` for each reviewer's raw response. Read it to diagnose any
skip verdicts.

---

## Context for Future Sessions

- Full codebase-validated steelman proposal: produced in session `425539d9-99b2-49c8-beb3-6674dc...`
- Revision log of corrections vs. prior proposal: see session transcript
- 10 adversarial angles (W1–W10) cover structural and architectural audit; all are
  represented in the debate brief at `docs/plans/2026-06-15-trust-release/deliberation-brief.md`
- Hermes deliberation artifacts expire per `onTask.freshnessTtlSeconds`
- If deliberation runs more than 24h before implementation begins, re-run it

---

## PLAN_BRIEF (already written to docs/plans/2026-06-15-trust-release/deliberation-brief.md)

The 10 weaknesses (W1–W10) are the highest-leverage adversarial angles from the
architectural audit:
- W1: Litestream pause semantics (no true pause command; SIGTERM + restart risks
  replica overwriting the restored file before generation reset)
- W2: DBToolLock 5s timeout incompatible with restore operation duration
- W3: Delta=0 parity gate is non-deterministic on sampling models; needs temperature=0.0
  or accuracy-floor framing
- W4: v52 migration (ADD COLUMN) requires exclusive write lock under active WAL writers
- W5: api_shape_changed circuit breaker durability — in-memory state resets on restart;
  DB-persisted state conflicts with scratch-DB diagnostic runs
- W6: vcrpy cassette staleness — no regeneration policy, stale cassettes mask the exact
  failures W5 is supposed to catch
- W7: check_pr_evidence.py body parsing is syntactically forgeable with placeholder content
- W8: Track B dependency inversion — 1A (db_anomaly.py) and 1B (restore hardening) are
  "parallel" but 1B references 1A's known_bad_shas.json output
- W9: M7 (trust status CLI) reads health v2 schema but depends on M3 landing first;
  undeclared ordering constraint
- W10: Two canonical plan documents after this proposal; no designation of which is
  authoritative for future sessions
