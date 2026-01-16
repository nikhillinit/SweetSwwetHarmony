# Research Findings: VC Exit Predictor Integration

## Sources Reviewed
| Source | Type | Credibility | Key Insight |
|--------|------|-------------|-------------|
| VC_Exit_Predictor_Engineering_Spec_v2.0_FROZEN.md | External | HIGH | Dual-hazard model (traction 0-24m, liquidity 2-10yr) |
| vc_exit_predictor_full_bundle (source code) | External | HIGH | 15-20% implemented - governance complete, core empty |
| Discovery Engine codebase | Internal | HIGH | Rich existing infrastructure (founder, velocity, thesis) |
| Gap Analysis doc | External | MEDIUM | Identifies missing components vs production system |
| PitchBook approach (referenced) | External | HIGH | 34 features, 67.8% accuracy benchmark |
| sionic-ai/muvera-py | GitHub | HIGH | FDE for multi-vector encoding (8.5x speedup) |
| sionic-ai/claude-code-skills-training | HuggingFace | HIGH | Team memory: `/advise` + `/retrospective` pattern |
| ahmnouira/pillar-landing | GitHub | MEDIUM | Investment lifecycle stages (Discovery → Execution) |
| povc_ssh_integration_analysis.md | Internal | HIGH | Multi-agent LLM (3 experts + manager), weight generator |
| VCGraphBuilder Implementation Plan.md | Internal | HIGH | ETL pipeline: EntityResolver + RelationshipParsers |
| VC Investment Graph Schema.md | Internal | HIGH | 4-table graph schema for entity-centric architecture |
| base.py (BaseCollector) | Internal | HIGH | Retry strategy, rate limiting patterns |
| signal_store.py (migrations) | Internal | HIGH | thesis_classifications, collector_metrics tables |
| Implementation Guide: LLMClassifier.classify | Internal | HIGH | Self-correction loop, Pydantic validation, graceful degradation |
| Specification: LLMClassifier Output Format | Internal | HIGH | 3-layer validation (prompt, JSON schema, Pydantic), evidence schema |
| Specification: CompanyClassifierService | Internal | HIGH | Signal bundling, citation-backed classifications, CLI-driven |

## Evidence Matrix
| Claim | Supporting Sources | Contradicting Sources | Confidence |
|-------|-------------------|----------------------|------------|
| Governance-first design is production-ready | Spec v2.0, full_bundle code review | None | HIGH |
| Core modules (ingest, features, labeling, models) are empty stubs | Code review agent | None | HIGH |
| Discovery Engine has 80%+ of needed data | Integration analysis | Missing: time-series snapshots | HIGH |
| Heuristic scoring viable before ML | PitchBook approach, spec analysis | Spec recommends survival analysis | MEDIUM |
| Integration point is after verification gate | Integration analysis | Could also be parallel | HIGH |

## Quantitative Data
| Metric | Value | Source | Date |
|--------|-------|--------|------|
| External spec implementation | 15-20% | Code review agent | 2026-01-15 |
| Discovery Engine collectors | 12 sources | Integration analysis | 2026-01-15 |
| Existing founder scoring boost | +0.15 max | verification_gate_v2.py | 2026-01-15 |
| Existing velocity boost | +0.20 max | signal_velocity.py | 2026-01-15 |
| PitchBook feature count | 34 features | Spec research | 2026-01-15 |
| PitchBook accuracy target | 67.8% precision@80th | Spec research | 2026-01-15 |
| Estimated LOC for full external spec | 6,600-9,900 | Code review agent | 2026-01-15 |

## Contradictions Found

### 1. Governance Complexity vs Speed
**Spec:** Full 13-service architecture with CT stream, WARC storage, entity resolution
**Discovery Engine:** Simple SQLite storage, direct collector → pipeline flow
**Resolution:** Adopt governance principles (source registry, label contracts) but skip infrastructure (WARC, CT stream). Use existing collectors.

### 2. Dual-Hazard Model vs Single Score
**Spec:** Separate Hazard A (traction) and Hazard B (liquidity) with survival analysis
**Discovery Engine:** Single confidence score with routing thresholds
**Resolution:** MVP uses single "deal quality score" combining factors. Phase 2 can add separate hazard models.

