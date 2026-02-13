# Task Plan: M2 Ops Hardening Sprint — DRAFT

## Goal
Harden production operations with CI-enforced quality gates, reliable startup
probes, and basic external observability. Gap-focused: only addresses what's
missing, not what already exists.

## Current Phase
M2.1 — CI Regression Gate (S0 passed)

---

## Context

**Completed:** G0 + M1 + M3 (commit ed5fcd7 on main, 6759+ tests, 490+ regression gate)
**Prerequisite:** Post-M3 stabilization checkpoint (canary publish + log review)
**Deployment target:** Systemd VM (containerization is M5)
**Existing assets (NOT in M2 scope):**
- `docs/operator-guide.md` (160 lines) -- operational runbook
- `docs/architecture-overview.md` (162 lines) -- system structure
- `api/routers/health.py` (793 lines, 10+ endpoints) -- health API
- `utils/instrumentation.py` (129 lines) -- in-process metrics
- 5+ runbooks in `docs/runbooks/`

---

## Phase S0: Post-M3 Stabilization Gate (PREREQUISITE)

**Hard dependency -- M2 tasks MUST NOT start until this passes.**

### Tasks
- [ ] **S0.1** Canary publish: run one controlled non-dry-run batch publish against
  Notion staging (DELIVERY_MODE=batch_publish, 1-2 items). Verify:
  - `notion_page_id` persisted on `batch_items` row
  - Audit log entry with `action_type=batch_commit` present
  - Error envelope shape in API logs (503/423/409 as applicable)
- [ ] **S0.2** Log review: confirm NotionTransport lifecycle messages in API logs
  (`NotionConnector initialized (app-scoped)` / `NotionTransport shut down`)
- [ ] **S0.3** Gate decision: only proceed if canary confirms real Notion round-trip

**Gate:** Canary publish succeeds with correct persistence; transport logs present
**Status:** PASSED (dry-run, 2026-02-12)

---

## Phase M2.1: CI Regression Gate (HIGHEST PRIORITY)

**Estimated effort:** 0.5 day
**Purpose:** Prevent regressions from merging into main

### Task M2.1.1: Create regression gate workflow
**File:** `.github/workflows/regression-gate.yml`

Trigger: `pull_request` targeting `main`.
Steps: checkout, setup Python 3.11, install deps, run regression suite.

```yaml
name: Regression Gate
on:
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  regression:
    name: Core Regression Suite
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Run regression suite
        run: |
          python -m pytest tests/api/ tests/integration/ \
            tests/workflows/test_batch_publisher.py \
            --tb=short -q
```

### Task M2.1.2: Enable branch protection (enforcement mechanism)

**Manual step in GitHub Settings > Branches > Branch protection rules for `main`:**
1. Check "Require status checks to pass before merging"
2. Add required check: `Core Regression Suite` (the job name, not workflow name)
3. Check "Require branches to be up to date before merging"
4. Optionally: "Do not allow bypassing the above settings" (prevents admin override)

**Verification:** After configuring, the merge button on any PR will show
"Required status check — Core Regression Suite" and block merge until green.

**Document in:** `docs/runbooks/ci-regression-gate.md` — include screenshots of
Settings UI and the exact check name to select.

### Task M2.1.3: Validate the gate end-to-end
1. Create a branch with a deliberately failing test (e.g., `assert False`)
2. Open a PR targeting `main`
3. Verify: workflow runs, fails, merge button is blocked
4. Push a fix (remove `assert False`)
5. Verify: workflow re-runs, passes, merge button unblocks
6. Close the PR without merging

**Gate:** PR with failing test is blocked (merge button disabled); PR with passing
tests shows green check and allows merge. Branch protection rule is active.
**Status:** pending

---

## Phase M2.2: Systemd Startup Probe

**Estimated effort:** 0.5-1 day
**Purpose:** systemd knows when the API is genuinely ready (not just process-alive)

### Decision: Type=notify vs ExecStartPost vs wrapper script

**REJECTED: ExecStartPost** -- With `Type=simple`, ExecStartPost runs before
uvicorn binds the port. Requires fragile shell retry loops.

