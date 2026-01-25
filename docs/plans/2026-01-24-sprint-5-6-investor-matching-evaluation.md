# Task Plan: Sprint 5-6 (Investor Matching & Evaluation)

## Goal
Build portfolio-forensics-based investor matching (Sprint 5) and quantifiable evaluation/calibration system (Sprint 6) for the Discovery Engine.

## Current Phase
Phase 1

## Phases

### Phase 1: Requirements & Discovery
- [x] Explore existing codebase architecture
- [x] Understand entity/storage patterns (claims, embeddings, signals)
- [x] Review similarity engine implementation
- [x] Document current test infrastructure
- [ ] Initiate Codex collaboration via Maestro
- [ ] Achieve consensus on architecture approach
- **Status:** in_progress

### Phase 2: Sprint 5 - Investor Entity & Storage
- [ ] Design Investor entity schema (Migration 9)
- [ ] Define portfolio edges store (KG-lite via claims/edges)
- [ ] Create investor_profiles table
- [ ] Create investor_portfolios table
- [ ] Create investor_preferences table (inferred from behavior)
- [ ] Add investor-specific predicates to claims system
- **Status:** pending

### Phase 3: Sprint 5 - Portfolio Data Ingestion
- [ ] Evaluate data sources (existing importers, curated lists)
- [ ] Build portfolio-page scraper (if legally allowed)
- [ ] Implement portfolio company linking via canonical_key
- [ ] Create investor "profile claims" from observed behavior:
  - Common problem/customer clusters
  - Geographic preferences
  - Stage proxies (seed vs Series A behavior)
- **Status:** pending

### Phase 4: Sprint 5 - Matching Engine
- [ ] Extend similarity_engine for startup↔investor matching
- [ ] Build investor claim extraction (infer thesis from portfolio)
- [ ] Implement matching algorithm (claims→FTS similarity)
- [ ] Add match_reasons explanations ("backed X/Y solving similar problems")
- [ ] Wire into pipeline after exit_predictor stage
- **Status:** pending

### Phase 5: Sprint 6 - Gold Set Construction
- [ ] Design gold set schema (~100 companies + labeled investors)
- [ ] Source human-labeled data:
  - problem/customer classifications
  - investor focus areas (ground truth)
- [ ] Build gold set loader for evaluation runs
- **Status:** pending

### Phase 6: Sprint 6 - Metrics & Evaluation
- [ ] Define extraction metrics (exact/partial match, abstention rate)
- [ ] Define similarity metrics (peers in top 10)
- [ ] Build evaluation runner with baseline comparison
- [ ] Track downstream metrics (Notion approve/reject as weak supervision)
- **Status:** pending

### Phase 7: Sprint 6 - Regression Tests & Drift Detection
- [ ] Add regression tests for claim extraction schema stability
- [ ] Add regression tests for similarity ranking on gold set
- [ ] Implement drift alerts:
  - Extraction confidence collapse detection
  - Abstention rate spikes
- [ ] Wire alerts to Slack
- **Status:** pending

### Phase 8: Testing & Verification
- [ ] Comprehensive unit tests for new modules
- [ ] Integration tests for investor matching pipeline
- [ ] E2E test: URL → relevant investors with explanations
- [ ] Performance benchmarks
- **Status:** pending

### Phase 9: Delivery
- [ ] Documentation
- [ ] Final code review
- [ ] Feature flag enablement
- [ ] User handoff
- **Status:** pending

## Key Questions
1. What portfolio data sources are legally accessible? (Scraping vs APIs)
2. How to infer investor thesis from portfolio without self-description?
3. What constitutes "similar problems/customers" for matching?
4. How large should gold set be for meaningful calibration?
5. What's acceptable false positive rate for investor recommendations?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Extend existing claims/edges pattern | Proven KG-lite architecture already in codebase |
| Use canonical_key for investor linking | Consistent with company deduplication |
| Portfolio-forensics approach | Infers reality from behavior, not marketing |
| FTS + embedding similarity | Proven in similarity_engine.py |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes
- Codex collaboration pending for architecture critique
- Existing infrastructure provides strong foundation
- Feature-flag approach for safe rollout
