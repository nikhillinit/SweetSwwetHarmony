"""Pipeline Scheduler — schedule and execute automated discovery pipeline runs.

Designed for cron-friendly, idempotent execution (no daemon loop).
External triggers (Windows Task Scheduler, cron, manual CLI) invoke
`should_run` / `enqueue_run` / `execute_run`.

Uses ops/storage.py tables: pipeline_schedules, pipeline_run_history.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from croniter import croniter

from ops.storage import OpsStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class ScheduleStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScheduleConfig:
    name: str
    cron_expression: str
    collectors: List[str] = field(default_factory=list)
    mode: str = "full"  # full | collect | process
    dry_run: bool = False
    enabled: bool = True
    max_retries: int = 0


@dataclass
class RunRecord:
    id: int
    schedule_id: int
    status: RunStatus
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    signals_found: int = 0
    signals_processed: int = 0
    signals_pushed: int = 0
    errors: int = 0
    error_message: Optional[str] = None
    cost: float = 0.0


# ---------------------------------------------------------------------------
# PipelineScheduler
# ---------------------------------------------------------------------------

class PipelineScheduler:
    """Manages pipeline schedule CRUD and run execution."""

    def __init__(self, storage: OpsStorage):
        self.storage = storage
        self._ensure_tables()

    def _ensure_tables(self):
        """Ensure scheduler tables exist (idempotent)."""
        with self.storage.pool.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pipeline_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    cron_expression TEXT NOT NULL,
                    collectors TEXT DEFAULT '[]',
                    mode TEXT DEFAULT 'full' CHECK(mode IN ('full', 'collect', 'process')),
                    dry_run INTEGER DEFAULT 0,
                    enabled INTEGER DEFAULT 1,
                    max_retries INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON pipeline_schedules(enabled);

                CREATE TABLE IF NOT EXISTS pipeline_run_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'success', 'failed', 'cancelled')),
                    idempotency_key TEXT UNIQUE,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP,
                    signals_found INTEGER DEFAULT 0,
                    signals_processed INTEGER DEFAULT 0,
                    signals_pushed INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0,
                    error_message TEXT,
                    cost REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(schedule_id) REFERENCES pipeline_schedules(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_run_history_schedule ON pipeline_run_history(schedule_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_run_history_status ON pipeline_run_history(status);
                CREATE INDEX IF NOT EXISTS idx_run_history_idempotency ON pipeline_run_history(idempotency_key);
            """)

    # -----------------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------------

    def create_schedule(self, config: ScheduleConfig) -> int:
        with self.storage.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO pipeline_schedules
                   (name, cron_expression, collectors, mode, dry_run, enabled, max_retries)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    config.name,
                    config.cron_expression,
                    json.dumps(config.collectors),
                    config.mode,
                    int(config.dry_run),
                    int(config.enabled),
                    config.max_retries,
                ),
            )
            schedule_id = cursor.lastrowid
            logger.info(f"Created schedule '{config.name}' (id={schedule_id})")
            return schedule_id

    def get_schedule(self, schedule_id: int) -> Optional[dict]:
        with self.storage.read_transaction() as conn:
            conn.row_factory = _dict_factory
            row = conn.execute(
                "SELECT * FROM pipeline_schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
            return row

    def list_schedules(self) -> List[dict]:
        with self.storage.read_transaction() as conn:
            conn.row_factory = _dict_factory
            rows = conn.execute(
                "SELECT * FROM pipeline_schedules ORDER BY created_at"
            ).fetchall()
            return rows

    def update_schedule(self, schedule_id: int, **kwargs) -> None:
        allowed = {"cron_expression", "collectors", "mode", "dry_run", "enabled", "max_retries", "name"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        # Serialize collectors to JSON if present
        if "collectors" in updates:
            updates["collectors"] = json.dumps(updates["collectors"])
        if "dry_run" in updates:
            updates["dry_run"] = int(updates["dry_run"])
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [schedule_id]

        with self.storage.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE pipeline_schedules SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Schedule {schedule_id} not found")

    def pause_schedule(self, schedule_id: int) -> None:
        self.update_schedule(schedule_id, enabled=False)
        logger.info(f"Paused schedule {schedule_id}")

    def resume_schedule(self, schedule_id: int) -> None:
        self.update_schedule(schedule_id, enabled=True)
        logger.info(f"Resumed schedule {schedule_id}")

    def delete_schedule(self, schedule_id: int) -> None:
        with self.storage.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM pipeline_schedules WHERE id = ?",
                (schedule_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Schedule {schedule_id} not found")
            logger.info(f"Deleted schedule {schedule_id}")

    # -----------------------------------------------------------------------
    # Cron helpers
    # -----------------------------------------------------------------------

    def get_next_run(self, schedule_id: int) -> Optional[datetime]:
        schedule = self.get_schedule(schedule_id)
        if not schedule or not schedule["enabled"]:
            return None
        cron = croniter(schedule["cron_expression"], datetime.now(timezone.utc))
        return cron.get_next(datetime).replace(tzinfo=timezone.utc)

    def should_run(self, schedule_id: int) -> bool:
        schedule = self.get_schedule(schedule_id)
        if not schedule or not schedule["enabled"]:
            return False

        # Check if already ran in this cron slot
        now = datetime.now(timezone.utc)
        cron = croniter(schedule["cron_expression"], now)
        # Get the previous cron tick (the current slot)
        prev_tick = cron.get_prev(datetime).replace(tzinfo=timezone.utc)
        idempotency_key = self._make_idempotency_key(schedule_id, prev_tick)

        with self.storage.read_transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM pipeline_run_history WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return row is None

    def _make_idempotency_key(self, schedule_id: int, slot_time: datetime) -> str:
        slot_str = slot_time.strftime("%Y-%m-%dT%H:%M")
        return f"pipeline_run:{schedule_id}:{slot_str}"

    # -----------------------------------------------------------------------
    # Run management
    # -----------------------------------------------------------------------

    def enqueue_run(self, schedule_id: int) -> int:
        schedule = self.get_schedule(schedule_id)
        if not schedule or not schedule["enabled"]:
            raise ValueError(f"Schedule {schedule_id} is disabled or not found")

        now = datetime.now(timezone.utc)
        cron = croniter(schedule["cron_expression"], now)
        prev_tick = cron.get_prev(datetime).replace(tzinfo=timezone.utc)
        idempotency_key = self._make_idempotency_key(schedule_id, prev_tick)

        with self.storage.transaction() as conn:
            # Check for existing run with same idempotency key
            row = conn.execute(
                "SELECT id FROM pipeline_run_history WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row:
                return row[0]

            cursor = conn.execute(
                """INSERT INTO pipeline_run_history
                   (schedule_id, status, idempotency_key, started_at)
                   VALUES (?, ?, ?, ?)""",
                (schedule_id, RunStatus.PENDING.value, idempotency_key, now.isoformat()),
            )
            run_id = cursor.lastrowid
            logger.info(f"Enqueued run {run_id} for schedule {schedule_id}")
            return run_id

    def record_run(
        self,
        schedule_id: int,
        status: RunStatus,
        started_at: datetime,
        finished_at: Optional[datetime] = None,
        signals_found: int = 0,
        signals_processed: int = 0,
        signals_pushed: int = 0,
        errors: int = 0,
        error_message: Optional[str] = None,
        cost: float = 0.0,
    ) -> int:
        cron = croniter(
            self.get_schedule(schedule_id)["cron_expression"],
            started_at,
        )
        # Use started_at as the slot reference
        # Get the previous tick relative to started_at
        prev_tick = cron.get_prev(datetime).replace(tzinfo=timezone.utc)
        idempotency_key = self._make_idempotency_key(schedule_id, prev_tick)

        with self.storage.transaction() as conn:
            # Upsert: update if exists, insert if not
            existing = conn.execute(
                "SELECT id FROM pipeline_run_history WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE pipeline_run_history
                       SET status = ?, finished_at = ?, signals_found = ?,
                           signals_processed = ?, signals_pushed = ?,
                           errors = ?, error_message = ?, cost = ?
                       WHERE id = ?""",
                    (
                        status.value, finished_at.isoformat() if finished_at else None,
                        signals_found, signals_processed, signals_pushed,
                        errors, error_message, cost, existing[0],
                    ),
                )
                return existing[0]

            cursor = conn.execute(
                """INSERT INTO pipeline_run_history
                   (schedule_id, status, idempotency_key, started_at, finished_at,
                    signals_found, signals_processed, signals_pushed,
                    errors, error_message, cost)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    schedule_id, status.value, idempotency_key,
                    started_at.isoformat(),
                    finished_at.isoformat() if finished_at else None,
                    signals_found, signals_processed, signals_pushed,
                    errors, error_message, cost,
                ),
            )
            return cursor.lastrowid

    def get_run_history(self, schedule_id: int, limit: int = 20) -> List[dict]:
        with self.storage.read_transaction() as conn:
            conn.row_factory = _dict_factory
            rows = conn.execute(
                """SELECT * FROM pipeline_run_history
                   WHERE schedule_id = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (schedule_id, limit),
            ).fetchall()
            return rows

    # -----------------------------------------------------------------------
    # Execute (calls the actual pipeline)
    # -----------------------------------------------------------------------

    async def execute_run(self, schedule_id: int) -> dict:
        schedule = self.get_schedule(schedule_id)
        if not schedule or not schedule["enabled"]:
            raise ValueError(f"Schedule {schedule_id} is disabled or not found")

        collectors = json.loads(schedule["collectors"]) if schedule["collectors"] else []
        dry_run = bool(schedule["dry_run"])

        started_at = datetime.now(timezone.utc)
        run_id = self.enqueue_run(schedule_id)

        # Mark as running
        with self.storage.transaction() as conn:
            conn.execute(
                "UPDATE pipeline_run_history SET status = ?, started_at = ? WHERE id = ?",
                (RunStatus.RUNNING.value, started_at.isoformat(), run_id),
            )

        try:
            from workflows.pipeline import DiscoveryPipeline

            pipeline = DiscoveryPipeline()
            await pipeline.initialize()

            stats = await pipeline.run_full_pipeline(
                collectors=collectors or None,
                dry_run=dry_run,
            )

            finished_at = datetime.now(timezone.utc)
            result = {
                "run_id": run_id,
                "status": RunStatus.SUCCESS.value,
                "signals_found": stats.signals_collected,
                "signals_processed": stats.signals_processed,
                "signals_pushed": stats.prospects_created,
                "errors": stats.collectors_failed,
                "error_message": None,
                "duration_seconds": (finished_at - started_at).total_seconds(),
            }

            self.record_run(
                schedule_id=schedule_id,
                status=RunStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                signals_found=stats.signals_collected,
                signals_processed=stats.signals_processed,
                signals_pushed=stats.prospects_created,
                errors=stats.collectors_failed,
            )

            logger.info(f"Pipeline run {run_id} completed: {result}")
            return result

        except Exception as e:
            finished_at = datetime.now(timezone.utc)
            error_msg = str(e)

            result = {
                "run_id": run_id,
                "status": RunStatus.FAILED.value,
                "signals_found": 0,
                "signals_processed": 0,
                "signals_pushed": 0,
                "errors": 1,
                "error_message": error_msg,
                "duration_seconds": (finished_at - started_at).total_seconds(),
            }

            self.record_run(
                schedule_id=schedule_id,
                status=RunStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                error_message=error_msg,
                errors=1,
            )

            logger.error(f"Pipeline run {run_id} failed: {error_msg}")
            return result

    # -----------------------------------------------------------------------
    # Status summary
    # -----------------------------------------------------------------------

    def get_schedule_status(self, schedule_id: int) -> dict:
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        with self.storage.read_transaction() as conn:
            conn.row_factory = _dict_factory

            # Last run
            last_run = conn.execute(
                """SELECT * FROM pipeline_run_history
                   WHERE schedule_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (schedule_id,),
            ).fetchone()

            # Total runs + success count
            stats = conn.execute(
                """SELECT
                       COUNT(*) as total,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes
                   FROM pipeline_run_history
                   WHERE schedule_id = ?""",
                (schedule_id,),
            ).fetchone()

        total = stats["total"] if stats else 0
        successes = stats["successes"] if stats else 0
        success_rate = (successes / total * 100) if total > 0 else 0.0

        next_run = self.get_next_run(schedule_id)

        return {
            "id": schedule_id,
            "name": schedule["name"],
            "cron_expression": schedule["cron_expression"],
            "enabled": bool(schedule["enabled"]),
            "last_run": last_run,
            "next_run": next_run.isoformat() if next_run else None,
            "total_runs": total,
            "success_rate": success_rate,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
