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

## Phase 5: Delivery ⬜
- [x] Write OPTIMAL_STRATEGY.md
- [ ] Validate against success criteria
- [ ] Quality checklist complete
- [ ] Handoff ready

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
