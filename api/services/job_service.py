"""
Job Service for Background Operations

Manages long-running background jobs:
- Collection runs
- Signal processing
- Notion sync
- Database backups
- Email imports

Jobs are persisted to the database and can be:
- Started (runs in background)
- Monitored (get status/progress)
- Cancelled (if still running)

Usage:
    from api.services.job_service import JobService

    service = JobService(store)

    # Start a collection job
    job = await service.start_collect_job(
        collector="github",
        created_by="user@example.com"
    )

    # Check status
    status = await service.get_job(job.id)

    # Get logs
    logs = await service.get_job_logs(job.id)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field

from storage.signal_store import SignalStore, Job, JobLog

logger = logging.getLogger(__name__)


@dataclass
class JobResult:
    """Result of a completed job."""
    job_id: str
    success: bool
    message: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class JobService:
    """
    Service for managing background jobs.

    Jobs are persisted to SQLite and run as asyncio tasks.
    """

    def __init__(self, store: SignalStore):
        self.store = store
        self._running_tasks: Dict[str, asyncio.Task] = {}

    # =========================================================================
    # JOB LIFECYCLE
    # =========================================================================

    async def create_job(
        self,
        job_type: str,
        params: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
    ) -> Job:
        """
        Create a new job in pending state.

        Args:
            job_type: Type of job (collect, process, sync, etc.)
            params: Job parameters
            created_by: User who created the job

        Returns:
            Created Job
        """
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        db = await self.store._get_db()
        await db.execute("""
            INSERT INTO jobs (id, job_type, status, params_json, created_by, created_at)
            VALUES (?, ?, 'pending', ?, ?, ?)
        """, (job_id, job_type, _json_dumps(params), created_by, now))
        await db.commit()

        return Job(
            id=job_id,
            job_type=job_type,
            status="pending",
            params=params,
            created_by=created_by,
            created_at=datetime.fromisoformat(now),
        )

    async def start_job(
        self,
        job_id: str,
        executor: Callable[[str, JobService], Awaitable[JobResult]],
    ) -> Job:
        """
        Start a job running in the background.

        Args:
            job_id: Job ID to start
            executor: Async function to run the job

        Returns:
            Updated Job
        """
        now = datetime.now(timezone.utc).isoformat()

        db = await self.store._get_db()
        await db.execute("""
            UPDATE jobs SET status = 'running', started_at = ?
            WHERE id = ? AND status = 'pending'
        """, (now, job_id))
        await db.commit()

        # Create background task
        task = asyncio.create_task(self._run_job(job_id, executor))
        self._running_tasks[job_id] = task

        return await self.get_job(job_id)

    async def _run_job(
        self,
        job_id: str,
        executor: Callable[[str, JobService], Awaitable[JobResult]],
    ):
        """Run a job and update its status on completion."""
        try:
            result = await executor(job_id, self)

            # Update job as completed
            now = datetime.now(timezone.utc).isoformat()
            db = await self.store._get_db()
            await db.execute("""
                UPDATE jobs
                SET status = 'completed',
                    completed_at = ?,
                    result_json = ?,
                    progress_pct = 100,
                    progress_message = ?
                WHERE id = ?
            """, (now, _json_dumps(result.result), result.message, job_id))
            await db.commit()

        except asyncio.CancelledError:
            # Job was cancelled
            now = datetime.now(timezone.utc).isoformat()
            db = await self.store._get_db()
            await db.execute("""
                UPDATE jobs
                SET status = 'cancelled', completed_at = ?, error_message = 'Job was cancelled'
                WHERE id = ?
            """, (now, job_id))
            await db.commit()
            raise

        except Exception as e:
            # Job failed
            logger.exception(f"Job {job_id} failed: {e}")
            now = datetime.now(timezone.utc).isoformat()
            db = await self.store._get_db()
            await db.execute("""
                UPDATE jobs
                SET status = 'failed', completed_at = ?, error_message = ?
                WHERE id = ?
            """, (now, str(e), job_id))
            await db.commit()

        finally:
            # Clean up task reference
            self._running_tasks.pop(job_id, None)

    async def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if job was cancelled, False if not running
        """
        task = self._running_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # =========================================================================
    # JOB QUERIES
    # =========================================================================

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        db = await self.store._get_db()
        cursor = await db.execute("""
            SELECT id, job_type, status, params_json, progress_pct,
                   progress_message, result_json, error_message,
                   started_at, completed_at, created_by, created_at
            FROM jobs WHERE id = ?
        """, (job_id,))
        row = await cursor.fetchone()

        if not row:
            return None

        return Job(
            id=row[0],
            job_type=row[1],
            status=row[2],
            params=_json_loads(row[3]),
            progress_pct=row[4] or 0,
            progress_message=row[5],
            result=_json_loads(row[6]),
            error_message=row[7],
            started_at=_parse_datetime(row[8]),
            completed_at=_parse_datetime(row[9]),
            created_by=row[10],
            created_at=_parse_datetime(row[11]),
        )

    async def list_jobs(
        self,
        job_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Job]:
        """List jobs with optional filtering."""
        db = await self.store._get_db()

        query = """
            SELECT id, job_type, status, params_json, progress_pct,
                   progress_message, result_json, error_message,
                   started_at, completed_at, created_by, created_at
            FROM jobs
            WHERE 1=1
        """
        params = []

        if job_type:
            query += " AND job_type = ?"
            params.append(job_type)

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        jobs = []

        async for row in cursor:
            jobs.append(Job(
                id=row[0],
                job_type=row[1],
                status=row[2],
                params=_json_loads(row[3]),
                progress_pct=row[4] or 0,
                progress_message=row[5],
                result=_json_loads(row[6]),
                error_message=row[7],
                started_at=_parse_datetime(row[8]),
                completed_at=_parse_datetime(row[9]),
                created_by=row[10],
                created_at=_parse_datetime(row[11]),
            ))

        return jobs

    # =========================================================================
    # JOB LOGS
    # =========================================================================

    async def log(
        self,
        job_id: str,
        message: str,
        level: str = "info",
    ):
        """Add a log entry for a job."""
        now = datetime.now(timezone.utc).isoformat()
        db = await self.store._get_db()
        await db.execute("""
            INSERT INTO job_logs (job_id, level, message, logged_at)
            VALUES (?, ?, ?, ?)
        """, (job_id, level, message, now))
        await db.commit()

    async def get_job_logs(
        self,
        job_id: str,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> List[JobLog]:
        """Get logs for a job."""
        db = await self.store._get_db()

        query = "SELECT id, job_id, level, message, logged_at FROM job_logs WHERE job_id = ?"
        params = [job_id]

        if level:
            query += " AND level = ?"
            params.append(level)

        query += " ORDER BY logged_at DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        logs = []

        async for row in cursor:
            logs.append(JobLog(
                id=row[0],
                job_id=row[1],
                level=row[2],
                message=row[3],
                logged_at=_parse_datetime(row[4]),
            ))

        return logs

    async def update_progress(
        self,
        job_id: str,
        progress_pct: int,
        message: Optional[str] = None,
    ):
        """Update job progress."""
        db = await self.store._get_db()
        await db.execute("""
            UPDATE jobs SET progress_pct = ?, progress_message = ?
            WHERE id = ?
        """, (progress_pct, message, job_id))
        await db.commit()

    # =========================================================================
    # SPECIALIZED JOB STARTERS
    # =========================================================================

    async def start_collect_job(
        self,
        collector: str,
        dry_run: bool = False,
        created_by: Optional[str] = None,
    ) -> Job:
        """Start a collection job for a specific collector."""
        job = await self.create_job(
            job_type="collect",
            params={"collector": collector, "dry_run": dry_run},
            created_by=created_by,
        )
        return await self.start_job(job.id, _run_collect_job)

    async def start_process_job(
        self,
        dry_run: bool = False,
        created_by: Optional[str] = None,
    ) -> Job:
        """Start a signal processing job."""
        job = await self.create_job(
            job_type="process",
            params={"dry_run": dry_run},
            created_by=created_by,
        )
        return await self.start_job(job.id, _run_process_job)

    async def start_sync_job(
        self,
        created_by: Optional[str] = None,
    ) -> Job:
        """Start a Notion sync job."""
        job = await self.create_job(
            job_type="sync",
            params={},
            created_by=created_by,
        )
        return await self.start_job(job.id, _run_sync_job)


# =============================================================================
# JOB EXECUTORS
# =============================================================================

async def _run_collect_job(job_id: str, service: JobService) -> JobResult:
    """Execute a collection job."""
    job = await service.get_job(job_id)
    if not job:
        return JobResult(job_id=job_id, success=False, message="Job not found")

    collector_name = job.params.get("collector") if job.params else None
    dry_run = job.params.get("dry_run", False) if job.params else False

    await service.log(job_id, f"Starting collection for {collector_name}")
    await service.update_progress(job_id, 10, f"Loading collector {collector_name}")

    try:
        # Import collector dynamically
        from workflows.pipeline import DiscoveryPipeline

        pipeline = DiscoveryPipeline()
        await pipeline.initialize()

        await service.update_progress(job_id, 30, "Running collection")

        # Run single collector
        result = await pipeline.run_collectors(
            collectors=[collector_name],
            dry_run=dry_run,
        )

        signals_found = result.get("signals_collected", 0)
        await service.log(job_id, f"Collected {signals_found} signals")

        return JobResult(
            job_id=job_id,
            success=True,
            message=f"Collected {signals_found} signals from {collector_name}",
            result=result,
        )

    except ImportError as e:
        return JobResult(
            job_id=job_id,
            success=False,
            message="Pipeline not available",
            error=str(e),
        )
    except Exception as e:
        await service.log(job_id, f"Error: {e}", level="error")
        raise


async def _run_process_job(job_id: str, service: JobService) -> JobResult:
    """Execute a signal processing job."""
    await service.log(job_id, "Starting signal processing")
    await service.update_progress(job_id, 10, "Loading pipeline")

    try:
        from workflows.pipeline import DiscoveryPipeline

        pipeline = DiscoveryPipeline()
        await pipeline.initialize()

        await service.update_progress(job_id, 30, "Processing signals")

        result = await pipeline.process_pending()

        processed = result.get("signals_processed", 0)
        pushed = result.get("signals_pushed", 0)

        await service.log(job_id, f"Processed {processed} signals, pushed {pushed}")

        return JobResult(
            job_id=job_id,
            success=True,
            message=f"Processed {processed} signals, pushed {pushed} to Notion",
            result=result,
        )

    except ImportError as e:
        return JobResult(
            job_id=job_id,
            success=False,
            message="Pipeline not available",
            error=str(e),
        )
    except Exception as e:
        await service.log(job_id, f"Error: {e}", level="error")
        raise


async def _run_sync_job(job_id: str, service: JobService) -> JobResult:
    """Execute a Notion sync job."""
    await service.log(job_id, "Starting Notion sync")
    await service.update_progress(job_id, 10, "Connecting to Notion")

    try:
        from workflows.suppression_sync import SuppressionSync

        sync = SuppressionSync()

        await service.update_progress(job_id, 30, "Syncing suppression cache")

        result = await sync.sync()

        synced = result.get("records_synced", 0)
        await service.log(job_id, f"Synced {synced} records from Notion")

        return JobResult(
            job_id=job_id,
            success=True,
            message=f"Synced {synced} records from Notion",
            result=result,
        )

    except ImportError as e:
        return JobResult(
            job_id=job_id,
            success=False,
            message="Suppression sync not available",
            error=str(e),
        )
    except Exception as e:
        await service.log(job_id, f"Error: {e}", level="error")
        raise


# =============================================================================
# UTILITIES
# =============================================================================

import json


def _json_dumps(obj: Any) -> Optional[str]:
    """Serialize to JSON, returning None for None."""
    if obj is None:
        return None
    return json.dumps(obj)


def _json_loads(s: Optional[str]) -> Optional[Any]:
    """Parse JSON, returning None for None."""
    if s is None:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _parse_datetime(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
