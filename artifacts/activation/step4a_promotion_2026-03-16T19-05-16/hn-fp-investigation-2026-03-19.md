# HackerNews FP Investigation (2026-03-19)

## Summary
- **Hypothesis tested:** Is current HN failure source-specific, key-specific, parser-specific, or thesis-routing-specific?
- **30d rerun:** 41 FP / 42 labeled, 0 TP, 100.00% FP rate (1 UNSURE)
- **90d context:** 151 FP / 157 labeled, 2 TP, 98.69% FP rate (4 UNSURE)
- **Reviewed samples:**
  - 12 unique HN FP signals across deterministic slices
  - 5 strongest HN non-FP/unlabeled counterfactuals (2 TP, 3 UNSURE)
  - 0 non-HN overlap signals on shared canonical keys (zero cross-source contamination)

## Root Cause Breakdown
Primary-cause counts only (from 12-signal coded sample).

| Primary Cause | Count | Pct | Example Signal IDs | Typical Fix |
|---------------|-------|-----|--------------------|-------------|
| b2b_leak | 6 | 50% | 608, 593, 604, 582, 591, 605 | negative_keyword / thesis_matcher |
| parsing_artifact | 3 | 25% | 607, 610, 609 | parser_fix |
| category_misroute | 3 | 25% | 606, 602, 603 | thesis_matcher |
| duplicate_noise | 0 | 0% | — | — |
| weak_canonical_key | 0 | 0% | — | — |
| other | 0 | 0% | — | — |

**Key structural finding:** All 41 HN FPs in the 30d window have `thesis_category=UNKNOWN`. The keyword pre-filter and LLM classifier (shadow mode) are not gating HN signals. This is the root cause of the 100% FP rate — signals pass through unclassified.

## Signal Coding Table

| signal_id | slice | primary_cause | secondary_cause | fix_type | confidence | notes |
|-----------|-------|---------------|-----------------|----------|------------|-------|
| 608 | recent_fp | b2b_leak | | negative_keyword | high | Vertex.js — 1kloc SPA framework, developer tool |
| 607 | recent_fp | parsing_artifact | | parser_fix | high | Terminal-style portfolio, personal dev project, not a startup |
| 606 | recent_fp | category_misroute | | thesis_matcher | medium | Tomoshibi — creative writing app, consumer-adjacent but not in thesis |
| 602 | recent_fp | category_misroute | | thesis_matcher | medium | Now I Get It — scientific paper translator, educational tool |
| 603 | recent_fp | category_misroute | | thesis_matcher | medium | Hired.wtf — reverse job board, novelty career site |
| 593 | recent_fp | b2b_leak | | negative_keyword | high | FindTheFuckUp — legal document error finder, B2B legal tech |
| 610 | recent_fp | parsing_artifact | | parser_fix | high | The Silent Filter — blog post about linguistic drift, not a company |
| 609 | recent_fp | parsing_artifact | | parser_fix | high | BananaOS — vibecoded OS for 486, novelty hobby project |
| 604 | recent_fp | b2b_leak | | negative_keyword | high | Crewship — deploy AI agents, developer infrastructure |
| 582 | recent_fp | b2b_leak | | negative_keyword | high | RetroTick — run Windows EXEs in browser, dev/emulation tool |
| 591 | recent_fp | b2b_leak | | negative_keyword | high | CodeLeash — agent development framework, developer tool |
| 605 | recent_fp, repeated_key_fp | b2b_leak | | negative_keyword | high | PgDog — Postgres scaling tool, B2B database infrastructure |

## Example Signals

### B2B Leak — Signal 605 (PgDog)
- **Title:** Show HN: PgDog — Scale Postgres without changing the app
- **Key:** domain:github.com | **Confidence:** 0.77 | **Category:** UNKNOWN
- **Reason:** Postgres scaling tool, B2B database infrastructure
- A clear database infrastructure product with no consumer thesis fit. Should have been excluded by B2B/enterprise/developer tools exclusion.

### B2B Leak — Signal 604 (Crewship)
- **Title:** Show HN: Crewship — Deploy AI agents to production in one command
- **Key:** domain:crewship.dev | **Confidence:** 0.60 | **Category:** UNKNOWN
- **Reason:** Deploy AI agents to production, developer infrastructure tool
- Developer tooling for AI agent deployment. Clearly B2B/developer tools exclusion.

