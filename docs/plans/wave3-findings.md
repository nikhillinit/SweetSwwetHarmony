# Wave 3 Hunter Sandbox — Research Findings

**Created:** 2026-02-09
**Status:** Active research

---

## 1. Existing Infrastructure (Available for Wave 3)

### 1.1 Run Manager (`workflows/run_manager.py`)
- Lifecycle: `QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED`
- Already has `RunType.HUNTER` defined
- `create_run()`, `start_run()`, `complete_run()`, `fail_run()`, `cancel_run()`
- Returns Pydantic `RunRecord` models
- `inputs_hash` for reproducibility
- `correlation_id` for tracing
- **Hunter uses:** Create hunter runs, track execution, store results

### 1.2 RBAC (`api/auth/rbac.py`)
- Already has `Permission.HUNTER_RUN` and `Permission.HUNTER_PROMOTE`
- ANALYST (operator) can run + promote; GP (admin) has all permissions
- `require_permission()` decorator for FastAPI endpoints
- `OperatorContext.from_request()` for signed audit identity
- **Hunter uses:** Gate run creation (operator+), gate promotion (operator+)

### 1.3 Audit Events (`storage/audit_events.py`)
- Immutable INSERT-only log
- `record_event_from_context(OperatorContext)` — preferred for API
- Tracks who/when/what/before/after/reason
- **Hunter uses:** Log run start, query execution, result feedback, promotions

### 1.4 API Contracts (`api/contracts.py`)
- `ErrorEnvelope`, `BaseResponse[T]`, `ListResponse[T]`
- Idempotency: in-memory L1 + SQLite L2 (24h TTL)
- Optimistic concurrency via `check_version()`
- `inputs_hash()`, `payload_fingerprint()`
- **Hunter uses:** All mutation endpoints use idempotency + concurrency

### 1.5 Pagination (`api/pagination.py`)
- `encode_cursor()` / `decode_cursor()` — base64 opaque strings
- `paginate_query()` — composite cursor SQL helper
- `build_page_meta()` — (page, next_cursor, has_more)
- **Hunter uses:** Paginate hunter results list

### 1.6 Instrumentation (`utils/instrumentation.py`)
- `metrics.increment("hunter.query.success")` / `metrics.timer("hunter.run")`
- Thread-safe, zero-dependency
- **Hunter uses:** Track query counts, execution latency, budget consumption

### 1.7 Exemplar Matcher (`intelligence/exemplar_matcher.py`)
- TF-IDF cosine similarity against thesis exemplar library
- `ExemplarMatcher.match(query_text, exemplars, threshold)` → `ExemplarMatchResult`
- `VETO_THRESHOLD = 0.75` for veto-eligible matches
- **Hunter uses:** Score hunter results for relevance via exemplar similarity

---

## 2. Entity Resolution (Wave 2 — Identity Shield)

### 2.1 Entity Identity Store (`storage/entity_identity_store.py`)
- Feature flag: `USE_PHASE_G_IDENTITY_RESOLUTION` (default false)
- ID: SHA256[:16] via `entity_id_for_seed()`
- Strong key lookups (domain, registry IDs) + weak alias lookups
- Blocking index for fuzzy candidate retrieval
- **Hunter uses:** Entity-normalized dedupe of hunter results

### 2.2 Shadow Evaluator + Merge Suggestions (Wave 2 complete)
- Shadow mode runs Phase G alongside Phase 1a, logs discrepancies
- Merge suggestions scored via Jaro-Winkler + shared aliases/domains
- **Hunter uses:** Canonical key resolution for already-known detection

### 2.3 Merge Cascade (`storage/merge_cascade.py`)
- 4-step atomic cascade (review_items → signals → company_files → audit)
- **Hunter uses:** Not directly — promotion creates new signals, doesn't merge

---

## 3. Collector Architecture

### 3.1 BaseCollector Pattern
- 16+ collectors inheriting from `BaseCollector`
- `_collect_signals()` abstract method
- Built-in: dedup checking, retry, rate limiting, error handling
- Each collector has specific search parameters / API endpoints

### 3.2 Collectors Available for Hunter Queries
| Collector | API Key Status | Hunter Viability |
|-----------|---------------|------------------|
| `github.py` | GITHUB_TOKEN ✅ | High — repo search API with topic/keyword queries |
| `hacker_news.py` | None needed | Medium — Algolia search API |
| `news_api.py` | GNEWS_API_KEY ✅ | Medium — keyword search |
| `sec_edgar.py` | None needed | Low — structured filing queries |
| `rss_feeds.py` | None needed | Low — category-based, not query-driven |
| `job_postings.py` | None needed | Medium — company name search via ATS |
| `domain_whois.py` | None needed | Low — requires known domains |

