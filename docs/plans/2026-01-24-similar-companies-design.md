# Sprint 4: Similar Companies - Critical Review & Implementation Plan

## Executive Summary

The Codex proposal provides a solid foundation but has **5 critical gaps** that would cause MVP failure. This plan addresses them while maintaining the agreed scope (single embedding, FTS+Gemini hybrid, SQLite+numpy).

---

## Critical Review of Codex Proposal

### Issue 1: FTS Query Construction Gap (CRITICAL)
**Problem:** Design says "Stage 1: FTS5 candidate retrieval" but FTS5 expects **keyword queries**, not document-to-document matching. You can't `MATCH` a 500-word profile against FTS5.

**Fix:** Add explicit keyword extraction step before FTS query:
```python
# Extract 5-10 keywords from profile using simple noun extraction
keywords = extract_search_keywords(profile)  # ["food", "delivery", "consumer", "subscription"]
fts_query = " OR ".join(keywords)  # "food OR delivery OR consumer OR subscription"
```

### Issue 2: Cold Start / Bootstrap Problem (CRITICAL)
**Problem:** If 1000 companies exist with no embeddings, first similarity query requires 300+ Gemini API calls for candidates. This is too slow for real-time use.

**Fix:** Add batch pre-computation job (like `exit_predictor_batch.py`):
- `similar_companies_batch.py` runs nightly
- Embeds all companies with missing/stale embeddings
- Query-time only embeds the query company (1 API call)

### Issue 3: Soft Boost Formula Missing
**Problem:** "Apply soft boosts" mentioned but no mathematical formula specified.

**Fix:** Define explicit formula:
```
final_score = cosine_sim * 0.85 + category_boost * 0.10 + model_boost * 0.05
where:
  category_boost = 1.0 if same_category else 0.0
  model_boost = 1.0 if same_business_model else 0.0
```

### Issue 4: Integration Points Unclear
**Problem:** How does this connect to existing URL Profiler, claims table, dashboard?

**Fix:** Documented in architecture section below.

### Issue 5: Missing Embedding Staleness Strategy
**Problem:** `source_text_hash` detects changes but no job to recompute stale embeddings.

**Fix:** Batch job checks `source_text_hash` against current profile and re-embeds if changed.

---

## Revised Architecture (Sprint 4 MVP)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Similar Companies Flow (Revised)                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Input (URL / canonical_key)                                        │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 1. Get Company Profile                                       │    │
│  │    - From claims table (existing) OR                         │    │
│  │    - Trigger URL Profiler (if URL provided)                  │    │
│  └─────────────────────────────────────────────────────────────┘    │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 2. Build Similarity Text (labeled template)                  │    │
│  │    "Company: {name}\nProblem: {problem}\nCustomer: {...}..."  │    │
│  │    - Compute source_text_hash (SHA256)                       │    │
│  │    - Check thin-profile (< 200 chars)                        │    │
│  └─────────────────────────────────────────────────────────────┘    │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 3. Extract Search Keywords (NEW - fixes Issue 1)             │    │
│  │    - Extract nouns + domain terms (5-10 keywords)            │    │
│  │    - Used for FTS query construction                         │    │
│  └─────────────────────────────────────────────────────────────┘    │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 4. Stage 1: FTS5 Candidate Retrieval                         │    │
│  │    - Query: "keyword1 OR keyword2 OR ..." (not full profile) │    │
│  │    - Retrieve K=300 candidates via BM25                      │    │
│  │    - Optional: narrow to same-category if count >= 50        │    │
│  └─────────────────────────────────────────────────────────────┘    │
│         │                                                           │
│         ▼                                                           │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 5. Stage 2: Embedding Rerank                                 │    │
│  │    - Get/compute query embedding (1 Gemini call)             │    │
│  │    - Load candidate embeddings from cache (pre-computed)     │    │
│  │    - Cosine similarity + soft boosts                         │    │
│  │    - Return top N with match_reasons                         │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  Batch Job (nightly): Pre-compute embeddings for all companies      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Model (Revised)

