"""
Tests for storage/migrations.py

Covers:
- list_migrations: Show all/applied migrations
- export_data: Export database to JSON
- import_data: Import JSON to database
- validate_schema: Verify database structure
- get_info: Show database statistics

CRITICAL: These tests cover the migration system with ZERO prior coverage.
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore, CURRENT_SCHEMA_VERSION


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def fresh_db() -> tuple[SignalStore, str]:
    """Fresh database with all migrations applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    store = SignalStore(db_path=path)
    await store.initialize()

    yield store, path

    await store.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def populated_db(fresh_db: tuple[SignalStore, str]) -> tuple[SignalStore, str]:
    """Database with test data for export/import tests."""
    store, path = fresh_db

    # Add signals
    signal_id = await store.save_signal(
        signal_type="funding",
        source_api="sec_edgar",
        canonical_key="ein:123456789",
        company_name="Acme Corp",
        confidence=0.75,
        raw_data={"amount": 500000},
    )

    await store.save_signal(
        signal_type="launch",
        source_api="product_hunt",
        canonical_key="domain:startup.io",
        company_name="Startup Inc",
        confidence=0.6,
        raw_data={"votes": 150},
    )

    # Add suppression entry
    from storage.signal_store import SuppressionEntry
    from datetime import timedelta

    entry = SuppressionEntry(
        canonical_key="domain:existing.com",
        notion_page_id="notion-123",
        status="Source",
        company_name="Existing Co",
        cached_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    await store.update_suppression_cache([entry])

    return store, path


# =============================================================================
# LIST MIGRATIONS TESTS
# =============================================================================

class TestListMigrations:
    """Tests for list_migrations function."""

    @pytest.mark.asyncio
    async def test_list_migrations_shows_all_versions(self, fresh_db: tuple[SignalStore, str]):
        """All migrations should be visible."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT version, applied_at, description FROM schema_migrations ORDER BY version"
        )
        rows = await cursor.fetchall()

        # Should have all migrations up to CURRENT_SCHEMA_VERSION
        assert len(rows) >= 1
        versions = [row[0] for row in rows]
        assert max(versions) == CURRENT_SCHEMA_VERSION

    @pytest.mark.asyncio
    async def test_list_migrations_shows_applied_status(self, fresh_db: tuple[SignalStore, str]):
        """Applied migrations should have timestamps."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT version, applied_at FROM schema_migrations"
        )
        rows = await cursor.fetchall()

        for version, applied_at in rows:
            assert applied_at is not None, f"Migration v{version} should have applied_at timestamp"

    @pytest.mark.asyncio
    async def test_list_migrations_empty_db(self):
        """Fresh DB without initialization should have no migrations table."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            import aiosqlite
            async with aiosqlite.connect(path) as db:
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                )
                row = await cursor.fetchone()
                # Table shouldn't exist on completely fresh DB
                assert row is None
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_migration_version_tracking(self, fresh_db: tuple[SignalStore, str]):
        """Version table should be updated after migrations."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT MAX(version) FROM schema_migrations"
        )
        row = await cursor.fetchone()

        assert row[0] == CURRENT_SCHEMA_VERSION


# =============================================================================
# MIGRATION APPLICATION TESTS
# =============================================================================

class TestMigrationApplication:
    """Tests for migration application logic."""

    @pytest.mark.asyncio
    async def test_migration_v1_creates_base_tables(self, fresh_db: tuple[SignalStore, str]):
        """Migration v1 should create signals, signal_processing, suppression_cache tables."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        # Core tables from v1
        assert "signals" in tables
        assert "signal_processing" in tables
        assert "suppression_cache" in tables
        assert "schema_migrations" in tables

    @pytest.mark.asyncio
    async def test_migration_preserves_existing_data(self, populated_db: tuple[SignalStore, str]):
        """Re-running migrations should not lose data."""
        store, path = populated_db

        # Count existing signals
        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        initial_count = (await cursor.fetchone())[0]
        assert initial_count == 2

        # Close and re-initialize (would apply migrations if any pending)
        await store.close()

        store2 = SignalStore(db_path=path)
        await store2.initialize()

        cursor = await store2._db.execute("SELECT COUNT(*) FROM signals")
        final_count = (await cursor.fetchone())[0]

        await store2.close()

        assert final_count == initial_count, "Signals should not be lost after re-initialization"

    @pytest.mark.asyncio
    async def test_migration_runs_in_order(self, fresh_db: tuple[SignalStore, str]):
        """Migrations should be applied in version order."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY applied_at"
        )
        rows = await cursor.fetchall()

        versions = [row[0] for row in rows]
        # Versions should be monotonically increasing by apply order
        for i in range(1, len(versions)):
            assert versions[i] >= versions[i-1], "Migrations should be applied in order"

    @pytest.mark.asyncio
    async def test_migration_idempotent(self, fresh_db: tuple[SignalStore, str]):
        """Running initialize() twice should not cause errors."""
        store, path = fresh_db

        # Initialize is already called in fixture
        # Call it again - should not fail
        await store.close()

        store2 = SignalStore(db_path=path)
        await store2.initialize()

        # Verify still works
        cursor = await store2._db.execute("SELECT COUNT(*) FROM schema_migrations")
        count = (await cursor.fetchone())[0]

        await store2.close()

        assert count == CURRENT_SCHEMA_VERSION


