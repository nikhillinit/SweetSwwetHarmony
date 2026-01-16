# VC Exit Predictor: Optimal Integration Strategy

## Executive Summary

**Recommendation: Build a hybrid VC Exit Predictor that adopts governance principles from the external spec while leveraging Discovery Engine's existing infrastructure.**

The external VC Exit Predictor spec (v2.0) is an excellent design but only 15-20% implemented. The Discovery Engine already has 80% of the required infrastructure (founder scoring, signal velocity, thesis classification, Crunchbase funding data). The optimal path is a **3-phase approach**: (1) MVP heuristic scorer using existing data, (2) investor network + traction tracking, (3) ML model upgrade. This delivers value in 1 week while building toward full capability.

**Primary risk:** No historical exit data for validation. **Mitigation:** Track predictions for 12 months before ML training.

## Key Findings

1. **External spec is governance-complete but code-empty** (Source: Code review agent)
   - Pydantic models, YAML configs, label contracts: 100% complete
   - Ingest, features, labeling, models modules: 0% implemented
   - Estimated 6,600-9,900 LOC to complete externally

2. **Discovery Engine has rich existing infrastructure** (Source: Integration analysis)
   - Founder store with serial founder detection (+0.15 boost available)
   - Signal velocity with 48h momentum tracking (+0.20 boost available)
   - Thesis classifier with consumer category scoring
   - Crunchbase funding_rounds data for investor network

3. **Key gap: Time-series data for growth rates** (Source: Gap analysis)
   - Current: Snapshot metrics only (stars, followers, jobs)
   - Needed: Historical snapshots for MoM growth calculation
   - Workaround: Use signal velocity as growth proxy for MVP

4. **Academic validation supports feature selection** (Source: Perplexity research, 50+ papers)
   - **Strongly validated**: Founder prior exits (1.89x), investor centrality (+2.5pp), patents (2x IPO)
   - **Contradicted**: Human capital > structural capital (reversed at early-stage)
   - **Skip**: Description readability (no peer-reviewed evidence)

5. **Industry platforms converging on agentic RAG** (Source: Platform mapping, 200+ sources)
   - Hebbia's ISD achieves 92% accuracy vs 68% vanilla RAG
   - Rogo uses layered fine-tuned models (GPT-4o + o1-mini + o1)
   - All platforms keep confidence scoring proprietary

## Strategic Options

### Option A: Full Spec Implementation
- **Description**: Implement complete 13-service architecture from external spec
- **Pros**: Production-grade governance, dual-hazard models, full reproducibility
- **Cons**: 6-10 weeks development, requires CT stream infrastructure, over-engineered for current scale
- **Confidence**: LOW (high effort, uncertain ROI)

### Option B: Hybrid Integration (Recommended)
- **Description**: Adopt governance principles + scoring approach, reuse Discovery Engine infrastructure
- **Pros**: 3-week delivery, leverages existing code, validates approach before ML
- **Cons**: Single score vs dual hazard, heuristic vs survival analysis
- **Confidence**: HIGH (balanced effort/value)

### Option C: Minimal MVP
- **Description**: Simple weighted formula using only existing data, no new infrastructure
- **Pros**: 1-week delivery, zero new dependencies
- **Cons**: No investor network, no growth rates, lower accuracy
- **Confidence**: MEDIUM (fast but limited)

## Library Decisions

Reviewed 4 key GitHub libraries cited in spec documentation:

| Library | Decision | Rationale |
|---------|----------|-----------|
| **agentjson** | SKIP | Gemini structured output already handles JSON. Adds Rust dependency for problem we don't have. |
| **pycox** | ADOPT (Phase 3) | DeepHit competing risks model ideal for IPO/acquisition/failure. Pin v0.3.0 due to pre-alpha status. |
| **Splink** | SKIP | Canonical keys already handle deduplication. Probabilistic matching adds false positive risk. |
| **tsfresh** | SKIP | Needs 20-50+ data points. We have 1-10 sparse signals. SignalVelocityTracker already sufficient. |

**Key insight**: Discovery Engine's existing infrastructure (SignalVelocityTracker, canonical keys, Gemini structured output) is already more sophisticated than the external spec's empty modules. Only pycox adds new capability.

## Recommendation

**Implement Option B (Hybrid Integration) in 3 phases:**

### Phase 1: MVP Scorer (Week 1)
Build `utils/exit_predictor.py` using existing data:

