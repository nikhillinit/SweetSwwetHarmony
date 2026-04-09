# Phase 1: Move 0 Prep + Liveness Prep - Context

**Gathered:** 2026-04-08
**Reviewed:** 2026-04-08 (multi-framework pass: CEO, eng, devex, premortem, red-team, scout-mindset, reference-class, kill-criteria, prioritization, six-hats, systems-thinking, causal-inference, risk-register, evaluation-rubric)
**Status:** Ready for planning

<domain>
## Phase Boundary

Lock the governance paper trail for the 2026-04-18 Step 4B regret check, populate the Track B / C / E data substrates, and ship the Move 0 charter deliverables, all before the 2026-04-19 protected-paths unfreeze. All Phase 1 work stays in allowed Move 0 paths (`docs/`, `data/shadow/`, `scripts/red-team-hybrid/`, `.planning/`, `.github/workflows/`). No changes to `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, or `storage/migrations/`.

Scope is bounded to REQs that deliver the Move 0 prep and Liveness prep substrate. Phase 2 (Move 0.5 Liveness Restoration) is the actual engagement ship and starts after the protected-paths unfreeze.

**Already delivered in this session (atomic commit `4efe8cf` on `prep/red-team-hybrid-prep`):**
- **LIV-01** — Pipeline restarted. 155 new signals ingested across 4 operational collectors (hacker_news, arxiv, rss_feeds, news_api). `MAX(created_at)` advanced from 2026-03-01 to 2026-04-08.
- **LIV-02** — `scripts/red-team-hybrid/freshness_watchdog.py` shipped. Exit 0 verified against live DB with all 4 operational collectors FRESH at age ~0.05h. Satisfies Phase 1 success criterion 1.

**Still to deliver in Phase 1 (11 REQs):** LIV-03, LIV-11, GOV-01, GOV-02, GOV-03, GOV-04, SUB-01, REC-01, REC-02, REC-03, REC-04.

</domain>

<decisions>
## Implementation Decisions

### Phase 1 scope calibration under the 11-day clock (2026-04-08 → 2026-04-19)
- **D-01:** Pragmatic split. Ship all Claude-autonomous REQs at full depth (LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-02, REC-03). Scaffold REC-01 (Track B) and REC-04 (Track E) with Claude-seeded CSVs that the analyst extends incrementally over the 11 days.
- **D-02:** Insurance against R20 (analyst abandonment) is the rationale — if analyst bandwidth is constrained on any single day, governance artifacts still land and the Track B/E CSVs accumulate progress rather than block on a single analyst session. **Clarification from review:** the split is for analyst-bandwidth insurance, NOT Claude-capacity constraint. Wave A Fermi estimate is ~1-2 hours of Claude effort total; Claude is not the bottleneck.
- **D-03:** Phase 1 is NOT gated on REC-01/REC-04 hitting their 30 / 50 row targets by 2026-04-19. If actuals undershoot by 2026-04-18, document the gap in the Phase 1 VERIFICATION.md and carry the shortfall into Phase 2 day 1 rather than stalling the phase boundary.

### REC-01 Track B labelling substrate source
- **D-04:** DB-mining-plus-analyst-confirmation pattern. Claude surfaces 30 candidate episodes from the 767 signals in `signals.db`, stratified by `thesis_category` and `confidence` buckets so the cohort spans TP-likely, FP-likely, and ambiguous regions of the classifier's decision surface.
- **D-05:** Each candidate is written as a row in `data/shadow/track_b_episodes.csv` (template already exists at `scripts/red-team-hybrid/track_b_episodes.template.csv`). Columns must capture: `signal_id`, `source_api`, `canonical_key`, `company_name`, `confidence_from_classifier`, `thesis_category`, `claude_pre_label`, `pre_label_rationale`, `analyst_label` (empty on seed), `labeled_at` (ISO 8601, empty on seed), `labeler_id` (empty on seed). The `labeled_at` and `labeler_id` columns mirror `signal_quality_metrics` so the CSV can be round-tripped through `quality-label` without lossy transforms.
- **D-06:** The existing `quality-label` skill is the labelling tool. Analyst confirmations flow through it so they also persist to `signal_quality_metrics` with an audit trail. No new labelling UI.
- **D-07:** Selection bias acknowledgement (per 2026-04-06 bias audit): Track B cohort sourced this way is known-biased because it's drawn from what the engine already surfaced. It is the SECONDARY canary, not the primary one. The random-sampled cohort addressing the bias audit is a Phase 3+ deliverable, not Phase 1.

### REC-04 Track E founder watchlist source
- **D-08:** Claude-seed-plus-analyst-extend pattern. Claude extracts founder names mentioned in `signals.raw_data` for the higher-quality collectors (news_api, hacker_news titles, arxiv authors where a company is cited) and seeds `data/shadow/founder_watchlist.csv` with 30-40 records.
- **D-09:** Analyst extends with 10-20 high-conviction names from their external network over the 11-day window. Analyst additions are marked with a `source=analyst|claude` column so downstream work can segment.
- **D-10:** Reputation scoring stub is a placeholder column (`reputation_score REAL NULL`) plus a one-paragraph methodology note in the CSV header comment. Full scoring is deferred to V2-05.
- **D-11:** Hard constraint from LOB.txt risk analysis: no LinkedIn scraping, even though prior plans mentioned it. Founder names extracted ONLY from sources where the founder self-published or was mentioned in a public news source.

### GOV-04 framing correction landing pattern
- **D-12:** Prepend a dated `Framing Correction (2026-04-08)` callout at the top of `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` before §1. The callout is ~6-10 lines and states the substrate-plus-engagement complementarity explicitly.
- **D-13:** The original §2 text is preserved verbatim so the git history shows what the 2026-04-06 red-team review actually concluded vs. what the 2026-04-08 synthesis revised.
- **D-14:** Same callout pattern is applied wherever GOV-01 (withdraw the 9% precision claim) needs to edit active docs — date the correction, preserve the original line, add the bias-audit caveat inline.

### Governance paper-trail ordering
- **D-15:** Order of operations within Phase 1:
  1. **Wave A (docs, Claude-autonomous, Fermi ~1-2h total):** LIV-11 / GOV-02 (R20 to risk register), GOV-04 (framing correction), GOV-01 (9% precision withdrawal across 4 known lines — see D-25), LIV-03 (freshness precondition in new file `14-step4b-preconditions.md` — see D-24), GOV-03 (freshness gate contract doc only — see D-20).
  2. **Wave B (data, Claude-seeded):** REC-02 (Track C hold-out cohort deterministic split), REC-03 (Track D design doc), REC-01 (Track B candidate mining + CSV seed), REC-04 (Track E founder extraction + CSV seed).
  3. **Wave C (synthesis):** SUB-01 (Move 0 charter deliverables per `01-move-0-charter.md` — this is the rollup that depends on Waves A and B landing).
- **D-16:** Wave A commits are ≤50 lines each, atomic per REQ. Wave B commits carry the CSV + the generating script (in `scripts/red-team-hybrid/`) so the mining is reproducible. Wave C is a verification commit confirming the charter deliverables list is complete.

### REC-02 Track C hold-out cohort split methodology
- **D-17:** Claude's discretion on the specific split strategy. Constraint: deterministic seed (so the split is reproducible from a fresh clone), file-based output committed to `data/shadow/holdout_split/`, and the seed value plus algorithm documented inline. Follow whatever `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` specifies — the canonical design doc is the authority.

### REC-03 Track D (CT-log + DNS shadow) design depth
- **D-18:** Design-only during Move 0 means docs only (ADR-style decision record + requirements). NO collector code, NO schema migrations — both would touch protected paths. Output goes to `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md` (new file).
- **D-19:** The design doc must answer: what CT-log sources / what DNS data source / what the canonical key strategy looks like for stealth companies / what the anti-fingerprinting posture is / what the cost envelope is. Implementation blocks on the 2026-04-19 unfreeze.

### GOV-03 scope constraint (added 2026-04-08 post-review — CRIT-1)
- **D-20:** GOV-03 ("freshness precondition on all governance gates") is Phase 1 DOCS ONLY. `governance/` is a Python package in the protected-paths list (verified: `governance/cli.py`, `contracts.py`, `state_policies.py`, `writer.py`). No code changes allowed until 2026-04-19 unfreeze. Phase 1 ships `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` specifying the precondition semantics that Phase 2 implements in code. The contract doc must include: required precondition input format, blocking vs advisory behavior, failure escalation path, and the list of gates it applies to (Step 4B regret check, canary runs, drift alerts).
- **D-21:** The planner MUST NOT propose any edit to `governance/*.py`. Any such proposal is an immediate plan reject. `check_protected_paths.sh` enforces at commit time as a secondary gate.

### Pipeline keep-alive (R19 root cause fix, added 2026-04-08 post-review — CRIT-2)
- **D-22:** LIV-01 restarted collection once. Without a scheduled task, R19 recurs within 36h (causal-inference lens: the ROOT cause of R19 was "no scheduled collection", not "pipeline machinery broken"; restarting doesn't fix the root cause). Phase 1 ships a scheduled keep-alive. Default path: `scripts/red-team-hybrid/install_keepalive_task.ps1` that installs a Windows Task Scheduler entry running `python run_pipeline.py collect --collectors hacker_news,arxiv,rss_feeds,news_api` daily at 08:00 local time, followed by `python scripts/red-team-hybrid/freshness_watchdog.py --json` whose output is appended to `artifacts/keepalive/YYYY-MM-DD.json`. Rationale: `signals.db` is a local SQLite file, so a GitHub Actions runner cannot execute the keep-alive against production data. The scheduled-task approach runs on the actual machine hosting the DB.
- **D-23:** Alternative path if the planner determines `signals.db` can be reached from CI (unlikely but worth checking): `.github/workflows/freshness-keepalive.yml` running the same command sequence. `.github/workflows/` is not in the protected-paths list. Planner decides between D-22 and D-23 during /gsd-plan-phase after verifying where `signals.db` actually lives. Default = D-22.

### LIV-03 target file (locked, added 2026-04-08 post-review — IMP-1)
- **D-24:** LIV-03 freshness precondition lands in a NEW file: `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md`. Not an inline edit to `02-bounded-context-map.md` (already dense, hard to discover) or any existing doc. The new file specifies: the preconditions every governance gate requires (freshness < 5 days for ≥3 operational collectors over the prior 7 days per LIV-03), the verification command (`python scripts/red-team-hybrid/freshness_watchdog.py --json`), the abort-or-postpone decision tree if a precondition fails, and the link back to R19 / R20. This same file hosts the GOV-03 contract (D-20) — one file, two REQs, coherent narrative.

### GOV-01 explicit targets (locked, added 2026-04-08 post-review — IMP-2)
- **D-25:** The "9% pipeline precision" claim appears in exactly 4 known locations (verified by grep on 2026-04-08):
  1. `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md:44` — inline citation, add dated strike-through caveat
  2. `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md:74` — metric table row, add caveat
  3. `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md:265-268` — entire §11 ("The relationship to the existing 9% precision number") — **NEEDS REWRITE, not strike.** The whole section is framed around treating 9% as a baseline to hit. Post-bias-audit, 9% is not a valid baseline because the cohort it was measured on is selection-biased. §11 must be rewritten to frame Tier-2 recall as the primary metric and explicitly retire 9% as a comparison target.
- **D-26:** A wider grep pass across all `docs/` + `.planning/` + `CLAUDE.md` is still required to catch any additional citations. The planner MUST run `grep -rn "9%" docs/ .planning/ CLAUDE.md` before committing GOV-01 and include the full list of touched files in the commit message.

### REC-04 founder extraction scoping (added 2026-04-08 post-review — IMP-3)
- **D-27:** `signals.raw_data` is a JSON blob with heterogeneous schemas per collector. Manual extraction across 767 rows is ~64h; per-collector scripted extraction is ~6-8h. Phase 1 ships `scripts/red-team-hybrid/extract_founder_candidates.py` with per-collector handlers:
  - `arxiv`: parse `authors[]`, filter to rows where a company name appears in title or abstract
  - `hacker_news`: parse `by` field + title, apply simple person-name heuristic (two capitalized tokens, not a known product-name pattern)
  - `news_api`: parse `title` + `description`, extract capitalized phrases near "founder", "CEO", "co-founder"
  - Others: skipped (low-signal raw_data)
- **D-28:** Target is 30-40 Claude-seeded rows. Extraction heuristics are weak — if they yield fewer, D-03 applies: document the shortfall in VERIFICATION.md, do not block the phase boundary. The analyst's 10-20 extensions close the gap to the 50 target.

### Interim R20 mitigation (added 2026-04-08 post-review — IMP-5)
- **D-29:** R20 ("Analyst abandonment") as originally captured in LIV-11/GOV-02 points mitigation at Move 0.5 (Phase 2), which starts 2026-04-19 — AFTER the 2026-04-18 regret check. This leaves R20 effectively OPEN during the entire Phase 1 window. Phase 1 ships these interim mitigations so R20's risk-register row has a non-empty Phase 1 entry:
  1. **Automated keep-alive** (D-22 / D-23) — removes analyst from the critical path for freshness.
  2. **Daily watchdog alert** — the keep-alive task writes `artifacts/keepalive/YYYY-MM-DD.json` on every run. A companion check (manual or scripted) flags missing days as a degraded signal, independent of analyst attention.
  3. **STATE.md progress tick** — Claude updates `.planning/STATE.md` at the end of each Wave commit with Phase 1 progress percentage. Analyst sees movement even on days they don't open the repo.
- **D-30:** The R20 risk-register row (in LIV-11) explicitly lists these interim mitigations AND the Phase 2 permanent mitigation. Status flips from "Open" to "Mitigating (interim) / Pending permanent (Phase 2)".

### Phase 1 → Phase 2 handoff contract (added 2026-04-08 post-review — IMP-6)
- **D-31:** Phase 2's daily digest (LIV-07 to LIV-14) needs these Phase 1 outputs as structured inputs:
  1. `scripts/red-team-hybrid/freshness_watchdog.py --json` — freshness status for the digest header ("4 collectors fresh / 1 stale / 0 missing")
  2. `data/shadow/track_b_episodes.csv` — label progress count for the digest calibration positives section
  3. `data/shadow/founder_watchlist.csv` — watchlist count for the digest context
  4. `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` — governance gate contract for the digest's governance status line
- **D-32:** The planner MUST confirm these inputs exist with the expected schema before Phase 1 closes. Schema compatibility is the Phase 1 VERIFICATION.md check, not a Phase 2 discovery.

### Day-by-day kill criteria (added 2026-04-08 post-review — IMP-4)
- **D-33:** Phase 1 gates, enforceable at the daily STATE.md update:
  - **Day 3 (2026-04-11):** All Wave A must be landed (LIV-03, LIV-11/GOV-02, GOV-01, GOV-03 docs, GOV-04). If not → abort Wave B REC-03 (Track D design) first, then abort REC-04 if still behind. LIV-11/GOV-02 + GOV-04 are non-negotiable — they gate the 2026-04-18 regret check.
  - **Day 6 (2026-04-14):** Wave B must be ≥50% landed. If not → carry Track B (REC-01) CSV seed to Phase 2 day 1; keep REC-02 (Track C split) and REC-04 (Track E seed) in Phase 1.
  - **Day 9 (2026-04-17):** Wave C (SUB-01 charter rollup) must be drafted. If not → document the gap and ship at 80% per `01-move-0-charter.md §1`.
  - **Day 10 (2026-04-18, HARD GATE):** `freshness_watchdog --json` must exit 0. `14-step4b-preconditions.md` must be committed. R20 row in risk register must be "Mitigating (interim)". If any of these fail → the 2026-04-18 Step 4B regret check POSTPONES per LIV-03 until remediation. The check does not run on failed preconditions.
- **D-34:** Gate evaluations commit to `.planning/STATE.md` as a daily line (e.g., "2026-04-11 day 3 check: Wave A 5/5 landed, on track"). Executor MUST update this before claiming a Wave complete.

### Claude's Discretion
- Column schema for `founder_watchlist.csv` beyond D-09 (`source=analyst|claude`) and D-10 (`reputation_score` stub)
- Specific `thesis_category` × `confidence` stratification buckets for the DB mining in D-04
- Wider grep sweep for "9%" beyond the 4 known lines per D-26
- Exact prose of the R20 row in the risk register (structure is locked per D-30; wording is Claude's choice)
- Whether to batch-commit Wave A as one per-REQ commit or one wave-level commit
- Windows Task Scheduler XML template if D-22 path is chosen over D-23
- Per-handler heuristic tuning in `extract_founder_candidates.py` per D-27

</decisions>

<specifics>
## Specific Ideas

- **R20 framing** — severity 5 × likelihood 5 = 25, Showstopper. The user's phrasing from earlier synthesis: "analyst abandonment" is the binding constraint, and R19 (38-day silent freeze) was empirical proof that the existing engagement loop was already broken. Permanent mitigation is Move 0.5; Phase 1 ships interim mitigations per D-29.
- **Insurance, not parallelism** — The pragmatic split is explicitly insurance against R20 sharp edges, not a way to do more work faster. Claude-autonomous REQs are the certainty floor (~1-2h Fermi); Track B/E is the growth ceiling.
- **Selection bias honesty** — The 2026-04-06 bias audit withdrew the 9% pipeline precision claim. Every artifact Phase 1 ships that references cohort metrics must carry the bias-audit caveat inline. No exceptions.
- **No LinkedIn scraping** — hard no, per LOB.txt. Even though prior plan docs reference it, Track E stays on public sources only.
- **R19 root cause** — the ROOT cause was "no scheduled collection", not "pipeline broken". LIV-01's manual restart is insufficient. D-22/D-23 closes the actual root cause.
- **Two-file consolidation** — `14-step4b-preconditions.md` hosts both LIV-03 (preconditions) and GOV-03 (gate contract). One file, two REQs, coherent narrative, single discovery point for the 2026-04-18 reviewer.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy anchor
- `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` — Direction-A-derived hybrid strategy. This is the canonical plan for the entire 4-move arc.
- `docs/plans/2026-04-06-red-team-hybrid/README.md` — Index for the 13-file plan directory.

### Move 0 charter (SUB-01 source)
- `docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md` — Move 0 deliverables list, 80% rule, day-12 hard freeze. This is what SUB-01 verifies against.
- `docs/plans/2026-04-06-red-team-hybrid/02-bounded-context-map.md` — Bounded-context map. Referenced for context only; LIV-03 does NOT land here (see D-24).

### Risk register (LIV-11 / GOV-02 / R20 target)
- `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` — Active risks with scores. R19 already in "New risks" section as Showstopper 20. R20 goes here with Showstopper 25 + interim mitigations per D-30.

### Dead-letter and LLM failure (governance context)
- `docs/plans/2026-04-06-red-team-hybrid/03-dead-letter-contract.md` — Dead-letter contract. Referenced by R14 mitigation.
- `docs/plans/2026-04-06-red-team-hybrid/04-llm-failure-mode.md` — LLM structured-outputs failure mode. Referenced by R6 tooltip mitigation and R10.

### Track C (hold-out cohort, REC-02 source)
- `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` — Hold-out cohort design. REC-02 implements the split per this spec. Contains 9% citation at line 44 (GOV-01 target per D-25).

### Tier-2 eval (context for cohort work, GOV-01 primary target)
- `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` — Tier-2 recall eval framework and Wilson CI requirement. Contains 9% citations at line 74 and §11 (lines 265-268). §11 requires a REWRITE per D-25, not just a strike-through.

### Collector audit (context for substrate work)
- `docs/plans/2026-04-06-red-team-hybrid/07-collector-audit.md` — Collector audit findings. Not a Phase 1 deliverable but referenced by later Wave B decisions about which collectors to mine for Track B candidates.

### Track B labelling (REC-01 source)
- `docs/plans/2026-04-06-red-team-hybrid/08-track-b-labelling.md` — Track B labelling methodology. REC-01 implements the CSV seed per this spec. R15 escalation triggers live in §7.

### Track E watchlist (REC-04 source)
- `docs/plans/2026-04-06-red-team-hybrid/09-track-e-watchlist.md` — Track E founder watchlist design. REC-04 implements the CSV seed per this spec. R16 analyst-availability gate in §3.

### Bias audit (GOV-01 source)
- `docs/plans/2026-04-06-lob-progress-eval/bias-audit.md` — 2026-04-06 bias audit that withdrew the 9% pipeline precision claim. GOV-01 references this when editing docs that still cite the number.

### GSD project context
- `.planning/PROJECT.md` — Project context with synthesis revisions.
- `.planning/REQUIREMENTS.md` — v1 REQs. Phase 1 REQs are LIV-01 to LIV-03, LIV-11, GOV-01 to GOV-04, SUB-01, REC-01 to REC-04.
- `.planning/ROADMAP.md` — 5-phase roadmap. Phase 1 success criteria live in §Phase 1.
- `.planning/STATE.md` — Session context index (Phase 1 will update this per D-34).

### CLAUDE.md rules
- `.claude/rules/invariants.md` — Core invariants (Notion schema, architecture rules, prohibitions).
- `.claude/rules/plan-verification.md` — Plan verification requirements.
- `CLAUDE.md` — Active sprint state, API key coverage, protected paths.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/red-team-hybrid/freshness_watchdog.py` — Just shipped. Phase 1 Wave B scripts (`extract_founder_candidates.py`, `install_keepalive_task.ps1`) follow the same stdlib-only, argparse-driven, JSON-or-text output pattern.
- `scripts/red-team-hybrid/check_protected_paths.sh` — Pre-commit guard. Every Phase 1 commit runs this before git commit. Secondary defense against D-21 violations.
- `scripts/red-team-hybrid/track_b_episodes.template.csv` — Existing template. REC-01 CSV seed extends this with real rows per D-05 schema.
- `.claude/skills/quality-label` — Existing skill. REC-01 routes analyst confirmations through it so labels land in `signal_quality_metrics` with audit trail. No new labelling UI.
- `signals.db` (v51, 767 signals) — Source data for REC-01 DB mining and REC-04 founder extraction. `thesis_classifications` table (3,085 rows, last classified 2026-04-05) is the label source for stratification.
- `.planning/codebase/*.md` — Existing codebase maps from GSD init. Downstream agents can use them for Wave B mining logic.
- `governance/` (cli.py, contracts.py, state_policies.py, writer.py) — **PROTECTED**. Do not edit in Phase 1. GOV-03 is docs-only per D-20/D-21.

### Established Patterns
- **Atomic commits per REQ** — GSD execution pattern. Wave A follows this strictly.
- **Protected-paths guard runs before every commit** — Step 4B regret window insurance. Enforced by `scripts/red-team-hybrid/check_protected_paths.sh` and `.claude/hooks/postedit_protected_paths.ps1`.
- **Docs in `docs/plans/2026-04-06-red-team-hybrid/` are the canonical substrate** — `.planning/` derivatives must not contradict them without a dated correction.
- **Dated callouts for corrections** — D-12/D-13/D-14 generalize this pattern: preserve original text, add dated delta, never silently overwrite historical claims.
- **Two-file consolidation for related REQs** — D-20/D-24 share `14-step4b-preconditions.md`. One coherent doc > two fragmented files.

### Integration Points
- Phase 1 output connects to Phase 2 (Move 0.5 Liveness Restoration) via: Track B/E CSVs as data input, LIV-03 freshness precondition as governance input, GOV-03 gate contract as monitoring contract, freshness_watchdog output as digest header input (see D-31).
- Phase 1 output connects to the 2026-04-18 Step 4B regret check via: LIV-03 precondition (blocks the check if freshness fails), LIV-11/GOV-02 R20 row (visible risk context for the reviewer), keep-alive task output as freshness evidence.
- Phase 1 output connects to R19 permanent fix via: D-22/D-23 scheduled keep-alive. This closes the actual R19 root cause, not just the symptom.

</code_context>

<rubric>
## Phase 1 Verification Rubric

Evaluator runs this at day 10 (2026-04-18) or day 11 (2026-04-19). Each criterion is pass/fail, no judgment calls.

**Hard gates (any fail → phase blocked, regret check postpones per LIV-03):**
1. `bash scripts/red-team-hybrid/check_protected_paths.sh` → rc=0
2. `python scripts/red-team-hybrid/freshness_watchdog.py --json` → rc=0, all 4 operational collectors FRESH
3. `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` contains R20 row with Severity 5 × Likelihood 5 = 25 Showstopper + interim mitigation list per D-30
4. `docs/plans/2026-04-06-red-team-hybrid/14-step4b-preconditions.md` exists and specifies LIV-03 preconditions + GOV-03 gate contract
5. Keep-alive task installed AND has run successfully at least twice: `scripts/red-team-hybrid/install_keepalive_task.ps1` installed with evidence in `artifacts/keepalive/` OR `.github/workflows/freshness-keepalive.yml` present with successful run history
6. `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` has dated "Framing Correction (2026-04-08)" callout per D-12 (GOV-04)
7. `grep -rn "9%" docs/ .planning/ CLAUDE.md` returns only dated/caveat-wrapped matches; §11 of `06-tier-2-recall-eval.md` is REWRITTEN per D-25 (GOV-01)

**Soft gates (fail → documented in VERIFICATION.md, not blocked):**
8. `data/shadow/track_b_episodes.csv` has ≥15 candidate rows (target 30, per D-03)
9. `data/shadow/founder_watchlist.csv` has ≥30 rows (target 50, per D-03)
10. `data/shadow/holdout_split/*.csv` committed (REC-02 Track C)
11. `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md` exists with the 5 answers from D-19
12. `.planning/STATE.md` contains day-by-day gate evaluations per D-34
13. Phase 1 → Phase 2 handoff inputs (D-31) exist with expected schemas

**Overall pass condition:** all 7 hard gates green. Soft gate shortfalls are tolerated if documented in `.planning/phases/01-move-0-prep-liveness-prep/1-VERIFICATION.md` with explicit reason and carry-over plan to Phase 2.

</rubric>

<deferred>
## Deferred Ideas

- **Track D implementation (CT-log + DNS shadow collector)** — Design only in Phase 1 per REC-03; implementation unblocks on 2026-04-19 and lands in Phase 3 (Move 1 Substrate).
- **Track B random-sampled cohort addressing the full 2026-04-06 bias audit** — Phase 1 ships the biased-but-useful DB-mined Track B. The true random sample is a Phase 3+ deliverable because it requires new collection logic that touches protected paths.
- **Full founder reputation scoring (V2-05)** — Phase 1 ships the stub column only. Real scoring is V2 per REQUIREMENTS.md.
- **GOV-03 code implementation** — Phase 1 ships the contract doc only per D-20. Implementation in `governance/` lands in Phase 2 after the 2026-04-19 unfreeze.
- **Postgres dual-write (SUB-08 / V2-03)** — Permanently deferred per synthesis.
- **LOB.txt grafts: outreach narrative, traffic-light timing, engine confessions** — REQs UX-01 through UX-04, Phase 3 deliverables.
- **Twitch trust-transfer (V2-01)** — Gated on labeled cohort plus demographic guardrail, deferred.
- **Pandora-lite full feature set (V2-04)** — Phase 2 ships LIV-13 digest column only; full scale is V2.
- **CLAUDE.md Closed-Loop Skills section** — Was flagged as a pre-Phase-1 housekeeping item but deferred because context was tight. Not in Phase 1 scope; add as a todo or roll into Phase 2 doc updates.
- **GitHub Actions-based keep-alive (D-23)** — fallback if signals.db is reachable from CI. Default is Windows Task Scheduler (D-22) because signals.db is local SQLite.

</deferred>

---

*Phase: 01-move-0-prep-liveness-prep*
*Context gathered: 2026-04-08*
*Reviewed: 2026-04-08 (13-lens multi-framework pass; findings integrated as D-20..D-34 + rubric)*
