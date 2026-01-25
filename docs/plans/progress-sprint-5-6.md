# Progress Log: Sprint 5-6

## Session: 2026-01-25

### Completed
- [x] Explored codebase architecture for sprint planning
- [x] Created planning files (task_plan.md, findings.md, progress.md)
- [x] Documented existing patterns
- [x] Codex Iteration 1: Initial architecture proposal received
- [x] Claude Critical Review: Identified 2 blocking + 3 important issues
- [x] Codex Iteration 2: Connection issues, partial exploration
- [x] **Consensus Document Created** (synthesized from both perspectives)

### Consensus Achieved

#### Migration 9 Schema (8 tables + FTS5)
1. `investors` - Core entity with type, AUM, source
2. `investor_portfolios` - Portfolio edges with FK to claim_extractions
3. `investor_profile_claims` - Inferred thesis with lift scores
4. `investor_profiles` - Cached distributions + embeddings
5. `investor_preferences` - Manual overrides
6. `global_baselines` - P(predicate=value) for lift calculation
7. `investor_profile_fts` - FTS5 search index
8. `investor_matches` - Cached match results

#### Evidence Independence Rules
- Different source_api = independent
- Different source_signal_id = independent
- Same source but 30+ days apart = independent
- Minimum 2 independent sources for high confidence

#### Global Baselines Strategy
- Computed nightly by InvestorProfileBatch job
- Source: Crunchbase companies from last 2 years
- Predicates: sector, stage, geo, business_model
- Lift formula: log(P_investor / P_global)

#### Pipeline Integration
- Feature flag: ENABLE_INVESTOR_MATCHING
- Integration point: After exit_predictor stage
- Output: Top 5 investors with explanations to Notion

#### Drift Detection Thresholds
- Extraction F1 drop > 5 points: RED alert
- Abstention rate > 25%: RED alert
- Top-10 recall < 60%: RED alert
- Confidence collapse (< 55% for 3 runs): RED alert

### Key Files Created
- `docs/plans/2026-01-24-sprint-5-6-investor-matching-evaluation.md` - Task plan
- `docs/plans/findings-sprint-5-6.md` - Research findings
- `docs/plans/codex-iteration-1.md` - Codex proposal + Claude critique
- `docs/plans/consensus-sprint-5-6.md` - **Final consensus architecture**

### Codex Collaboration Summary
- **Iteration 1**: Codex proposed 6-table schema with lift-based thesis inference
- **Claude Critique**: Found schema integration gap, undefined global baselines, missing batch job
- **Iteration 2**: Codex experienced connectivity issues
- **Synthesis**: Claude combined best elements + addressed all blocking issues

### Test Results
*(pending implementation)*

### Errors
| Error | Resolution |
|-------|------------|
| Codex --reasoning-effort flag | Flag not supported in current Codex CLI version |
| Codex reconnection issues | Network timeouts during iteration 2, worked around |

---

## Next Steps
1. ~~Review consensus document with stakeholder~~
2. ~~Implement Migration 9 in signal_store.py~~ ✅ COMPLETE
3. ~~Create utils/investor_matching.py~~ ✅ COMPLETE
4. ~~Create utils/investor_profile_batch.py~~ ✅ COMPLETE
5. ~~Add feature flag and pipeline integration~~ ✅ COMPLETE
6. ~~Write test suite~~ ✅ COMPLETE (47 tests total)

**Sprint 5 COMPLETE** - All investor matching components implemented.

---

## Migration 9 Implementation (2026-01-25)

### Schema Changes
- Updated `CURRENT_SCHEMA_VERSION` from 8 to 9
- Added 13 new tables:

**Investor Matching (Sprint 5):**
1. `investors` - Core investor entity
2. `investor_portfolios` - Portfolio edges with FK to claim_extractions
3. `investor_profile_claims` - Inferred thesis with lift scores
4. `investor_profiles` - Cached distributions + embeddings
5. `investor_preferences` - Manual overrides
6. `global_baselines` - P(predicate=value) for lift calculation
7. `investor_profile_fts` - FTS5 search index
8. `investor_matches` - Cached match results

**Evaluation & Calibration (Sprint 6):**
9. `gold_set_companies` - Gold set for evaluation
10. `gold_set_labels` - Human annotations
11. `gold_set_investor_labels` - Investor relevance labels
12. `evaluation_runs` - Evaluation run metrics
13. `drift_alerts` - Drift detection alerts