### 3.3 Query-Friendly Collectors (Phase 1 targets)
1. **GitHub** — `q=` parameter for repo search, topic filtering
2. **Hacker News** — Algolia search, Show HN filtering
3. **News API** — GNews keyword search with category filters

---

## 4. Quality Labels (Pattern Mining Source)

### 4.1 Tables
- `signal_quality_metrics` — latest label per signal (TP/FP/UNSURE)
- `quality_feedback` — raw audit trail of manual labels
- `thesis_classifications` — keyword + LLM classification results

### 4.2 Current Stats
- 47 total signals, 31 labeled (74.2% FP rate as of Phase 3)
- Labels: `TP`, `FP`, `UNSURE`
- Sources: `manual`, `notion_status_event`, `auto`

### 4.3 Pattern Mining Data Available
- `source_api` — which collector found each signal
- `category` — thesis category classification
- `confidence_score` — pipeline confidence
- `keyword_score` / `llm_score` — thesis matcher scores
- `raw_data` — JSON with source-specific fields (descriptions, topics, etc.)
- **Key insight:** With only ~14 TP signals, pattern mining must be conservative. Bootstrap mode (manual seed targets) is critical.

---

## 5. Migration State

### 5.1 Current: v38 (wave2_shadow_canary)
- Tables: shadow_entity_runs, shadow_disagreements, merge_suggestions, canary_runs, canary_drift_alerts

### 5.2 Wave 3 Needs: v39
- `hunter_queries` — generated search queries per run
- `hunter_results` — sandbox-isolated results (NEVER in signals table)
- `hunter_budget` — daily spend tracking per collector
- `hunter_negative_keywords` — auto-generated from operator rejects

### 5.3 Note on Roadmap vs Reality
- Roadmap originally said v38 for hunter tables, but v38 was consumed by Wave 2
- Wave 3 will use v39

---

## 6. API Router Pattern (from triage.py)

### 6.1 Action Pattern (for mutation endpoints)
1. Check idempotency BEFORE transaction
2. Open `transaction_immediate()`
3. Fetch current state
4. Optimistic concurrency via `check_version()`
5. Validate state transition
6. Inline SQL (avoid nested transactions)
7. Inline SQL for audit event
8. Store idempotency result
9. Commit

### 6.2 Read Pattern (for list/detail endpoints)
- Cursor-based pagination via `paginate_query()`
- `ListResponse[T]` wrapper with `ListMeta`
- Correlated subqueries for aggregated data

---

## 7. Dashboard Pattern (from triage_fast.py)

### 7.1 Architecture
- Sidebar filters → cursor pagination → data table → action buttons
- `st.session_state` for pagination state
- `@st.cache_data` with TTL + cache buster
- `APIClient()` for all data fetching (never direct DB)
- Filter changes reset cursor

### 7.2 Mock Pattern (for tests)
```python
class MockSessionState(dict):
    def __getattr__(self, key): return self.get(key)
    def __setattr__(self, key, value): self[key] = value

def _make_ctx_manager(value):
    @contextmanager
    def ctx():
        yield value
    return ctx
```

---

## 8. Critical Design Decisions (Pre-Planning)

### 8.1 Sandbox Isolation (HARD REQUIREMENT)
- Hunter results go to `hunter_results` table ONLY
- NEVER write to `signals` table until explicit promotion
- Promotion is a separate, RBAC-gated action
- Tests MUST verify isolation explicitly

### 8.2 Bootstrap Mode
- With only ~14 TPs, auto-pattern mining is weak
- Bootstrap mode: operator provides manual seed targets (company names, domains, categories)
- Seed targets generate initial queries without requiring statistical patterns

### 8.3 Entity-Normalized Dedupe
- Before storing a hunter result, resolve canonical key + entity ID
- Skip if entity already exists in signals table ("already known")
- Skip if entity already exists in hunter_results for this run ("duplicate")

### 8.4 Budget Controls
- `MAX_DAILY_QUERIES` per collector (env var, default 50)
- `MAX_DAILY_COST_UNITS` total (env var, default 100)
- Circuit breaker: stop all queries when budget exhausted
- Alert via instrumentation metrics

### 8.5 Negative Feedback Loop
- When operator marks result as "not_relevant" → extract keywords
- Store in `hunter_negative_keywords` table
- Query generator excludes negative keywords from future queries
- 30-day cooldown before auto-deactivation