### 3. CT Stream as Primary Discovery vs Existing Collectors
**Spec:** Certificate Transparency stream for .ai/.io domain discovery
**Discovery Engine:** GitHub, Companies House, SEC, Crunchbase, LinkedIn collectors
**Resolution:** Skip CT stream entirely. Existing collectors provide sufficient deal flow for consumer thesis.

### 4. Platform Sinkhole Enforcement
**Spec:** Stop-at-redirect for LinkedIn, Medium, Notion
**Discovery Engine:** LinkedIn collector uses Proxycurl API (ToS-compliant)
**Resolution:** Proxycurl handles ToS compliance. No sinkhole needed for API-based access.

## Gap Analysis: Spec vs Discovery Engine

### What Spec Has That We Need
| Component | Spec Status | Discovery Engine Status | Action |
|-----------|-------------|------------------------|--------|
| Source Registry (governance) | Complete YAML | Not formalized | ADOPT: Create simplified source_registry.yml |
| Label Contracts (anti-tautology) | Complete YAML | Not implemented | ADOPT: Critical for ML training later |
| Feature Registry | Complete YAML | Not formalized | DEFER: Add when ML training begins |
| Canonical Company Schema | JSON Schema | ConsolidatedSignal dataclass | KEEP: Existing approach sufficient |
| Prediction Output Schema | JSON Schema | ProspectPayload dataclass | EXTEND: Add exit prediction fields |

### What Spec Has That We Don't Need
| Component | Reason to Skip |
|-----------|----------------|
| CT Stream Ingestion | Existing collectors sufficient |
| CZDS Zone Files | Not relevant for consumer thesis |
| Commercial Intent Latch | Collectors already filter |
| WARC Artifact Store | SQLite sufficient for current scale |
| Platform Sinkhole | Proxycurl handles ToS |
| LLM Entity Resolution | Canonical keys already work |

### What Discovery Engine Has That Spec Lacks
| Component | Value Add |
|-----------|-----------|
| Thesis Matcher (consumer keywords) | Pre-filters irrelevant signals |
| LLM Classifier (Gemini) | Semantic thesis fit scoring |
| Signal Consolidator | Multi-source merging done |
| Verification Gate (anti-inflation) | Prevents score gaming |
| Notion Integration (durable outbox) | Production-ready CRM sync |
| Founder Store | Serial founder detection |
| Signal Velocity | 48h momentum tracking |

## Framework Analysis: Weighted Scoring Approach

### Feature Weights (Proposed)
Based on PitchBook research + Discovery Engine capabilities:

| Feature Category | Weight | Availability | Notes |
|------------------|--------|--------------|-------|
| Thesis Fit | 0.25 | ✅ Available | thesis_classifications table |
| Founder Quality | 0.25 | ✅ Available | founder_store, serial founder |
| Traction Signals | 0.20 | ⚠️ Partial | Stars, votes, jobs (no growth rates) |
| Investor Quality | 0.15 | ⚠️ Buildable | Crunchbase funding_rounds data exists |
| Signal Velocity | 0.10 | ✅ Available | signal_velocity module |
| Company Age | 0.05 | ✅ Available | founding_date from consolidation |

### Scoring Formula (Heuristic MVP)
```python
deal_quality = (
    thesis_fit_score * 0.25 +
    founder_score * 0.25 +
    traction_score * 0.20 +
    investor_quality_score * 0.15 +
    velocity_score * 0.10 +
    age_score * 0.05
)

# Normalize to 0-1, then rank as percentile
percentile_rank = rank_among_cohort(deal_quality) * 100
```

### Exit Probability Heuristic
```python
# Based on thesis fit + traction
if deal_quality >= 0.8:
    exit_probability = 0.7 + (deal_quality - 0.8) * 1.5  # 70-100%
elif deal_quality >= 0.5:
    exit_probability = 0.3 + (deal_quality - 0.5) * 1.33  # 30-70%
else:
    exit_probability = deal_quality * 0.6  # 0-30%
```

## Open Questions
- [x] Which governance components to adopt? → Source registry + label contracts
- [x] Single score vs dual hazard? → Single score MVP
- [x] Where in pipeline? → After verification gate
- [ ] How to validate predictions? → Need 12-month outcome tracking
- [ ] Retrain cadence? → Quarterly once ML model added

