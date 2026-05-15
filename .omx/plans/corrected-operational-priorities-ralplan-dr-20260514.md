# Corrected RALPLAN-DR: Operational Priorities After Red-Team Critique

## Scope

Correct the execution order and gates for:

- `feat/keepalive-composite-verdict`
- live `signals.db` audit and reconciliation decisions
- the five unblocked build-strategy follow-ons in the vault roadmap
- sibling operational follow-ons that must not be hidden by the five-item
  build-strategy list: routing-layer-as-SoR ADR and `health.py` ops-rule work

Do not implement, redeploy, or mutate `signals.db` from this plan.

Non-goal:
This plan does not close Topic 6. The routing-layer-as-SoR ADR remains an
operational incident-response follow-on even if the five build-strategy
follow-ons complete.

## Current Evidence Snapshot

- Targeted keepalive/composite/contract/architecture tests passed: `18 passed`.
- `install_keepalive_task` tests passed: `5 passed`.
- Raw watchdog artifact compatibility currently fails:
  - `keepalive_monitor_ping.py` exits `1` when given the current raw artifact because it now requires a composite JSON containing `watchdog`.
- Live `signals.db` read-only facts:
  - `signals` rows: `750`
  - schema version: `53`
  - integrity: `ok`
  - `MAX(created_at)`: `2026-05-14T02:54:10.038890+00:00`
- Vault roadmap currently shows five unblocked follow-ons:
  1. T1.1 window sweep
  2. T1.3 schema reconciliation playbook
  3. T1.4 Phase G diagnostic
  4. T1.2 BBA/laboratory/import-linter ADR
  5. T1.3 `PRAGMA user_version` ADR
- No PR exists yet for `feat/keepalive-composite-verdict`.

## RALPLAN-DR Summary

### Principles

1. Separate git-revertible keepalive code from higher-blast-radius DB recovery or reconciliation.
2. Treat local passing tests as necessary but not sufficient when an operator-facing compatibility contract is broken.
3. Prefer fresh read-only audit before any vault-driven recovery claim or DB mutation.
4. Do not couple review/merge readiness with scheduler redeployment readiness.
5. Advance the roadmap in the order that reduces uncertainty for later irreversible work.

### Decision Drivers

1. Blast radius: DB restore or reconciliation is materially riskier than keepalive code review.
2. Evidence mismatch: branch tests are green, but raw watchdog artifact compatibility is currently broken.
3. Operational truth: live `signals.db` is healthy enough to justify audit-first reconciliation, not immediate destructive recovery.
4. Runtime truth: PR merge does not update the Windows scheduled task; the
   generated wrapper changes only after rerunning `install_keepalive_task.ps1`.

### Viable Options

#### Option A: Finish keepalive compatibility and review first; keep DB work audit-only until a later gate

Pros:
- Resolves the explicit blocker on branch readiness.
- Keeps the risky DB lane non-mutating until evidence says otherwise.
- Produces a cleaner PR and redeployment decision boundary.

Cons:
- Does not immediately close the vault's open recovery/reconciliation language.
- Leaves higher-level operational ambiguity open for one more cycle.

#### Option B: Prioritize DB reconciliation/recovery decisions before any more keepalive work

Pros:
- Attacks the highest-stakes domain first.
- Could close stale incident language earlier if the audit is decisive.

Cons:
- Risks spending time on destructive-path planning before the lower-risk keepalive slice is even reviewable.
- Encourages conflating healthy-read audit facts with recovery execution.

#### Option C: Bundle keepalive, DB reconciliation, and roadmap follow-ons into one initiative

Pros:
- Single umbrella narrative.
- Fewer handoff artifacts.

Cons:
- Weakest risk isolation.
- Makes code review, redeployment, and DB mutation gates too easy to blur.
- Harder to tell what is actually ready.

### Recommended Order

Choose Option A.

