# Decision Matrix — Hermes-Orchestrated P0 Recovery Sprint Orchestration

> Date: 2026-05-29
> Decision: How to orchestrate the P0 signals.db recovery using the Hermes harness.
> Method: Weighted decision matrix (thinking-frameworks: decision-matrix).
> Status: CONFIRMED 2026-05-29 — operator selected S4 (Matrix 2) = Option 4 (Matrix 1)
>   orchestration + enforcement down-payment. Feeds the recovery-sprint design doc.

## Decision context

`signals.db` is in the 4-row truncated state (verified 2026-05-29: 1.46 MB, byte-identical
to `signals.db.pre-recovery-20260423-truncated`). The pipeline is non-functional. The
2026-05-08 incident (Issue #149) was never recovered. The Hermes orchestration harness is
fully built and hardened (Track A PRs #217–#233), with 11 registered task runners including
`restore-db` and `incident`.

### Decided constraints (locked before this matrix)

- **Objective:** recovery-first reactivation.
- **Recovery target:** safe baseline (612-row `signals.db.pre-step4b-promotion-20260404`) +
  containment-first (ship offsite/cloud backup control before restore).
- **Scope:** P0 recovery sprint only; defer all other backlog.
- **Autonomy:** graduated/gated Hermes execution (dry-run -> preflight -> execute, with
  `--ack-risk` + 2-approval `deliberate` gate on high-risk steps).

## Criteria and weights

| Criterion | Weight | Rationale |
|---|---|---|
| Recovery safety / blast-radius control | 30% | Incident context; containment-first chosen |
| Audit-trail integrity | 20% | Incident's existing trail is murky; clean provenance is high value |
| Hermes leverage | 20% | Explicit user framing: "full Hermes orchestration" |
| Time-to-live-pipeline | 15% | P0 sprint — speed counts |
| Operational simplicity (single operator) | 15% | Sprint, one hand on the wheel |

## Gap surfaced by the matrix

All three originally-proposed options assume `restore-db` works on first contact. It has only
ever been exercised against **mocks** — never a real production restore. On an incident DB,
the first real use of the harness *is* the recovery. That unscored risk generates **Option 4**.

## Alternatives

1. **Linear gated pipeline** — single operator, strict sequence, each step a `hermes task`
   run escalating dry-run -> preflight-only -> execute; `deliberate` 2-approval + `--ack-risk`
   on high-risk steps; full ledger trail.
2. **Parallel-lane Hermes team** — containment lane and recovery-prep lane concurrent,
   converge at the restore gate.
3. **Audit-only** — restore via `scripts/restore_db.py` directly, Hermes only ledgers it.
4. **Linear gated + harness canary (surfaced)** — Option 1, but the first `restore-db
   --execute` runs against a **throwaway copy** of the truncated DB (612-row backup as source)
   to prove guards/sidecar-handling/lock/ledger/repair-prompt end-to-end *before* touching the
   live target. Recovery doubles as Hermes production validation.

## Scoring (1–10; weighted contribution in parentheses)

| Option | Safety 30% | Audit 20% | Leverage 20% | Speed 15% | Simplicity 15% | Total |
|---|---|---|---|---|---|---|
| 1 — Linear gated pipeline | 8 (2.40) | 9 (1.80) | 9 (1.80) | 7 (1.05) | 8 (1.20) | 8.25 |
| 2 — Parallel-lane team | 6 (1.80) | 6 (1.20) | 9 (1.80) | 8 (1.20) | 4 (0.60) | 6.60 |
| 3 — Audit-only script | 7 (2.10) | 5 (1.00) | 2 (0.40) | 8 (1.20) | 7 (1.05) | 5.75 |
| **4 — Linear gated + harness canary** | 10 (3.00) | 9 (1.80) | 10 (2.00) | 6 (0.90) | 7 (1.05) | **8.75** |

## Sensitivity analysis

- **1 vs 4: ~6% gap (close-ish).** Delta is entirely Safety (10 vs 8) and Leverage (10 vs 9),
  paid for with one extra step (Speed 6 vs 7).
- **Flip condition:** Option 1 overtakes only if Speed weight roughly doubles (>=28%). But
  containment-first was chosen *over* speed, so the weighting that selects Option 4 is the one
  consistent with the locked constraints. Result is robust.
- Options 2 and 3 are not close. Option 3 self-disqualifies on Leverage (contradicts "full
  Hermes"). Option 2's concurrency adds lock-contention surface and a harder-to-read ledger
  during an incident — wrong trade for a single-operator P0.

## Recommendation

**Option 4 — Linear gated pipeline with a harness-canary restore.** It dominates the
highest-weighted criterion (recovery safety) and maxes Hermes leverage, at the cost of one
disposable dry-execute step. When the recovery tool is itself unproven in production, the
canary is cheap insurance.

## Next steps

- Confirm Option 4 with operator. (Superseded by Matrix 2 below — Option 4 is retained as the
  *orchestration topology* inside the chosen sequencing option S4.)
- Feed into the recovery-sprint design doc (brainstorming -> `docs/superpowers/specs/`).
- Verify offsite/cloud-backup control design against existing `scripts/backup_db.py` before
  the containment step is specified.

---

# Matrix 2 — Sequencing recovery vs. Hermes Track A enforcement program

> Added 2026-05-29 after reviewing
> `hermes_track_a_post_pr235_updated_proposal.md` (an enforcement/hardening program:
> phases E0–E5, PRs H1–H5; thesis = "make it impossible, or immediately detectable, for
> production state to move outside the Hermes control plane").

## Why a second matrix

Matrix 1 chose the *orchestration topology* (Option 4: linear gated + harness canary). The
proposal raises a different, higher-order question: **how does the P0 recovery sequence
relative to the enforcement program?** It also contributes a new decision criterion —
**policy consistency / bypass-debt** — and notes that under its own Phase E2 policy the
recovery restore is a maximum-risk, deliberation-required operation (all five E2 triggers fire:
production target, stale backup, unobserved hash, large row-count delta, sidecars present).

## Criteria and weights (re-weighted for sequencing under incident pressure)

| Criterion | Weight | Note |
|---|---|---|
| Time-to-live-pipeline | 30% | Product is dead; recovery urgency now leads |
| Recovery safety / blast-radius | 25% | Incident + unproven harness |
| Policy consistency / bypass-debt | 20% | NEW, from the proposal |
| Hermes leverage | 15% | "Full Hermes" framing |
| Operational simplicity (single operator) | 10% | P0 sprint |

## Alternatives

- **S1** — Recover now (emergency bypass per proposal anti-goal #9) + manual codex/kimi
  `deliberate`; proposal becomes the post-recovery roadmap.
- **S2** — Build PR H2 (deliberation gate-binding for critical restore) first, then recover
  by-the-book.
- **S3** — Adopt the full E0–E5 enforcement program as the strategy; recovery folded in as its
  E3 restore rehearsal.
- **S4 (surfaced)** — Recover now + enforcement down-payment: the recovery sprint also harvests
  the real restore into the proposal's first artifacts (H1 policy-reconciliation doc + E3
  `restore-db` rehearsal fixture captured from the live run), paying down bypass debt
  immediately. Mechanically = Matrix-1 Option 4 plus an artifact-harvest step.

## Scoring (1–10; weighted contribution in parentheses)

| Option | Speed 30% | Safety 25% | Policy 20% | Leverage 15% | Simplicity 10% | Total |
|---|---|---|---|---|---|---|
| S1 — Recover now, roadmap later | 9 (2.70) | 8 (2.00) | 6 (1.20) | 8 (1.20) | 8 (0.80) | 7.90 |
| S2 — Gate-bind first, then recover | 4 (1.20) | 9 (2.25) | 9 (1.80) | 9 (1.35) | 5 (0.50) | 7.10 |
| S3 — Full enforcement program | 2 (0.60) | 9 (2.25) | 10 (2.00) | 10 (1.50) | 3 (0.30) | 6.65 |
| **S4 — Recover now + down-payment** | 8 (2.40) | 9 (2.25) | 8 (1.60) | 9 (1.35) | 7 (0.70) | **8.30** |

## Sensitivity analysis

- **S4 vs S1: ~5% gap.** S4's edge is Safety (canary becomes a recorded fixture -> forces
  rigor on the real restore) and Policy (bypass debt paid down immediately), at the cost of
  Speed (8 vs 9) and one harvest step.
- **Flip condition:** S1 overtakes only if Time-to-live weight >= 40% AND bypass-debt is
  zero-weighted — but zeroing bypass-debt discards the proposal's central contribution. Under
  any weighting that takes the proposal seriously, S4 wins. Robust.
- S2 and S3 lose decisively on speed; both reverse the locked "P0-only, defer backlog"
  decision. S3 is the proposal's own end-state but is wrong as a *sequencing* choice while the
  pipeline is dead.

## Recommendation

**S4 — Recover now + enforcement down-payment.** Synthesis of the locked decisions
(recover first, P0-focused, containment-first) with the proposal's core insight (no untraced
production state change). It is Matrix-1 Option 4 with the canary + live restore *captured* as
the proposal's H1 doc and E3 fixture, so the recovery both unblocks the product and lays the
first enforced brick. The full E0–E5 program proceeds afterward on a live corpus.

## Relationship between the two artifacts

- The `.omx/plans/hermes-integration-ralplan-dr-20260527.md` addendum (Track A hardening
  PRs 1A-9) is **landed** and is now architecture history for the *build* phase.
- The tracked post-PR235 enforcement strategy is
  `docs/superpowers/specs/2026-06-01-hermes-track-a-post-pr235-hardening.md`. It is the
  forward **enforcement** program definition (H0-H5) and supersedes "keep hardening /
  doc-refresh" as the post-recovery roadmap.
- This recovery sprint (S4) is the bridge: it is both the product recovery and the
  enforcement program's first real (non-fixture) E3 evidence.
