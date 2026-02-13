# Phase 1a: Canonical Identity + ReviewItem + Thin Files

**Created:** 2026-02-08
**Status:** planning (v4 — post-review, identity charter)
**Branch:** `feature/phase1a-canonical-identity`
**Depends on:** Phase 0 (PR #28, merged)
**Estimated:** 16-20 hours
**Identity decisions governed by:** [`docs/plans/identity-charter.md`](identity-charter.md)

---

## Review History

- **v1:** Initial plan
- **v2:** Triaged 5 external reviews (22 accepted, 12 rejected, 3 modified)
- **v3:** Integrated 15 accepted, 5 modified, 3 documentation items from consolidated review of 4 independent reviewers (~50 raw comments deduplicated to 25 themes)
- **v4 (current):** Identity charter created. Triaged 3 independent reviews (~45 raw comments): 14 accepted, 3 minor, 9 rejected. Key changes: UUID5 → SHA256[:16], company_aliases eliminated (Phase G entity_aliases authoritative), StoredSignal projection updates catalogued

---

## Goal

Implement stable company identity (SHA256[:16] via `entity_id_for_seed()` + canonical_key on signals), ReviewItem state machine, CompanyFile thin files, and backfill existing 47 signals — with full validation gate blocking pipeline if any signal has NULL company_id.

## Glossary

| Term | Definition |
|------|-----------|
| **Thin file** | A `company_files` row with `status='thin'` — accumulator for sparse signals not yet promoted |
| **Promoted** | A thin file that met promotion criteria and has an associated ReviewItem |
| **Archived** | A thin file with no new evidence after 60 days; still in DB, eligible for reactivation |
| **GC'd** | Permanently deleted from DB after 365+ days in archived status |
| **Representative key** | The `canonical_key` stored on `company_files` — winner's key on merge (Phase 1a); recompute by strength deferred to Phase 2 |

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| company_id type | SHA256[:16] via `entity_id_for_seed()` | Matches Phase G entity_id; single ID space |
| Identity alias storage | Phase G `entity_aliases` (v19) | No new table; `audit_log` for operator actions |
| distinct_source_count | Derived at read time from `source_apis` JSON | Eliminates drift between column and actual sources |
| Migration version | v28 (identity), v29 (review queue) | Follows existing v27 pattern |
| State machine enforcement | Storage layer (not CLI) | Single point of enforcement |
| Thin file GC | 365-day purge for archived | Architect recommendation |
| Exemplar similarity | DISABLED until Phase 3 | No exemplar library yet |
| Backfill strategy | Dry-run + validator + migration gate | Architect BLOCKER requirement |
| Row access pattern | Explicit-column SELECTs + tuple unpacking | SignalStore has no `row_factory`; `fetchone()` returns tuples |
| Transaction mode for identity writes | `transaction_immediate()` unconditionally when identity store active | Phase G code requires `BEGIN IMMEDIATE`; avoids concurrency flakes |
| company_name derivation | `signals.company_name` column (position 4, nullable) > `canonical_key` fallback | Actual column name; no claim_facts dependency in Phase 1a |
| source_apis validation | Application-level in `_parse_source_apis()` | `CHECK(json_valid(...))` requires SQLite 3.38+; not guaranteed on Windows |
| Deferred vs new reviews | Deferred reviews do NOT block new promotions | By design: deferred = "maybe later"; new evidence creates fresh pending review |

## Dependency Graph

```
Task 1 (v28 migration DDL)
  ├── Task 2 (backfill script) ── Task 3 (migration gate)
  └── Task 5 (cascade_merge)

[Task 4 ELIMINATED — Phase G entity_aliases is authoritative]

Task 6 (v29 ReviewItem DDL) ── Task 7 (state transition validator)

Task 8 (v29 CompanyFile DDL)
  └── Task 9 (promotion rules)
      └── Task 10 (thin file GC)

Task 11 (pipeline wiring)  ← depends on Tasks 3, 5, 7, 9

Task 12 (performance indexes + SLO tests)

Task 13 (integration tests)  ← depends on all above
```

## Parallelization Strategy

**Safe to parallelize (no shared state):**
- Group A: Tasks 1-3, 5 (identity migration + backfill + gate + cascade_merge)
- Group B: Tasks 6-7 (ReviewItem DDL + state machine) — independent DDL
- Group C: Tasks 8-10 (CompanyFile + promotion + GC) — independent DDL

**Must be sequential:**
- Task 5 after Task 1 (cascade_merge needs identity column)
- Task 11 after Groups A+B+C (pipeline wires everything together)
- Task 12-13 after Task 11 (verify the whole thing)

---

## Pre-implementation Checklist

Before starting any tasks, complete these mechanical fixes:

- [ ] **Fix brittle test INSERTs (A7):** Run `ripgrep "INSERT INTO signals VALUES" -n` across codebase. Convert all matches to column-list INSERTs (either include `company_id` explicitly or omit via column list so SQLite fills NULL). Known locations: `tests/ops/quality/test_disagreement_report.py` (4 occurrences). Also search: `ripgrep "INSERT INTO signals" -t py` for any remaining brittle INSERTs beyond the 4 already known.
- [ ] **Verify all error messages are actionable (A10):** Every RuntimeError in Phase 1a code must include the specific migration command to fix it: `python -m storage.migrate --db signals.db`
- [ ] **Update StoredSignal dataclass (A10):** Add `company_id: Optional[str] = None` field
- [ ] **Update `_row_to_signal()` (A10):** Handle new `company_id` column at position 4 (shifts `company_name` to position 5, etc.)
- [ ] **Update ~12 explicit SELECT projections (A10):** Files requiring column-list updates:
  - `storage/signal_store.py` — `save_signal` INSERT, `_row_to_signal`, multiple SELECTs (~line 2279)
  - `ops/quality/thesis.py` (~line 51)
  - `ops/quality/export.py` (~lines 86-131)
  - `ops/quality/enrichment.py` (~line 76)
  - `run_pipeline.py` — export-queue (~line 4575), triage (~line 4394), shadow backfill (~line 4244)
  - `view_signals.py`, `view_signals_detail.py`

---

## Tasks

### Group A: Identity Foundation (v28 migration)

#### Task 1: v28 Migration — Add company_id column
- **Files:** `storage/migrations/v28_canonical_identity.py`, `storage/signal_store.py`
- **What:**
  - Create `v28_canonical_identity.py` with DDL (follows v27_audit_log.py pattern)
  - ALTER TABLE signals ADD COLUMN company_id TEXT
  - signals already HAS canonical_key — no new column needed for that
  - Import in signal_store.py, add to MIGRATIONS dict as v28
- **Tests:** Migration applies cleanly, column exists after migration
- **Status:** pending

#### Task 2: Backfill Script with Dry-Run + Validator
- **Files:** `storage/migrations/backfill_v28_identity.py`
- **Prerequisite:** Pipeline MUST be stopped during backfill. Document this as a maintenance-mode requirement — concurrent pipeline can create stale/duplicate company_ids. **(A15)**
- **What:**
  - Read all signals missing company_id
  - Read existing `signals.canonical_key` as source of truth (do NOT recompute from raw_data) **(A3)**
  - Group by canonical_key → resolve company_id per group:
    1. Check `entity_aliases` for existing binding: `SELECT entity_id FROM entity_aliases WHERE strong_key = ?`
    2. **If found: resolve to current root** via `resolve_entity_root(entity_id)` — handles transitive merges where the alias points to a stale pre-merge ID **(A5)**
    3. If not found: generate via `EntityIdentityStore.entity_id_for_seed(canonical_key)`, then register binding via `upsert_strong_key_bindings()`
  - Dry-run mode: print signal_id → canonical_key → company_id mapping
  - Apply mode: UPDATE signals SET company_id = ? WHERE id = ?
  - Post-backfill validator: assert no NULLs, report # of merge-resolved roots vs newly generated IDs
  - **All queries use explicit-column SELECTs + tuple unpacking (no row_factory) (A2)**
- **Tests:** Dry-run produces correct mapping, apply mode updates all rows, validator passes, root resolution handles merged entities
- **Status:** pending

#### Task 3: Migration Gate — Pipeline refuses to run if company_id NULL
- **Files:** `storage/signal_store.py` (new method), `workflows/pipeline.py` (wire check)
- **What:**
  - Add `check_identity_integrity()` method to SignalStore
  - Query: `SELECT COUNT(*) FROM signals WHERE company_id IS NULL`
  - If > 0: raise `IdentityMigrationRequired` error with actionable message:
    ```
    "X signals have NULL company_id. Run backfill first:
     python -m storage.migrations.backfill_v28_identity --db signals.db --apply"
    ```
  - Wire into pipeline startup (before collectors run)
- **Tests:** Gate blocks when NULLs exist, passes when all populated
- **Status:** pending

#### Task 4: ~~company_aliases Table~~ — ELIMINATED (A2)
- **Status:** ELIMINATED
- **Reason:** Phase G's `entity_aliases` handles all strong key → entity_id mappings. `entity_migrations` handles merge history. `audit_log` handles operator-initiated merge records. No new alias table needed.

#### Task 5: cascade_merge(winner, loser, reason)
- **Files:** `storage/signal_store.py` (or `storage/merge_cascade.py` new file)
- **What:**
  - Accept winner_company_id, loser_company_id, reason, actor, **tx: Optional[aiosqlite.Connection] = None (A12)**
  - If `tx` provided, use it; otherwise open new `transaction_immediate()` **(M1)**
  - **All queries use explicit-column SELECTs + tuple unpacking (A2)**
  - **Step 1: Resolve review_items UNIQUE collision (A1)**
    - Query active reviews for both winner and loser: `SELECT id, status, evidence_bundle FROM review_items WHERE company_id IN (?, ?) AND status IN ('pending', 'approved', 'publish_queued')`
    - If both have active reviews, choose primary by precedence: `publish_queued` > `approved` > `pending`, then newest `updated_at`
    - For non-primary active review(s): set `status='rejected'` with `reason='merged_into:<primary_id>'`
    - **Merge evidence in Python (A14):**
      ```python
      primary_bundle = json.loads(primary_review_evidence_bundle)
      loser_bundle = json.loads(loser_review_evidence_bundle)
      merged_ids = sorted(set(primary_bundle["signal_ids"]) | set(loser_bundle["signal_ids"]))
      primary_bundle["signal_ids"] = merged_ids
      # UPDATE review_items SET evidence_bundle = json.dumps(primary_bundle) WHERE id = primary_id
      ```
    - Then reassign remaining loser review_items: `UPDATE review_items SET company_id = winner WHERE company_id = loser`
  - **Step 2: Reassign signals**
    - `UPDATE signals SET company_id = winner WHERE company_id = loser`
  - **Step 3: Merge company_files**
    - Fetch both files with explicit columns: `SELECT company_id, source_apis, first_seen_at, last_seen_at FROM company_files WHERE company_id = ?`
    - **Compute timestamps in Python, not SQL (A3):**
      ```python
      earliest = min(winner_first_seen, loser_first_seen)
      latest = max(winner_last_seen, loser_last_seen)
      ```
    - **Sort source_apis for determinism (A11):**
      ```python
      combined = sorted(list(set(w_sources + l_sources)))
      ```
    - UPDATE winner's company_file with merged values
    - DELETE loser's company_file
    - **Keep winner's canonical_key as representative (M4)** — recompute by strength deferred to Phase 2
  - **Step 4: Record merge + audit**
    - Phase G records alias via `entity_aliases` / `entity_migrations` (automatic via `merge_entities()`)
    - INSERT INTO `audit_log` for the cascade action (operator audit trail)
- **Tests:** Signals reassigned, audit logged, review collision handled correctly with evidence merged in Python, idempotent on re-run, source_apis sorted deterministically
- **Status:** pending

### Group B: ReviewItem State Machine (v29 migration)

#### Task 6: v29 Migration — ReviewItem Table
- **Files:** `storage/migrations/v29_review_queue.py`, `storage/signal_store.py`
- **What:**
  - CREATE TABLE review_items (id, company_id, status, evidence_bundle TEXT, reason, created_at, updated_at, decided_at, decided_by)
  - status CHECK IN ('pending', 'approved', 'rejected', 'deferred', 'published', 'publish_queued')
  - evidence_bundle: JSON with schema `{"signal_ids": [...], "schema_version": 1}`
  - Partial unique index: `CREATE UNIQUE INDEX idx_review_one_active_per_company ON review_items(company_id) WHERE status IN ('pending', 'approved', 'publish_queued')`
  - **Design note (D1):** Deferred is intentionally excluded from the partial unique index. A company with a deferred review CAN receive a new pending review from a fresh promotion. This is by design — deferred means "maybe later" and new evidence should create a fresh review cycle.
  - Import in signal_store.py, add to MIGRATIONS dict as v29
- **Tests:** Migration applies, table exists, constraints enforced, partial unique index prevents duplicate active reviews
- **Status:** pending

#### Task 7: State Transition Validator
- **Files:** `storage/signal_store.py` (new methods), `ops/review_cli.py` (new file)
- **What:**
  - VALID_TRANSITIONS dict:
    - pending → approved, rejected, deferred
    - approved → published, publish_queued
    - publish_queued → published, rejected (emergency halt)
    - deferred → pending
    - rejected, published → terminal (no outbound transitions)
  - `update_review_status(review_id, new_status, actor, reason)` method
    - Validates transition, raises `InvalidStateTransition` if bad
    - INSERT INTO audit_log on every transition
  - `create_review_item(company_id, evidence_signals)`:
    - **Use INSERT ON CONFLICT DO NOTHING + check rowcount (A9):**
      ```python
      cursor = await tx.execute("""
          INSERT INTO review_items (company_id, status, evidence_bundle, created_at, updated_at)
          VALUES (?, 'pending', ?, ?, ?)
          ON CONFLICT(company_id) WHERE status IN ('pending','approved','publish_queued')
          DO NOTHING
      """, ...)
      if cursor.rowcount == 0:
          # Active review already exists — return existing ID
          cursor = await tx.execute(
              "SELECT id FROM review_items WHERE company_id = ? AND status IN ('pending','approved','publish_queued')",
              (company_id,))
          return cursor.fetchone()[0]
      return cursor.lastrowid
      ```
  - `get_review_queue(status=None, limit=50)` — query review_items
  - **Review CLI commands (ops/review_cli.py) (A13):**
    - `review list [--status pending|approved|...]`
    - `review approve <id> [--reason ...]`
    - `review reject <id> --reason ...`
    - `review defer <id> --reason ...`
    - `review reopen <id>` (deferred → pending)
    - `review halt <id> --reason ...` (publish_queued → rejected, emergency halt)
  - Register review subcommands in `ops/quality_cli.py`
- **Tests:** Valid transitions succeed, invalid transitions raise, audit trail written, ON CONFLICT handles race, halt command works
- **Status:** pending

### Group C: CompanyFile / Thin Files (v29 migration, same)

#### Task 8: v29 Migration — CompanyFile Table
- **Files:** `storage/migrations/v29_review_queue.py` (same file as Task 6)
- **What:**
  - CREATE TABLE company_files (id, company_id UNIQUE, company_name, canonical_key, status CHECK IN ('thin','promoted','archived'), source_apis TEXT, first_seen_at, last_seen_at, promoted_at, archived_at, metadata TEXT)
  - `distinct_source_count` ELIMINATED (A5) — derive at read time via `len(_parse_source_apis(source_apis))`
  - Thin file = accumulator for sparse signals
- **Tests:** Table created, constraints work
- **Status:** pending

#### Task 9: Promotion Rules + Thin File Manager
- **Files:** `workflows/thin_file_manager.py` (new file)
- **What:**
  - **All queries use explicit-column SELECTs + tuple unpacking (A2)**
  - **`_parse_source_apis(source_apis_str)` with application-level validation (M3):**
    ```python
    def _parse_source_apis(source_apis_str):
        try:
            sources = json.loads(source_apis_str)
            if not isinstance(sources, list):
                return []
            return [s for s in sources if isinstance(s, str) and s]
        except (json.JSONDecodeError, TypeError):
            return []
    ```
  - **`upsert_company_file(company_id, company_name, canonical_key, source_api)` — all branches specified (M2):**
    - **Existing + archived (reactivation):** Set status='thin', archived_at=NULL, bump last_seen_at, **append triggering source_api to source_apis (A12)**, audit log 'reactivate'
    - **Existing + thin/promoted:** If source_api not in existing sources, append it + bump last_seen_at
    - **New:** INSERT with status='thin', source_apis=[source_api], first_seen_at=now, last_seen_at=now
    - **company_name derivation (M5, A13):** Use `signals.company_name` column (position 4, nullable) if available, else `canonical_key` as fallback
  - **`check_and_promote_atomic(company_id)` — full pseudocode (M2):**
    - Single `transaction_immediate()`
    - SELECT company_file WHERE company_id = ? AND status = 'thin'
    - If not found or `_meets_criteria()` fails → return None
    - Gather evidence: `SELECT id FROM signals WHERE company_id = ? ORDER BY created_at DESC LIMIT 100`
    - INSERT review_item with ON CONFLICT DO NOTHING **(A9)**
    - If rowcount == 0 → return None (active review already exists)
    - UPDATE company_files SET status='promoted', promoted_at=now
    - Audit log 'promote'
    - Return review_id
  - `check_promotion(company_file) -> bool` — evaluates rules:
    1. `len(_parse_source_apis(source_apis)) >= 2` (multi-source verification, derived at read time) **(A5)**
    2. `has_trusted_source()` — SEC, Companies House, Crunchbase
    3. `manually_promoted` — operator override
  - **`run_promotion_sweep(last_seen_cursor=None, company_id_cursor=None, limit=100)` — paginated with composite cursor (A7, A8):**
    ```python
    # Thin files eligible for first promotion
    SELECT company_id, last_seen_at FROM company_files
    WHERE status = 'thin' AND (last_seen_at, company_id) > (?, ?)
    ORDER BY last_seen_at ASC, company_id ASC
    LIMIT ?

    # Re-promotion: promoted files with no active review + new evidence (A7)
    SELECT cf.company_id, cf.last_seen_at FROM company_files cf
    WHERE cf.status = 'promoted'
      AND cf.company_id NOT IN (
          SELECT company_id FROM review_items
          WHERE status IN ('pending', 'approved', 'publish_queued'))
      AND cf.last_seen_at > COALESCE(
          (SELECT MAX(decided_at) FROM review_items WHERE company_id = cf.company_id),
          '1970-01-01')
      AND (cf.last_seen_at, cf.company_id) > (?, ?)
    ORDER BY cf.last_seen_at ASC, cf.company_id ASC
    LIMIT ?
    ```
    Returns `(processed_count, new_last_seen_cursor, new_company_id_cursor)` for next batch
  - `archive_stale_files(days=60)` — marks thin files with no new evidence as archived
- **Tests:** Promotion triggers on 2+ sources, trusted source, manual; archive after 60 days; reactivation appends source; sweep respects LIMIT; re-promotion triggers for promoted files with deferred/rejected reviews and new evidence (A7); composite cursor prevents skipped rows (A8)
- **Status:** pending

#### Task 10: Thin File GC (Garbage Collection)
- **Files:** `scripts/gc_thin_files.py`
- **What:**
  - Delete archived company_files older than 365 days (configurable via THIN_FILE_GC_RETENTION_DAYS)
  - Dry-run mode: report what would be purged
  - Apply mode: DELETE with **single summary audit_log entry (D3):**
    ```python
    details = {
        "deleted_count": len(deleted_files),
        "retention_days": retention_days,
        "cutoff": cutoff_iso,
        "company_ids": [f[0] for f in deleted_files[:100]]  # Sample for audit
    }
    ```
- **Tests:** GC purges old archived, preserves recent, dry-run doesn't delete, audit entry contains sample IDs
- **Status:** pending

### Group D: Integration & Wiring

#### Task 11: Pipeline Wiring
- **Files:** `workflows/pipeline.py`, `storage/signal_store.py`
- **What:**
  - **SignalStore.__init__ parameters (A14):**
    ```python
    def __init__(self, db_path, suppression_ttl_days=30,
                 identity_store: Optional[EntityIdentityStore] = None,
                 use_thin_files: bool = False):
    ```
    Validation: `use_thin_files=True` requires `identity_store` to be provided
  - **save_signal() changes:**
    - **Fail-fast guard at method entry (A4):**
      ```python
      if self._use_thin_files and not self._identity_store:
          raise RuntimeError(
              "use_thin_files requires Phase G identity store. "
              "Ensure entity_aliases table exists (migration 19+)."
          )
      ```
    - **Use `transaction_immediate()` when identity store is active (M1)** — unconditionally, not conditional per-call
    - Resolve company_id via `lookup_strong_keys([canonical_key])` before INSERT
    - If not found, generate via `EntityIdentityStore.entity_id_for_seed(canonical_key)`, **then register binding via `upsert_strong_key_bindings([StrongKeyBinding(strong_key=canonical_key, entity_id=new_id, source_signal_id=signal_id, source_key=source_api)], tx)` (A4)**
    - INSERT signal with resolved company_id
    - If `_use_thin_files`: call `upsert_company_file()` within same transaction
  - **Phase G merge cascade wiring (A6):**
    - When `upsert_strong_key_bindings()` returns merge pairs, each `(loser, winner)` pair MUST be processed through `cascade_merge(winner, loser, reason='identity_merge', actor='pipeline', tx=tx)` **(A12)**
    - This ensures signals.company_id, company_files.company_id, and review_items.company_id stay consistent when merges happen during normal pipeline operation
  - **Phase G table validation:**
    - Check both `entity_aliases` AND `entity_migrations` tables exist (not just one) **(A11)**
    - Error message: `"Phase 1a requires Phase G tables (migration 19). Missing: {tables}. Run: python -m storage.migrate --db signals.db"`
  - After collection phase: run promotion sweep on updated CompanyFiles (paginated)
  - Promoted files → auto-create ReviewItem with evidence bundle
  - Wire identity gate check at pipeline startup
- **Tests:** Full pipeline run creates company_files, promotes on 2+ sources, creates review_items, merge pairs cascade correctly
- **Status:** pending

#### Task 12: Performance Indexes + SLO Tests
- **Files:** `storage/migrations/v29_review_queue.py`, `tests/performance/test_phase1a_slos.py`
- **What:**
  - Indexes (in v28/v29 DDL):
    - idx_signals_company_id ON signals(company_id)
    - idx_signals_company_created ON signals(company_id, created_at DESC) **(A9)**
    - idx_review_status_created ON review_items(status, created_at)
    - idx_review_company_id ON review_items(company_id)
    - idx_company_file_status_seen ON company_files(status, last_seen_at)
    - ~~idx_aliases_company_id, idx_aliases_canonical_key~~ REMOVED (company_aliases eliminated)
    - ~~idx_company_file_sources~~ REMOVED (distinct_source_count eliminated)
  - **SLO tests with explicit thresholds:**
    ```python
    SLO_TARGETS = {
        'csv_export_ms': 2000,       # <2s for 500 ReviewItems
        'review_queue_ms': 500,      # <500ms for 1000 items
        'promotion_check_ms': 500,   # <500ms per check
    }
    ```
    Warn (don't hard-fail) if exceeded; print actual vs target
- **Tests:** Indexes exist, SLO benchmarks pass or warn
- **Status:** pending

#### Task 13: Integration Tests
- **Files:** `tests/integration/test_phase1a_identity.py`
- **What:**
  - End-to-end: create signals → company_files created → promotion → review_items
  - **Merge with review collision:** merge two companies that both have active reviews → verify loser's review deactivated, evidence merged in Python (A14), signals reassigned, audit logged **(A1)**
  - Gate: pipeline blocked with NULL company_id
  - State machine: full lifecycle pending→approved→published
  - Emergency halt: publish_queued → rejected **(A13)**
  - Backfill: dry-run + apply on test data, including root resolution for merged entities **(A5)**
  - **Merge cascade from pipeline:** upsert_strong_key_bindings triggers merge → verify cascade propagates **(A6)**
- **Tests:** All integration scenarios pass
- **Status:** pending

---

## Execution Order

```
Pre:   Fix brittle test INSERTs + StoredSignal + projections (pre-implementation checklist)
Phase 1: Write DDL files (Tasks 1, 6, 8 — can be one combined step) [Task 4 ELIMINATED]
Phase 2: Backfill script (Task 2) + State validator (Task 7) — parallel
Phase 3: Migration gate (Task 3) + cascade_merge (Task 5) + Promotion rules (Task 9) — parallel
Phase 4: Pipeline wiring (Task 11) — sequential, needs all above
Phase 5: GC script (Task 10) + Indexes (Task 12) — parallel
Phase 6: Integration tests (Task 13) — sequential, needs all above
```

## Files Created/Modified

**New files:**
- `docs/plans/identity-charter.md` — single source of truth for identity decisions
- `storage/migrations/v28_canonical_identity.py`
- `storage/migrations/v29_review_queue.py`
- `storage/migrations/backfill_v28_identity.py`
- `workflows/thin_file_manager.py`
- `ops/review_cli.py`
- `scripts/gc_thin_files.py`
- `tests/performance/test_phase1a_slos.py`
- `tests/integration/test_phase1a_identity.py`
- `tests/storage/test_v28_migration.py`
- `tests/storage/test_v29_migration.py`
- `tests/workflows/test_thin_file_manager.py`

**Modified files:**
- `storage/signal_store.py` — import new DDL, add MIGRATIONS v28+v29, add identity/merge/review methods, add `__init__` params for identity_store/use_thin_files, update StoredSignal + `_row_to_signal()` + SELECT projections
- `workflows/pipeline.py` — wire identity gate + company_file upsert + promotion + merge cascade
- `ops/quality_cli.py` — register review subcommands from review_cli.py
- `ops/quality/thesis.py` — update SELECT projection for company_id column
- `ops/quality/export.py` — update SELECT projection for company_id column
- `ops/quality/enrichment.py` — update SELECT projection for company_id column
- `run_pipeline.py` — update export-queue, triage, shadow backfill projections
- `view_signals.py`, `view_signals_detail.py` — update SELECT projections
- `tests/ops/quality/test_disagreement_report.py` — fix brittle INSERT INTO signals VALUES
- `.env.example` — add THIN_FILE_GC_RETENTION_DAYS

## Success Criteria

- [ ] Signals have stable company_id (SHA256[:16] via `entity_id_for_seed()`) + existing canonical_key
- [ ] Backfill dry-run passes (47 signals, 0 NULLs)
- [ ] Backfill resolves entity roots for already-merged entities
- [ ] New strong-key bindings registered on first signal for each canonical_key **(A4)**
- [ ] Pipeline refuses to run if any signal has NULL company_id
- [ ] State transitions enforced (invalid transitions raise error)
- [ ] Emergency halt (publish_queued → rejected) works via CLI
- [ ] Promotion rules work without exemplar similarity
- [ ] Re-promotion triggers for promoted files with deferred/rejected reviews and new evidence **(A7)**
- [ ] Promotion sweep is paginated with composite cursor (LIMIT 100) **(A8)**
- [ ] cascade_merge handles review_items UNIQUE constraint correctly
- [ ] All queries use tuple unpacking (no row_factory dependency)
- [ ] Performance SLOs met (warn threshold, not hard fail)
- [ ] Thin file GC purges archived >1 year with audit summary
- [ ] Pipeline merges cascade through signals + company_files + review_items
- [ ] All tests pass (unit + integration + SLO)

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Backfill corrupts data | Dry-run first, backup before apply, pipeline stopped (A15) |
| ID format mismatch | SHA256[:16] exclusively via `entity_id_for_seed()`; identity charter locks this down |
| Duplicate identity tables | Eliminated `company_aliases`; Phase G `entity_aliases` is authoritative |
| State machine bypassed | Enforce at storage layer, not CLI |
| Performance regression | Indexes + SLO tests in same PR |
| Breaking existing tests | Fix brittle INSERTs pre-implementation (A7); run full suite before merge |
| Review UNIQUE violation on merge | cascade_merge deactivates loser's review before reassigning (A1) |
| Stale company_id after merge | Pipeline processes merge pairs from upsert_strong_key_bindings through cascade (A6) |
| Concurrent backfill + pipeline | Document maintenance-mode prerequisite (A15) |
| SQL syntax errors in merge | Compute timestamps/aggregations in Python, not SQL (A3) |
| Dict-style row access crashes | Mandate explicit-column SELECTs + tuple unpacking throughout (A2) |

## Review Triage Reference

**v3 triage:** Items tagged A1-A15, M1-M5, D1-D3. Rejected items R1-R10 documented in memory-keeper checkpoint `phase1a-plan-v3-review-triage`.

**v4 triage (this version):** 14 accepted, 3 minor, 9 rejected from 3 independent reviewers (~45 raw comments). Key accepted items:
- **A1:** UUID5 → SHA256[:16] (all references updated)
- **A2:** company_aliases eliminated (Phase G entity_aliases authoritative)
- **A3:** Backfill reads existing canonical_key (no raw_data recomputation)
- **A4:** Register strong-key bindings on new ID generation
- **A5:** distinct_source_count eliminated (derived at read time)
- **A7:** Re-promotion for promoted files with no active review + new evidence
- **A8:** Composite pagination cursor `(last_seen_at, company_id)`
- **A9:** Compound index `idx_signals_company_created`
- **A10:** StoredSignal + ~12 projection updates catalogued in pre-implementation checklist
- **A11:** Phase G validation = migration 19 only (not 19-21)
- **A12:** cascade_merge accepts optional `tx` parameter
- **A13:** `entity_name` → `signals.company_name` (actual column name)
- **A14:** Evidence merge in Python (explicit json.loads + set union + json.dumps)

Rejected items: Backfill exclusive lock, signal circular dependency framing, source_apis as separate table, batch pipeline operations, evidence summary stats, promotion_check_sequence, disaster recovery tests, rollback procedures, FK on non-UNIQUE column. Full triage in plan file `harmonic-wishing-feigenbaum.md`.
