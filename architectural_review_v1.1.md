# Discovery Engine v1.1 — Senior Architect Review

**Reviewer:** Senior Architect Skill
**Date:** 2026-02-08
**Review Scope:** Discovery Engine v1.1 Implementation Plan
**Focus Areas:** Architectural soundness, scalability, technical debt, maintainability

---

## Executive Summary

**Overall Assessment: ✅ APPROVED WITH MINOR RECOMMENDATIONS**

The Discovery Engine v1.1 architecture is **well-designed** and demonstrates strong engineering principles. The phased adoption strategy is sound, risk mitigation is comprehensive, and the separation of concerns is excellent. The plan successfully balances immediate value delivery with long-term architectural quality.

**Key Strengths:**
- ✅ Clean separation of concerns (delivery policy vs decision logic)
- ✅ Incremental adoption with rollback paths
- ✅ Comprehensive safety mechanisms (confidence gating, exemplar veto, canaries)
- ✅ Non-breaking changes (existing systems continue working)
- ✅ Runtime resilience (works without LLM via fallbacks)

**Areas for Attention:**
- ⚠️ Database migration strategy needs more detail
- ⚠️ TF-IDF vectorizer versioning/evolution path unclear
- ⚠️ Thin files 60-day archive policy needs GC consideration
- ⚠️ Phase dependencies could cause cascading delays
- ⚠️ No explicit performance benchmarks or SLOs

**Recommendation:** Proceed with v1.1 implementation. Address flagged concerns in Phase 0-1.

---

## 1. Architectural Soundness

### 1.1 Separation of Concerns ✅ EXCELLENT

**Decision vs Delivery Policy Layer** (Phase 0)

```python
# v1.1 approach (CORRECT)
decision = AUTO_PUSH | NEEDS_REVIEW | HOLD | REJECT  # What system recommends
delivery_action = assert_notion_write_allowed(...)    # What actions are allowed
```

**Why this is excellent:**
- Single Responsibility Principle: Decision logic separate from delivery enforcement
- Open/Closed Principle: Can add new decisions without changing delivery rules
- Testability: Can test decision logic and delivery policy independently
- Evolvability: Can change `DELIVERY_MODE` without touching decision code

**Baseline anti-pattern (avoided):**
```python
# Baseline proposal (WRONG)
AUTO_PUSH → REVIEW_QUEUE  # Mutates semantics, couples concerns
```

**Verdict:** ✅ Textbook example of clean architecture.

---

### 1.2 Identity Management ✅ SOUND WITH MINOR CONCERN

**Dual-identifier pattern** (Phase 1):
- `company_id`: Immutable UUID (internal primary key)
- `canonical_key`: Deterministic upsert key (can evolve via merges)

**Strengths:**
- Handles rebrands, domain changes, stealth companies
- Merge operation with alias tracking
- Deterministic algorithm (domain-based preferred, name-based fallback)

**Concern ⚠️: Canonical key collision handling**

The plan mentions collision strategy:
```
n:<name>::<namespace>#<hash(source_item_id or url_host)>
```

**Issue:** Hash function not specified. Recommendations:
1. Use SHA-256 (first 8 chars) for collision discriminator
2. Document maximum collision budget (e.g., handle up to 1000 collisions per name)
3. Add monitoring alert if collision rate > 1%

**Verdict:** ✅ Sound design, add hash specification in Phase 1.

---

### 1.3 State Machine Design ✅ ROBUST

**ReviewItem lifecycle** (Phase 1):
```
pending → approved | rejected | deferred
approved → published (optional: publish_queued)
```

**Strengths:**
- Constrained transitions (no arbitrary state jumps)
- Audit trail via state changes
- Cooldown logic for rejected items

**Concern ⚠️: No explicit state machine enforcement**

Recommendation: Add state transition validation:
```python
VALID_TRANSITIONS = {
    'pending': {'approved', 'rejected', 'deferred'},
    'approved': {'published', 'publish_queued'},
    'deferred': {'pending'},  # Can re-evaluate
    'rejected': set(),  # Terminal (unless cooldown elapsed)
    'published': set()  # Terminal
}

def _validate_transition(from_state, to_state):
    if to_state not in VALID_TRANSITIONS.get(from_state, set()):
        raise InvalidStateTransitionError(...)
```

