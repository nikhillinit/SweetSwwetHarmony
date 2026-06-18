# Trust Release Ratification Spec — Red Team Review (Round 2)

> Applied skill: **deliberation-debate-red-teaming**. Reviewer: Claude (claude-opus-4-8).
> Date: 2026-06-18. Target: the "Trust Release Ratification + DB-Path Hardening + Controlled
> Product Restart" 12-phase spec.
> Method: verified the spec's load-bearing claims against live code AND the GitHub connector,
> per `.claude/rules/plan-verification.md`.

## Verdict

**PROCEED WITH CAUTION — adopt the first slice, descope the program, fix four design gaps.**

This spec is a genuine improvement over both the prior plan and (in fairness) my prior review.
Its epistemic posture is right — *nothing is a release fact until independently ratified* — and,
unlike the prior plan, **its factual claims check out** (see Fidelity below). The failure mode here
is not fidelity. It is **scope and internal consistency**: a 2-issue + CI-hardening stabilization
has become a 14-milestone, 4-new-subsystem program, and the priority labels contradict the
acceptance gate. Trust is earned by a small verifiable core shipped fast, not by more machinery.

Confidence: **0.82**.

## Fidelity check — the spec's own claims (mostly verified ✅)

I held the spec to its own standard and verified its load-bearing claims:

| Claim | Result |
|-------|--------|
| `check_pr_evidence.py` accepts `/actions/runs/0` | ✅ TRUE — regex `actions/runs/\d+` (`check_pr_evidence.py:7`); `\d+` matches `0` |
| `ops/trust_status.py` is minimal (no max-age) | ✅ TRUE — 45 lines, only `load_reports`/`summarize` |
| `scripts/db_anomaly.py` is minimal | ✅ TRUE — 106 lines: sha/row-count/size/known-bad/watermark only |
| `active-sprint.md` is stale | ✅ TRUE — says main `275cded` / PR #271; live main is `6023a29` |
| PR #281 open, non-draft, 38 files, "fix(db): harden canonical signals db resolution", base `6023a29` | ✅ TRUE — connector-confirmed (head `d80dd7f`, +455/−318, 38 files) |
| PR #281 "mergeable" | ⚠️ **OVERSTATED** — `mergeable_state: "unstable"`; status API `total_count: 0`, `state: pending`. Checks are **not green.** |

**Credit where due (steelman, not strawman):** the two-tier `quick_check`/`integrity_check` split,
backup-*mode discovery* instead of "always Litestream," always-reporting CI controllers vs
path-filtered required checks, fresh-empty *expiry*, gating doc reconciliation on audit rather than
PR text, and separating ratification from portability — these are all correct, and several are
backed by accurate citations. This spec earned its conclusions.

## Roles

| Role | Lens |
|------|------|
| **Forensic Verifier** | Did the spec's facts hold? (Mostly yes — above.) |
| **Delivery / PM** | Will this ever ship? Scope, sequencing, critical path. |
| **Security / Adversary** | Bootstrap paradox, forgeability, network-in-merge-path. |
| **SRE / 2am operator** | Self-DoS gates, #281's red checks, restore reality. |
| **Contrarian** | Does more trust machinery produce more trust? |

## Critiques

### Delivery / PM — the dominant risk

