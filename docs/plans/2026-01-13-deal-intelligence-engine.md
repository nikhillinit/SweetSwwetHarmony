# Deal Intelligence Engine - Integration Plan

## Overview

Build a unified "Deal Intelligence Engine" by adopting the best methodologies from Harmonic.ai, PitchBook Exit Predictor, and Evertrace - all using existing data and free APIs.

**Goal:** Surface better deals faster by combining:
- Signal correlation (Evertrace approach)
- Traction metrics (Harmonic approach)
- Investor network analysis (PitchBook approach)
- Unified deal quality scoring

**Cost:** ~$50/mo for OpenAI embeddings (optional Phase 6)
**Effort:** ~20-25 days across 7 phases

---

## Source Analysis

### Harmonic.ai
- **Database:** 30M+ companies, 190M+ people
- **Key Value:** Traction metrics (web traffic %, headcount growth %)
- **API:** REST + GraphQL, tiered pricing
- **Adoptable:** Build our own traction metrics from existing data

### PitchBook VC Exit Predictor
- **Method:** XGBoost classifier, 34 features, 67.8% accuracy
- **Key Value:** Eigenvector centrality for investor ranking
- **Adoptable:** Build investor network graph, calculate centrality scores

### Evertrace
- **Focus:** Pre-company founder detection ("stealth founders")
- **Key Value:** Signal correlation, behavioral patterns, person-centric approach
- **Adoptable:** Link founder_store to signals, detect founder intent patterns

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DEAL INTELLIGENCE ENGINE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐               │
│  │  FOUNDER    │   │  SIGNAL     │   │  INVESTOR   │               │
│  │  TRACKER    │   │ CORRELATOR  │   │  NETWORK    │               │
│  │ (Phase 4)   │   │ (Phase 1)   │   │ (Phase 3)   │               │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘               │
│         │                 │                 │                       │
│         ▼                 ▼                 ▼                       │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │              TRACTION SCORE (Phase 2)                    │       │
│  │  GitHub momentum + Hiring velocity + Social proof        │       │
│  └─────────────────────────────────────────────────────────┘       │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐       │
│  │              DEAL QUALITY SCORE (Phase 5)                │       │
│  │  thesis_fit × traction × investor_quality × founder_score│       │
│  │  → Percentile rank (0-100)                               │       │
│  └─────────────────────────────────────────────────────────┘       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Signal Correlation Engine (Evertrace-inspired)

**Goal:** Link scattered signals to reveal founder intent and detect known founders in new signals.

### New File: `utils/signal_correlator.py`

```python
@dataclass
class CorrelatedFounder:
    founder_id: int
    founder_name: str
    canonical_key: str
    signals: List[Signal]
    correlation_type: str  # 'domain', 'github', 'company', 'name_match'
    confidence: float

class SignalCorrelator:
    """Connect signals across sources to surface founder intent."""

    async def correlate_founder_signals(self, founder_id: int) -> List[Signal]:
        """Find all signals linked to a known founder."""

    async def detect_founder_in_signal(self, signal: Signal) -> Optional[CorrelatedFounder]:
        """Check if a new signal links to a known founder."""

    async def find_serial_founder_ventures(self, founder_id: int) -> List[str]:
        """Track all canonical_keys associated with a serial founder."""
```

### Integration Points
- Call after signal collection, before thesis filter
- Add `correlated_founder_id` to signals table (nullable FK)
- Alert when serial founder starts new venture

### Database Changes
```sql
ALTER TABLE signals ADD COLUMN correlated_founder_id INTEGER REFERENCES founders(id);
ALTER TABLE signals ADD COLUMN correlation_confidence REAL;
```

### Tests
- Test founder-to-signal correlation by email, GitHub, name
- Test signal-to-founder detection
- Test serial founder venture tracking

---

## Phase 2: Traction Score (Harmonic-inspired)

**Goal:** Calculate momentum metrics from existing data sources.

### New File: `utils/traction_calculator.py`

