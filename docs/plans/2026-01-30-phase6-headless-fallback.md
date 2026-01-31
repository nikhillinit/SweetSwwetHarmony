# Phase 6: Headless Fallback - Implementation Plan

## Goal
Add Playwright as a third-tier transport fallback for JavaScript-rendered sites that fail httpx and curl_cffi.

## Current Architecture

```
┌───────────────────────────────────────────────────────┐
│                  TransportEscalator                   │
├───────────────────────────────────────────────────────┤
│  Primary: HttpxTransport (HTTP/2, fast)               │
│     ↓ on 403/429/blocked patterns                     │
│  Fallback: CurlCffiTransport (browser impersonation)  │
└───────────────────────────────────────────────────────┘
```

## Target Architecture

```
┌───────────────────────────────────────────────────────┐
│                  TransportEscalator                   │
├───────────────────────────────────────────────────────┤
│  Tier 1: HttpxTransport (HTTP/2, fast)                │
│     ↓ on 403/429/blocked patterns                     │
│  Tier 2: CurlCffiTransport (browser impersonation)    │
│     ↓ on still blocked OR requires_js=true           │
│  Tier 3: PlaywrightTransport (headless browser)       │
│          └── Semaphore gated (max 2 concurrent)       │
└───────────────────────────────────────────────────────┘
```

## Tasks

### 6.1 Integrate Playwright with Semaphore Gating
**File:** `monitoring/content_pipeline/transport_playwright.py`

Create PlaywrightTransport implementing TransportProtocol:

```python
class PlaywrightTransport:
    """Headless browser transport using Playwright."""

    _semaphore: asyncio.Semaphore  # Shared across instances
    _browser_pool: Optional[BrowserPool]  # Reusable contexts

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        timeout: Optional[float] = None,
        max_html_bytes: Optional[int] = None,
        max_json_bytes: Optional[int] = None,
        wait_for_selector: Optional[str] = None,  # Phase 6.2
        **kwargs,
    ) -> FetchArtifact:
        async with self._semaphore:
            # Acquire browser context from pool
            # Navigate and wait
            # Return FetchArtifact with transport_used="playwright"
```

Key requirements:
- [ ] Global semaphore limiting to 2 concurrent browser operations
- [ ] Playwright async API (not sync)
- [ ] Chromium only (smaller install footprint)
- [ ] Graceful degradation if Playwright not installed

### 6.2 Selector-Based Wait (Not networkidle)
**Why:** `networkidle` waits for all network activity to stop - slow and unreliable.
**Better:** Wait for specific content selector to appear.

```python
# Wait strategies (in order of preference):
1. wait_for_selector: str  # Explicit selector from config
2. Auto-detect main content: "main", "article", "#content", "[data-content]"
3. DOM stability check: Wait until DOM stops changing for 500ms
4. Hard timeout: 30 seconds max
```

Add to TransportConfig:
```python
@dataclass
class TransportConfig:
    # ... existing fields ...
    playwright_wait_selector: Optional[str] = None
    playwright_timeout_ms: int = 30000  # 30s max
```

### 6.3 Add `js_rendered_v1` Preset
**File:** `config/watch_presets.yaml`

```yaml
js_rendered_v1:
  description: "JavaScript-rendered sites requiring headless browser"
  extractor:
    preset: "js_rendered_v1"
    selectors: null  # Use page content
    fallback_on_empty: true
  transport:
    initial: "httpx"
    on_403: "curl_cffi"
    on_blocked: "playwright"  # New escalation trigger
    playwright_wait_selector: null  # Auto-detect
    playwright_timeout_ms: 30000
  content_limits:
    max_html_bytes: 5242880
    max_json_bytes: 2097152
```

Also add to default presets in `presets.py`.

### 6.4 Browser Context Timeout/Recycling
**File:** `monitoring/content_pipeline/browser_pool.py`