1. Resolve the keepalive compatibility contract and re-establish branch reviewability.
2. Run a read-only live-state reconciliation pass against the vault's still-open incident text.
3. Sequence the five roadmap follow-ons from uncertainty-reducing diagnostics to ADR/documentation hardening.
4. Only if the audit still proves a corpus problem, open a separate deliberate DB-mutation ralplan.

## Plan

### Phase 1: Re-baseline the keepalive branch as "not review-ready yet"

Goal:
Make the current blocker explicit: the branch is not ready for PR or redeploy while raw watchdog artifact compatibility fails.

Acceptance criteria:
- Plan and reviews explicitly say the branch is blocked by monitor/artifact compatibility, despite `18 passed` plus `5 passed`.
- The blocking contract is written as one of:
  - backward compatibility with raw watchdog artifacts must be restored, or
  - composite-only input is an intentional breaking change that requires a controlled installer/redeployment path and updated docs.

### Phase 2: Complete the keepalive compatibility decision and local review lane

Goal:
Finish the narrow git-only slice before any redeployment decision.

Acceptance criteria:
- One canonical input contract is chosen for `keepalive_monitor_ping.py`.
- Tests cover the chosen contract, including the current failing raw-artifact path if compatibility is preserved.
- ADR and runbook language match the chosen contract and distinguish:
  - local artifact composition
  - monitor transport
  - scheduler exit semantics
- Branch passes the targeted keepalive suite after the contract decision.

### Phase 3: Open a review gate before any PR or scheduler redeploy

Goal:
Separate "code looks correct" from "safe to deploy into the task scheduler."

Acceptance criteria:
- Review path is explicit:
  - local diff review
  - one independent code review pass
  - docs/runbook consistency check
- PR creation is blocked until compatibility, docs, and tests all pass.
- Redeployment is blocked until PR review is complete and the operator confirms the desired compatibility mode.

### Phase 4: Run read-only operational reconciliation against the live DB and vault text

Goal:
Decide whether the vault's open recovery/salvage language is stale, partially stale, or still actionable without accidentally closing sibling operational work.

Recommended order inside this phase:
1. T1.3 schema reconciliation playbook
2. T1.4 Phase G diagnostic
3. T1.1 offline window sweep

Acceptance criteria:
- Reconciliation uses fresh read-only facts from live `signals.db`, not old incident assumptions.
- Output distinguishes:
  - confirmed current runtime truth
  - stale vault wording
  - genuinely unresolved corpus or routing questions
- No restore, rewrite, or collector rerun occurs in this phase.
- The audit explicitly preserves Topic 6 as open unless a separate
  routing-layer-as-SoR ADR lands.
- The audit explicitly preserves `health.py` ops-rule work as a sibling Sprint 0
  follow-on, not as one of the five build-strategy items.

### Phase 5: Advance ADR follow-ons only after the audit lane is grounded

Goal:
Document durable guardrails after the runtime and corpus facts are re-confirmed.

Recommended build-strategy order inside this phase:
1. T1.2 BBA/laboratory/import-linter ADR
2. T1.3 `PRAGMA user_version` ADR

Sibling operational ADR:
- Routing-layer-as-SoR remains a separate operational ADR, grounded in Topic 6
  and PR #164's consumer relay proof (`claim_due_outbox` /
  `finalize_outbox`). It should not be deprioritized as "low urgency"; it is the
  authority boundary for productizing routing confidence.

Acceptance criteria:
- ADR work references confirmed runtime truth:
  - schema source of truth is migration/version reality, not header drift alone
  - Phase G findings reflect the actual live path, not speculative alternatives
- ADRs remain non-mutating design artifacts unless separately approved.

### Phase 6: If reconciliation still points to corpus risk, fork to a separate deliberate recovery plan

Goal:
Prevent destructive recovery from hitchhiking on the keepalive branch or on routine roadmap work.

