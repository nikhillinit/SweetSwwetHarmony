# Strategy: Optimal VC Exit Predictor Integration

## Goal
Design and plan an optimized VC Exit Predictor feature that integrates seamlessly with the Discovery Engine, combining the governance-first architecture from the external spec with the existing pipeline infrastructure.

## Phase 1: Requirements ✅
- [x] Define success criteria (PitchBook-style percentile ranking, 60%+ precision@80th)
- [x] Identify stakeholders (Investment team, Pipeline maintainers)
- [x] Set scope boundaries (US/UK consumer focus, Pre-Seed to Series A)
- [x] List known constraints (No paid APIs, must use existing collectors)

## Phase 2: Research ✅
- [x] Inventory existing docs (4 parallel agent reviews completed)
- [x] Identify external sources needed (VC Exit Predictor spec v2.0)
- [x] Map competitive landscape (PitchBook 34-feature approach)
- [x] Gather quantitative data (Exit probability thresholds, signal weights)

## Phase 3: Analysis ✅
- [x] Apply framework (Gap analysis: spec vs current codebase)
- [x] Cross-reference sources (4 agent findings consolidated)
- [x] Identify contradictions (Governance complexity vs MVP speed)
- [x] Quantify where possible (Implementation estimates, feature counts)

## Phase 4: Synthesis ✅
- [x] Draft strategic options (3 paths: Full, Staged, MVP)
- [x] Evaluate trade-offs per option
- [x] Stress-test recommendations
- [x] Document risks & mitigations

## Phase 5: Delivery ✅
- [x] Write OPTIMAL_STRATEGY.md
- [x] Validate against success criteria
- [x] Quality checklist complete
- [x] Design document approved (2026-01-15-exit-predictor-phase1-design.md)

---

# Implementation Plan

**Design:** `docs/plans/2026-01-15-exit-predictor-phase1-design.md`
**Methodology:** TDD (Red-Green-Refactor)

## Implementation Tasks

### Phase A: Data Models & Core Logic

- [ ] A1. Create ExitEvidence dataclass in `utils/exit_predictor.py`
- [ ] A2. Create ExitPrediction dataclass with all fields
- [ ] A3. Write failing test for _compute_traction_score
- [ ] A4. Implement _compute_traction_score (pass test)
- [ ] A5. Write failing test for _compute_funding_score
- [ ] A6. Implement _compute_funding_score (pass test)
- [ ] A7. Write failing test for _compute_age_score
- [ ] A8. Implement _compute_age_score (pass test)

### Phase B: ExitPredictor Class

- [ ] B1. Write failing test for _compute_deal_quality
- [ ] B2. Implement _compute_deal_quality (pass test)
- [ ] B3. Write failing test for _compute_exit_probability
- [ ] B4. Implement _compute_exit_probability (pass test)
- [ ] B5. Write failing test for _compute_confidence
- [ ] B6. Implement _compute_confidence (pass test)
- [ ] B7. Write failing test for _compute_recommendation
- [ ] B8. Implement _compute_recommendation (pass test)

### Phase C: Full Predictor & Evidence

- [ ] C1. Write failing test for predict() method
- [ ] C2. Implement ExitPredictor.predict() (pass test)
- [ ] C3. Write failing test for _build_evidence
- [ ] C4. Implement _build_evidence (pass test)
- [ ] C5. Write test for stubbed investor_centrality=0.5
- [ ] C6. Write test for stubbed patent_count=0

### Phase D: Database Layer

- [ ] D1. Add migration 7 to storage/migrations.py
- [ ] D2. Write failing test for store_exit_prediction
- [ ] D3. Implement store_exit_prediction in SignalStore
- [ ] D4. Write failing test for get_exit_prediction
- [ ] D5. Implement get_exit_prediction
- [ ] D6. Write failing test for update_percentile_rank
- [ ] D7. Implement update_percentile_rank

### Phase E: Batch Job

- [ ] E1. Create utils/exit_predictor_batch.py
- [ ] E2. Write failing test for compute_percentiles
- [ ] E3. Implement ExitPredictorBatch.compute_percentiles

### Phase F: Pipeline Integration

- [ ] F1. Write failing integration test for pipeline
- [ ] F2. Add ExitPredictor initialization to pipeline
- [ ] F3. Wire predict() call after verification gate
- [ ] F4. Add ENABLE_EXIT_PREDICTOR feature flag
- [ ] F5. Add metrics tracking

### Phase G: Final Verification

- [ ] G1. Run full test suite
- [ ] G2. Manual smoke test with dry run
- [ ] G3. Update CLAUDE.md with new feature

## Git Commits

Each phase completion = 1 commit with passing tests.

## Dependencies

- Phase A: None (can start immediately)
- Phase B: Requires A (needs data models)
- Phase C: Requires A, B
- Phase D: Requires A (needs ExitPrediction model)
- Phase E: Requires D (needs storage methods)
- Phase F: Requires C, D, E
- Phase G: Requires all above

## Key Questions
- Q1: Should we adopt full governance-as-code or simplified approach? → **RESOLVED: Hybrid - full governance for sources, simplified for features**
- Q2: ML model or heuristic scoring initially? → **RESOLVED: Heuristic MVP, ML in Phase 2**
- Q3: How to handle missing historical data? → **RESOLVED: Use signal velocity as growth proxy, build snapshots in background**

## Decisions Made
| Decision | Rationale | Date |
|----------|-----------|------|
| Hybrid governance approach | Full spec is 15% implemented, need working system faster | 2026-01-15 |
| Heuristic scoring first | No training data yet, can validate formula before ML | 2026-01-15 |
| Integrate after verification gate | Uses already-processed signals, minimal pipeline disruption | 2026-01-15 |
| Reuse existing founder/velocity infrastructure | Already built and tested, reduces scope | 2026-01-15 |
| Skip OpenCorporates | Forbidden in spec, GLEIF alternative not needed for UK/US focus | 2026-01-15 |

## Errors Encountered
| Error | Resolution | Attempt # |
|-------|------------|-----------|
| None yet | - | - |
