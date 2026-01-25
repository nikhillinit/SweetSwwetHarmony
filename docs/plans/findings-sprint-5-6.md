# Findings & Decisions: Sprint 5-6

## Requirements

### Sprint 5: Investor Matching v1
- Create Investor entity + portfolio edges store (KG-lite via claims/edges)
- Ingest portfolio data from pragmatic sources
- Build investor "profile claims" from observed behavior:
  - Common problem/customer clusters
  - Geographic preferences
  - Stage proxies
- Match startup profile ↔ investor portfolio clusters
- Return "relevant investors" with explanations

### Sprint 6: Evaluation + Calibration
- Build ~100 company gold set with human labels
- Track extraction metrics (exact/partial match, abstention rate, evidence quality)
- Track similarity metrics (true peers in top 10)
- Track downstream metrics (Notion outcomes as weak supervision)
- Add regression tests for schema stability + ranking stability
- Implement drift alerts (confidence collapse, abstention spikes)

## Research Findings

### Existing Infrastructure Analysis
1. **Claims/Edges System** (Migration 7):
   - `predicates` table with controlled vocabulary
   - `claim_extractions` with evidence trails
   - `claims` table with confidence scoring
   - `claim_evidence` many-to-many linking
   - Already supports entity→predicate→value pattern

2. **Similarity Engine** (similarity_engine.py):
   - Hybrid FTS5 + embedding rerank
   - Stage 1: Keyword candidates (K=300)
   - Stage 2: Cosine similarity with soft boosts
   - Returns match_reasons

3. **Embedding Storage** (Migration 8):
   - 768-dim embeddings (Gemini text-embedding-004)
   - source_text_hash for staleness detection
   - Batch pre-computation pattern

4. **Portfolio Pattern** (competitor_detector.py):
   - Loads portfolio.json
   - Returns CompetitorMatch with confidence
   - Pattern extensible for investor portfolios

### Data Source Assessment
| Source | Viability | Notes |
|--------|-----------|-------|
| Curated JSON lists | HIGH | Zero cost, immediate |
| Crunchbase API | HIGH | Already integrated |
| SEC Form D | HIGH | Already have collector |
| LinkedIn | MEDIUM | Proxycurl integration exists |
| Portfolio page scraping | LOW | Legal/ToS concerns |
| AngelList/Wellfound | ABANDONED | API deprecated 2023 |

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Extend predicates for investors | Reuse proven claims architecture |
| Store investor thesis as claims | Enables evidence trail + conflict resolution |
| Use same embedding model | Consistent semantic space |
| Batch compute investor profiles | Avoid cold-start performance issues |
| Gold set as tagged signals | Leverage existing signal storage |
| Drift detection via health monitor | Extend existing pattern |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| (pending) | |

## Resources

### Existing Files to Extend
- storage/signal_store.py - Add Migration 9 for investor tables
- storage/claim_store.py - Add investor predicates
- utils/similarity_engine.py - Pattern for investor matching
- utils/exit_predictor.py - Deal quality integration
- workflows/pipeline.py - Add investor_matching stage
- utils/signal_health.py - Add drift detection

### New Files to Create
- storage/investor_store.py - Investor entity management
- utils/investor_matching.py - Matching engine
- utils/portfolio_ingester.py - Portfolio data import
- evaluation/gold_set.py - Gold set management
- evaluation/evaluator.py - Metrics runner
- tests/evaluation/ - Evaluation test suite

## Visual/Browser Findings
*(Update after research)*

---
*Update this file after every 2 view/browser/search operations*
