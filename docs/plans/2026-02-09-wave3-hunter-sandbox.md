# Wave 3: Hunter Sandbox — Implementation Plan

**Created:** 2026-02-09
**Revision:** v1
**Depends on:** Wave 2 COMPLETE (shadow entity resolution + canary baseline)
**Duration:** ~30-34h (13 tasks across 4 phases)
**Branch:** `feature/wave3-hunter-sandbox`
**Related:** `docs/plans/2026-02-09-phase4-full-roadmap.md` (master roadmap, Wave 3 = 4c)

---

## Goal

Pattern-driven query generation with sandbox isolation, budget controls, and quality feedback loop. Runs AFTER identity shield is established (Wave 2). Hunter results are fully isolated from the main `signals` table until explicit operator promotion.

---

## Non-Negotiable Constraints

1. **Sandbox isolation** — Hunter NEVER writes to `signals` table. All results go to `hunter_results` only.
2. **Budget enforcement** — Hard caps on daily queries per collector and total cost. Circuit breaker stops all queries when exhausted.
3. **Entity-normalized dedupe** — Resolve canonical key before storing. Skip "already known" entities.
4. **RBAC-gated promotion** — Only `operator` or higher can promote results to signals pipeline.
5. **Audit trail** — Every run, feedback action, and promotion creates an immutable audit event.
6. **TDD discipline** — Write failing tests before implementation for every task.

---

## Phase A: Foundation (Tasks 1-3, ~8h)

### Task 1: Migration v39 — Hunter Tables DDL
**Est:** 1h | **Roadmap ref:** 4c.3

**Tables:**

```sql
-- hunter_queries: generated search queries per run
CREATE TABLE hunter_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,               -- FK to run_history.run_id
    collector TEXT NOT NULL,             -- e.g., 'github', 'hacker_news'
    query_text TEXT NOT NULL,
    query_type TEXT NOT NULL DEFAULT 'pattern',  -- 'pattern', 'bootstrap', 'manual'
    source_pattern TEXT,                 -- pattern key that generated this
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, executing, completed, failed, skipped
    results_count INTEGER DEFAULT 0,
    cost_units REAL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    error_message TEXT,
    metadata TEXT                        -- JSON
);

-- hunter_results: sandbox-isolated results (NEVER in signals table)
CREATE TABLE hunter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    query_id INTEGER NOT NULL,
    company_name TEXT NOT NULL,
    canonical_key TEXT,
    company_id TEXT,                     -- entity-normalized ID
    source_api TEXT NOT NULL,
    raw_data TEXT,                       -- JSON
    confidence_score REAL,
    exemplar_similarity REAL,
    thesis_fit_score REAL,
    already_known INTEGER NOT NULL DEFAULT 0,  -- 1 if entity exists in signals
    status TEXT NOT NULL DEFAULT 'pending',     -- pending, relevant, not_relevant, already_known, promoted
    operator_feedback TEXT,
    promoted_signal_id INTEGER,           -- FK to signals.id after promotion
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    promoted_at TEXT,
    updated_at TEXT NOT NULL,
    metadata TEXT,
    FOREIGN KEY(query_id) REFERENCES hunter_queries(id)
);

-- hunter_budget: daily spend tracking per collector
CREATE TABLE hunter_budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_date TEXT NOT NULL,            -- YYYY-MM-DD
    collector TEXT NOT NULL,
    queries_executed INTEGER DEFAULT 0,
    queries_cap INTEGER,
    cost_units REAL DEFAULT 0.0,
    cost_cap REAL,
    circuit_breaker_tripped INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(budget_date, collector)
);

-- hunter_negative_keywords: auto-generated from operator rejects
CREATE TABLE hunter_negative_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL,
    collector TEXT,                       -- NULL = all collectors
    source TEXT NOT NULL,                 -- 'operator_reject', 'manual'
    source_result_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    deactivated_at TEXT,
    metadata TEXT
);
```

