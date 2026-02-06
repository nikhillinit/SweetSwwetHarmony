# Task Plan: Phase 7 — Production Hardening

## Goal
Harden the Discovery Engine for production reliability by adding global exception handling, circuit breakers, health check caching, store singletons, structured logging, query parameter bounds, and graceful shutdown — all with TDD and zero regressions.

## Current Phase
ALL PHASES COMPLETE

## Context: Existing Baseline (Phases 0-6 Complete)

| Component | File | Key Details |
|-----------|------|-------------|
| Retry strategy | `collectors/retry_strategy.py` | Exponential backoff, jitter, Retry-After |
| Timeout config | `collectors/timeout_config.py` | Per-operation timeouts (search/enrich/download) |
| Base collector | `collectors/base.py` | Rate limiting, retry, timeout telemetry, batch error isolation |
| Pipeline orchestrator | `workflows/pipeline.py` | `asyncio.gather(return_exceptions=True)` for collector isolation |
| FastAPI app | `api/main.py` | Lifespan, CORS, 8 routers, no middleware |
| Health endpoints | `api/routers/health.py` | /health/detailed, /collectors, /database, /ops, /ops/metrics, rules CRUD |
| Ops monitoring | `ops/monitoring/` | Metrics, alerts, notifier, rule evaluator |
| Ops storage | `ops/storage.py` | SQLite/WAL, transactions, read_transaction, 3 alert tables |

### Current Test Counts
- 305 ops tests, 52 API tests, 74 dashboard tests, + storage/integration tests ≈ 431+ total

### What Phase 7 Fixes
1. **No global exception middleware** — raw 500s with stack traces leak to clients
2. **No circuit breakers** — failed services get hammered endlessly
3. **New store per request in health router** — connection churn, potential locks
4. **No query parameter bounds** — expensive queries via unbounded params
5. **No health check caching** — DB hammered on repeated health calls
6. **No request ID tracking** — can't correlate errors across logs
7. **No structured logging** — hard to aggregate/search in production
8. **No graceful shutdown** — in-flight requests can be dropped

## Phases

### Phase 1: Research & Architecture Design
- [x] Audit existing error handling, retry, health endpoints, API setup
- [x] Identify 10 production hardening gaps with severity ratings
- [x] Document findings in findings.md
- [x] Design solutions that are minimally invasive
- **Status:** complete

### Phase 2: Global Exception Handling & Request IDs (TDD)
- [x] Create `api/middleware.py` with:
  - `ExceptionHandlerMiddleware` — catch unhandled exceptions, return JSON 500 (no stack traces)
  - `RequestIdMiddleware` — inject `X-Request-ID` UUID header, add to logging context
- [x] Wire into `api/main.py` via `app.add_middleware()`
- [x] Tests: unhandled exceptions return clean JSON, request ID propagated in response headers
- **Status:** complete (11 tests, 67 total API tests passing)

### Phase 3: Circuit Breaker Pattern (TDD)
- [x] Create `utils/circuit_breaker.py`:
  - `CircuitBreaker` class with states: CLOSED → OPEN → HALF_OPEN
  - Configurable failure threshold, recovery timeout, half-open success count
  - Async `call()` method + `@protect` decorator
  - `stats()` and `reset()` for observability
- [x] Integrate into `connectors/notion_transport.py` (wraps retry loop)
- [x] Tests: 13 tests covering state transitions, trip on threshold, recovery, decorator, stats
- **Status:** complete (13 tests, 80 total passing)

### Phase 4: Store Singletons & Health Caching (TDD)
- [x] Fix `get_store()` to use `request.app.state.store` (fallback for tests)
- [x] Cache `_get_ops_storage()`, `_get_ops_collector()`, `_get_ops_alert_engine()` as singletons
- [x] Create `api/health_cache.py` — `ttl_cache()` decorator (TTL, per-arg, cache_clear)
- [x] Create `api/health_bounds.py` — `BoundedParams` with `hours`, `limit`, `window_hours`, `history_days`
- [x] Wire bounds into `/ops/metrics` and `/ops/history` endpoints
- [x] Tests: 13 tests (4 cache + 9 bounds), 80 total API tests passing
- **Status:** complete

### Phase 5: Structured Logging & Graceful Shutdown (TDD)
- [x] Create `utils/logging_config.py`:
  - `configure_logging(json_format, stream)` — JSON or text formatter
  - `RequestIdFilter` — injects `request_id` from contextvars into log records
  - `set_request_id()` / `get_request_id()` — context variable helpers
  - `startup_check()` — validates DB file exists
  - Log level from `LOG_LEVEL` env var (default INFO)
- [x] Wire `configure_logging()` + `startup_check()` into `api/main.py` lifespan
- [x] Connect `RequestIdMiddleware` to `set_request_id()` for log propagation
- [x] Add startup/shutdown logging to lifespan
- [x] Tests: 9 tests (5 configure + 2 filter + 2 startup), 102 total passing
- **Status:** complete

### Phase 6: Verification & Cleanup
- [x] Run full test suite — 493 passed (317 ops + 74 dashboard + 102 API/new), 0 failures
- [x] Verify health endpoints return clean JSON errors (no stack traces) — tested in test_middleware.py
- [x] Verify circuit breaker trips and recovers correctly — tested in test_circuit_breaker.py
- [x] Verify request IDs appear in response headers and logs — tested in test_middleware.py + test_logging_config.py
- [x] Update MEMORY.md with Phase 7 completion state
- [x] PR-ready: clean branch, meaningful commit
- **Status:** complete

## Key Questions
1. Should circuit breaker be per-service or per-endpoint? → **Per-service** (e.g., one CB for all Notion calls, one for GitHub API)
2. Health cache TTL? → **30 seconds** (balances freshness vs. DB load)
3. JSON logging always or only when env var set? → **Only when `LOG_FORMAT=json`** (human-readable default)
4. Where to store circuit breaker state? → **In-memory** (resets on restart, which is acceptable)
5. Should middleware catch all exceptions or only specific ones? → **All unhandled** (catch-all safety net)

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Middleware pattern for exceptions | Non-invasive, applies globally, no per-endpoint changes |
| Circuit breaker as utility class | Reusable, composable with existing retry strategy |
| Lifespan-managed store singletons | Eliminates per-request connection churn |
| In-memory TTL cache (no Redis) | No new dependencies, sufficient for single-process API |
| Query param bounds via Pydantic `Query()` | FastAPI-native validation, returns 422 automatically |
| `LOG_FORMAT=json` env toggle | Dev-friendly default, production JSON via config |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes
- Never nest `transaction()` calls — use `read_transaction()` for reads
- Windows console: `sys.stdout.reconfigure(errors='replace')` in CLI
- Store tests: `PRAGMA foreign_keys = OFF` for ops-only test DBs
- Test baseline: 305 ops + 52 API + 74 dashboard ≈ 431+ total
- Priority order: Phase 2 (exceptions) → Phase 3 (circuit breakers) → Phase 4 (caching) → Phase 5 (logging)
