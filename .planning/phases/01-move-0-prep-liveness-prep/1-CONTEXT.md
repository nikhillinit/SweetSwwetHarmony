# Phase 1: Move 0 Prep + Liveness Prep - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Lock the governance paper trail for the 2026-04-18 Step 4B regret check, populate the Track B / C / E data substrates, and ship the Move 0 charter deliverables, all before the 2026-04-19 protected-paths unfreeze. All Phase 1 work stays in allowed Move 0 paths (`docs/`, `data/shadow/`, `scripts/red-team-hybrid/`, `.planning/`). No changes to `collectors/`, `workflows/`, `governance/`, `monitoring/`, `connectors/`, or `storage/migrations/`.

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
- **D-02:** Insurance against R20 (analyst abandonment) is the rationale — if analyst bandwidth is constrained on any single day, governance artifacts still land and the Track B/E CSVs accumulate progress rather than block on a single analyst session.
- **D-03:** Phase 1 is NOT gated on REC-01/REC-04 hitting their 30 / 50 row targets by 2026-04-19. If actuals undershoot by 2026-04-18, document the gap in the Phase 1 VERIFICATION.md and carry the shortfall into Phase 2 day 1 rather than stalling the phase boundary.

### REC-01 Track B labelling substrate source
- **D-04:** DB-mining-plus-analyst-confirmation pattern. Claude surfaces 30 candidate episodes from the 767 signals in `signals.db`, stratified by `thesis_category` and `confidence` buckets so the cohort spans TP-likely, FP-likely, and ambiguous regions of the classifier's decision surface.
- **D-05:** Each candidate is written as a row in `data/shadow/track_b_episodes.csv` (template already exists at `scripts/red-team-hybrid/track_b_episodes.template.csv`). Columns must capture signal ID, source_api, canonical_key, company_name, Claude's pre-labelled guess, pre-label rationale, and an empty `analyst_label` column for the human confirmation.
- **D-06:** The existing `quality-label` skill is the labelling tool. Analyst confirmations flow through it so they also persist to `signal_quality_metrics` with an audit trail. No new labelling UI.
- **D-07:** Selection bias acknowledgement (per 2026-04-06 bias audit): Track B cohort sourced this way is known-biased because it's drawn from what the engine already surfaced. It is the SECONDARY canary, not the primary one. The random-sampled cohort addressing the bias audit is a Phase 3+ deliverable, not Phase 1.

### REC-04 Track E founder watchlist source
- **D-08:** Claude-seed-plus-analyst-extend pattern. Claude extracts founder names mentioned in `signals.raw_data` for the higher-quality collectors (news_api, hacker_news titles, arxiv authors where a company is cited) and seeds `data/shadow/founder_watchlist.csv` with 30-40 records.
- **D-09:** Analyst extends with 10-20 high-conviction names from their external network over the 11-day window. Analyst additions are marked with a `source=analyst` column so downstream work can segment.
- **D-10:** Reputation scoring stub is a placeholder column (`reputation_score REAL NULL`) plus a one-paragraph methodology note in the CSV header comment. Full scoring is deferred to V2-05.
- **D-11:** Hard constraint from LOB.txt risk analysis: no LinkedIn scraping, even though prior plans mentioned it. Founder names extracted ONLY from sources where the founder self-published or was mentioned in a public news source.

### GOV-04 framing correction landing pattern
- **D-12:** Prepend a dated `Framing Correction (2026-04-08)` callout at the top of `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` before §1. The callout is ~6-10 lines and states the substrate-plus-engagement complementarity explicitly.
- **D-13:** The original §2 text is preserved verbatim so the git history shows what the 2026-04-06 red-team review actually concluded vs. what the 2026-04-08 synthesis revised.
- **D-14:** Same callout pattern is applied wherever GOV-01 (withdraw the 9% precision claim) needs to edit active docs — date the correction, preserve the original line, add the bias-audit caveat inline.