### Helper Methods Added
- `save_investor()` - Save/update investor entity
- `save_portfolio_entry()` - Save portfolio relationship
- `save_investor_profile_claim()` - Save inferred claim
- `save_global_baseline()` - Save global baseline
- `get_global_baseline()` - Retrieve baseline probability
- `save_investor_match()` - Save match result
- `get_investor_matches()` - Get matches for company
- `get_investor_portfolio()` - Get portfolio entries
- `get_investor_profile_claims()` - Get profile claims
- `save_evaluation_run()` - Save evaluation metrics
- `save_drift_alert()` - Save drift alert
- `get_unacknowledged_drift_alerts()` - Get open alerts
- `acknowledge_drift_alert()` - Mark alert acknowledged

### Verification
- In-memory migration test: PASSED
- Schema version: 9
- All existing storage tests: PASSING (120 tests)

---

## Investor Matching Implementation (2026-01-25)

### Module Created: utils/investor_matching.py

**Data Classes:**
- `PortfolioEvidence` - Portfolio company evidence for explanations
- `MatchExplanation` - Explanation with reason, predicate, lift_score, examples
- `InvestorMatch` - Full match result with all scores
- `InvestorMatchResult` - Query result with matches list

**Scoring Functions:**
- `compute_distribution_match()` - Stage/sector distribution fit scoring
- `compute_constraint_score()` - Preference constraint scoring (hard_no, exclude, boost, penalize)
- `compute_final_score()` - Weighted combination with cold-start penalty
- `generate_explanation()` - Human-readable explanation generation

**Main Class:**
- `InvestorMatcher` - Main matching interface
  - `match()` - Match single company to investors
  - `match_batch()` - Match multiple companies

**Scoring Weights:**
```python
DEFAULT_WEIGHTS = {
    'fts': 0.20,        # BM25 keyword match
    'embedding': 0.25,  # Semantic similarity
    'stage': 0.20,      # Stage distribution fit
    'sector': 0.25,     # Sector distribution fit
    'constraint': 0.10, # Preference compliance
}
COLD_START_PENALTY = 0.15
```

### Pipeline Integration

**Changes to workflows/pipeline.py:**
- Added `use_investor_matching` config flag (default: False)
- Added `ENABLE_INVESTOR_MATCHING` env var
- Added `_investor_matcher` initialization
- Added investor matching after exit_predictor stage
- Updated `_push_to_notion()` to accept investor_match_result
- Added investor_matches to ProspectPayload serialization

**Changes to connectors/notion_connector_v2.py:**
- Added `investor_matches: List[Dict[str, Any]]` field to ProspectPayload

### Test Coverage
- 31 tests in tests/utils/test_investor_matching.py
  - 7 distribution matching tests
  - 7 constraint scoring tests
  - 5 final score tests
  - 4 explanation generation tests
  - 3 InvestorMatcher mock tests
  - 3 integration tests with real DB
  - 2 data class tests

### Verification
- All 31 investor matching tests: PASSING
- Pipeline integration tests: PASSING (13 tests)
- Storage tests: PASSING (54 tests)

---

## Investor Profile Batch Job (2026-01-25)

### Module Created: utils/investor_profile_batch.py

**BatchResult Dataclass:**
- Tracks: total_investors, profiles_updated, claims_refreshed, baselines_computed, fts_entries_created, cold_start_count
- Duration calculation and to_dict() for logging

**InvestorProfileBatch Class:**
- `run()` - Execute full batch job
- `_compute_global_baselines()` - Compute P(predicate=value) from portfolios and signals
- `_compute_lift_score()` - Calculate log-odds lift vs global baseline
- `_refresh_all_profiles()` - Update all investor profiles and claims
- `_refresh_investor_claims()` - Generate claims for single investor
- `_update_investor_profile()` - Update cached profile distributions
- `_rebuild_fts_index()` - Rebuild FTS5 search index

**Configuration:**
```python
BASELINE_PREDICATES = ['sector', 'stage', 'geo', 'business_model']
LIFT_THRESHOLD = 0.1  # Minimum lift to create claim
COLD_START_THRESHOLD = 3  # Portfolios < 3 = cold start
```

**CLI Usage:**
```bash
python -m utils.investor_profile_batch --db-path signals.db -v
```

### Test Coverage
- 16 tests in tests/utils/test_investor_profile_batch.py
  - 3 BatchResult tests
  - 5 InvestorProfileBatch unit tests
  - 6 integration tests with real DB
  - 2 CLI tests

### Verification
- All 47 investor tests: PASSING (31 matching + 16 batch)
- Sprint 5 feature-complete