---

## GitHub Library Assessments

### Libraries Reviewed (from sigridjineth repos + RESOURCES.md)

| Library | Purpose | Recommendation | Rationale |
|---------|---------|----------------|-----------|
| **agentjson** | LLM JSON repair | DO NOT ADOPT | Gemini's `response_mime_type="application/json"` already produces valid JSON 99%+. Adds Rust dependency for problem we don't have. |
| **pycox** | Survival analysis | ADOPT (Phase 3) | DeepHit handles competing risks (IPO/acquisition/failure) natively. Pre-alpha status, so pin version 0.3.0. |
| **Splink** | Entity resolution | DO NOT ADOPT | Discovery Engine's canonical keys already handle 95%+ of deduplication. Probabilistic matching adds false positive risk. |
| **tsfresh** | Time-series features | DO NOT ADOPT | Designed for dense time-series (100+ data points). Discovery Engine has sparse signals (1-10 per company). SignalVelocityTracker already sufficient. |

### Detailed Library Analysis

#### agentjson (sigridjineth, 87 stars)
- **What it does**: Rust-powered JSON repair with Python bindings, fixes trailing commas, unquoted keys, Python literals
- **Why skip**: Discovery Engine's LLM classifier already uses Gemini with structured output mode (`response_mime_type="application/json"`). Parse failures return safe defaults. Repairing corrupted output risks semantic errors.
- **Alternative**: Simple regex for trailing comma removal if needed

#### pycox (havakv, 800+ stars)
- **What it does**: PyTorch survival analysis with DeepHit, DeepSurv, CoxTime, MTLR
- **Why adopt**: DeepHit's competing risks architecture directly maps to IPO/acquisition/failure prediction. Network outputs `[batch x num_risks x num_durations]` tensors.
- **Caveats**: Pre-alpha status (v0.3.0). Pin version. Write wrapper classes to isolate from API changes.
- **When**: Phase 3 (ML model upgrade) after 12 months of prediction data

#### Splink (UK Ministry of Justice, 1,200+ stars)
- **What it does**: Probabilistic record linkage using Fellegi-Sunter model with EM training
- **Why skip**: Discovery Engine already has authoritative identifiers (domains, Companies House numbers, Crunchbase IDs). Canonical keys handle deduplication deterministically. `name_loc` fallback correctly flags weak matches for human review.
- **When to reconsider**: If `name_loc` volume exceeds 100K signals/month

#### tsfresh (blue-yonder, 9.1K stars)
- **What it does**: Extracts 794+ features from time-series data with hypothesis testing feature selection
- **Why skip**: Requires 20-50+ data points per series. Discovery Engine has 1-10 sparse signals per company. SignalVelocityTracker already implements burst detection, convergence, acceleration with domain-appropriate logic.
- **When to reconsider**: If continuous data sources added (daily headcount, monthly revenue)

### Libraries to Consider for Future Phases

| Library | Purpose | When to Adopt |
|---------|---------|---------------|
| **sentence-transformers** | Text embeddings for company descriptions | Phase 2 (investor network) |
| **SHAP** | Model explainability | Phase 3 (ML model) |
| **networkx** | Investor co-investment graph | Phase 2 (investor quality) |
| **XGBoost/LightGBM** | LambdaMART ranking | Phase 3 (ML model) |

### Key Insight from Library Analysis

The Discovery Engine's existing infrastructure is **more sophisticated than the external spec's empty modules**:
- SignalVelocityTracker > tsfresh for sparse event data
- Canonical keys > Splink for deterministic deduplication
- Gemini structured output > agentjson for JSON parsing
- Only pycox offers capability not already present (survival analysis)

---

## Academic Validation of Exit Prediction Claims

### Source: Perplexity Research Report (50+ peer-reviewed papers)