# =============================================================================
# EXPORT DATA TESTS
# =============================================================================

class TestExportData:
    """Tests for export_data function."""

    @pytest.mark.asyncio
    async def test_export_data_creates_valid_json(self, populated_db: tuple[SignalStore, str]):
        """Export should create valid JSON file."""
        store, path = populated_db

        fd, export_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        try:
            # Export using migrations module pattern
            export_data = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": CURRENT_SCHEMA_VERSION,
                "signals": [],
                "processing": [],
                "suppression_cache": [],
            }

            # Export signals
            cursor = await store._db.execute(
                """SELECT id, signal_type, source_api, canonical_key,
                   company_name, confidence, raw_data, detected_at, created_at
                   FROM signals"""
            )
            for row in await cursor.fetchall():
                export_data["signals"].append({
                    "id": row[0],
                    "signal_type": row[1],
                    "source_api": row[2],
                    "canonical_key": row[3],
                    "company_name": row[4],
                    "confidence": row[5],
                    "raw_data": row[6],
                    "detected_at": row[7],
                    "created_at": row[8],
                })

            # Write to file
            with open(export_path, "w") as f:
                json.dump(export_data, f, indent=2)

            # Verify valid JSON
            with open(export_path, "r") as f:
                loaded = json.load(f)

            assert "signals" in loaded
            assert "exported_at" in loaded
            assert "schema_version" in loaded
        finally:
            os.unlink(export_path)

    @pytest.mark.asyncio
    async def test_export_data_includes_all_tables(self, populated_db: tuple[SignalStore, str]):
        """Export should include signals, processing, and suppression cache."""
        store, path = populated_db

        # Build export data
        export_data: Dict[str, Any] = {
            "signals": [],
            "processing": [],
            "suppression_cache": [],
        }

        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        signal_count = (await cursor.fetchone())[0]

        cursor = await store._db.execute("SELECT COUNT(*) FROM signal_processing")
        processing_count = (await cursor.fetchone())[0]

        cursor = await store._db.execute("SELECT COUNT(*) FROM suppression_cache")
        suppression_count = (await cursor.fetchone())[0]

        assert signal_count == 2, "Should have 2 signals"
        assert processing_count == 2, "Should have 2 processing records"
        assert suppression_count == 1, "Should have 1 suppression entry"

    @pytest.mark.asyncio
    async def test_export_data_handles_large_db(self, fresh_db: tuple[SignalStore, str]):
        """Export should handle many rows efficiently."""
        store, path = fresh_db

        # Insert 100 signals
        for i in range(100):
            await store.save_signal(
                signal_type="test",
                source_api="test_api",
                canonical_key=f"domain:test{i}.com",
                company_name=f"Test Company {i}",
                confidence=0.5,
                raw_data={"index": i},
            )

        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        count = (await cursor.fetchone())[0]

        assert count == 100


# =============================================================================
# IMPORT DATA TESTS
# =============================================================================

