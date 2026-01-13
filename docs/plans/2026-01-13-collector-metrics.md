# Collector Metrics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add per-collector timing and API metrics for performance debugging with a CLI command to view them.

**Architecture:** Extend the existing pipeline metrics system with a new `collector_metrics` table that stores per-collector timing, signal counts, and API metrics (calls, retries, rate limits). Capture metrics in `_run_single_collector()` by timing before/after and reading from the `BaseCollector` counters. Add `python run_pipeline.py metrics` CLI command.

**Tech Stack:** Python, SQLite (aiosqlite), pytest

---

## Task 1: Add collector_metrics Schema Migration

**Files:**
- Modify: `storage/signal_store.py:176-194` (add migration 4)

**Step 1: Write the failing test**

Create test file `tests/storage/test_collector_metrics.py`:

```python
"""Tests for collector metrics storage."""
import pytest
from datetime import datetime, timezone

from storage.signal_store import SignalStore


@pytest.fixture
async def store(tmp_path):
    """Create a SignalStore with temp database."""
    db_path = str(tmp_path / "test.db")
    store = SignalStore(db_path=db_path)
    await store.initialize()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_collector_metrics_table_exists(store):
    """Verify collector_metrics table is created."""
    cursor = await store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='collector_metrics'"
    )
    row = await cursor.fetchone()
    assert row is not None, "collector_metrics table should exist"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_collector_metrics.py::test_collector_metrics_table_exists -v`
Expected: FAIL - table does not exist

**Step 3: Write minimal implementation**

In `storage/signal_store.py`, add migration 4 after migration 3 (around line 193):

```python
    4: """
    -- Collector metrics: per-collector timing and API stats
    CREATE TABLE IF NOT EXISTS collector_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        collector_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        duration_seconds REAL,
        signals_found INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        api_calls INTEGER NOT NULL DEFAULT 0,
        rate_limit_hits INTEGER NOT NULL DEFAULT 0,
        retries INTEGER NOT NULL DEFAULT 0,
        errors INTEGER NOT NULL DEFAULT 0,
        error_messages TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_collector_metrics_run_id ON collector_metrics(run_id);
    CREATE INDEX IF NOT EXISTS idx_collector_metrics_collector ON collector_metrics(collector_name);
    CREATE INDEX IF NOT EXISTS idx_collector_metrics_started_at ON collector_metrics(started_at);
    """
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_collector_metrics.py::test_collector_metrics_table_exists -v`
Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_collector_metrics.py
git commit -m "feat(storage): add collector_metrics schema migration"
```

---

## Task 2: Add CollectorMetrics Dataclass

**Files:**
- Modify: `workflows/pipeline.py:160-195` (add dataclass after PipelineStats)

**Step 1: Write the failing test**

Add to `tests/storage/test_collector_metrics.py`:

```python
from workflows.pipeline import CollectorMetrics


def test_collector_metrics_dataclass():
    """Verify CollectorMetrics dataclass has expected fields."""
    metrics = CollectorMetrics(
        collector_name="github",
        started_at=datetime.now(timezone.utc),
    )
    assert metrics.collector_name == "github"
    assert metrics.status == "pending"
    assert metrics.api_calls == 0
    assert metrics.retries == 0
    assert metrics.rate_limit_hits == 0
    assert metrics.errors == 0
    assert metrics.error_messages == []


def test_collector_metrics_complete():
    """Verify complete() sets completed_at and calculates duration."""
    start = datetime.now(timezone.utc)
    metrics = CollectorMetrics(collector_name="github", started_at=start)
    metrics.complete()
    assert metrics.completed_at is not None
    assert metrics.duration_seconds >= 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_collector_metrics.py::test_collector_metrics_dataclass -v`
Expected: FAIL - cannot import CollectorMetrics

**Step 3: Write minimal implementation**

In `workflows/pipeline.py`, add after the `PipelineStats` class (around line 242):

```python
@dataclass
class CollectorMetrics:
    """Metrics captured for a single collector run."""
    collector_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    signals_found: int = 0
    status: str = "pending"

    # API metrics
    api_calls: int = 0
    rate_limit_hits: int = 0
    retries: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)

    def complete(self):
        """Mark as completed and calculate duration."""
        self.completed_at = datetime.now(timezone.utc)
        self.duration_seconds = (self.completed_at - self.started_at).total_seconds()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_collector_metrics.py::test_collector_metrics_dataclass tests/storage/test_collector_metrics.py::test_collector_metrics_complete -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/storage/test_collector_metrics.py