### Governance paper-trail ordering
- **D-15:** Order of operations within Phase 1:
  1. **Wave A (docs, Claude-autonomous):** LIV-11 / GOV-02 (R20 to risk register), GOV-04 (framing correction), GOV-01 (9% precision withdrawal), LIV-03 (freshness precondition on Step 4B regret check), GOV-03 (freshness precondition on all governance gates).
  2. **Wave B (data, Claude-seeded):** REC-02 (Track C hold-out cohort deterministic split), REC-03 (Track D design doc), REC-01 (Track B candidate mining + CSV seed), REC-04 (Track E founder extraction + CSV seed).
  3. **Wave C (synthesis):** SUB-01 (Move 0 charter deliverables per `01-move-0-charter.md` — this is the rollup that depends on Waves A and B landing).
- **D-16:** Wave A commits are ≤50 lines each, atomic per REQ. Wave B commits carry the CSV + the generating script (in `scripts/red-team-hybrid/`) so the mining is reproducible. Wave C is a verification commit confirming the charter deliverables list is complete.

### REC-02 Track C hold-out cohort split methodology
- **D-17:** Claude's discretion on the specific split strategy. Constraint: deterministic seed (so the split is reproducible from a fresh clone), file-based output committed to `data/shadow/holdout_split/`, and the seed value plus algorithm documented inline. Follow whatever `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` specifies — the canonical design doc is the authority.

### REC-03 Track D (CT-log + DNS shadow) design depth
- **D-18:** Design-only during Move 0 means docs only (ADR-style decision record + requirements). NO collector code, NO schema migrations — both would touch protected paths. Output goes to `docs/plans/2026-04-06-red-team-hybrid/13-track-d-design.md` (new file).
- **D-19:** The design doc must answer: what CT-log sources / what DNS data source / what the canonical key strategy looks like for stealth companies / what the anti-fingerprinting posture is / what the cost envelope is. Implementation blocks on the 2026-04-19 unfreeze.

### Claude's Discretion
- Exact column schemas for `track_b_episodes.csv` and `founder_watchlist.csv` beyond what the template specifies
- Specific `thesis_category` × `confidence` stratification buckets for the DB mining
- Exact set of docs that cite the "9% pipeline precision" claim (GOV-01 requires a grep pass)
- Exact file path for LIV-03 (the freshness precondition on the Step 4B regret check — could live in `02-bounded-context-map.md` or a new `14-step4b-preconditions.md`)
- Wording of the R20 row in the risk register (beyond the Showstopper 25 score and Move 0.5 mitigation pointer)
- Whether to batch-commit Wave A as one per-REQ commit or one wave-level commit

</decisions>

<specifics>
## Specific Ideas

- **R20 framing** — severity 5 × likelihood 5 = 25, Showstopper. The user's phrasing from earlier synthesis: "analyst abandonment" is the binding constraint, and R19 (38-day silent freeze) was empirical proof that the existing engagement loop was already broken. Mitigation is Move 0.5, not a tooltip.
- **Insurance, not parallelism** — The pragmatic split is explicitly insurance against R20 sharp edges, not a way to do more work faster. Claude-autonomous REQs are the certainty floor; Track B/E is the growth ceiling.
- **Selection bias honesty** — The 2026-04-06 bias audit withdrew the 9% pipeline precision claim. Every artifact Phase 1 ships that references cohort metrics must carry the bias-audit caveat inline. No exceptions.
- **No LinkedIn scraping** — hard no, per LOB.txt. Even though prior plan docs reference it, Track E stays on public sources only.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy anchor
- `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md` — Direction-A-derived hybrid strategy. This is the canonical plan for the entire 4-move arc.
- `docs/plans/2026-04-06-red-team-hybrid/README.md` — Index for the 13-file plan directory.

### Move 0 charter (SUB-01 source)
- `docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md` — Move 0 deliverables list, 80% rule, day-12 hard freeze. This is what SUB-01 verifies against.
- `docs/plans/2026-04-06-red-team-hybrid/02-bounded-context-map.md` — Bounded-context map. Candidate location for LIV-03 freshness precondition per §8 of the doc.

### Risk register (LIV-11 / GOV-02 / R20 target)
- `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md` — Active risks with scores. R19 already in "New risks" section as Showstopper 20. R20 goes here with Showstopper 25.

### Dead-letter and LLM failure (governance context)
- `docs/plans/2026-04-06-red-team-hybrid/03-dead-letter-contract.md` — Dead-letter contract. Referenced by R14 mitigation.
- `docs/plans/2026-04-06-red-team-hybrid/04-llm-failure-mode.md` — LLM structured-outputs failure mode. Referenced by R6 tooltip mitigation and R10.