**RECOMMENDED: Health-check wrapper script** -- Zero new Python dependencies.
Works with existing `Type=simple`. Polls `/health` with retries.

**ALTERNATIVE: Type=notify + sdnotify** -- Technically superior but adds a
dependency (`sdnotify` or `python-systemd`). Consider for future.

### Task M2.2.1: Create startup health-check script (Python stdlib)
**File:** `scripts/healthcheck_startup.py`

Uses only Python stdlib (`http.client`) -- no curl, no requests, no external deps.
The Python venv is already available via the systemd `PATH` setting.

```python
#!/usr/bin/env python3
"""Startup health probe for systemd ExecStartPost.

Polls the API /health endpoint with retries. Exits 0 on success, 1 on timeout.
Uses only stdlib -- no external dependencies.

Environment variables:
    HEALTHCHECK_RETRIES  Max attempts (default: 10)
    HEALTHCHECK_DELAY    Seconds between retries (default: 3)
    HEALTHCHECK_PORT     API port (default: 8000)
"""
import http.client
import os
import sys
import time

MAX_RETRIES = int(os.environ.get("HEALTHCHECK_RETRIES", "10"))
RETRY_DELAY = int(os.environ.get("HEALTHCHECK_DELAY", "3"))
PORT = int(os.environ.get("HEALTHCHECK_PORT", "8000"))
PATH = "/health"

def check_health() -> bool:
    try:
        conn = http.client.HTTPConnection("localhost", PORT, timeout=5)
        conn.request("GET", PATH)
        resp = conn.getresponse()
        conn.close()
        return resp.status == 200
    except (ConnectionRefusedError, OSError, http.client.HTTPException):
        return False

for attempt in range(1, MAX_RETRIES + 1):
    if check_health():
        print(f"API healthy after {attempt} check(s)")
        sys.exit(0)
    print(f"Waiting for API... ({attempt}/{MAX_RETRIES})")
    time.sleep(RETRY_DELAY)

print(f"API failed to become healthy within {MAX_RETRIES * RETRY_DELAY}s")
sys.exit(1)
```

### Task M2.2.2: Update systemd service
**File:** `scripts/sweetharmony.service`

Changes:
- Add `ExecStartPost=/opt/sweetharmony/.venv/bin/python /opt/sweetharmony/scripts/healthcheck_startup.py`
- Add `TimeoutStartSec=45` (10 retries x 3s = 30s + margin)
- Keep `Type=simple` (defer Type=notify to future iteration)
- No new runtime dependencies (uses Python from the existing venv)

### Task M2.2.3: Tests
- Unit test: mock subprocess calling healthcheck script, verify exit codes
- Integration note: full systemd test requires VM (manual gate)

**Gate:** `systemctl start sweetharmony` fails if `/health` doesn't respond within timeout
**Status:** pending

---

## Phase M2.3: OpenMetrics Endpoint (SHOULD-HAVE)

**Estimated effort:** 0.5 day
**Purpose:** Expose existing metrics in scrape-compatible format

### Task M2.3.1: Add /metrics endpoint in OpenMetrics text format
**File:** `api/routers/health.py` (add to existing health router)
**Canonical path:** `GET /api/v1/health/metrics` (router prefix `/health` + app mount `/api/v1`)

Reads from `utils/instrumentation.metrics.snapshot()` and emits OpenMetrics text:

```
# TYPE discovery_counter counter
# HELP discovery_counter Application counter
discovery_counter{name="triage.approve.success"} 42
# TYPE discovery_timer_total_ms gauge
discovery_timer_total_ms{name="db.query"} 1234.56
```

Response: `Content-Type: application/openmetrics-text; version=1.0.0; charset=utf-8`

### Task M2.3.2: Include OpsMetricsCollector data
If ops tables are available, also emit:
- `discovery_health_pct` gauge
- `discovery_extractions_24h` gauge
- `discovery_open_incidents` gauge

### Task M2.3.3: Tests
**File:** `tests/api/test_metrics_endpoint.py`
- [ ] GET `/api/v1/health/metrics` returns 200 with `application/openmetrics-text` content type
- [ ] Counter lines match `name{label="value"} number` format
- [ ] Timer lines include count, total_ms, avg_ms
- [ ] Empty metrics returns valid (empty) OpenMetrics response
- [ ] Ops metrics included when ops tables exist

