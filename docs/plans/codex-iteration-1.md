# Codex Proposal Iteration 1 - Sprint 5/6 Architecture

## Proposal Summary

Codex proposed a 6-table schema with:
1. `investors` - Core investor entity
2. `investor_portfolios` - Portfolio edges with evidence
3. `investor_profiles` - Cached distributions + embeddings
4. `investor_preferences` - Manual overrides
5. `investor_profile_claims` - Inferred thesis claims
6. `investor_profile_fts` - FTS5 search index

Key algorithms:
- Thesis inference via log-odds lift against global baseline
- Recency-weighted counts with lead-bias multipliers
- Hybrid FTS + embedding rerank (mirrors similarity_engine.py)

---

## Claude's Critical Evaluation

### FEASIBILITY ISSUES

#### BLOCKING: Schema Integration Gap
**Issue:** Proposal creates 6 NEW tables but doesn't show how they integrate with existing signal_store.py migration chain (currently at Migration 8). The `evidence_id` field references "KG evidence trail id" but doesn't specify which table.

**Risk:** Migration conflicts, foreign key issues, orphaned data.

**Suggestion:** Explicitly define Migration 9 as extension of signal_store.py, show FK relationships to existing `claims`, `claim_extractions`, `claim_evidence` tables.

#### BLOCKING: Global Baseline Computation Undefined
**Issue:** The lift score formula `lift = log( (p_investor + eps) / (p_global + eps) )` assumes `p_global` exists, but there's no specification of:
- How/when to compute global baseline
- Storage location (cache? table? config?)
- Update frequency
- What constitutes "global" (all Crunchbase? Last 2 years?)

**Risk:** Without concrete implementation, this is hand-wavy pseudocode.

**Suggestion:** Define `global_baselines` table or config with fields: predicate, global_probability, sample_size, computed_at, source.

### EFFICIENCY ISSUES

#### IMPORTANT: Denormalization Trade-offs
**Issue:** `investor_profiles` stores JSON blobs (stage_distribution_json, sector_distribution_json, geo_distribution_json) which duplicates data derivable from `investor_profile_claims`.

**Concern:** Two sources of truth. Updates require syncing both.

**Alternative:** Compute distributions on-demand from claims, or use materialized view pattern with clear refresh triggers.

#### IMPORTANT: FTS Index Maintenance
**Issue:** "FTS table is rebuilt or updated whenever profile claims change" but no specification of:
- Trigger mechanism (application-level? DB trigger?)
- Full rebuild vs incremental update
- Performance impact during updates

**Suggestion:** Define explicit update strategy. Consider shadow table pattern for atomic swaps.

### SOPHISTICATION ISSUES

#### IMPORTANT: Cold-Start Handling Incomplete
**Issue:** Proposal mentions "fallback to preferences/manual claims" for cold-start investors but doesn't define:
- Threshold for "cold-start" (< N portfolio entries?)
- How manual claims integrate with scoring
- UI/UX for analyst input

**Suggestion:** Define `min_portfolio_size = 3` threshold; add `is_cold_start` computed column; explicit fallback scoring weights.

#### IMPORTANT: Explanation Evidence Quality
**Issue:** "enforce evidence_count >= 2 for claim surfacing" but no definition of:
- What counts as independent evidence (same source? different dates?)
- How to handle edge cases (strong single source like SEC filing)
- Confidence adjustment for weak evidence

**Suggestion:** Define evidence independence criteria; add `evidence_strength` enum (strong/moderate/weak).

#### MINOR: Gold Set Sampling Strategy
**Issue:** 180-220 companies recommended but no stratification by:
- Company age
- Signal freshness
- Data source coverage

**Suggestion:** Add temporal and source diversity constraints.

### MISSING ELEMENTS

1. **Batch Job Specification**: No mention of nightly investor profile refresh job (like similar_companies_batch.py pattern)

2. **Feature Flagging**: No ENABLE_INVESTOR_MATCHING feature flag for safe rollout

3. **Pipeline Integration Point**: Where in pipeline.py does investor matching execute? After exit_predictor?

4. **API/MCP Exposure**: How does the internal MCP server expose investor matching? New prompt? New tool?

---

## Blocking Issues Summary

| Issue | Severity | Status |
|-------|----------|--------|
| Schema integration with existing migrations | BLOCKING | Unresolved |
| Global baseline computation undefined | BLOCKING | Unresolved |

## Critique for Next Iteration

Codex should address:

1. **Show explicit Migration 9** that extends signal_store.py, with FK references to existing tables

2. **Define global_baselines storage** with computation strategy and update frequency

3. **Specify batch job pattern** for investor profile refresh (like similar_companies_batch.py)

4. **Add feature flag and pipeline integration point**

5. **Clarify evidence independence** criteria for explanations
