"""Pipeline Scheduler — schedule and execute automated discovery pipeline runs.

Designed for cron-friendly, idempotent execution (no daemon loop).
External triggers (Windows Task Scheduler, cron, manual CLI) invoke
`should_run` / `enqueue_run` / `execute_run`.

Uses ops/storage.py tables: pipeline_schedules, pipeline_run_history.
"""

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

from croniter import croniter

from ops.storage import OpsStorage
from utils.git_utils import get_git_info, DETACHED

logger = logging.getLogger(__name__)

_TRUTHY = {"true", "1", "yes", "on", "y"}

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def _branch_label(branch, sha):
    """Human-readable label for the current git branch state."""
    if branch is None:
        return "unknown branch (git unavailable)"
    if branch == DETACHED:
        return f"detached HEAD ({sha})"
    return branch


def _append_cadence_ledger_event(event: str, reason: str, schedule_name: str,
                                  git_branch=None, git_sha=None) -> None:
    """Append a non-run event (e.g., 'blocked') to the cadence ledger."""
    ledger_path = os.path.join(_REPO_ROOT, "artifacts", "cadence", "cadence_ledger.jsonl")
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    entry = {
        "run_id": None,
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schedule_name": schedule_name,
        "reason": reason,
        "git_branch": git_branch,
        "git_sha": git_sha,
    }
    try:
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write cadence ledger event: {e}")


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
            # Check if table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_schedules'"
            )
            table_exists = cursor.fetchone() is not None

            if table_exists:
                # Table exists - migrate if needed
                conn.executescript("""
                    -- Backup existing data
                    CREATE TABLE IF NOT EXISTS pipeline_schedules_backup AS SELECT * FROM pipeline_schedules;

                    -- Drop old table
                    DROP TABLE IF EXISTS pipeline_schedules;

                    -- Create new table with quality modes
                    CREATE TABLE pipeline_schedules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        cron_expression TEXT NOT NULL,
                        collectors TEXT DEFAULT '[]',
                        mode TEXT DEFAULT 'full' CHECK(mode IN ('full', 'collect', 'process', 'quality-sync', 'quality-classify', 'quality-patterns', 'canary-monitor')),
                        dry_run INTEGER DEFAULT 0,
                        enabled INTEGER DEFAULT 1,
                        max_retries INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );

                    -- Restore data
                    INSERT OR IGNORE INTO pipeline_schedules SELECT * FROM pipeline_schedules_backup;

                    -- Cleanup
                    DROP TABLE IF EXISTS pipeline_schedules_backup;

                    -- Create index
                    CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON pipeline_schedules(enabled);
                """)
            else:
                # Fresh database - just create
                conn.executescript("""
                    CREATE TABLE pipeline_schedules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        cron_expression TEXT NOT NULL,
                        collectors TEXT DEFAULT '[]',
                        mode TEXT DEFAULT 'full' CHECK(mode IN ('full', 'collect', 'process', 'quality-sync', 'quality-classify', 'quality-patterns', 'canary-monitor')),
                        dry_run INTEGER DEFAULT 0,
                        enabled INTEGER DEFAULT 1,
                        max_retries INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON pipeline_schedules(enabled);
                """)

            # Create other tables
            conn.executescript("""

                CREATE TABLE IF NOT EXISTS scheduler_locks (
                    schedule_name TEXT PRIMARY KEY,
                    locked_at TEXT NOT NULL,
                    locked_by TEXT DEFAULT 'scheduler'
                );

                CREATE TABLE IF NOT EXISTS pattern_runs (
                    run_id INTEGER PRIMARY KEY,
                    detected_at TEXT NOT NULL,
                    pattern_data TEXT NOT NULL,
                    pattern_count INTEGER,
                    FOREIGN KEY(run_id) REFERENCES pipeline_run_history(id) ON DELETE CASCADE
                );

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
    # Lock management (prevent overlapping runs)
    # -----------------------------------------------------------------------

    def _acquire_lock(self, schedule_name: str) -> bool:
        """
        Acquire advisory lock to prevent overlapping runs.

        Returns:
            True if lock acquired, False if already running
        """
        import sqlite3

        try:
            with self.storage.transaction() as conn:
                conn.execute(
                    "INSERT INTO scheduler_locks (schedule_name, locked_at) VALUES (?, ?)",
                    (schedule_name, datetime.now(timezone.utc).isoformat())
                )
            logger.debug(f"Acquired lock for schedule: {schedule_name}")
            return True
        except sqlite3.IntegrityError:
            logger.warning(f"Schedule {schedule_name} already running, skipping")
            return False

    def _release_lock(self, schedule_name: str):
        """Release advisory lock."""
        with self.storage.transaction() as conn:
            conn.execute(
                "DELETE FROM scheduler_locks WHERE schedule_name = ?",
                (schedule_name,)
            )
        logger.debug(f"Released lock for schedule: {schedule_name}")

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

    def get_schedule_by_name(self, name: str) -> Optional[dict]:
        with self.storage.read_transaction() as conn:
            conn.row_factory = _dict_factory
            row = conn.execute(
                "SELECT * FROM pipeline_schedules WHERE name = ?", (name,),
            ).fetchone()
            return row

    def ensure_schedule(self, config: ScheduleConfig) -> tuple:
        """Idempotent create. Returns (id, created, warnings).

        Warns on ALL field drift (R6-4), not just cron/mode. All warnings are
        non-blocking — the schedule is NOT modified, just reported.
        """
        existing = self.get_schedule_by_name(config.name)
        if existing:
            warnings = []
            if existing["cron_expression"] != config.cron_expression:
                warnings.append(f"cron mismatch: DB='{existing['cron_expression']}' vs requested='{config.cron_expression}'")
            if existing["mode"] != config.mode:
                warnings.append(f"mode mismatch: DB='{existing['mode']}' vs requested='{config.mode}'")
            if bool(existing["enabled"]) != config.enabled:
                warnings.append(f"enabled mismatch: DB={bool(existing['enabled'])} vs requested={config.enabled}")
            if bool(existing["dry_run"]) != config.dry_run:
                warnings.append(f"dry_run mismatch: DB={bool(existing['dry_run'])} vs requested={config.dry_run}")
            try:
                existing_collectors = json.loads(existing["collectors"]) if existing["collectors"] else []
            except (json.JSONDecodeError, TypeError):
                warnings.append("collectors malformed in DB (cannot parse), skipping comparison")
                existing_collectors = None
            if existing_collectors is not None and existing_collectors != config.collectors:
                warnings.append(f"collectors mismatch: DB={existing_collectors} vs requested={config.collectors}")
            try:
                existing_max_retries = int(existing.get("max_retries", 0))
            except (TypeError, ValueError):
                warnings.append("max_retries malformed in DB (cannot parse), skipping comparison")
                existing_max_retries = None
            if existing_max_retries is not None and existing_max_retries != config.max_retries:
                warnings.append(f"max_retries mismatch: DB={existing_max_retries} vs requested={config.max_retries}")
            return existing["id"], False, warnings
        sid = self.create_schedule(config)
        return sid, True, []

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
    # -----------------------------------------------------------------------
    # Quality workflow execution handlers (Phase 9)
    # -----------------------------------------------------------------------

    async def _execute_quality_sync(self, run_id: int, schedule: dict) -> dict:
        """
        Execute quality sync workflow:
        1. Sync Notion status events
        2. Backfill TP/FP outcomes from events

        Implements complete feedback loop closure.
        """
        logger.info(f"Starting quality-sync (run_id={run_id})")

        try:
            import os
            from ops.quality.status_events import sync_status_events
            from ops.quality.outcomes import backfill_outcomes_from_events

            # Get signals DB path
            db_path = os.getenv("DISCOVERY_DB_PATH", "signals.db")

            # Step 1: Sync Notion → local events
            events_synced = sync_status_events(db_path)
            logger.info(f"Synced {events_synced} status events")

            # Step 2: Infer TP/FP outcomes
            outcomes_updated = backfill_outcomes_from_events(db_path)
            logger.info(f"Updated {outcomes_updated} outcomes")

            return {
                "events_synced": events_synced,
                "outcomes_updated": outcomes_updated
            }
        except Exception as e:
            logger.error(f"Quality sync failed: {e}")
            raise

    async def _execute_quality_classify(self, run_id: int, schedule: dict) -> dict:
        """
        Batch classify recent signals with LLM.
        Uses UPSERT to prevent duplicates if job runs twice.
        """
        import os

        logger.info(f"Starting thesis-classify-batch (run_id={run_id})")

        # Only run if LLM mode is shadow or active
        llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
        if llm_mode == "off":
            logger.info("LLM disabled, skipping batch classification")
            return {"classified": 0, "reason": "llm_disabled"}

        try:
            from ops.quality.thesis import batch_classify_recent

            # Get signals DB path
            db_path = os.getenv("DISCOVERY_DB_PATH", "signals.db")

            # Classify in chunks to prevent lock contention
            classified = batch_classify_recent(
                db_path,
                limit=50,
                chunk_size=10,  # Process 10 at a time
                upsert=True  # Idempotent
            )

            logger.info(f"Classified {classified} signals")
            return {"classified": classified}
        except Exception as e:
            logger.error(f"Batch classification failed: {e}")
            raise

    async def _execute_quality_patterns(self, run_id: int, schedule: dict) -> dict:
        """
        Detect FP patterns weekly.
        Stores results in ops DB, not ephemeral file.
        """
        logger.info(f"Starting find-patterns (run_id={run_id})")

        try:
            import os
            from ops.quality.patterns import detect_patterns_wrapper

            # Get signals DB path
            db_path = os.getenv("DISCOVERY_DB_PATH", "signals.db")

            # Detect patterns over last 30 days
            patterns = detect_patterns_wrapper(db_path, days=30)

            # Store in ops DB for durability
            pattern_json = json.dumps(patterns)
            with self.storage.transaction() as conn:
                conn.execute(
                    """INSERT INTO pattern_runs (run_id, detected_at, pattern_data, pattern_count)
                       VALUES (?, ?, ?, ?)""",
                    (run_id, datetime.now(timezone.utc).isoformat(), pattern_json, len(patterns))
                )

            logger.info(f"Detected {len(patterns)} patterns")
            return {"patterns_detected": len(patterns)}
        except Exception as e:
            logger.error(f"Pattern detection failed: {e}")
            raise

    # -----------------------------------------------------------------------
    # Canary monitor execution handler
    # -----------------------------------------------------------------------

    async def _execute_canary_monitor(self, run_id: int, schedule: dict,
                                      git_branch=None, git_sha=None) -> dict:
        import asyncio
        # R4-1: Derive DB path from OpsStorage (same as CLI --db), NOT env var
        db_path = os.path.abspath(self.storage.db_path)
        python = sys.executable
        repo_root = _REPO_ROOT
        artifacts_dir = os.path.join(repo_root, "artifacts", "cadence")
        os.makedirs(artifacts_dir, exist_ok=True)
        # R6-3: Read per invocation so runtime env changes apply
        step_timeout = int(os.getenv("CANARY_STEP_TIMEOUT_SECONDS", "300"))

        # R4-1 + MO-4: Ensure subprocesses use the same DB path
        sub_env = {**os.environ, "DISCOVERY_DB_PATH": db_path}

        steps = [
            {"name": "canary", "cmd": [python, "-m", "monitoring.canary_checker", "run",
                                        "--db", db_path, "--store-results"], "required": True},
            {"name": "drift", "cmd": [python, os.path.join(repo_root, "run_pipeline.py"),
                                       "drift", "check", "--db-path", db_path], "required": True},
            {"name": "activation", "cmd": [python, os.path.join(repo_root, "run_pipeline.py"),
                                            "activation-check", "--step", "2", "--json",
                                            "--db-path", db_path], "required": True},
        ]

        # Optional shadow export — gate on LLM_THESIS_MODE (R2-5, R3-5)
        llm_mode = os.getenv("LLM_THESIS_MODE", "off").lower()
        if llm_mode in ("shadow", "active"):
            shadow_out = os.path.join(artifacts_dir, "shadow_cadence.jsonl")
            steps.append({"name": "shadow-export", "cmd": [
                python, os.path.join(repo_root, "scripts", "shadow_report.py"),
                "export", "--db-path", db_path, "--since-days", "1",
                "--limit", "2000", "--out", shadow_out], "required": False})

        results = {}
        failure_error = None
        try:
            for step in steps:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *step["cmd"],
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=repo_root,
                        env=sub_env,
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            proc.communicate(), timeout=step_timeout
                        )
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.communicate()  # reap
                        msg = f"step '{step['name']}' timed out after {step_timeout}s"
                        logger.error(f"canary-monitor {msg}")
                        results[step["name"]] = {"returncode": -1, "stdout": "", "stderr": msg}
                        if step["required"]:
                            failure_error = RuntimeError(f"canary-monitor required {msg}")
                            break
                        continue

                    results[step["name"]] = {
                        "returncode": proc.returncode,
                        "stdout": stdout.decode(errors="replace"),
                        "stderr": stderr.decode(errors="replace"),
                    }
                    logger.info(f"canary-monitor step '{step['name']}' exited {proc.returncode}")

                    if proc.returncode != 0 and step["required"]:
                        failure_error = RuntimeError(
                            f"canary-monitor required step '{step['name']}' failed "
                            f"(exit {proc.returncode}): {stderr.decode(errors='replace')[:500]}"
                        )
                        break
                    elif proc.returncode != 0:
                        logger.warning(f"canary-monitor optional step '{step['name']}' failed, continuing")

                except asyncio.CancelledError:
                    raise  # R6-2: Don't swallow cancellation
                except Exception as e:
                    # R5-1: Catch spawn failures (bad path, permission denied, etc.)
                    msg = f"step '{step['name']}' spawn failed: {e}"
                    logger.error(f"canary-monitor {msg}")
                    results[step["name"]] = {"returncode": -2, "stdout": "", "stderr": str(e)}
                    if step["required"]:
                        failure_error = RuntimeError(f"canary-monitor required {msg}")
                        break
        finally:
            # R3-1: Always write artifact, even with partial results
            _write_cadence_artifact(artifacts_dir, run_id, results,
                                    error=str(failure_error) if failure_error else None,
                                    git_branch=git_branch, git_sha=git_sha)

        if failure_error:
            raise failure_error

        return results

    # Execute (calls the actual pipeline)
    # -----------------------------------------------------------------------

    async def execute_run(self, schedule_id: int) -> dict:
        schedule = self.get_schedule(schedule_id)
        if not schedule or not schedule["enabled"]:
            raise ValueError(f"Schedule {schedule_id} is disabled or not found")

        schedule_name = schedule["name"]
        mode = schedule["mode"]

        # Branch-safety guardrail
        branch, sha = get_git_info()
        if branch != "main":
            label = _branch_label(branch, sha)
            logger.warning(
                "Schedule '%s' executing on %s. Code may differ from production.",
                schedule_name, label,
            )
            if (mode == "canary-monitor"
                    and os.getenv("REQUIRE_MAIN_FOR_CANARY", "").lower().strip() in _TRUTHY):
                _append_cadence_ledger_event(
                    event="blocked",
                    reason=f"Branch guardrail: on {label}, not 'main'",
                    schedule_name=schedule_name,
                    git_branch=branch,
                    git_sha=sha,
                )
                raise RuntimeError(
                    f"Canary monitor blocked: on {label}, not 'main'. "
                    f"Set REQUIRE_MAIN_FOR_CANARY=false to override."
                )

        # Phase 9: Acquire lock to prevent overlapping runs
        if not self._acquire_lock(schedule_name):
            return {
                "run_id": None,
                "status": "skipped",
                "reason": "already_running"
            }

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
            # Phase 9: Dispatch to appropriate handler based on mode
            if mode == "quality-sync":
                mode_result = await self._execute_quality_sync(run_id, schedule)
                stats_dict = {
                    "signals_found": 0,
                    "signals_processed": mode_result.get("outcomes_updated", 0),
                    "signals_pushed": 0,
                    "collectors_failed": 0
                }
            elif mode == "quality-classify":
                mode_result = await self._execute_quality_classify(run_id, schedule)
                stats_dict = {
                    "signals_found": 0,
                    "signals_processed": mode_result.get("classified", 0),
                    "signals_pushed": 0,
                    "collectors_failed": 0
                }
            elif mode == "quality-patterns":
                mode_result = await self._execute_quality_patterns(run_id, schedule)
                stats_dict = {
                    "signals_found": 0,
                    "signals_processed": mode_result.get("patterns_detected", 0),
                    "signals_pushed": 0,
                    "collectors_failed": 0
                }
            elif mode == "canary-monitor":
                mode_result = await self._execute_canary_monitor(
                    run_id, schedule, git_branch=branch, git_sha=sha,
                )
                stats_dict = {
                    "signals_found": 0,
                    "signals_processed": len([r for r in mode_result.values() if r["returncode"] == 0]),
                    "signals_pushed": 0,
                    "collectors_failed": len([r for r in mode_result.values() if r["returncode"] != 0]),
                }
            else:
                # Default: full pipeline execution
                from workflows.pipeline import DiscoveryPipeline

                pipeline = DiscoveryPipeline()
                await pipeline.initialize()

                stats = await pipeline.run_full_pipeline(
                    collectors=collectors or None,
                    dry_run=dry_run,
                )
                stats_dict = {
                    "signals_found": stats.signals_collected,
                    "signals_processed": stats.signals_processed,
                    "signals_pushed": stats.prospects_created,
                    "collectors_failed": stats.collectors_failed
                }

            finished_at = datetime.now(timezone.utc)
            result = {
                "run_id": run_id,
                "status": RunStatus.SUCCESS.value,
                "signals_found": stats_dict["signals_found"],
                "signals_processed": stats_dict["signals_processed"],
                "signals_pushed": stats_dict["signals_pushed"],
                "errors": stats_dict["collectors_failed"],
                "error_message": None,
                "duration_seconds": (finished_at - started_at).total_seconds(),
            }

            self.record_run(
                schedule_id=schedule_id,
                status=RunStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                signals_found=stats_dict["signals_found"],
                signals_processed=stats_dict["signals_processed"],
                signals_pushed=stats_dict["signals_pushed"],
                errors=stats_dict["collectors_failed"],
            )

            logger.info(f"Schedule '{schedule_name}' run {run_id} completed: {result}")
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

            logger.error(f"Schedule '{schedule_name}' run {run_id} failed: {error_msg}")
            return result

        finally:
            # Phase 9: Always release lock
            self._release_lock(schedule_name)

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

def _write_cadence_artifact(artifacts_dir: str, run_id: int, results: dict,
                            error: str | None = None,
                            git_branch: str | None = None,
                            git_sha: str | None = None) -> None:
    """Write JSON summary + append JSONL ledger line. Always called (even on failure)."""
    ts = datetime.now(timezone.utc).isoformat()

    def _trim(text: str, max_chars: int = 2048) -> str:
        """First/last ~2K chars for triage (MO-5, R5-3: char-based, not byte-based)."""
        if len(text) <= max_chars * 2:
            return text
        return text[:max_chars] + "\n...[trimmed]...\n" + text[-max_chars:]

    entry = {
        "run_id": run_id,
        "timestamp": ts,
        "git_branch": git_branch,
        "git_sha": git_sha,
        "steps": {
            k: {
                "returncode": v["returncode"],
                "stdout_trimmed": _trim(v.get("stdout", "")),
                "stderr_trimmed": _trim(v.get("stderr", "")),
            }
            for k, v in results.items()
        },
        "all_passed": all(v["returncode"] == 0 for v in results.values()) if results else False,
        "error": error,
    }

    try:
        summary_path = os.path.join(artifacts_dir, "latest_summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2)

        ledger_path = os.path.join(artifacts_dir, "cadence_ledger.jsonl")
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # R6-1: Catch OSError + UnicodeEncodeError + anything else
        logger.warning(f"Failed to write cadence artifact: {e}")


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
