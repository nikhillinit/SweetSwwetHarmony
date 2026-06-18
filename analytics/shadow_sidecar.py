"""Shadow sidecar — read-only signals.db consumer + isolated shadow store.

This module is the Phase 0 (red-team v2 task `p0.5`) implementation of the
shadow sidecar. It enforces the **safety contract** from red-team v2 P1:

  1. NEVER opens a writable connection to `signals.db`.
  2. Read access uses one of two safe modes:
       - SNAPSHOT mode (preferred): copies signals.db → data/shadow/signals_snapshot.db
         once per run, reads from the snapshot. Zero coupling to live DB.
       - IMMUTABLE_URI mode: opens signals.db via
         `file:signals.db?mode=ro&immutable=1` URI. Tells SQLite the file
         will not change during the connection — no WAL locks acquired.
  3. All sidecar writes go to a separate file (`data/shadow/discovery.db`).
  4. The sidecar process registers with `DBToolLock` so the watermark guard
     knows to ignore it.

This sidecar exists because the red-team v1 plan was overly cautious about
*which* work the Step 4B regret window blocks. Public-data shadow collection
in a separate store is fine; the regret window protects production routing /
governance / Notion push code, not all engineering activity.

Critical: do NOT add any code to this module that opens `signals.db` in a
writable mode (no `mode=rw`, no plain `sqlite3.connect("signals.db")` without
the immutable URI). CI lint guards in `tests/scripts/` enforce this property.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, List, Mapping, Optional

from storage.db_paths import guard_db_path, resolve_canonical_db_path
from utils.db_tool_lock import DBToolLock

logger = logging.getLogger(__name__)


# ---- Constants -------------------------------------------------------------

DEFAULT_PRODUCTION_DB = Path("signals.db")
DEFAULT_SHADOW_ROOT = Path("data/shadow")
DEFAULT_SHADOW_DB = DEFAULT_SHADOW_ROOT / "discovery.db"
DEFAULT_SNAPSHOT_DB = DEFAULT_SHADOW_ROOT / "signals_snapshot.db"


def _default_production_db() -> Path:
    return resolve_canonical_db_path()


class ReadMode(str, Enum):
    """How the sidecar should access the production signals.db.

    SNAPSHOT     — copy signals.db to data/shadow/signals_snapshot.db once at
                   sidecar startup, then open the snapshot. Best when the
                   sidecar runs for hours and you want zero coupling to the
                   live DB.

    IMMUTABLE_URI — open signals.db via SQLite URI with `mode=ro&immutable=1`.
                    No WAL locks acquired, no SHM contention with the live
                    pipeline. Best for short-lived sidecar runs that want
                    fresh data without paying snapshot copy cost.
    """

    SNAPSHOT = "snapshot"
    IMMUTABLE_URI = "immutable_uri"


# ---- Errors ----------------------------------------------------------------


class ShadowSidecarError(Exception):
    """Base error for shadow sidecar."""


class UnsafeWriteError(ShadowSidecarError):
    """Raised when code attempts to write to the production DB through the sidecar."""


# ---- Sidecar ---------------------------------------------------------------


@dataclass
class ShadowSidecarConfig:
    """Configuration for ShadowSidecar.

    All paths are relative to the project root unless absolute.
    """

    production_db: Path = field(default_factory=_default_production_db)
    shadow_db: Path = field(default_factory=lambda: DEFAULT_SHADOW_DB)
    snapshot_db: Path = field(default_factory=lambda: DEFAULT_SNAPSHOT_DB)
    read_mode: ReadMode = ReadMode.IMMUTABLE_URI
    register_dbtool_lock: bool = True

    def __post_init__(self) -> None:
        self.production_db = guard_db_path(Path(self.production_db).expanduser().resolve())


class ShadowSidecar:
    """Read-only producer/consumer for production signals + isolated shadow store.

    Usage::

        cfg = ShadowSidecarConfig()
        sidecar = ShadowSidecar(cfg)
        sidecar.initialize()
        with sidecar.production_read_connection() as conn:
            rows = conn.execute("SELECT id, source_api FROM signals LIMIT 10").fetchall()
        sidecar.shadow_write("INSERT INTO shadow_signals (...) VALUES (...)", params)
        sidecar.close()
    """

    SHADOW_SCHEMA = """
    -- Shadow store schema. Mirrors-by-shape but does NOT share rows with signals.db.
    -- All shadow collectors write here only. Nothing in this file ever
    -- reaches the production pipeline.

    CREATE TABLE IF NOT EXISTS shadow_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shadow_collector TEXT NOT NULL,        -- e.g. "shadow_ct_log"
        canonical_key TEXT,                    -- nullable for free-form discoveries
        company_name TEXT,
        confidence REAL NOT NULL DEFAULT 0.0,
        raw_data TEXT NOT NULL,                -- JSON
        detected_at TEXT NOT NULL,             -- ISO 8601
        created_at TEXT NOT NULL               -- ISO 8601
    );

    CREATE INDEX IF NOT EXISTS idx_shadow_signals_collector
        ON shadow_signals(shadow_collector, created_at);
    CREATE INDEX IF NOT EXISTS idx_shadow_signals_canonical_key
        ON shadow_signals(canonical_key);

    CREATE TABLE IF NOT EXISTS shadow_episodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id TEXT NOT NULL,
        canonical_key TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        evidence_classes TEXT NOT NULL,        -- JSON array of EvidenceClass values
        shadow_tier TEXT NOT NULL,             -- "none" | "tier_1" | "tier_2"
        bundle_metadata TEXT,                  -- JSON
        UNIQUE(company_id)
    );

    CREATE INDEX IF NOT EXISTS idx_shadow_episodes_tier
        ON shadow_episodes(shadow_tier, last_seen_at);

    CREATE TABLE IF NOT EXISTS shadow_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL UNIQUE,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        collector TEXT NOT NULL,
        items_collected INTEGER NOT NULL DEFAULT 0,
        notes TEXT
    );
    """

    def __init__(self, config: Optional[ShadowSidecarConfig] = None) -> None:
        self.config = config or ShadowSidecarConfig()
        self._shadow_conn: Optional[sqlite3.Connection] = None
        self._dbtool_lock: Optional[DBToolLock] = None
        self._initialized = False

    # ---- Lifecycle ----

    def initialize(self) -> None:
        """Set up the shadow store and (optionally) snapshot the production DB.

        Side effects:
          - Creates `data/shadow/` if it doesn't exist
          - Creates `data/shadow/discovery.db` and applies SHADOW_SCHEMA
          - If read_mode == SNAPSHOT, copies signals.db -> snapshot_db
          - Registers with DBToolLock as a non-destructive reader
        """
        if self._initialized:
            return

        self.config.shadow_db.parent.mkdir(parents=True, exist_ok=True)

        # Open the shadow write store and apply the schema
        self._shadow_conn = sqlite3.connect(str(self.config.shadow_db))
        self._shadow_conn.executescript(self.SHADOW_SCHEMA)
        self._shadow_conn.commit()

        # Register with DBToolLock so the watermark guard / restore tooling
        # knows the sidecar is reading. The sidecar uses a per-tool lock name
        # so it cannot collide with destructive tools.
        if self.config.register_dbtool_lock:
            self._dbtool_lock = DBToolLock(
                self.config.shadow_db,
                tool_name="shadow_sidecar",
                ttl_seconds=4 * 3600,
            )
            if not self._dbtool_lock.acquire(timeout_seconds=5):
                holder = self._dbtool_lock.get_holder_info()
                raise ShadowSidecarError(
                    f"Could not acquire shadow_sidecar DB tool lock; holder={holder}"
                )

        # Snapshot if requested
        if self.config.read_mode == ReadMode.SNAPSHOT:
            self._take_snapshot()

        self._initialized = True
        logger.info(
            "ShadowSidecar initialized: shadow_db=%s, read_mode=%s",
            self.config.shadow_db,
            self.config.read_mode.value,
        )

    def close(self) -> None:
        """Release the lock and close the shadow connection."""
        if self._shadow_conn is not None:
            self._shadow_conn.close()
            self._shadow_conn = None
        if self._dbtool_lock is not None:
            self._dbtool_lock.release()
            self._dbtool_lock = None
        self._initialized = False

    def __enter__(self) -> "ShadowSidecar":
        self.initialize()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ---- Production DB read access ----

    @contextmanager
    def production_read_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a read-only connection to the production signals.db.

        SNAPSHOT mode: connect to the local snapshot file (no live coupling).
        IMMUTABLE_URI mode: connect to signals.db via immutable URI (no WAL locks).

        The yielded connection is read-only by construction. Any INSERT/UPDATE/
        DELETE statement will raise sqlite3.OperationalError ("attempt to write
        a readonly database").
        """
        if not self._initialized:
            raise ShadowSidecarError("ShadowSidecar.initialize() must be called first")

        if self.config.read_mode == ReadMode.SNAPSHOT:
            target = self.config.snapshot_db
            uri = f"file:{target.as_posix()}?mode=ro"
        else:
            target = self.config.production_db
            uri = f"file:{target.as_posix()}?mode=ro&immutable=1"

        if not target.exists():
            raise ShadowSidecarError(f"Production DB not found: {target}")

        conn = sqlite3.connect(uri, uri=True)
        try:
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    # ---- Shadow store write access ----

    def shadow_write(self, sql: str, params: tuple = ()) -> int:
        """Execute an INSERT/UPDATE/DELETE on the shadow store.

        Refuses to write if the SQL statement appears to target the production
        signals.db by file path. This is a conservative substring check, not a
        bulletproof parser, but combined with the read-mode contract above it
        makes accidental cross-contamination very hard.
        """
        if self._shadow_conn is None:
            raise ShadowSidecarError("ShadowSidecar.initialize() must be called first")

        prod_path_str = str(self.config.production_db)
        if prod_path_str in sql:
            raise UnsafeWriteError(
                f"Shadow write SQL references production DB path {prod_path_str!r}; "
                "the shadow sidecar must never write to production state."
            )

        cursor = self._shadow_conn.execute(sql, params)
        self._shadow_conn.commit()
        return cursor.lastrowid or 0

    def shadow_query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Run a SELECT on the shadow store. Always returns sqlite3.Row objects."""
        if self._shadow_conn is None:
            raise ShadowSidecarError("ShadowSidecar.initialize() must be called first")
        self._shadow_conn.row_factory = sqlite3.Row
        cursor = self._shadow_conn.execute(sql, params)
        return cursor.fetchall()

    def begin_run(self, collector: str, run_id: str, notes: str = "") -> int:
        """Record a shadow collector run start. Returns the row id."""
        return self.shadow_write(
            """
            INSERT INTO shadow_runs (run_id, started_at, collector, notes)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, _utcnow_iso(), collector, notes),
        )

    def end_run(self, run_id: str, items_collected: int) -> None:
        """Mark a shadow collector run as completed."""
        self.shadow_write(
            """
            UPDATE shadow_runs
               SET completed_at = ?, items_collected = ?
             WHERE run_id = ?
            """,
            (_utcnow_iso(), items_collected, run_id),
        )

    # ---- Internal helpers ----

    def _take_snapshot(self) -> None:
        """Copy signals.db to the snapshot location.

        Uses sqlite3 backup API rather than file copy so the snapshot is
        consistent even if the live DB is being written to.
        """
        if not self.config.production_db.exists():
            raise ShadowSidecarError(
                f"Cannot snapshot: production DB not found at {self.config.production_db}"
            )

        self.config.snapshot_db.parent.mkdir(parents=True, exist_ok=True)

        # Use SQLite backup API for a consistent snapshot
        src_uri = f"file:{self.config.production_db.as_posix()}?mode=ro&immutable=1"
        src = sqlite3.connect(src_uri, uri=True)
        try:
            dst = sqlite3.connect(str(self.config.snapshot_db))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()

        logger.info(
            "Snapshotted %s -> %s",
            self.config.production_db,
            self.config.snapshot_db,
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DEFAULT_PRODUCTION_DB",
    "DEFAULT_SHADOW_DB",
    "DEFAULT_SHADOW_ROOT",
    "DEFAULT_SNAPSHOT_DB",
    "ReadMode",
    "ShadowSidecar",
    "ShadowSidecarConfig",
    "ShadowSidecarError",
    "UnsafeWriteError",
]