**Indexes:**
- `hunter_queries`: (run_id), (collector, status), (created_at DESC)
- `hunter_results`: (run_id), (query_id), (canonical_key), (company_id), (status), (created_at DESC)
- `hunter_budget`: (budget_date, collector) UNIQUE already covers lookups
- `hunter_negative_keywords`: (keyword, collector, active), (source_result_id)

**Files:**
- `storage/migrations/v39_active_hunter.py`

**Tests:**
- `tests/storage/test_v39_active_hunter.py` — DDL creation, indexes exist, UNIQUE constraints work

**DoD:** Migration applies cleanly. Downgrade drops tables. All indexes verified.

---

### Task 2: Pattern Miner — Mine TP Archetypes for Query Templates
**Est:** 3h | **Roadmap ref:** 4c.1

**Purpose:** Extract recurring patterns from labeled TP signals to generate search query templates.

**Algorithm:**
1. Query `signal_quality_metrics` WHERE `human_label = 'TP'`
2. Join with `signals` for `source_api`, `raw_data`, `category`, `confidence_score`
3. Extract features per TP:
   - Source collector distribution
   - Category concentration (CPG, health tech, travel, marketplace)
   - Common keywords from descriptions/names (TF-IDF top-N)
   - Confidence score ranges
   - Signal type patterns
4. Cluster into "archetypes" (e.g., "health-tech GitHub trending", "CPG SEC filing")
5. For each archetype, produce a `QueryTemplate`:
   - `collector`: which collector to target
   - `keywords`: positive search terms
   - `categories`: thesis categories
   - `example_companies`: TP company names for reference
   - `min_confidence`: archetype's typical confidence floor

**Bootstrap mode:** When TP count < 20, accept manual seed targets:
- `ManualSeed(company_name, domain, category, reason)`
- Generates queries directly from seeds without statistical patterns

**Files:**
- `intelligence/pattern_miner.py`

**Key classes:**
- `Archetype` — dataclass with collector, keywords, categories, example_companies
- `QueryTemplate` — dataclass with collector, query_text, source_archetype, priority
- `PatternMiner` — main class with `mine_archetypes(db)` and `generate_templates(archetypes)`
- `BootstrapSeeder` — `from_manual_seeds(seeds)` → list of QueryTemplate

**Tests:**
- `tests/intelligence/test_pattern_miner.py`
- Cases: empty TP set → empty archetypes, single TP → single archetype, multiple TPs → clustered archetypes
- Bootstrap mode: manual seeds → valid templates
- Edge: all TPs from same collector → single-collector archetype

**DoD:** Pattern miner produces valid query templates from both TP labels and manual seeds. Pure function, no side effects.

---

### Task 3: Query Generator — Collector-Specific Queries with Bootstrap
**Est:** 4h | **Roadmap ref:** 4c.2

**Purpose:** Convert `QueryTemplate` objects into collector-specific, executable search queries.

**Architecture:**
```
QueryTemplate → QueryGenerator.generate(template) → List[HunterQuery]
```

**Per-collector query strategies:**
| Collector | Query Strategy |
|-----------|---------------|
| `github` | GitHub Search API: `q={keywords} topic:{category} stars:>10 created:>{date}` |
| `hacker_news` | Algolia: `search?query={keywords}&tags=show_hn` |
| `news_api` | GNews: `search?q={keywords}&category={category}` |
| `job_postings` | Greenhouse/Lever company search (limited — needs domain) |

**Negative keyword filtering:**
- Load active keywords from `hunter_negative_keywords` table
- Append `-keyword` to GitHub queries, exclude from HN/GNews results
- Skip queries that are 100% covered by negative keywords

**Deduplication:**
- `inputs_hash` on (collector, query_text, date_range) to avoid re-running identical queries

**Files:**
- `intelligence/query_generator.py`

**Key classes:**
- `HunterQuery` — dataclass with collector, query_text, query_params, priority, source_template
- `QueryGenerator` — main class with `generate(templates, negative_keywords)` → `List[HunterQuery]`
- Per-collector formatters: `_format_github_query()`, `_format_hn_query()`, `_format_news_query()`