git commit -m "feat(pipeline): add CollectorMetrics dataclass"
```

---

## Task 3: Add save_collector_metrics() Method

**Files:**
- Modify: `storage/signal_store.py` (add method after save_pipeline_run)

**Step 1: Write the failing test**

Add to `tests/storage/test_collector_metrics.py`:

```python
@pytest.mark.asyncio
async def test_save_collector_metrics(store):
    """Verify collector metrics are saved to database."""
    from workflows.pipeline import CollectorMetrics

    metrics = CollectorMetrics(
        collector_name="github",
        started_at=datetime.now(timezone.utc),
        signals_found=42,
        status="success",
        api_calls=15,
        retries=2,
    )
    metrics.complete()

    await store.save_collector_metrics("test-run-123", metrics)

    # Verify saved
    cursor = await store._db.execute(
        "SELECT collector_name, signals_found, api_calls, retries FROM collector_metrics WHERE run_id = ?",
        ("test-run-123",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "github"
    assert row[1] == 42
    assert row[2] == 15
    assert row[3] == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_collector_metrics.py::test_save_collector_metrics -v`
Expected: FAIL - save_collector_metrics not found

**Step 3: Write minimal implementation**

In `storage/signal_store.py`, add after `get_pipeline_run()` method:

```python
    async def save_collector_metrics(self, run_id: str, metrics: "CollectorMetrics") -> None:
        """
        Save collector metrics for a pipeline run.

        Args:
            run_id: Pipeline run ID to associate with
            metrics: CollectorMetrics object with timing and API stats
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        now = datetime.now(timezone.utc).isoformat()
        error_messages_json = json.dumps(metrics.error_messages) if metrics.error_messages else None

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO collector_metrics (
                    run_id, collector_name, started_at, completed_at, duration_seconds,
                    signals_found, status, api_calls, rate_limit_hits, retries,
                    errors, error_messages, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metrics.collector_name,
                    metrics.started_at.isoformat(),
                    metrics.completed_at.isoformat() if metrics.completed_at else None,
                    metrics.duration_seconds,
                    metrics.signals_found,
                    metrics.status,
                    metrics.api_calls,
                    metrics.rate_limit_hits,
                    metrics.retries,
                    metrics.errors,
                    error_messages_json,
                    now,
                )
            )

        logger.debug(f"Saved metrics for collector {metrics.collector_name} (run: {run_id})")
```

Also add import at top of file if not present:
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from workflows.pipeline import CollectorMetrics
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_collector_metrics.py::test_save_collector_metrics -v`
Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_collector_metrics.py
git commit -m "feat(storage): add save_collector_metrics method"
```

---

## Task 4: Add get_collector_metrics() Method

**Files:**
- Modify: `storage/signal_store.py` (add query method)

**Step 1: Write the failing test**

Add to `tests/storage/test_collector_metrics.py`:

```python
@pytest.mark.asyncio
async def test_get_collector_metrics_by_run(store):
    """Verify we can query collector metrics by run_id."""
    from workflows.pipeline import CollectorMetrics

    # Save metrics for two collectors
    for name, signals in [("github", 42), ("sec_edgar", 18)]:
        metrics = CollectorMetrics(
            collector_name=name,
            started_at=datetime.now(timezone.utc),
            signals_found=signals,
            status="success",
        )
        metrics.complete()
        await store.save_collector_metrics("run-abc", metrics)

    # Query
    results = await store.get_collector_metrics(run_id="run-abc")

    assert len(results) == 2
    assert results[0]["collector_name"] == "github"
    assert results[1]["collector_name"] == "sec_edgar"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_collector_metrics.py::test_get_collector_metrics_by_run -v`
Expected: FAIL - get_collector_metrics not found

**Step 3: Write minimal implementation**

In `storage/signal_store.py`:

```python
    async def get_collector_metrics(
        self,
        run_id: Optional[str] = None,
        collector_name: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Query collector metrics with optional filters.

        Args:
            run_id: Filter to specific pipeline run
            collector_name: Filter to specific collector
            limit: Maximum results (default 100)

        Returns:
            List of collector metrics dictionaries
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT
                run_id, collector_name, started_at, completed_at, duration_seconds,
                signals_found, status, api_calls, rate_limit_hits, retries,
                errors, error_messages
            FROM collector_metrics
            WHERE 1=1
        """
        params = []

        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if collector_name:
            query += " AND collector_name = ?"
            params.append(collector_name)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "run_id": row[0],
                "collector_name": row[1],
                "started_at": row[2],
                "completed_at": row[3],
                "duration_seconds": row[4],
                "signals_found": row[5],
                "status": row[6],
                "api_calls": row[7],
                "rate_limit_hits": row[8],
                "retries": row[9],
                "errors": row[10],
                "error_messages": json.loads(row[11]) if row[11] else [],
            }
            for row in rows
        ]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_collector_metrics.py::test_get_collector_metrics_by_run -v`
Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_collector_metrics.py
git commit -m "feat(storage): add get_collector_metrics query method"
```

