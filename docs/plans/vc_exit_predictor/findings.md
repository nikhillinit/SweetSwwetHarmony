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