**Tests:**
- `tests/intelligence/test_query_generator.py`
- Cases: template → valid queries per collector, negative keywords exclusion, dedup check
- Edge: empty templates → no queries, all-negative → all skipped

**DoD:** Generator produces well-formed, collector-specific queries. Negative keywords respected. No side effects.

---

## Phase B: Sandbox Core (Tasks 4-7, ~10h)

### Task 4: Hunter Sandbox — Isolated Execution + Entity-Normalized Dedupe
**Est:** 4h | **Roadmap ref:** 4c.4

**Purpose:** Execute hunter queries against collectors in an isolated sandbox. Results go to `hunter_results` only.

**Architecture:**
```
HunterQuery → ActiveHunter.execute(query) → List[HunterResult] → hunter_results table
```

**Execution flow:**
1. Create run via `run_manager.create_run(RunType.HUNTER, ...)`
2. For each `HunterQuery`:
   a. Check budget (abort if exhausted)
   b. Set query status → `executing`
   c. Invoke collector's search method (reuse existing collector infrastructure)
   d. For each raw result:
      - Build canonical key (via `build_canonical_key()`)
      - Resolve entity ID (via Phase 1a `company_id` logic, or Phase G if enabled)
      - Check "already known" (exists in `signals` table?)
      - Check duplicate (already in `hunter_results` for this run?)
      - Score exemplar similarity (via `ExemplarMatcher`)
      - INSERT into `hunter_results`
   e. Update query `results_count` and `cost_units`
   f. Set query status → `completed` / `failed`
3. Complete run via `run_manager.complete_run()`

**Entity-normalized dedupe:**
- Use `canonical_key` from `utils/canonical_keys.py` (existing)
- Cross-reference against `signals.canonical_key` for "already known" detection
- Cross-reference against `hunter_results.canonical_key` within same run for duplicates

**CRITICAL:** The sandbox MUST NOT import or call any function that writes to `signals` table. Collector results are converted to `HunterResult` objects, NOT `Signal` objects.

**Files:**
- `workflows/active_hunter.py`

**Key classes:**
- `HunterResult` — dataclass (not Signal!) with company_name, canonical_key, company_id, raw_data, scores
- `ActiveHunter` — main class with `execute_run(queries, db)`, `execute_query(query, db)`
- `HunterConfig` — env-based config (max queries, budget caps, enabled collectors)

**Tests:**
- `tests/workflows/test_active_hunter.py` (partial — sandbox safety tests in Task 11)
- Cases: single query → results in hunter_results, already-known → flagged, duplicate → skipped
- Verify: run lifecycle (QUEUED → RUNNING → COMPLETED)

**DoD:** Queries execute, results stored in `hunter_results` with entity dedupe. Run lifecycle tracked.

---

### Task 5: Cost Circuit Breaker — Budget Caps + Alerts
**Est:** 2h | **Roadmap ref:** 4c.5

**Purpose:** Prevent runaway API costs. Hard caps enforced before every query execution.

**Config (env vars):**
```
HUNTER_MAX_DAILY_QUERIES=50        # per collector
HUNTER_MAX_DAILY_COST_UNITS=100    # total across all collectors
HUNTER_CIRCUIT_BREAKER_RESET=daily # reset at midnight UTC
```

**Flow:**
1. Before each query execution: `check_budget(collector, date) → BudgetStatus`
2. If `BudgetStatus.exhausted` → skip query, set status `skipped`, log alert
3. After each query: `record_spend(collector, date, queries=1, cost_units=N)`
4. When ANY collector trips breaker: `metrics.increment("hunter.circuit_breaker.tripped")`

**Storage:** Upsert into `hunter_budget` table (UNIQUE on date + collector).

**Files:**
- Add to `workflows/active_hunter.py` (budget methods on `ActiveHunter`)

**Key functions:**
- `check_budget(db, collector, date) → BudgetStatus` (dataclass: allowed, remaining_queries, remaining_cost)
- `record_spend(db, collector, date, queries, cost_units)`
- `get_daily_budget_summary(db, date) → Dict[collector, BudgetStatus]`