**D1 — Scope explosion (the showstopper).** The presenting problem was two open issues (#148/#149)
plus CI hardening. This spec answers with **12 phases / ~14 milestones** and four net-new
subsystems: `release-ledger.json` + `check_release_ledger.py`, trust-status v2 (max-age truth
table), db-anomaly v2 (hot/deep + semantic canaries), backup-mode discovery, always-reporting CI
controllers, operator notifications, an acceptance gate, and a portability adapter layer. A
*stabilization* release that must first build this much will not stabilize anything for weeks.
The irony is sharp: the spec rightly faults the prior plan for over-engineered test-theater, then
out-engineers it. **Trust is a small, verified core shipped fast** — not a 14-item program.

**D2 — Priority labels contradict the acceptance gate (internal inconsistency).** §2.* and the
strategy say *"only ratification is P0; portability and the rest are P1/P2."* But Phase 10's
Acceptance Gate requires trust-status v2 (Phase 4, P1), db-anomaly hot+deep (Phase 6, P1),
fresh-empty expiry (Phase 5, P1), dry-run canary, and a fired notification test (Phase 9, P1) to be
green **before** product restart (Phase 11). So product restart is gated on essentially *all* P1
work. Either the gate is real — and P1 is de facto P0, contradicting the labels — or it isn't.
The plan can't have both "only ratification is P0" and "10 phases block restart."

**D3 — "or explicit waiver" guts the gate.** Phase 11 depends on "Acceptance Gate **or explicit
waiver**." An ungoverned waiver makes the gate optional, i.e. theater. If a waiver path is needed
(it probably is, for urgent product work), specify *who* may waive, *what* is recorded, and *which*
gate items are non-waivable (e.g. DB-recovery ratification should never be waivable).

### Security / Adversary

**S1 — Evidence-checker bootstrap paradox (new blind spot the spec misses).** The plan hardens
`check_pr_evidence.py` *first* so evidence becomes credible. But that patch lands in a PR whose
evidence is validated by the **old, broken** checker — the hardened checker cannot ratify its own
introduction. The spec correctly notes the *origin* was circular (the checker shipped in a PR with
a placeholder run URL) but gives no bootstrap procedure. The trust chain's first link is unrooted.
Fix: ratify the bootstrap PR **out-of-band** (manual reviewer attestation recorded in the evidence
freeze) and say so explicitly.

**S2 — `require_live` injects GitHub availability into the merge path, with an unresolved policy.**
Phase 0's `require_live=True` calls the GitHub API during evidence checking. New failure modes:
token scope, rate limits, API outage now can block merges. The `syntax_only` fallback names the
state but not the **gate decision**: does `syntax_only` *block* or *pass* a merge? Block → every PR
is hostage to API availability. Pass → live verification is toothless exactly when an attacker
supplies a syntactically-valid fake URL during an outage. Define the policy (recommend: live
verification required only for *release-evidence* PRs, with a logged manual override).

**S3 — `head_sha` matching is fragile under squash/rebase.** Phase 0 requires the run `head_sha`
to match the PR/merge context. Squash and rebase merges rewrite SHAs, and PR Actions runs use the
PR head or a synthetic merge SHA. Strict equality will reject legitimate evidence. Allow
`head_branch` OR `head_sha`, and key the rule on the repo's merge method.

### SRE / 2am operator

**O1 — Release-ledger max-age check is a self-DoS time-bomb.** Phase 3 fails CI "if the release
ledger is older than a configured max age." If no one refreshes the ledger, *unrelated* PRs start
failing — mergeability decays without active maintenance. This is the **exact path-filtered-trap
anti-pattern the spec condemns in Phase 8**, self-inflicted via staleness. Make ledger-staleness a
*warning*, or give it a documented human bypass and a named owner.

**O2 — #281 is elevated to a P0 gate but its checks are red/pending.** The spec calls #281
"mergeable"; the connector says `unstable` with `total_count: 0` statuses. Before treating its merge
as imminent, find out *why* checks aren't green — and note #281 may itself be a live instance of
the pending-required-check trap Phase 8 is meant to fix. A P0 gate built on a PR with unknown CI
state inherits that uncertainty.

**O3 — Sequencing: ratifying (Phase 2) against a DB-path model #281 may rewrite.** #281 removes
default `signals.db` and reroutes resolution across 38 files. Phase 2 ratifies DB-recovery / Notion
/ thesis claims. If #281 lands *after* Phase 2 via the "explicit deferral" branch, some Phase-2
ratification is invalidated. The dependency is acknowledged but the "deferral" path quietly ratifies
against a model in flux. Prefer: settle #281 (merge or split) *before* DB-recovery ratification.

### Contrarian

**C1 — Does more machinery equal more trust?** The spec's north star is sound, but the proposed
proof of trustworthiness is *seven new artifacts and a truth table*. Each new subsystem (release
ledger, trust-status v2, controllers) is itself un-ratified code that can be wrong, stale, or
gamed — new surface that must *also* be trusted. The minimal trust core is far smaller: (1) a
credible evidence checker, (2) a settled DB-path model (#281), (3) a one-page ratification of the
already-landed claims from real artifacts. Everything past that is *hardening*, not *trust*, and
should not block the release being declared trustworthy.

## Risk register (Severity × Likelihood)

| # | Risk | Role | S | L | Score | Category |
|---|------|------|---|---|-------|----------|
| D1 | Scope explosion → stabilization never ships; product work starved | PM | 4 | 5 | **20** | SHOWSTOPPER |
| D2 | Priority/gate contradiction → unclear what actually blocks restart | PM | 3 | 5 | **15** | SHOWSTOPPER |
| S1 | Evidence-checker can't ratify its own bootstrap PR → unrooted trust chain | Security | 4 | 4 | **16** | SHOWSTOPPER |
| O1 | Ledger max-age check self-DoSes unrelated PRs | SRE | 4 | 3 | 12 | High |
| S2 | `require_live` makes merges hostage to GitHub API; gate policy undefined | Security | 3 | 4 | 12 | High |
| D3 | Ungoverned "or waiver" makes the acceptance gate optional | PM | 3 | 3 | 9 | High |
| O3 | Phase-2 ratification invalidated if #281 lands after via deferral | SRE | 3 | 3 | 9 | Monitor |
| S3 | `head_sha` matching rejects legitimate squash/rebase evidence | Security | 2 | 4 | 8 | Monitor |
| O2 | #281 treated as mergeable while checks are red/pending | SRE | 2 | 3 | 6 | Monitor |
| C1 | New trust subsystems are themselves un-ratified surface | Contrarian | 3 | 3 | 9 | Monitor |

## Required changes (before committing to the full program)

**Showstoppers:**

1. **D1 — Descope to a minimal trust core, defer the rest.** Ship and *stop* at: (a) evidence-checker
   hardening, (b) #281 settlement, (c) a one-page ratification of landed claims from real artifacts,
   (d) the `active-sprint.md` / strategy-doc refresh to `6023a29`. Declare the Trust Release
   *ratified* at that point. Everything else (trust-status v2, db-anomaly v2, controllers, ledger
   CI, portability) becomes a *follow-on hardening backlog*, explicitly **not** blocking the release
   or product restart.
2. **D2/D3 — Resolve the gate.** Either drop the acceptance gate to the 4-item core above and label
   the P1 phases honestly as post-release hardening, or keep a real gate with a *governed,
   itemized* waiver (named approver, recorded rationale, non-waivable items listed). Don't ship the
   current "only ratification is P0" + "10 phases block restart" + "or waiver" triple.
3. **S1 — Specify the bootstrap.** The hardened evidence checker's own PR is ratified out-of-band
   by a named human attestation, recorded in `evidence-freeze-20260617.json`. State it.

**High:**

4. **O1 — Ledger staleness is a warning, not a hard CI fail** (or has a documented bypass + owner).
5. **S2 — Define the `require_live` gate policy:** live verification required only for release-evidence
   PRs; on API-unavailable, fall back to `syntax_only` + logged manual override; never silently pass.
6. **S3 — Loosen `head_sha` to `head_branch` OR `head_sha`, keyed to merge method.**

**Monitor / accept:** O2 (check #281 CI before calling it mergeable), O3 (prefer settling #281 before
Phase-2 ratification), C1 (treat new trust subsystems as un-ratified until they have their own
evidence).

## Recommendation

**PROCEED WITH CAUTION.** Adopt the spec's "concrete next implementation slice" essentially as
written — it is correct and low-risk:

1. Adversarial tests for `check_pr_evidence.py`; reject `/runs/0` and wrong-repo URLs; add
   `syntax_only` vs `live_verified`.
2. Evidence-capability freeze recording both local and connector facts (the spec's two-class JSON
   is good).
3. Review/settle PR #281 against the DB-path checklist — **first confirm why its checks are red.**
4. One-page ratification of landed claims from artifacts; then refresh `active-sprint.md` to
   `6023a29`.

But **do not commit to the 12-phase program** as the definition of "trust release." Declare the
release ratified after the minimal core, then treat trust-status v2 / db-anomaly v2 / CI
controllers / ledger / portability as a prioritized *hardening backlog*. Fix the bootstrap paradox
(S1), the gate contradiction (D2/D3), and the self-DoS ledger check (O1) before any of that
machinery becomes a required gate. The spec's instincts are right; its scope is the risk.
