"""
Tests for OpenVC CSV Importer.

TDD: Tests for importing OpenVC CSV exports into the signal store.
"""

import csv
import pytest
from datetime import datetime
from pathlib import Path
from io import StringIO

from importers.openvc_csv import (
    OpenVCImporter,
    OpenVCRecord,
    parse_openvc_csv,
    map_stage_to_confidence,
)


class TestOpenVCRecord:
    """Tests for OpenVCRecord dataclass."""

    def test_record_from_dict(self):
        """Can create record from CSV row dict."""
        row = {
            "Company Name": "Acme Corp",
            "Stage": "Seed",
            "Sector": "Consumer",
            "Geography": "US",
            "Funding Target": "$2M",
            "Website": "https://acme.com",
        }
        record = OpenVCRecord.from_csv_row(row)

        assert record.company_name == "Acme Corp"
        assert record.stage == "seed"  # Normalized to lowercase
        assert record.sector == "Consumer"
        assert record.geography == "US"
        assert record.funding_target == "$2M"
        assert record.website == "https://acme.com"

    def test_record_handles_missing_fields(self):
        """Record handles missing optional fields gracefully."""
        row = {
            "Company Name": "Stealth Startup",
            "Stage": "Pre-Seed",
        }
        record = OpenVCRecord.from_csv_row(row)

        assert record.company_name == "Stealth Startup"
        assert record.stage == "pre-seed"  # Normalized to lowercase
        assert record.sector is None
        assert record.website is None

    def test_record_normalizes_stage(self):
        """Stage names are normalized."""
        variations = [
            ("pre-seed", "pre-seed"),
            ("Pre-Seed", "pre-seed"),
            ("PRE-SEED", "pre-seed"),
            ("Seed", "seed"),
            ("Series A", "series-a"),
            ("series a", "series-a"),
            ("Series B", "series-b"),
        ]
        for input_stage, expected in variations:
            row = {"Company Name": "Test", "Stage": input_stage}
            record = OpenVCRecord.from_csv_row(row)
            assert record.stage == expected, f"Expected {expected} for {input_stage}"

    def test_record_generates_canonical_key_from_domain(self):
        """Canonical key generated from website domain."""
        row = {
            "Company Name": "Acme Corp",
            "Stage": "Seed",
            "Website": "https://acme.com/about",
        }
        record = OpenVCRecord.from_csv_row(row)

        assert record.canonical_key == "domain:acme.com"

    def test_record_generates_canonical_key_fallback(self):
        """Canonical key falls back to name_loc when no website."""
        row = {
            "Company Name": "Stealth Labs",
            "Stage": "Seed",
            "Geography": "San Francisco",
        }
        record = OpenVCRecord.from_csv_row(row)

        assert record.canonical_key == "name_loc:stealth-labs:san-francisco"

    def test_record_generates_canonical_key_name_only(self):
        """Canonical key falls back to name when no website or geography."""
        row = {
            "Company Name": "Mystery Inc",
            "Stage": "Seed",
        }
        record = OpenVCRecord.from_csv_row(row)

        assert record.canonical_key == "name:mystery-inc"


class TestMapStageToConfidence:
    """Tests for stage to confidence mapping."""

    def test_pre_seed_confidence(self):
        """Pre-seed has lower confidence (earlier stage)."""
        assert map_stage_to_confidence("pre-seed") == 0.60

    def test_seed_confidence(self):
        """Seed has medium confidence."""
        assert map_stage_to_confidence("seed") == 0.70

    def test_series_a_confidence(self):
        """Series A has higher confidence (more validated)."""
        assert map_stage_to_confidence("series-a") == 0.80

    def test_series_b_plus_confidence(self):
        """Series B+ has highest confidence but may be out of scope."""
        assert map_stage_to_confidence("series-b") == 0.85
        assert map_stage_to_confidence("series-c") == 0.85

    def test_unknown_stage_default(self):
        """Unknown stage gets default confidence."""
        assert map_stage_to_confidence("unknown") == 0.65
        assert map_stage_to_confidence("") == 0.65


class TestParseOpenVCCSV:
    """Tests for CSV parsing."""

    def test_parse_basic_csv(self):
        """Can parse basic OpenVC CSV format."""
        csv_content = """Company Name,Stage,Sector,Geography,Website
Acme Corp,Seed,Consumer,US,https://acme.com
Beta Inc,Pre-Seed,HealthTech,UK,https://beta.io
"""
        records = list(parse_openvc_csv(StringIO(csv_content)))

        assert len(records) == 2
        assert records[0].company_name == "Acme Corp"
        assert records[0].stage == "seed"
        assert records[1].company_name == "Beta Inc"
        assert records[1].stage == "pre-seed"

    def test_parse_handles_extra_columns(self):
        """Parser ignores extra unknown columns."""
        csv_content = """Company Name,Stage,Extra Column,Another One
Acme Corp,Seed,ignored,also ignored
"""
        records = list(parse_openvc_csv(StringIO(csv_content)))

        assert len(records) == 1
        assert records[0].company_name == "Acme Corp"

    def test_parse_handles_empty_rows(self):
        """Parser skips empty rows."""
        csv_content = """Company Name,Stage
Acme Corp,Seed

Beta Inc,Pre-Seed
"""
        records = list(parse_openvc_csv(StringIO(csv_content)))

        assert len(records) == 2

    def test_parse_skips_rows_without_company_name(self):
        """Parser skips rows missing company name."""
        csv_content = """Company Name,Stage
Acme Corp,Seed
,Pre-Seed
Beta Inc,Seed
"""
        records = list(parse_openvc_csv(StringIO(csv_content)))

        assert len(records) == 2
        assert records[0].company_name == "Acme Corp"
        assert records[1].company_name == "Beta Inc"