**NOTE:** No Prometheus deployment in M2. Endpoint is scrape-ready for when
Prometheus arrives (M5 / containerization). Manual validation suffices.

**Gate:** `GET /api/v1/health/metrics` returns valid OpenMetrics text/plain
**Status:** pending

---

## Phase M2.4: API Rate Limiting (COULD-HAVE)

**Estimated effort:** 0.5 day
**Purpose:** Defense-in-depth against accidental request floods

**Deprioritized rationale:** Internal API on a single VM for a 1-3 person team.
Existing systemd resource limits (MemoryMax=2G, CPUQuota=80%) already cap process
resources. Rate limiting is insurance, not a critical gap.

### Task M2.4.1: Add slowapi rate limiting middleware
**File:** `api/middleware.py`

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
```

**File:** `api/main.py` (lifespan or startup)
```python
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

Default limits:
- Global: 100 requests/minute per IP
- Write endpoints (triage, batch, publish): 20 requests/minute per IP
- Health/read endpoints: exempt (no limit)

### Task M2.4.2: Custom 429 response
Must match existing error envelope format (`{"error": ..., "request_id": ...}`).
Include `Retry-After` and `X-RateLimit-*` headers.

### Task M2.4.3: Tests
**File:** `tests/api/test_rate_limiting.py`
- [ ] Exceeding limit returns 429 with correct error envelope
- [ ] `Retry-After` header present on 429 responses
- [ ] `X-RateLimit-Remaining` header present on all responses
- [ ] Health endpoints are exempt from rate limiting
- [ ] Different IPs have independent limits

### Task M2.4.4: Add slowapi dependency
**File:** `requirements.txt`
Add: `slowapi>=0.1.9`

**Gate:** 429 returned with correct headers when limit exceeded; health endpoints exempt
**Status:** pending (COULD-HAVE -- do not block M2 gate on this)

---

## M2 Exit Gates

### MUST-PASS (block M2 completion)
1. CI regression gate blocks PR merges when tests fail
2. systemd service fails to start (ExecStartPost exit 1) when API is unhealthy
3. Zero new collection errors vs G0 baseline (`pytest --collect-only -q`)

### SHOULD-PASS (strongly desired, don't block if one slips)
4. `/api/v1/health/metrics` returns valid OpenMetrics text/plain

### COULD-PASS (nice-to-have)
5. Rate limit returns 429 with correct headers

---

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| CI gate is priority #1 | Only mechanism that prevents regressions from shipping |
| Python stdlib health probe | Zero runtime deps (no curl), testable, uses existing venv |
| ExecStartPost approach (revised) | User's suggestion, implemented via Python script not shell+curl |
| No Prometheus deployment in M2 | Deployment target is bare VM; Prometheus is M5 scope |
| Rate limiting deprioritized | Internal API, existing process-level resource caps |
| Stabilization as hard prerequisite | Canary must confirm M3 works before hardening |

## Files to Create
| File | Purpose | Phase |
|------|---------|-------|
| `.github/workflows/regression-gate.yml` | PR-blocking regression suite | M2.1 |
| `docs/runbooks/ci-regression-gate.md` | CI gate setup instructions + check names | M2.1 |
| `scripts/healthcheck_startup.py` | Startup health probe for systemd (stdlib only) | M2.2 |
| `tests/api/test_metrics_endpoint.py` | OpenMetrics endpoint tests | M2.3 |
| `tests/api/test_rate_limiting.py` | Rate limiting tests | M2.4 |

## Files to Modify
| File | Change | Phase |
|------|--------|-------|
| `scripts/sweetharmony.service` | ExecStartPost + TimeoutStartSec | M2.2 |
| `api/routers/health.py` | Add `/metrics` route (serves at `/api/v1/health/metrics`) | M2.3 |
| `api/middleware.py` | Add rate limiting middleware | M2.4 |
| `api/main.py` | Wire rate limiter into app | M2.4 |
| `requirements.txt` | Add slowapi | M2.4 |
