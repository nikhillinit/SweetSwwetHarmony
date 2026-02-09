# Task Plan: Discovery Engine v1.1.1 — Phased Adoption (Revised)

**Related Docs:**
- [Findings v1.1](findings_v1.1.md) — Architectural analysis & key decisions
- [Architectural Review](architectural_review_v1.1.md) — Senior architect review
- [Baseline Task Plan](task_plan.md) — Current data bootstrap plan
- [v1.1 Proposal](C:\Users\nikhi\Downloads\Discovery Engine v1.1.txt) — Full specification

**Session:** discovery-engine-v1.1-implementation (cca1f8e0)
**Created:** 2026-02-08
**Revision:** v1.1.1 (efficiency + efficacy corrections from comprehensive review)

---

## Executive Summary

**Decision:** Adopt Discovery Engine v1.1 architecture with phased implementation that merges baseline quick wins with v1.1 governance backbone.

**Strategy:** Start with Phase 0 to fix Notion pollution + operator efficiency immediately, then adopt v1.1 incrementally over 5-6 weeks.

**v1.1.1 Key Changes (from efficiency review):**
1. **Batch publish moved to Week 2-3** (was Month 2+) — only depends on Phase 0-1
2. **Intelligence visible in CSV/CLI immediately** (not trapped until dashboard)
3. **Triage CLI added to Phase 0** — operator efficiency from Day 1
4. **Promotion rules clarified** — exemplar similarity inactive until Phase 3
5. **Backfill script + validation gate** — first-class blocker for Phase 1
6. **Performance indexes + SLOs** — prevent scale-induced debt

---

## Current State

From baseline `task_plan.md`:
- ✅ Phase 1 complete: ML deps installed, 2590+ tests passing
- ✅ Phase 2 complete: 47 signals collected (9 collectors)
- ✅ Phase 3 complete: 31 labels (7 TP, 23 FP, 1 UNSURE), FP rate 74.2%
- 🔄 Phase 4 in progress: Train ML model & shadow mode

---

## Phases Overview (Revised v1.1.1)