class TestOpenVCImporter:
    """Tests for OpenVCImporter class."""

    @pytest.mark.asyncio
    async def test_import_creates_signals(self, tmp_path):
        """Importer creates signals from CSV."""
        from storage.signal_store import SignalStore

        # Create CSV file
        csv_file = tmp_path / "openvc_export.csv"
        csv_file.write_text("""Company Name,Stage,Sector,Geography,Website
Acme Corp,Seed,Consumer,US,https://acme.com
Beta Health,Pre-Seed,HealthTech,UK,https://beta.health
""")

        # Create store and import
        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            importer = OpenVCImporter(store)
            result = await importer.import_csv(str(csv_file))

            assert result["imported"] == 2
            assert result["skipped"] == 0
            assert result["errors"] == 0

            # Verify signals in store
            pending = await store.get_pending_signals()
            assert len(pending) >= 2

            # Find our imported signals
            acme = [s for s in pending if "acme" in s.canonical_key.lower()]
            assert len(acme) == 1
            assert acme[0].source_api == "openvc"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_import_deduplicates(self, tmp_path):
        """Importer skips duplicates on re-import."""
        from storage.signal_store import SignalStore

        csv_file = tmp_path / "openvc_export.csv"
        csv_file.write_text("""Company Name,Stage,Sector,Website
Acme Corp,Seed,Consumer,https://acme.com
""")

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            importer = OpenVCImporter(store)

            # First import
            result1 = await importer.import_csv(str(csv_file))
            assert result1["imported"] == 1

            # Second import (same file)
            result2 = await importer.import_csv(str(csv_file))
            assert result2["imported"] == 0
            assert result2["skipped"] == 1  # Duplicate
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_import_stores_raw_data(self, tmp_path):
        """Importer preserves raw CSV data in signal."""
        from storage.signal_store import SignalStore

        csv_file = tmp_path / "openvc_export.csv"
        csv_file.write_text("""Company Name,Stage,Sector,Geography,Funding Target,Website
Acme Corp,Seed,Consumer,US,$2M,https://acme.com
""")

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            importer = OpenVCImporter(store)
            await importer.import_csv(str(csv_file))

            pending = await store.get_pending_signals()
            acme = [s for s in pending if "acme" in s.canonical_key.lower()][0]

            # Raw data should contain CSV fields (stage is normalized)
            assert acme.raw_data["company_name"] == "Acme Corp"
            assert acme.raw_data["stage"] == "seed"  # Normalized
            assert acme.raw_data["sector"] == "Consumer"
            assert acme.raw_data["funding_target"] == "$2M"
            assert acme.raw_data["source"] == "openvc"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_import_filters_by_thesis(self, tmp_path):
        """Importer can filter by thesis fit."""
        from storage.signal_store import SignalStore

        csv_file = tmp_path / "openvc_export.csv"
        csv_file.write_text("""Company Name,Stage,Sector,Website
Consumer App,Seed,Consumer,https://consumer.app
B2B SaaS Tool,Seed,Enterprise SaaS,https://b2b.saas
Health Startup,Pre-Seed,HealthTech,https://health.io
""")

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            importer = OpenVCImporter(store)
            # Only import consumer-related sectors
            result = await importer.import_csv(
                str(csv_file),
                sector_filter=["Consumer", "HealthTech", "Health Tech"]
            )

            assert result["imported"] == 2  # Consumer App + Health Startup
            assert result["skipped"] == 1  # B2B SaaS filtered out
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_import_filters_by_stage(self, tmp_path):
        """Importer can filter by funding stage."""
        from storage.signal_store import SignalStore

        csv_file = tmp_path / "openvc_export.csv"
        csv_file.write_text("""Company Name,Stage,Sector,Website
Early Stage,Pre-Seed,Consumer,https://early.com
Mid Stage,Seed,Consumer,https://mid.com
Late Stage,Series B,Consumer,https://late.com
""")

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            importer = OpenVCImporter(store)
            # Only import pre-seed and seed (fund focus)
            result = await importer.import_csv(
                str(csv_file),
                stage_filter=["pre-seed", "seed", "series-a"]
            )

            assert result["imported"] == 2  # Pre-Seed + Seed
            assert result["skipped"] == 1  # Series B filtered out
        finally:
            await store.close()


class TestOpenVCImporterDryRun:
    """Tests for dry run mode."""

    @pytest.mark.asyncio
    async def test_dry_run_does_not_persist(self, tmp_path):
        """Dry run reports what would be imported without persisting."""
        from storage.signal_store import SignalStore

        csv_file = tmp_path / "openvc_export.csv"
        csv_file.write_text("""Company Name,Stage,Sector,Website
Acme Corp,Seed,Consumer,https://acme.com
""")

        db_path = tmp_path / "test.db"
        store = SignalStore(str(db_path))
        await store.initialize()

        try:
            importer = OpenVCImporter(store)
            result = await importer.import_csv(str(csv_file), dry_run=True)

            assert result["imported"] == 1
            assert result["dry_run"] is True

            # Should NOT be in store
            pending = await store.get_pending_signals()
            acme = [s for s in pending if "acme" in s.canonical_key.lower()]
            assert len(acme) == 0
        finally:
            await store.close()
