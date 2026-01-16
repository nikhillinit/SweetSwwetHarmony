# Exit Predictor Phase 1 Design

**Date:** 2026-01-15
**Status:** Approved
**Phase:** 1 of 3 (Heuristic MVP)

## Overview

Implement a heuristic-based exit prediction system that scores companies using weighted factors derived from academic research (Gompers, Hochberg, NBER papers). Phase 1 delivers a working MVP with stubbed values for unavailable data sources.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Unavailable features | Stub with defaults | investor_centrality=0.5, patent_count=0 |
| Exit timeline/type | Placeholders | exit_timeline="unknown", exit_type_probabilities={} |
| Evidence schema | Simplified | signal_id, factor, value (no quote field) |
| Percentile ranking | Nightly batch job | Avoids N queries per prediction |
| Integration point | After verification gate | Clean separation, non-blocking |
| Original weights | Keep as-is | Accept constant offset, easier Phase 2 upgrade |

## Data Models

### ExitEvidence

```python
@dataclass
class ExitEvidence:
    """Simplified evidence for MVP - no quote field."""
    signal_id: int
    factor: str      # e.g., "founder_score", "thesis_fit"
    value: float     # The computed score for this factor
```

### ExitPrediction

```python
@dataclass
class ExitPrediction:
    """Exit prediction result for a company."""
    canonical_key: str

    # Component scores (0-1 each)
    thesis_fit: float
    founder_score: float
    traction_score: float
    funding_score: float
    velocity_score: float
    age_score: float
    investor_centrality: float  # Stubbed at 0.5 until Phase 2
    patent_count: float         # Stubbed at 0 until Phase 3

    # Computed outputs
    deal_quality_score: float   # Weighted sum (0-1)
    percentile_rank: Optional[int]  # NULL until nightly batch
    exit_probability: float     # Heuristic (0-1)
    confidence: Literal["high", "medium", "low"]
    recommendation: Literal["source", "tracking", "hold", "pass"]

    # Placeholders for Phase 3
    exit_timeline: str = "unknown"
    exit_type_probabilities: Dict[str, float] = field(default_factory=dict)

    # Evidence trail
    evidence: List[ExitEvidence] = field(default_factory=list)

    # Metadata
    predicted_at: datetime = field(default_factory=datetime.utcnow)
    model_version: str = "heuristic_v1"
```

## ExitPredictor Class

```python
class ExitPredictor:
    WEIGHTS = {
        "founder_prior_exit": 0.25,
        "investor_centrality": 0.20,  # Stubbed at 0.5
        "thesis_fit": 0.20,
        "traction_velocity": 0.15,
        "patent_count": 0.10,         # Stubbed at 0
        "team_size_optimal": 0.05,
        "company_age": 0.05,
    }

    HIGH_CONFIDENCE_THRESHOLD = 0.70
    MEDIUM_CONFIDENCE_THRESHOLD = 0.40

    def __init__(
        self,
        founder_store: Optional[FounderStore] = None,
        velocity_tracker: Optional[SignalVelocityTracker] = None,
        signal_store: Optional[SignalStore] = None,
    ):
        self._founder_store = founder_store
        self._velocity_tracker = velocity_tracker
        self._signal_store = signal_store

    async def predict(
        self,
        consolidated: ConsolidatedSignal,
        thesis_classification: Optional[ThesisClassification] = None,
    ) -> ExitPrediction:
        scores = await self._compute_component_scores(consolidated, thesis_classification)
        deal_quality = self._compute_deal_quality(scores)
        exit_prob = self._compute_exit_probability(deal_quality)
        confidence = self._compute_confidence(scores, deal_quality)
        recommendation = self._compute_recommendation(deal_quality, confidence)
        evidence = self._build_evidence(consolidated, scores)

        return ExitPrediction(
            canonical_key=consolidated.canonical_key,
            **scores,
            deal_quality_score=deal_quality,
            percentile_rank=None,
            exit_probability=exit_prob,
            confidence=confidence,
            recommendation=recommendation,
            evidence=evidence,
        )
```

## Component Score Computation