| Claim | Validation | Effect Size | Key Citation |
|-------|------------|-------------|--------------|
| **Prior founder exits predict success** | STRONGLY SUPPORTED | 1.89x odds ratio | Gompers et al. (Management Science, 2010) |
| **Investor network centrality matters** | STRONGLY SUPPORTED | +2.5pp exit rate per SD | Hochberg, Ljungqvist & Lu (J. Finance, 2007) |
| **Patent quality predicts IPO** | STRONGLY SUPPORTED | 2x IPO odds with grant | Koning, Mueller & Ziedonis (NBER w23268) |
| **Human capital > structural capital** | CONTRADICTED | Structural capital dominates early-stage | Canfield et al. (2024) |
| **Team heterogeneity helps** | MIXED | Inverted-U, optimal team ~4 | Tamvada & Shrivastava (2011) |
| **Education prestige predicts** | PARTIALLY SUPPORTED | Series A+ only, not seed | Molinas (2023) |
| **Web traffic predicts success** | OUTDATED | Alexa discontinued Jan 2022 | Sharchilev et al. (KDD 2018) |
| **Social sentiment predicts survival** | SHORT-TERM ONLY | 1-2yr survival, not 5yr exit | Antretter et al. (JBV Insights, 2019) |
| **Description readability matters** | NOT VALIDATED | No peer-reviewed evidence | - |

### Methodology Recommendations (Academic Consensus)

1. **Discrete-Time Hazard > Cox PH**: Cox assumes constant hazard ratios - violated in startups where mortality peaks months 6-24. Use MTLR.

2. **Velocity > Levels**: Use `(signal_t1 - signal_t0) / signal_t0` not absolute values. Sharchilev uses 6-month windows.

3. **Node2Vec > Simple Centrality**: Huang (2025, Princeton) shows betweenness centrality has NEGATIVE effect. Embeddings capture richer structure.

4. **SBERT > VADER/LIWC**: Dictionary-based sentiment weak for startup language. Use `all-MiniLM-L6-v2` (384-dim).

5. **Leakage Prevention**:
   - Patent citations: Grant date + 24 months minimum
   - Funding round size: Exclude Series A+ (outcome proxies)
   - Web mentions: First-year only

### Validated Feature Weights (Research-Backed)

| Feature | Weight | Evidence | Risk |
|---------|--------|----------|------|
| `founder_prior_exit` | HIGH | 1.89x odds (Gompers) | Survivorship bias |
| `investor_centrality_degree` | HIGH | +2.5pp per SD (Hochberg) | Endogeneity |
| `patent_count` + `forward_cites` | HIGH | 2x IPO (NBER) | 18mo lag required |
| `github_stars_velocity` | MEDIUM | Sharchilev top-10 | Fake stars risk |
| `team_size` | MEDIUM | Optimal ~4 (Tamvada) | Inverted-U |
| `founder_education_elite` | LOW | Series A+ only | Time decay |
| `social_sentiment` | LOW | Short-term only | Ephemeral |
| `description_readability` | SKIP | Not validated | Confounding |

### Free Data Sources (Validated)

| Category | Source | Access | Quality |
|----------|--------|--------|---------|
| Founder background | OpenCorporates, GitHub, ORCID | API (free) | Good |
| Web traction | GitHub stars, domain age, Google Trends | API (free) | Good |
| Patents | NBER, USPTO, Lens.org | Bulk download | Excellent |
| News/PR | GDELT, Common Crawl | BigQuery | Good |
| Funding | SEC EDGAR | FTP | Excellent (public only) |

---

## GenAI Platform Competitive Intelligence

### Source: Perplexity Platform Mapping (200+ sources)

| Platform | Architecture | Key Innovation | Accuracy |
|----------|--------------|----------------|----------|
| **Hebbia** | Iterative Source Decomposition (ISD) | Agent-based document traversal | 92% (vs 68% vanilla RAG) |
| **Rogo** | Layered fine-tuned models | GPT-4o chat + o1-mini structure + o1 reasoning | Not disclosed |
| **Crustdata** | Waterfall enrichment | DB → crawlers → tech detection → signals | 80-85% match rate |
| **AlphaSense** | Domain-specific NLP | 500M+ docs, Canalyst models | Not disclosed |
| **Brightwave** | Deep research | Entailment-based fact verification | Not disclosed |

### Architectural Patterns to Adopt

1. **Agentic RAG**: All platforms converging on multi-agent orchestration
2. **Domain-Specific Fine-Tuning**: Rogo, AlphaSense show custom training on financial data is competitive moat
3. **Velocity Features**: Crustdata emphasizes real-time signal changes over absolute levels
4. **Entailment Verification**: Brightwave's approach to fact-checking LLM outputs

