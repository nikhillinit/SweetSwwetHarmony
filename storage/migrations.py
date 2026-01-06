"""
Migration utilities for SignalStore.

Provides tools for managing schema migrations, backing up databases,
and recovering from migration failures.

Usage:
    # List applied migrations
    python storage/migrations.py list

    # Export data (before migration)
    python storage/migrations.py export signals.db backup.json

    # Import data (after migration)
    python storage/migrations.py import backup.json signals.db

    # Validate schema
    python storage/migrations.py validate signals.db
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from signal_store import SignalStore, CURRENT_SCHEMA_VERSION


async def list_migrations(db_path: str) -> None:
    """List all applied migrations."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        cursor = await store._db.execute(
            """
            SELECT version, applied_at, description
            FROM schema_migrations
            ORDER BY version
            """
        )
        rows = await cursor.fetchall()

        print(f"\nApplied migrations for {db_path}:")
        print("-" * 60)

        if not rows:
            print("  (no migrations applied)")
        else:
            for version, applied_at, description in rows:
                print(f"  v{version}: {description}")
                print(f"    Applied: {applied_at}")

        print(f"\nCurrent schema version: {CURRENT_SCHEMA_VERSION}")

    finally:
        await store.close()


async def export_data(db_path: str, output_path: str) -> None:
    """Export all data from database to JSON file."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        export_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": CURRENT_SCHEMA_VERSION,
            "signals": [],
            "processing": [],
            "suppression_cache": [],
        }

        # Export signals
        cursor = await store._db.execute(
            """
            SELECT
                id, signal_type, source_api, canonical_key,
                company_name, confidence, raw_data,
                detected_at, created_at
            FROM signals
            """
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

        # Export processing
        cursor = await store._db.execute(
            """
            SELECT
                signal_id, status, notion_page_id,
                processed_at, error_message, metadata
            FROM signal_processing
            """
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

        # Export suppression cache
        cursor = await store._db.execute(
            """
            SELECT
                canonical_key, notion_page_id, status,
                company_name, cached_at, expires_at, metadata
            FROM suppression_cache
            """
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

        # Write to file
        with open(output_path, "w") as f:
            json.dump(export_data, f, indent=2)

        print(f"\nExported data to {output_path}:")
        print(f"  Signals: {len(export_data['signals'])}")
        print(f"  Processing records: {len(export_data['processing'])}")
        print(f"  Suppression entries: {len(export_data['suppression_cache'])}")

    finally:
        await store.close()


async def import_data(input_path: str, db_path: str) -> None:
    """Import data from JSON file into database."""
    # Load export file
    with open(input_path, "r") as f:
        import_data = json.load(f)

    print(f"\nImporting data from {input_path} to {db_path}...")
    print(f"  Export schema version: {import_data.get('schema_version')}")
    print(f"  Current schema version: {CURRENT_SCHEMA_VERSION}")

    store = SignalStore(db_path)
    await store.initialize()

    try:
        # Import signals
        print(f"\nImporting {len(import_data['signals'])} signals...")
        signal_id_map = {}  # Old ID -> New ID

        for signal in import_data["signals"]:
            old_id = signal["id"]

            # Insert signal
            cursor = await store._db.execute(
                """
                INSERT INTO signals (
                    signal_type, source_api, canonical_key, company_name,
                    confidence, raw_data, detected_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal["signal_type"],
                    signal["source_api"],
                    signal["canonical_key"],
                    signal["company_name"],
                    signal["confidence"],
                    signal["raw_data"],
                    signal["detected_at"],
                    signal["created_at"],
                )
            )

            new_id = cursor.lastrowid
            signal_id_map[old_id] = new_id

        await store._db.commit()

        # Import processing records
        print(f"Importing {len(import_data['processing'])} processing records...")

        for proc in import_data["processing"]:
            old_signal_id = proc["signal_id"]
            new_signal_id = signal_id_map.get(old_signal_id)

            if new_signal_id:
                await store._db.execute(
                    """
                    INSERT INTO signal_processing (
                        signal_id, status, notion_page_id,
                        processed_at, error_message, metadata,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_signal_id,
                        proc["status"],
                        proc["notion_page_id"],
                        proc["processed_at"],
                        proc["error_message"],
                        proc["metadata"],
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    )
                )

        await store._db.commit()

        # Import suppression cache
        print(f"Importing {len(import_data['suppression_cache'])} suppression entries...")

        for entry in import_data["suppression_cache"]:
            await store._db.execute(
                """
                INSERT INTO suppression_cache (
                    canonical_key, notion_page_id, status,
                    company_name, cached_at, expires_at, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_key) DO UPDATE SET
                    notion_page_id = excluded.notion_page_id,
                    status = excluded.status,
                    company_name = excluded.company_name,
                    cached_at = excluded.cached_at,
                    expires_at = excluded.expires_at,
                    metadata = excluded.metadata
                """,
                (
                    entry["canonical_key"],
                    entry["notion_page_id"],
                    entry["status"],
                    entry["company_name"],
                    entry["cached_at"],
                    entry["expires_at"],
                    entry["metadata"],
                )
            )

        await store._db.commit()

        print("\nImport complete!")

    finally:
        await store.close()


async def validate_schema(db_path: str) -> None:
    """Validate database schema is correct."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        errors = []

        # Check tables exist
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        expected_tables = {
            "signals",
            "signal_processing",
            "suppression_cache",
            "schema_migrations",
        }

        missing_tables = expected_tables - tables
        if missing_tables:
            errors.append(f"Missing tables: {missing_tables}")

        # Check signals table columns
        cursor = await store._db.execute("PRAGMA table_info(signals)")
        signal_columns = {row[1] for row in await cursor.fetchall()}

        expected_signal_columns = {
            "id", "signal_type", "source_api", "canonical_key",
            "company_name", "confidence", "raw_data",
            "detected_at", "created_at"
        }

        missing_columns = expected_signal_columns - signal_columns
        if missing_columns:
            errors.append(f"Missing columns in signals table: {missing_columns}")

        # Check indexes
        cursor = await store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row[0] for row in await cursor.fetchall()}

        expected_indexes = {
            "idx_signals_canonical_key",
            "idx_signals_signal_type",
            "idx_processing_signal_id",
            "idx_processing_status",
            "idx_suppression_canonical_key",
        }

        # Filter out auto-indexes (sqlite_autoindex_*)
        actual_indexes = {idx for idx in indexes if not idx.startswith("sqlite_autoindex_")}
        missing_indexes = expected_indexes - actual_indexes
        if missing_indexes:
            errors.append(f"Missing indexes: {missing_indexes}")

        # Report results
        print(f"\nSchema validation for {db_path}:")
        print("-" * 60)

        if errors:
            print("FAILED - Errors found:")
            for error in errors:
                print(f"  ERROR: {error}")
            sys.exit(1)
        else:
            print("  OK: All tables present")
            print("  OK: All columns present")
            print("  OK: All indexes present")
            print("\nSchema is valid!")

    finally:
        await store.close()