---

## Task 5: Capture Timing in Pipeline

**Files:**
- Modify: `workflows/pipeline.py:745-880` (_run_single_collector method)

**Step 1: Write the failing test**

Create `tests/workflows/test_collector_metrics_capture.py`:

```python
"""Tests for collector metrics capture in pipeline."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from workflows.pipeline import DiscoveryPipeline, PipelineConfig, CollectorMetrics
from discovery_engine.mcp_server import CollectorResult, CollectorStatus


@pytest.fixture
def mock_store():
    """Create mock SignalStore."""
    store = AsyncMock()
    store.save_collector_metrics = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_run_single_collector_captures_timing(mock_store):
    """Verify _run_single_collector captures timing metrics."""
    config = PipelineConfig(
        db_path=":memory:",
        github_token="fake-token",
    )
    pipeline = DiscoveryPipeline(config)
    pipeline._store = mock_store
    pipeline._collector_metrics = []

    # Mock the collector
    mock_result = CollectorResult(
        collector="github",
        status=CollectorStatus.SUCCESS,
        signals_found=42,
        dry_run=True,
    )

    with patch("workflows.pipeline.GitHubCollector") as MockCollector:
        mock_collector = AsyncMock()
        mock_collector.run = AsyncMock(return_value=mock_result)
        mock_collector._retry_count = 2
        MockCollector.return_value = mock_collector

        result = await pipeline._run_single_collector("github", dry_run=True)

    assert result.signals_found == 42
    assert len(pipeline._collector_metrics) == 1
    metrics = pipeline._collector_metrics[0]
    assert metrics.collector_name == "github"
    assert metrics.duration_seconds is not None
    assert metrics.duration_seconds >= 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_collector_metrics_capture.py::test_run_single_collector_captures_timing -v`
Expected: FAIL - _collector_metrics attribute doesn't exist

**Step 3: Write minimal implementation**

In `workflows/pipeline.py`, modify `__init__` to add:
```python
        # Collector metrics for current run
        self._collector_metrics: List[CollectorMetrics] = []
```

