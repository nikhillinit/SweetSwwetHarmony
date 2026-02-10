"""Tests for v39 Active Hunter migration DDL."""

import json
import sqlite3

import pytest

from storage.migrations.v39_active_hunter import V39_ACTIVE_HUNTER_DDL


@pytest.fixture
def db():
    """In-memory SQLite DB with v39 DDL applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    # Create prerequisite tables referenced by FKs
    conn.execute(
        "CREATE TABLE run_history (id TEXT PRIMARY KEY, run_type TEXT, status TEXT, created_at TEXT)"
    )
    conn.executescript(V39_ACTIVE_HUNTER_DDL)
    yield conn
    conn.close()


class TestHunterQueriesTable:
    def test_create_and_read(self, db):
        db.execute(
            """INSERT INTO hunter_queries
               (run_id, collector, query_text, query_type, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("run1", "github", "health food startup", "pattern", "pending", "2026-01-01T00:00:00Z"),
        )
        row = db.execute("SELECT * FROM hunter_queries WHERE run_id='run1'").fetchone()
        assert row is not None

    def test_check_rejects_bad_status(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_queries
                   (run_id, collector, query_text, status, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("run1", "github", "test", "invalid_status", "2026-01-01T00:00:00Z"),
            )

    def test_check_rejects_bad_query_type(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_queries
                   (run_id, collector, query_text, query_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("run1", "github", "test", "invalid_type", "2026-01-01T00:00:00Z"),
            )

    def test_unique_run_hash(self, db):
        db.execute(
            """INSERT INTO hunter_queries
               (run_id, collector, query_text, inputs_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("run1", "github", "q1", "hash1", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_queries
                   (run_id, collector, query_text, inputs_hash, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("run1", "github", "q2", "hash1", "2026-01-01T00:00:00Z"),
            )

    def test_json_valid_rejects_bad_metadata(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_queries
                   (run_id, collector, query_text, created_at, metadata)
                   VALUES (?, ?, ?, ?, ?)""",
                ("run1", "github", "test", "2026-01-01T00:00:00Z", "not-json"),
            )


class TestHunterResultsTable:
    def test_create_and_read(self, db):
        db.execute(
            """INSERT INTO hunter_queries
               (id, run_id, collector, query_text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (1, "run1", "github", "test", "2026-01-01T00:00:00Z"),
        )
        db.execute(
            """INSERT INTO hunter_results
               (run_id, query_id, result_dedupe_key, company_name, source_api,
                raw_data, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("run1", 1, "dk1", "Acme", "github", '{"url":"test"}', "pending",
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        row = db.execute("SELECT * FROM hunter_results").fetchone()
        assert row is not None

    def test_check_rejects_bad_status(self, db):
        db.execute(
            """INSERT INTO hunter_queries
               (id, run_id, collector, query_text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (1, "run1", "github", "test", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_results
                   (run_id, query_id, result_dedupe_key, company_name, source_api,
                    raw_data, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("run1", 1, "dk1", "Acme", "github", '{}', "bad_status",
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )

    def test_unique_dedupe_key(self, db):
        db.execute(
            """INSERT INTO hunter_queries
               (id, run_id, collector, query_text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (1, "run1", "github", "test", "2026-01-01T00:00:00Z"),
        )
        db.execute(
            """INSERT INTO hunter_results
               (run_id, query_id, result_dedupe_key, company_name, source_api,
                raw_data, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("run1", 1, "dk1", "Acme", "github", '{}',
             "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_results
                   (run_id, query_id, result_dedupe_key, company_name, source_api,
                    raw_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("run1", 1, "dk1", "Another", "github", '{}',
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )

    def test_raw_data_must_be_valid_json(self, db):
        db.execute(
            """INSERT INTO hunter_queries
               (id, run_id, collector, query_text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (1, "run1", "github", "test", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_results
                   (run_id, query_id, result_dedupe_key, company_name, source_api,
                    raw_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("run1", 1, "dk2", "Acme", "github", "not-json",
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )


class TestHunterBudgetTable:
    def test_create_and_unique_constraint(self, db):
        db.execute(
            """INSERT INTO hunter_budget
               (budget_date, collector, queries_cap, cost_cap, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("2026-01-01", "github", 50, 100.0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_budget
                   (budget_date, collector, queries_cap, cost_cap, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("2026-01-01", "github", 50, 100.0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )


class TestHunterBudgetTransactionsTable:
    def test_create_and_read(self, db):
        db.execute(
            """INSERT INTO hunter_budget_transactions
               (budget_date, collector, run_id, delta_queries, delta_cost, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("2026-01-01", "github", "run1", 1, 1.0, "reserve", "2026-01-01T00:00:00Z"),
        )
        row = db.execute("SELECT * FROM hunter_budget_transactions").fetchone()
        assert row is not None

    def test_check_rejects_bad_reason(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_budget_transactions
                   (budget_date, collector, delta_queries, delta_cost, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("2026-01-01", "github", 1, 1.0, "bad_reason", "2026-01-01T00:00:00Z"),
            )


class TestHunterNegativeKeywordsTable:
    def test_create_and_unique_constraint(self, db):
        db.execute(
            """INSERT INTO hunter_negative_keywords
               (keyword, collector, category, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("blockchain", "github", "crypto", "manual", "2026-01-01T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_negative_keywords
                   (keyword, collector, category, source, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("blockchain", "github", "crypto", "manual", "2026-01-01T00:00:00Z"),
            )

    def test_check_rejects_bad_source(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_negative_keywords
                   (keyword, source, created_at)
                   VALUES (?, ?, ?)""",
                ("test", "bad_source", "2026-01-01T00:00:00Z"),
            )

    def test_json_valid_metadata(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO hunter_negative_keywords
                   (keyword, source, created_at, metadata)
                   VALUES (?, ?, ?, ?)""",
                ("test", "manual", "2026-01-01T00:00:00Z", "not-json"),
            )


class TestIndexesExist:
    def test_all_indexes_created(self, db):
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_h%'"
        ).fetchall()
        index_names = {r[0] for r in rows}
        expected = {
            "idx_hq_run", "idx_hq_collector_status", "idx_hq_created", "idx_hq_run_hash",
            "idx_hr_run_status", "idx_hr_canonical_status", "idx_hr_review", "idx_hr_created",
            "idx_hbt_date_collector", "idx_hbt_run",
            "idx_hnk_active", "idx_hnk_source",
        }
        assert expected.issubset(index_names), f"Missing: {expected - index_names}"


class TestDowngrade:
    def test_drop_all_five_tables(self, db):
        tables_before = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hunter_%'"
            ).fetchall()
        }
        assert len(tables_before) == 5

        db.execute("DROP TABLE IF EXISTS hunter_negative_keywords")
        db.execute("DROP TABLE IF EXISTS hunter_budget_transactions")
        db.execute("DROP TABLE IF EXISTS hunter_budget")
        db.execute("DROP TABLE IF EXISTS hunter_results")
        db.execute("DROP TABLE IF EXISTS hunter_queries")

        tables_after = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hunter_%'"
            ).fetchall()
        }
        assert len(tables_after) == 0