### Gaps Identified (Industry-Wide)

- No platform discloses hallucination rates
- Confidence scoring methodologies are proprietary
- Evaluation benchmarks largely absent
- No adversarial testing disclosures

### Implication for Discovery Engine

The Discovery Engine's exit predictor should:
1. Use **discrete-time hazard models** (MTLR via pycox)
2. Compute **velocity/momentum** not absolute levels
3. Build **investor network embeddings** (Node2Vec, not betweenness)
4. Leverage **validated features** (founder exit, investor centrality, patents)
5. Skip **unvalidated features** (description readability, social sentiment for exits)

---

## Additional Inspiration Sources

### Source: sionic-ai/muvera-py (Multi-Vector Retrieval)

**Key Pattern: Fixed Dimensional Encodings (FDE)**
- Transforms complex multi-vector representations into efficient single-vector encodings
- Uses LSH (SimHash) space partitioning + vector aggregation
- Performance: Query time 1618ms → 190ms (8.5x speedup)
- Mathematical insight: Dot product between FDE vectors approximates Chamfer similarity

**Applicability to Exit Predictor:**
| Application | How It Applies |
|-------------|----------------|
| Investor network embeddings | Compress Node2Vec vectors into fixed dimensions for fast similarity |
| Company feature vectors | Aggregate sparse features into dense representation |
| Cohort similarity search | Find similar companies for percentile ranking |

**Architecture Pattern to Adopt:**
```python
# FDE-inspired aggregation for company features
class CompanyFeatureEncoder:
    def encode(self, features: Dict[str, float]) -> np.ndarray:
        # Partition features by category (founder, traction, investor)
        # Aggregate within partitions
        # Concatenate into fixed-size vector
        pass
```

### Source: sionic-ai/claude-code-skills-training (Team Memory)

**Key Pattern: Experiment Knowledge Capture**
- `/advise` before work → search for relevant past experiments
- `/retrospective` after work → auto-generate skill from conversation

**Skill Structure Best Practices:**
```
plugins/experiment-name/
├── .claude-plugin/plugin.json    # Trigger conditions
├── skills/SKILL.md               # Main knowledge doc
├── references/
│   ├── experiment-log.md         # Daily notes
│   └── troubleshooting.md        # Error → solution mappings
└── scripts/                      # Reusable code
```

**Critical Insight: "Failures are most valuable"**
- Document what didn't work and why
- Failed attempts table gets referenced most
- Knowledge preserves across team turnover

**Applicability to Exit Predictor:**
| Application | How It Applies |
|-------------|----------------|
| Prediction retrospectives | After 12mo, capture which features worked/failed |
| Model experiment log | Track hyperparameter sweeps, feature selection |
| Error → solution mappings | Document data quality issues, edge cases |

**What Makes a Good Exit Predictor Skill:**
1. **Specific trigger conditions**: "Consumer thesis company, seed stage, US/UK"
2. **Failed predictions table**: What signals looked good but failed
3. **Copy-paste configurations**: Exact feature weights, thresholds

### Source: ahmnouira/pillar-landing (CRE Investment Platform)

**Key Pattern: Investment Lifecycle Workflow**
- Discovery → Diligence → Execution → Management

**Parallel to Discovery Engine:**
| Pillar Stage | Discovery Engine Stage | Exit Predictor Role |
|--------------|------------------------|---------------------|
| Discovery | Signal Collection | Identify high-potential signals |
| Diligence | Verification Gate | Exit probability scoring |
| Execution | Notion Push | Prioritize deals by exit potential |
| Management | Tracking Status | Monitor prediction accuracy |

**Architectural Insight:**
- Professional investment software uses typed utilities and domain models
- Component-based architecture with clear separation
- TypeScript for type safety (parallels our Pydantic models)

---

## Synthesis: Cross-Source Patterns

### Pattern 1: Aggregate → Encode → Compare
From MUVERA-PY: Don't compare raw multi-dimensional data. Aggregate into fixed representations first.
- **Apply to**: Company feature vectors, investor networks, traction signals
- **Benefit**: Fast percentile ranking at scale