| Phase | Description | Duration | Status | Depends On |
|-------|-------------|----------|--------|------------|
| 0 | Delivery Policy + Triage CLI + CSV Export | Week 1 | ✅ COMPLETE (PR #28) | — |
| 1a | Identity + ReviewItem + Thin Files | Week 2 | ✅ COMPLETE (PR #29 + #30) | Phase 0 |
| 1b | Batch Publish Workflow | Week 2-3 | ✅ COMPLETE (PR #31) | Phase 0-1a |
| 2 | Functional Schema + Web3 + Intelligence Visibility | Week 3-4 | ✅ COMPLETE (PR #32) | Phase 1a |
| 3 | Case-law + Exemplars + Intelligence Visibility | Week 5-6 | pending | Phase 2 |
| 4+ | Dashboard + ACH + Active Hunter | Month 2+ | pending | Phase 3 |

**Parallel tracks (throughout):**
- **Track A:** Baseline Phase 5 manual tuning (Weeks 1-4)
- **Track B:** Documentation + ADRs (continuous)
- **Track C:** Performance monitoring + SLO validation

---

## Phase 0: Delivery Policy + Triage CLI + CSV Export (Week 1)

**Goal:** Stop Notion pollution AND reduce operator fatigue immediately.

**Key insight (v1.1.1):** Adding triage CLI here gives operator efficiency from Day 1, not Month 2+. Dashboard later becomes UI overlay on same triage actions.

| Task | Description | Files | Status |
|------|-------------|-------|--------|
| 0.1 | Add delivery policy layer + Notion write guard | `workflows/delivery_policy.py` | ✅ done |
| 0.2 | Add `DELIVERY_MODE` env var (staging_only/manual_publish/batch_publish/auto_publish) | `.env`, `.env.example` | ✅ done |
| 0.3 | Add `assert_notion_write_allowed(intent, publish_context)` | `workflows/delivery_policy.py` | ✅ done |
| 0.4 | Wire guard into `notion_pusher.py` | `workflows/notion_pusher.py` | ✅ done |
| 0.5 | Add CSV export command: `run_pipeline.py export-queue --format csv` | `run_pipeline.py` | ✅ done |
| 0.6 | Add manual push command: `run_pipeline.py push --signal-ids X,Y,Z` | `run_pipeline.py` | ✅ done |
| 0.7 | Set default `DELIVERY_MODE=staging_only` | `.env` | ✅ done |
| 0.8 | Test: verify auto-push blocked, manual push works | Test suite | ✅ done |
| 0.9 | Config validator on startup (DELIVERY_MODE, threshold bounds) | `utils/config_validator.py` | ✅ done |
| 0.10 | Structured `audit_log` table | `storage/migrations/v27_audit_log.py` | ✅ done |
| 0.11 | **Triage CLI: compact list + approve/reject actions** | `run_pipeline.py` | ✅ done |

**Triage CLI (Task 0.11):**
```bash
python run_pipeline.py triage list --limit 20 --compact
# Output: [ID] [Company] [1-line summary] [Flags] [Decision]

python run_pipeline.py triage approve 123 --reason "Clear consumer fit"
python run_pipeline.py triage reject 124 --reason "B2B dev tool"
python run_pipeline.py triage defer 125 --reason "Need more signals"
```

**Success criteria:**
- [x] `DELIVERY_MODE=staging_only` blocks all Notion writes
- [x] `DELIVERY_MODE=manual_publish` allows single-item manual push
- [x] CSV export produces complete queue snapshot
- [x] Manual push works with guard
- [x] Triage CLI: compact list view + approve/reject/defer actions
- [x] Config validation catches invalid DELIVERY_MODE on startup

**Actual time:** ~6 hours | **PR:** #28

---

## Phase 1a: Identity + ReviewItem + Thin Files (Week 2)

**Goal:** Implement canonical identity, ReviewItem state machine, and thin files with full validation.

| Task | Description | Files | Status |
|------|-------------|-------|--------|
| 1.1 | Add `company_id` (UUID5) + `canonical_key` to signals table | `storage/migrations/v28_canonical_identity.py` | ✅ done |
| 1.1a | **Backfill script with dry-run + validator** (BLOCKER) | `storage/migrations/backfill_v28_identity.py` | ✅ done |
| 1.1b | **Migration gate: pipeline refuses to run if company_id NULL** | `storage/identity_gate.py` | ✅ done |
| 1.2 | Implement canonical_key algorithm (domain preferred, name fallback) | `utils/canonical_keys.py` | ✅ done |
| 1.2a | **Canonical key hash spec: SHA-256 first 8 chars for collisions** | `utils/canonical_keys.py` | ✅ done |
| 1.3 | Add `company_aliases` table for merged keys | Phase G `entity_aliases` is authoritative | ✅ done (reuse Phase G) |
| 1.4 | Implement `merge_companies(winner, loser, reason)` | `storage/merge_cascade.py` | ✅ done |
| 1.5 | Create `ReviewItem` table (company_id, status, evidence_bundle) | `storage/migrations/v29_review_queue.py` | ✅ done |
| 1.5a | **State transition validator (enforced at storage layer)** | `storage/review_store.py` | ✅ done |
| 1.6 | Create `CompanyFile` table (thin files) | `storage/migrations/v29_review_queue.py` | ✅ done |
| 1.7 | Implement promotion rules (**exemplar similarity inactive until Phase 3**) | `workflows/thin_file_manager.py` | ✅ done |
| 1.8 | Add thin file hygiene (60-day archive, 5-10% sampling) | `workflows/thin_file_manager.py` | ✅ done |
| 1.8a | **Thin file GC: retention policy + purge script** | `scripts/gc_thin_files.py` | ✅ done |
| 1.9 | Wire pipeline to create CompanyFiles | `workflows/pipeline.py` | ✅ done |
| 1.10 | **Database indexes for all new tables** | `storage/migrations/v29_review_queue.py` | ✅ done |
| 1.11 | **Performance SLOs + regression tests** | `tests/performance/` | ✅ done |

**Backfill script (Task 1.1a):**
```python
# storage/migrations/backfill_v28_identity.py
# - Extract domain from raw_data when available
# - Else use name-based canonical_key
# - Group by canonical_key, assign deterministic UUID5 per group
# - Dry-run mode: prints signal_id → canonical_key → company_id
# - Post-backfill validator: no NULLs, collision rate <5%
```

**Promotion rules (Task 1.7 - clarified):**
```python
# Phase 1: Only use available criteria
PROMOTION_RULES = [
    ('sources', lambda cf: cf.distinct_source_count >= 2),
    ('trusted_source', lambda cf: cf.has_trusted_source()),
    ('operator_manual', lambda cf: cf.manually_promoted),
    # 'exemplar_similarity' disabled until Phase 3 (no exemplar library yet)
]
```

**State transition validator (Task 1.5a):**
```python
VALID_TRANSITIONS = {
    'pending': {'approved', 'rejected', 'deferred'},
    'approved': {'published', 'publish_queued'},
    'deferred': {'pending'},
    'rejected': set(),   # Terminal (unless cooldown elapsed)
    'published': set()   # Terminal
}
```

**Indexes (Task 1.10):**
```sql
CREATE INDEX idx_aliases_company_id ON company_aliases(company_id);
CREATE INDEX idx_aliases_canonical_key ON company_aliases(canonical_key);
CREATE INDEX idx_review_status_created ON ReviewItem(status, created_at);
CREATE INDEX idx_review_company_id ON ReviewItem(company_id);
CREATE INDEX idx_company_file_status_seen ON CompanyFile(status, last_seen_at);
CREATE INDEX idx_company_file_sources ON CompanyFile(distinct_source_count);
```

**Performance SLOs (Task 1.11):**
```
CSV export < 2s for 500 ReviewItems
Review queue load < 500ms for 1000 items
Manual push < 1s per signal
Thin file promotion check < 500ms
```

**Success criteria:**
- [x] Signals have stable `company_id` (SHA256[:16]) + deterministic `canonical_key`
- [x] Backfill dry-run passes (47 signals, 0 NULLs, collision rate <5%)
- [x] Pipeline refuses to run if any signal has NULL company_id
- [x] State transitions enforced (invalid transitions raise error)
- [x] Promotion rules work without exemplar similarity
- [x] Performance SLOs met
- [x] Thin file GC purges archived >1 year

**Actual time:** ~14 hours | **PRs:** #29 + #30

---

## Phase 1b: Batch Publish Workflow (Week 2-3)

**Goal:** Git-style preview → commit workflow for safer, faster publishing.

**Key insight (v1.1.1):** Only depends on Phase 0-1a. Moving from Month 2+ to Week 2-3 delivers batch efficiency 3-4 weeks earlier.

| Task | Description | Files | Status |
|------|-------------|-------|--------|
| 1b.1 | Migration v31: `publish_batches` + `batch_items` tables | `storage/migrations/v31_batch_publish.py` | ✅ done |
| 1b.2 | Add `publish_queued → approved` transition (abort revert) | `storage/review_store.py` | ✅ done |
| 1b.3 | BatchPublisher core: create, preview, commit, abort, list | `workflows/batch_publisher.py` | ✅ done |
| 1b.4 | CLI: `publish create\|preview\|commit\|abort\|list` | `run_pipeline.py` | ✅ done |
| 1b.5 | Integration tests: full lifecycle, abort guard, determinism | `tests/integration/test_batch_publish_e2e.py` | ✅ done |
| 1b.6 | Governance lint + Phase 1a regression verification | All test suites | ✅ done |

**Compensation semantics (Task 1b.8):**
```
Batch Publish Compensation (Local DB Only):
- Atomic: All ReviewItem state transitions succeed or rollback
- NOT atomic: Notion API writes (eventually consistent)
- Idempotency: Skip already-published via canonical_key
- Remediation: Manual rollback using pre-image snapshots
- No automatic cross-system rollback (Notion + DB)
```

**Success criteria:**
- [x] Can create batch from approved items (atomic via single `transaction_immediate()`)
- [x] Preview shows deterministic batch contents (correlated subqueries, stable ORDER BY)
- [x] Commit executes guarded writes (delivery policy check, `--yes` confirmation)
- [x] Dry-run has zero mutations (read-only, separate audit entry)
- [x] Abort drops draft and reverts reviews to approved
- [x] Abort guard refuses if any items already pushed to Notion
- [x] 38 tests passing (7 DDL + 24 unit + 7 e2e)
- [x] Governance lint passes (no direct SignalStore construction)
- [x] Phase 1a regression tests pass (42/42)

**Actual time:** ~4 hours | **PR:** #31

---

## Phase 2: Functional Schema + Web3 + Intelligence Visibility (Week 3-4)

**Goal:** Add functional schema extraction, Web3 exclusion, AND surface intelligence in CSV/CLI immediately.

**Key insight (v1.1.1):** Building intelligence without making it visible means it won't improve decisions until dashboard ships (Month 2+). Surface in CSV/CLI now.

| Task | Description | Files | Status |
|------|-------------|-------|--------|
| 2.1 | Create `functional_schemas` table (versioned: history + active flag) | `storage/migrations/v32_functional_schema.py` | pending |
| 2.2 | Implement functional schema extractor (LLM-based, confidence-gated) | `consumer/functional_extractor.py` | pending |
| 2.3 | Add schema confidence gating (low confidence = advisory only) | `consumer/functional_extractor.py` | pending |
| 2.4 | Wire schema extraction into pipeline (optional, LLM-gated) | `workflows/pipeline.py` | pending |
| 2.5 | Implement deterministic Web3 hard exclusion with co-occurrence rules | `consumer/exclusions/web3_detector.py` | pending |
| 2.6 | Replace baseline heavy keyword penalty with co-occurrence detector | `consumer/thesis_filter/thesis_matcher_v2.py` | pending |
| 2.7 | Add "adjacent categories" to LLM thesis classification prompt | `consumer/thesis_filter/llm_classifier.py` | pending |
| 2.8 | Update functional schema on new evidence (company-level aggregation) | `storage/signal_store.py` | pending |
| 2.9 | **Surface functional schema in CSV export** | CSV export code | pending |
| 2.10 | **Surface functional schema in triage CLI** | `run_pipeline.py` triage | pending |

**Extended CSV export (Task 2.9):**
```csv
signal_id,company_name,confidence,problem_solved,customer_archetype,schema_confidence,reason_chain,decision
123,Acme Inc,0.82,"Creators monetize short-form video",creators,0.91,"consumer_fit+multi_source",approved
```

**Extended triage CLI (Task 2.10):**
```bash
python run_pipeline.py triage list --compact
# [123] Acme Inc | "Creators monetize video" | creators | 0.91 conf | APPROVED
# [124] Foo Corp | "B2B infra monitoring" | engineering_teams | 0.87 conf | REJECTED
```

**Functional schema versioning (Task 2.1):**
```sql
CREATE TABLE functional_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    problem_solved_text TEXT,
    customer_text TEXT,
    approach_text TEXT,
    customer_archetype TEXT,
    problem_archetypes TEXT,  -- JSON array
    schema_confidence REAL,
    evidence_spans TEXT,  -- JSON
    is_active BOOLEAN DEFAULT 1,
    superseded_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(company_id, schema_version)
);
CREATE INDEX idx_schema_company_active ON functional_schemas(company_id, is_active);
```

**Success criteria:**
- [ ] Functional schemas extracted with provenance + confidence + versioned history
- [ ] Schema influences priority/explanation but not hard routing (safety)
- [ ] Web3 exclusion doesn't reject "access tokens" or "DAO pattern"
- [ ] LLM prompt includes adjacent categories
- [ ] **CSV export includes functional schema columns**
- [ ] **Triage CLI shows functional summary**

**Estimated time:** 16-20 hours

---

## Phase 3: Case-law + Exemplars + Intelligence Visibility (Week 5-6)

**Goal:** Add case-law retrieval, dual memory (anti-patterns + exemplars), AND make them visible in exports.

| Task | Description | Files | Status |
|------|-------------|-------|--------|
| 3.0 | **Vectorizer metadata + versioning plan** | `intelligence/vectorizer_config.py` | pending |
| 3.1 | Create `precedents` table (with `vectorizer_version` column) | `storage/migrations/v33_case_law.py` | pending |
| 3.2 | Build case-law corpus from labeled signals (builder mode) | `scripts/build_case_law_corpus.py` | pending |
| 3.3 | Implement TF-IDF retrieval (top K wins + top K losses) | `intelligence/case_law_retriever.py` | pending |
| 3.4 | Add recency warnings (precedents >3 years old) | `intelligence/case_law_retriever.py` | pending |
| 3.5 | Create `thesis_exemplars` table (with `vectorizer_version`) | `storage/migrations/v34_exemplars.py` | pending |
| 3.6 | Build exemplar library from 7 TP labels + portfolio wins | `scripts/build_exemplar_library.py` | pending |
| 3.7 | Implement exemplar similarity scoring (TF-IDF baseline) | `intelligence/exemplar_matcher.py` | pending |
| 3.8 | Add exemplar veto logic (high similarity → cannot auto-drop) | `workflows/semantic_filter.py` | pending |
| 3.9 | **Wire case-law + exemplars into CSV export** | CSV export code | pending |
| 3.10 | **Wire case-law + exemplars into triage CLI** | `run_pipeline.py` triage | pending |
| 3.11 | Add anti-pattern propose → approve workflow | `ops/quality/patterns.py` | pending |
| 3.12 | **Activate exemplar similarity in promotion rules** | `workflows/thin_file_manager.py` | pending |
| 3.13 | Retrain trigger (corpus > 2x since last build → auto-rebuild) | `intelligence/vectorizer_config.py` | pending |

**Vectorizer metadata (Task 3.0):**
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

**Extended CSV (Task 3.9):**
```csv
signal_id,...,precedent_wins,precedent_losses,similarity_max,exemplar_match,exemplar_labels,veto_applied
```

**Extended triage CLI (Task 3.10):**
```bash
python run_pipeline.py triage detail 123
# Company: Acme Inc | Confidence: 0.82
# Functional: "Creators monetize video" (creators, 0.91 conf)
# Case-law: Similar to WinCo (0.87 sim), FPCorp (0.45 sim)
# Exemplar: Matches "creator_economy" (0.82 sim) | VETO ACTIVE
# Constraints: None fired
# Decision: APPROVED (reason: consumer_fit + multi_source + exemplar_match)
```

**Success criteria:**
- [ ] Case-law corpus built from 31 labeled signals
- [ ] TF-IDF retrieval surfaces similar wins/losses
- [ ] Exemplar library created from 7 TP labels
- [ ] Exemplar veto prevents high-similarity items from auto-quarantine
- [ ] **Exemplar similarity added to promotion rules**
- [ ] **CSV export includes case-law + exemplar columns**
- [ ] **Triage detail view shows case-law + exemplar info**
- [ ] Vectorizer versioned with retrain trigger
- [ ] Anti-patterns require human approval before affecting routing

**Estimated time:** 24-28 hours

---

## Phase 4+: Dashboard + Advanced Features (Month 2+)

**Key insight (v1.1.1):** Dashboard becomes a UI overlay on existing triage/export data, not a greenfield build. All intelligence already visible via CLI/CSV.

### Dashboard with Two-Pass Triage (Week 7-8)
- UI overlay on triage CLI data (all data already available)
- Operator triage view (compact list + keyboard shortcuts)
- Fast pass: approve/reject/hold for deep review
- Deep review: full evidence + schema + case-law + ACH
- Explorer role: read/search/comment/flag
- Authentication: local accounts + RBAC

### ACH Matrix + Tribunal (Week 9-10)
- ACH runs table (hypotheses x evidence matrix)
- Non-LLM fallback (deterministic + retrieval signals)
- Tribunal as optional narrative wrapper

### Active Hunter (Week 11-12)
- Generate targeted queries from functional patterns + exemplars
- Sandbox + promotion rules

### Entity Resolution (Week 13-14)
- Record linkage suggestions (Jaro-Winkler + TF-IDF)
- Clustered merge presentation

### Drift Monitoring (Week 15-16)
- SPC-lite alerts
- Canary regression checks

---

## Integration with Baseline Plan

**What stays from baseline:**
- ✅ Phase 1 (ML deps) — already complete
- ✅ Phase 2 (bootstrap data) — already complete, 47 signals collected
- ✅ Phase 3 (bootstrap labels) — already complete, 31 labels applied
- 🔄 Phase 4 (train ML) — **continues in parallel** with v1.1 Phase 0-1
- 🔄 Phase 5 (tune/optimize) — **runs in parallel** with manual FP detection (Weeks 1-4)
- 📋 Phase 6 (live mode) — **enhanced** by v1.1 Phase 2-3 intelligence

**Revised critical path (v1.1.1):**
```
Week 1: Phase 0 (delivery + triage + CSV) + Baseline Phase 5 (manual tuning)
  ├─ Delivery policy + CSV export + triage CLI
  └─ Manual FP detection feeds Phase 3 anti-patterns

Week 2: Phase 1a (identity + queue) + Phase 1b (batch publish)
  ├─ Identity backfill + validation (BLOCKER)
  ├─ ReviewItem + CompanyFile + indexes
  └─ Batch publish (create/preview/commit/abort)

Week 3-4: Phase 2 (functional + Web3) + Baseline Phase 5 cont.
  ├─ Functional schema (versioned) + Web3 detector
  ├─ Schema VISIBLE in CSV/CLI (immediate value)
  └─ Manual tuning → anti-pattern examples

Week 5-6: Phase 3 (case-law + exemplars)
  ├─ Case-law corpus + TF-IDF (versioned)
  ├─ Exemplar library + bootstrap from 7 TP labels
  ├─ Activate exemplar similarity in promotion rules
  └─ Intelligence VISIBLE in CSV/CLI

Month 2+: Dashboard + Advanced (UI overlay on existing data)
```

**Critical path to first intelligence value: 3 weeks** (Phase 2 schemas visible in CSV) instead of 6+ weeks (waiting for dashboard).

---

## Key Decisions (Revised v1.1.1)

| Decision | Rationale |
|----------|-----------|
| v1.1 delivery policy instead of enum mutation | Cleaner separation of decision vs delivery |
| Triage CLI in Phase 0 (not Month 2+) | Operator efficiency from Day 1 |
| Batch publish in Week 2-3 (not Month 2+) | Only depends on Phase 0-1, delivers value 3-4 weeks earlier |
| Intelligence visible in CSV/CLI immediately | Each phase improves operator decisions, not trapped until dashboard |
| Promotion rules: exemplar similarity inactive until Phase 3 | Avoids dependency on non-existent exemplar library |
| UUID5 for company_id (not random UUID4) | Deterministic per canonical_key group, reproducible |
| Backfill with dry-run + migration gate | Prevents identity corruption downstream |
| Vectorizer versioning from start | Prevents silent similarity degradation |
| Dashboard = UI overlay on existing data | All intelligence available via CLI/CSV first |
| Baseline Phase 5 runs in parallel | Manual tuning feeds Phase 3 anti-patterns |

---

## Risk Mitigation (Enhanced v1.1.1)

| Risk | Original | Enhanced |
|------|----------|----------|
| Identity corruption | Backfill script | Backfill + validator + **migration gate** |
| Performance debt | "Monitor later" | **Indexes + SLO tests in Phase 1** |
| Intelligence invisibility | "Dashboard later" | **CSV/CLI visibility each phase** |
| Operator fatigue | "Dashboard Month 2+" | **Triage CLI Week 1** |
| Batch publish partial failure | "Compensation doc" | **Idempotency + pre-images + remediation guide** |
| Promotion rule confusion | Exemplar ref in Phase 1 | **Explicitly inactive until Phase 3** |
| Vectorizer staleness | Not addressed | **Versioning + retrain trigger** |

---

## Success Metrics (Measurable)

| Milestone | Metric | Target |
|-----------|--------|--------|
| Week 1 | Triage CLI reduces review time | 30% faster (manual measurement) |
| Week 2-3 | Batch publish reduces push time | 70% faster (5min → 1.5min per 10 items) |
| Week 3-4 | Schema visibility improves decision confidence | Operator reports better clarity |
| Week 5-6 | Exemplar veto prevents false negatives | 1+ false negative prevented/week |
| Month 2 | Dashboard triage reduces review time further | <30 min/day for 100 signals/week |

---

## Estimated Hours (Revised)

| Phase | Estimate | Notes |
|-------|----------|-------|
| Phase 0 | 6-8 hours | +2h for triage CLI + config validator |
| Phase 1a | 16-20 hours | Includes backfill, validation, indexes, SLOs |
| Phase 1b | 8-10 hours | Batch publish + compensation docs |
| Phase 2 | 16-20 hours | Includes intelligence visibility in CSV/CLI |
| Phase 3 | 24-28 hours | Includes vectorizer versioning + exemplar bootstrap |
| **Total** | **70-86 hours** | ~2.5 weeks full-time or ~6 weeks part-time |

**Value acceleration:** Major workflow improvements (batch publish, triage) in Week 2 instead of Month 2. First intelligence value in Week 3 instead of Week 6+.

---

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phases 0, 1a, 1b, 2 complete — functional schema + Web3 + intelligence visible |
| Where am I going? | Phase 3 → case-law + exemplars + intelligence visibility (Week 5-6) |
| What's the goal? | Merge baseline quick wins with v1.1 governance; surface intelligence immediately |
| What have I learned? | See findings_v1.1.md + architectural_review_v1.1.md + phase3-findings.md |
| What have I done? | Phase 0-2 (PRs #28-#32), ~940+ tests |

---

## Next Immediate Actions

1. **Start Phase 3, Task 3.0:** Vectorizer metadata + versioning config
2. **Phase 3 plan:** `docs/plans/2026-02-09-phase3-case-law-exemplars.md`
3. **Parallel:** Continue baseline Phase 4 (ML training)
4. **Parallel:** Continue baseline Phase 5 (manual FP detection for tuning)
5. **Phase 3 focus:** Case-law TF-IDF retrieval + exemplar matching + veto + intelligence in CSV/CLI