async def get_info(db_path: str) -> None:
    """Get comprehensive database information."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        stats = await store.get_stats()

        print(f"\nDatabase information for {db_path}:")
        print("=" * 60)

        print("\nSignals:")
        print(f"  Total: {stats['total_signals']}")
        for signal_type, count in stats['signals_by_type'].items():
            print(f"    {signal_type}: {count}")

        print("\nProcessing Status:")
        for status, count in stats['processing_status'].items():
            print(f"  {status}: {count}")

        print(f"\nSuppression Cache:")
        print(f"  Active entries: {stats['active_suppression_entries']}")

        # Get schema version
        cursor = await store._db.execute(
            "SELECT MAX(version) FROM schema_migrations"
        )
        version = (await cursor.fetchone())[0] or 0
        print(f"\nSchema version: {version}")

        # Get database size
        db_size = Path(db_path).stat().st_size
        print(f"Database size: {db_size:,} bytes ({db_size / 1024 / 1024:.2f} MB)")

    finally:
        await store.close()


def print_usage():
    """Print usage information."""
    print("""
Usage: python migrations.py <command> [args]

Commands:
    list <db_path>
        List all applied migrations

    export <db_path> <output.json>
        Export database to JSON file

    import <input.json> <db_path>
        Import JSON file into database

    validate <db_path>
        Validate database schema

    info <db_path>
        Show database statistics

Examples:
    python migrations.py list signals.db
    python migrations.py export signals.db backup.json
    python migrations.py import backup.json signals_new.db
    python migrations.py validate signals.db
    python migrations.py info signals.db
""")


async def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1]

    if command == "list":
        if len(sys.argv) < 3:
            print("Error: Missing db_path argument")
            print_usage()
            sys.exit(1)
        await list_migrations(sys.argv[2])

    elif command == "export":
        if len(sys.argv) < 4:
            print("Error: Missing arguments")
            print_usage()
            sys.exit(1)
        await export_data(sys.argv[2], sys.argv[3])

    elif command == "import":
        if len(sys.argv) < 4:
            print("Error: Missing arguments")
            print_usage()
            sys.exit(1)
        await import_data(sys.argv[2], sys.argv[3])

    elif command == "validate":
        if len(sys.argv) < 3:
            print("Error: Missing db_path argument")
            print_usage()
            sys.exit(1)
        await validate_schema(sys.argv[2])

    elif command == "info":
        if len(sys.argv) < 3:
            print("Error: Missing db_path argument")
            print_usage()
            sys.exit(1)
        await get_info(sys.argv[2])

    else:
        print(f"Error: Unknown command '{command}'")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
