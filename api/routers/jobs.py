"""
Jobs Router

Endpoints for managing background jobs:
- POST /jobs/collect - Start a collection job
- POST /jobs/process - Process pending signals
- POST /jobs/sync - Sync from Notion
- GET /jobs - List recent jobs
- GET /jobs/{id} - Get job status
- GET /jobs/{id}/logs - Get job logs
- POST /jobs/{id}/cancel - Cancel a running job
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth.jwt_auth import get_current_user, require_role, User, Role
from api.services.job_service import JobService
from storage.signal_store import SignalStore, Job, JobLog

router = APIRouter(prefix="/jobs", tags=["jobs"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class CollectRequest(BaseModel):
    """Request to start a collection job."""
    collector: str
    dry_run: bool = False


class ProcessRequest(BaseModel):
    """Request to start a processing job."""
    dry_run: bool = False


class JobResponse(BaseModel):
    """Job status response."""
    id: str
    job_type: str
    status: str
    progress_pct: int
    progress_message: Optional[str]
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_by: Optional[str]
    created_at: Optional[datetime]


class JobDetailResponse(JobResponse):
    """Detailed job response including params and result."""
    params: Optional[dict]
    result: Optional[dict]


class JobLogResponse(BaseModel):
    """Job log entry."""
    id: int
    level: str
    message: str
    logged_at: Optional[datetime]


class JobListResponse(BaseModel):
    """List of jobs."""
    jobs: List[JobResponse]
    total: int


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

async def get_store() -> SignalStore:
    """Get initialized SignalStore."""
    store = SignalStore()
    await store.initialize()
    return store


async def get_job_service(store: SignalStore = Depends(get_store)) -> JobService:
    """Get job service instance."""
    return JobService(store)


# =============================================================================
# AVAILABLE COLLECTORS
# =============================================================================

AVAILABLE_COLLECTORS = [
    "github", "github_activity", "sec_edgar", "companies_house",
    "domain_whois", "job_postings", "product_hunt", "hacker_news",
    "arxiv", "uspto", "linkedin", "crunchbase", "opencorporates",
]


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/collect", response_model=JobResponse)
async def start_collect_job(
    request: CollectRequest,
    user: User = Depends(require_role([Role.GP, Role.ANALYST])),
    service: JobService = Depends(get_job_service),
):
    """
    Start a collection job for a specific collector.

    Runs the collector in the background and returns job ID for tracking.
    """
    if request.collector not in AVAILABLE_COLLECTORS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown collector: {request.collector}. Available: {AVAILABLE_COLLECTORS}",
        )

    job = await service.start_collect_job(
        collector=request.collector,
        dry_run=request.dry_run,
        created_by=user.email,
    )

    return _job_to_response(job)


@router.post("/process", response_model=JobResponse)
async def start_process_job(
    request: ProcessRequest = ProcessRequest(),
    user: User = Depends(require_role([Role.GP, Role.ANALYST])),
    service: JobService = Depends(get_job_service),
):
    """
    Start a signal processing job.

    Processes pending signals through verification gate and pushes to Notion.
    """
    job = await service.start_process_job(
        dry_run=request.dry_run,
        created_by=user.email,
    )

    return _job_to_response(job)


@router.post("/sync", response_model=JobResponse)
async def start_sync_job(
    user: User = Depends(require_role([Role.GP, Role.ANALYST])),
    service: JobService = Depends(get_job_service),
):
    """
    Start a Notion sync job.

    Syncs suppression cache from Notion to avoid duplicate pushes.
    """
    job = await service.start_sync_job(created_by=user.email)

    return _job_to_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    job_type: Optional[str] = None,
    job_status: Optional[str] = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    service: JobService = Depends(get_job_service),
):
    """
    List recent jobs.

    Optionally filter by job type or status.
    """
    jobs = await service.list_jobs(
        job_type=job_type,
        status=job_status,
        limit=limit,
    )

    return JobListResponse(
        jobs=[_job_to_response(j) for j in jobs],
        total=len(jobs),
    )


@router.get("/types")
async def get_job_types():
    """Get available job types and collectors."""
    return {
        "job_types": ["collect", "process", "sync", "backup", "import"],
        "collectors": AVAILABLE_COLLECTORS,
    }


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    service: JobService = Depends(get_job_service),
):
    """
    Get detailed job status.

    Includes job parameters and result if completed.
    """
    job = await service.get_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    return _job_to_detail_response(job)


@router.get("/{job_id}/logs", response_model=List[JobLogResponse])
async def get_job_logs(
    job_id: str,
    limit: int = 100,
    level: Optional[str] = None,
    user: User = Depends(get_current_user),
    service: JobService = Depends(get_job_service),
):
    """
    Get logs for a job.

    Returns log entries in reverse chronological order.
    """
    # Verify job exists
    job = await service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    logs = await service.get_job_logs(job_id, limit=limit, level=level)

    return [
        JobLogResponse(
            id=log.id,
            level=log.level,
            message=log.message,
            logged_at=log.logged_at,
        )
        for log in logs
    ]


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    user: User = Depends(require_role([Role.GP, Role.ANALYST])),
    service: JobService = Depends(get_job_service),
):
    """
    Cancel a running job.

    Only works for jobs that are currently running.
    """
    job = await service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    if job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not running (status: {job.status})",
        )

    cancelled = await service.cancel_job(job_id)

    return {
        "success": cancelled,
        "job_id": job_id,
        "message": "Job cancelled" if cancelled else "Could not cancel job",
    }


# =============================================================================
# HELPERS
# =============================================================================

def _job_to_response(job: Job) -> JobResponse:
    """Convert Job to response model."""
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress_pct=job.progress_pct,
        progress_message=job.progress_message,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_by=job.created_by,
        created_at=job.created_at,
    )


def _job_to_detail_response(job: Job) -> JobDetailResponse:
    """Convert Job to detailed response model."""
    return JobDetailResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress_pct=job.progress_pct,
        progress_message=job.progress_message,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_by=job.created_by,
        created_at=job.created_at,
        params=job.params,
        result=job.result,
    )