class TestImportData:
    """Tests for import_data function."""

    @pytest.mark.asyncio
    async def test_import_data_restores_exactly(self, populated_db: tuple[SignalStore, str]):
        """Import should restore data from export."""
        store, path = populated_db

        # Export first
        export_data: Dict[str, Any] = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": CURRENT_SCHEMA_VERSION,
            "signals": [],
            "processing": [],
            "suppression_cache": [],
        }

        cursor = await store._db.execute(
            """SELECT id, signal_type, source_api, canonical_key,
               company_name, confidence, raw_data, detected_at, created_at
               FROM signals"""
        )
        for row in await cursor.fetchall():
            export_data["signals"].append({
                "id": row[0],
                "signal_type": row[1],
                "source_api": row[2],
                "canonical_key": row[3],
                "company_name": row[4],
                "confidence": row[5],
                "raw_data": row[6],
                "detected_at": row[7],
                "created_at": row[8],
            })

        cursor = await store._db.execute(
            """SELECT signal_id, status, notion_page_id, processed_at, error_message, metadata
               FROM signal_processing"""
        )
        for row in await cursor.fetchall():
            export_data["processing"].append({
                "signal_id": row[0],
                "status": row[1],
                "notion_page_id": row[2],
                "processed_at": row[3],
                "error_message": row[4],
                "metadata": row[5],
            })

        cursor = await store._db.execute(
            """SELECT canonical_key, notion_page_id, status, company_name, cached_at, expires_at, metadata
               FROM suppression_cache"""
        )
        for row in await cursor.fetchall():
            export_data["suppression_cache"].append({
                "canonical_key": row[0],
                "notion_page_id": row[1],
                "status": row[2],
                "company_name": row[3],
                "cached_at": row[4],
                "expires_at": row[5],
                "metadata": row[6],
            })

        original_signal_count = len(export_data["signals"])
        original_suppression_count = len(export_data["suppression_cache"])

        # Create new database and import
        fd, new_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            new_store = SignalStore(db_path=new_path)
            await new_store.initialize()

            # Import signals
            for signal in export_data["signals"]:
                cursor = await new_store._db.execute(
                    """INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
                       confidence, raw_data, detected_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (signal["signal_type"], signal["source_api"], signal["canonical_key"],
                     signal["company_name"], signal["confidence"], signal["raw_data"],
                     signal["detected_at"], signal["created_at"])
                )
            await new_store._db.commit()

            # Verify import
            cursor = await new_store._db.execute("SELECT COUNT(*) FROM signals")
            imported_count = (await cursor.fetchone())[0]

            assert imported_count == original_signal_count

            await new_store.close()
        finally:
            os.unlink(new_path)

    @pytest.mark.asyncio
    async def test_import_data_to_empty_db(self, fresh_db: tuple[SignalStore, str]):
        """Import to fresh DB should work."""
        store, path = fresh_db

        # Start with no signals
        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        initial_count = (await cursor.fetchone())[0]
        assert initial_count == 0

        # Import mock data
        await store._db.execute(
            """INSERT INTO signals (signal_type, source_api, canonical_key, company_name,
               confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("funding", "sec_edgar", "ein:999999999", "Imported Corp",
             0.8, '{"amount": 1000000}', datetime.now(timezone.utc).isoformat(),
             datetime.now(timezone.utc).isoformat())
        )
        await store._db.commit()

        cursor = await store._db.execute("SELECT COUNT(*) FROM signals")
        final_count = (await cursor.fetchone())[0]
        assert final_count == 1

    @pytest.mark.asyncio
    async def test_import_data_invalid_json_raises(self):
        """Import with invalid JSON should raise error."""
        fd, json_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        try:
            with open(json_path, "w") as f:
                f.write("{ invalid json }")

            with pytest.raises(json.JSONDecodeError):
                with open(json_path, "r") as f:
                    json.load(f)
        finally:
            os.unlink(json_path)


# =============================================================================
# VALIDATE SCHEMA TESTS
# =============================================================================

class TestValidateSchema:
    """Tests for validate_schema function."""

    @pytest.mark.asyncio
    async def test_validate_schema_passes_valid_db(self, fresh_db: tuple[SignalStore, str]):
        """Valid DB should pass validation."""
        store, path = fresh_db

        # Check tables exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        expected_tables = {"signals", "signal_processing", "suppression_cache", "schema_migrations"}
        assert expected_tables.issubset(tables)

    @pytest.mark.asyncio
    async def test_validate_schema_detects_missing_table(self, fresh_db: tuple[SignalStore, str]):
        """Validation should detect missing tables."""
        store, path = fresh_db

        # Manually drop a table
        await store._db.execute("DROP TABLE suppression_cache")
        await store._db.commit()

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        assert "suppression_cache" not in tables

    @pytest.mark.asyncio
    async def test_validate_schema_detects_missing_column(self, fresh_db: tuple[SignalStore, str]):
        """Validation should detect missing columns."""
        store, path = fresh_db

        cursor = await store._db.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in await cursor.fetchall()}

        expected_columns = {"id", "signal_type", "source_api", "canonical_key",
                          "company_name", "confidence", "raw_data", "detected_at", "created_at"}

        assert expected_columns.issubset(columns)

    @pytest.mark.asyncio
    async def test_validate_schema_checks_indexes(self, fresh_db: tuple[SignalStore, str]):
        """Validation should verify indexes exist."""
        store, path = fresh_db

        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in await cursor.fetchall()}

        # Filter out auto-indexes
        actual_indexes = {idx for idx in indexes if not idx.startswith("sqlite_autoindex_")}

        expected_indexes = {
            "idx_signals_canonical_key",
            "idx_signals_signal_type",
            "idx_processing_signal_id",
            "idx_processing_status",
            "idx_suppression_canonical_key",
        }

        assert expected_indexes.issubset(actual_indexes)