### A) Company Profiles FTS Table (NEW)

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS company_profiles_fts USING fts5(
    canonical_key UNINDEXED,
    company_name,
    searchable_text,      -- Combined profile text for keyword matching
    category,             -- For optional narrowing
    business_model,       -- For soft boost
    tokenize='porter unicode61'
);
```

**Integration:** Populated when:
- URL Profiler completes (from ProfileExtractionResult)
- Claims updated in claims table (trigger or sync job)

### B) Company Embeddings Table (NEW)

```sql
CREATE TABLE IF NOT EXISTS company_embeddings (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    embedding_kind TEXT NOT NULL DEFAULT 'profile_v1',

    embedding BLOB NOT NULL,              -- numpy float32 bytes (768 dims * 4 bytes = 3KB)
    embedding_model TEXT NOT NULL,        -- 'text-embedding-004'
    embedding_version TEXT NOT NULL,      -- 'v1'

    source_text_hash TEXT NOT NULL,       -- SHA256 of input text
    source_text_preview TEXT,             -- First 512 chars for debugging

    created_at TEXT NOT NULL,
    updated_at TEXT,

    UNIQUE (canonical_key, embedding_kind, embedding_model, embedding_version)
);

CREATE INDEX idx_embeddings_key ON company_embeddings(canonical_key);
CREATE INDEX idx_embeddings_hash ON company_embeddings(source_text_hash);
```

### C) SimilarCompany Result Dataclass

```python
@dataclass
class SimilarCompany:
    canonical_key: str
    company_name: str
    similarity_score: float       # 0.0-1.0 (final score with boosts)
    raw_cosine_score: float       # 0.0-1.0 (raw cosine similarity)
    match_reasons: List[str]      # ["same category", "similar problem", ...]
    business_model: str
    category: str
    profile_url: Optional[str]
```

---

## Components to Build

### 1. KeywordExtractor (`utils/keyword_extractor.py`)
Extracts search keywords from profile text.

```python
class KeywordExtractor:
    def extract(self, profile_text: str, max_keywords: int = 10) -> List[str]:
        """Extract search keywords using simple noun extraction + TF-IDF-like scoring."""
        # 1. Tokenize and normalize
        # 2. Filter stopwords
        # 3. Extract nouns (simple POS tagging or heuristics)
        # 4. Score by frequency + position (early words weighted higher)
        # 5. Return top N
```

### 2. EmbeddingGenerator (`utils/embedding_generator.py`)
Generates Gemini embeddings.

```python
class EmbeddingGenerator:
    MODEL = "text-embedding-004"  # Gemini embedding model (768 dims, free tier)

    async def embed(self, text: str) -> np.ndarray:
        """Generate single embedding via Gemini API."""

    async def embed_batch(self, texts: List[str], batch_size: int = 100) -> List[np.ndarray]:
        """Generate embeddings in batches (for batch job)."""
```

### 3. EmbeddingStore (`storage/embedding_store.py`)
SQLite storage with numpy serialization.

```python
class EmbeddingStore:
    async def save_embedding(
        self, canonical_key: str, embedding: np.ndarray,
        source_text_hash: str, source_text_preview: str
    ) -> int:
        """Save embedding with staleness detection."""

    async def get_embedding(self, canonical_key: str) -> Optional[np.ndarray]:
        """Get cached embedding if not stale."""

    async def get_embeddings_batch(self, canonical_keys: List[str]) -> Dict[str, np.ndarray]:
        """Batch load for candidate ranking."""

    async def get_stale_keys(self, current_hashes: Dict[str, str]) -> List[str]:
        """Find keys where source_text_hash changed."""
```

### 4. ProfileTextBuilder (`utils/profile_text_builder.py`)
Builds embedding input text from profile fields.

```python
class ProfileTextBuilder:
    TEMPLATE = """Company: {company_name}
Problem: {problem_solved}
Customer: {target_customer}
Business model: {business_model}
Category: {category_hints}"""

    def build(self, profile: ProfileExtractionResult) -> str:
        """Build labeled template for embedding."""

    def compute_hash(self, text: str) -> str:
        """SHA256 of input text for staleness detection."""

    def is_thin_profile(self, text: str) -> bool:
        """Check if profile is too sparse (< 200 chars or missing key fields)."""