### _compute_component_scores

```python
async def _compute_component_scores(
    self,
    consolidated: ConsolidatedSignal,
    thesis_classification: Optional[ThesisClassification]
) -> Dict[str, float]:
    # Thesis fit from classification or default
    thesis_fit = thesis_classification.thesis_fit_score if thesis_classification else 0.5

    # Founder score from store
    founder_score = 0.5
    if self._founder_store and consolidated.canonical_key:
        founder_data = await self._founder_store.get_aggregate_founder_score(
            consolidated.canonical_key
        )
        if founder_data:
            founder_score = founder_data

    # Traction from social proof
    traction_score = self._compute_traction_score(consolidated.social_proof)

    # Funding from raw data
    funding_score = self._compute_funding_score(consolidated.merged_raw_data)

    # Velocity from tracker
    velocity_score = 0.5
    if self._velocity_tracker and consolidated.canonical_key:
        velocity = await self._velocity_tracker.get_velocity(consolidated.canonical_key)
        if velocity:
            velocity_score = velocity.momentum_score

    # Age score
    age_score = self._compute_age_score(consolidated.founding_date)

    # Stubbed values
    investor_centrality = 0.5  # Phase 2
    patent_count = 0.0  # Phase 3

    return {
        "thesis_fit": thesis_fit,
        "founder_score": founder_score,
        "traction_score": traction_score,
        "funding_score": funding_score,
        "velocity_score": velocity_score,
        "age_score": age_score,
        "investor_centrality": investor_centrality,
        "patent_count": patent_count,
    }
```

### _compute_traction_score

```python
def _compute_traction_score(self, social_proof: Dict[str, int]) -> float:
    """Log-scale normalization. 1000 stars = 1.0."""
    stars = social_proof.get("stars", 0)
    votes = social_proof.get("votes", 0)
    upvotes = social_proof.get("upvotes", 0)

    # Log scale: log10(1001) / 3 ≈ 1.0
    star_score = min(1.0, math.log10(stars + 1) / 3) if stars > 0 else 0
    vote_score = min(1.0, math.log10(votes + 1) / 2.5) if votes > 0 else 0
    upvote_score = min(1.0, math.log10(upvotes + 1) / 2.5) if upvotes > 0 else 0

    # Take max of available signals, default to 0.3 if no data
    best_score = max(star_score, vote_score, upvote_score)
    return best_score if best_score > 0 else 0.3
```

### _compute_funding_score

```python
def _compute_funding_score(self, raw_data: Dict[str, Any]) -> float:
    """Log-scale: $10M = 1.0."""
    total_funding = raw_data.get("total_funding", 0)
    if not total_funding:
        # Try to extract from nested data
        funding_data = raw_data.get("funding", {})
        total_funding = funding_data.get("total", 0)

    if total_funding <= 0:
        return 0.3  # Default for unknown

    # Log scale: log10(10_000_001) / 7 ≈ 1.0
    return min(1.0, math.log10(total_funding + 1) / 7)
```

### _compute_age_score

```python
def _compute_age_score(self, founding_date: Optional[datetime]) -> float:
    """Inverted U: peak at 2 years, decay after 5."""
    if not founding_date:
        return 0.5  # Default for unknown

    age_days = (datetime.utcnow() - founding_date).days
    age_years = age_days / 365.25

    if age_years < 0.5:
        # Too new - ramp up
        return 0.3 + (age_years / 0.5) * 0.4
    elif age_years <= 2:
        # Sweet spot - peak
        return 0.7 + (age_years - 0.5) / 1.5 * 0.3
    elif age_years <= 5:
        # Gradual decay
        return 1.0 - (age_years - 2) / 3 * 0.3
    else:
        # Old company - lower score
        return max(0.3, 0.7 - (age_years - 5) / 5 * 0.4)
```

## Deal Quality & Recommendations

### _compute_deal_quality

