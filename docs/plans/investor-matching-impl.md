# Task Plan: Implement Investor Matching Module

## Goal
Create `utils/investor_matching.py` that matches startups to relevant investors based on portfolio behavior analysis, with evidence-based explanations.

## Current Phase
Complete

## Phases

### Phase 1: Core Data Classes & Types
- [x] Define `InvestorMatch` dataclass (score, reasons, evidence)
- [x] Define `InvestorMatchResult` dataclass (query info, results list)
- [x] Define `MatchExplanation` dataclass (claim, lift, portfolio examples)
- [x] Add type hints and documentation
- **Status:** complete

### Phase 2: FTS Candidate Retrieval
- [x] Implement `_build_fts_query()` from startup claims
- [x] Implement `_search_investor_fts()` with K=300 candidates
- [x] Handle empty/thin profiles gracefully
- **Status:** complete

### Phase 3: Scoring Components
- [x] Implement `compute_distribution_match()` (stage/sector)
- [x] Implement `_compute_embedding_score()` (cosine similarity)
- [x] Implement `compute_constraint_score()` (preferences)
- **Status:** complete

### Phase 4: Final Scoring & Ranking
- [x] Implement weighted combination: `final = w1*fts + w2*embed + w3*stage + w4*sector + w5*constraint`
- [x] Apply cold-start penalties (COLD_START_PENALTY = 0.15)
- [x] Rank and select top N
- **Status:** complete

### Phase 5: Explanation Generation
- [x] Implement `generate_explanation()` from matching claims
- [x] Add portfolio examples as evidence
- [x] Format human-readable reasons with lift scores
- **Status:** complete

### Phase 6: Main Matching Interface
- [x] Implement `InvestorMatcher` class
- [x] Implement `match()` async method
- [x] Implement `match_batch()` for multiple companies
- [x] Add caching support (saves to investor_matches table)
- **Status:** complete

### Phase 7: Testing
- [x] Unit tests for scoring components (19 tests)
- [x] Unit tests for data classes (2 tests)
- [x] Unit tests for explanation generation (4 tests)
- [x] Integration tests: end-to-end matching (3 tests)
- [x] Mock tests for matcher (3 tests)
- **Status:** complete (31 tests passing)

### Phase 8: Pipeline Integration
- [x] Add ENABLE_INVESTOR_MATCHING feature flag
- [x] Wire into pipeline.py after exit_predictor
- [x] Update ProspectPayload with investor_matches field
- **Status:** complete

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Mirror similarity_engine.py pattern | Proven FTS+embedding hybrid approach |
| Use existing store methods | Migration 9 already provides CRUD |
| Weighted scoring (not ML) | Interpretable, tunable, no training data needed |
| Evidence-first explanations | "backed X/Y solving similar problems" |

## Scoring Weights (Initial)

```python
WEIGHTS = {
    'fts': 0.20,        # BM25 keyword match
    'embedding': 0.25,  # Semantic similarity
    'stage': 0.20,      # Stage distribution fit
    'sector': 0.25,     # Sector distribution fit
    'constraint': 0.10, # Preference compliance
}
```

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Files to Create/Modify
- `utils/investor_matching.py` (NEW) - Main module
- `tests/utils/test_investor_matching.py` (NEW) - Tests
- `workflows/pipeline.py` (MODIFY) - Integration