Modify `_run_single_collector` to capture timing (wrap the collector run):
```python
    async def _run_single_collector(
        self,
        collector_name: str,
        dry_run: bool,
    ) -> CollectorResult:
        """Run a single collector and return results"""
        # Start timing
        metrics = CollectorMetrics(
            collector_name=collector_name,
            started_at=datetime.now(timezone.utc),
        )

        try:
            logger.info(f"Running collector: {collector_name}")

            # ... existing collector instantiation code ...

            # Run collector
            result = await collector.run(dry_run=dry_run)

            # Capture metrics from collector
            metrics.signals_found = result.signals_found
            metrics.status = result.status.value
            metrics.retries = getattr(collector, '_retry_count', 0)
            metrics.errors = len(getattr(collector, '_errors', []))
            metrics.error_messages = getattr(collector, '_errors', [])

            logger.info(
                f"Collector {collector_name} completed: "
                f"{result.signals_found} signals found"
            )

            return result

        except Exception as e:
            logger.exception(f"Error running collector {collector_name}")
            metrics.status = "error"
            metrics.errors = 1
            metrics.error_messages = [str(e)]
            return CollectorResult(
                collector=collector_name,
                status=CollectorStatus.ERROR,
                error_message=str(e),
                dry_run=dry_run,
            )
        finally:
            # Always capture timing
            metrics.complete()
            self._collector_metrics.append(metrics)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_collector_metrics_capture.py::test_run_single_collector_captures_timing -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_collector_metrics_capture.py
git commit -m "feat(pipeline): capture collector timing metrics"
```

---

## Task 6: Save Collector Metrics with Pipeline Run

**Files:**
- Modify: `workflows/pipeline.py:550-563` (run_full_pipeline metrics save section)

**Step 1: Write the failing test**

Add to `tests/workflows/test_collector_metrics_capture.py`:

```python
@pytest.mark.asyncio
async def test_pipeline_saves_collector_metrics(mock_store):
    """Verify pipeline saves collector metrics alongside run metrics."""
    config = PipelineConfig(db_path=":memory:")
    pipeline = DiscoveryPipeline(config)
    pipeline._store = mock_store
    pipeline._initialized = True

    # Add some metrics
    pipeline._collector_metrics = [
        CollectorMetrics(
            collector_name="github",
            started_at=datetime.now(timezone.utc),
            signals_found=42,
            status="success",
        ),
    ]
    pipeline._collector_metrics[0].complete()

    # Mock save_pipeline_run to return a run_id
    mock_store.save_pipeline_run = AsyncMock(return_value="run-123")

    await pipeline._save_pipeline_metrics(pipeline._collector_metrics)

    # Verify collector metrics were saved
    mock_store.save_collector_metrics.assert_called_once()
    call_args = mock_store.save_collector_metrics.call_args
    assert call_args[0][0] == "run-123"  # run_id
    assert call_args[0][1].collector_name == "github"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/workflows/test_collector_metrics_capture.py::test_pipeline_saves_collector_metrics -v`
Expected: FAIL - _save_pipeline_metrics method doesn't exist

**Step 3: Write minimal implementation**

In `workflows/pipeline.py`, add helper method and update run_full_pipeline:

```python
    async def _save_pipeline_metrics(
        self,
        stats: PipelineStats,
    ) -> Optional[str]:
        """
        Save pipeline run and collector metrics to database.

        Returns run_id if successful, None otherwise.
        """
        try:
            run_id = await self._store.save_pipeline_run(stats)
            logger.info(f"Pipeline metrics saved (run_id: {run_id})")

            # Save collector metrics
            for metrics in self._collector_metrics:
                await self._store.save_collector_metrics(run_id, metrics)

            logger.info(f"Saved {len(self._collector_metrics)} collector metrics")
            return run_id

        except Exception as e:
            logger.warning(f"Failed to save pipeline metrics (non-fatal): {e}")
            return None
```

Update `run_full_pipeline` to use the new method (replace lines 555-561):
```python
            # Save metrics to database (non-fatal)
            if not dry_run:
                await self._save_pipeline_metrics(stats)
```

