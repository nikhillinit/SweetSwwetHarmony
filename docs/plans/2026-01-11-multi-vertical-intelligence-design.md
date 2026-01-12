# Multi-Vertical Intelligence Platform Design

**Date:** 2026-01-11
**Status:** Approved
**Author:** Press On Ventures Engineering

---

## Executive Summary

This design extends Harmonic's discovery pipeline with domain-specialized intelligence across all Press On investment verticals. Digital health serves as the reference implementation, with architecture patterns replicable for travel/hospitality, B2B SaaS, and consumer verticals.

**Key Principles:**
- Harmonic is the source of truth (not Notion)
- Notion is an optional data source and on-demand destination
- Each vertical gets specialized collectors, classifiers, and enrichment
- Architecture proves on health, then scales to other verticals

---

## Table of Contents

1. [Investment Verticals](#1-investment-verticals)
2. [System Architecture](#2-system-architecture)
3. [Domain Router](#3-domain-router)
4. [Health Collectors & Data Sources](#4-health-collectors--data-sources)
5. [Medical Entity Resolution](#5-medical-entity-resolution)
6. [Health Enrichment Logic](#6-health-enrichment-logic)
7. [Data Schema & Storage](#7-data-schema--storage)
8. [Notion Integration](#8-notion-integration)
9. [Testing Strategy](#9-testing-strategy)
10. [Error Handling & Resilience](#10-error-handling--resilience)
11. [Implementation Roadmap](#11-implementation-roadmap)

---

## 1. Investment Verticals

The platform supports all Press On VC investment verticals:

| Vertical | Focus Areas | Portfolio Examples |
|----------|-------------|-------------------|
| **Health & Wellness** | Consumer health products/services, health IT | 10Beauty, Cofertility, Feno, Rhythm Science, Jacob Bar |
| **Travel & Hospitality** | Luxury travel, hotel tech, experiential | Skylark |
| **Premium Consumer** | DTC brands, beverages, nutrition | SipMargs, Jacob Bar |
| **Consumer Platforms** | Marketplaces, community commerce, facilities management | Recess, Snapfix |
| **B2B SaaS** | Enterprise software, business tools, vertical SaaS | - |

**Scope Exclusions (Health Vertical):**
- Pure pharmaceutical/biotech (drug development, clinical trials for therapeutics)
- Medical devices for providers only (surgical equipment, provider-only diagnostics)

**In Scope:**
- Consumer health products (devices, wearables, beauty tech)
- Consumer health services (telehealth, fertility, virtual care)
- Health-enabling consumer brands (wellness, nutrition)
- Enterprise health IT with provider touchpoint (EHR, clinical workflow)

---

## 2. System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXISTING COLLECTORS                                │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌───────────┐ │
│  │   HN    │ │ GitHub  │ │ Reddit  │ │  USPTO  │ │Crunchbase│ │SEC EDGAR │ │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └─────┬─────┘ │
└───────┼──────────┼──────────┼──────────┼──────────┼────────────────┼───────┘
        │          │          │          │          │                │
        └──────────┴──────────┴──────────┼──────────┴────────────────┘
                                         │
┌────────────────────────────────────────┼────────────────────────────────────┐
│                    NEW VERTICAL COLLECTORS                                   │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │Product Hunt │ │ Kickstarter │ │Health IT    │ │Rock Health  │           │
│  │  (health)   │ │  (health)   │ │   News      │ │ Portfolio   │           │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘           │
└─────────┼───────────────┼───────────────┼───────────────┼───────────────────┘
          │               │               │               │
          └───────────────┴───────────────┴───────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DOMAIN ROUTER                                      │
│  ┌────────────────────┐    ┌────────────────────────────────────────────┐   │
│  │ Fast Domain Detect │───►│ Vertical-Specific LLM Classifiers          │   │
│  │   (keyword rules)  │    │ ┌────────┐ ┌────────┐ ┌────────┐ ┌───────┐ │   │
│  └────────────────────┘    │ │ Health │ │ Travel │ │  SaaS  │ │Consumer│ │   │
│                            │ └────────┘ └────────┘ └────────┘ └───────┘ │   │
│                            └────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      VERTICAL ENRICHMENT LAYERS                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Health Enrichment                                                    │    │
│  │ ┌─────────────────┐ ┌─────────────┐ ┌─────────────┐                 │    │
│  │ │ClinicalTrials.gov│ │  OpenFDA   │ │   PubMed   │                 │    │
│  │ └─────────────────┘ └─────────────┘ └─────────────┘                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Medical Entity Resolver (SciSpacy + UMLS)                           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         HARMONIC DATABASE                                    │
│  ┌─────────────┐ ┌─────────────────────────────────────────────────────┐   │
│  │   Signals   │ │           Vertical Enrichment Tables                │   │
│  │  (+ domain  │ │ ┌───────────────┐ ┌─────────────┐ ┌───────────────┐ │   │
│  │  metadata)  │ │ │Clinical Trials│ │FDA Clearances│ │ Publications │ │   │
│  └─────────────┘ │ └───────────────┘ └─────────────┘ └───────────────┘ │   │
│                  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                          ┌─────────┴─────────┐
                          ▼                   ▼
              ┌───────────────────┐ ┌───────────────────┐
              │   Notion (Pull)   │ │  Notion (Push)    │
              │  Optional enrich  │ │  On-demand only   │
              └───────────────────┘ └───────────────────┘
```

### Core Components

1. **Vertical Collectors Framework** - Pluggable collector system where each vertical adds specialized sources
2. **Domain Router** - Lightweight classification routes signals to vertical-specific LLM prompts
3. **Vertical Enrichment Layers** - Each domain adds its own enrichment logic
4. **Extensible Metadata Storage** - Shared pattern: Signal.metadata for lightweight flags + vertical-specific enrichment tables
5. **Notion Integration** - Pull-based enrichment (optional) + push on user demand (never auto-modify)

---

## 3. Domain Router

The domain router routes signals to vertical-specific classifiers, enabling specialized intelligence without prompt dilution.

### Stage 1: Fast Domain Detection (Rule-Based)

Keyword matching on signal source + content:

| Domain | Triggers |
|--------|----------|
| Health | "FDA", "clinical trial", "telehealth", "wearable", source=Product Hunt health |
| Travel | "hotel", "booking", "hospitality", "travel tech", source=Phocuswright |
| SaaS | "enterprise software", "B2B", "API", source=G2/Capterra |
| Consumer | "DTC", "CPG", "brand", Kickstarter consumer category |

Output: Primary domain(s) + confidence. Signals can match multiple domains.

### Stage 2: Vertical-Specific LLM Classification

Route to domain-specific prompt template:

| Domain | Prompt Expertise |
|--------|-----------------|
| Health | Consumer health products vs. services, regulatory stages, reimbursement, filters out pharma/provider-only devices |
| Travel | Luxury vs. budget, B2B hotel tech vs. consumer booking, experiential categories |
| SaaS | Vertical SaaS, GTM motion, enterprise vs. SMB |
| Consumer | DTC brands, premium positioning, retail channels |

Each prompt outputs standardized schema:
```json
{
  "fit_score": 8,
  "category": "consumer_health_device",
  "reasoning": "AI-powered oral health tracking matches thesis...",
  "investment_stage_fit": "seed"
}
```

### Multi-Domain Handling

Signals matching multiple domains (e.g., "SaaS for hospitals" = health + SaaS) get classified by both vertical prompts. Highest fit_score wins for primary categorization, secondary domain stored in metadata.

### Cost Control

- Fast domain detection is free (rules)
- Only 1-2 LLM calls per signal (primary + secondary domain if applicable)
- Estimated ~$15-30/month for classification across all verticals at moderate volume

---

## 4. Health Collectors & Data Sources

The domain router classifies ALL signals - both from existing collectors and new vertical-specific sources.

### Existing Collectors (Feed Domain Router)

| Collector | Health Signals | Example |
|-----------|---------------|---------|
| HackerNews | Health startups on Show HN, health tech discussions | "Show HN: AI-powered fertility tracking app" |
| GitHub | Health-related repos, founder activity | Telehealth SDK, health data libraries |
| Reddit | r/healthtech, r/digitalhealth, r/wearables | Consumer health product launches |
| USPTO | Health/wellness trademark classes | Beauty device brands, supplement names |
| Crunchbase | Health category funding | Series A for virtual care startup |
| SEC EDGAR | Health company filings | IPO prospectus for health IT |

### New Health-Specific Collectors

| Source | Signals | Cost |
|--------|---------|------|
| Product Hunt (health category) | Consumer health apps/services | Free |
| Kickstarter/Indiegogo (health) | Wearables, beauty tech, devices | Free |
| Healthcare IT News RSS | Enterprise health IT | Free |
| Rock Health portfolio | Accelerator portfolio companies | Free (scrape) |

### Clinical Context Sources (Enrichment Only)

| Source | Purpose | Cost |
|--------|---------|------|
| ClinicalTrials.gov API | Trial phase, validation status | Free |
| OpenFDA API | Device clearances, regulatory | Free |
| PubMed E-utilities | Research citations, founder papers | Free |

### Collector Implementation Pattern

```
BaseVerticalCollector (abstract)
  └── HealthCollector (domain-specific)
        ├── ProductHuntHealthCollector
        ├── KickstarterHealthCollector
        ├── HealthITNewsCollector
        └── AcceleratorCollector (Rock Health, StartUp Health)
```

### Rate Limits & Scheduling

| Source | Rate Limit | Schedule |
|--------|------------|----------|
| Product Hunt | 500 requests/day | Hourly polls |
| ClinicalTrials.gov | 3 requests/second | Batch enrichment |
| OpenFDA | 240 requests/minute | On-demand enrichment |

---

## 5. Medical Entity Resolution

The medical entity resolver normalizes health company names and medical terminology across data sources.

### Challenge

- "Acme Therapeutics Inc" (FDA) vs "Acme Tx" (ClinicalTrials.gov) vs "Acme Therapeutics" (HN post)
- "cardiovascular" vs "cardiac" vs "heart disease" (medical terminology variations)
- Consumer health brands may use creative names that don't match legal entities

### Solution: Layered Resolution

**Layer 1: Standard Entity Resolution (Existing)**
- Fuzzy string matching on company names
- Domain normalization (acmetherapeutics.com → Acme Therapeutics)
- Existing Harmonic entity resolver handles this

**Layer 2: Medical NLP Enhancement (New)**
- **SciSpacy** - Medical entity extraction from signal text (diseases, treatments, devices)
- **UMLS Integration** - Concept Unique Identifiers (CUIs) normalize medical terminology
- **MedSpacy** - Clinical text processing for parsing regulatory documents

### Implementation

```python
class HealthEntityResolver:
    def __init__(self):
        self.base_resolver = EntityResolver()  # existing
        self.nlp = spacy.load("en_core_sci_lg")  # SciSpacy
        self.linker = UMLSEntityLinker()  # UMLS concepts

    def resolve(self, signal: Signal) -> ResolvedEntity:
        # Step 1: Base company name resolution
        entity = self.base_resolver.resolve(signal.company_name)

        # Step 2: Extract medical concepts from text
        doc = self.nlp(signal.content)
        medical_concepts = [ent._.umls_cui for ent in doc.ents]

        # Step 3: Link to enrichment sources via CUI + company name
        return ResolvedEntity(entity_id, company_name, medical_concepts)
```

### Storage

- `entity_id` links signals across sources
- `medical_concepts` (UMLS CUIs) stored in Signal.metadata for filtering

---

## 6. Health Enrichment Logic

The enrichment layer adds clinical/regulatory context to health signals based on selective triggers.

### Enrichment Triggers (Hybrid)

Enrich if ANY condition is true:
1. **High-value source** - Product Hunt health, Kickstarter health, Crunchbase funding
2. **New entity** - First signal for this company
3. **High confidence** - Health classifier fit_score >= 7/10

### Enrichment Pipeline

```
Signal passes trigger?
    │
    ├─► ClinicalTrials.gov lookup (by company name + medical concepts)
    │   └─► Store: trial phase, status, enrollment, conditions
    │
    ├─► OpenFDA lookup (by company name)
    │   └─► Store: 510k clearances, device classifications, recalls
    │
    └─► PubMed lookup (by founder names + company name)
        └─► Store: publication count, recent papers, research focus
```

### Enrichment Priority

| Priority | Source | Reason |
|----------|--------|--------|
| Immediate | ClinicalTrials.gov | Fast, free, high signal value |
| Immediate | OpenFDA | Fast, free, regulatory status critical |
| Deferred | PubMed | Slower, run async, less urgent |

### Enrichment Output Schema

```python
class HealthEnrichment:
    entity_id: str

    # Clinical Trials
    active_trials: int
    highest_phase: str  # "preclinical", "phase_1", "phase_2", "phase_3", "approved"
    total_enrollment: int
    primary_conditions: list[str]

    # Regulatory
    fda_clearances: list[FDAClearance]
    device_class: str  # I, II, III
    recent_recalls: list[Recall]

    # Research
    publication_count: int
    recent_publications: list[Publication]
    founder_h_index: int  # if academic founder

    # Metadata
    enriched_at: datetime
    enrichment_sources: list[str]
```

### Freshness

- Re-enrich entities every 30 days or on new signal
- Track `enriched_at` to avoid redundant API calls

---

## 7. Data Schema & Storage

Implements hybrid approach: lightweight flags in Signal.metadata + dedicated enrichment tables per vertical.

### Core Signal Table (Minimal Changes)

```sql
-- Add domain field to existing Signal table
ALTER TABLE signals ADD COLUMN domain VARCHAR(50);  -- "health", "travel", "saas", "consumer"
```

### Signal.metadata JSON (Lightweight Flags)

```json
{
  "domain": "health",
  "health_category": "consumer_device",
  "priority_stage": "phase_2",
  "has_fda_clearance": true,
  "has_active_trials": true,
  "medical_concepts": ["C0018799", "C0027051"],
  "enrichment_status": "complete"
}
```

### Health Enrichment Tables

```sql
-- Clinical trial data linked by entity_id
CREATE TABLE health_clinical_trials (
    id INTEGER PRIMARY KEY,
    entity_id VARCHAR(100) NOT NULL,
    nct_id VARCHAR(20) UNIQUE,
    title TEXT,
    phase VARCHAR(20),
    status VARCHAR(30),
    enrollment INTEGER,
    conditions TEXT,  -- JSON array
    start_date DATE,
    completion_date DATE,
    fetched_at TIMESTAMP
);

-- FDA regulatory data
CREATE TABLE health_fda_clearances (
    id INTEGER PRIMARY KEY,
    entity_id VARCHAR(100) NOT NULL,
    application_number VARCHAR(20),
    device_name TEXT,
    device_class VARCHAR(5),
    clearance_type VARCHAR(20),  -- "510k", "PMA", "de_novo"
    decision VARCHAR(20),
    decision_date DATE,
    fetched_at TIMESTAMP
);

-- Research publications
CREATE TABLE health_publications (
    id INTEGER PRIMARY KEY,
    entity_id VARCHAR(100) NOT NULL,
    pmid VARCHAR(20),
    title TEXT,
    authors TEXT,
    journal VARCHAR(200),
    pub_date DATE,
    citation_count INTEGER,
    fetched_at TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX idx_trials_entity ON health_clinical_trials(entity_id);
CREATE INDEX idx_trials_phase ON health_clinical_trials(phase);
CREATE INDEX idx_fda_entity ON health_fda_clearances(entity_id);
CREATE INDEX idx_pubs_entity ON health_publications(entity_id);
```

### Extensibility Pattern for Other Verticals

```sql
-- Travel vertical (Phase 2)
CREATE TABLE travel_certifications (...);
CREATE TABLE travel_reviews (...);

-- SaaS vertical (Phase 2)
CREATE TABLE saas_integrations (...);
CREATE TABLE saas_tech_stack (...);
```

---

## 8. Notion Integration

Notion is an optional data source for enrichment and an on-demand destination for signals. Never auto-modifies Notion.

### Pull from Notion (Enrichment Source)

```python
class NotionEnrichmentSource:
    """Pull deal status, feedback, notes from Notion to enrich signals"""

    def pull_deal_context(self, entity_id: str) -> NotionContext:
        # Search Notion database for matching company
        # Pull: deal status, partner notes, pass reasons, meeting history
        return NotionContext(
            deal_status="active_diligence",
            partner_notes="Strong founder, need tech DD",
            pass_reason=None,
            last_interaction=datetime(2026, 1, 5)
        )

    def sync_suppression_list(self) -> list[str]:
        # Pull "passed" and "portfolio" companies to suppress from signals
        return ["company_id_1", "company_id_2", ...]
```

### Push to Notion (On-Demand Only)

```python
class NotionPusher:
    """Push signals to Notion only on explicit user command"""

    def push_signal(self, signal: Signal, enrichment: HealthEnrichment):
        # User explicitly triggers: /push-to-notion <signal_id>
        pass

    def push_batch(self, signals: list[Signal], filter: str):
        # User triggers: /push-to-notion --filter "health AND fit_score>=8"
        pass
```

### User Commands

| Command | Action |
|---------|--------|
| `/pull-notion-context <entity>` | Fetch Notion data for specific company |
| `/sync-suppression` | Update suppression list from Notion passes |
| `/push-to-notion <signal_id>` | Push single signal to Notion Inbox |
| `/push-to-notion --filter "..."` | Batch push signals matching filter |

### Key Principle

- Harmonic works without Notion
- Notion enrichment adds context when available
- Notion push is user-initiated, never automatic
- Losing Notion access = lose one enrichment source, Harmonic continues working

---

## 9. Testing Strategy

Testing ensures each component works independently and integrates correctly.

### Unit Tests

```python
# Domain Router Tests
def test_domain_router_detects_health_from_keywords():
    signal = Signal(content="FDA-cleared wearable for heart monitoring")
    assert router.detect_domain(signal) == ["health"]

def test_domain_router_handles_multi_domain():
    signal = Signal(content="SaaS platform for hospital scheduling")
    domains = router.detect_domain(signal)
    assert "health" in domains and "saas" in domains

# Health Entity Resolver Tests
def test_medical_entity_extraction():
    text = "Treatment for cardiovascular disease"
    concepts = resolver.extract_medical_concepts(text)
    assert "C0018799" in concepts  # UMLS CUI for cardiovascular

def test_company_name_normalization():
    assert resolver.normalize("Acme Tx") == resolver.normalize("Acme Therapeutics Inc")

# Health Enrichment Tests
def test_enrichment_triggers_on_new_entity():
    signal = Signal(entity_id="new_entity", source="hackernews")
    assert enrichment_layer.should_enrich(signal) == True

def test_enrichment_skips_known_low_confidence():
    signal = Signal(entity_id="known_entity", fit_score=3, source="reddit")
    assert enrichment_layer.should_enrich(signal) == False
```

### Integration Tests

```python
@pytest.mark.integration
async def test_health_signal_full_pipeline():
    raw_signal = {"title": "AI fertility tracker", "source": "producthunt_health"}
    signal = await pipeline.process(raw_signal)

    assert signal.domain == "health"
    assert signal.metadata["health_category"] is not None
    assert signal.fit_score > 0

@pytest.mark.integration
async def test_enrichment_creates_clinical_trial_records():
    signal = Signal(entity_id="test_entity", company_name="Pfizer")
    await enrichment_layer.enrich(signal)

    trials = db.query(HealthClinicalTrials).filter_by(entity_id="test_entity").all()
    assert len(trials) > 0
```

### Mock Strategy

- Mock external APIs (ClinicalTrials.gov, OpenFDA, PubMed) in unit tests
- Use recorded responses for deterministic integration tests
- Live API tests run nightly, not on every commit

---

## 10. Error Handling & Resilience

The health intelligence layer handles failures gracefully without blocking the core pipeline.

### Failure Isolation

| Component | Failure Mode | Handling |
|-----------|--------------|----------|
| Health Collector | API down/rate limited | Log warning, skip source this cycle, retry next run |
| Domain Router | Classification error | Default to "unknown" domain, process with generic classifier |
| Entity Resolver | SciSpacy model error | Fall back to base entity resolver (no medical concepts) |
| Enrichment API | ClinicalTrials.gov timeout | Mark enrichment as "pending", retry async later |
| Notion Pull | Auth revoked | Log error, continue without Notion context |

### Retry Strategy

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=60))
async def fetch_clinical_trials(company_name: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(CLINICALTRIALS_API, params={"query": company_name})
        response.raise_for_status()
        return response.json()
```

### Graceful Degradation

```
Full pipeline: Signal → Domain Router → Health Classifier → Enrichment → Storage
                           ↓ fail              ↓ fail           ↓ fail
Degraded:      Signal → Generic Classifier → No enrichment → Storage (partial metadata)
```

### Health Monitoring

```python
class HealthIntelligenceMetrics:
    signals_processed: Counter
    signals_by_domain: Counter  # per vertical
    enrichment_success_rate: Gauge
    enrichment_latency_p95: Histogram
    api_errors_by_source: Counter

    def report_health(self) -> dict:
        return {
            "status": "healthy" if self.enrichment_success_rate > 0.9 else "degraded",
            "signals_last_hour": self.signals_processed.last_hour(),
            "enrichment_rate": self.enrichment_success_rate.value()
        }
```

### Alerting

- Enrichment success rate < 80% for 1 hour → Slack alert
- Any collector fails 3 consecutive runs → Slack alert
- Notion auth error → Slack alert (user action required)

---

## 11. Implementation Roadmap

Phased delivery starting with digital health, then expanding to other verticals.

### Phase 1: Digital Health (4-6 weeks)

| Week | Deliverables |
|------|--------------|
| 1-2 | Domain router infrastructure + health keyword detection |
| 2-3 | Health-specific LLM classifier prompt + integration |
| 3-4 | Medical entity resolver (SciSpacy + UMLS) |
| 4-5 | Health collectors (Product Hunt, Kickstarter, Health IT News) |
| 5-6 | Enrichment layer (ClinicalTrials.gov, OpenFDA, PubMed) + storage tables |

### Phase 2: Travel & B2B SaaS (3-4 weeks each, parallelizable)

| Vertical | Key Components |
|----------|----------------|
| Travel & Hospitality | Travel collectors (Phocuswright, Skift), travel classifier prompt, travel enrichment (certifications, reviews) |
| B2B SaaS | SaaS collectors (G2, Capterra, funding sources), SaaS classifier prompt, SaaS enrichment (tech stack, integrations) |

### Phase 3: Premium Consumer & Platforms (2-3 weeks)

| Vertical | Key Components |
|----------|----------------|
| Premium Consumer | DTC collectors (brand launches, CPG news), consumer classifier prompt, brand sentiment enrichment |
| Consumer Platforms | Marketplace collectors, platform classifier prompt, community metrics enrichment |

### Dependencies

```
Phase 1 (Health) ─── proves architecture
       │
       ├──► Phase 2a (Travel) ─┐
       │                       ├──► Phase 3 (Consumer)
       └──► Phase 2b (SaaS) ───┘
```

### Success Metrics

| Metric | Target |
|--------|--------|
| Health signals discovered/week | 50+ qualified leads |
| Enrichment coverage | >80% of health signals enriched |
| False positive rate | <20% (signals not matching thesis) |
| Time to first vertical | 6 weeks to production health intelligence |

---

## Appendix A: Budget Estimate

| Category | Monthly Cost |
|----------|--------------|
| LLM Classification (~$15-30) | $25 |
| External APIs (all free tier) | $0 |
| Infrastructure (existing) | $0 |
| **Total** | **~$25/month** |

Within the $20-50/month budget constraint.

---

## Appendix B: OSS Dependencies

| Tool | Purpose | License |
|------|---------|---------|
| SciSpacy | Medical NLP | MIT |
| spacy-umls | UMLS entity linking | MIT |
| MedSpacy | Clinical text processing | MIT |
| tenacity | Retry logic | Apache 2.0 |
| httpx | Async HTTP client | BSD |

---

## Appendix C: API Documentation

- [ClinicalTrials.gov API](https://clinicaltrials.gov/api/gui)
- [OpenFDA API](https://open.fda.gov/apis/)
- [PubMed E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/)
- [Product Hunt API](https://api.producthunt.com/v2/docs)
