# Findings & Decisions — Phase 7: Production Hardening

## Requirements
- Harden the Discovery Engine for production reliability
- Add circuit breakers, structured error handling, graceful degradation
- Improve API resilience (middleware, rate limiting, request validation)
- Add production configuration management
- Ensure pipeline survives partial failures cleanly

## Research Findings

### What's Already Solid
- **Retry logic**: `collectors/retry_strategy.py` — exponential backoff, jitter, Retry-After parsing
- **Timeout config**: `collectors/timeout_config.py` — per-operation timeouts (search/enrich/download)
- **Base collector**: `collectors/base.py` — rate limiting, retry, timeout telemetry, batch error handling
- **Pipeline isolation**: `workflows/pipeline.py:912` — `asyncio.gather(*tasks, return_exceptions=True)` — collector failures don't crash pipeline
- **Ops monitoring**: Full Phase 0-5 ops layer with metrics, alerts, rules engine
- **Health endpoints**: `/health/detailed`, `/health/collectors`, `/health/database`, `/health/ops`

### Gap 1: No Global Exception Middleware in API (HIGH)
- `api/main.py` has NO exception handler middleware
- Unhandled exceptions in routers will return raw 500s with stack traces
- No request ID tracking for correlating errors
- **Fix**: `ExceptionHandlerMiddleware` + `RequestIdMiddleware` in `api/middleware.py`

### Gap 2: No Circuit Breakers for External Services (HIGH)
- Collectors retry on failure but never "trip" to prevent cascading load
- If Notion API is down, pipeline keeps hammering it (retry per signal)
- No backpressure mechanism when downstream services are degraded
- **Fix**: `CircuitBreaker` class in `utils/circuit_breaker.py`

### Gap 3: Store Created Per Request in Health Router (MEDIUM)
- `health.py:107-111` — `get_store()` creates a NEW `SignalStore()` + `initialize()` per request
- `_get_ops_storage()` / `_get_ops_collector()` also create fresh instances per call
- **Fix**: Use `request.app.state.store` from lifespan, cache ops instances

### Gap 4: No Query Parameter Bounds (MEDIUM)
- `hours`, `limit`, `window_hours` params in health endpoints have no upper bounds
- `GET /health/ops/history?hours=999999&limit=999999` can trigger expensive queries
- **Fix**: Pydantic `Query(ge=1, le=720)` / `Query(ge=1, le=1000)` bounds

### Gap 5: No Health Check Caching (MEDIUM)
- `/health/detailed`, `/health/collectors`, `/health/ops` run full DB queries every call
- Monitoring tools polling every 10-30s will hammer the DB unnecessarily
- **Fix**: In-memory TTL cache decorator (30s TTL)

### Gap 6: No Request ID Tracking (MEDIUM)
- Can't correlate errors across log entries in production
- No way to trace a single request through multiple log lines
- **Fix**: UUID per request in middleware, injected into logging context

### Gap 7: No Structured Logging (MEDIUM)
- Mix of `logger.info/warning/error` with string formatting
- No JSON logging option for log aggregation tools (ELK, CloudWatch, etc.)
- `print()` usage in bootstrap and CLI files
- **Fix**: `utils/logging_config.py` with `LOG_FORMAT=json` toggle

### Gap 8: No Graceful Shutdown (MEDIUM)
- `api/main.py` lifespan closes store but doesn't drain in-flight requests
- Pipeline has no cancellation/signal handling (SIGTERM, SIGINT)
- **Fix**: Signal handler + drain timeout in lifespan shutdown

### Gap 9: Hardcoded Schema Version (LOW)
- `health.py:148` — `"schema_version": 16` is hardcoded instead of dynamically queried
- **Fix**: Query from `schema_migrations` table (already done in `/health/database`)

### Gap 10: API Has No Rate Limiting (LOW for now)
- Internal-facing API, but risky if ever exposed
- **Defer**: Not critical for initial production hardening; can add later via nginx or middleware

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Middleware pattern for exception handling | Non-invasive, applies globally, consistent error format |
| Circuit breaker as utility class | Reusable, composable with existing retry strategy |
| Per-service circuit breakers | One CB for Notion, one for GitHub — matches API boundaries |
| Lifespan-managed store singletons | Eliminates per-request connection churn |
| In-memory TTL cache (no Redis) | No new deps, single-process API, 30s TTL |
| Query param bounds via Pydantic `Query()` | FastAPI-native, auto 422 response |
| `LOG_FORMAT=json` env toggle | Human-readable default for dev, JSON for prod |
| Graceful shutdown with drain timeout | Prevents data corruption on restart |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| (none yet) | |

## Resources
- `collectors/retry_strategy.py` — existing retry infrastructure
- `collectors/base.py` — base collector with rate limiting + timeout telemetry
- `workflows/pipeline.py` — pipeline orchestrator (lines 661-760, 887-946)
- `api/main.py` — FastAPI app setup (lifespan, CORS, routers)
- `api/routers/health.py` — health endpoints (get_store creates new per request)
- `ops/storage.py` — ops storage layer
- `ops/monitoring/` — monitoring infrastructure

---
*Update this file after every 2 view/browser/search operations*