```python
def _compute_deal_quality(self, scores: Dict[str, float]) -> float:
    """Weighted sum of component scores."""
    adjusted_weights = {
        "founder_score": 0.25,
        "thesis_fit": 0.20,
        "investor_centrality": 0.20,
        "traction_score": 0.075,
        "velocity_score": 0.075,
        "patent_count": 0.10,
        "age_score": 0.05,
        "funding_score": 0.05,
    }

    total = sum(
        scores.get(key, 0.5) * weight
        for key, weight in adjusted_weights.items()
    )
    return round(total, 4)
```

### _compute_exit_probability

```python
def _compute_exit_probability(self, deal_quality: float) -> float:
    """Sigmoid-like mapping from deal quality to exit probability."""
    # Simple heuristic: scale deal_quality to reasonable exit probability range
    # Base rate for VC-backed startups is ~10-20%
    # High quality deals might reach 40-50%
    base_rate = 0.15
    max_rate = 0.50

    # Sigmoid-ish curve
    scaled = (deal_quality - 0.5) * 4  # Center at 0.5, spread
    probability = base_rate + (max_rate - base_rate) / (1 + math.exp(-scaled))

    return round(min(max(probability, 0.05), 0.95), 3)
```

### _compute_confidence

```python
def _compute_confidence(
    self,
    scores: Dict[str, float],
    deal_quality: float
) -> Literal["high", "medium", "low"]:
    """Confidence based on data completeness and score clarity."""
    # Count non-default scores (not 0.5 or 0.3)
    defaults = {0.5, 0.3, 0.0}
    non_default_count = sum(
        1 for v in scores.values()
        if v not in defaults
    )

    # Score clarity: distance from 0.5
    clarity = abs(deal_quality - 0.5)

    if non_default_count >= 5 and clarity >= 0.15:
        return "high"
    elif non_default_count >= 3 or clarity >= 0.10:
        return "medium"
    else:
        return "low"
```

### _compute_recommendation

```python
def _compute_recommendation(
    self,
    deal_quality: float,
    confidence: Literal["high", "medium", "low"]
) -> Literal["source", "tracking", "hold", "pass"]:
    """Map deal quality and confidence to recommendation."""
    if deal_quality >= 0.70 and confidence in ("high", "medium"):
        return "source"
    elif deal_quality >= 0.50:
        return "tracking"
    elif deal_quality >= 0.30:
        return "hold"
    else:
        return "pass"
```

## Database Schema

### Migration 7

```sql
CREATE TABLE IF NOT EXISTS exit_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT NOT NULL,

    -- Component scores
    thesis_fit REAL NOT NULL,
    founder_score REAL NOT NULL,
    traction_score REAL NOT NULL,
    funding_score REAL NOT NULL,
    velocity_score REAL NOT NULL,
    age_score REAL NOT NULL,
    investor_centrality REAL NOT NULL,
    patent_count REAL NOT NULL,

    -- Computed outputs
    deal_quality_score REAL NOT NULL,
    percentile_rank INTEGER,  -- NULL until batch
    exit_probability REAL NOT NULL,
    confidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,

    -- Placeholders
    exit_timeline TEXT DEFAULT 'unknown',
    exit_type_probabilities TEXT,  -- JSON

    -- Evidence
    evidence TEXT,  -- JSON

    -- Metadata
    model_version TEXT NOT NULL,
    predicted_at TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(canonical_key)
);

CREATE INDEX IF NOT EXISTS idx_exit_predictions_deal_quality
ON exit_predictions(deal_quality_score DESC);

CREATE INDEX IF NOT EXISTS idx_exit_predictions_recommendation
ON exit_predictions(recommendation);
```

## Batch Percentile Job

```python
class ExitPredictorBatch:
    """Nightly batch job for percentile computation."""

    def __init__(self, signal_store: SignalStore):
        self._signal_store = signal_store

    async def compute_percentiles(self) -> int:
        """Compute percentile ranks for all predictions."""
        # Get all predictions ordered by deal_quality_score DESC
        predictions = await self._signal_store.get_all_exit_predictions(
            order_by="deal_quality_score DESC"
        )

        total = len(predictions)
        if total == 0:
            return 0

        updated = 0
        for position, pred in enumerate(predictions):
            # Percentile: top 1% = 99, bottom 1% = 1
            percentile = int((1 - position / total) * 100)
            percentile = max(1, min(99, percentile))

            await self._signal_store.update_exit_prediction_percentile(
                canonical_key=pred.canonical_key,
                percentile_rank=percentile,
            )
            updated += 1

        return updated
```

