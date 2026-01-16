"""
PitchBook CSV Importer

Imports CSV exports from PitchBook into the Discovery Engine signal store.

Usage:
    python -m importers.pitchbook_csv path/to/pitchbook_export.csv

Or via CLI:
    python run_pipeline.py import-csv --source pitchbook path/to/export.csv
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

# Stage confidence mapping based on deal type
DEAL_TYPE_CONFIDENCE_MAP = {
    "seed round": 0.70,
    "seed": 0.70,
    "pre-seed": 0.60,
    "early stage vc": 0.75,  # Could be seed or series A
    "later stage vc": 0.85,
    "series a": 0.80,
    "series b": 0.85,
    "series c": 0.85,
    "corporate": 0.80,
    "angel": 0.60,
}

DEFAULT_CONFIDENCE = 0.65

# PitchBook CSV column name variations
COLUMN_MAPPINGS = {
    "company_name": ["Company", "Company Name", "company_name", "Name"],
    "sector": ["Primary Industry Sector", "Industry Sector", "Sector", "Industry"],
    "deal_type": ["Last Financing Deal Type", "Deal Type", "Round Type", "Stage"],
    "financing_date": ["Last Financing Date", "Financing Date", "Date"],
    "financing_amount": ["Last Financing Amount (M)", "Amount (M)", "Amount"],
    "valuation": ["Last Financing Valuation (M)", "Valuation (M)", "Valuation"],
    "revenue": ["Revenue (M)", "Revenue"],
    "contact": ["Primary Contact", "Contact", "CEO", "Founder"],
    "year_founded": ["Year Founded", "Founded", "Founding Year"],
    "employees": ["Employees", "Employee Count", "Team Size"],
    "website": ["Website", "URL", "Domain", "Site"],
}

# Sector mapping to thesis categories
THESIS_SECTORS = {
    # Consumer Products and Services
    "consumer products and services": "consumer",
    "consumer goods": "consumer",
    "consumer services": "consumer",
    "food & beverage": "consumer_cpg",
    "retail": "consumer_marketplace",
    # Healthcare
    "healthcare": "consumer_healthtech",
    "health care": "consumer_healthtech",
    "healthtech": "consumer_healthtech",
    # Travel
    "travel": "travel_hospitality",
    "hospitality": "travel_hospitality",
    "restaurants": "travel_hospitality",
    # Financial (often out of scope but keep for review)
    "financial services": "fintech",
    # Tech (may or may not be consumer-facing)
    "information technology": "tech",
    "software": "tech",
    # B2B (often out of scope)
    "business products and services": "b2b",
    # Energy (out of scope)
    "energy": "energy",
}


def _normalize_stage(deal_type: str) -> str:
    """Normalize deal type to standard stage format."""
    if not deal_type:
        return ""

    normalized = deal_type.lower().strip()

    # Map deal types to stages
    stage_map = {
        "seed round": "seed",
        "pre-seed": "pre-seed",
        "early stage vc": "early-stage",  # Could be seed or series A
        "later stage vc": "later-stage",
        "series a": "series-a",
        "series b": "series-b",
        "series c": "series-c",
        "corporate": "corporate",
        "angel": "angel",
    }

    return stage_map.get(normalized, normalized)


def _extract_domain(url: str) -> Optional[str]:
    """Extract domain from URL, handling Google search URLs."""
    if not url:
        return None

    try:
        # Handle Google search URLs (e.g., https://www.google.com/search?q=domain.com)
        if "google.com/search" in url:
            # Extract the query parameter
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed.query)
            if "q" in query_params:
                # The query is often just the domain
                query = query_params["q"][0]
                # Try to extract domain from query
                if "." in query and not " " in query:
                    domain = query.lower()
                    if domain.startswith("www."):
                        domain = domain[4:]
                    return domain
            return None

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

    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def map_deal_type_to_confidence(deal_type: str) -> float:
    """Map deal type to confidence score."""
    normalized = deal_type.lower().strip() if deal_type else ""
    return DEAL_TYPE_CONFIDENCE_MAP.get(normalized, DEFAULT_CONFIDENCE)


@dataclass
class PitchBookRecord:
    """Parsed record from PitchBook CSV export."""

    company_name: str
    sector: Optional[str] = None
    deal_type: Optional[str] = None
    financing_date: Optional[str] = None
    financing_amount: Optional[str] = None
    valuation: Optional[str] = None
    revenue: Optional[str] = None
    contact: Optional[str] = None
    year_founded: Optional[str] = None
    employees: Optional[str] = None
    website: Optional[str] = None

    # Computed fields
    stage: str = field(default="")
    canonical_key: str = field(default="")
    confidence: float = field(default=DEFAULT_CONFIDENCE)
    thesis_category: Optional[str] = field(default=None)

    def __post_init__(self):
        """Compute derived fields after initialization."""
        # Normalize stage from deal type
        self.stage = _normalize_stage(self.deal_type)

        # Compute confidence from deal type
        self.confidence = map_deal_type_to_confidence(self.deal_type)

        # Map sector to thesis category
        if self.sector:
            self.thesis_category = THESIS_SECTORS.get(self.sector.lower())

        # Generate canonical key
        self.canonical_key = self._generate_canonical_key()

    def _generate_canonical_key(self) -> str:
        """Generate canonical key for deduplication."""
        # Priority 1: Domain from website
        domain = _extract_domain(self.website)
        if domain:
            return f"domain:{domain}"

        # Priority 2: Name only (PitchBook doesn't have consistent geography)
        name_slug = _slugify(self.company_name)
        return f"name:{name_slug}"

    @classmethod
    def from_csv_row(cls, row: Dict[str, str]) -> "PitchBookRecord":
        """Create record from CSV row dict."""
        def get_field(field_name: str) -> Optional[str]:
            """Get field value trying multiple column name variations."""
            for col_name in COLUMN_MAPPINGS.get(field_name, [field_name]):
                if col_name in row and row[col_name]:
                    return row[col_name].strip()
            return None

        company_name = get_field("company_name") or ""

        return cls(
            company_name=company_name,
            sector=get_field("sector"),
            deal_type=get_field("deal_type"),
            financing_date=get_field("financing_date"),
            financing_amount=get_field("financing_amount"),
            valuation=get_field("valuation"),
            revenue=get_field("revenue"),
            contact=get_field("contact"),
            year_founded=get_field("year_founded"),
            employees=get_field("employees"),
            website=get_field("website"),
        )

    def to_raw_data(self) -> Dict[str, Any]:
        """Convert to raw_data dict for signal storage."""
        data = {
            "company_name": self.company_name,
            "stage": self.stage,
            "source": "pitchbook",
        }

        # Add optional fields
        if self.sector:
            data["sector"] = self.sector
        if self.deal_type:
            data["deal_type"] = self.deal_type
        if self.financing_date:
            data["financing_date"] = self.financing_date
        if self.financing_amount:
            data["financing_amount"] = self.financing_amount
        if self.valuation:
            data["valuation"] = self.valuation
        if self.revenue:
            data["revenue"] = self.revenue
        if self.contact:
            data["contact"] = self.contact
        if self.year_founded:
            data["year_founded"] = self.year_founded
        if self.employees:
            data["employees"] = self.employees
        if self.website:
            data["website"] = self.website
        if self.thesis_category:
            data["thesis_category"] = self.thesis_category

        return data


def parse_pitchbook_csv(file_obj: TextIO) -> Iterator[PitchBookRecord]:
    """
    Parse PitchBook CSV file and yield records.

    Args:
        file_obj: File-like object to read CSV from

    Yields:
        PitchBookRecord for each valid row
    """
    reader = csv.DictReader(file_obj)

    for row in reader:
        # Skip empty rows
        if not any(row.values()):
            continue

        record = PitchBookRecord.from_csv_row(row)

        # Skip rows without company name
        if not record.company_name:
            continue

        yield record


class PitchBookImporter:
    """
    Imports PitchBook CSV exports into SignalStore.

    Usage:
        store = SignalStore("signals.db")
        await store.initialize()

        importer = PitchBookImporter(store)
        result = await importer.import_csv("pitchbook_export.csv")
        print(f"Imported {result['imported']} signals")
    """

    def __init__(self, store: SignalStore):
        """Initialize importer."""
        self._store = store

    async def import_csv(
        self,
        file_path: Union[str, Path],
        *,
        dry_run: bool = False,
        sector_filter: Optional[List[str]] = None,
        stage_filter: Optional[List[str]] = None,
        thesis_only: bool = False,
    ) -> Dict[str, Any]:
        """
        Import PitchBook CSV file.

        Args:
            file_path: Path to CSV file
            dry_run: If True, report what would be imported without persisting
            sector_filter: Only import records matching these sectors
            stage_filter: Only import records matching these stages
            thesis_only: Only import records that map to thesis categories

        Returns:
            Dict with import statistics
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
            "by_sector": {},
            "by_thesis": {},
        }

        logger.info(f"Importing PitchBook CSV: {file_path} (dry_run={dry_run})")

        with open(file_path, "r", encoding="utf-8") as f:
            for record in parse_pitchbook_csv(f):
                try:
                    # Track by sector
                    sector_key = record.sector or "Unknown"
                    stats["by_sector"][sector_key] = stats["by_sector"].get(sector_key, 0) + 1

                    # Track by thesis category
                    thesis_key = record.thesis_category or "unmapped"
                    stats["by_thesis"][thesis_key] = stats["by_thesis"].get(thesis_key, 0) + 1

                    # Apply thesis filter
                    if thesis_only and not record.thesis_category:
                        logger.debug(f"Skipping {record.company_name}: no thesis category")
                        stats["skipped"] += 1
                        continue

                    # Skip B2B and Energy (out of scope)
                    if record.thesis_category in ("b2b", "energy"):
                        logger.debug(f"Skipping {record.company_name}: {record.thesis_category} out of scope")
                        stats["skipped"] += 1
                        continue

                    # Apply sector filter
                    if sector_filter and record.sector:
                        if record.sector.lower() not in sector_filter:
                            logger.debug(f"Skipping {record.company_name}: sector filter")
                            stats["skipped"] += 1
                            continue

                    # Apply stage filter
                    if stage_filter:
                        if record.stage not in stage_filter:
                            logger.debug(f"Skipping {record.company_name}: stage filter")
                            stats["skipped"] += 1
                            continue

                    # Check for duplicate
                    if not dry_run:
                        suppressed = await self._store.check_suppression(record.canonical_key)
                        if suppressed:
                            logger.debug(f"Skipping {record.company_name}: suppressed")
                            stats["skipped"] += 1
                            continue

                        existing = await self._check_existing(record.canonical_key)
                        if existing:
                            logger.debug(f"Skipping {record.company_name}: duplicate")
                            stats["skipped"] += 1
                            continue

                    # Import signal
                    if not dry_run:
                        await self._import_record(record)

                    stats["imported"] += 1
                    logger.info(
                        f"{'Would import' if dry_run else 'Imported'}: "
                        f"{record.company_name} ({record.thesis_category or 'no-thesis'}) - {record.canonical_key}"
                    )

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

    async def _import_record(self, record: PitchBookRecord) -> int:
        """Import a single record as a signal."""
        signal_id = await self._store.save_signal(
            signal_type="pitchbook_listing",
            source_api="pitchbook",
            canonical_key=record.canonical_key,
            company_name=record.company_name,
            confidence=record.confidence,
            raw_data=record.to_raw_data(),
            detected_at=datetime.now(timezone.utc),
        )

        return signal_id