Also reset `_collector_metrics` at the start of `run_full_pipeline`:
```python
        # Reset collector metrics for this run
        self._collector_metrics = []
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/workflows/test_collector_metrics_capture.py::test_pipeline_saves_collector_metrics -v`
Expected: PASS

**Step 5: Commit**

```bash
git add workflows/pipeline.py tests/workflows/test_collector_metrics_capture.py
git commit -m "feat(pipeline): save collector metrics with pipeline run"
```

---

## Task 7: Add CLI metrics Command

**Files:**
- Modify: `run_pipeline.py` (add metrics command)

**Step 1: Write the failing test**

Create `tests/test_cli_metrics.py`:

```python
"""Tests for CLI metrics command."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from io import StringIO
import sys


def test_metrics_command_exists():
    """Verify metrics command is registered."""
    import run_pipeline

    # Parse with metrics command
    parser = run_pipeline.create_parser()
    args = parser.parse_args(["metrics"])
    assert args.command == "metrics"


def test_metrics_command_has_limit_option():
    """Verify metrics command accepts --limit option."""
    import run_pipeline

    parser = run_pipeline.create_parser()
    args = parser.parse_args(["metrics", "--limit", "10"])
    assert args.limit == 10
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_metrics.py::test_metrics_command_exists -v`
Expected: FAIL - metrics command not found

**Step 3: Write minimal implementation**

In `run_pipeline.py`, add metrics subparser after health_parser (around line 1083):

```python
    # Metrics command
    metrics_parser = subparsers.add_parser(
        "metrics",
        help="Show pipeline run metrics with per-collector breakdown",
    )
    metrics_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=5,
        help="Number of recent runs to show (default: 5)",
    )
    metrics_parser.add_argument(
        "--collector", "-c",
        type=str,
        default=None,
        help="Filter to specific collector",
    )
    metrics_parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        dest="db_path",
        help="Path to signals database",
    )
```

Also add to command dispatch (around line 1150):
```python
    elif args.command == "metrics":
        asyncio.run(cmd_metrics(args))
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_metrics.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add run_pipeline.py tests/test_cli_metrics.py
git commit -m "feat(cli): add metrics command skeleton"
```

---

## Task 8: Implement cmd_metrics Function

**Files:**
- Modify: `run_pipeline.py` (add cmd_metrics implementation)

**Step 1: Write the failing test**

Add to `tests/test_cli_metrics.py`:

```python
@pytest.mark.asyncio
async def test_cmd_metrics_displays_runs():
    """Verify cmd_metrics displays pipeline runs with collector breakdown."""
    import run_pipeline

    # Mock the pipeline and store
    mock_pipeline = AsyncMock()
    mock_store = AsyncMock()
    mock_pipeline._store = mock_store

    # Mock data
    mock_store.get_pipeline_runs = AsyncMock(return_value=[
        {
            "run_id": "run-123",
            "started_at": "2026-01-13T14:32:01",
            "duration_seconds": 45.2,
            "collectors_run": 3,
            "signals_collected": 66,
        }
    ])
    mock_store.get_collector_metrics = AsyncMock(return_value=[
        {
            "collector_name": "github",
            "duration_seconds": 12.3,
            "signals_found": 42,
            "status": "success",
            "api_calls": 15,
            "retries": 0,
            "rate_limit_hits": 0,
        },
        {
            "collector_name": "sec_edgar",
            "duration_seconds": 28.1,
            "signals_found": 18,
            "status": "success",
            "api_calls": 8,
            "retries": 2,
            "rate_limit_hits": 0,
        },
    ])

    with patch.object(run_pipeline, 'DiscoveryPipeline', return_value=mock_pipeline):
        mock_pipeline.initialize = AsyncMock()
        mock_pipeline.close = AsyncMock()

        # Capture stdout
        captured = StringIO()
        with patch('sys.stdout', captured):
            args = MagicMock()
            args.limit = 5
            args.collector = None
            args.db_path = None
            await run_pipeline.cmd_metrics(args)

        output = captured.getvalue()
        assert "github" in output
        assert "12.3" in output or "12.3s" in output
        assert "42" in output
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_metrics.py::test_cmd_metrics_displays_runs -v`
Expected: FAIL - cmd_metrics not implemented

