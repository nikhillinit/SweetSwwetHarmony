# Discovery Engine Hardening Implementation Plan
> **For Claude:** REQUIRED SUB-SKILL: TDD enforcement (RED-GREEN-REFACTOR)

**Goal:** Harden Discovery Engine for production with retry logic, rate limiting, health monitoring, and test coverage.

**Architecture:** Add centralized retry/rate-limiting infrastructure to collectors, integrate health monitoring into pipeline, enforce TDD for all new code.

**Tech Stack:** Python 3.11+, asyncio, aiosqlite, httpx, tenacity, pytest-asyncio

---

## Phase 1: Quick Wins (Suppression Warmup + Health Check)

### Task 1.1: Suppression Cache Warmup

#### Step 1: Write failing test for warmup method
```python
# File: tests/test_pipeline_warmup.py
import pytest
from workflows.pipeline import DiscoveryPipeline, PipelineConfig

@pytest.mark.asyncio
async def test_pipeline_warmup_calls_suppression_sync(mocker):
    """Pipeline should auto-sync suppression cache on initialize"""
    mock_sync = mocker.patch('workflows.suppression_sync.SuppressionSync.sync')
    mock_sync.return_value = mocker.MagicMock(entries_synced=100)

    config = PipelineConfig(warmup_suppression_cache=True)
    pipeline = DiscoveryPipeline(config)
    await pipeline.initialize()

    mock_sync.assert_called_once()
```

#### Step 2: Verify test fails (RED)
```bash
pytest tests/test_pipeline_warmup.py -v
# Expected: FAILED - warmup_suppression_cache not in PipelineConfig
```

#### Step 3: Implement minimal code (GREEN)
- Add `warmup_suppression_cache: bool = True` to PipelineConfig
- Add `_warmup_suppression_cache()` method to DiscoveryPipeline
- Call warmup in `initialize()` after Notion setup

#### Step 4: Verify test passes
```bash
pytest tests/test_pipeline_warmup.py -v
# Expected: PASSED
```

#### Step 5: Commit
```bash
git add -A && git commit -m "feat: Add suppression cache warmup on pipeline init"
```

---

### Task 1.2: Health Check CLI Command

#### Step 1: Write failing test for health command
```python
# File: tests/test_health_command.py
import pytest
from click.testing import CliRunner
from run_pipeline import cli

def test_health_command_exists():
    """Health command should be available in CLI"""
    runner = CliRunner()
    result = runner.invoke(cli, ['health', '--help'])
    assert result.exit_code == 0
    assert 'health' in result.output.lower()
```

#### Step 2: Verify test fails (RED)
```bash
pytest tests/test_health_command.py -v
# Expected: FAILED - no 'health' command
```

#### Step 3: Implement minimal code (GREEN)
- Create `workflows/health_monitor.py` with HealthCheckManager
- Add `health` subparser to run_pipeline.py
- Wire up SignalHealthMonitor integration

#### Step 4: Verify test passes
```bash
pytest tests/test_health_command.py -v
# Expected: PASSED
```

#### Step 5: Commit
```bash
git add -A && git commit -m "feat: Add health check CLI command"
```

---

## Phase 2: Collector Hardening (Retry + Rate Limiting)

### Task 2.1: Create Retry Strategy Module

#### Step 1: Write failing test for retry logic
```python
# File: collectors/test_retry_strategy.py
import pytest
import asyncio
from collectors.retry_strategy import AsyncRateLimiter, RetryConfig, with_retry

@pytest.mark.asyncio
async def test_retry_on_transient_error():
    """Should retry on transient errors with backoff"""
    attempts = []

    async def flaky_func():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("Transient failure")
        return "success"

    config = RetryConfig(max_retries=3, backoff_base=0.1)
    result = await with_retry(flaky_func, config)

    assert result == "success"
    assert len(attempts) == 3
```

#### Step 2: Verify test fails (RED)
```bash
pytest collectors/test_retry_strategy.py -v
# Expected: FAILED - module not found
```

#### Step 3: Implement minimal code (GREEN)
- Create `collectors/retry_strategy.py`
- Implement RetryConfig dataclass
- Implement with_retry async wrapper

#### Step 4: Verify test passes
```bash
pytest collectors/test_retry_strategy.py -v
# Expected: PASSED
```

#### Step 5: Commit
```bash
git add -A && git commit -m "feat: Add centralized retry strategy for collectors"
```

