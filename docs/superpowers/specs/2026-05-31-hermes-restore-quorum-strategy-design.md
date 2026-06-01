# Hermes Restore — Phase 2 Deliberation Unblock & Honest Quorum Strategy

- **Date:** 2026-05-31
- **Status:** Draft strategy for scrutiny/refinement (NOT an execution authorization)
- **Author:** Claude (brainstorming + RedTeam refinement), reviewed by external reviewer
- **Scope:** Unblock the Hermes `restore-db` Phase 2 deliberation and reach a genuine
  approving quorum, first against `signals.db.canary`, then (separately,
  operator-gated) against live `signals.db`.
- **Hard stop:** No live `signals.db` restore until Phase 2 returns `approved`
  (or a documented `DELIBERATION_OVERRIDE`) **and** the operator explicitly says go.

> This document is the deliverable. It does **not** execute even the safe
> (canary / deliberation) re-runs — those write Hermes ledger artifacts and are
> the first executable steps handed to the operator / next session.

---

## 1. Premise correction (why the original task framing is largely overtaken by events)

The task prompt recommends creating branch `codex/hermes-restore-plan-contract`,
adding failing tests, and implementing a restore-plan contract fix. **That work
already merged** as PR #245 (commit `3522fab`, "fix: clarify hermes restore plan
contract", merged 2026-05-31 12:48 PT) — on that exact branch name.

The blocked Phase 2 run reviewed a **pre-fix** plan:

- Blocked deliberation: `hermes_20260531_155328_ae2156bb`, executed 08:53 PT (15:53Z).
- Its input (`run_record.json.inputs.plan`) was
  `ai-logs/hermes/runs/hermes_20260531_155233_d6cbadd4/task_plan.json` — the
  **Phase-1 canary execute** plan, generated 08:52 PT, before the fix.
- Pre-fix diff (`git show 3522fab -- integrations/hermes/tasks/restore_db.py`)
  changed `affected_tables` from `["signals", "schema_migrations"]` to `["*"]`,
  and added `lock_scope` and `postflight_gate_contracts`. That `["signals",
  "schema_migrations"]` value is *exactly* Kimi's blocker #1 complaint.

**Conclusion:** The three Kimi blockers map 1:1 to fields PR #245 added. Kimi
reviewed a stale plan. Regenerating the plan from current `main` resolves the
substantive concerns. **Expected new-code outcome: zero** (pure ops + docs)
unless Workstream A finds a real gap.

### 1.1 Current-main contract fields (verified present)

`integrations/hermes/tasks/restore_db.py` `plan()` now emits:

| Kimi blocker | Resolving field (current main) |
|---|---|
| #1 under-reported blast radius | `mutation.affected_tables: ["*"]`, `blast_radius: "entire_sqlite_database_file"`, `affected_table_scope: "all_tables_in_database"` |
| #2 lock mismatch | `lock_scope { target_path, locks_required, reason: RESTORE_GLOBAL_LOCK_REASON }` |
| #3 missing watermark | `postflight_gate_contracts.row_count_above_watermark.min_row_count` |

---

## 2. The real blocker is reviewer **quorum**, not code

`integrations/hermes/tasks/deliberation.py` enforces a 2-trusted-approval quorum
and a **blocker-first** synthesizer:

- `HIGH_RISK_APPROVAL_QUORUM = 2`.
- "Trusted" reviewer = `success is True AND parsed is True` (`_trusted_reviewer`,
  `deliberation.py:389-390`).
- `_synthesize` (`deliberation.py:347-380`) computes blockers **before**
  approvals:
  ```
  blockers = [active reviewers with verdict in {block, needs_changes}]
  if blockers: status = "blocked"      # evaluated BEFORE the approval count
  ```
- A reviewer that returns `success=True` with **non-JSON prose** is auto-classified
  by `_classify_text_response` (`deliberation.py:324-344`) to `needs_changes`
  (it can never emit `approve`) → it becomes a **blocker**.

