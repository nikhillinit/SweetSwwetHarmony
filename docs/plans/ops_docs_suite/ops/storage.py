import sqlite3
import logging
import re
import threading
from contextlib import contextmanager
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

MIGRATION_0 = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    company_id INTEGER,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    action TEXT CHECK(action IN ('approve', 'reject', 'defer', 'bookmark')) NOT NULL,
    rejection_reason TEXT,
    rejection_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_signals_company ON signals(company_id);
CREATE INDEX IF NOT EXISTS idx_user_actions_signal ON user_actions(signal_id);
CREATE INDEX IF NOT EXISTS idx_user_actions_created ON user_actions(created_at DESC);
"""

MIGRATION_1 = """
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
"""

MIGRATION_2 = """
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
    status TEXT NOT NULL,
    latency_ms REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_timestamp ON system_health(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_health_component ON system_health(component, timestamp DESC);
"""

MIGRATION_3 = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts USING fts5(
    content,
    type UNINDEXED,
    confidence UNINDEXED,
    content='memory_facts',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memory_facts_ai AFTER INSERT ON memory_facts BEGIN
    INSERT INTO memory_facts_fts(rowid, content, type, confidence)
    VALUES (new.id, new.content, new.type, new.confidence);
END;

CREATE TRIGGER IF NOT EXISTS memory_facts_ad AFTER DELETE ON memory_facts BEGIN
    INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
    VALUES('delete', old.id, old.content, old.type, old.confidence);
END;

CREATE TRIGGER IF NOT EXISTS memory_facts_au AFTER UPDATE ON memory_facts BEGIN
    INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
    VALUES('delete', old.id, old.content, old.type, old.confidence);

    INSERT INTO memory_facts_fts(rowid, content, type, confidence)
    VALUES (new.id, new.content, new.type, new.confidence);
END;

INSERT INTO memory_facts_fts(memory_facts_fts) VALUES('rebuild');

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
"""

MIGRATION_4 = """
DROP TRIGGER IF EXISTS memory_facts_au;
DROP TRIGGER IF EXISTS memory_facts_ad;
DROP TRIGGER IF EXISTS memory_facts_ai;

CREATE TRIGGER memory_facts_ai AFTER INSERT ON memory_facts BEGIN
    INSERT INTO memory_facts_fts(rowid, content, type, confidence)
    VALUES (new.id, new.content, new.type, new.confidence);
END;

CREATE TRIGGER memory_facts_ad AFTER DELETE ON memory_facts BEGIN
    INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
    VALUES('delete', old.id, old.content, old.type, old.confidence);
END;

CREATE TRIGGER memory_facts_au AFTER UPDATE ON memory_facts BEGIN
    INSERT INTO memory_facts_fts(memory_facts_fts, rowid, content, type, confidence)
    VALUES('delete', old.id, old.content, old.type, old.confidence);

    INSERT INTO memory_facts_fts(rowid, content, type, confidence)
    VALUES (new.id, new.content, new.type, new.confidence);
END;
"""

MIGRATION_5 = ""

MIGRATION_6 = """
CREATE INDEX IF NOT EXISTS idx_citations_fact_signal ON fact_citations(fact_id, signal_id, cited_at DESC);
"""

MIGRATIONS = [
    (0, MIGRATION_0),
    (1, MIGRATION_1),
    (2, MIGRATION_2),
    (3, MIGRATION_3),
    (4, MIGRATION_4),
    (5, MIGRATION_5),
    (6, MIGRATION_6),
]


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
                conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            # FIX: Only rollback if we're actually in a transaction to avoid
            # "no transaction is active" errors after explicit commits
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
    def __init__(self, db_path: str = "signals.db"):
        self.db_path = db_path
        self.pool = ConnectionPool(db_path)
        self._init_db()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _ensure_health_indexes(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_health_timestamp ON system_health(timestamp DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_health_component ON system_health(component, timestamp DESC);")

    def _apply_migration_5(self, conn: sqlite3.Connection) -> None:
        has_old = self._table_exists(conn, "system_health")
        has_new = self._table_exists(conn, "system_health_new")

        if (not has_old) and has_new:
            conn.execute("ALTER TABLE system_health_new RENAME TO system_health;")
            self._ensure_health_indexes(conn)
            return

        if has_old and has_new:
            conn.execute("DROP TABLE system_health_new;")
            has_new = False

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_health_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                component TEXT NOT NULL,
                status TEXT CHECK(status IN ('healthy', 'degraded', 'unhealthy')) NOT NULL,
                latency_ms REAL,
                error TEXT
            );
            """
        )

        if has_old:
            conn.execute(
                """
                INSERT INTO system_health_new (id, timestamp, component, status, latency_ms, error)
                SELECT
                    id,
                    timestamp,
                    component,
                    CASE
                        WHEN status IN ('healthy', 'degraded', 'unhealthy') THEN status
                        ELSE 'degraded'
                    END AS status,
                    latency_ms,
                    error
                FROM system_health;
                """
            )
            conn.execute("DROP TABLE system_health;")

        conn.execute("ALTER TABLE system_health_new RENAME TO system_health;")
        self._ensure_health_indexes(conn)

    def _init_db(self):
        with self.pool.get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            cursor = conn.execute("SELECT MAX(version) FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row[0] is not None else -1

            for version, migration_sql in MIGRATIONS:
                if version > current_version:
                    logger.info(f"Applying migration {version}...")
                    try:
                        if version == 5:
                            self._apply_migration_5(conn)
                        else:
                            conn.executescript(migration_sql)

                        conn.execute(
                            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                            (version,),
                        )
                    except sqlite3.OperationalError as e:
                        msg = str(e).lower()
                        if "fts5" in msg or "no such module" in msg:
                            raise RuntimeError(
                                "SQLite FTS5 support is required but not available in this environment. "
                                "Install/upgrade SQLite with FTS5 enabled, or use a Python build linked "
                                "against an FTS5-enabled SQLite."
                            ) from e
                        raise

                    logger.info(f"Migration {version} applied successfully")

            logger.info("Database initialization complete")

    @contextmanager
    def transaction(self):
        """
        Explicit transactional context manager.
        
        Provides atomic transactions with automatic commit on success 
        and rollback on exception. Uses BEGIN IMMEDIATE to acquire 
        locks early and prevent deadlocks.
        """
        with self.pool.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
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

    def get_health_summary(self, hours: int = 24) -> dict:
        with self.transaction() as conn:
            conn.row_factory = sqlite3.Row
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