**Tests:**
- Add to `tests/workflows/test_active_hunter.py`
- Cases: under budget → allowed, at limit → denied, breaker tripped → alert logged
- Reset: next day → fresh budget

**DoD:** No query executes when budget exhausted. Metrics track breaker trips.

---

### Task 6: Quality Scorer — Exemplar Similarity + Operator Feedback
**Est:** 2h | **Roadmap ref:** 4c.6

**Purpose:** Score hunter results for relevance; accept operator feedback.

**Scoring pipeline (per result):**
1. Build corpus text from `raw_data` (company name + description + tags)
2. Run `ExemplarMatcher.match()` → `exemplar_similarity` score
3. Run keyword thesis matcher → `thesis_fit_score`
4. Store scores in `hunter_results`

**Operator feedback (via API/CLI):**
- `relevant` — mark as candidate for promotion
- `not_relevant` — reject, feed into negative feedback loop (Task 7)
- `already_known` — duplicate that dedupe missed, log for improvement

**Feedback updates:**
```sql
UPDATE hunter_results
SET status = ?, operator_feedback = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
WHERE id = ? AND updated_at = ?  -- optimistic concurrency
```

**Files:**
- Add scoring to `workflows/active_hunter.py`
- Feedback API in Task 9

**Tests:**
- Add to `tests/workflows/test_active_hunter.py`
- Cases: high exemplar similarity → high score, no match → zero, feedback updates status

**DoD:** Every result has exemplar_similarity + thesis_fit_score. Feedback changes status atomically.

---

### Task 7: Negative Feedback Loop — Auto-Negative Keywords from Rejects
**Est:** 2h | **Roadmap ref:** 4c.7

**Purpose:** When operator marks result "not_relevant", extract keywords to suppress similar future queries.

**Algorithm:**
1. On `not_relevant` feedback → extract top-N keywords from result's `raw_data`
2. Filter: only keep keywords that appear in 2+ rejected results (prevents over-fitting to single reject)
3. INSERT into `hunter_negative_keywords` with `source='operator_reject'`
4. Query generator (Task 3) loads active negative keywords and excludes from future queries

**Deactivation policy:**
- Keywords auto-deactivate after 90 days (configurable via `HUNTER_NEG_KEYWORD_TTL_DAYS`)
- Manual deactivation via CLI/API

**Files:**
- Add to `intelligence/query_generator.py` (negative keyword loading + application)
- Add extraction logic to `workflows/active_hunter.py`

**Key functions:**
- `extract_negative_keywords(result_raw_data) → List[str]`
- `update_negative_keywords(db, keywords, source_result_id)`
- `load_active_negative_keywords(db, collector) → List[str]`

**Tests:**
- Add to `tests/intelligence/test_query_generator.py`
- Cases: reject → keywords extracted, keyword used → query modified, TTL expiry → deactivated

**DoD:** Rejected results produce negative keywords. Future queries exclude them. TTL-based cleanup works.

---

## Phase C: Interface Layer (Tasks 8-10, ~8h)

### Task 8: CLI — `run_pipeline.py hunter` Subcommand
**Est:** 3h | **Roadmap ref:** 4c.8

**Purpose:** Operator-facing CLI for hunter workflow.

**Subcommands:**
```bash
# Generate queries from patterns (or bootstrap seeds)
python run_pipeline.py hunter generate [--bootstrap seeds.json] [--collectors github,hacker_news]

# Execute generated queries in sandbox
python run_pipeline.py hunter run [--run-id <id>] [--dry-run] [--max-queries 10]

# Review results (list pending, filter by score)
python run_pipeline.py hunter review [--status pending] [--min-score 0.5] [--limit 20]

# Provide feedback on a result
python run_pipeline.py hunter feedback <result_id> <relevant|not_relevant|already_known> [--reason "..."]

# Promote approved results to signals pipeline
python run_pipeline.py hunter promote <result_id> [--dry-run]

# View budget status
python run_pipeline.py hunter budget [--date 2026-02-09]

# View run status
python run_pipeline.py hunter status [--run-id <id>]
```