```python
@dataclass
class ExitPrediction:
    canonical_key: str

    # Component scores (0-1)
    thesis_fit_score: float      # From thesis_classifications
    founder_score: float         # From founder_store
    traction_score: float        # From social_proof (stars, votes, jobs)
    funding_score: float         # From Crunchbase total_funding_usd
    velocity_score: float        # From signal_velocity
    age_score: float             # From founding_date

    # Outputs
    deal_quality_score: float    # Weighted combination
    percentile_rank: int         # 0-100 among all signals
    exit_probability: float      # Heuristic estimate
    recommendation: str          # source/tracking/hold/pass

class ExitPredictor:
    # Weights based on academic validation (Gompers, Hochberg, NBER)
    WEIGHTS = {
        'founder_prior_exit': 0.25,  # 1.89x odds ratio (Gompers et al.)
        'investor_centrality': 0.20,  # +2.5pp per SD (Hochberg et al.)
        'thesis_fit': 0.20,           # Consumer thesis alignment
        'traction_velocity': 0.15,    # Momentum not levels (Sharchilev)
        'patent_count': 0.10,         # 2x IPO odds (NBER)
        'team_size_optimal': 0.05,    # Inverted-U, optimal ~4 (Tamvada)
        'company_age': 0.05,          # Founding date recency
    }
    # NOTE: Excluded per academic research:
    # - description_readability (no peer-reviewed evidence)
    # - social_sentiment (short-term only, not exit predictor)
    # - education_prestige (Series A+ only, not seed)

    async def predict(self, consolidated: ConsolidatedSignal, ...) -> ExitPrediction:
        # Calculate component scores
        # Weighted combination
        # Percentile ranking
        # Exit probability heuristic
```

**Integration point**: After verification gate in `_process_company()` (pipeline.py:1478)

**Deliverables**:
- `utils/exit_predictor.py` (~300 LOC)
- `exit_predictions` table (migration 7)
- Pipeline integration
- 20+ unit tests

### Phase 2: Investor Network + Snapshots (Weeks 2-3)
Build infrastructure for enhanced scoring:

```python
# utils/investor_network.py
class InvestorNetworkAnalyzer:
    """Build co-investment graph from Crunchbase data."""

    async def build_network(self) -> nx.Graph:
        # Query funding_rounds from Crunchbase signals
        # Create edges between co-investors
        # Calculate eigenvector_centrality

    def score_investors(self, investors: List[str]) -> float:
        # Average centrality of company's investors
        # Normalized to 0-1
```

```sql
-- Migration 7: Snapshot tracking
CREATE TABLE company_snapshots (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    github_stars INTEGER,
    linkedin_followers INTEGER,
    job_posting_count INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_key, snapshot_date)
);

CREATE TABLE investor_network (
    investor_name TEXT PRIMARY KEY,
    eigenvector_centrality REAL,
    co_investment_count INTEGER,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**Deliverables**:
- `utils/investor_network.py` (~200 LOC)
- Weekly snapshot collection job
- `utils/traction_calculator.py` (~150 LOC)
- Enhanced exit predictor with growth rates

### Phase 3: ML Model (Month 2+)
Once 12 months of predictions + outcomes exist:

- **pycox DeepHit** for competing risks (IPO vs acquisition vs failure)
  - Pin version 0.3.0 (pre-alpha, may have API changes)
  - Write wrapper class to isolate from library internals
- **XGBoost LambdaMART** for ranking optimization (NDCG)
- **SHAP + SurvSHAP(t)** for explainability (investor-facing evidence)
- Compare to heuristic baseline via A/B test
- Quarterly retraining pipeline

## Governance Adoption

### Adopt from External Spec
| Component | Implementation |
|-----------|----------------|
| Source Registry | Create `config/source_registry.yml` listing all 12 collectors with usage_mode |
| Label Contracts | Create `config/label_contracts/exit_24m.yml` for anti-tautology |
| Prediction Schema | Extend `ProspectPayload` with exit prediction fields |

### Skip from External Spec
| Component | Reason |
|-----------|--------|
| CT Stream Ingestion | Existing collectors sufficient |
| WARC Artifact Store | SQLite sufficient for scale |
| Commercial Intent Latch | Collectors already filter |
| LLM Entity Resolution | Canonical keys work |
| Dual Hazard Models | Single score sufficient for MVP |

### Source Registry (Simplified)
```yaml
# config/source_registry.yml
sources:
  github:
    usage_mode: trainable
    rate_limit: {requests_per_min: 30}
    pii_risk: low

  crunchbase:
    usage_mode: trainable
    rate_limit: {requests_per_min: 10}
    pii_risk: medium

  linkedin:
    usage_mode: inference_only  # Proxycurl ToS limits training
    rate_limit: {requests_per_min: 5}
    pii_risk: high

  sec_edgar:
    usage_mode: trainable
    rate_limit: {requests_per_min: 10}
    pii_risk: high  # Officer names in Form D
```

### Label Contract (Exit Prediction)
```yaml
# config/label_contracts/exit_24m.yml
contract_id: exit_within_24m
version: "1.0"

defining_signals:
  - crunchbase_acquisition
  - sec_ipo_filing
  - news_exit_announcement

disallowed_feature_families:
  - acquisition_*  # Can't use exit data to predict exit
  - ipo_*

lead_lag:
  enforce_strictly_before_event: true
  buffer_days: 30