## Pipeline Integration

### Location

In `workflows/pipeline.py`, after verification gate (~line 1478), before Notion push:

```python
# After verification gate passes, before Notion push
if verification_result.passes_gate:
    # NEW: Exit prediction
    if self._exit_predictor:
        try:
            prediction = await self._exit_predictor.predict(
                consolidated=consolidated_signal,
                thesis_classification=thesis_result,
            )
            # Store prediction
            await self._signal_store.store_exit_prediction(prediction)
            # Attach to prospect payload for Notion
            prospect_payload.exit_prediction = prediction
            prospect_payload.deal_quality_score = prediction.deal_quality_score
            self._metrics["exit_predictions_computed"] += 1
        except Exception as e:
            logger.warning(f"Exit prediction failed for {canonical_key}: {e}")
            # Non-blocking - continue without prediction

    # Existing: Push to Notion
    await self._push_to_notion(prospect_payload)
```

### Feature Flag

```python
# In DiscoveryPipeline.__init__
self._exit_predictor = None
if os.getenv("ENABLE_EXIT_PREDICTOR", "false").lower() == "true":
    self._exit_predictor = ExitPredictor(
        founder_store=self._founder_store,
        velocity_tracker=self._velocity_tracker,
        signal_store=self._signal_store,
    )
```

## Testing Strategy

### Unit Tests (~25 tests)

```
tests/test_exit_predictor.py
- test_compute_traction_score_with_stars()
- test_compute_traction_score_with_votes()
- test_compute_traction_score_no_data_returns_default()
- test_compute_funding_score_log_scale()
- test_compute_age_score_peak_at_2_years()
- test_compute_age_score_decay_after_5_years()
- test_compute_deal_quality_weighted_sum()
- test_compute_confidence_high_when_complete()
- test_compute_confidence_low_when_sparse()
- test_recommendation_source_when_high_quality()
- test_recommendation_tracking_when_medium()
- test_recommendation_hold_when_low()
- test_recommendation_pass_when_very_low()
- test_stubbed_investor_centrality_is_0_5()
- test_stubbed_patent_count_is_0()
- test_evidence_built_correctly()
```

### Integration Tests (~10 tests)

```
tests/test_exit_predictor_integration.py
- test_predict_with_real_founder_store()
- test_predict_with_real_velocity_tracker()
- test_predict_stores_to_database()
- test_batch_percentile_computation()
- test_pipeline_integration_non_blocking()
```

### Database Tests (~8 tests)

```
tests/test_exit_predictions_storage.py
- test_migration_7_creates_table()
- test_store_exit_prediction()
- test_get_exit_prediction_by_canonical_key()
- test_update_percentile_rank()
- test_unique_constraint_on_canonical_key()
```

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `utils/exit_predictor.py` | Create | ExitPredictor class, data models |
| `utils/exit_predictor_batch.py` | Create | Batch percentile computation |
| `storage/migrations.py` | Modify | Add migration 7 |
| `storage/signal_store.py` | Modify | Add exit prediction storage methods |
| `workflows/pipeline.py` | Modify | Integrate ExitPredictor |
| `tests/test_exit_predictor.py` | Create | Unit tests |
| `tests/test_exit_predictor_integration.py` | Create | Integration tests |
| `tests/test_exit_predictions_storage.py` | Create | Database tests |

## Phase 2 Upgrade Path

When investor network data becomes available:
1. Replace `investor_centrality = 0.5` stub with real computation
2. Add investor graph analysis module
3. Update weights if needed based on backtesting

## Phase 3 Upgrade Path

When ML model is trained:
1. Replace heuristic with model inference
2. Populate exit_timeline and exit_type_probabilities
3. Add patent_count from USPTO collector
4. A/B test heuristic vs ML for confidence calibration