Acceptance criteria:
- Any DB restore, salvage, or reconciliation mutation requires a new deliberate-mode ralplan.
- The follow-on must import the active Branch A / Branch B runtime distinction
  from `.omx/plans/db-recovery-before-collection-ralplan-dr-20260513.md`, but
  must refresh candidate facts against the current 750-row/schema-53 live DB
  before reusing any older 612-row baseline assumptions.
- Branch A means in-place restoration or mutation of default `signals.db`; it is
  the only path that directly restores default CLI/MCP/keepalive runtime if a
  restore is still needed.
- Branch B means fresh-target recovery; it preserves the default DB but does not
  unblock default collection or `HarmonicKeepAlive` unless CLI, MCP /
  `SignalStore()`, and the generated keepalive wrapper are explicitly retargeted.
- That follow-on plan must include pre-mutation gates:
  - fresh DB snapshot and integrity facts
  - live-writer/process audit
  - explicit rollback target
  - sidecar/WAL handling
  - operator approval before mutation

## Review Gates

### Gate A: Compatibility Review

Pass only if:
- the keepalive input contract is explicit;
- the failing raw-artifact case is either fixed or intentionally retired with migration steps;
- targeted tests and docs align.

### Gate B: PR Review

Pass only if:
- branch diff is narrow and reviewable;
- no local-only artifacts are swept into the change;
- there is at least one independent review pass beyond the author.

### Gate C: Redeployment Review

Pass only if:
- the merged branch reflects the chosen compatibility contract;
- installer and runbook semantics match;
- operator approves scheduler/task changes separately from code approval.

Deployment definition:
Merging code is not redeployment. Redeployment means rerunning
`scripts/red-team-hybrid/install_keepalive_task.ps1` so the inner runner is
regenerated and the scheduled task is re-registered under the selected
compatibility and verdict mode. Without that step, the live task may continue to
execute the old wrapper.

### Gate D: DB Mutation Review

Pass only if:
- read-only reconciliation proves mutation is still needed;
- a separate deliberate recovery plan is approved;
- the Branch A vs Branch B selection is explicit, including caller-path proof
  for CLI, MCP / `SignalStore()`, and keepalive wrapper behavior;
- all live-writer and rollback gates are satisfied.

Reversibility:
- Phases 1-3 are git-revertible.
- Phase 4 is read-only.
- Phase 5 is doc-only unless separately approved.
- Phase 6 is the only destructive lane and stays behind Gate D.

## ADR

### Decision

Use a split-lane plan: finish and review the keepalive compatibility slice first, keep DB work read-only until reconciliation evidence justifies anything stronger, then sequence roadmap follow-ons from diagnostics to ADR hardening.

### Drivers

- Compatibility gap blocks current branch readiness.
- Live DB evidence does not currently justify immediate destructive recovery.
- Review and redeployment need cleaner boundaries than the earlier plan gave them.

### Alternatives Considered

- DB-first recovery/reconciliation planning
- one bundled initiative covering keepalive, DB, and roadmap together

### Why Chosen

It resolves the proven blocker first while preserving the stricter gate around higher-risk DB actions.

### Consequences

- The keepalive branch can progress without overstating deploy readiness.
- Vault incident text may remain temporarily open until the audit lane finishes.
- Any destructive DB path becomes a separate explicit decision, not an implied next step.
- Topic 6 routing-layer-as-SoR stays visible as an operational authority decision
  rather than being buried under the five build-strategy follow-ons.
- Scheduler state cannot be assumed from git state; installer rerun evidence is
  required for "merged and in effect."

### Follow-Ups

1. Review and correct the keepalive compatibility contract.
2. Run the read-only reconciliation trio: schema, Phase G, window sweep.
3. Keep routing-layer-as-SoR and `health.py` ops-rule work as sibling
   operational follow-ons.
4. Draft the two build-strategy ADR follow-ons after runtime truth is reconfirmed.
5. Open a separate deliberate recovery ralplan only if the reconciliation lane still proves a corpus problem.