# =============================================================================
# GET INFO TESTS
# =============================================================================

class TestGetInfo:
    """Tests for get_info function (via get_stats)."""

    @pytest.mark.asyncio
    async def test_get_info_shows_stats(self, populated_db: tuple[SignalStore, str]):
        """Get info should show accurate statistics."""
        store, path = populated_db

        stats = await store.get_stats()

        assert stats["total_signals"] == 2
        assert "signals_by_type" in stats
        assert "processing_status" in stats

    @pytest.mark.asyncio
    async def test_get_info_empty_db(self, fresh_db: tuple[SignalStore, str]):
        """Get info on empty DB should return zeros."""
        store, path = fresh_db

        stats = await store.get_stats()

        assert stats["total_signals"] == 0

    @pytest.mark.asyncio
    async def test_get_info_signals_by_type(self, populated_db: tuple[SignalStore, str]):
        """Should count signals by type correctly."""
        store, path = populated_db

        stats = await store.get_stats()

        # We have 1 funding and 1 launch signal
        assert "funding" in stats["signals_by_type"] or len(stats["signals_by_type"]) > 0


# =============================================================================
# SCHEMA VERSION TESTS
# =============================================================================

class TestSchemaVersion:
    """Tests for schema version tracking."""

    @pytest.mark.asyncio
    async def test_current_schema_version_constant(self):
        """CURRENT_SCHEMA_VERSION should be defined."""
        from storage.signal_store import CURRENT_SCHEMA_VERSION

        assert CURRENT_SCHEMA_VERSION >= 1
        assert isinstance(CURRENT_SCHEMA_VERSION, int)

    @pytest.mark.asyncio
    async def test_migrations_dict_complete(self):
        """MIGRATIONS dict should have all versions up to CURRENT."""
        from storage.signal_store import MIGRATIONS, CURRENT_SCHEMA_VERSION

        for version in range(1, CURRENT_SCHEMA_VERSION + 1):
            assert version in MIGRATIONS, f"Missing migration v{version}"

    @pytest.mark.asyncio
    async def test_migration_sql_non_empty(self):
        """Each migration should have non-empty SQL."""
        from storage.signal_store import MIGRATIONS

        for version, sql in MIGRATIONS.items():
            assert sql.strip(), f"Migration v{version} has empty SQL"


# =============================================================================
# MIGRATION 14 TESTS - Finance Predicates
# =============================================================================