### Pattern 2: Retrospective Knowledge Capture
From Skills Training: Document failures during the experiment, not after.
- **Apply to**: Monthly exit prediction review
- **Benefit**: Continuous model improvement

### Pattern 3: Investment Lifecycle Stages
From Pillar: Clear stage gates with defined criteria.
- **Apply to**: Signal → Tracking → Source → Funded lifecycle
- **Benefit**: Exit predictor enhances stage transitions

### Pattern 4: Team Memory Over Individual Knowledge
From Skills Training: Make contribution frictionless so knowledge compounds.
- **Apply to**: Exit prediction calibration data
- **Benefit**: Predictions improve with each reviewed outcome

---

## VC Investment Graph Architecture (from Internal Docs)

### Source: povc_ssh_integration_analysis.md

**Three-Pillar Prediction System:**
| Pillar | Purpose | Relevance to Exit Predictor |
|--------|---------|----------------------------|
| Path Selector | Sample informative paths on VC graph | Find paths to successful exits |
| Weight Generator | Learn per-sample weights for agent fusion | Adaptive feature weighting |
| Inference Pipeline | Aggregate 3 agents → Manager Agent | Ensemble exit predictions |

**Multi-Agent LLM Architecture:**
```python
class MultiAgentLLM:
    def __init__(self):
        self.technical_agent = TechnicalAgent()   # Tech stack, GitHub activity
        self.market_agent = MarketAgent()         # Market size, PMF
        self.network_agent = NetworkAgent()       # Investor quality, graph centrality
        self.manager_agent = ManagerAgent()       # Final decision
        self.weight_generator = WeightGenerator() # Learned weights

    def predict(self, company_data):
        analyses = [
            self.technical_agent.analyze(company_data),
            self.market_agent.analyze(company_data),
            self.network_agent.analyze(company_data['graph_context'])
        ]
        weights = self.weight_generator.get_weights(company_data)
        return self.manager_agent.decide(analyses, weights)
```

**Integration Roadmap (12 weeks):**
| Phase | Weeks | Deliverable |
|-------|-------|-------------|
| Foundation | 1-4 | VC Graph + graph-based features |
| Multi-Agent | 5-8 | 3 agents + weight generator |
| Advanced | 9-12 | Path selector + similar-company analysis |

### Source: VCGraphBuilder Service Implementation Plan

**ETL Pipeline Architecture:**
```
SignalStore → VCGraphBuilder → EntityResolver → RelationshipParsers → Graph DB
```

**Key Components:**
1. **VCGraphBuilder (Orchestrator)**: Fetches signals, delegates to parsers
2. **EntityResolver (Stateful)**: Alias cache for O(1) entity lookup
3. **RelationshipParsers (Stateless)**: One per signal_type (Crunchbase, SEC EDGAR)

**Entity Resolution Pattern:**
```python
class EntityResolver:
    async def get_or_create_entity(self, aliases, entity_type, canonical_name, properties):
        # 1. Check cache for any matching alias
        for alias_type, alias_value in aliases.items():
            cache_key = f"{alias_type}:{alias_value}"
            if cache_key in self._alias_cache:
                return await self.store.get_graph_entity(self._alias_cache[cache_key])

        # 2. Create new entity + aliases if not found
        new_entity = await self.store.create_graph_entity(...)
        for alias_type, alias_value in aliases.items():
            await self.store.add_entity_alias(...)
            self._alias_cache[f"{alias_type}:{alias_value}"] = new_entity.id
        return new_entity
```

### Source: VC Investment Graph Schema

**Four-Table Schema:**
```sql
-- Canonical nodes (companies, people, investors)
CREATE TABLE graph_entities (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,  -- e.g., "domain:acme.com"
    entity_type TEXT NOT NULL,           -- 'company', 'person', 'investor_firm'
    canonical_name TEXT NOT NULL,
    properties TEXT,                     -- JSON
    created_at TEXT, updated_at TEXT
);

-- Alias → Entity mapping for entity resolution
CREATE TABLE entity_aliases (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,            -- 'crunchbase_name', 'domain', 'manual'
    UNIQUE(alias, alias_type)
);

-- Typed, directed, time-stamped edges
CREATE TABLE graph_relationships (
    id INTEGER PRIMARY KEY,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    relationship_type TEXT NOT NULL,     -- 'invested_in', 'founded', 'acquired'
    effective_date TEXT,                 -- When relationship started
    observed_date TEXT NOT NULL,         -- When we saw it
    properties TEXT                      -- JSON (amount, round, etc.)
);

-- Signal traceability
CREATE TABLE relationship_sources (
    relationship_id INTEGER NOT NULL,
    signal_id INTEGER NOT NULL,
    UNIQUE(relationship_id, signal_id)
);
```