**Step 3: Write minimal implementation**

In `run_pipeline.py`, add the cmd_metrics function:

```python
async def cmd_metrics(args):
    """Show pipeline run metrics with per-collector breakdown."""
    print("=" * 70)
    print("DISCOVERY ENGINE - PIPELINE METRICS")
    print("=" * 70)

    config = PipelineConfig.from_env()
    if args.db_path:
        config.db_path = args.db_path

    pipeline = DiscoveryPipeline(config)

    try:
        await pipeline.initialize()

        # Get recent pipeline runs
        runs = await pipeline._store.get_pipeline_runs(limit=args.limit)

        if not runs:
            print("\nNo pipeline runs found.")
            return

        print(f"\nLast {len(runs)} runs:\n")

        for run in runs:
            run_id = run["run_id"]
            started = run["started_at"][:19].replace("T", " ")
            duration = run.get("duration_seconds", 0) or 0

            print(f"Run: {started} ({duration:.1f}s total)")

            # Get collector metrics for this run
            collector_metrics = await pipeline._store.get_collector_metrics(
                run_id=run_id,
                collector_name=args.collector,
            )

            if not collector_metrics:
                print("  (no collector metrics)")
            else:
                for cm in collector_metrics:
                    name = cm["collector_name"]
                    dur = cm.get("duration_seconds", 0) or 0
                    signals = cm.get("signals_found", 0)
                    status = cm.get("status", "unknown")
                    api_calls = cm.get("api_calls", 0)
                    retries = cm.get("retries", 0)
                    rate_limits = cm.get("rate_limit_hits", 0)

                    # Status indicator
                    status_icon = "✓" if status == "success" else "✗" if status == "error" else "○"

                    # Format API metrics
                    api_parts = [f"{api_calls} calls"]
                    if retries > 0:
                        api_parts.append(f"{retries} retries")
                    if rate_limits > 0:
                        api_parts.append(f"{rate_limits} rate limits")
                    api_str = ", ".join(api_parts)

                    print(f"  {name:<16} {dur:>6.1f}s   {status_icon}   {signals:>3} signals   |  API: {api_str}")

            print()

    finally:
        await pipeline.close()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_metrics.py::test_cmd_metrics_displays_runs -v`
Expected: PASS

**Step 5: Commit**

```bash
git add run_pipeline.py tests/test_cli_metrics.py
git commit -m "feat(cli): implement metrics command with collector breakdown"
```

---

## Task 9: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (mark telemetry task complete, add metrics command)

**Step 1: Update documentation**

In `CLAUDE.md`, mark the task complete:
```markdown
**Phase 1: Automated Monitoring** ✅
- [x] Auto-trigger SignalHealthMonitor after pipeline runs (pipeline.py:645)
- [x] Wire Slack alerts to health anomalies (pipeline.py:1049-1065)
- [x] Add pipeline run metrics/telemetry
```

Add metrics command to Commands section:
```markdown
# View pipeline metrics with collector breakdown
python run_pipeline.py metrics
python run_pipeline.py metrics --limit 10 --collector github
```

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: mark pipeline metrics complete, add metrics command"
```

---

## Task 10: Run Full Test Suite

**Step 1: Run all tests**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

**Step 2: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address test failures from collector metrics"
```

---

## Summary

**Files created:**
- `tests/storage/test_collector_metrics.py`
- `tests/workflows/test_collector_metrics_capture.py`
- `tests/test_cli_metrics.py`

**Files modified:**
- `storage/signal_store.py` - Schema migration 4, save/query methods
- `workflows/pipeline.py` - CollectorMetrics dataclass, timing capture
- `run_pipeline.py` - metrics CLI command
- `CLAUDE.md` - Documentation updates

**Total commits:** 10