**Evidence this is the dominant failure mode:** the blocked run used panel
`codex,kimi`; Codex returned `success=True, parsed=False` (a prose "I can't review,
shell access failing" message) and `consensus.blockers = ["codex", "kimi"]` —
Codex blocked the quorum despite never emitting valid JSON.

### 2.1 Consequences (these drive the strategy)

1. **Adding a non-JSON reviewer to the gating panel near-guarantees a block.**
   A "full codex+kimi+gemini panel" is therefore *riskier*, not safer, while
   Codex emits prose on this box. (This reverses an earlier draft recommendation.)
2. **Both gating reviewers must cleanly approve.** A single prose response from
   *either* reviewer blocks. Gemini's JSON reliability is therefore load-bearing
   and **unproven** (Gemini never actually ran in the blocked deliberation).
3. **Resolving Kimi's concerns alone moves `blocked → no_quorum`, not
   `approved`** — a second trusted JSON approval is still required.
4. **`claude` is not a usable reviewer here.** It is doctor-green
   (`providers doctor`) but the Hermes reviewer adapters
   (`integrations/hermes/adapters.py`) cover only codex / kimi / gemini; the only
   `claude` reference is a config path (`.claude/hermes/model-routing.json`).
   The probe (Workstream B) is authoritative — do not assume doctor-green ==
   usable reviewer.

---

## 3. Decisions (locked)

- **D1 — Quorum path:** Honest quorum first; `DELIBERATION_OVERRIDE` only as a
  documented fallback if a genuine second JSON reviewer cannot be obtained.
- **D2 — Gating panel:** Probe-then-select. The gating panel contains only
  reviewers proven to emit valid JSON (`parsed=True`) by the Workstream B probe.
  Codex runs **out-of-band / advisory** unless the probe shows it emits JSON.
- **D3 — Boundary:** This pass produces the strategy document only. Canary /
  deliberation re-runs are next-session/operator steps (they write ledger
  artifacts).
- **D4 — Canonical deliberation input:** `task_plan.json` (matches Hermes
  artifact writing + plan-hash binding; confirmed as the blocked run's input).

---

## 4. Workstreams

### WS-A · Confirm the merged fix (read-only; code only if a real gap appears)

1. Generate `restore-db` **plan-only** for `signals.db.canary` from current main.
2. Assert the generated `task_plan.json` contains:
   - `mutation.affected_tables == ["*"]`,
   - `lock_scope.reason` present,
   - `postflight_gate_contracts.row_count_above_watermark.min_row_count` present.
3. Run the focused suite:
   `tests/ops/hermes/test_restore_db_task.py`,
   `tests/ops/hermes/test_recovery_sprint_canary_fixture.py`,
   `tests/scripts/test_restore_db.py`.
4. **Gate:** fields present + tests green → **no code**. Only a real gap opens a
   narrow PR, in a fresh worktree from `origin/main`, staging exact paths only.

### WS-B · Reviewer JSON-emission probe (hard gate — the actual critical path)

1. Dry-run a deliberation per candidate reviewer (kimi, gemini, codex) against a
   small scratch plan.
2. Eligible gating reviewers = those returning `parsed=True`. Kimi is proven;
   **Gemini must pass the probe**; Codex is advisory unless it passes.
3. **Require ≥ 2 eligible reviewers.** If fewer than 2:
   - escalate to the D1 override fallback (documented justification + operator
     sign-off), **or**
   - invest in repairing a reviewer (e.g., Codex's `windows sandbox: spawn setup
     refresh` + JSON emission) — higher effort, uncertain.
4. Record each reviewer's true status honestly (doctor-green ≠ JSON-capable).

### WS-C · Regenerate plan & re-run deliberation

1. Regenerate the canary restore plan from current main (plan-only suffices as
   the deliberation input; full canary *execute* is optional evidence and
   acquires the global `signals.db` lock).
2. Pass a **meaningful `--min-row-count` (612 for the canary baseline)** so the
   watermark gate is non-trivial — the blocked run used `min_row_count=0`, and a
   0 watermark could itself draw a fresh concern.
3. Re-run the deliberation with `--panel <eligible JSON reviewers from WS-B>`,
   feeding the **fresh** `task_plan.json` path. Verify the generated input file
   actually contains the fixed `affected_tables`, `lock_scope.reason`, and
   `min_row_count` (guard against the 12,000-char `task_text` truncation in
   `deliberation.py:26,421-430`).
4. **Gate on consensus:** `approved` → operator gate (WS-D). `no_quorum` /
   `conflicted` / `blocked` → diagnose the specific failing reviewer, fix,
   re-run; override only as the D1 fallback.

**Residual risk to capture explicitly:** PR #245's lock change is
*documentation, not alignment* — `locks_required` still names `signals.db` for a
canary target; the `lock_scope.reason` justifies the deliberate global lock but a
strict re-reviewing Kimi may still flag it. Capture Kimi's verdict verbatim.

### WS-D · Operator gate + live-restore runbook (written, NOT executed)

Reconcile with / cite the authoritative DR plans rather than inventing a
procedure:

- `.omx/plans/db-recovery-before-collection-ralplan-dr-20260513.md` (Branch A is
  the chosen default: audited 612/53 in-place restore via `scripts/restore_db.py`
  after exclusivity + evidence-copy gates).
- `.omx/plans/corrected-operational-priorities-ralplan-dr-20260514.md` (operator
  approves mutation; scheduler/task changes approved separately from code).

Pre-conditions checklist:

1. **Approving quorum artifact** from WS-C (or documented D1 override).
2. **Backup selection is an operator decision via the DR audit.** The DR default
   names `backups/signals-20260511-030832.db` as the leading 612/53 candidate
   (compared read-only against `signals.backup-20260511T105827.db`,
   `signals-20260404-072102.db`, etc.). **Do not assume the canary backup**
   `backups/signals-20260529-190655.db` (SHA `01ced671…`) is the live-restore
   source — it was the canary fixture, not the audited default.
3. **Watermark:** accepted 612/schema-53 recovery leaves
   `.omx/state/db_watermark.json` (currently `{signal_count: 612, schema_version:
   53, …2026-04-23}`) **unchanged**. Re-init **only** if the operator deliberately
   selects a different baseline or the watermark is proven stale. (Live
   `signals.db` is currently 4 rows / schema 26.)
4. **Exclusivity gate:** `HarmonicKeepAlive` remains disabled (already done as
   containment) until the live restore + a one-shot collection + health pass.
5. **Restore invocation:** `--expected-schema-version 53`, an operator-selected
   `--min-row-count` (≥ 612 for the named baseline; a fuller recovery requires an
   explicit operator-selected count/source), sidecar-safe handling, rollback
   snapshot path. The Hermes `restore-db` execute path wraps
   `scripts.restore_db.restore_backup_with_lock_and_ledger` (lock + ledger +
   pre-restore backup), consistent with the DR canonical `scripts/restore_db.py`.
6. **Post-restore verification:** integrity ok, schema == 53, `COUNT(*) signals
   >= 612`, no unexpected sidecars, ledger evidence present.

All live commands are written but **gated behind explicit operator "go."**

---

## 5. Guardrails

- Likely **no code worktree/PR** at all (WS-A gate). This doc was committed from a
  clean `.worktrees/` lane off `origin/main`, staging only this file — never the
  dirty primary checkout (`state/collectors.json`, credential-ish files, keepalive
  artifacts, `signals.db.canary`, rollback artifacts).
- Reaffirm the hard stop on live `signals.db` restore throughout.

---

## 6. Open questions

| # | Question | Disposition |
|---|---|---|
| a | Does WS-D match the authoritative DR procedure? | **Resolved in this doc** — cites the two `.omx/plans` DR docs; Branch A default. |
| b | Authoritative `--min-row-count` for the *real* `signals.db`? | **612** for the named baseline; any fuller recovery requires an explicit operator-selected count/source. |
| c | Gemini JSON reliability? | **Deferred to WS-B probe** (load-bearing; unproven). |
| d | Canonical deliberation input artifact? | **Resolved: `task_plan.json`** (D4). |

---

## 7. Evidence index

- Blocked deliberation: `ai-logs/hermes/runs/hermes_20260531_155328_ae2156bb/`
  (`deliberation.md`, `deliberation_record.json`, `run_record.json`).
- Canary Phase-1 runs: dry-run `…155212_166f8f98`, preflight `…155222_30fb758a`,
  execute `…155233_d6cbadd4`.
- Contract fix: PR #245 / commit `3522fab` (merged `c1f0a23`).
- Code: `integrations/hermes/tasks/restore_db.py`,
  `integrations/hermes/tasks/deliberation.py`,
  `integrations/hermes/tasks/base.py`, `integrations/hermes/adapters.py`.
- State: `.omx/state/db_watermark.json` (612/53), live `signals.db` (4 rows /
  schema 26), canary backup `backups/signals-20260529-190655.db`
  (SHA `01ced671a3c1a3800646edad42c2fa9ef2841f587d8255b4049a7c6e3fdd0a26`).
- DR plans: `.omx/plans/db-recovery-before-collection-ralplan-dr-20260513.md`,
  `.omx/plans/corrected-operational-priorities-ralplan-dr-20260514.md`.