**Query Examples:**
```sql
-- Find all investors in a company
SELECT i.* FROM graph_entities c
JOIN graph_relationships r ON c.id = r.target_entity_id
JOIN graph_entities i ON r.source_entity_id = i.id
WHERE c.canonical_key = 'domain:acme.com'
  AND r.relationship_type = 'invested_in';

-- Compute investor quality (exit rate)
SELECT i.canonical_name,
       COUNT(CASE WHEN r2.relationship_type = 'acquired' THEN 1 END) as exits,
       COUNT(*) as total_investments
FROM graph_entities i
JOIN graph_relationships r ON i.id = r.source_entity_id
LEFT JOIN graph_relationships r2 ON r.target_entity_id = r2.target_entity_id
WHERE i.entity_type = 'investor_firm'
GROUP BY i.id;
```

### Synthesis: Graph Architecture for Exit Predictor

**Phase 2 Enhancement - Investor Network:**
1. Implement `graph_entities` + `graph_relationships` schema (migration 7)
2. Build `VCGraphBuilder` ETL from existing Crunchbase signals
3. Compute investor centrality using NetworkX PageRank
4. Add `investor_quality_score` to exit prediction

**Phase 3 Enhancement - Multi-Agent:**
1. Implement Technical, Market, Network agents
2. Train Weight Generator on historical predictions
3. Use Manager Agent for final exit probability

**Key Integration Points:**
| Current Component | Graph Enhancement |
|-------------------|-------------------|
| `founder_store` | Link to `graph_entities` (person type) |
| `thesis_classifications` | Feed Market Agent |
| `signal_velocity` | Feed Technical Agent |
| Crunchbase collector | Populate `graph_relationships` |

---

## LLM Classification Specifications (from Internal Docs)

### Source: Implementation Guide - LLMClassifier.classify Method

**Self-Correction Loop Pattern:**
```python
async def classify(self, signals: List[Signal]) -> ThesisClassification:
    # Stage 1: Initial API call
    response = await self._call_gemini_api(signals)

    # Stage 2: Initial validation
    result = self._validate_response(response)
    if result.success:
        return self._create_success_response(result.data)

    # Stage 3: Self-correction attempt
    corrected = await self._attempt_self_correction(response, result.error)

    # Stage 4: Final validation
    final_result = self._validate_response(corrected)
    if final_result.success:
        return self._create_success_response(final_result.data, was_corrected=True)

    # Stage 5: Graceful failure
    return self._handle_final_error(signals, final_result.error)
```

**Key Patterns:**
| Pattern | Description | Exit Predictor Application |
|---------|-------------|---------------------------|
| Self-correction loop | LLM fixes own malformed JSON | Apply to exit prediction prompts |
| Graceful degradation | Failures return structured objects, not exceptions | `ExitPrediction` with `prediction_failed=True` |
| Pydantic validation | Schema enforcement with constraints | `exit_probability` bounded 0.0-1.0 |
| Correction metadata | Track `was_corrected` flag | Monitor prediction reliability |

### Source: Specification - LLMClassifier Output Format

**Three-Layer Validation System:**
```
Layer 1: Prompt Engineering → Explicit JSON format instructions
Layer 2: JSON Schema → Language-agnostic contract
Layer 3: Pydantic Models → Runtime type safety (final gatekeeper)
```

**ThesisClassification Schema:**
```python
class Evidence(BaseModel):
    signal_id: int
    source_name: str
    claim: str = Field(min_length=1)
    quote: str = Field(min_length=1)

class ThesisClassificationOutput(BaseModel):
    thesis_match: bool
    thesis_fit_score: float = Field(ge=0.0, le=1.0)
    category: Literal['consumer_cpg', 'consumer_health_tech',
                      'travel_hospitality', 'consumer_marketplace',
                      'other', 'excluded']
    stage_estimate: Literal['pre_seed', 'seed', 'series_a',
                            'later_stage', 'unknown']
    confidence: Literal['high', 'medium', 'low']
    company_name: str
    rationale: str
    evidence: List[Evidence] = Field(min_items=1)
```