### Parsing Artifact — Signal 610 (The Silent Filter)
- **Title:** Show HN: The Silent Filter, The Delegation of Synthesis and Linguistic Drift
- **Key:** domain:juanpabloaj.com | **Confidence:** 0.60 | **Category:** UNKNOWN
- **Reason:** Blog post about linguistic drift, content not a startup
- This is a blog post, not a company or product. The HN parser treated it as a startup signal.

### Category Misroute — Signal 606 (Tomoshibi)
- **Title:** Show HN: Tomoshibi — A writing app where your words fade by firelight
- **Key:** domain:tomoshibi.in-hakumei.com | **Confidence:** 0.60 | **Category:** UNKNOWN
- **Reason:** Creative writing app, novelty tool. Consumer-adjacent but not in thesis categories (CPG, health tech, travel, marketplaces).

### Category Misroute — Signal 602 (Now I Get It)
- **Title:** Show HN: Now I Get It — Translate scientific papers into interactive webpages
- **Key:** domain:nowigetit.us | **Confidence:** 0.72 | **Category:** UNKNOWN
- **Reason:** Educational tool, not in consumer thesis. Higher confidence (0.72) made it look promising but it's off-thesis.

## Counterfactuals

### HN Non-FP Signals (5 found)
| signal_id | label | confidence | canonical_key | title |
|-----------|-------|------------|---------------|-------|
| 125 | TP | 0.72 | domain:apps.apple.com | Wildex — Pokemon Go for real wildlife |
| 139 | TP | 0.60 | domain:flightdeepresearch.com | Deep Research for Flights |
| 152 | UNSURE | 0.72 | hacker_news:47060220 | Rebrain.gg — Doom learn, don't doom scroll |
| 110 | UNSURE | 0.67 | domain:lairner.com | Lairner — language learning app |
| 158 | UNSURE | 0.65 | domain:thinkqurio.com | ThinkQurio — Socratic AI for homework |

**Analysis:** HN CAN produce valid signals. The 2 TPs (Wildex = consumer wildlife app, FlightDeepResearch = travel) are genuine thesis fits. The 3 UNSUREs are consumer education tools — borderline but plausibly in-scope. Complete collector disablement would sacrifice this signal source.

### Non-HN Overlap: ZERO
No other collector detected the same canonical keys as HN FPs. The noise is entirely HN-isolated and does not contaminate other sources or the identity system.

### Does the evidence support HN-only fix, broader key-quality fix, or both?
**HN-only fix.** Zero weak canonical keys (no `name_loc:` prefix), zero cross-source overlap, zero duplicate descriptions. The problem is specific to HN's signal-to-noise ratio and the lack of thesis classification, not a systemic identity or key quality issue.

## Blast-Radius Results

Keyword blast-radius tested against 90d HN signals:

| Keyword | FP Hits | TP Hits | UNSURE | UNLABELED | Coverage of 151 FPs |
|---------|---------|---------|--------|-----------|---------------------|
| framework | 3 | 0 | 0 | 1 | 2.0% |
| deploy | 2 | 0 | 0 | 0 | 1.3% |
| database | 3 | 0 | 0 | 0 | 2.0% |
| developer tool | 0 | 0 | 0 | 0 | 0.0% |
| devops | 0 | 0 | 0 | 0 | 0.0% |
| infrastructure | 1 | 0 | 0 | 0 | 0.7% |

**Conclusion:** Negative keywords are ineffective for this source. The highest-coverage keyword ("framework") only catches 2% of FPs. The B2B/developer tool signals use highly diverse language that can't be captured by a small keyword set. Keyword-based remediation is rejected as primary fix.

## Draft Proposal Reconciliation
- **Proposal actions generated:** 0 (notes-only)
- **Proposal notes generated:** 3 (source_api FP rate, source_api_category FP rate, temporal hotspot)
- **Changes after counterfactual/overlap review:**
  - Collector disablement is NOT recommended (2 TPs exist, 3 UNSUREs are plausible)
  - Negative keywords are NOT recommended as primary fix (blast-radius < 3% coverage)
  - Primary recommendation shifted to **thesis classification activation** for HN signals
  - Temporal hotspot at 15:00 UTC is not actionable (just reflects US morning posting patterns)

## Decision Matrix