```

### 5. SimilarityEngine (`utils/similarity_engine.py`)
Main orchestrator.

```python
class SimilarityEngine:
    def __init__(
        self,
        embedding_generator: EmbeddingGenerator,
        embedding_store: EmbeddingStore,
        profile_store: SignalStore,  # Reuse existing
        keyword_extractor: KeywordExtractor,
        profile_text_builder: ProfileTextBuilder,
    ):
        self.k_candidates = 300
        self.n_results = 20
        self.category_boost = 0.10
        self.model_boost = 0.05

    async def find_similar(
        self,
        canonical_key: str,
        n: int = 20,
        category_filter: Optional[str] = None,
    ) -> List[SimilarCompany]:
        """Main entry point: find N similar companies."""
        # 1. Load profile from claims/signals
        # 2. Build similarity text
        # 3. Extract keywords
        # 4. FTS5 candidate retrieval
        # 5. Load candidate embeddings (from cache)
        # 6. Get/compute query embedding
        # 7. Cosine similarity + soft boosts
        # 8. Generate match_reasons
        # 9. Return top N
```

### 6. SimilarCompaniesBatch (`utils/similar_companies_batch.py`)
Nightly batch job.

```python
class SimilarCompaniesBatch:
    async def run(self) -> BatchResult:
        """Pre-compute embeddings for all companies."""
        # 1. Get all canonical_keys from signals + claims
        # 2. Build profile text for each
        # 3. Check against existing embeddings (hash comparison)
        # 4. Batch embed missing/stale profiles
        # 5. Save to embedding_store
        # 6. Return stats (new, updated, skipped)
```

### 7. Dashboard Integration (`dashboard/similar_companies.py`)

Add to URL Profiler page:
- "Find Similar" button after profile result
- Results display with score bars and match reasons

Add to Mini-Scout:
- "Similar Companies" column action
- Modal with similar company cards

---

## Scoring Formula (Explicit)

```python
def compute_final_score(
    cosine_sim: float,          # 0.0-1.0
    same_category: bool,
    same_business_model: bool,
) -> float:
    """
    Weighted combination:
    - 85% cosine similarity (semantic)
    - 10% category match (structural)
    - 5% business model match (structural)
    """
    category_boost = 1.0 if same_category else 0.0
    model_boost = 1.0 if same_business_model else 0.0

    final = (
        cosine_sim * 0.85 +
        category_boost * 0.10 +
        model_boost * 0.05
    )
    return min(1.0, final)  # Cap at 1.0
```

---

## Match Reasons Generation

```python
def generate_match_reasons(
    cosine_sim: float,
    same_category: bool,
    same_model: bool,
    keyword_overlap: List[str],
    thin_profile: bool,
) -> List[str]:
    reasons = []

    if cosine_sim >= 0.78:
        reasons.append("similar problem/customer")
    elif cosine_sim >= 0.65:
        reasons.append("related business area")

    if same_category:
        reasons.append("same category")

    if same_model:
        reasons.append("same business model")

    if keyword_overlap:
        reasons.append(f"keyword overlap: {', '.join(keyword_overlap[:3])}")

    if thin_profile:
        reasons.append("broad search (limited profile)")

    return reasons or ["general similarity"]
```

---

## Relaxation Ladder (Simplified)

```
Step 1: FTS with keywords, narrow to same-category if >= 50 candidates
        → If results >= N, return

Step 2: FTS with keywords, NO category narrowing (global)
        → If results >= N, return

Step 3: Random sample from same category (fallback for thin profiles)
        → Return whatever we have
```

---

## Migration (Migration 8)

```sql
-- Migration 8: Similar Companies tables

-- FTS index for company profiles
CREATE VIRTUAL TABLE IF NOT EXISTS company_profiles_fts USING fts5(
    canonical_key UNINDEXED,
    company_name,
    searchable_text,
    category,
    business_model,
    tokenize='porter unicode61'
);