```python
@dataclass
class TractionScore:
    # GitHub momentum
    github_stars_growth_30d: float  # % change
    github_commit_velocity: float   # commits per week

    # Hiring momentum
    job_posting_velocity: float     # new postings per week
    job_count_growth_30d: float     # % change

    # Social momentum
    ph_vote_growth_30d: float       # % change
    hn_mention_growth_30d: float    # % change

    # Composite
    composite_momentum: float       # 0-1 weighted average
    momentum_percentile: int        # 0-100 rank vs all signals

class TractionCalculator:
    """Calculate traction metrics from historical signal data."""

    async def calculate(self, canonical_key: str, days: int = 30) -> TractionScore:
        """Compute momentum metrics for a company."""

    async def calculate_github_momentum(self, signals: List[Signal]) -> dict:
        """Stars growth, commit velocity from github signals."""

    async def calculate_hiring_velocity(self, signals: List[Signal]) -> dict:
        """Job posting frequency and growth."""

    async def calculate_social_momentum(self, signals: List[Signal]) -> dict:
        """Product Hunt votes, HN mentions over time."""
```

### Data Requirements
- Need historical snapshots of GitHub stars (store in raw_data)
- Need job posting counts over time
- Need PH/HN scores over time

### New Collector Enhancement: `collectors/github.py`
- Store `stars` in raw_data for time-series analysis
- Add `previous_stars` tracking

### Database Changes
```sql
CREATE TABLE traction_scores (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    github_stars_growth_30d REAL,
    github_commit_velocity REAL,
    job_posting_velocity REAL,
    job_count_growth_30d REAL,
    ph_vote_growth_30d REAL,
    hn_mention_growth_30d REAL,
    composite_momentum REAL,
    momentum_percentile INTEGER,
    UNIQUE(canonical_key, calculated_at)
);
```

---

## Phase 3: Investor Network Graph (PitchBook-inspired)

**Goal:** Build co-investment network and calculate eigenvector centrality for investor ranking.

### New File: `utils/investor_network.py`

```python
import networkx as nx
from typing import Dict, List

@dataclass
class InvestorRank:
    investor_name: str
    eigenvector_centrality: float
    percentile_rank: int  # 0-100
    co_investment_count: int
    notable_coinvestors: List[str]

class InvestorNetworkAnalyzer:
    """Build investor co-investment network using eigenvector centrality."""

    def __init__(self, signal_store: SignalStore):
        self.store = signal_store
        self.graph: nx.Graph = None
        self.investor_ranks: Dict[str, float] = {}

    async def build_network(self) -> nx.Graph:
        """Create graph from Crunchbase + SEC funding data."""
        G = nx.Graph()

        # Get funding rounds from Crunchbase collector data
        rounds = await self._get_funding_rounds()

        for round in rounds:
            investors = round.get('investors', [])
            # Add edges between all co-investors
            for i, inv1 in enumerate(investors):
                for inv2 in investors[i+1:]:
                    if G.has_edge(inv1, inv2):
                        G[inv1][inv2]['weight'] += 1
                    else:
                        G.add_edge(inv1, inv2, weight=1)

        self.graph = G
        return G

    def rank_investors(self) -> Dict[str, float]:
        """Calculate eigenvector centrality (same as PitchBook/PageRank)."""
        self.investor_ranks = nx.eigenvector_centrality(
            self.graph,
            weight='weight',
            max_iter=1000
        )
        return self.investor_ranks

    def score_company_investors(self, investors: List[str]) -> float:
        """Score company based on average investor centrality."""
        scores = [self.investor_ranks.get(inv, 0) for inv in investors]
        return sum(scores) / len(scores) if scores else 0
```

### Data Sources
- Crunchbase: `funding_rounds[].investors`
- SEC Form D: `issuer.related_persons` (officers/directors)

### Database Changes
```sql
CREATE TABLE investor_network (
    id INTEGER PRIMARY KEY,
    investor_name TEXT UNIQUE NOT NULL,
    eigenvector_centrality REAL,
    percentile_rank INTEGER,
    co_investment_count INTEGER,
    notable_coinvestors TEXT,  -- JSON array
    calculated_at TEXT NOT NULL
);

CREATE TABLE company_investor_scores (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    investor_quality_score REAL,
    investors TEXT,  -- JSON array
    calculated_at TEXT NOT NULL,
    UNIQUE(canonical_key)
);
```

### Dependencies
- `networkx` library (add to requirements.txt)

---

## Phase 4: Founder Intent Detection (Evertrace-inspired)

**Goal:** Detect behavioral patterns suggesting founder intent before company formation.

### New File: `utils/founder_intent.py`