**Verdict:** ✅ Solid design, add explicit transition validation.

---

### 1.4 Thin Files Pattern ✅ INNOVATIVE

**CompanyFile accumulator** (Phase 1):
- Status: `thin | promoted | archived`
- Promotion rules: 2+ sources OR trusted source OR high exemplar similarity
- 60-day archive, 5-10% sampling

**Strengths:**
- Solves sparse signal problem without queue bloat
- Sampling prevents false negatives
- Configurable promotion rules

**Concern ⚠️: Garbage collection strategy**

60-day archive creates growing storage:
- 100 thin files/week × 52 weeks = 5,200 archived records/year
- No plan for purging old archived files

**Recommendation:**
1. Add `archived_at` timestamp
2. Purge archived files > 1 year old (or configurable threshold)
3. Add `THIN_FILE_ARCHIVE_RETENTION_DAYS` env var
4. Create maintenance script: `scripts/gc_thin_files.py`

**Verdict:** ✅ Excellent pattern, add GC in Phase 1.

---

## 2. Scalability Concerns

### 2.1 Database Performance ⚠️ NEEDS ATTENTION

**Current state:** SQLite with WAL mode, 26 migrations, ~712 tests passing

**Phase 1 adds 3 new tables:**
- `company_aliases`
- `ReviewItem`
- `CompanyFile`

**Concern: No performance benchmarks or SLOs**

At scale:
- 10,000 signals/month = 120K signals/year
- 100 CompanyFiles promoted/week = 5,200 ReviewItems/year
- TF-IDF retrieval on 5,200 precedents = O(n) linear scan

**Recommendations:**

1. **Add indexes immediately (Phase 1):**
```sql
-- company_aliases
CREATE INDEX idx_aliases_company_id ON company_aliases(company_id);
CREATE INDEX idx_aliases_canonical_key ON company_aliases(canonical_key);

-- ReviewItem
CREATE INDEX idx_review_company_id ON ReviewItem(company_id);
CREATE INDEX idx_review_status ON ReviewItem(status);
CREATE INDEX idx_review_created_at ON ReviewItem(created_at);

-- CompanyFile
CREATE INDEX idx_company_file_status ON CompanyFile(status);
CREATE INDEX idx_company_file_last_seen ON CompanyFile(last_seen_at);
CREATE INDEX idx_company_file_distinct_sources ON CompanyFile(distinct_source_count);
```

2. **Define SLOs:**
- CSV export < 2s for 500 ReviewItems
- Manual push < 1s per signal
- Thin file promotion check < 500ms
- Case-law retrieval < 1s (top 10 results)

3. **Add EXPLAIN ANALYZE tests:**
```python
def test_review_queue_query_performance():
    # Seed 1000 ReviewItems
    start = time.perf_counter()
    items = await store.get_review_queue(limit=50)
    duration = time.perf_counter() - start
    assert duration < 0.5, f"Query too slow: {duration}s"
```

**Verdict:** ⚠️ Add indexes + SLOs in Phase 1, benchmark in Phase 2.

---

### 2.2 TF-IDF Vectorizer Scalability ✅ SOUND WITH CAVEAT

**Phase 3 introduces TF-IDF for:**
- Case-law retrieval (precedents)
- Exemplar similarity (thesis exemplars)

**Current approach:**
```
Builder mode: Train vectorizer on full corpus
Runtime mode: Use frozen vectorizer (no retraining)
```

**Strengths:**
- Works without LLM
- Deterministic retrieval
- Fast inference (pre-built vectors)

**Concern ⚠️: Vectorizer evolution path unclear**

Scenarios:
1. Corpus grows 10x (31 labels → 300 labels): Does vectorizer need retraining?
2. Vocabulary drift: New terms appear ("Web3" in 2021, "AI agents" in 2024)
3. Version compatibility: How to handle vectorizer v1 → v2 migration?

**Recommendations:**

1. **Version vectorizers:**
```python
vectorizer_metadata = {
    'version': 'v1.0.0',
    'trained_at': '2026-02-08',
    'corpus_size': 31,
    'vocab_size': 1500,
    'min_df': 2,
    'max_df': 0.8
}
```