```python
class BrowserPool:
    """Manages reusable browser contexts with lifecycle management."""

    def __init__(
        self,
        max_contexts: int = 2,
        context_ttl_seconds: int = 300,  # 5 minutes
        max_pages_per_context: int = 50,
    ):
        self._contexts: Dict[str, BrowserContext] = {}
        self._page_counts: Dict[str, int] = {}

    async def acquire(self) -> BrowserContext:
        """Get or create a browser context."""
        # Recycle if TTL expired or page limit reached

    async def release(self, context_id: str):
        """Return context to pool."""

    async def cleanup(self):
        """Close all contexts and browser."""
```

Lifecycle rules:
- [ ] Context reused until TTL expires (5 min)
- [ ] Context recycled after N pages (50)
- [ ] Graceful shutdown on pipeline exit
- [ ] Lazy browser launch (only when needed)

### 6.5 Observability Metrics
**File:** `monitoring/content_pipeline/metrics.py`

```python
@dataclass
class PipelineMetrics:
    """Metrics for content pipeline operations."""

    # Transport metrics
    httpx_requests: int = 0
    curl_requests: int = 0
    playwright_requests: int = 0

    # Escalation metrics
    escalations_to_curl: int = 0
    escalations_to_playwright: int = 0

    # Timing metrics
    avg_httpx_time_ms: float = 0
    avg_curl_time_ms: float = 0
    avg_playwright_time_ms: float = 0

    # Error metrics
    blocked_by_bot_detection: int = 0
    timeout_errors: int = 0
    extraction_errors: int = 0

    # Browser pool metrics
    browser_contexts_created: int = 0
    browser_contexts_recycled: int = 0
    browser_semaphore_waits: int = 0
    browser_semaphore_timeouts: int = 0
```

Emit via:
- [ ] Structured logging (JSON)
- [ ] Optional StatsD/DataDog integration
- [ ] Pipeline result metadata

### 6.6 Full Pipeline Integration Tests
**File:** `tests/monitoring/content_pipeline/test_playwright_integration.py`

Test scenarios:
- [ ] Escalation from httpx → curl → playwright on blocked patterns
- [ ] Semaphore correctly limits concurrent browsers
- [ ] Selector-based wait finds content
- [ ] Browser pool recycles contexts
- [ ] Timeout handling (page load, selector wait)
- [ ] Metrics correctly emitted
- [ ] Graceful degradation when Playwright not installed

Fixtures:
- Mock server returning JS-required content
- Mock server with Cloudflare challenge
- Golden file tests for SPA sites

## Exit Criteria

- [ ] Concurrency capped at 2 (semaphore test)
- [ ] True JS-only sites work (SPA fixture test)
- [ ] Metrics emitted (logging assertion)

## Dependencies

```bash
pip install playwright
playwright install chromium --with-deps
```

Optional (already installed):
- curl_cffi (Phase 5)
- httpx (existing)

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Playwright install size (~150MB) | Chromium only, lazy install on first use |
| Memory usage with browsers | Semaphore + context recycling |
| Slow tests | Mock browser for unit tests, real browser for integration only |
| CI/CD complexity | playwright install in CI workflow |

## File Changes

| File | Change |
|------|--------|
| `transport_playwright.py` | NEW - PlaywrightTransport class |
| `browser_pool.py` | NEW - BrowserPool class |
| `metrics.py` | NEW - PipelineMetrics class |
| `transport_escalator.py` | MODIFY - Add tier 3 playwright fallback |
| `config.py` | MODIFY - Add playwright config fields |
| `presets.py` | MODIFY - Add js_rendered_v1 preset |
| `orchestrator.py` | MODIFY - Wire metrics |
| `tests/...` | NEW - Integration tests |

## Task Order

```
6.1 PlaywrightTransport + Semaphore
  ↓
6.4 BrowserPool (can parallel with 6.2)
  ↓
6.2 Selector-based wait
  ↓
6.3 js_rendered_v1 preset
  ↓
6.5 Metrics
  ↓
6.6 Integration tests
```

Estimated: 3-4 days