async def run_import(
    file_path: str,
    db_path: str = "signals.db",
    dry_run: bool = False,
    sector_filter: Optional[List[str]] = None,
    stage_filter: Optional[List[str]] = None,
    thesis_only: bool = False,
) -> Dict[str, Any]:
    """Run import standalone."""
    store = SignalStore(db_path)
    await store.initialize()

    try:
        importer = PitchBookImporter(store)
        return await importer.import_csv(
            file_path,
            dry_run=dry_run,
            sector_filter=sector_filter,
            stage_filter=stage_filter,
            thesis_only=thesis_only,
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
        print("Usage: python -m importers.pitchbook_csv <csv_file> [--dry-run]")
        sys.exit(1)

    file_path = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    result = asyncio.run(run_import(file_path, dry_run=dry_run))

    print(f"\nImport Results:")
    print(f"  Imported: {result['imported']}")
    print(f"  Skipped: {result['skipped']}")
    print(f"  Errors: {result['errors']}")
    print(f"\nBy Sector:")
    for sector, count in sorted(result["by_sector"].items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count}")
    print(f"\nBy Thesis Category:")
    for thesis, count in sorted(result["by_thesis"].items(), key=lambda x: -x[1]):
        print(f"  {thesis}: {count}")
    if result["dry_run"]:
        print("\n  (Dry run - no changes made)")
