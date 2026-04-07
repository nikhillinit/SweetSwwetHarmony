# Coding Conventions

**Analysis Date:** 2026-04-07

## Naming Patterns

**Files:**
- Modules use `snake_case`: `signal_store.py`, `canonical_keys.py`, `thesis_filter.py`
- Test files follow `test_<module>.py` or `<module>_test.py` pattern: `test_github.py`, `test_base_collector_retry.py`
- Collector modules: `<source_name>.py` (e.g., `github.py`, `sec_edgar.py`, `arxiv.py`)

**Functions:**
- `snake_case` for all function and method names: `_collect_signals()`, `get_pending_signals()`, `build_canonical_key()`
- Private methods prefixed with single underscore: `_extract_canonical_key()`, `_save_signals()`, `_check_duplicates()`
- Async functions use `async def` and maintain same naming: `async def _collect_signals()`, `async def run()`

**Variables:**
- `snake_case` for variables and parameters: `signal_store`, `canonical_key`, `collector_name`, `raw_data`
- Module-level constants use `UPPER_CASE`: `RELEVANT_TOPICS`, `CONSUMER_TOPICS`, `MIN_STARS`, `CURRENT_SCHEMA_VERSION`
- Private module attributes prefixed with underscore: `_rate_limiter`, `_processed_identities`, `_errors`

**Types:**
- `CamelCase` for classes: `BaseCollector`, `SignalStore`, `RepoMetrics`, `VerificationGate`
- `CamelCase` for enums: `TopicMode`, `VerificationStatus`, `DealStatus`
- Type hints use `typing` module imports: `Optional[str]`, `List[Signal]`, `Dict[str, Any]`, `Tuple[str, str, str]`

## Code Style

**Formatting:**
- Python 3.11+ — see `pyproject.toml` requires-python: ">=3.11"
- Project does NOT enforce automated formatting (no black/ruff formatters found)
- Manual style following PEP 8 conventions observed in codebase
- Line length not explicitly restricted; typical range 80-120 characters

**Linting:**
- No explicit linting config found (no `.flake8`, `ruff.toml`, `.pylintrc`)
- Project uses AST-based CI linting guards for specific patterns (e.g., `scripts/db_lint_guards.py` checks for dangerous DB patterns)
- Async/await patterns are standard; pytest-asyncio handles auto-detection

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first if present)
2. Standard library imports: `asyncio`, `logging`, `os`, `json`, `uuid`, `sys`
3. Third-party imports: `httpx`, `aiosqlite`, `pytest`
4. Relative imports from project: `from collectors.base import`, `from storage.signal_store import`
5. TYPE_CHECKING imports (conditional, for forward references): `if TYPE_CHECKING: from workflows.pipeline import`

**Path Aliases:**
- No path aliases found; all imports use absolute paths relative to project root
- Example: `from collectors.base import BaseCollector` (not relative `from .base import`)
- Circular import prevention uses TYPE_CHECKING guards: `if TYPE_CHECKING: from workflows.pipeline import PipelineStats`

**Example from `collectors/github.py`:**
```python
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

import httpx

from collectors.base import BaseCollector, CollectorSkipError
from collectors.retry_strategy import RateLimitError, with_retry, RetryConfig
from storage.signal_store import SignalStore
from utils.canonical_keys import build_canonical_key, build_canonical_key_candidates
from verification.verification_gate_v2 import Signal, VerificationStatus
```

## Error Handling

**Patterns:**
- Custom exceptions defined as simple classes inheriting from `Exception`: `class CollectorSkipError(Exception): pass`
- Exceptions include docstrings explaining when to use them
- Error messages logged at appropriate levels: `logger.error()`, `logger.warning()`, `logger.exception()`
- Batch operations catch individual signal errors and continue processing; aggregate errors tracked in `_errors: List[str]`
- Retry logic uses decorator pattern: `@with_retry(retry_config)` from `collectors/retry_strategy.py`
- No try/except/finally patterns in collectors; context managers handle cleanup

**Example from `collectors/base.py`:**
```python
class CollectorSkipError(Exception):
    """
    Raised when a collector should be skipped for this run.

    Maps to CollectorStatus.SKIPPED in BaseCollector.run().
    Use for conditions like rate-limit fail-fast where continuing
    is pointless but the error is not a true failure.
    """
    pass
```

## Logging

**Framework:** Built-in `logging` module, not custom wrappers

**Patterns:**
- Logger created at module level: `logger = logging.getLogger(__name__)`
- Log levels by purpose:
  - `logger.info()` — Milestone events: "Collected X signals", "Fetched Y records"
  - `logger.debug()` — Detailed flow: "Skipping unchanged signal", "Processing source"
  - `logger.warning()` — Degraded operation: "No SignalStore configured", "Error checking signal"
  - `logger.error()` — Non-fatal failures: "HTTP error fetching category", "Error collecting"
  - `logger.exception()` — Caught exceptions with traceback: used in except blocks

