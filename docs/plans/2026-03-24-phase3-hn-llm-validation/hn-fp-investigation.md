# HN FP Pattern Investigation — Phase 3 Pre-Run Baseline

**Date:** 2026-03-24 (snapshot)
**Source:** `patterns_phase3.json` + direct DB queries

---

## Pattern Summary (90-day window)

| Pattern | Count | FP Rate | Impact |
|---------|-------|---------|--------|
| HN overall FP rate | 157 labeled, 151 FP, 2 TP | **98.69%** | HIGH — dominant noise source |
| HN + UNKNOWN category | 119 FP, 2 TP | 98.35% | Largest slice — keyword_score=0 |
| HN + excluded category | 32 FP, 0 TP | 100% | Already excluded but still entering |
| rss_feeds FP rate | 20 FP, 7 TP | 74.07% | MEDIUM — secondary noise source |

---

## Root Cause Analysis

### Primary: All HN signals get keyword_score=0

Every labeled HN signal has `keyword_category=unknown` and `keyword_score=0.0`. The keyword matcher was never designed for HN's "Show HN" content format. Result: LLM is always skipped (threshold 0.2), so no intelligent classification happens.

### FP Categories (manual eyeball of labeled + pending signals)

**Category 1: Developer tools / infra (>50% of HN FPs)**
Examples from labeled FPs:
- Vertex.js (SPA framework), CodeLeash (agent dev framework), Better Hub (GitHub UX)
- Unfucked (Git diffing), Terminal Phone (E2EE), Hacker Smacker (HN analytics)

Examples from pending (Phase 3 targets):
- Agent Passport (OAuth for AI agents), Fostrom (IoT platform for devs)
- OkaiDokai (tool firewall), Claudebin (Claude Code sessions), Strava for Claude Code
- CEL by Example, fuse box for microservices, Unix in HTML file

**Category 2: Hobby/art/game projects (~25%)**
- 3D Mahjong (CSS), Clocksimulator, GPU ray tracer (Julia), Linex (puzzle)
- Bashtorio (Factorio in browser), Axiom (math OS)

**Category 3: B2B/enterprise (~15%)**
- WARN Firehose (layoff data), Trust Protocols (AI governance)
- Beautiful interactive explainers, Rendering 18K videos

**Category 4: Ambiguous/consumer-adjacent (~10%)**
- Rebrain.gg (learning app — could be consumer ed-tech)
- LatentScore (music from mood — consumer creative)
- Breadboard (HyperCard for web — consumer creative tools)
- Gave AI $100 (AI experiment)
- 17MB pronunciation model (consumer health-adjacent?)

---

## 2 Historical True Positives (baseline for false-negative detection)

| ID | Company | Title | Why TP |
|----|---------|-------|--------|
| 125 | Wildex | "Pokemon Go for real wildlife" | Consumer health/fitness, gamified outdoor app |
| 139 | Deep Research for Flights | "Deep Research for Flights" | Travel & hospitality, consumer flight search |

Both are clearly consumer-facing with thesis fit. The LLM should correctly identify these patterns.

---

## Expected Phase 3 LLM Outcomes (prediction)

Based on manual categorization of the 28 pending signals:

| Expected LLM verdict | Count | Signals |
|---|---|---|
| **REJECT** (dev tools/infra/B2B) | ~18 | Agent Passport, Fostrom, OkaiDokai, Claudebin, CEL, fuse box, Unix HTML, VectorNest, Echo SSH, Trust Protocols, Script Snap, Strava for Claude, Local MicroVMs, CIA Factbook, WARN Firehose, Axiom, 3D Mahjong, GPU ray tracer |
| **HOLD** (ambiguous/needs review) | ~7 | Rebrain.gg, LatentScore, Breadboard, 17MB pronunciation, Rendering 18K videos, Beautiful explainers, email marketing KB |
| **PASS** (thesis fit) | ~2-3 | Mines.fyi (consumer data viz?), Gave AI $100 (consumer experiment?), Bashtorio (consumer gaming?) |
| **Uncertain** | ~1 | Depends on LLM prompt interpretation of edge cases |

### Success Criteria for Phase 3

- **>80% correct rejects**: LLM should reject at least 14 of the ~18 clear dev-tool/B2B signals
- **No false rejection of consumer-adjacent signals**: Rebrain.gg, LatentScore, Breadboard should get HOLD, not REJECT
- **LLM rationale quality**: Each rejection should cite specific exclusion reason (dev tools, B2B, etc.)

---

## Guardrails

Per fp-pattern-investigator rules:
- **No broad suppressions**: We're not disabling HN collector (2 historical TPs prove viability)
- **Bounded scope**: Only HN signals, only scratch DB, only thesis classification layer
- **Reversible**: Scratch DB is disposable; no prod changes without separate governance

---

## References
- Prior HN investigation: `artifacts/activation/step4a_promotion_2026-03-16T19-05-16/hn-fp-investigation-2026-03-19.md`
- Pattern source: `patterns_phase3.json`
- Systems analysis: `docs/plans/2026-03-24-phase3-hn-llm-validation/findings.md`
