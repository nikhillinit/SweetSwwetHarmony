"""Ops storage layer — adapted for signal_store.py v24 schema.

CRITICAL: This module does NOT create signals/companies tables.
Those are owned by storage/signal_store.py. This module only creates
and manages ops-specific tables (user_actions, memory_facts, etc.)
which are added via v24 migration in signal_store.py.

Uses synchronous sqlite3 (not aiosqlite) for CLI and extraction workflows.
"""

import sqlite3
import logging
import re
import threading
from contextlib import contextmanager
from typing import Optional, List

logger = logging.getLogger(__name__)


class ConnectionPool:
    def __init__(self, db_path: str, max_connections: int = 10):
        self.db_path = db_path
        self.max_connections = max_connections
        self._connections = []
        self._lock = threading.Lock()

    @contextmanager
    def get_connection(self):
        with self._lock:
            if self._connections:
                conn = self._connections.pop()
            else:
                conn = sqlite3.connect(self.db_path, isolation_level=None)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            # Only rollback if we're actually in a transaction
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass  # No transaction active, which is fine
            conn.row_factory = None
            with self._lock:
                if len(self._connections) < self.max_connections:
                    self._connections.append(conn)
                else:
                    conn.close()
        except Exception:
            conn.close()
            raise


class OpsStorage:
    """Ops-layer storage that shares signals.db with SignalStore.

    Expects the v24 migration (from signal_store.py) to have already
    created the ops tables. If tables are missing, creates them as a
    fallback — but NEVER touches signals/companies/signal_processing.
    """

    def __init__(self, db_path: str = "signals.db"):
        self.db_path = db_path
        self.pool = ConnectionPool(db_path)
        self._ensure_ops_tables()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _ensure_ops_tables(self):
        """Verify ops tables exist; create as fallback if v24 migration hasn't run."""
        with self.pool.get_connection() as conn:
            # Check if v24 migration was already applied via signal_store
            v24_applied = False
            if self._table_exists(conn, "schema_migrations"):
                row = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version >= 24"
                ).fetchone()
                v24_applied = row is not None

            if v24_applied and self._table_exists(conn, "memory_facts"):
                logger.info("Ops tables already exist (v24 migration applied)")
                self._ensure_fts_and_triggers(conn)
                return

            # Fallback: create ops-only tables if signal_store hasn't run v24 yet
            logger.info("Creating ops tables (v24 migration not yet applied)")
            self._create_ops_tables_fallback(conn)

    def _create_ops_tables_fallback(self, conn: sqlite3.Connection):
        """Create ops-exclusive tables as fallback. NEVER touches signals/companies."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                action TEXT CHECK(action IN ('approve', 'reject', 'defer', 'bookmark')) NOT NULL,
                rejection_reason TEXT,
                rejection_notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_user_actions_signal ON user_actions(signal_id);
            CREATE INDEX IF NOT EXISTS idx_user_actions_created ON user_actions(created_at DESC);

            CREATE TABLE IF NOT EXISTS memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT CHECK(type IN ('constraint', 'nuance', 'example')) NOT NULL,
                content TEXT NOT NULL,
                confidence REAL CHECK(confidence >= 0 AND confidence <= 1) NOT NULL,
                source_action_id INTEGER,
                source_signal_id INTEGER,
                status TEXT CHECK(status IN ('active', 'pending', 'retired')) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                superseded_by INTEGER,
                used_count INTEGER DEFAULT 0,
                last_used_at TIMESTAMP,
                FOREIGN KEY(source_action_id) REFERENCES user_actions(id) ON DELETE SET NULL,
                FOREIGN KEY(source_signal_id) REFERENCES signals(id) ON DELETE SET NULL,
                FOREIGN KEY(superseded_by) REFERENCES memory_facts(id) ON DELETE SET NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedupe
                ON memory_facts(source_action_id, type, content)
                WHERE source_action_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_memory_active
                ON memory_facts(type) WHERE superseded_by IS NULL AND status = 'active';
            CREATE INDEX IF NOT EXISTS idx_memory_pending
                ON memory_facts(type) WHERE superseded_by IS NULL AND status = 'pending';
            CREATE INDEX IF NOT EXISTS idx_memory_status_created
                ON memory_facts(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_memory_source_signal
                ON memory_facts(source_signal_id);
            CREATE INDEX IF NOT EXISTS idx_memory_used_count
                ON memory_facts(used_count DESC, last_used_at DESC);

            CREATE TABLE IF NOT EXISTS memory_action_state (
                action_id INTEGER PRIMARY KEY,
                status TEXT CHECK(status IN ('processing', 'processed', 'no_facts', 'failed', 'failed_permanent', 'suspicious')) NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(action_id) REFERENCES user_actions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_action_state_status ON memory_action_state(status);
            CREATE INDEX IF NOT EXISTS idx_action_state_attempts ON memory_action_state(attempts);

            CREATE TABLE IF NOT EXISTS extraction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decisions_processed INTEGER NOT NULL,
                facts_created INTEGER NOT NULL,
                llm_failures INTEGER NOT NULL,
                duration_seconds REAL NOT NULL,
                estimated_cost REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                operation TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id INTEGER,
                user TEXT NOT NULL,
                before_state TEXT,
                after_state TEXT,
                reason TEXT
            );

            CREATE TABLE IF NOT EXISTS system_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                component TEXT NOT NULL,
                status TEXT CHECK(status IN ('healthy', 'degraded', 'unhealthy')) NOT NULL,
                latency_ms REAL,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_health_timestamp ON system_health(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_health_component ON system_health(component, timestamp DESC);

            CREATE TABLE IF NOT EXISTS fact_citations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_id INTEGER NOT NULL,
                signal_id INTEGER,
                cited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                context TEXT,
                FOREIGN KEY(fact_id) REFERENCES memory_facts(id) ON DELETE CASCADE,
                FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_citations_fact ON fact_citations(fact_id, cited_at DESC);
            CREATE INDEX IF NOT EXISTS idx_citations_signal ON fact_citations(signal_id);
            CREATE INDEX IF NOT EXISTS idx_citations_fact_signal ON fact_citations(fact_id, signal_id, cited_at DESC);

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

        self._ensure_fts_and_triggers(conn)

    def _ensure_fts_and_triggers(self, conn: sqlite3.Connection):
        """Create FTS5 virtual table and triggers if missing."""
        if not self._table_exists(conn, "memory_facts_fts"):
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts USING fts5(
                        content,
                        type UNINDEXED,
                        confidence UNINDEXED,
                        content='memory_facts',
                        content_rowid='id',
                        tokenize='porter unicode61'
                    )
                """)
            except sqlite3.OperationalError as e:
                if "fts5" in str(e).lower() or "no such module" in str(e).lower():
                    raise RuntimeError(
                        "SQLite FTS5 support is required but not available. "
                        "Use Python 3.11+ from python.org."
                    ) from e
                raise

        # Ensure triggers exist
        for trigger_name, trigger_sql in [
            ("memory_facts_ai", """
                CREATE TRIGGER IF NOT EXISTS memory_facts_ai AFTER INSERT ON memory_facts BEGIN
                    INSERT INTO memory_facts_fts(rowid, content, type, confidence)
                    VALUES (new.id, new.content, new.type, new.confidence);
                END
            """),
            ("memory_facts_ad", """
                CREATE TRIGGER IF NOT EXISTS memory_facts_ad AFTER DELETE ON memory_facts BEGIN
                    INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
                    VALUES('delete', old.id, old.content, old.type, old.confidence);
                END
            """),
            ("memory_facts_au", """
                CREATE TRIGGER IF NOT EXISTS memory_facts_au AFTER UPDATE ON memory_facts BEGIN
                    INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
                    VALUES('delete', old.id, old.content, old.type, old.confidence);
                    INSERT INTO memory_facts_fts(rowid, content, type, confidence)
                    VALUES (new.id, new.content, new.type, new.confidence);
                END
            """),
        ]:
            conn.execute(trigger_sql)

        # Rebuild FTS index
        conn.execute("INSERT INTO memory_facts_fts(memory_facts_fts) VALUES('rebuild')")

    @contextmanager
    def transaction(self):
        """Explicit transactional context manager with BEGIN IMMEDIATE."""
        with self.pool.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    @contextmanager
    def read_transaction(self):
        """Read-only transaction using plain BEGIN (not IMMEDIATE).

        Use this for consistent read snapshots without write-lock contention.
        """
        with self.pool.get_connection() as conn:
            conn.execute("BEGIN")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def search_facts(self, query: str, limit: int = 15, type_filter: Optional[str] = None) -> List[dict]:
        safe_query = self.escape_fts_query(query)
        if not safe_query.strip():
            return []

        with self.transaction() as conn:
            conn.row_factory = sqlite3.Row
            sql = """
                SELECT
                    mf.id, mf.type, mf.content, mf.confidence,
                    mf.source_action_id, mf.source_signal_id,
                    mf.status, mf.created_at, mf.superseded_by,
                    mf.used_count, mf.last_used_at
                FROM memory_facts mf
                JOIN memory_facts_fts fts ON mf.id = fts.rowid
                WHERE fts.memory_facts_fts MATCH ?
                AND mf.status = 'active'
                AND mf.superseded_by IS NULL
            """
            params = [safe_query]

            if type_filter:
                sql += " AND mf.type = ?"
                params.append(type_filter)

            sql += " ORDER BY mf.confidence DESC, mf.created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def escape_fts_query(raw: str) -> str:
        if not raw:
            return ""
        cleaned = re.sub(r'["()|*:?\\]', " ", raw)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def record_fact_usage(self, fact_ids: List[int], signal_id: int, context: str = "") -> None:
        with self.transaction() as conn:
            for fact_id in fact_ids:
                conn.execute(
                    """
                    UPDATE memory_facts
                    SET used_count = COALESCE(used_count, 0) + 1,
                        last_used_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (fact_id,),
                )
                conn.execute(
                    """
                    INSERT INTO fact_citations (fact_id, signal_id, context)
                    VALUES (?, ?, ?)
                    """,
                    (fact_id, signal_id, context),
                )

    def get_health_summary(self, hours: int = 24, conn=None) -> dict:
        if conn is not None:
            return self._health_summary_query(conn, hours)
        with self.transaction() as c:
            return self._health_summary_query(c, hours)

    def _health_summary_query(self, conn, hours: int) -> dict:
        """Execute the health summary query on a given connection."""
        old_factory = conn.row_factory
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                """
                SELECT
                    component,
                    COUNT(*) as total_checks,
                    SUM(CASE WHEN status = 'healthy' THEN 1 ELSE 0 END) as healthy_count,
                    AVG(latency_ms) as avg_latency_ms
                FROM system_health
                WHERE timestamp > datetime('now', ?)
                GROUP BY component
                """,
                (f"-{hours} hours",),
            )
            rows = cursor.fetchall()
            result = {}
            for row in rows:
                component = row["component"]
                total = row["total_checks"]
                healthy = row["healthy_count"]
                health_percent = (healthy / total * 100) if total > 0 else 0
                result[component] = {
                    "health_percent": health_percent,
                    "total_checks": total,
                    "avg_latency_ms": row["avg_latency_ms"],
                }
            return result
        finally:
            conn.row_factory = old_factory

    def log_audit(self, operation: str, target_type: str,
                  target_id: Optional[int] = None, user: str = "system",
                  before_state: Optional[str] = None,
                  after_state: Optional[str] = None,
                  reason: Optional[str] = None, conn=None) -> None:
        """Insert a row into the audit_log table.

        Args:
            conn: Optional connection. If None, opens its own transaction.
        """
        sql = """
            INSERT INTO audit_log
            (operation, target_type, target_id, user, before_state, after_state, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (operation, target_type, target_id, user,
                  before_state, after_state, reason)
        if conn is not None:
            conn.execute(sql, params)
        else:
            with self.transaction() as c:
                c.execute(sql, params)

    def log_health(self, component: str, status: str, latency_ms: float = 0, error: Optional[str] = None) -> None:
        """Log a health check entry."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO system_health (component, status, latency_ms, error)
                VALUES (?, ?, ?, ?)
                """,
                (component, status, latency_ms, error),
            )