**Example from `collectors/base.py`:**
```python
logger.info(f"Collected {self._signals_found} signals from {self.collector_name}")
logger.warning(f"No SignalStore configured, skipping save")
logger.debug(f"Duplicate signal: {canonical_key}")
logger.error(f"Error checking signal {signal.id}: {e}")
```

## Comments

**When to Comment:**
- Module docstrings are mandatory: describe purpose, usage, and invariants
- Function docstrings for public APIs and complex private methods
- Inline comments explain non-obvious logic (e.g., "IMPORTANT: identity-based (key+type+source), not canonical-key-based, so multi-source convergence works")
- TODOs/FIXMEs discouraged; use governance issues or tickets instead

**Docstring Style:**
Module-level docstrings describe purpose, signals/data flow, and usage examples:

```python
"""GitHub Signal Collector for Discovery Engine

Finds repositories with recent star/fork spikes indicating developer tools gaining traction.

Focus areas:
- AI Infrastructure (LLM frameworks, vector DBs, inference engines)
- Developer tools (APIs, SDKs, DevOps)
- Machine Learning (training, serving, deployment)

Strategy:
1. Search for trending repos by stars/recency
2. Filter by relevant topics (ai, ml, llm, infrastructure, developer-tools)
3. Identify the company/org behind the repo
4. Calculate spike metrics (growth rate, velocity)
5. Build canonical keys for deduplication
6. Return signals compatible with verification_gate_v2
"""
```

Class and method docstrings follow similar format with Args/Returns:

```python
def __init__(
    self,
    store: Optional[SignalStore] = None,
    collector_name: str = "unknown",
    retry_config: Optional[RetryConfig] = None,
):
    """
    Args:
        store: Optional SignalStore instance for persistence
        collector_name: Name of collector (for logging and results)
        retry_config: Configuration for retry behavior (default: RetryConfig())
    """
```

## Function Design

**Size:** Functions are modular; collectors typically 50-200 lines of implementation after docstrings

**Parameters:**
- Use positional parameters for required args: `def __init__(self, store, collector_name)`
- Use keyword-only parameters with defaults for optional args: `api_name: Optional[str] = None`
- Type hints are mandatory for function signatures

**Return Values:**
- Async methods return awaitable objects: `async def _collect_signals(self) -> List[Signal]`
- Methods return typed objects: `VerificationResult`, `CollectorResult`, `StoredSignal`
- Empty returns implicit (None); explicit `return` statements not used for void functions

**Example from `collectors/base.py`:**
```python
async def _collect_signals(self) -> List[Signal]:
    """Fetch and convert signals from source. Implemented by subclasses."""
    raise NotImplementedError

async def run(self, dry_run: bool = True) -> CollectorResult:
    """Main entry point. Execute collection → save → dedup checks."""
    # Implementation
    return CollectorResult(...)
```

## Module Design

**Exports:**
- Explicit public API via classes and functions; no `__all__` found in most modules
- Private functions/classes use underscore prefix: `_extract_canonical_key()`, `_processed_identities`
- Dataclasses used for data containers: `RepoMetrics`, `StoredSignal`, `Signal`

**Dataclasses:**
All data containers use `@dataclass` decorator from `dataclasses` module:

```python
@dataclass
class RepoMetrics:
    """GitHub repository metrics snapshot."""
    repo_full_name: str
    org: str
    repo: str
    description: str
    stars: int
    forks: int
    watchers: int
    open_issues: int
    language: Optional[str]
    topics: List[str]
    created_at: datetime
    updated_at: datetime
    pushed_at: datetime
    html_url: str
    homepage: Optional[str] = None
```

**Patterns:**
- Dataclasses with computed properties using `@property` decorator
- Enums for configuration: `class TopicMode(str, Enum): TECH = "tech"; CONSUMER = "consumer"`
- BaseCollector pattern: all collectors inherit and implement `_collect_signals() -> List[Signal]`
- Context managers use `async with` for resource management: `async with self._http_client.get(...)`

## Type Annotations

**Coverage:** Comprehensive type hints throughout:
- Function signatures always typed: `async def run(self, dry_run: bool = True) -> CollectorResult`
- Method parameters typed: `signals: List[Signal]`, `canonical_key: str`
- Return types explicitly annotated: `-> Dict[str, Any]`, `-> Optional[str]`, `-> List[Signal]`
- Generic types from `typing`: `List`, `Dict`, `Optional`, `Tuple`, `Union`, `Any`, `Callable`

**Style:**
- `from __future__ import annotations` enables forward references without quotes
- TYPE_CHECKING guards prevent circular imports: `if TYPE_CHECKING: from workflows.pipeline import`
- Modern union syntax (`str | None`) not used; project uses `Optional[str]` for compatibility

---

*Convention analysis: 2026-04-07*
