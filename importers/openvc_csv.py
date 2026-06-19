"""
OpenVC CSV Importer

Imports CSV exports from OpenVC.app into the Discovery Engine signal store.

Usage:
    python -m importers.openvc_csv path/to/openvc_export.csv

Or via CLI:
    python run_pipeline.py import-csv --source openvc path/to/export.csv
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TextIO, Union
from urllib.parse import urlparse

from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Stage confidence mapping based on validation/maturity
STAGE_CONFIDENCE_MAP = {
    "pre-seed": 0.60,
    "seed": 0.70,
    "seed+": 0.75,
    "series-a": 0.80,
    "series-b": 0.85,
    "series-c": 0.85,
    "series-d": 0.85,
}

DEFAULT_CONFIDENCE = 0.65

# OpenVC CSV column name variations (they might change format)
COLUMN_MAPPINGS = {
    "company_name": ["Company Name", "company_name", "Company", "Name", "Startup"],
    "stage": ["Stage", "stage", "Funding Stage", "Round"],
    "sector": ["Sector", "sector", "Industry", "Vertical", "Category"],
    "geography": ["Geography", "geography", "Location", "Country", "Region"],
    "funding_target": ["Funding Target", "funding_target", "Target", "Raise", "Amount"],
    "website": ["Website", "website", "URL", "Domain", "Site"],
    "description": ["Description", "description", "About", "Summary"],
    "founder": ["Founder", "founder", "Founders", "Team", "CEO"],
    "traction": ["Traction", "traction", "MRR", "Revenue", "Users"],
}


def _normalize_stage(stage: str) -> str:
    """Normalize stage name to standard format."""
    if not stage:
        return ""

    # Lowercase and replace spaces with hyphens
    normalized = stage.lower().strip().replace(" ", "-")

    # Handle common variations
    variations = {
        "preseed": "pre-seed",
        "pre_seed": "pre-seed",
        "seriesa": "series-a",
        "series_a": "series-a",
        "seriesb": "series-b",
        "series_b": "series-b",
        "seriesc": "series-c",
        "series_c": "series-c",
        "seriesd": "series-d",
        "series_d": "series-d",
    }

    return variations.get(normalized, normalized)


def _extract_domain(url: str) -> Optional[str]:
    """Extract domain from URL."""
    if not url:
        return None

    try:
        # Add scheme if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]

        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]

        return domain.lower() if domain else None
    except Exception:
        return None


def _slugify(text: str) -> str:
    """Convert text to slug format for canonical keys."""
    if not text:
        return ""

    # Lowercase, replace spaces/special chars with hyphens
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def map_stage_to_confidence(stage: str) -> float:
    """Map funding stage to confidence score."""
    normalized = _normalize_stage(stage)
    return STAGE_CONFIDENCE_MAP.get(normalized, DEFAULT_CONFIDENCE)


@dataclass
class OpenVCRecord:
    """Parsed record from OpenVC CSV export."""

    company_name: str
    stage: str
    sector: Optional[str] = None
    geography: Optional[str] = None
    funding_target: Optional[str] = None
    website: Optional[str] = None
    description: Optional[str] = None
    founder: Optional[str] = None
    traction: Optional[str] = None

    # Computed fields
    canonical_key: str = field(default="")
    confidence: float = field(default=DEFAULT_CONFIDENCE)

    def __post_init__(self):
        """Compute derived fields after initialization."""
        # Normalize stage
        self.stage = _normalize_stage(self.stage)

        # Compute confidence from stage
        self.confidence = map_stage_to_confidence(self.stage)

        # Generate canonical key
        self.canonical_key = self._generate_canonical_key()

    def _generate_canonical_key(self) -> str:
        """Generate canonical key for deduplication."""
        # Priority 1: Domain from website
        domain = _extract_domain(self.website)
        if domain:
            return f"domain:{domain}"

        # Priority 2: Name + Location
        name_slug = _slugify(self.company_name)
        if self.geography:
            loc_slug = _slugify(self.geography)
            return f"name_loc:{name_slug}:{loc_slug}"

        # Priority 3: Name only (least reliable)
        return f"name:{name_slug}"

    @classmethod
    def from_csv_row(cls, row: Dict[str, str]) -> "OpenVCRecord":
        """Create record from CSV row dict."""
        def get_field(field_name: str) -> Optional[str]:
            """Get field value trying multiple column name variations."""
            for col_name in COLUMN_MAPPINGS.get(field_name, [field_name]):
                if col_name in row and row[col_name]:
                    return row[col_name].strip()
            return None

        company_name = get_field("company_name") or ""
        stage = get_field("stage") or ""

        return cls(
            company_name=company_name,
            stage=stage,
            sector=get_field("sector"),
            geography=get_field("geography"),
            funding_target=get_field("funding_target"),
            website=get_field("website"),
            description=get_field("description"),
            founder=get_field("founder"),
            traction=get_field("traction"),
        )

    def to_raw_data(self) -> Dict[str, Any]:
        """Convert to raw_data dict for signal storage."""
        data = {
            "company_name": self.company_name,
            "stage": self.stage,
            "source": "openvc",
        }

        # Add optional fields
        if self.sector:
            data["sector"] = self.sector
        if self.geography:
            data["geography"] = self.geography
        if self.funding_target:
            data["funding_target"] = self.funding_target
        if self.website:
            data["website"] = self.website
        if self.description:
            data["description"] = self.description
        if self.founder:
            data["founder"] = self.founder
        if self.traction:
            data["traction"] = self.traction

        return data


def parse_openvc_csv(file_obj: TextIO) -> Iterator[OpenVCRecord]:
    """
    Parse OpenVC CSV file and yield records.

    Args:
        file_obj: File-like object to read CSV from

    Yields:
        OpenVCRecord for each valid row
    """
    reader = csv.DictReader(file_obj)

    for row in reader:
        # Skip empty rows
        if not any(row.values()):
            continue

        record = OpenVCRecord.from_csv_row(row)

        # Skip rows without company name
        if not record.company_name:
            continue

        yield record


class OpenVCImporter:
    """
    Imports OpenVC CSV exports into SignalStore.

    Usage:
        store = SignalStore()
        await store.initialize()

        importer = OpenVCImporter(store)
        result = await importer.import_csv("openvc_export.csv")
        print(f"Imported {result['imported']} signals")
    """

    def __init__(self, store: SignalStore):
        """
        Initialize importer.

        Args:
            store: SignalStore instance for persistence
        """
        self._store = store

    async def import_csv(
        self,
        file_path: Union[str, Path],
        *,
        dry_run: bool = False,
        sector_filter: Optional[List[str]] = None,
        stage_filter: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Import OpenVC CSV file.

        Args:
            file_path: Path to CSV file
            dry_run: If True, report what would be imported without persisting
            sector_filter: Only import records matching these sectors (case-insensitive)
            stage_filter: Only import records matching these stages (normalized)

        Returns:
            Dict with import statistics:
            - imported: Number of signals imported
            - skipped: Number of records skipped (filtered or duplicate)
            - errors: Number of errors
            - dry_run: Whether this was a dry run
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        # Normalize filters
        if sector_filter:
            sector_filter = [s.lower() for s in sector_filter]
        if stage_filter:
            stage_filter = [_normalize_stage(s) for s in stage_filter]

        stats = {
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "dry_run": dry_run,
        }

        logger.info(f"Importing OpenVC CSV: {file_path} (dry_run={dry_run})")

        with open(file_path, "r", encoding="utf-8") as f:
            for record in parse_openvc_csv(f):
                try:
                    # Apply sector filter
                    if sector_filter and record.sector:
                        if record.sector.lower() not in sector_filter:
                            logger.debug(f"Skipping {record.company_name}: sector {record.sector} not in filter")
                            stats["skipped"] += 1
                            continue

                    # Apply stage filter
                    if stage_filter:
                        if record.stage not in stage_filter:
                            logger.debug(f"Skipping {record.company_name}: stage {record.stage} not in filter")
                            stats["skipped"] += 1
                            continue

                    # Check for duplicate
                    if not dry_run:
                        suppressed = await self._store.check_suppression(record.canonical_key)
                        if suppressed:
                            logger.debug(f"Skipping {record.company_name}: already in suppression cache")
                            stats["skipped"] += 1
                            continue

                        # Check if canonical key already exists in signals
                        existing = await self._check_existing(record.canonical_key)
                        if existing:
                            logger.debug(f"Skipping {record.company_name}: duplicate canonical key")
                            stats["skipped"] += 1
                            continue

                    # Import signal
                    if not dry_run:
                        await self._import_record(record)

                    stats["imported"] += 1
                    logger.info(f"{'Would import' if dry_run else 'Imported'}: {record.company_name} ({record.canonical_key})")

                except Exception as e:
                    logger.error(f"Error importing {record.company_name}: {e}")
                    stats["errors"] += 1

        logger.info(
            f"Import complete: {stats['imported']} imported, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )

        return stats

    async def _check_existing(self, canonical_key: str) -> bool:
        """Check if canonical key already exists in signals table."""
        cursor = await self._store._db.execute(
            "SELECT 1 FROM signals WHERE canonical_key = ? LIMIT 1",
            (canonical_key,)
        )
        row = await cursor.fetchone()
        return row is not None

    async def _import_record(self, record: OpenVCRecord) -> int:
        """
        Import a single record as a signal.

        Returns:
            Signal ID
        """
        signal_id = await self._store.save_signal(
            signal_type="openvc_listing",
            source_api="openvc",
            canonical_key=record.canonical_key,
            company_name=record.company_name,
            confidence=record.confidence,
            raw_data=record.to_raw_data(),
            detected_at=datetime.now(timezone.utc),
        )

        return signal_id


async def run_import(
    file_path: str,
    db_path: Optional[str] = None,
    dry_run: bool = False,
    sector_filter: Optional[List[str]] = None,
    stage_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run import standalone.

    Args:
        file_path: Path to OpenVC CSV file
        db_path: Path to signals database
        dry_run: If True, don't persist
        sector_filter: Only import these sectors
        stage_filter: Only import these stages

    Returns:
        Import statistics dict
    """
    store = SignalStore(db_path)
    await store.initialize()

    try:
        importer = OpenVCImporter(store)
        return await importer.import_csv(
            file_path,
            dry_run=dry_run,
            sector_filter=sector_filter,
            stage_filter=stage_filter,
        )
    finally:
        await store.close()


if __name__ == "__main__":
    import asyncio
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m importers.openvc_csv <csv_file> [--dry-run]")
        sys.exit(1)

    file_path = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    result = asyncio.run(run_import(file_path, dry_run=dry_run))

    print(f"\nImport Results:")
    print(f"  Imported: {result['imported']}")
    print(f"  Skipped: {result['skipped']}")
    print(f"  Errors: {result['errors']}")
    if result["dry_run"]:
        print("  (Dry run - no changes made)")
