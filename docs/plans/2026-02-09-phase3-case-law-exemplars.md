# Phase 3: Case-law + Exemplars + Intelligence Visibility

**Status:** PLANNING (v1.1 — 5 targeted fixes from review)
**Created:** 2026-02-09
**Depends on:** Phase 2 (PR #32 — functional schema + Web3 + intelligence visibility)
**Estimated:** 20-26 hours (14 tasks)
**Branch:** `feature/phase3-case-law-exemplars`
**Findings:** `docs/plans/phase3-findings.md`
**Progress:** `docs/plans/phase3-progress.md`

---

## Goal

Add case-law retrieval (similar TP/FP precedents), exemplar matching (known-good patterns), and an exemplar veto mechanism — then surface all intelligence in CSV/CLI. Activate exemplar similarity in promotion rules (disabled since Phase 1a). Add anti-pattern governance (propose → approve workflow) so automated pattern detection cannot silently change routing.

---

## Plan Invariants

> These assumptions bound the design. Violating any requires revisiting affected tasks.

1. **TF-IDF baseline retrieval.** Phase 3 uses scikit-learn TF-IDF (already installed) for similarity. Embedding-based retrieval (via existing `similarity_engine.py`) is a Phase 3.5+ upgrade path, not Phase 3 scope.
2. **Vectorizer is corpus-global, not per-company.** One TF-IDF vectorizer trained on all labeled signals. Per-company models are not needed at 31-label scale.
3. **Corpus from `signal_quality_metrics` labels.** Labeled signals provide the ground truth for both precedents (TP + FP case-law) and exemplars (TP-only library).
4. **Precedents are precomputed, not live.** Corpus build is a script invocation, not a pipeline-time operation. Retrieval at pipeline time queries precomputed vectors.
5. **Veto is advisory for Phase 3.** Exemplar veto prevents auto-quarantine of high-similarity signals but does NOT override explicit operator rejection. It's a safety net, not an override.
6. **Anti-pattern proposals require human approval.** No automated pattern can change routing without explicit operator approval via CLI.

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Retrieval method | TF-IDF (scikit-learn) | No API cost, reuses installed deps, sufficient for 31-label corpus |
| Vectorizer storage | joblib + metadata JSON | Matches `ml_thesis_model.py` pattern (SHA-256 model_id) |
| Module location | `intelligence/` | Matches existing classification modules (domain_router, health_classifier, etc.) |
| Precedent storage | New `precedents` table (v33) | Separates precomputed vectors from raw labels in `signal_quality_metrics` |
| Exemplar storage | New `thesis_exemplars` table (v34) | Separates curated exemplars from raw precedents |
| Vectorizer versioning | `vectorizer_version` column in both tables | Enables invalidation when corpus grows |
| Retrain trigger | `corpus_size > prev * 2` | Simple growth-based rule, avoids unnecessary rebuilds |
| Veto semantics | Advisory (prevents auto-quarantine, not operator override) | Safety net for false negatives, respects human judgment |
| Anti-pattern governance | Propose → approve state machine | Prevents automated FP patterns from silently changing routing |
| Triage detail | New `triage detail <id>` subcommand | Dedicated view for full intelligence per signal (case-law + exemplar + schema) |
| Sparse corpus handling | `min_df=1` for TF-IDF | 31 labels is small; `min_df=2` would discard too many terms |
| Similarity metric | Cosine similarity on TF-IDF vectors | Standard, interpretable, no hyperparams beyond vectorizer |
| Veto/promotion threshold | 0.75 (initial) | TF-IDF cosine scores tend lower than embedding similarity; calibrate empirically via `--calibrate` before activation |
| Top-K retrieval | K=3 wins + K=3 losses | Balances context without overwhelming operator |

---

## Task Breakdown

### Task 3.0: Vectorizer metadata + versioning config
**File:** `intelligence/vectorizer_config.py` (NEW)
**Est:** 1h

Core config for vectorizer lifecycle:

```python
from dataclasses import dataclass, field
from typing import Optional
import hashlib, json, os

VECTORIZER_DIR = os.environ.get("VECTORIZER_DIR", "models/vectorizers")

@dataclass
class VectorizerMetadata:
    """Tracks vectorizer version for invalidation and audit."""
    version: str                    # e.g. "v1.0.0"
    trained_at: str                 # ISO 8601
    corpus_size: int                # Number of labeled signals used
    corpus_labels: dict             # {"TP": 7, "FP": 23, "UNSURE": 1}
    vocab_size: int                 # TF-IDF vocabulary size
    vectorizer_hash: str            # SHA-256 of serialized vectorizer
    min_df: int = 1                 # TF-IDF min_df used
    max_features: int = 3000        # TF-IDF max_features
    ngram_range: tuple = (1, 2)     # TF-IDF ngram_range

    def should_retrain(self, current_corpus_size: int) -> bool:
        """Retrain if corpus has more than doubled."""
        return current_corpus_size > self.corpus_size * 2

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "VectorizerMetadata": ...
```

Also: helper functions for save/load metadata JSON alongside joblib vectorizer files.

**Tests:**
- VectorizerMetadata serialization round-trip
- `should_retrain()` triggers at 2x corpus size
- `should_retrain()` does not trigger below threshold

---

### Task 3.1: Create `precedents` table (v33 migration)
**File:** `storage/migrations/v33_case_law.py` (NEW)
**Est:** 1.5h

```sql
CREATE TABLE IF NOT EXISTS precedents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    canonical_key TEXT NOT NULL,
    company_id TEXT,
    human_label TEXT NOT NULL CHECK(human_label IN ('TP', 'FP')),
    corpus_text TEXT NOT NULL,             -- Concatenated text used for TF-IDF
    tfidf_vector BLOB,                    -- Serialized sparse vector (scipy CSR)
    similarity_text_hash TEXT,            -- SHA-256 of corpus_text for staleness
    signal_created_at TEXT,              -- Copied from signals.created_at (for staleness, not row age)
    vectorizer_version TEXT NOT NULL,     -- Links to VectorizerMetadata.version
    label_reason TEXT,                    -- From signal_quality_metrics.notes or quality_feedback.reason
    source_api TEXT,                      -- Signal's source_api for context
    confidence REAL,                      -- Signal's original confidence
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(signal_id, vectorizer_version),
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_precedents_label ON precedents(human_label);
CREATE INDEX IF NOT EXISTS idx_precedents_company ON precedents(company_id);
CREATE INDEX IF NOT EXISTS idx_precedents_version ON precedents(vectorizer_version);
```

**Also:** Bump `CURRENT_SCHEMA_VERSION` to 33, register migration in `signal_store.py`.

**Rollback:**
```sql
DROP INDEX IF EXISTS idx_precedents_version;
DROP INDEX IF EXISTS idx_precedents_company;
DROP INDEX IF EXISTS idx_precedents_label;
DROP TABLE IF EXISTS precedents;
```

**Tests:**
- Migration applies cleanly on fresh DB
- Migration applies on v32 DB
- Table exists with correct columns
- UNIQUE constraint enforced on (signal_id, vectorizer_version)
- Indexes created
- FOREIGN KEY cascade on signal deletion

---

### Task 3.2: Build case-law corpus from labeled signals
**Files:** `utils/corpus_text_builder.py` (NEW), `scripts/build_case_law_corpus.py` (NEW)
**Est:** 2.5h

**Step 1 — Shared text builder** (`utils/corpus_text_builder.py`):

Extract `build_corpus_text` into a shared module so that the build script, `CaseLawRetriever`, `ExemplarMatcher`, and CSV export all import from the same source. Follows the same pattern as `utils/ml_text_builder.py` (which carries the comment "This function MUST be used in both training and inference to prevent training/serving skew").

```python
"""Shared text builder for TF-IDF corpus (case-law + exemplars).

This function MUST be used in both corpus building and runtime retrieval
to prevent training/serving skew. Any change here requires corpus rebuild.

Mirrors the pattern in utils/ml_text_builder.py for the ML classifier.
"""
import json
from typing import Optional

def build_corpus_text(
    company_name: str,
    raw_data: str | dict,
    schema_row: Optional[dict] = None,
) -> str:
    """Deterministic text construction for TF-IDF similarity.

    Args:
        company_name: Company name from signals table.
        raw_data: JSON string or dict from signals.raw_data.
        schema_row: Optional dict from functional_schemas table.

    Returns:
        Concatenated, whitespace-normalized text for TF-IDF.
    """
    parts = [company_name or ""]
    if isinstance(raw_data, str):
        raw_data = json.loads(raw_data or "{}")
    parts.append(raw_data.get("description", ""))
    parts.append(raw_data.get("title", ""))
    if schema_row:
        parts.append(schema_row.get("problem_solved_text", "") or "")
        parts.append(schema_row.get("customer_text", "") or "")
        parts.append(schema_row.get("customer_archetype", "") or "")
    return " ".join(p for p in parts if p).strip()
```

**Step 2 — Corpus build script** (`scripts/build_case_law_corpus.py`):

Script that:
1. Reads `signal_quality_metrics` JOIN `signals` for labeled signals (TP + FP only, exclude UNSURE)
   - Also selects `signals.created_at` for each signal (stored as `precedents.signal_created_at`)
2. Builds text corpus via `from utils.corpus_text_builder import build_corpus_text`
   - Also includes `functional_schemas.problem_solved_text` + `customer_archetype` if available
3. Trains TF-IDF vectorizer on full corpus
4. Transforms each labeled signal to TF-IDF sparse vector
5. Saves vectorizer to `models/vectorizers/case_law_v{version}.joblib`
6. Saves metadata to `models/vectorizers/case_law_v{version}_meta.json`
7. Inserts/updates rows in `precedents` table with vector BLOBs
8. **Prunes old-version rows** from `precedents` after successful build

**Old-version pruning (after successful insert):**
```python
def _prune_old_versions(conn, current_version: str) -> int:
    """Delete precedents from superseded vectorizer versions."""
    cursor = conn.execute(
        "DELETE FROM precedents WHERE vectorizer_version != ?",
        (current_version,),
    )
    return cursor.rowcount
```

Called at the end of a successful (non-dry-run) build. Logged as: `"Pruned {n} precedents from old vectorizer versions"`.

**TF-IDF config (tuned for small corpus):**
```python
TfidfVectorizer(
    max_features=3000,      # Reduced from 5000 (small corpus)
    ngram_range=(1, 2),
    min_df=1,               # min_df=1 for 31-label corpus (min_df=2 discards too many)
    sublinear_tf=True,
    strip_accents="unicode",
)
```

**CLI interface:**
```bash
python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0
python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0 --dry-run
python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0 --calibrate
```

**Dry-run mode:** Print corpus stats (text lengths, label distribution, vocabulary size) without writing to DB.

**Calibrate mode (`--calibrate`):** Prints TF-IDF cosine similarity score distributions to help set veto/promotion thresholds empirically:
1. For each labeled signal, compute pairwise similarity against all other labeled signals
2. Partition pairs into: TP-vs-TP, FP-vs-FP, TP-vs-FP
3. Print distribution stats per partition: min, 25th, 50th, 75th, max
4. Suggest threshold based on separation (e.g., 75th percentile of TP-vs-TP)
5. Does not write to DB (read-only)

Example output:
```
Similarity distributions (31 labeled signals):
  TP-vs-TP (21 pairs): min=0.12 p25=0.31 p50=0.45 p75=0.62 max=0.88
  FP-vs-FP (253 pairs): min=0.04 p25=0.15 p50=0.28 p75=0.41 max=0.73
  TP-vs-FP (161 pairs): min=0.02 p25=0.11 p50=0.22 p75=0.35 max=0.61
Suggested veto threshold: 0.62 (75th pctl TP-vs-TP)
```

**Tests:**
- Corpus built from labeled signals (mock DB with TP + FP signals)
- UNSURE signals excluded from corpus
- Text construction includes schema fields when available
- `build_corpus_text` imported from `utils.corpus_text_builder` (not defined locally)
- Vectorizer saved + metadata created
- Precedents rows inserted with correct vectorizer_version
- Precedents rows have `signal_created_at` copied from `signals.created_at`
- Old-version rows pruned after successful build
- Dry-run mode does not write to DB or prune
- Calibrate mode prints TP-vs-TP, FP-vs-FP, TP-vs-FP distributions
- Calibrate mode does not write to DB
- Empty corpus (0 labels) handled gracefully

---

### Task 3.3: TF-IDF case-law retrieval
**File:** `intelligence/case_law_retriever.py` (NEW)
**Est:** 2.5h

Core class for retrieving similar precedents at query time:

```python
class CaseLawRetriever:
    """Retrieves similar TP/FP precedents for a given signal using TF-IDF similarity.

    Usage:
        retriever = CaseLawRetriever(vectorizer_path="models/vectorizers/case_law_v1.0.0.joblib")
        results = retriever.find_similar(query_text, top_k=3)
    """

    def __init__(self, vectorizer_path: str):
        """Load pre-trained TF-IDF vectorizer."""

    def find_similar(
        self,
        query_text: str,
        precedents: List[dict],  # From DB query
        top_k_wins: int = 3,
        top_k_losses: int = 3,
    ) -> CaseLawResult:
        """Find top-K similar wins (TP) and losses (FP).

        1. Transform query_text to TF-IDF vector
        2. Compute cosine similarity against all precedents
        3. Partition by label: TP (wins) vs FP (losses)
        4. Return top-K from each partition
        """

    def find_similar_from_db(
        self,
        query_text: str,
        db_conn,
        vectorizer_version: str,
        top_k_wins: int = 3,
        top_k_losses: int = 3,
    ) -> CaseLawResult:
        """Convenience: load precedents from DB, then find_similar."""
```

**Result dataclass:**
```python
@dataclass
class PrecedentMatch:
    signal_id: int
    canonical_key: str
    company_name: str       # For display
    human_label: str        # "TP" or "FP"
    similarity_score: float # 0.0-1.0
    label_reason: str       # Why it was labeled TP/FP
    source_api: str
    confidence: float       # Original signal confidence

@dataclass
class CaseLawResult:
    wins: List[PrecedentMatch]      # Top-K TP precedents
    losses: List[PrecedentMatch]    # Top-K FP precedents
    max_similarity_tp: float        # Highest similarity among TP (wins)
    max_similarity_fp: float        # Highest similarity among FP (losses)
    vectorizer_version: str
    query_text_length: int
```

**Similarity computation:**
```python
from sklearn.metrics.pairwise import cosine_similarity
import scipy.sparse as sp
import numpy as np

def _compute_similarities(query_vec, precedent_vecs) -> np.ndarray:
    """Cosine similarity between query and all precedents."""
    # query_vec: (1, V) sparse; precedent_vecs: list of (1, V) sparse
    stacked = sp.vstack(precedent_vecs)
    return cosine_similarity(query_vec, stacked).flatten()
```

**Tests:**
- Similar TP signal returns high similarity score
- Dissimilar signal returns low similarity
- Top-K partitioning: wins from TP, losses from FP
- Empty precedents handled gracefully
- Vectorizer version mismatch logged as warning
- Query with empty text returns empty result
- Max similarity per label computed correctly (max_similarity_tp, max_similarity_fp)
- Result sorting by similarity (descending)

---

### Task 3.4: Recency warnings for old precedents
**File:** `intelligence/case_law_retriever.py` (same file, extend)
**Est:** 0.5h

Add recency awareness based on the **original signal timestamp**, not the precedent row insertion time:
```python
@dataclass
class PrecedentMatch:
    # ... existing fields ...
    signal_created_at: str          # Original signal creation date (from signals.created_at)
    is_stale: bool = False          # True if >3 years old

STALE_THRESHOLD_DAYS = 365 * 3     # 3 years
```

When returning matches, compute staleness from `signal_created_at` (NOT `precedents.created_at`, which resets on INSERT OR REPLACE during corpus rebuild):
```python
match.is_stale = (now - match.signal_created_at).days > STALE_THRESHOLD_DAYS
```

Stale precedents are included in results but flagged, so the operator can weigh them appropriately.

**Tests:**
- Recent precedent → `is_stale = False`
- 4-year-old precedent → `is_stale = True`
- Stale precedents still returned (not filtered out)

---

### Task 3.5: Create `thesis_exemplars` table (v34 migration)
**File:** `storage/migrations/v34_exemplars.py` (NEW)
**Est:** 1.5h

```sql
CREATE TABLE IF NOT EXISTS thesis_exemplars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exemplar_key TEXT NOT NULL,             -- Descriptive key: e.g. "creator_economy", "meal_delivery"
    canonical_key TEXT,                     -- Source company canonical_key (NULL for hand-crafted)
    company_name TEXT,                      -- Source company name
    human_label TEXT NOT NULL DEFAULT 'TP', -- Must be TP (exemplars are positive examples)
    category TEXT NOT NULL,                 -- customer_archetype or domain category
    description TEXT NOT NULL,              -- Short description of the exemplar pattern
    corpus_text TEXT NOT NULL,              -- Full text for TF-IDF matching
    tfidf_vector BLOB,                     -- Serialized sparse vector
    vectorizer_version TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',    -- 'auto' (from labels), 'manual' (hand-crafted), 'portfolio' (from fund portfolio)
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(exemplar_key, vectorizer_version)
);
CREATE INDEX IF NOT EXISTS idx_exemplars_category ON thesis_exemplars(category) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_exemplars_version ON thesis_exemplars(vectorizer_version);
CREATE INDEX IF NOT EXISTS idx_exemplars_active ON thesis_exemplars(is_active);
```

**Also:** Bump `CURRENT_SCHEMA_VERSION` to 34, register migration.

**Rollback:**
```sql
DROP INDEX IF EXISTS idx_exemplars_active;
DROP INDEX IF EXISTS idx_exemplars_version;
DROP INDEX IF EXISTS idx_exemplars_category;
DROP TABLE IF EXISTS thesis_exemplars;
```

**Tests:**
- Migration applies cleanly on fresh DB
- Migration applies on v33 DB
- Table exists with correct columns
- UNIQUE constraint on (exemplar_key, vectorizer_version)
- Indexes created

---

### Task 3.6: Build exemplar library from TP labels + portfolio
**File:** `scripts/build_exemplar_library.py` (NEW)
**Est:** 2h

Script that:
1. Reads `signal_quality_metrics` JOIN `signals` WHERE `human_label = 'TP'` (7 signals currently)
2. Optionally reads portfolio entries from a JSON file (`data/portfolio_exemplars.json`)
3. Clusters TP signals by `customer_archetype` (from `functional_schemas`)
4. Generates exemplar entries per archetype cluster
5. Trains shared TF-IDF vectorizer (or reuses case-law vectorizer)
6. Saves to `thesis_exemplars` table

**Exemplar key derivation:**
```python
def derive_exemplar_key(archetype: str, company_name: str) -> str:
    """e.g. 'creators_acme_inc' or 'foodies_beta_corp'"""
    slug = re.sub(r'[^a-z0-9]+', '_', company_name.lower()).strip('_')
    return f"{archetype}_{slug}"
```

**Portfolio exemplar format (optional input):**
```json
[
    {
        "exemplar_key": "meal_delivery_leader",
        "company_name": "HelloFresh",
        "category": "foodies",
        "description": "Meal kit subscription for health-conscious consumers",
        "corpus_text": "HelloFresh meal kit delivery subscription fresh ingredients recipes..."
    }
]
```

**Vectorizer sharing decision:**
- If case-law vectorizer exists → reuse it (same vocabulary space → comparable similarities)
- If not → train dedicated exemplar vectorizer from exemplar corpus only

**CLI interface:**
```bash
python scripts/build_exemplar_library.py --db signals.db --version v1.0.0
python scripts/build_exemplar_library.py --db signals.db --version v1.0.0 --portfolio data/portfolio_exemplars.json
python scripts/build_exemplar_library.py --db signals.db --version v1.0.0 --dry-run
```

**Tests:**
- Exemplar library built from TP labels only (FP excluded)
- Portfolio exemplars loaded from JSON file
- Exemplar keys are unique per archetype+company
- Vectorizer reused from case-law when available
- Dry-run mode prints stats without writing
- 0 TP labels → graceful warning (empty library is valid)

---

### Task 3.7: Exemplar similarity scoring
**File:** `intelligence/exemplar_matcher.py` (NEW)
**Est:** 2.5h

Core class for matching a signal against exemplar library:

```python
class ExemplarMatcher:
    """Scores signal similarity against thesis exemplar library.

    Usage:
        matcher = ExemplarMatcher(vectorizer_path="models/vectorizers/case_law_v1.0.0.joblib")
        result = matcher.match(query_text, exemplars)  # exemplars from DB
    """

    def match(
        self,
        query_text: str,
        exemplars: List[dict],  # From thesis_exemplars table
        threshold: float = 0.5, # Minimum similarity to report
    ) -> ExemplarMatchResult:
        """Find matching exemplars above threshold.

        1. Transform query_text to TF-IDF vector
        2. Compute cosine similarity against all active exemplars
        3. Filter by threshold
        4. Return sorted by similarity (descending)
        """

    def match_from_db(
        self,
        query_text: str,
        db_conn,
        vectorizer_version: str,
        threshold: float = 0.5,
    ) -> ExemplarMatchResult:
        """Convenience: load exemplars from DB, then match."""
```

**Result dataclasses:**
```python
@dataclass
class ExemplarMatch:
    exemplar_key: str         # e.g. "creator_economy"
    category: str             # e.g. "creators"
    description: str          # Human-readable exemplar description
    similarity_score: float   # 0.0-1.0
    company_name: str         # Source company (if from TP label)
    source: str               # "auto", "manual", "portfolio"

@dataclass
class ExemplarMatchResult:
    matches: List[ExemplarMatch]   # All matches above threshold
    best_match: Optional[ExemplarMatch]  # Highest similarity
    max_similarity: float          # Convenience: best match score
    matched_categories: List[str]  # Unique categories matched
    vectorizer_version: str
    veto_eligible: bool            # True if max_similarity >= VETO_THRESHOLD
```

**VETO_THRESHOLD:** Configurable via env var `EXEMPLAR_VETO_THRESHOLD` (default: 0.75).

**Tests:**
- Similar signal returns high similarity
- Dissimilar signal returns no matches
- Threshold filtering works (below threshold excluded)
- Multiple exemplar matches sorted by similarity
- Veto eligibility computed correctly
- Empty exemplar library → empty result (not error)
- Best match populated correctly
- Matched categories unique and sorted

---

### Task 3.8: Exemplar veto logic
**File:** `workflows/semantic_filter.py` (NEW)
**Est:** 1.5h

Semantic filter that integrates exemplar matching into the routing decision:

```python
class SemanticFilter:
    """Advisory filter that prevents high-exemplar-similarity signals
    from being auto-quarantined.

    Does NOT override:
    - Hard disqualifiers (Web3, B2B)
    - Explicit operator rejection
    - Confidence routing thresholds

    DOES prevent:
    - Auto-quarantine of signals matching known-good exemplars
    - Adds "exemplar_veto" to reason chain when active
    """

    def check_veto(
        self,
        signal_data: dict,
        exemplar_result: ExemplarMatchResult,
        current_routing: str,
    ) -> VetoDecision:
        """Check if exemplar similarity should veto auto-quarantine.

        Returns VetoDecision with:
        - veto_applied: bool (True if routing was modified)
        - original_routing: str
        - modified_routing: str (same as original if no veto)
        - reason: str
        - exemplar_match: Optional[ExemplarMatch]
        """
```

**Veto rules:**
1. Only applies when `current_routing` would be "TRACKING" or "HOLD" (not "REJECT" or "SOURCE")
2. Only applies when `exemplar_result.veto_eligible` is True (max_similarity >= 0.75)
3. Upgrades "HOLD" → "TRACKING" (prevents drop, doesn't promote to "SOURCE")
4. "TRACKING" stays "TRACKING" but adds veto flag for operator awareness
5. "REJECT" from hard disqualifiers → veto NOT applied (hard kills override)
6. "SOURCE" → veto NOT applicable (already routed)

**VetoDecision dataclass:**
```python
@dataclass
class VetoDecision:
    veto_applied: bool
    original_routing: str
    modified_routing: str
    reason: str                           # Human-readable explanation
    exemplar_match: Optional[ExemplarMatch] = None
    similarity_score: float = 0.0
```

**Plumbing: persist exemplar similarity for promotion rules (Task 3.12):**

After `check_veto()` computes the exemplar similarity, write the score to `company_files.metadata` so that `_meets_promotion_criteria()` can read it:

1. Add helper `update_company_file_metadata(store, company_id, patch: dict)` — performs a JSON merge into the existing `metadata` column:
   ```python
   def update_company_file_metadata(store, company_id: str, patch: dict):
       """Merge keys into company_files.metadata (JSON column)."""
       row = store.get_company_file(company_id)
       metadata = json.loads(row["metadata"] or "{}") if row else {}
       metadata.update(patch)
       store.conn.execute(
           "UPDATE company_files SET metadata = ? WHERE company_id = ?",
           (json.dumps(metadata), company_id),
       )
   ```
2. Called in pipeline after veto check: `update_company_file_metadata(store, company_id, {"exemplar_similarity_score": exemplar_result.max_similarity})`
3. Score is per-company (max over all signals for that company). If multiple signals exist, use `max()`.
4. `_meets_promotion_criteria()` in `thin_file_manager.py` reads `metadata.exemplar_similarity_score` from the DB row — no additional wiring needed there.

**Tests:**
- HOLD + high similarity → TRACKING (veto applied)
- TRACKING + high similarity → TRACKING (veto logged, no routing change)
- REJECT + high similarity → REJECT (hard kill overrides)
- SOURCE + high similarity → SOURCE (no change needed)
- Low similarity → no veto regardless of routing
- Veto reason includes exemplar_key and similarity score
- Exemplar similarity score persisted to company_files.metadata
- Metadata merge preserves existing keys

---

### Task 3.9: Case-law + exemplars in CSV export
**File:** `run_pipeline.py` (modify `cmd_export_queue`)
**Est:** 1.5h

Extend CSV columns from 14 → 21:

**New columns (appended after existing):**
```
precedent_wins, precedent_losses, similarity_max_tp, similarity_max_fp,
exemplar_match, exemplar_category, veto_applied
```

**Implementation:**
- LEFT JOIN `precedents` grouped by signal's `company_id` (aggregate top matches)
- LEFT JOIN `thesis_exemplars` via precomputed match results
- Actually: precomputed match results need a join table. Simpler approach:
  - **Store match results** in a lightweight `signal_intelligence` cache table (or compute on the fly during export).
  - **Decision: compute on-the-fly** during CSV export (avoid another table). Load vectorizer once, compute per-signal matches. Cache in memory during export.

**On-the-fly computation:**
```python
# During export, optionally compute case-law + exemplar matches
if case_law_retriever:
    for row in rows:
        query_text = build_corpus_text(row)
        case_law = retriever.find_similar_from_db(query_text, conn, version)
        exemplar = matcher.match_from_db(query_text, conn, version)
        # Append to row
```

**Column values:**
| Column | Value | Example |
|--------|-------|---------|
| `precedent_wins` | Top match company names (semicolon-separated) | `WinCo;GoodCorp` |
| `precedent_losses` | Top FP match company names (semicolon-separated) | `FPCorp;BadMatch` |
| `similarity_max_tp` | Highest similarity among TP (wins) | `0.87` |
| `similarity_max_fp` | Highest similarity among FP (losses) | `0.62` |
| `exemplar_match` | Best matching exemplar_key | `creator_economy` |
| `exemplar_category` | Best matching exemplar category | `creators` |
| `veto_applied` | Whether exemplar veto was triggered | `true` / empty |

**Graceful degradation:** If vectorizer not built yet (no `models/vectorizers/` files), new columns are empty strings. No error.

**Tests:**
- CSV export includes new columns (21 total)
- Signals with precedent matches show correct values
- Signals without matches show empty values
- Column count matches header count
- Veto flag set correctly
- Export works when vectorizer not yet built (graceful empty)

---

### Task 3.10: Case-law + exemplars in triage CLI
**File:** `run_pipeline.py` (modify + add `cmd_triage_detail`)
**Est:** 2h

**Part A:** Extend compact list with similarity indicator:
```
   ID  Company                    Problem                              Archetype     Conf  Sim       Source          Status
  --- ------------------------- -----------------------------------  ------------- ----- --------- --------------- --------
  123  Acme Inc                  Creators monetize short-form video   creators      0.82  0.87W/0.62L sec_edgar    pending
  124  Beta Corp                 [no schema]                          —             0.45  —         github          queued
```

**New column:** `Sim` — compact `{tp}W/{fp}L` format showing best TP similarity (W=win) and FP similarity (L=loss), e.g. `0.87W/0.62L`. Shows "—" if no vectorizer built.

**Part B:** New `triage detail <id>` subcommand:
```bash
python run_pipeline.py triage detail 123
```

**Output:**
```
Company: Acme Inc | Confidence: 0.82 | Status: pending
Functional: "Creators monetize short-form video" (creators, 0.91 conf)

Case-law (similar precedents):
  WIN: WinCo (0.87 sim, TP, "consumer creator platform")
  WIN: GoodStart (0.72 sim, TP, "video monetization")
  LOSS: FPCorp (0.45 sim, FP, "B2B video infrastructure")

Exemplar matches:
  creator_economy (0.82 sim, category: creators) — "Creator monetization platform"

Veto: ACTIVE (exemplar similarity 0.82 >= 0.75 threshold)
Decision: TRACKING (reason: consumer_fit + exemplar_veto_active)
```

**CLI registration:**
```python
triage_sub.add_parser("detail", help="Show full intelligence for a signal")
# args: signal_id (positional)
```

**Tests:**
- Triage list shows Sim column
- Triage detail shows case-law matches
- Triage detail shows exemplar matches
- Triage detail shows veto status
- Triage detail for signal with no matches shows "No precedents" / "No exemplar matches"
- Invalid signal ID returns error message

---

### Task 3.11: Anti-pattern propose → approve workflow
**File:** `ops/quality/patterns.py` (modify) + `storage/migrations/v33_case_law.py` (extend)
**Est:** 2h

**New table (in v33 migration alongside precedents):**
```sql
CREATE TABLE IF NOT EXISTS anti_pattern_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,           -- 'source_fp_rate', 'category_fp_rate', 'duplicate_desc', 'temporal', 'weak_key'
    pattern_key TEXT NOT NULL,            -- e.g. 'github:crypto' or 'hour_03_04'
    description TEXT NOT NULL,            -- Human-readable description
    proposed_action TEXT NOT NULL,        -- JSON: {"type": "add_negative_keyword", "keyword": "crypto mining"}
    evidence TEXT NOT NULL,              -- JSON: stats, example signal_ids, etc.
    confidence REAL NOT NULL,            -- Pattern confidence (0.0-1.0)
    status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed', 'approved', 'rejected', 'expired', 'applied')),
    proposed_by TEXT NOT NULL DEFAULT 'system',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at TEXT                      -- Auto-expire after N days if not reviewed
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_proposals_one_active
    ON anti_pattern_proposals(pattern_type, pattern_key)
    WHERE status IN ('proposed', 'approved', 'applied');
CREATE INDEX IF NOT EXISTS idx_proposals_status ON anti_pattern_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_type ON anti_pattern_proposals(pattern_type);
```

**CLI commands (extend quality CLI):**
```bash
# List pending proposals
python -m ops.cli quality proposals --status proposed

# Approve a proposal
python -m ops.cli quality approve-proposal 42 --notes "Confirmed by operator"

# Reject a proposal
python -m ops.cli quality reject-proposal 42 --notes "False pattern"
```

**Workflow:**
1. `detect_patterns()` (existing) → generates proposals with status `proposed`
2. Operator reviews via CLI → approves or rejects
3. Only approved patterns feed into routing adjustments (see Enforcement below)
4. Proposals expire after 30 days if not reviewed (status → `expired`)

**Enforcement path (how approved proposals take effect):**

Approved proposals are consumed based on their `proposed_action.type`:

| Action type | Enforcement mechanism | Automated? |
|---|---|---|
| `add_negative_keyword` | Applied via existing `tuning-proposal-apply` skill (appends to v2 negative keywords in `hard_disqualifiers.py`) | Yes — operator runs `quality apply-proposal <id>` |
| `add_domain_blacklist` | Operator manually adds domain to suppression cache via `run_pipeline.py push --suppress` | Manual (Phase 3) |
| `adjust_threshold` | Operator manually updates env var / config | Manual (Phase 3) |

For Phase 3, keyword-type proposals get a dedicated CLI apply command:
```bash
# Apply an approved keyword proposal (writes to negative_keyword_policy)
python -m ops.cli quality apply-proposal 42
# Dry-run: show what would change
python -m ops.cli quality apply-proposal 42 --dry-run
```

`apply-proposal` validates: (a) proposal status is `approved`, (b) action type is supported for auto-apply, (c) keyword doesn't already exist. On success, updates proposal status to `applied` (new terminal state).

Automated enforcement for non-keyword action types (domain blacklists, threshold adjustments) is deferred to Phase 4+ as a `PatternEnforcer` that loads approved proposals at pipeline startup.

**Tests:**
- Proposal created from detected pattern
- Proposal approved → status updated
- Proposal rejected → status updated
- Duplicate proposal (same type+key) blocked while any active status exists (proposed/approved/applied)
- Second proposal allowed after first is rejected or expired (partial index permits it)
- Expired proposals auto-detected
- `apply-proposal` adds keyword to negative_keyword_policy
- `apply-proposal` rejects non-approved proposals
- `apply-proposal` dry-run shows diff without writing
- `apply-proposal` sets status to `applied`

---

### Task 3.12: Activate exemplar similarity in promotion rules
**File:** `workflows/thin_file_manager.py` (modify)
**Est:** 1h

Add Rule 4 to `_meets_promotion_criteria`:

```python
def _meets_promotion_criteria(
    source_apis: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Check if a company file meets promotion criteria.

    Rules (OR logic): (multi_source OR trusted) OR manual OR exemplar
      1. Multi-source: 2+ distinct source APIs
      2. Trusted source: any source in TRUSTED_SOURCES
      3. Manual: metadata.manual_promotion is True
      4. Exemplar similarity: metadata.exemplar_similarity_score >= threshold (Phase 3)
    """
    # Rule 3: Manual override
    if metadata and metadata.get("manual_promotion"):
        return True

    # Rule 1: Multi-source verification
    if len(source_apis) >= 2:
        return True

    # Rule 2: Trusted source
    if any(s in TRUSTED_SOURCES for s in source_apis):
        return True

    # Rule 4: Exemplar similarity (Phase 3)
    exemplar_threshold = float(os.environ.get("EXEMPLAR_PROMOTION_THRESHOLD", "0.75"))
    if metadata and metadata.get("exemplar_similarity_score", 0) >= exemplar_threshold:
        return True

    return False
```

**Also:** Update module docstring (remove "DISABLED until Phase 3" comment, replace with actual rule).

**Tests:**
- Exemplar similarity >= 0.75 → promoted
- Exemplar similarity < 0.75 → not promoted (unless other rules match)
- Threshold configurable via env var
- Existing promotion tests still pass (no regression)

---

### Task 3.13: Retrain trigger (corpus > 2x → auto-rebuild)
**File:** `intelligence/vectorizer_config.py` (extend) + `scripts/build_case_law_corpus.py` (extend)
**Est:** 1h

Add retrain check to corpus build script:

```python
def check_retrain_needed(db_path: str, vectorizer_dir: str) -> bool:
    """Check if vectorizer needs retraining.

    Returns True if:
    1. No vectorizer exists (first build)
    2. Current corpus size > 2x the trained corpus size
    """
    metadata = load_latest_metadata(vectorizer_dir)
    if metadata is None:
        return True

    current_corpus_size = count_labeled_signals(db_path)
    return metadata.should_retrain(current_corpus_size)
```

**Also:** Add `--check-only` flag to build script:
```bash
# Just check if retrain is needed (for scheduling)
python scripts/build_case_law_corpus.py --db signals.db --check-only
# Output: "Retrain needed: corpus grew from 31 to 65 (>2x)" or "No retrain needed"
```

**Scheduling integration:**
```bash
# Can be added to quality scheduler
python -m ops.cli schedule create quality-retrain --cron "0 4 * * 0"
# Weekly check + rebuild if needed
```

**Tests:**
- No vectorizer → retrain needed
- Corpus doubled → retrain needed
- Corpus grew by 50% → retrain NOT needed
- `--check-only` flag prints status without writing

---

## Dependency Graph

```
Task 3.0 (Vectorizer config) ────────────────────────────────────┐
                                                                  ▼
Task 3.1 (v33 migration) ──► Task 3.2 (Corpus build) ──► Task 3.3 (Retrieval)
                         │                                   │
                         │                                   ├──► Task 3.4 (Recency)
                         │                                   │
                         └──► Task 3.11 (Anti-pattern proposals)
                                                                  │
Task 3.5 (v34 migration) ──► Task 3.6 (Exemplar build) ──► Task 3.7 (Matching)
                                                                  │
                                                  ┌───────────────┤
                                                  ▼               ▼
                                      Task 3.8 (Veto) ──► Task 3.12 (Promotion rules)
                                                  │
                              ┌───────────────────┤
                              ▼                   ▼
                      Task 3.9 (CSV)       Task 3.10 (Triage CLI)
                                                  │
                                                  ▼
                                          Task 3.13 (Retrain trigger)
```

**Critical path:** 3.0 → 3.1 → 3.2 → 3.3 → 3.9/3.10 (case-law chain)
**Parallel track A:** 3.5 → 3.6 → 3.7 → 3.8 (exemplar chain, parallel with case-law after 3.0)
**Parallel track B:** 3.11 (anti-pattern proposals, depends on 3.1 only)
**Final integration:** 3.9, 3.10, 3.12, 3.13 (depends on both chains)

---

## Task Execution Order

| Order | Task | Rationale |
|-------|------|-----------|
| 1 | 3.0 | Vectorizer config — everything depends on versioning |
| 2 | 3.1 | v33 migration — precedents + proposals DDL |
| 3 | 3.5 | v34 migration — exemplars DDL (parallel with corpus build) |
| 4 | 3.2 | Build case-law corpus — depends on 3.0 + 3.1 |
| 5 | 3.6 | Build exemplar library — depends on 3.0 + 3.5 |
| 6 | 3.3 | Case-law retrieval — depends on 3.2 |
| 7 | 3.4 | Recency warnings — extends 3.3 |
| 8 | 3.7 | Exemplar matching — depends on 3.6 |
| 9 | 3.8 | Exemplar veto — depends on 3.7 |
| 10 | 3.11 | Anti-pattern proposals — depends on 3.1 (DDL) |
| 11 | 3.12 | Promotion rule activation — depends on 3.7 |
| 12 | 3.9 | CSV export extension — depends on 3.3 + 3.7 |
| 13 | 3.10 | Triage CLI extension — depends on 3.3 + 3.7 + 3.8 |
| 14 | 3.13 | Retrain trigger — depends on 3.0 + 3.2 |

---

## Environment Variables (New)

| Variable | Default | Purpose |
|----------|---------|---------|
| `VECTORIZER_DIR` | `models/vectorizers` | Directory for vectorizer joblib + metadata files |
| `EXEMPLAR_VETO_THRESHOLD` | `0.75` | Minimum exemplar similarity for veto eligibility |
| `EXEMPLAR_PROMOTION_THRESHOLD` | `0.75` | Minimum exemplar similarity for promotion rule |
| `CASE_LAW_TOP_K` | `3` | Number of top wins/losses to retrieve |
| `ANTI_PATTERN_EXPIRY_DAYS` | `30` | Days before unreviewed proposals expire |

---

## Files Created/Modified

### New Files
| File | Purpose |
|------|---------|
| `utils/corpus_text_builder.py` | Shared text builder for TF-IDF (case-law + exemplars) |
| `intelligence/vectorizer_config.py` | Vectorizer metadata, versioning, retrain trigger |
| `intelligence/case_law_retriever.py` | TF-IDF case-law retrieval |
| `intelligence/exemplar_matcher.py` | Exemplar similarity scoring |
| `workflows/semantic_filter.py` | Exemplar veto logic |
| `storage/migrations/v33_case_law.py` | precedents + anti_pattern_proposals DDL |
| `storage/migrations/v34_exemplars.py` | thesis_exemplars DDL |
| `scripts/build_case_law_corpus.py` | Corpus build script |
| `scripts/build_exemplar_library.py` | Exemplar library build script |
| `tests/intelligence/test_vectorizer_config.py` | Vectorizer config tests |
| `tests/intelligence/test_case_law_retriever.py` | Case-law retrieval tests |
| `tests/intelligence/test_exemplar_matcher.py` | Exemplar matching tests |
| `tests/workflows/test_semantic_filter.py` | Veto logic tests |
| `tests/storage/test_v33_case_law.py` | v33 migration tests |
| `tests/storage/test_v34_exemplars.py` | v34 migration tests |
| `tests/scripts/test_build_corpus.py` | Corpus build script tests |
| `tests/scripts/test_build_exemplars.py` | Exemplar build script tests |
| `tests/cli/test_triage_detail.py` | Triage detail CLI tests |
| `tests/cli/test_csv_export_caselaw.py` | CSV export extension tests |
| `tests/integration/test_phase3_intelligence.py` | End-to-end Phase 3 tests |
| `tests/performance/test_phase3_slos.py` | Performance SLO tests |

### Modified Files
| File | Change |
|------|--------|
| `storage/signal_store.py` | +v33/v34 migration registration, bump version to 34 |
| `workflows/thin_file_manager.py` | +exemplar similarity promotion rule (Rule 4) |
| `ops/quality/patterns.py` | +proposal creation from detected patterns |
| `run_pipeline.py` | +CSV export columns, +triage detail subcommand, +triage list Sim column |

---

## Success Criteria

- [ ] Case-law corpus built from 31 labeled signals (7 TP + 23 FP, UNSURE excluded)
- [ ] TF-IDF retrieval surfaces top-3 similar wins and top-3 similar losses
- [ ] Precedents older than 3 years flagged as stale (not filtered)
- [ ] Exemplar library created from 7 TP labels
- [ ] Exemplar veto prevents auto-quarantine when similarity >= 0.75
- [ ] Exemplar veto does NOT override hard disqualifiers (Web3, B2B)
- [ ] Exemplar similarity activated in promotion rules (Task 3.12)
- [ ] Anti-pattern proposals require human approval (propose → approve workflow)
- [ ] CSV export includes 7 new columns (precedent_wins, precedent_losses, similarity_max_tp, similarity_max_fp, exemplar_match, exemplar_category, veto_applied)
- [ ] Triage list shows Sim column
- [ ] Triage detail subcommand shows full intelligence per signal
- [ ] Vectorizer versioned with retrain trigger (corpus > 2x)
- [ ] All existing tests pass (zero regressions)
- [ ] ~72 new tests for Phase 3 code
- [ ] Performance: TF-IDF retrieval < 100ms per signal
- [ ] Performance: CSV export < 3s for 500 signals with all JOINs
- [ ] Graceful degradation: all intelligence columns empty when vectorizer not built

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Small corpus (31 labels) | Use min_df=1, max_features=3000; TF-IDF handles small corpora well |
| Sparse TF-IDF vectors | Cosine similarity on sparse matrices is cheap; no dense conversion needed |
| Vectorizer drift | Versioning column in both tables; retrain trigger at 2x corpus growth |
| Exemplar veto over-promotion | Veto is advisory (HOLD→TRACKING only); cannot override hard disqualifiers |
| Anti-pattern false detections | Propose→approve gate; no automated routing changes without human review |
| CSV export latency | On-the-fly computation cached in memory during export; SLO < 3s |
| Missing vectorizer files | Graceful degradation: empty columns in CSV/CLI, no error |
| scikit-learn import cost | Lazy import pattern (import inside function, not at module level) |
| Stale precedents misleading | Recency flag (is_stale) shown in CLI; operator can weigh appropriately |

---

## Post-Phase 3 Validation

After all 14 tasks complete:
1. Run full test suite (target: 1012+ tests, 0 failures)
2. Build case-law corpus: `python scripts/build_case_law_corpus.py --db signals.db --version v1.0.0`
3. Build exemplar library: `python scripts/build_exemplar_library.py --db signals.db --version v1.0.0`
4. Run pipeline with functional schema + intelligence in dry-run mode
5. Export CSV and verify 21 columns present with case-law + exemplar data
6. Run `triage list` and verify Sim column
7. Run `triage detail <id>` and verify full intelligence output
8. Verify anti-pattern proposals:
   - Detect patterns → proposals created with status 'proposed'
   - Approve → status updated
   - Only approved proposals affect routing
9. Verify exemplar veto:
   - High-similarity HOLD signal → vetoed to TRACKING
   - High-similarity REJECT signal → NOT vetoed (hard kill)
10. Governance lint passes
11. Performance: `pytest tests/performance/test_phase3_slos.py`