---

### Task 2.2: Create Rate Limiter Module

#### Step 1: Write failing test for rate limiting
```python
# File: utils/test_rate_limiter.py
import pytest
import asyncio
from utils.rate_limiter import AsyncRateLimiter, RateLimiterPool

@pytest.mark.asyncio
async def test_rate_limiter_respects_rate():
    """Rate limiter should enforce rate limits"""
    limiter = AsyncRateLimiter(rate=5, period=1)  # 5 per second

    start = asyncio.get_event_loop().time()
    for _ in range(5):
        await limiter.acquire()
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed < 1.5  # Should complete quickly for first 5
```

#### Step 2: Verify test fails (RED)
```bash
pytest utils/test_rate_limiter.py -v
# Expected: FAILED - module not found
```

#### Step 3: Implement minimal code (GREEN)
- Create `utils/rate_limiter.py`
- Implement AsyncRateLimiter with token bucket
- Implement RateLimiterPool with per-API limits

#### Step 4: Verify test passes
```bash
pytest utils/test_rate_limiter.py -v
# Expected: PASSED
```

#### Step 5: Commit
```bash
git add -A && git commit -m "feat: Add per-API rate limiter for collectors"
```

---

## Phase 3: BaseCollector Refactoring

### Task 3.1: Refactor job_postings.py to BaseCollector

#### Step 1: Write failing test for BaseCollector inheritance
```python
# File: collectors/test_job_postings_base.py
import pytest
from collectors.job_postings import JobPostingsCollector
from collectors.base import BaseCollector

def test_job_postings_inherits_base_collector():
    """JobPostingsCollector should inherit from BaseCollector"""
    assert issubclass(JobPostingsCollector, BaseCollector)
```

#### Step 2: Verify test fails (RED)
```bash
pytest collectors/test_job_postings_base.py::test_job_postings_inherits_base_collector -v
# Expected: FAILED - does not inherit BaseCollector
```

#### Step 3: Implement refactor (GREEN)
- Update class to inherit BaseCollector
- Move domains param to constructor
- Rename run() → _collect_signals()
- Update async context manager

#### Step 4: Verify test passes + existing tests
```bash
pytest collectors/test_job_postings*.py -v
# Expected: ALL PASSED
```

#### Step 5: Commit
```bash
git add -A && git commit -m "refactor: Migrate job_postings.py to BaseCollector pattern"
```

---

### Task 3.2: Refactor github_activity.py to BaseCollector

#### Step 1: Write failing test for BaseCollector inheritance
```python
# File: collectors/test_github_activity_base.py
import pytest
from collectors.github_activity import GitHubActivityCollector
from collectors.base import BaseCollector

def test_github_activity_inherits_base_collector():
    """GitHubActivityCollector should inherit from BaseCollector"""
    assert issubclass(GitHubActivityCollector, BaseCollector)
```

#### Step 2-5: Same pattern as Task 3.1

---

## Phase 4: Test Coverage for Untested Collectors

### Task 4.1: Add tests for github.py (P1)

Priority test cases:
1. test_repo_metrics_properties
2. test_filter_for_spikes
3. test_confidence_scoring
4. test_rate_limiting
5. test_collect_signals_dry_run

### Task 4.2-4.4: Same pattern for product_hunt, arxiv, uspto

---

## Phase 5: Consumer Module Tests

### Task 5.1: Test hard_disqualifiers.py (highest priority)
### Task 5.2: Test consumer_store.py
### Task 5.3: Test collectors with mocks

---

## Code Review Checkpoints

| After Task | Review Type | Blocking Level |
|------------|-------------|----------------|
| Phase 1 complete | Spec compliance | Important |
| Phase 2 complete | Code quality | Important |
| Phase 3 complete | Spec + Code | Critical |
| Phase 4 complete | Test coverage | Important |
| All phases | Final review | Critical |

---

## TDD Enforcement Rules

**The Iron Law:** Write failing tests first, then minimal code to pass them.

**Red Flags Requiring Restart:**
- Code written before failing tests
- Tests passing immediately upon writing
- Inability to explain why tests failed
- Tests marked for "later" addition

**Verification Commands:**
```bash
# RED: Confirm test fails
pytest path/to/test.py -v --tb=short

# GREEN: Confirm test passes
pytest path/to/test.py -v

# REFACTOR: Confirm all tests still pass
pytest --tb=short
```
