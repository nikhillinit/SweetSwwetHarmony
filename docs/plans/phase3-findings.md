# Phase 3 Findings: Case-law + Exemplars + Intelligence Visibility

**Created:** 2026-02-09
**Purpose:** Research findings for Phase 3 planning

---

## Finding 1: Existing TF-IDF Infrastructure

**Location:** `utils/ml_thesis_model.py`

The codebase already has a TF-IDF + LogisticRegression pipeline (scikit-learn):
- `TfidfVectorizer(max_features=5000, ngram_range=(1,2), min_df=2, sublinear_tf=True)`
- Model versioning via SHA-256 hash (model_id pattern)
- Save/load via joblib
- `get_feature_importances()` for top features

**Implication:** Phase 3 TF-IDF retrieval for case-law can reuse this pattern directly. No new ML deps needed — scikit-learn already installed.

---

## Finding 2: Existing Embedding Infrastructure

**Locations:**
- `utils/embedding_generator.py` — Gemini text-embedding-004 (768-dim, free tier 1500 RPM)
- `storage/embedding_store.py` — SQLite BLOB storage + FTS5 candidate retrieval
- `utils/similarity_engine.py` — Hybrid FTS5+embedding rerank pipeline

**Architecture:**
1. Stage 1: FTS5 candidate retrieval (K=300)
2. Stage 2: Embedding rerank with cosine similarity
3. Soft boosts: category match (+0.10), business model match (+0.05)

**Implication:** For Phase 3, the plan proposes TF-IDF baseline (lightweight, no API costs). But the embedding infrastructure exists if we want a Phase 3.5 upgrade path.

---

## Finding 3: Label Corpus Size

**Current state:** 31 labels (7 TP, 23 FP, 1 UNSURE)

**Tables:**
- `quality_feedback` — append-only audit trail (label + reason + notes)
- `signal_quality_metrics` — latest resolved label per signal (1:1)

**Key fields for corpus building:**
- `signal_quality_metrics.human_label` — TP/FP/UNSURE
- `signal_quality_metrics.canonical_key` — ties to company identity
- Joins to `signals.raw_data` for text content
- Joins to `functional_schemas` for structured decomposition

**Risk:** 31 labels is small for TF-IDF. min_df=2 may discard too many terms. Need to handle sparse corpus gracefully.

---

## Finding 4: Intelligence Directory

**Location:** `intelligence/` — already exists with domain classification:
- `domain_router.py` — Routes to vertical classifiers (health, travel, saas, consumer)
- `health_classifier.py`, `travel_classifier.py`, `saas_classifier.py`, `consumer_classifier.py`
- `thesis_config.py` — Thesis configuration

**Implication:** Phase 3 files (`case_law_retriever.py`, `exemplar_matcher.py`) should go in `intelligence/` following this established pattern.

---

## Finding 5: Promotion Rules Placeholder

**Location:** `workflows/thin_file_manager.py:13`
```python
# Exemplar similarity is DISABLED until Phase 3.
```

**Current rules (OR logic):**
1. Multi-source: `len(source_apis) >= 2`
2. Trusted source: sec_edgar, companies_house, crunchbase
3. Manual override: `metadata.manual_promotion`

**Task 3.12 will add Rule 4:** Exemplar similarity score >= threshold.

---

## Finding 6: CSV Export Columns (Phase 2 State)

**Current columns (14):**
```
signal_id, company_name, canonical_key, confidence, signal_type, source_api,
detected_at, status, company_id, problem_solved, customer_archetype,
schema_confidence, thesis_category, thesis_rationale
```

**Phase 3 additions (6 new columns → 20 total):**
```
precedent_wins, precedent_losses, similarity_max,
exemplar_match, exemplar_labels, veto_applied
```

**Implementation:** Extend LEFT JOINs to include `precedents` and `thesis_exemplars`.

---

## Finding 7: Triage CLI (Phase 2 State)

**Current columns:**
```
ID | Company | Problem (40 chars) | Archetype | Conf | Source | Status
```

**Verbose mode adds:** detected_at, approach_text, evidence_signal_ids

**Phase 3 addition:** `triage detail <id>` subcommand showing case-law + exemplar matches.

---

## Finding 8: Anti-Pattern Infrastructure

**Location:** `ops/quality/patterns.py`

5 detection strategies, all SQL-based (no ML deps):
1. Source API FP rate concentration
2. Source+category FP rate concentration
3. Duplicate FP descriptions (text normalization)
4. Temporal hotspots (hour-of-day)
5. Weak canonical keys

**Task 3.11 will add:** Propose → approve workflow for anti-patterns affecting routing.

---

## Finding 9: Migration Pattern

**Current:** CURRENT_SCHEMA_VERSION = 32 (v32 = functional_schemas)
**Phase 3 needs:** v33 (precedents) and v34 (thesis_exemplars)

**Registration pattern:** Each migration is a function registered in signal_store.py's migration list.

---

## Finding 10: Vectorizer Versioning Plan

From task_plan_v1.1.md (Task 3.0):
```python
VECTORIZER_METADATA = {
    'version': 'v1.0.0',
    'trained_at': '2026-02-08',
    'corpus_size': 31,
    'vocab_size': 1500,
    'hash': 'sha256:abc123...'
}
# Retrain trigger: if corpus_size > prev * 2
```

Each precedent/exemplar row stores `vectorizer_version` for invalidation tracking.

---

## Finding 11: Script Patterns

**Build scripts follow pattern:**
- `scripts/quality/*.py` for quality ops
- `scripts/gc_thin_files.py` for maintenance
- `scripts/train_thesis_model.py` for ML training

Phase 3 scripts (`build_case_law_corpus.py`, `build_exemplar_library.py`) should follow this pattern.

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Retrieval method | TF-IDF baseline (not embeddings) | No API cost, reuses scikit-learn, corpus too small for dense embeddings |
| Module location | `intelligence/` | Matches existing classification modules |
| Storage | New tables (precedents, thesis_exemplars) | Separates concerns from signal_quality_metrics |
| Vectorizer | joblib serialization with metadata | Matches ml_thesis_model.py pattern |
| Veto logic | High exemplar similarity → cannot auto-drop | Prevents false negatives on thesis-adjacent signals |
| Retrain trigger | corpus_size > 2x previous | Simple, effective growth-based trigger |
| Anti-pattern approval | propose → approve state machine | Prevents automated patterns from silently changing routing |