2. **Add retrain trigger:**
```python
# In builder mode
if corpus_size > prev_corpus_size * 2:  # Doubled since last train
    retrain_vectorizer()
```

3. **Support multiple vectorizer versions:**
```python
# precedents table
ALTER TABLE precedents ADD COLUMN vectorizer_version TEXT;
```

4. **Graceful fallback:**
```python
if vectorizer_version_mismatch:
    logger.warning("Vectorizer version mismatch, using keyword fallback")
    return keyword_based_retrieval(query)
```

**Verdict:** ✅ Sound approach, add versioning in Phase 3.

---

### 2.3 Batch Publish Atomicity ✅ WELL-DESIGNED

**Phase 4 batch publish workflow:**
```
create → preview → commit → (all succeed or all fail)
```

**Strengths:**
- Git-style preview before commit
- Atomic batch (transaction semantics)
- Per-item logging for diagnostics

**Concern ⚠️: Partial failure compensation**

Plan mentions "compensating actions" but doesn't specify:
- What if 7 of 10 items pushed, then Notion API fails on item 8?
- Rollback requires Notion API to support delete/update reversal
- Notion API is eventually consistent (no ACID guarantees)

**Recommendation:**

1. **Document compensation limits:**
```python
# In docs/batch_publish.md
Compensation Strategy:
- Best-effort only (Notion API is eventually consistent)
- Stores pre-images for manual rollback
- Alerts operator on partial failures
- Does NOT guarantee atomic rollback
```

2. **Add idempotency keys:**
```python
# publish_batches table
idempotency_key = f"batch_{batch_id}_item_{signal_id}_{timestamp}"
```

3. **Store pre-images:**
```sql
CREATE TABLE publish_pre_images (
    batch_id TEXT NOT NULL,
    signal_id INTEGER NOT NULL,
    notion_page_id TEXT,
    snapshot_json TEXT,  -- Full Notion page before write
    created_at TEXT NOT NULL
);
```

**Verdict:** ✅ Good design, clarify compensation limits in Phase 4.

---

## 3. Technical Debt Assessment

### 3.1 Migration Strategy ⚠️ NEEDS DETAIL

**Plan adds 6 new migrations (v27-v32):**
- v27: Canonical identity (`company_id`, `canonical_key`, `company_aliases`)
- v28: ReviewItem + CompanyFile
- v29: Batch publish scaffolding
- v30: Functional schemas
- v31: Precedents (case-law)
- v32: Thesis exemplars

**Current migration system:**
```python
CURRENT_SCHEMA_VERSION = 26
MIGRATIONS = {1: "...", 2: "...", ...}
```

**Concern ⚠️: No backfill strategy for v27**

Phase 1.1 adds `company_id` + `canonical_key` to **existing signals table**.

**Issue:** 47 existing signals need backfilled values:
```sql
ALTER TABLE signals ADD COLUMN company_id TEXT;
ALTER TABLE signals ADD COLUMN canonical_key TEXT;

-- Backfill???
UPDATE signals SET company_id = ??? WHERE company_id IS NULL;
```

**Recommendations:**

1. **Add backfill script:**
```python
# storage/migrations/backfill_v27_company_identity.py
async def backfill_company_identity(db_path):
    async with aiosqlite.connect(db_path) as conn:
        # For each signal missing company_id
        signals = await conn.execute(
            "SELECT id, company_name, raw_data FROM signals WHERE company_id IS NULL"
        )
        for signal_id, company_name, raw_data in signals:
            # Extract domain from raw_data
            domain = extract_domain(json.loads(raw_data))
            canonical_key = build_canonical_key(domain, company_name)
            company_id = str(uuid.uuid4())

            await conn.execute(
                "UPDATE signals SET company_id=?, canonical_key=? WHERE id=?",
                (company_id, canonical_key, signal_id)
            )
```

2. **Add dry-run mode:**
```bash
python storage/migrations/backfill_v27_company_identity.py --db signals.db --dry-run
# Shows what would change

python storage/migrations/backfill_v27_company_identity.py --db signals.db
# Applies changes
```

3. **Add validation checks:**
```python
def validate_v27_migration():
    # After backfill
    assert all(signal.company_id is not None for signal in signals)
    assert all(signal.canonical_key is not None for signal in signals)
    assert len(set(canonical_keys)) >= 0.9 * len(signals)  # Allow 10% dupes
```