-- Embedding cache
CREATE TABLE IF NOT EXISTS company_embeddings (
    id INTEGER PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    embedding_kind TEXT NOT NULL DEFAULT 'profile_v1',
    embedding BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_version TEXT NOT NULL,
    source_text_hash TEXT NOT NULL,
    source_text_preview TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    UNIQUE (canonical_key, embedding_kind, embedding_model, embedding_version)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_key ON company_embeddings(canonical_key);
CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON company_embeddings(source_text_hash);
```

---

## Implementation Tasks

### Phase 1: Core Infrastructure (Foundation)
- [ ] **T1.1** Add Migration 8 (FTS + embeddings tables)
- [ ] **T1.2** Create `ProfileTextBuilder` with template + hash + thin-profile check
- [ ] **T1.3** Create `KeywordExtractor` (simple noun extraction)
- [ ] **T1.4** Create `EmbeddingGenerator` (Gemini API wrapper)
- [ ] **T1.5** Create `EmbeddingStore` (CRUD + batch load)

### Phase 2: Similarity Engine
- [ ] **T2.1** Create `SimilarityEngine.find_similar()` orchestrator
- [ ] **T2.2** Implement FTS candidate retrieval with keywords
- [ ] **T2.3** Implement cosine similarity + soft boost scoring
- [ ] **T2.4** Implement match_reasons generation
- [ ] **T2.5** Implement relaxation ladder

### Phase 3: Batch Job
- [ ] **T3.1** Create `SimilarCompaniesBatch` job
- [ ] **T3.2** Add staleness detection (hash comparison)
- [ ] **T3.3** Wire into `run_pipeline.py` CLI

### Phase 4: Dashboard Integration
- [ ] **T4.1** Add "Find Similar" to URL Profiler page
- [ ] **T4.2** Add "Similar Companies" to Mini-Scout
- [ ] **T4.3** Create similar company result cards

### Phase 5: Testing
- [ ] **T5.1** Unit tests for ProfileTextBuilder
- [ ] **T5.2** Unit tests for KeywordExtractor
- [ ] **T5.3** Unit tests for EmbeddingStore
- [ ] **T5.4** Integration tests for SimilarityEngine
- [ ] **T5.5** E2E test with real profiles

---

## Verification Plan

1. **Unit Tests**: Run `pytest tests/utils/test_similarity_*.py`
2. **Integration Test**:
   - Profile 5 known companies via URL Profiler
   - Run batch job to compute embeddings
   - Query similar companies for each
   - Verify results make semantic sense
3. **Dashboard Test**:
   - Profile a consumer CPG company
   - Click "Find Similar"
   - Verify results are relevant (same category, related problems)

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `storage/signal_store.py` | MODIFY | Add Migration 8 |
| `storage/embedding_store.py` | CREATE | Embedding CRUD |
| `utils/profile_text_builder.py` | CREATE | Template builder |
| `utils/keyword_extractor.py` | CREATE | FTS query keywords |
| `utils/embedding_generator.py` | CREATE | Gemini embeddings |
| `utils/similarity_engine.py` | CREATE | Main orchestrator |
| `utils/similar_companies_batch.py` | CREATE | Nightly batch job |
| `dashboard/url_profiler_page.py` | MODIFY | Add "Find Similar" |
| `dashboard/mini_scout.py` | MODIFY | Add similarity column |
| `run_pipeline.py` | MODIFY | Add batch CLI command |

---

## Dependencies

- **numpy**: Vector operations (cosine similarity)
- **google-generativeai**: Gemini embedding API (already installed for LLM classifier)

No new dependencies required.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Cold start slow | Batch job pre-computes; real-time only embeds query |
| Gemini rate limits | Batch job respects 1500 RPM; adds backoff |
| FTS misses semantically similar | Embeddings catch in Stage 2 |
| Thin profiles | Relaxation ladder broadens search |
| Stale embeddings | Hash-based staleness in batch job |