```

## Risks & Mitigations

| Risk | Severity | Mitigation | Residual Risk |
|------|----------|------------|---------------|
| No historical exit data for validation | YELLOW | Track predictions 12mo before ML | Accept |
| Investor network cold start | YELLOW | Default to median for unknown investors | Monitor |
| Traction scoring without growth rates | YELLOW | Use velocity as proxy, add snapshots Phase 2 | Accept |
| Over-reliance on thesis fit | YELLOW | Cap thesis_fit contribution at 0.25 | Monitor |
| Heuristic accuracy unknown | RED | Backtest on known Crunchbase exits | Accept |

## Notion Schema Updates

**New properties to add:**

| Property | Type | Source |
|----------|------|--------|
| Deal Quality Score | Number (0-100) | percentile_rank |
| Exit Probability | Number (0-100%) | exit_probability * 100 |
| Exit Timeline | Select | <1yr, 1-2yr, 2-3yr, 3-5yr |
| Investor Quality | Number (0-1) | investor_quality_score |
| Traction Score | Number (0-1) | traction_score |

**Routing impact:**
- `percentile_rank >= 80` → Status "Source" (even if confidence < 0.7)
- `percentile_rank >= 60` → Status "Tracking"
- `exit_probability >= 0.6` → Add to "High Exit Potential" watchlist

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Precision@80th percentile | >50% (Year 1) | % of top 20% predictions that exit |
| Recall | >60% | % of actual exits in top 50% predictions |
| Coverage | 100% | All QUALIFIED signals get exit prediction |
| Routing uplift | 2x | High-exit predictions upgrade HOLD→REVIEW rate |
| Team adoption | >50% | Exit score used in >50% of deal reviews |

## Inspired Architectural Patterns

### From MUVERA-PY: Fixed Dimensional Encodings

**Pattern**: Aggregate multi-vector data into fixed-size representations for fast comparison.

```python
# Phase 2+ enhancement: CompanyFeatureEncoder
class CompanyFeatureEncoder:
    """Encode sparse company features into fixed-dimensional vector."""

    PARTITIONS = ['founder', 'traction', 'investor', 'thesis', 'velocity']

    def encode(self, features: Dict[str, float]) -> np.ndarray:
        # Partition features by category
        # Aggregate within partitions (sum for queries, mean for docs)
        # Concatenate into fixed-size vector
        # Enables O(1) percentile ranking via dot product
        pass
```

**Benefit**: Fast cohort similarity for percentile ranking at scale.

### From Claude Code Skills Training: Retrospective Knowledge Capture

**Pattern**: Document prediction outcomes systematically to build team memory.

```yaml
# config/prediction_retrospectives/2026-Q1.yml
retrospective_id: exit_predictions_2026_q1
period: 2026-01-01 to 2026-03-31

failed_predictions:
  - canonical_key: "domain:stealth-ai.io"
    predicted_rank: 92
    outcome: shutdown
    failure_reason: "Single founder burnout - team_size=1 not penalized"

  - canonical_key: "crunchbase:acme-health"
    predicted_rank: 87
    outcome: acqui-hire
    failure_reason: "Investor centrality high but product-market fit weak"

successful_predictions:
  - canonical_key: "domain:consumer-app.com"
    predicted_rank: 95
    outcome: series_a
    success_factors: "Serial founder + high velocity + strong thesis fit"

weight_adjustments:
  team_size_optimal: 0.05 → 0.08  # Penalize single founders more
  traction_velocity: 0.15 → 0.18  # Velocity more predictive than expected
```

**Benefit**: Model improves with each reviewed outcome; knowledge compounds.

### From Pillar: Investment Lifecycle Integration

**Pattern**: Exit predictor enhances stage transitions, not just scoring.

| Current Status | Exit Percentile | Recommended Action |
|----------------|-----------------|-------------------|
| Tracking | ≥80 | Upgrade to "Source" |
| Tracking | ≥60 | Add to watchlist, schedule check-in |
| Source | ≥90 | Priority outreach |
| Source | <50 | Flag for review (prediction may be wrong) |

**Benefit**: Actionable routing, not just informational scoring.

## Next Steps

| Action | Owner | Timeframe |
|--------|-------|-----------|
| Create `utils/exit_predictor.py` MVP | Developer | Week 1 |
| Add `exit_predictions` table (migration 7) | Developer | Week 1 |
| Wire into pipeline after verification gate | Developer | Week 1 |
| Add Notion properties for exit prediction | Developer | Week 1 |
| Build investor network analyzer | Developer | Week 2 |
| Add snapshot collection job | Developer | Week 2 |
| Backtest on Crunchbase exit data | Developer | Week 3 |
| Deploy to production | Developer | Week 3 |
| 12-month prediction tracking | Ongoing | Month 1-12 |
| ML model training | Developer | Month 13 |

---
*Sources: See findings.md for complete evidence matrix*
*Planning files: task_plan.md, findings.md, progress.md*