**Verdict:** ⚠️ BLOCKER - Add backfill script in Phase 1 Task 1.1.

---

### 3.2 Functional Schema Storage ⚠️ DESIGN QUESTION

**Phase 2 adds `functional_schemas` table:**
```sql
CREATE TABLE functional_schemas (
    company_id TEXT,
    problem_solved_text TEXT,
    customer_text TEXT,
    approach_text TEXT,
    customer_archetype TEXT,
    problem_archetypes TEXT,  -- JSON array
    schema_confidence REAL,
    evidence_spans TEXT,  -- JSON
    created_at TEXT,
    updated_at TEXT
);
```

**Concern ⚠️: One schema per company or versioned history?**

**Scenario 1: Company evolves**
```
2024-01: "AI video editing for developers"
2024-06: "AI video editing for TikTok creators"
→ Same company, different functional schema
```

**Should we:**
- A) Overwrite schema (lose history)?
- B) Store all versions (audit trail)?

**Recommendation:**

Add versioning to functional_schemas:
```sql
CREATE TABLE functional_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    problem_solved_text TEXT,
    -- ... other fields ...
    is_active BOOLEAN DEFAULT 1,  -- Only one active per company
    superseded_by INTEGER,  -- Reference to newer version
    created_at TEXT NOT NULL,
    UNIQUE(company_id, schema_version)
);

CREATE INDEX idx_functional_schema_company_active
    ON functional_schemas(company_id, is_active);
```

**Query patterns:**
```python
# Get current schema
SELECT * FROM functional_schemas
WHERE company_id = ? AND is_active = 1;

# Get full history
SELECT * FROM functional_schemas
WHERE company_id = ?
ORDER BY schema_version ASC;
```

**Verdict:** ⚠️ Add versioning decision to Phase 2 Task 2.1.

---

### 3.3 Test Coverage ✅ STRONG

**Current state:**
- 712 tests passing (ops: 441, API: 80, dashboard: 74, etc.)
- 85 new ML tests (76 pass, 9 need scikit-learn)

**Phase 0-3 adds ~200 new tests:**
- Delivery policy: 15 tests
- Canonical identity: 20 tests
- ReviewItem state machine: 25 tests
- Thin files: 20 tests
- Functional schema: 15 tests
- Web3 co-occurrence: 10 tests
- Case-law retrieval: 20 tests
- Exemplar library: 15 tests
- Batch publish: 25 tests

**Estimated total: 912 tests**

**Recommendations:**

1. **Add integration tests:**
```python
# tests/integration/test_v1_1_end_to_end.py
async def test_phase_0_delivery_policy_blocks_auto_push():
    # Set DELIVERY_MODE=staging_only
    # Create signal with AUTO_PUSH decision
    # Verify Notion write blocked
    # Verify CSV export includes signal

async def test_phase_1_thin_file_promotion():
    # Create 2 signals for same company (different sources)
    # Verify CompanyFile created
    # Verify promotion to ReviewItem

async def test_phase_3_exemplar_veto():
    # Add TP exemplar for "creators"
    # Create signal similar to exemplar
    # Verify cannot be auto-quarantined
```

2. **Add performance benchmarks:**
```python
# tests/performance/test_scalability.py
@pytest.mark.slow
async def test_review_queue_1000_items():
    # Seed 1000 ReviewItems
    # Measure query time, memory usage
    assert query_time < 1.0
    assert memory_delta < 50_000_000  # 50MB
```

**Verdict:** ✅ Strong test culture, add integration + perf tests.

---

## 4. Maintainability & Evolvability

### 4.1 Code Organization ✅ EXCELLENT

**Module structure:**
```
workflows/
  ├─ delivery_policy.py      # Phase 0 (new)
  ├─ thin_file_manager.py    # Phase 1 (new)
  ├─ semantic_filter.py      # Phase 3 (new)
storage/
  ├─ migrations/
     ├─ v27_canonical_identity.py
     ├─ v28_review_queue.py
     ├─ v29_batch_publish.py
     ├─ v30_functional_schema.py
     ├─ v31_case_law.py
     ├─ v32_exemplars.py
consumer/
  ├─ functional_extractor.py   # Phase 2 (new)
  ├─ exclusions/
     └─ web3_detector.py        # Phase 2 (new)
intelligence/
  ├─ case_law_retriever.py     # Phase 3 (new)
  ├─ exemplar_matcher.py       # Phase 3 (new)
cli/
  └─ publish_commands.py        # Phase 4 (new)
```