**Extension for Exit Prediction:**
```python
class ExitPredictionOutput(BaseModel):
    exit_probability: float = Field(ge=0.0, le=1.0)
    exit_timeline: Literal['1-2yr', '2-3yr', '3-5yr', '5-7yr', '7+yr']
    exit_type_probabilities: Dict[str, float]  # {ipo: 0.3, acquisition: 0.6, failure: 0.1}
    confidence: Literal['high', 'medium', 'low']
    key_factors: List[str] = Field(min_items=1)
    evidence: List[Evidence] = Field(min_items=1)
```

### Source: Specification - CompanyClassifierService

**Service Architecture:**
```
CLI (run_pipeline.py classify)
    → CompanyClassifierService
        → SignalStore.get_companies_needing_classification()
        → SignalStore.get_all_signals_for_company()
        → LLMClassifier.classify(signal_bundle)
        → SignalStore.save_thesis_classification()
```

**Signal Bundling Pattern:**
- All signals for a company gathered by canonical_key
- Entire bundle passed to LLM for context-rich analysis
- Classification produces citation-backed results

**Classification Priority Logic:**
1. Companies without any classification (new signals)
2. Companies where latest signal > last classification timestamp
3. Process oldest-first for fairness

**CLI Interface:**
```bash
# Batch classification
python run_pipeline.py classify --limit 10

# Single company re-classification
python run_pipeline.py classify --company-key "domain:acme.com"
```

**Required SignalStore Methods:**
| Method | Purpose |
|--------|---------|
| `get_companies_needing_classification(limit)` | Find unclassified companies |
| `get_all_signals_for_company(canonical_key)` | Bundle signals per company |
| `save_thesis_classification(result, canonical_key)` | Persist with evidence JSON |
| `log_classification_error(canonical_key, error)` | Track failures for review |

### Synthesis: Classification Patterns for Exit Predictor

**Recommended ExitPredictorService Architecture:**
```python
# services/exit_predictor_service.py
class ExitPredictorService:
    """Orchestrates exit prediction workflow."""

    def __init__(self, store: SignalStore, predictor: ExitPredictor):
        self.store = store
        self.predictor = predictor

    async def run(self, limit: int = 10):
        """Batch prediction with signal bundling."""
        companies = await self.store.get_companies_needing_exit_prediction(limit)

        for canonical_key in companies:
            try:
                # Bundle all signals for context
                signals = await self.store.get_all_signals_for_company(canonical_key)
                consolidated = await self._consolidate_signals(signals)

                # Generate prediction with self-correction
                prediction = await self.predictor.predict(consolidated)

                # Persist with evidence
                await self.store.save_exit_prediction(prediction, canonical_key)

            except Exception as e:
                await self.store.log_prediction_error(canonical_key, str(e))
```

**Evidence-Based Exit Prediction:**
```python
class ExitEvidence(BaseModel):
    signal_id: int
    source_name: str  # e.g., "crunchbase_funding", "github_activity"
    factor: str       # e.g., "serial_founder", "investor_centrality"
    value: float      # e.g., 0.85
    quote: str        # Supporting evidence text

class ExitPrediction(BaseModel):
    canonical_key: str
    exit_probability: float = Field(ge=0.0, le=1.0)
    percentile_rank: int = Field(ge=0, le=100)
    exit_timeline: str
    confidence: Literal['high', 'medium', 'low']
    key_factors: List[str]
    evidence: List[ExitEvidence] = Field(min_items=1)
    was_corrected: bool = False
    prediction_failed: bool = False
```

**Integration with Existing Pipeline:**
| Step | Current | With Exit Predictor |
|------|---------|---------------------|
| 1 | Signal collection | Signal collection |
| 2 | Signal consolidation | Signal consolidation |
| 3 | Thesis classification | Thesis classification |
| 4 | Verification gate | Verification gate |
| 5 | - | **Exit prediction** |
| 6 | Notion push | Notion push (with exit score) |