```python
@dataclass
class FounderIntent:
    founder_id: int
    intent_signals: List[str]
    intent_score: float  # 0-1
    detected_at: datetime

    # Specific patterns detected
    new_domain_registered: bool
    new_github_org: bool
    linkedin_status_change: bool
    left_employer: bool
    co_founder_seeking: bool

class FounderIntentDetector:
    """Detect founder intent patterns (Evertrace approach)."""

    async def detect_intent(self, founder_id: int) -> FounderIntent:
        """Check for behavioral signals suggesting new venture."""

    async def check_domain_activity(self, founder: Founder) -> List[Signal]:
        """Check if founder registered new domains."""

    async def check_github_activity(self, founder: Founder) -> dict:
        """Detect repo spikes, structured commits, new org creation."""

    async def check_career_transition(self, founder: Founder) -> bool:
        """Detect 'left Google → stealth mode' patterns."""
```

### Behavioral Patterns to Detect (from Evertrace)
1. **GitHub patterns:**
   - Repository activity spikes
   - Structured commit behavior
   - New organization creation
   - Private → public repo transitions

2. **Domain patterns:**
   - New domain by known founder email
   - Domain name matches founder's expertise area

3. **LinkedIn patterns:**
   - "Building something new" status
   - Left prominent employer
   - Added "Stealth" or "Founder" title

4. **Co-founder seeking:**
   - YC Co-Founder Matching activity
   - Twitter "looking for cofounder" signals

### Database Changes
```sql
CREATE TABLE founder_intent_signals (
    id INTEGER PRIMARY KEY,
    founder_id INTEGER REFERENCES founders(id),
    intent_type TEXT NOT NULL,  -- 'domain', 'github', 'linkedin', 'cofounder_seeking'
    intent_score REAL,
    signal_details TEXT,  -- JSON
    detected_at TEXT NOT NULL
);
```

---

## Phase 5: Deal Quality Score (PitchBook-inspired)

**Goal:** Unified scoring that combines all signals into percentile-ranked deal quality.

### New File: `utils/deal_quality_scorer.py`

```python
@dataclass
class DealQualityScore:
    canonical_key: str

    # Component scores (0-1)
    thesis_fit_score: float
    traction_score: float
    investor_quality_score: float
    founder_score: float

    # Weighted combination
    raw_score: float
    percentile_rank: int  # 0-100 (like PitchBook)

    # Routing recommendation
    recommendation: str  # 'source', 'tracking', 'hold', 'pass'

class DealQualityScorer:
    """Unified deal quality scoring (PitchBook-inspired)."""

    # Configurable weights
    WEIGHTS = {
        'thesis_fit': 0.30,
        'traction': 0.25,
        'investor_quality': 0.20,
        'founder': 0.25
    }

    async def score(self, canonical_key: str) -> DealQualityScore:
        """Calculate unified deal quality score."""

        # Get component scores
        thesis = await self._get_thesis_score(canonical_key)
        traction = await self.traction_calc.calculate(canonical_key)
        investor = await self.investor_network.score_company_investors(...)
        founder = await self.founder_store.get_aggregate_score(canonical_key)

        # Weighted combination
        raw = (
            thesis * self.WEIGHTS['thesis_fit'] +
            traction.composite_momentum * self.WEIGHTS['traction'] +
            investor * self.WEIGHTS['investor_quality'] +
            founder * self.WEIGHTS['founder']
        )

        # Percentile rank across all signals
        percentile = await self._calculate_percentile(raw)

        # Routing recommendation
        recommendation = self._get_recommendation(percentile, thesis)

        return DealQualityScore(...)

    def _get_recommendation(self, percentile: int, thesis_fit: float) -> str:
        """Route based on score (like current verification gate)."""
        if thesis_fit < 0.3:
            return 'pass'
        if percentile >= 80:
            return 'source'
        if percentile >= 50:
            return 'tracking'
        return 'hold'
```

### Database Changes
```sql
CREATE TABLE deal_quality_scores (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT UNIQUE NOT NULL,
    thesis_fit_score REAL,
    traction_score REAL,
    investor_quality_score REAL,
    founder_score REAL,
    raw_score REAL,
    percentile_rank INTEGER,
    recommendation TEXT,
    calculated_at TEXT NOT NULL
);
```

### Dashboard Integration
Add "Deal Quality" panel to Analytics tab:
- Top 10 by percentile rank
- Score breakdown by component
- Trend over time

---

## Files to Create/Modify