**Strengths:**
- Clear separation by concern
- New files for new features (no God objects)
- Migration scripts separate from application code

**Verdict:** ✅ Well-organized, maintains SRP.

---

### 4.2 Configuration Management ✅ SOUND

**Environment variables:**
```bash
# Phase 0
DELIVERY_MODE=staging_only|manual_publish|batch_publish|auto_publish
NOTION_WRITE_GUARD=1  # Fail closed if forbidden

# Phase 1
THIN_FILE_PROMOTION_MIN_SOURCES=2
THIN_FILE_ARCHIVE_DAYS=60
THIN_FILE_SAMPLING_RATE=0.05  # 5%

# Phase 2
FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD=0.6
WEB3_COOCCURRENCE_WINDOW_CHARS=100

# Phase 3
CASE_LAW_RETRIEVAL_TOP_K=10
EXEMPLAR_VETO_THRESHOLD=0.75
```

**Strengths:**
- Externalized configuration (12-factor app)
- Sensible defaults
- Feature flags for safe rollout

**Recommendation:**
Add validation on startup:
```python
# utils/config_validator.py
def validate_config():
    mode = os.getenv('DELIVERY_MODE', 'staging_only')
    if mode not in {'staging_only', 'manual_publish', 'batch_publish', 'auto_publish'}:
        raise ConfigurationError(f"Invalid DELIVERY_MODE: {mode}")

    threshold = float(os.getenv('EXEMPLAR_VETO_THRESHOLD', '0.75'))
    if not 0.0 <= threshold <= 1.0:
        raise ConfigurationError(f"EXEMPLAR_VETO_THRESHOLD must be in [0,1]: {threshold}")
```

**Verdict:** ✅ Good config management, add startup validation.

---

### 4.3 Documentation ✅ COMPREHENSIVE

**Planning artifacts created:**
- `task_plan_v1.1.md` (phased adoption, 373 lines)
- `findings_v1.1.md` (architectural analysis, 583 lines)
- `progress.md` (session log)

**Strengths:**
- Clear rationale for each decision
- Success criteria per phase
- Risk mitigation strategies
- 5-question reboot check

**Recommendations:**

1. **Add architecture decision records (ADRs):**
```markdown
# docs/adr/0001-delivery-policy-layer.md

## Context
Baseline plan proposes mutating AUTO_PUSH → REVIEW_QUEUE.
This couples decision semantics to delivery policy.

## Decision
Separate delivery policy layer with DELIVERY_MODE env var.

## Consequences
+ Cleaner separation of concerns
+ More maintainable (can change delivery without touching decisions)
+ Testable independently
- One extra abstraction layer
```

2. **Add API reference:**
```markdown
# docs/api/delivery_policy.md

## assert_notion_write_allowed

Centralized guard for Notion writes.

**Signature:**
```python
def assert_notion_write_allowed(
    intent: Literal['manual', 'auto'],
    publish_context: Dict[str, Any]
) -> None:
    ...
```

**Raises:**
- `NotionWriteForbiddenError`: DELIVERY_MODE forbids this write
```

**Verdict:** ✅ Excellent docs, add ADRs + API reference.

---

## 5. Security & Compliance

### 5.1 Notion Write Guard ✅ WELL-DESIGNED

**Fail-closed by default:**
```python
if NOTION_WRITE_GUARD=1 and forbidden:
    raise NotionWriteForbiddenError  # Fail closed
else:
    logger.warning("Write blocked but GUARD=0")  # Log only
```

**Strengths:**
- Explicit intent parameter (`manual` vs `auto`)
- Context includes batch_id for audit trail
- Fail-closed prevents accidental writes

**Verdict:** ✅ Security-conscious design.

---

### 5.2 Audit Logging ✅ COMPREHENSIVE

**Audit points:**
- Delivery policy decisions
- ReviewItem state transitions
- Batch publish operations
- Merge operations
- Anti-pattern approvals
- Constraint changes

**Recommendation:**