| Evidence Pattern | Threshold | Met? | Recommendation |
|------------------|-----------|------|----------------|
| HN has zero TP in 90d and parser/category artifacts dominate | 90d HN TP = 0 and >= 60% parsing_artifact or category_misroute | **NO** (2 TP exist; 50% are parsing/category) | Do not quarantine or disable |
| Weak keys are the main failure mode across sources | >= 40% weak_canonical_key + >= 3 overlapping keys | **NO** (0% weak keys, 0 overlap) | Not applicable |
| B2B leakage is dominant and reversible | >= 50% b2b_leak or category_misroute, blast-radius tp_hits = 0 | **PARTIALLY** (75% b2b+category, 0 TP hits — but blast radius coverage < 3%) | Keywords alone insufficient; thesis classifier needed |
| FP concentration is mostly low-confidence | >= 70% confidence <= cutoff, counterfactuals skew higher | **YES at 0.70 cutoff** (10/12 = 83% at conf <= 0.70; TPs at 0.72 and 0.60) | Threshold experiment viable but risky (TP at 0.60 would be lost) |

## Final Recommendation

### 1. Enable LLM thesis classification for HN (PRIMARY — post-window)
**Justification:** All 41 HN FPs in the 30d window have `thesis_category=UNKNOWN`. The keyword pre-filter alone cannot distinguish B2B developer tools from consumer products given HN's language diversity. Switching `LLM_THESIS_MODE=active` or implementing an HN-specific classification gate would filter the 50% that are clear B2B leaks and the 25% that are off-thesis categories.

**Evidence:** The 2 existing TPs (Wildex, FlightDeepResearch) DO fit the thesis — a working classifier would preserve these while rejecting the 98.69% that are FP.

**Risk:** Low. Shadow mode has been running; activating it changes routing but not collection.

### 2. Improve HN parser to reject non-startup content (SECONDARY — post-window)
**Justification:** 25% of the coded sample (3/12) are parsing artifacts — blog posts, personal projects, hobby OS. The HN collector should apply basic heuristics to distinguish "Show HN: [product]" from "Show HN: [blog post about concept]".

**Risk:** Low. Parser improvements are local to the HN collector.

### 3. Do NOT disable the HN collector
**Justification:** 2 TPs in 90d (Wildex, FlightDeepResearch) prove the collector can surface genuine consumer companies. 3 UNSUREs (Rebrain.gg, Lairner, ThinkQurio) are plausibly in-scope. The problem is classification, not collection.

### 4. Do NOT add targeted negative keywords as primary fix
**Justification:** Blast-radius testing shows maximum 2% FP coverage per keyword. The language diversity of B2B/developer HN posts makes keyword exclusion impractical. Keywords may be added as a supplementary measure AFTER thesis classification is active, targeting any remaining FP clusters.

### 5. Consider confidence threshold experiment (OPTIONAL — post-window)
**Justification:** 83% of sampled FPs have confidence <= 0.70. However, one TP (FlightDeepResearch) has confidence 0.60, so any cutoff above 0.60 risks losing valid signals. A threshold experiment at 0.65 could be tested in shadow mode to measure impact.

### What should be tested first after the observation window
1. Enable `LLM_THESIS_MODE=active` and run one collection cycle with HN
2. Measure the resulting thesis category distribution — expect most current FPs to classify as B2B/developer tools and be filtered
3. If FP rate drops below 70%, no further action needed
4. If FP rate remains above 70%, add parser heuristics (step 2) and consider threshold experiment (step 5)

---

## Appendix: Slice Composition

| Slice | Pool Size | Target | Notes |
|-------|-----------|--------|-------|
| recent_fp | 151 | 6 | Dominated the review queue |
| repeated_key_fp | 1 | 3 | Only 1 canonical key appeared >1 time |
| weak_key_fp | 0 | 3 | Zero `name_loc:` keys — HN uses domain-based keys |
| duplicate_description_fp | 0 | 3 | Zero duplicate descriptions — HN posts are unique |

## Appendix: Stats Reconciliation

| Metric | 30d Stats | Pattern File | Report |
|--------|-----------|-------------|--------|
| HN FP count | 41 | 41 | 41 |
| HN TP count | 0 | 0 | 0 (30d) / 2 (90d) |
| HN labeled count | 42 | 42 (41 FP + 1 UNSURE) | 42 |
| HN FP rate | 100.00% | 100.0% | 100.00% |
| Overall FP rate | 80.60% | — | 80.60% (30d) / 90.73% (90d) |
