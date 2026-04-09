# LOB.txt Progress Evaluation — Session Artifact (2026-04-06)

This directory captures a 2026-04-06 session that audited the Harmonic Discovery Engine codebase against `LOB.txt` (the original consumer-discovery-engine vision doc).

## Status: SUPERSEDED by red-team-hybrid

This evaluation is **NOT canonical** and **NOT actionable** in its original form.

**Why:** During the same session, the analysis ran a self-bias audit ([`bias-audit.md`](./bias-audit.md)) and discovered that:

1. The "9% pipeline precision" claim in the original `findings.md` was **selection bias**, not a real metric.
2. The "shifting the burden" diagnosis in the original `systems-thinking-leverage.md` **directly contradicts** `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` §2, which is the canonical Move 0 plan and explicitly frames governance as a **prerequisite** for collector innovation, not a substitute for it.
3. Both the findings and the systems analysis were anchored on `LOB.txt` as the current authoritative strategy without checking whether SweetSweetHarmony docs or `red-team-hybrid/` had superseded it.

The bias audit's own conclusion: *"I don't know whether LOB.txt is still the strategy. The one unconditional action is wiring 211 labels into the thesis-rule update job — everything else is conditional."*

## Canonical plan

For active work on `prep/red-team-hybrid-prep`, anchor on:

- [`docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`](../2026-04-06-red-team-hybrid/00-strategy.md) — Direction-A-derived hybrid strategy
- [`docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md`](../2026-04-06-red-team-hybrid/01-move-0-charter.md) — 12-day Move 0 deliverables (ends 2026-04-19)
- [`docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md`](../2026-04-06-red-team-hybrid/10-risk-register.md) — risk tracking

## What survived the bias audit

The single recommendation that passed all reversal tests:

> **Wire the 211 existing quality labels into the thesis-rule update job. Close the label→rule feedback delay from weeks to days.**

This is independent of which strategic anchor is current and high-leverage across all candidate diagnoses. It is now reflected in `red-team-hybrid/08-track-b-labelling.md` (the labeling sprint).

## What's still in this directory

- [`bias-audit.md`](./bias-audit.md) — **kept** as a methodological reference for how to apply scout-mindset to strategic analysis. Has standalone value.
- This `README.md` — replaces the original 4 files (`findings.md`, `systems-thinking-leverage.md`, `task_plan.md`, `progress.md`), which are deleted because their interpretations are falsified by `bias-audit.md` and contradicted by `red-team-hybrid/`.

## If a future agent reads this

**Before re-running this evaluation:**

1. Read `red-team-hybrid/00-strategy.md` (the canonical plan)
2. Find and read SweetSweetHarmony executive-decision-layer + evidence-table docs
3. Confirm whether `LOB.txt` is still the active strategy
4. If LOB.txt is confirmed current AND red-team-hybrid is not contradictory: re-run the rubric scorecard with `bias-audit.md` corrections applied (treat labeled-sample FP as selection bias, not pipeline precision; check current HN flow not historical stock; check SweetSweetHarmony context)
5. If LOB.txt is superseded: this directory can be deleted entirely

Until then, only the unconditional action ("wire labels") should be tracked against the active plan.

## Data points (still factually valid as of 2026-04-06)

- 612 signals in signals.db
- 211 labeled signals (187 FP / 19 TP / 5 UNSURE) — selection-biased sample, not a pipeline metric
- 15 pushed to Notion (all-time)
- HN: ~31% of historical signal volume; live LLM thesis filter active since 2026-03-25 (current FP rate unknown)

**Important caveat (added 2026-04-08):** The signal corpus has been frozen since 2026-03-01. The above counts represent the historical corpus, not an ongoing flow. See `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` R19 for the pipeline freeze finding.