Add structured audit log:
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,  -- 'delivery_blocked', 'state_transition', etc.
    actor TEXT,  -- User ID or 'system'
    resource_type TEXT,  -- 'signal', 'reviewitem', 'batch', etc.
    resource_id TEXT,
    old_value TEXT,  -- JSON
    new_value TEXT,  -- JSON
    reason TEXT,
    metadata TEXT,  -- JSON
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_event_type ON audit_log(event_type);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX idx_audit_created_at ON audit_log(created_at);
```

**Verdict:** ✅ Good audit coverage, add structured log table.

---

## 6. Phase Dependency Analysis

### 6.1 Critical Path ✅ WELL-SEQUENCED

```
Phase 0 (Week 1): Delivery policy
  ↓
Phase 1 (Week 2): Identity + ReviewItem + thin files
  ↓
Phase 2 (Week 3-4): Functional schema + Web3
  ↓
Phase 3 (Week 5-6): Case-law + exemplars
  ↓
Phase 4 (Batch publish)
```

**Strengths:**
- Clean dependencies (no circular)
- Each phase delivers value independently
- Can pause after any phase

**Concern ⚠️: Phase 2 blocks baseline Phase 5 (tuning)**

Plan says:
```
Baseline Phase 5 (tune/optimize) — enhanced by v1.1 Phase 3 (anti-patterns + exemplars)
Baseline Phase 6 (live mode) — delayed until v1.1 Phase 2 (intelligence layer) complete
```

**If Phase 2 takes 2 weeks → baseline Phase 5 waits 5 weeks (Phase 0-2)**

**Recommendation:**

Consider decoupling:
```
Baseline Phase 5 (tuning) can proceed with:
- Manual FP pattern detection (existing ops/quality/patterns.py)
- Manual negative keyword additions
- ML threshold analysis
→ v1.1 Phase 3 enhances but doesn't block
```

**Verdict:** ✅ Good sequencing, allow baseline Phase 5 to proceed in parallel.

---

## 7. Risk Assessment Summary

### 7.1 High-Priority Risks (Must Address)

| Risk | Phase | Severity | Mitigation Status |
|------|-------|----------|-------------------|
| v27 migration backfill strategy missing | 1 | 🔴 HIGH | ⚠️ ADD IN PHASE 1.1 |
| TF-IDF vectorizer versioning unclear | 3 | 🟡 MEDIUM | ⚠️ ADD IN PHASE 3 |
| No performance benchmarks/SLOs | 1-3 | 🟡 MEDIUM | ⚠️ ADD IN PHASE 1 |
| Thin file GC strategy missing | 1 | 🟡 MEDIUM | ⚠️ ADD IN PHASE 1.8 |
| Functional schema versioning unclear | 2 | 🟡 MEDIUM | ⚠️ ADD IN PHASE 2.1 |

---

### 7.2 Medium-Priority Risks (Address in Later Phases)

| Risk | Phase | Severity | Mitigation Status |
|------|-------|----------|-------------------|
| Batch publish compensation limits unclear | 4 | 🟡 MEDIUM | Document in Phase 4 |
| State machine transition validation missing | 1 | 🟡 MEDIUM | Add in Phase 1.5 |
| Canonical key hash function unspecified | 1 | 🟢 LOW | Add in Phase 1.2 |
| Audit log not structured | 0 | 🟢 LOW | Add in Phase 0 (optional) |

---

### 7.3 Low-Priority Risks (Monitor)

| Risk | Phase | Severity | Mitigation Status |
|------|-------|----------|-------------------|
| Phase 2 delays baseline Phase 5 | 2 | 🟢 LOW | ✅ Decouple (allow parallel) |
| Configuration validation missing | 0 | 🟢 LOW | Add startup validator |
| ADRs not created | All | 🟢 LOW | Add incrementally |

---

## 8. Recommendations Summary

### Phase 0 (Week 1) - APPROVED ✅

**Must-haves:**
- ✅ Delivery policy layer (as planned)
- ✅ CSV export + manual push (as planned)
- ➕ Add startup config validator

**Nice-to-haves:**
- Add structured audit_log table

---

### Phase 1 (Week 2) - APPROVED WITH BLOCKERS ⚠️

**Blockers (MUST FIX):**
- 🔴 Add v27 backfill script (Task 1.1)
- 🔴 Add canonical_key hash specification (Task 1.2)
- 🔴 Add thin file GC logic (Task 1.8)
- 🔴 Add state machine transition validator (Task 1.5)

**Must-haves:**
- ✅ Canonical identity (as planned)
- ✅ ReviewItem + CompanyFile (as planned)
- ➕ Add database indexes (see §2.1)
- ➕ Define SLOs (see §2.1)

**Nice-to-haves:**
- Add EXPLAIN ANALYZE tests

---

### Phase 2 (Week 3-4) - APPROVED WITH MINOR CHANGES ⚠️

**Must-address:**
- ⚠️ Decide functional schema versioning strategy (Task 2.1)
- ✅ Functional schema extraction (as planned)
- ✅ Web3 co-occurrence (as planned)

**Nice-to-haves:**
- Add functional schema history audit

---

### Phase 3 (Week 5-6) - APPROVED WITH CAVEAT ⚠️

**Must-address:**
- ⚠️ Add TF-IDF vectorizer versioning (see §2.2)
- ✅ Case-law + exemplars (as planned)

**Nice-to-haves:**
- Add vectorizer retrain trigger

---

### Phase 4 (Batch Publish) - APPROVED ✅

**Must-document:**
- Compensation limits (best-effort, no ACID guarantees)
- Add idempotency keys
- Add pre-image storage (optional)

---

## 9. Final Verdict

**✅ APPROVED FOR IMPLEMENTATION**

The Discovery Engine v1.1 architecture is **well-designed** and demonstrates strong software engineering practices. The phased adoption strategy successfully balances immediate value (Notion pollution fix in Week 1) with long-term architectural quality.

**Confidence Level: HIGH (85%)**

**Recommended Actions:**

1. **Address Phase 1 blockers before starting:**
   - v27 backfill script
   - Canonical key hash specification
   - Thin file GC
   - State machine validator
   - Database indexes

2. **Clarify ambiguities in Phase 2:**
   - Functional schema versioning
   - TF-IDF vectorizer evolution

3. **Add performance benchmarks throughout:**
   - Define SLOs in Phase 1
   - Add EXPLAIN ANALYZE tests
   - Measure at each phase

4. **Document as you go:**
   - ADRs for major decisions
   - API reference for new modules
   - Migration guides for operators

**Time Estimate Validation:**
- Phase 0: 4-6 hours → **REALISTIC**
- Phase 1: 12-16 hours → **INCREASE TO 16-20 hours** (blockers add complexity)
- Phase 2: 16-20 hours → **REALISTIC**
- Phase 3: 20-24 hours → **INCREASE TO 24-28 hours** (vectorizer versioning)
- Total: ~60-70 hours → **REVISED TO 68-78 hours**

**Recommendation:** Proceed with v1.1 implementation. This is a solid architectural upgrade.

---

## 10. Questions for Product Owner

Before starting implementation, clarify:

1. **Phase 1 canonical_key backfill:**
   - OK to run backfill script on 47 existing signals?
   - Acceptable downtime window (estimate: 5-10 minutes)?
   - Dry-run results review required?

2. **Functional schema versioning (Phase 2):**
   - Keep full history OR overwrite?
   - Use case: Do you need to see "company evolved from B2B to B2C"?

3. **Performance SLOs:**
   - Current CSV export time for 47 signals?
   - What's acceptable for 500 signals? (suggest: < 2s)
   - What's acceptable review queue load time? (suggest: < 500ms)

4. **Baseline Phase 5 timing:**
   - Can baseline Phase 5 (tuning) proceed with manual FP detection?
   - OR must wait for v1.1 Phase 3 (anti-patterns + exemplars)?

5. **Batch publish compensation:**
   - Acceptable to document "best-effort only" for rollback?
   - OR required to build full transactional rollback (adds 2-3 weeks)?

---

**Senior Architect Sign-off:**

Architecture: ✅ APPROVED
Scalability: ⚠️ NEEDS ATTENTION (address Phase 1 blockers)
Technical Debt: ✅ LOW (clean incremental approach)
Maintainability: ✅ EXCELLENT
Security: ✅ SOUND

**Overall: PROCEED WITH IMPLEMENTATION**

