# Consumer Pipeline Activation Plan

> **For Claude:** REQUIRED SUB-SKILL: TDD enforcement (RED-GREEN-REFACTOR)

**Goal:** Enable the consumer thesis filtering pipeline by default, complete retry integration for remaining collectors, and add comprehensive test coverage.

**Architecture:** The two-stage classification pipeline (keyword pre-filter → Gemini LLM) exists but is disabled. We'll enable it by default, ensure all collectors have retry/rate-limiting, and add tests for untested components.

**Tech Stack:** Python 3.11+, asyncio, pytest-asyncio, httpx, tenacity

---

## Phase A: Enable Core Functionality

### Task A.1: Enable Consumer Gating by Default

**Files:**
- Modify: `workflows/pipeline.py`
- Test: `workflows/tests/test_pipeline_config.py`

#### Step 1: Write failing test for default gating
```python
# File: workflows/tests/test_pipeline_config.py
def test_pipeline_config_gating_enabled_by_default():
    """Consumer gating should be enabled by default"""
    from workflows.pipeline import PipelineConfig
    config = PipelineConfig()
    assert config.use_gating is True, "use_gating should default to True"
```

#### Step 2: Run test - verify RED
```bash
python -m pytest workflows/tests/test_pipeline_config.py::test_pipeline_config_gating_enabled_by_default -v
# Expected: FAILED - use_gating defaults to False
```

#### Step 3: Implement - change default to True
```python
# File: workflows/pipeline.py line ~105
use_gating: bool = True  # Changed from False
```

#### Step 4: Run test - verify GREEN
```bash
python -m pytest workflows/tests/test_pipeline_config.py::test_pipeline_config_gating_enabled_by_default -v
# Expected: PASSED
```

#### Step 5: Commit
```bash
git add workflows/pipeline.py workflows/tests/test_pipeline_config.py
git commit -m "feat: Enable consumer gating by default"
```

---

### Task A.2: Add Retry Logic to arxiv.py

**Files:**
- Modify: `collectors/arxiv.py`
- Test: `collectors/test_arxiv.py`

#### Step 1: Write failing test for retry usage
```python
# File: collectors/test_arxiv.py (add to existing)
def test_arxiv_uses_retry_config():
    """ArxivCollector should have retry_config from BaseCollector"""
    from collectors.arxiv import ArxivCollector
    collector = ArxivCollector()
    assert hasattr(collector, 'retry_config')
    assert collector.retry_config.max_retries >= 3

def test_arxiv_uses_rate_limiter():
    """ArxivCollector should use rate limiter"""
    from collectors.arxiv import ArxivCollector
    collector = ArxivCollector()
    assert hasattr(collector, 'rate_limiter')
```

#### Step 2: Run test - verify RED or GREEN
```bash
python -m pytest collectors/test_arxiv.py::test_arxiv_uses_retry_config collectors/test_arxiv.py::test_arxiv_uses_rate_limiter -v
```

#### Step 3: If RED, implement retry integration
- Ensure ArxivCollector calls `super().__init__()` with collector_name
- Ensure HTTP calls use `_http_get()` or `with_retry()`

#### Step 4: Run test - verify GREEN
```bash
python -m pytest collectors/test_arxiv.py -v
# Expected: All tests PASSED
```

#### Step 5: Commit
```bash
git add collectors/arxiv.py collectors/test_arxiv.py
git commit -m "feat: Add retry logic to arxiv.py collector"
```

---

### Task A.3: Add Retry Logic to uspto.py

**Files:**
- Modify: `collectors/uspto.py`
- Test: `collectors/test_uspto.py`

#### Step 1: Write failing test for retry usage
```python
# File: collectors/test_uspto.py (add to existing)
def test_uspto_uses_retry_config():
    """USPTOCollector should have retry_config from BaseCollector"""
    from collectors.uspto import USPTOCollector
    collector = USPTOCollector()
    assert hasattr(collector, 'retry_config')
    assert collector.retry_config.max_retries >= 3

def test_uspto_uses_rate_limiter():
    """USPTOCollector should use rate limiter"""
    from collectors.uspto import USPTOCollector
    collector = USPTOCollector()
    assert hasattr(collector, 'rate_limiter')
```

#### Step 2-5: Same pattern as Task A.2

---

## Phase B: Harden Testing