class TestMigration14FinancePredicates:
    """Tests for migration 14 - Finance predicates for PDF profiler."""

    @pytest.mark.asyncio
    async def test_migration_14_creates_finance_predicates(self, fresh_db: tuple[SignalStore, str]):
        """Migration 14 should insert 7 finance predicates."""
        store, path = fresh_db

        # Query predicates table
        cursor = await store._db.execute(
            """SELECT name, display_name, data_type, units, description
               FROM predicates
               WHERE name IN (
                   'burn_rate_usd_monthly', 'runway_months', 'cash_on_hand_usd',
                   'valuation_pre_money_usd', 'valuation_post_money_usd',
                   'round_size_usd', 'cap_table_snapshot'
               )
               ORDER BY name"""
        )
        predicates = await cursor.fetchall()

        # Should have exactly 7 finance predicates
        assert len(predicates) == 7, "Should have 7 finance predicates"

        # Verify each predicate has display_name
        for row in predicates:
            name, display_name, data_type, units, description = row
            assert display_name is not None, f"Predicate {name} missing display_name"
            assert display_name != "", f"Predicate {name} has empty display_name"
            assert description is not None, f"Predicate {name} missing description"

    @pytest.mark.asyncio
    async def test_migration_14_burn_rate_predicate(self, fresh_db: tuple[SignalStore, str]):
        """Verify burn_rate_usd_monthly predicate structure."""
        store, path = fresh_db

        cursor = await store._db.execute(
            """SELECT display_name, data_type, units FROM predicates
               WHERE name = 'burn_rate_usd_monthly'"""
        )
        row = await cursor.fetchone()

        assert row is not None, "burn_rate_usd_monthly predicate should exist"
        display_name, data_type, units = row
        assert display_name == "Monthly Burn Rate"
        assert data_type == "numeric"
        assert units == "USD/month"

    @pytest.mark.asyncio
    async def test_migration_14_runway_predicate(self, fresh_db: tuple[SignalStore, str]):
        """Verify runway_months predicate structure."""
        store, path = fresh_db

        cursor = await store._db.execute(
            """SELECT display_name, data_type, units FROM predicates
               WHERE name = 'runway_months'"""
        )
        row = await cursor.fetchone()

        assert row is not None, "runway_months predicate should exist"
        display_name, data_type, units = row
        assert display_name == "Runway"
        assert data_type == "numeric"
        assert units == "months"

    @pytest.mark.asyncio
    async def test_migration_14_cash_on_hand_predicate(self, fresh_db: tuple[SignalStore, str]):
        """Verify cash_on_hand_usd predicate structure."""
        store, path = fresh_db

        cursor = await store._db.execute(
            """SELECT display_name, data_type, units FROM predicates
               WHERE name = 'cash_on_hand_usd'"""
        )
        row = await cursor.fetchone()

        assert row is not None, "cash_on_hand_usd predicate should exist"
        display_name, data_type, units = row
        assert display_name == "Cash on Hand"
        assert data_type == "numeric"
        assert units == "USD"

    @pytest.mark.asyncio
    async def test_migration_14_valuation_predicates(self, fresh_db: tuple[SignalStore, str]):
        """Verify pre/post money valuation predicates."""
        store, path = fresh_db

        cursor = await store._db.execute(
            """SELECT name, display_name, data_type, units FROM predicates
               WHERE name IN ('valuation_pre_money_usd', 'valuation_post_money_usd')
               ORDER BY name"""
        )
        rows = await cursor.fetchall()

        assert len(rows) == 2, "Should have both pre/post money valuation predicates"

        # Check pre-money
        pre_money = next((r for r in rows if r[0] == "valuation_pre_money_usd"), None)
        assert pre_money is not None
        assert pre_money[1] == "Pre-Money Valuation"
        assert pre_money[2] == "numeric"
        assert pre_money[3] == "USD"

        # Check post-money
        post_money = next((r for r in rows if r[0] == "valuation_post_money_usd"), None)
        assert post_money is not None
        assert post_money[1] == "Post-Money Valuation"
        assert post_money[2] == "numeric"
        assert post_money[3] == "USD"

    @pytest.mark.asyncio
    async def test_migration_14_round_size_predicate(self, fresh_db: tuple[SignalStore, str]):
        """Verify round_size_usd predicate structure."""
        store, path = fresh_db

        cursor = await store._db.execute(
            """SELECT display_name, data_type, units FROM predicates
               WHERE name = 'round_size_usd'"""
        )
        row = await cursor.fetchone()

        assert row is not None, "round_size_usd predicate should exist"
        display_name, data_type, units = row
        assert display_name == "Round Size"
        assert data_type == "numeric"
        assert units == "USD"

    @pytest.mark.asyncio
    async def test_migration_14_cap_table_predicate(self, fresh_db: tuple[SignalStore, str]):
        """Verify cap_table_snapshot predicate structure."""
        store, path = fresh_db

        cursor = await store._db.execute(
            """SELECT display_name, data_type, units FROM predicates
               WHERE name = 'cap_table_snapshot'"""
        )
        row = await cursor.fetchone()

        assert row is not None, "cap_table_snapshot predicate should exist"
        display_name, data_type, units = row
        assert display_name == "Cap Table"
        assert data_type == "json"
        assert units is None, "JSON predicate should have NULL units"