**Integration with run_manager:**
- `hunter generate` creates a `RunType.HUNTER` run, stores queries, returns run_id
- `hunter run` starts execution, uses run_manager lifecycle
- `hunter status` polls via `run_manager.get_run()`

**Files:**
- Extend `run_pipeline.py` with `hunter` subparser + handler functions

**Tests:**
- `tests/test_hunter_cli.py`
- Cases: generate with bootstrap → queries created, run → results stored, feedback → status updated, promote → signal created
- Dry-run mode: no mutations

**DoD:** All 7 subcommands work. Dry-run mode verified. Output is human-readable.

---

### Task 9: Hunter API Router — CRUD + RBAC + Idempotency
**Est:** 2h | **Roadmap ref:** 4c.9

**Purpose:** FastAPI endpoints for hunter operations (used by dashboard and external clients).

**Endpoints:**
```
GET  /hunter/runs                  — list runs (cursor-paginated)
GET  /hunter/runs/{run_id}         — run detail + status
POST /hunter/runs                  — create run (generate + execute) [operator+]
GET  /hunter/runs/{run_id}/queries — list queries for run
GET  /hunter/runs/{run_id}/results — list results (cursor-paginated, filterable)
POST /hunter/results/{id}/feedback — submit feedback [operator+]
POST /hunter/results/{id}/promote  — promote to signals [operator+]
GET  /hunter/budget                — current budget status
GET  /hunter/budget/history        — budget history (last 30 days)
```

**RBAC:**
- All read endpoints: `Permission.VIEW` (viewer+)
- Create run: `Permission.HUNTER_RUN` (operator+)
- Submit feedback: `Permission.HUNTER_RUN` (operator+)
- Promote: `Permission.HUNTER_PROMOTE` (operator+)

**Idempotency:** All POST endpoints use `X-Idempotency-Key` via `check_idempotency_db()`.

**Response DTOs:**
- `HunterRunResponse` — run_id, status, query_count, result_count, budget_used
- `HunterQueryResponse` — id, collector, query_text, status, results_count
- `HunterResultResponse` — id, company_name, canonical_key, scores, status, feedback
- `BudgetResponse` — per-collector caps/usage/remaining

**Files:**
- `api/routers/hunter.py`
- Wire into `api/main.py`

