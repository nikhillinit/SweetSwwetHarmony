"""
Scheduler Router

CRUD and trigger endpoints for pipeline schedules:
- GET    /schedules              — list all schedules
- POST   /schedules              — create a new schedule
- GET    /schedules/{id}         — get a single schedule
- GET    /schedules/{id}/status  — detailed status with stats
- PUT    /schedules/{id}/pause   — pause schedule
- PUT    /schedules/{id}/resume  — resume schedule
- DELETE /schedules/{id}         — delete schedule
- POST   /schedules/{id}/trigger — enqueue immediate run
- GET    /schedules/{id}/history — run history
"""

import logging
import os
from sqlite3 import IntegrityError
from typing import Any, Dict, List, Optional

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator

from ops.scheduler import PipelineScheduler, ScheduleConfig
from ops.storage import OpsStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class ScheduleCreateRequest(BaseModel):
    name: str
    cron_expression: str
    collectors: List[str] = []
    mode: str = "full"
    dry_run: bool = False
    max_retries: int = 0

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v}")
        return v


class ScheduleResponse(BaseModel):
    id: int
    name: str
    cron_expression: str
    collectors: Any = None
    mode: str = "full"
    dry_run: Any = False
    enabled: Any = True
    max_retries: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MessageResponse(BaseModel):
    message: str


class TriggerResponse(BaseModel):
    run_id: int
    message: str


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

def get_scheduler() -> PipelineScheduler:
    """Get a PipelineScheduler instance."""
    db_path = os.getenv("DISCOVERY_DB_PATH", "signals.db")
    storage = OpsStorage(db_path)
    return PipelineScheduler(storage)


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("", response_model=List[Dict[str, Any]])
async def list_schedules(scheduler: PipelineScheduler = Depends(get_scheduler)):
    """List all pipeline schedules."""
    schedules = await run_in_threadpool(scheduler.list_schedules)
    return schedules


@router.post("", status_code=201, response_model=Dict[str, Any])
async def create_schedule(
    body: ScheduleCreateRequest,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Create a new pipeline schedule."""
    config = ScheduleConfig(
        name=body.name,
        cron_expression=body.cron_expression,
        collectors=body.collectors,
        mode=body.mode,
        dry_run=body.dry_run,
        max_retries=body.max_retries,
    )
    try:
        schedule_id = await run_in_threadpool(scheduler.create_schedule, config)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Schedule name already exists")

    schedule = await run_in_threadpool(scheduler.get_schedule, schedule_id)
    return schedule


@router.get("/{schedule_id}", response_model=Dict[str, Any])
async def get_schedule(
    schedule_id: int,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Get a single schedule by ID."""
    schedule = await run_in_threadpool(scheduler.get_schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return schedule


@router.get("/{schedule_id}/status", response_model=Dict[str, Any])
async def get_schedule_status(
    schedule_id: int,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Get detailed schedule status with run statistics."""
    try:
        status = await run_in_threadpool(scheduler.get_schedule_status, schedule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return status


@router.put("/{schedule_id}/pause", response_model=MessageResponse)
async def pause_schedule(
    schedule_id: int,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Pause a schedule."""
    try:
        await run_in_threadpool(scheduler.pause_schedule, schedule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return {"message": f"Schedule {schedule_id} paused"}


@router.put("/{schedule_id}/resume", response_model=MessageResponse)
async def resume_schedule(
    schedule_id: int,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Resume a schedule."""
    try:
        await run_in_threadpool(scheduler.resume_schedule, schedule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return {"message": f"Schedule {schedule_id} resumed"}


@router.delete("/{schedule_id}", response_model=MessageResponse)
async def delete_schedule(
    schedule_id: int,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Delete a schedule and its run history."""
    try:
        await run_in_threadpool(scheduler.delete_schedule, schedule_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")
    return {"message": f"Schedule {schedule_id} deleted"}


@router.post("/{schedule_id}/trigger", status_code=202, response_model=TriggerResponse)
async def trigger_run(
    schedule_id: int,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Trigger an immediate pipeline run for this schedule."""
    schedule = await run_in_threadpool(scheduler.get_schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")

    try:
        run_id = await run_in_threadpool(scheduler.enqueue_run, schedule_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"run_id": run_id, "message": f"Run {run_id} enqueued for schedule {schedule_id}"}


@router.get("/{schedule_id}/history", response_model=List[Dict[str, Any]])
async def get_run_history(
    schedule_id: int,
    limit: int = 20,
    scheduler: PipelineScheduler = Depends(get_scheduler),
):
    """Get run history for a schedule."""
    schedule = await run_in_threadpool(scheduler.get_schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"Schedule {schedule_id} not found")

    history = await run_in_threadpool(scheduler.get_run_history, schedule_id, limit=limit)
    return history