| File | Action | Phase |
|------|--------|-------|
| `utils/signal_correlator.py` | Create | 1 |
| `utils/traction_calculator.py` | Create | 2 |
| `utils/investor_network.py` | Create | 3 |
| `utils/founder_intent.py` | Create | 4 |
| `utils/deal_quality_scorer.py` | Create | 5 |
| `storage/signal_store.py` | Add tables | 1-5 |
| `storage/migrations/006_deal_intelligence.py` | Create | 1-5 |
| `workflows/pipeline.py` | Integrate scoring | 5 |
| `dashboard/app.py` | Add Deal Quality panel | 5 |
| `requirements.txt` | Add networkx | 3 |
| `tests/test_signal_correlator.py` | Create | 1 |
| `tests/test_traction_calculator.py` | Create | 2 |
| `tests/test_investor_network.py` | Create | 3 |
| `tests/test_founder_intent.py` | Create | 4 |
| `tests/test_deal_quality_scorer.py` | Create | 5 |
| `utils/semantic_search.py` | Create | 6 |
| `utils/change_detector.py` | Create | 7 |
| `tests/test_semantic_search.py` | Create | 6 |
| `tests/test_change_detector.py` | Create | 7 |

---

## Implementation Order

### Phase 1: Signal Correlation (3 days)
1. Create SignalCorrelator class
2. Add database columns for correlation
3. Write tests for founder-signal linking
4. Integrate into pipeline after collection

### Phase 2: Traction Score (3 days)
1. Create TractionCalculator class
2. Add traction_scores table
3. Enhance GitHub collector to store historical stars
4. Write tests for momentum calculations

### Phase 3: Investor Network (4 days)
1. Add networkx to requirements
2. Create InvestorNetworkAnalyzer class
3. Build network from Crunchbase + SEC data
4. Calculate eigenvector centrality
5. Write tests for network analysis

### Phase 4: Founder Intent (3 days)
1. Create FounderIntentDetector class
2. Implement behavioral pattern detection
3. Add founder_intent_signals table
4. Write tests for intent detection

### Phase 5: Deal Quality Score (4 days)
1. Create DealQualityScorer class
2. Implement percentile ranking
3. Add deal_quality_scores table
4. Integrate into pipeline and dashboard
5. Write integration tests

### Phase 6: Semantic Search (2-3 days) - Optional
1. Add OpenAI API integration
2. Create SemanticSearch class
3. Generate embeddings for all companies
4. Add search endpoint to dashboard
5. Test with natural language queries

### Phase 7: Change Detection (2 days) - Optional
1. Create ChangeDetector class
2. Add company_snapshots table
3. Implement weekly snapshot job
4. Add change_events alerting
5. Wire to Slack notifications

---

## Verification

### Automated Tests
```bash
pytest tests/test_signal_correlator.py -v
pytest tests/test_traction_calculator.py -v
pytest tests/test_investor_network.py -v
pytest tests/test_founder_intent.py -v
pytest tests/test_deal_quality_scorer.py -v
```

### Manual Verification
1. Run pipeline with test data
2. Check signal correlation detects known founders
3. Verify traction scores match manual calculation
4. Confirm investor network builds correctly
5. Test deal quality percentile ranking
6. Verify dashboard shows Deal Quality panel

### Integration Check
- Run full pipeline: `python run_pipeline.py full --dry-run`
- Check deal_quality_scores table populated
- Verify routing recommendations match expectations

---

## Our Advantages vs Competitors

| Advantage | vs Harmonic | vs PitchBook | vs Evertrace |
|-----------|-------------|--------------|--------------|
| **Consumer-specific** | Generic model | Generic model | Generic model |
| **Thesis-aware** | No thesis | No thesis | No thesis |
| **Real-time** | Periodic batch | Periodic batch | Daily |
| **US + UK coverage** | US focus | Global | Europe focus |
| **$0 cost** | $500-2K/mo | $10K+/yr | Unknown |
| **Full control** | Black box | Black box | Black box |

---

## Phase 6: Semantic Search (Low-Code Guide-inspired)

**Goal:** Enable natural language queries like "find consumer health startups with strong traction"

### New File: `utils/semantic_search.py`