**Tests:**
- `tests/api/test_hunter_router.py`
- Cases: RBAC enforcement (viewer can't create run), idempotent create, pagination, feedback flow

**DoD:** All endpoints work. RBAC enforced. Idempotency tested. Wired into FastAPI app.

---

### Task 10: Hunter Dashboard View — Queries, Results, Feedback, Promote
**Est:** 3h | **Roadmap ref:** 4c.10

**Purpose:** Streamlit dashboard for operator-friendly hunter management.

**Layout:**
```
┌─────────────────────────────────────────────────┐
│ Hunter Sandbox                                   │
├───────────┬─────────────────────────────────────┤
│ Sidebar   │ Main Content                         │
│           │                                      │
│ Run       │ [Tab: Queries] [Tab: Results] [Tab: Budget] │
│ Selection │                                      │
│           │ Queries Tab:                         │
│ Filters:  │  - Table: collector, query, status   │
│ - Status  │  - Status badges                     │
│ - Score   │                                      │
│ - Collect │ Results Tab:                         │
│           │  - Table: company, scores, status    │
│ Actions:  │  - Inline feedback buttons           │
│ [Generate]│  - Promote button (with confirm)     │
│ [Run]     │                                      │
│           │ Budget Tab:                          │
│           │  - Per-collector usage bars           │
│           │  - Daily history chart               │
└───────────┴─────────────────────────────────────┘
```

**Patterns (following triage_fast.py):**
- `st.session_state` for run selection, pagination, cache buster
- `APIClient()` for all data fetching
- Cursor-based pagination for results
- Confirm dialog before promotion
- Cache buster on feedback/promote actions

**Files:**
- `dashboard/views/hunter.py`
- Wire into `dashboard/app.py` navigation

**Tests:**
- `tests/dashboard/test_hunter_view.py`
- Cases: render with no runs, render with results, feedback button click, promote confirm

**DoD:** Dashboard renders. Filters work. Feedback updates. Promote with confirmation. Wired into nav.

---

## Phase D: Safety & Integration Tests (Tasks 11-13, ~8h)

### Task 11: Sandbox Safety Tests — Hunter NEVER Writes to Signals
**Est:** 3h | **Roadmap ref:** 4c.11

**Purpose:** Explicit verification that the hunter sandbox cannot contaminate the signals table.

**Test strategy:**
1. **Import isolation test:** Verify `workflows/active_hunter.py` never imports signal-writing functions
2. **Write isolation test:** Run full hunter cycle, assert `signals` table row count unchanged
3. **Promotion guard test:** Promotion ONLY via explicit `promote_result()` call with RBAC check
4. **SQL injection test:** Malicious query text cannot escape hunter_results table
5. **Transaction isolation test:** Hunter write failure doesn't affect signals table state
6. **Concurrent run test:** Two simultaneous hunter runs don't cross-contaminate results

**Files:**
- `tests/workflows/test_hunter_sandbox_safety.py`

**Tests (minimum 12):**
1. `test_hunter_does_not_import_signal_store_write` — AST/import check
2. `test_signals_table_unchanged_after_hunter_run` — row count before/after
3. `test_signals_table_unchanged_after_hunter_failure` — failed run still safe
4. `test_promotion_requires_rbac` — viewer cannot promote
5. `test_promotion_creates_audit_event` — audit trail on promote
6. `test_promotion_sets_promoted_signal_id` — FK linkage verified
7. `test_promoted_result_appears_in_signals` — end-to-end promote
8. `test_duplicate_promotion_idempotent` — same result promoted twice → same signal
9. `test_concurrent_runs_isolated` — results separated by run_id
10. `test_budget_exhaustion_stops_all_queries` — circuit breaker verified
11. `test_negative_keywords_prevent_future_queries` — feedback loop works
12. `test_entity_dedupe_flags_already_known` — existing entity → already_known=1

**DoD:** All 12 safety tests pass. No scenario allows hunter to write to signals without explicit promotion.

---

### Task 12: Tests — Pattern Miner, Query Generator, Budget, Feedback
**Est:** 3h | **Roadmap ref:** 4c.12

**Purpose:** Unit tests for intelligence and workflow modules.

**Test files:**
- `tests/intelligence/test_pattern_miner.py` (~15 tests)
  - Empty TP set, single TP, multiple TPs, bootstrap mode
  - Category clustering, keyword extraction, archetype formation
  - Edge: all same collector, all same category

- `tests/intelligence/test_query_generator.py` (~15 tests)
  - Template → GitHub query, HN query, News query
  - Negative keyword exclusion
  - Dedup by inputs_hash
  - Edge: empty templates, all-negative, unsupported collector

**DoD:** ~30 unit tests covering pattern mining and query generation comprehensively.

---

### Task 13: Tests — Hunter API + CLI
**Est:** 2h | **Roadmap ref:** 4c.13

**Purpose:** API endpoint tests and CLI integration tests.

**Test files:**
- `tests/api/test_hunter_router.py` (~15 tests)
  - RBAC: viewer can read, operator can run/promote, admin can do all
  - Idempotency: duplicate create → same run_id
  - Pagination: results list with cursor
  - Feedback: valid transitions, optimistic concurrency
  - Promote: creates signal, audit event, idempotent

- `tests/test_hunter_cli.py` (~10 tests)
  - `hunter generate` with bootstrap seeds
  - `hunter run` with dry-run
  - `hunter review` with filters
  - `hunter feedback` valid + invalid
  - `hunter promote` with dry-run
  - `hunter budget` display
  - `hunter status` polling

**DoD:** ~25 API + CLI tests pass. RBAC verified. Idempotency verified.

---

## Migration Plan

| Version | Tables | Dependencies |
|---------|--------|--------------|
| v39 | `hunter_queries`, `hunter_results`, `hunter_budget`, `hunter_negative_keywords` | v38 (Wave 2) |

Downgrade: DROP all 4 tables + indexes.

---

## Files Created (Summary)

| File | Phase | Purpose |
|------|-------|---------|
| `storage/migrations/v39_active_hunter.py` | A | DDL: 4 hunter tables |
| `intelligence/pattern_miner.py` | A | Mine TP archetypes + bootstrap |
| `intelligence/query_generator.py` | A | Collector-specific query formatting |
| `workflows/active_hunter.py` | B | Sandbox execution + budget + scoring + feedback |
| `run_pipeline.py` (extend) | C | `hunter` subcommand (7 sub-subcommands) |
| `api/routers/hunter.py` | C | 9 API endpoints |
| `api/main.py` (extend) | C | Wire hunter router |
| `dashboard/views/hunter.py` | C | Streamlit hunter view |
| `dashboard/app.py` (extend) | C | Wire hunter into nav |
| `tests/storage/test_v39_active_hunter.py` | A | DDL tests |
| `tests/intelligence/test_pattern_miner.py` | D | Pattern miner tests (~15) |
| `tests/intelligence/test_query_generator.py` | D | Query generator tests (~15) |
| `tests/workflows/test_active_hunter.py` | B | Core workflow tests |
| `tests/workflows/test_hunter_sandbox_safety.py` | D | Safety isolation tests (12) |
| `tests/api/test_hunter_router.py` | D | API endpoint tests (~15) |
| `tests/dashboard/test_hunter_view.py` | C | Dashboard view tests |
| `tests/test_hunter_cli.py` | D | CLI integration tests (~10) |

---

## Dependency Graph

```
Task 1 (DDL v39)
  ├─► Task 2 (Pattern Miner) ──► Task 3 (Query Generator)
  │                                    │
  │                                    ▼
  ├─► Task 4 (Sandbox) ◄──────── Task 3
  │     │
  │     ├─► Task 5 (Budget)
  │     ├─► Task 6 (Scoring)
  │     └─► Task 7 (Negative Feedback) ◄── Task 6
  │
  ├─► Task 8 (CLI) ◄──── Tasks 4-7
  ├─► Task 9 (API) ◄──── Tasks 4-7
  └─► Task 10 (Dashboard) ◄── Task 9
        │
        ▼
  Tasks 11-13 (Safety + Integration Tests) ◄── Tasks 1-10
```

**Critical path:** T1 → T2 → T3 → T4 → T5/T6/T7 → T8/T9 → T10 → T11-13

---

## Estimated Hours

| Phase | Tasks | Hours | Cumulative |
|-------|-------|-------|------------|
| A: Foundation | 1-3 | 8h | 8h |
| B: Sandbox Core | 4-7 | 10h | 18h |
| C: Interface | 8-10 | 8h | 26h |
| D: Safety & Integration | 11-13 | 8h | 34h |
| **Total** | **13** | **34h** | |

---

## Gate Criteria (Wave 3 → Wave 4)

1. **Sandbox precision acceptable** — Hunter results have meaningful relevance scores
2. **Spend controls verified** — Budget circuit breaker tested for 3+ consecutive windows
3. **No accidental signals writes** — All 12 safety tests pass
4. **Negative feedback loop active** — Rejected results produce negative keywords, future queries exclude them
5. **All previous wave tests pass** — 1399+ existing tests still green

---

## Definition of Done

- **User-facing:** Operator can generate queries, run in sandbox, review results, provide feedback, promote approved results
- **Data:** hunter_results isolated from signals. Budget tracked per-collector per-day. Negative keywords persisted.
- **Reliability:** Budget circuit breaker enforced. Dedupe by canonical_key prevents duplicate processing.
- **Security:** Promotion requires `operator` role. Sandbox writes isolated (tested explicitly).
- **Tests:** ~60-80 new tests (target: 1460+ total)