### Track C (hold-out cohort, REC-02 source)
- `docs/plans/2026-04-06-red-team-hybrid/05-holdout-cohort-design.md` — Hold-out cohort design. REC-02 implements the split per this spec.

### Tier-2 eval (context for cohort work)
- `docs/plans/2026-04-06-red-team-hybrid/06-tier-2-recall-eval.md` — Tier-2 recall eval framework and Wilson CI requirement. Relevant when documenting REC-02 split methodology.

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
- `.planning/STATE.md` — Session context index.

### CLAUDE.md rules
- `.claude/rules/invariants.md` — Core invariants (Notion schema, architecture rules, prohibitions).
- `.claude/rules/plan-verification.md` — Plan verification requirements.
- `CLAUDE.md` — Active sprint state, API key coverage, protected paths.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/red-team-hybrid/freshness_watchdog.py` — Just shipped. Phase 1 Wave B scripts can use the same stdlib-only, argparse-driven, JSON-or-text output pattern.
- `scripts/red-team-hybrid/check_protected_paths.sh` — Pre-commit guard. Every Phase 1 commit runs this before git commit.
- `scripts/red-team-hybrid/track_b_episodes.template.csv` — Existing template. REC-01 CSV seed extends this with real rows.
- `.claude/skills/quality-label` — Existing skill. REC-01 routes analyst confirmations through it so labels land in `signal_quality_metrics` with audit trail. No new labelling UI.
- `signals.db` (v51, 767 signals) — Source data for REC-01 DB mining. `thesis_classifications` table (3,085 rows, last classified 2026-04-05) is the label source for stratification.
- `.planning/codebase/*.md` — Existing codebase maps from GSD init. Downstream agents can use them for Wave B mining logic.

### Established Patterns
- **Atomic commits per REQ** — GSD execution pattern. Wave A follows this strictly.
- **Protected-paths guard runs before every commit** — Step 4B regret window insurance. Enforced by `scripts/red-team-hybrid/check_protected_paths.sh` and `.claude/hooks/postedit_protected_paths.ps1`.
- **Docs in `docs/plans/2026-04-06-red-team-hybrid/` are the canonical substrate** — `.planning/` derivatives must not contradict them without a dated correction.
- **Dated callouts for corrections** — D-12/D-13/D-14 generalize this pattern: preserve original text, add dated delta, never silently overwrite historical claims.

### Integration Points
- Phase 1 output connects to Phase 2 (Move 0.5 Liveness Restoration) via: Track B/E CSVs as data input, LIV-03 freshness precondition as governance input, GOV-03 freshness gates as monitoring contract.
- Phase 1 output connects to the 2026-04-18 Step 4B regret check via: LIV-03 precondition (blocks the check if freshness fails), LIV-11/GOV-02 R20 row (visible risk context for the reviewer).

</code_context>

<deferred>
## Deferred Ideas

- **Track D implementation (CT-log + DNS shadow collector)** — Design only in Phase 1 per REC-03; implementation unblocks on 2026-04-19 and lands in Phase 3 (Move 1 Substrate).
- **Track B random-sampled cohort addressing the full 2026-04-06 bias audit** — Phase 1 ships the biased-but-useful DB-mined Track B. The true random sample is a Phase 3+ deliverable because it requires new collection logic that touches protected paths.
- **Full founder reputation scoring (V2-05)** — Phase 1 ships the stub column only. Real scoring is V2 per REQUIREMENTS.md.
- **Postgres dual-write (SUB-08 / V2-03)** — Permanently deferred per synthesis.
- **LOB.txt grafts: outreach narrative, traffic-light timing, engine confessions** — REQs UX-01 through UX-04, Phase 3 deliverables.
- **Twitch trust-transfer (V2-01)** — Gated on labeled cohort plus demographic guardrail, deferred.
- **Pandora-lite full feature set (V2-04)** — Phase 2 ships LIV-13 digest column only; full scale is V2.
- **CLAUDE.md Closed-Loop Skills section** — Was flagged as a pre-Phase-1 housekeeping item but deferred because context was tight. Not in Phase 1 scope; add as a todo or roll into Phase 2 doc updates.

</deferred>

---

*Phase: 01-move-0-prep-liveness-prep*
*Context gathered: 2026-04-08*