```python
import openai
from typing import List, Dict

class SemanticSearch:
    """Vector-based semantic search using OpenAI embeddings."""

    def __init__(self, store: SignalStore):
        self.store = store
        self.client = openai.OpenAI()

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding vector for text."""
        response = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding

    async def index_company(self, canonical_key: str, description: str):
        """Generate and store embedding for a company."""
        embedding = await self.generate_embedding(description)
        await self.store.save_embedding(canonical_key, embedding)

    async def search(self, query: str, limit: int = 20) -> List[Dict]:
        """Search companies by natural language query."""
        query_embedding = await self.generate_embedding(query)
        return await self.store.search_by_embedding(query_embedding, limit)
```

### Database Changes
```sql
-- Option A: SQLite with sqlite-vec extension
CREATE VIRTUAL TABLE company_embeddings USING vec0(
    canonical_key TEXT PRIMARY KEY,
    embedding FLOAT[1536]  -- text-embedding-3-small dimension
);

-- Option B: Migrate to Supabase with pgvector
-- CREATE TABLE company_embeddings (
--     canonical_key TEXT PRIMARY KEY,
--     embedding vector(1536)
-- );
-- CREATE INDEX ON company_embeddings USING hnsw (embedding vector_cosine_ops);
```

### Integration
- Generate embeddings after signal consolidation
- Store: company_name + description + thesis_category + why_now
- Search: Natural language queries from dashboard
- Cost: ~$0.02 per 1000 tokens ($20-50/mo at scale)

### Dashboard Enhancement
Add semantic search bar to Signals tab:
```python
query = st.text_input("Search (e.g., 'D2C food brands with viral products')")
if query:
    results = run_async(semantic_search.search(query, limit=20))
    display_results(results)
```

---

## Phase 7: Snapshot-based Change Detection (Low-Code Guide-inspired)

**Goal:** Formalize change detection by comparing weekly data snapshots.

### New File: `utils/change_detector.py`

```python
@dataclass
class ChangeEvent:
    canonical_key: str
    change_type: str  # 'headcount', 'funding', 'product_launch', 'github_spike'
    old_value: Any
    new_value: Any
    change_pct: float
    detected_at: datetime

class ChangeDetector:
    """Detect significant changes by comparing snapshots."""

    THRESHOLDS = {
        'headcount': 0.20,      # 20% change
        'github_stars': 0.50,   # 50% growth
        'job_postings': 0.30,   # 30% increase
    }

    async def detect_changes(self, canonical_key: str) -> List[ChangeEvent]:
        """Compare current vs previous snapshot, return significant changes."""

    async def snapshot_company(self, canonical_key: str):
        """Store current state as snapshot for future comparison."""
```

### Database Changes
```sql
CREATE TABLE company_snapshots (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    headcount INTEGER,
    github_stars INTEGER,
    job_posting_count INTEGER,
    total_funding_usd REAL,
    latest_signal_count INTEGER,
    raw_snapshot TEXT,  -- JSON of all current data
    UNIQUE(canonical_key, snapshot_date)
);

CREATE TABLE change_events (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    change_pct REAL,
    detected_at TEXT NOT NULL,
    notified BOOLEAN DEFAULT FALSE
);
```

### Integration
- Run weekly snapshot job after collection
- Detect changes by comparing current vs previous snapshot
- Alert on significant changes (Slack notification)

---

## Future Enhancements (Post-MVP)

1. **Train ML model** on outcome data (like PitchBook XGBoost)
2. **Add Twitter collector** for co-founder seeking signals
3. **LinkedIn real-time alerts** for career transitions
4. **Expected return calculation** based on historical exits
5. **Harmonic API integration** if traction metrics prove valuable
6. **Browse AI integration** for website content scraping ($200/mo)
7. **Migrate to Supabase** if SQLite becomes limiting (>1M records)

---

## Sources

- [Harmonic.ai](https://harmonic.ai/) - Traction metrics, funding data
- [PitchBook VC Exit Predictor](VC Exit Predictor Technical Documentation.pdf) - ML methodology, eigenvector centrality
- [Evertrace](https://www.evertrace.ai/) - Founder detection, signal correlation
- [Evertrace Data Sources](https://www.evertrace.ai/data-sources) - 9 data source methodology
- [Evertrace GitHub Detection](https://www.evertrace.ai/github) - Behavioral pattern analysis
- [Tech.eu - Evertrace acquires Morphais](https://tech.eu/2025/10/30/vc-sourcing-startup-evertrace-acquires-berlin-based-morphais/) - Behavioral data models
- Low-Code VC Discovery Quick Reference Guide - Semantic search, snapshot-based change detection