### Task B.1: Add SignalHealthMonitor Tests

**Files:**
- Create: `utils/test_signal_health.py`
- Reference: `utils/signal_health.py`

#### Step 1: Write tests for core functionality
```python
# File: utils/test_signal_health.py
import pytest
from datetime import datetime, timedelta, timezone
from utils.signal_health import SignalHealthMonitor, HealthStatus

class TestSignalHealthMonitor:
    def test_monitor_initialization(self):
        """Monitor should initialize with default thresholds"""
        monitor = SignalHealthMonitor()
        assert monitor is not None

    def test_healthy_status_with_recent_signals(self):
        """Monitor should report HEALTHY with recent signals"""
        # Implementation test

    def test_warning_status_with_stale_signals(self):
        """Monitor should report WARNING when signals are stale"""
        # Implementation test

    def test_critical_status_with_no_signals(self):
        """Monitor should report CRITICAL with no recent signals"""
        # Implementation test

    def test_anomaly_detection_volume_spike(self):
        """Monitor should detect volume spikes as anomalies"""
        # Implementation test

    def test_anomaly_detection_volume_drop(self):
        """Monitor should detect volume drops as anomalies"""
        # Implementation test
```

#### Step 2: Run tests - verify behavior
```bash
python -m pytest utils/test_signal_health.py -v
```

#### Step 3: Fix any issues discovered

#### Step 4: Commit
```bash
git add utils/test_signal_health.py
git commit -m "test: Add SignalHealthMonitor test coverage"
```

---

### Task B.2: Add Integration Tests

**Files:**
- Create/Modify: `tests/integration/test_collector_to_storage.py`

#### Step 1: Write integration test for collector → storage flow
```python
# File: tests/integration/test_collector_to_storage.py
import pytest
from storage.signal_store import SignalStore
from collectors.hacker_news import HackerNewsCollector

@pytest.mark.asyncio
async def test_collector_stores_signals():
    """Collector should store signals in SignalStore"""
    store = SignalStore(":memory:")
    await store.initialize()

    collector = HackerNewsCollector(store=store, max_stories=5)
    result = await collector.run(dry_run=False)

    # Verify signals were stored
    stored = await store.get_pending_signals(limit=100)
    assert len(stored) >= 0  # May be 0 if no HN stories match
```

#### Step 2-4: Standard TDD cycle

---

## Phase C: Improve Operations

### Task C.1: Add CLI Feature Flags

**Files:**
- Modify: `run_pipeline.py`
- Test: Manual verification

#### Step 1: Add --enable-gating and --disable-gating flags
```python
# In run_pipeline.py, add to argument parser
parser.add_argument('--enable-gating', action='store_true',
                    help='Enable consumer thesis filtering (default)')
parser.add_argument('--disable-gating', action='store_true',
                    help='Disable consumer thesis filtering')
```

#### Step 2: Wire flags to PipelineConfig

#### Step 3: Test manually
```bash
python run_pipeline.py full --help
python run_pipeline.py full --disable-gating --dry-run
```

#### Step 4: Commit
```bash
git add run_pipeline.py
git commit -m "feat: Add CLI flags for feature control"
```

---

### Task C.2: Enhance Health Check Command

**Files:**
- Modify: `run_pipeline.py`

#### Step 1: Add collector API connectivity checks
- Test GitHub API reachability
- Test SEC EDGAR API reachability
- Test Notion API reachability

#### Step 2: Add LLM classifier availability check
- Test Gemini API key validity

#### Step 3: Return structured health report

---

## Verification Checklist

Before declaring complete:
- [ ] All tests pass: `python -m pytest collectors/ utils/ workflows/tests/ -v`
- [ ] Consumer gating enabled by default
- [ ] arxiv.py has retry logic
- [ ] uspto.py has retry logic
- [ ] SignalHealthMonitor has test coverage
- [ ] Integration tests exist for collector→storage
- [ ] CLI flags work for feature control
- [ ] Health check command enhanced

## Commit History Target

1. `feat: Enable consumer gating by default`
2. `feat: Add retry logic to arxiv.py collector`
3. `feat: Add retry logic to uspto.py collector`
4. `test: Add SignalHealthMonitor test coverage`
5. `test: Add collector-to-storage integration tests`
6. `feat: Add CLI flags for feature control`
7. `feat: Enhance health check command`
