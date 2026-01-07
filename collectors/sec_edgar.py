"""
SEC EDGAR Form D Collector for Discovery Engine

Form D filings indicate companies raising money via Regulation D (exempt offerings).
This is a strong signal of early-stage fundraising, often before public announcements.

Key Form D fields we extract:
- Company name, CIK (Central Index Key)
- Offering amount (how much they're raising)
- Industry classification (SIC code)
- Filing date (freshness)
- Location (jurisdiction)
- Issuer type (determines stage)

Focus areas for Press On Ventures:
- Healthtech (SIC codes: 2834, 3841, 8071, 8082, etc.)
- Cleantech (SIC codes: 3711, 4911, 4931, 4939, etc.)
- AI Infrastructure (SIC codes: 7371, 7372, 7373, etc.)

SEC EDGAR API docs:
- Form D RSS feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D
- Full-text search: https://www.sec.gov/edgar/search-and-access
- Company lookup: https://www.sec.gov/cgi-bin/browse-edgar?company={name}
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

import httpx

from collectors.base import BaseCollector
from collectors.retry_strategy import with_retry, RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.rate_limiter import get_rate_limiter
from utils.canonical_keys import build_canonical_key_candidates, canonical_key_from_external_refs
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)


# =============================================================================
# INDUSTRY CLASSIFICATIONS
# =============================================================================

# SIC codes for thesis fit (Press On Ventures focus areas)
HEALTHTECH_SIC_CODES = {
    "2834",  # Pharmaceutical Preparations
    "2835",  # In Vitro & In Vivo Diagnostic Substances
    "2836",  # Biological Products (No Diagnostic Substances)
    "3841",  # Surgical & Medical Instruments & Apparatus
    "3842",  # Orthopedic, Prosthetic & Surgical Appliances
    "3845",  # Electromedical & Electrotherapeutic Apparatus
    "5047",  # Medical, Dental & Hospital Equipment & Supplies
    "8071",  # Medical Laboratories
    "8082",  # Home Health Care Services
    "8090",  # Miscellaneous Health & Allied Services, NEC
    "8091",  # Health & Allied Services, NEC
}

CLEANTECH_SIC_CODES = {
    "1311",  # Crude Petroleum & Natural Gas
    "1381",  # Drilling Oil & Gas Wells
    "2860",  # Industrial Organic Chemicals
    "2890",  # Miscellaneous Chemical Products
    "3510",  # Engines & Turbines
    "3511",  # Steam, Gas & Hydraulic Turbines
    "3531",  # Construction Machinery & Equipment
    "3600",  # Electronic & Other Electrical Equipment (No Computer Equipment)
    "3621",  # Motors & Generators
    "3711",  # Motor Vehicles & Passenger Car Bodies
    "3714",  # Motor Vehicle Parts & Accessories
    "4911",  # Electric Services
    "4922",  # Natural Gas Transmission
    "4923",  # Natural Gas Transmission & Distribution
    "4931",  # Electric & Other Services Combined
    "4939",  # Combination Utilities, NEC
    "4953",  # Refuse Systems
}

AI_INFRASTRUCTURE_SIC_CODES = {
    "3570",  # Computer & Office Equipment
    "3571",  # Electronic Computers
    "3572",  # Computer Storage Devices
    "3576",  # Computer Communications Equipment
    "3577",  # Computer Peripheral Equipment, NEC
    "7370",  # Computer Programming, Data Processing, etc.
    "7371",  # Computer Programming Services
    "7372",  # Prepackaged Software
    "7373",  # Computer Integrated Systems Design
    "7374",  # Computer Processing & Data Preparation
    "7389",  # Business Services, NEC (includes AI/ML services)
}

# Combine all target SIC codes
TARGET_SIC_CODES = HEALTHTECH_SIC_CODES | CLEANTECH_SIC_CODES | AI_INFRASTRUCTURE_SIC_CODES


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FormDFiling:
    """Parsed Form D filing data"""

    # Core identifiers
    cik: str  # Central Index Key (SEC's unique company ID)
    company_name: str
    accession_number: str  # Unique filing ID
    filing_date: datetime

    # Offering details
    offering_amount: Optional[float] = None  # Total offering amount in USD
    offering_sold: Optional[float] = None    # Amount already sold
    minimum_investment: Optional[float] = None

    # Classification
    sic_code: Optional[str] = None
    industry_group: Optional[str] = None  # healthtech, cleantech, ai_infrastructure
    issuer_type: Optional[str] = None     # Corporation, LLC, LP, etc.

    # Location
    state: Optional[str] = None
    country: Optional[str] = "US"  # Default to US

    # External references (for canonical key building)
    website: Optional[str] = None
    external_refs: Dict[str, str] = field(default_factory=dict)

    # Metadata
    filing_url: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_days(self) -> int:
        """Days since filing"""
        return (datetime.now(timezone.utc) - self.filing_date).days

    @property
    def is_recent(self) -> bool:
        """Filed within last 90 days"""
        return self.age_days <= 90

    @property
    def is_target_sector(self) -> bool:
        """Matches Press On Ventures thesis"""
        return self.industry_group is not None

    @property
    def stage_estimate(self) -> str:
        """Estimate funding stage from offering amount"""
        if not self.offering_amount:
            return "Unknown"

        amount = self.offering_amount
        if amount < 500_000:
            return "Pre-Seed"
        elif amount < 3_000_000:
            return "Seed"
        elif amount < 10_000_000:
            return "Seed +"
        elif amount < 30_000_000:
            return "Series A"
        else:
            return "Series B"

    def to_signal(self) -> Signal:
        """Convert filing to a Signal for verification gate"""

        # Build canonical key candidates from available identifiers
        canonical_key_candidates = build_canonical_key_candidates(
            domain_or_website=self.website or "",
            companies_house_number="",
            crunchbase_uuid="",
            pitchbook_uuid="",
            github_org="",
            github_repo="",
            fallback_company_name=self.company_name,
            fallback_region=self.state or self.country or "",
        )

        # Use first candidate as primary canonical key
        canonical_key = canonical_key_candidates[0] if canonical_key_candidates else f"sec_edgar_{self.cik}"

        # Confidence based on:
        # - Offering amount (higher = more serious)
        # - Industry match (target sector = higher confidence)
        # - Recency (fresher = higher confidence)
        base_confidence = 0.7  # Form D is authoritative data

        # Boost for target sectors
        if self.is_target_sector:
            base_confidence += 0.15

        # Boost for meaningful offering amounts
        if self.offering_amount and self.offering_amount >= 500_000:
            base_confidence += 0.1

        # Slight penalty for older filings
        if self.age_days > 60:
            base_confidence -= 0.05
        if self.age_days > 120:
            base_confidence -= 0.1

        confidence = min(max(base_confidence, 0.0), 1.0)

        return Signal(
            id=f"sec_edgar_{self.accession_number}",
            signal_type="funding_event",
            confidence=confidence,
            source_api="sec_edgar",
            source_url=self.filing_url,
            source_response_hash=None,  # Could hash raw XML if needed
            detected_at=self.filing_date,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["sec_edgar"],
            raw_data={
                "cik": self.cik,
                "company_name": self.company_name,
                "offering_amount": self.offering_amount,
                "offering_sold": self.offering_sold,
                "sic_code": self.sic_code,
                "industry_group": self.industry_group,
                "state": self.state,
                "country": self.country,
                "stage_estimate": self.stage_estimate,
                "filing_date": self.filing_date.isoformat(),
                "issuer_type": self.issuer_type,
                "website": self.website,
                "canonical_key": canonical_key,
                "canonical_key_candidates": canonical_key_candidates,
            }
        )


# =============================================================================
# SEC EDGAR COLLECTOR
# =============================================================================

class SECEdgarCollector(BaseCollector):
    """
    Collects Form D filings from SEC EDGAR to identify companies raising money.

    Form D is filed when companies raise money through Regulation D (private placements).
    This often happens BEFORE public announcements, making it a strong early signal.

    Usage:
        collector = SECEdgarCollector(store=signal_store)
        result = await collector.run(dry_run=False)
    """

    # SEC EDGAR endpoints
    FORM_D_RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    FORM_D_SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar"

    # SEC requires User-Agent per their fair use policy
    DEFAULT_USER_AGENT = "Press On Ventures Discovery Engine (research@pressonvc.com)"

    # Rate limiting (SEC allows reasonable automated access)
    REQUEST_DELAY_SECONDS = 0.15  # ~6 requests/second (well under SEC's limit)

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        user_agent: Optional[str] = None,
        lookback_days: int = 30,
        max_filings: int = 100,
        target_sectors_only: bool = True,
    ):
        """
        Args:
            store: Optional SignalStore instance for persistence
            user_agent: User-Agent string (SEC requires this)
            lookback_days: How many days back to search
            max_filings: Maximum number of filings to process
            target_sectors_only: Only return filings in target sectors
        """
        super().__init__(store=store, collector_name="sec_edgar")

        self.user_agent = user_agent or self.DEFAULT_USER_AGENT
        self.lookback_days = lookback_days
        self.max_filings = max_filings
        self.target_sectors_only = target_sectors_only

        self._client: Optional[httpx.AsyncClient] = None
        self._processed_accession_numbers: Set[str] = set()

    async def __aenter__(self):
        """Async context manager entry"""
        self._client = httpx.AsyncClient(
            headers={"User-Agent": self.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self._client:
            await self._client.aclose()

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from SEC EDGAR Form D filings.

        Returns:
            List of Signal objects
        """
        # Fetch recent Form D filings
        filings = await self._fetch_recent_form_d_filings()

        logger.info(f"Found {len(filings)} Form D filings")

        # Filter for target sectors if requested
        if self.target_sectors_only:
            filings = [f for f in filings if f.is_target_sector]
            logger.info(f"Filtered to {len(filings)} filings in target sectors")

        # Convert to signals
        signals = [f.to_signal() for f in filings]

        return signals

    async def _fetch_recent_form_d_filings(self) -> List[FormDFiling]:
        """
        Fetch recent Form D filings from SEC EDGAR.

        Uses the RSS feed approach which is more reliable than scraping.
        """
        filings: List[FormDFiling] = []

        # SEC EDGAR Browse endpoint with Form D filter
        # Note: The RSS/Atom feed is more reliable than screen-scraping
        params = {
            "action": "getcurrent",
            "type": "D",
            "company": "",
            "dateb": "",
            "owner": "include",
            "count": str(self.max_filings),
            "output": "atom",  # Returns Atom XML feed
        }

        url = f"{self.FORM_D_RSS_URL}?{urlencode(params)}"
        logger.info(f"Fetching Form D RSS feed: {url}")

        try:
            # Use rate limiter before making request
            await self.rate_limiter.acquire()

            # Wrap HTTP request with retry logic
            async def fetch_atom_feed():
                response = await self._client.get(url)
                response.raise_for_status()
                return response.text

            response_text = await with_retry(fetch_atom_feed, self.retry_config)

            # Parse Atom XML feed
            filings = self._parse_form_d_atom_feed(response_text)

            # Filter by date
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
            filings = [f for f in filings if f.filing_date >= cutoff_date]

            logger.info(f"Parsed {len(filings)} filings within lookback window")

            # Enrich filings with detailed data (fetch individual Form D XML)
            # Note: This is rate-limited to avoid hammering SEC servers
            for filing in filings[:self.max_filings]:
                if filing.accession_number not in self._processed_accession_numbers:
                    await self._enrich_filing(filing)
                    self._processed_accession_numbers.add(filing.accession_number)
                    await asyncio.sleep(self.REQUEST_DELAY_SECONDS)

        except Exception as e:
            logger.error(f"Error fetching Form D feed: {e}")
            raise

        return filings

    def _parse_form_d_atom_feed(self, atom_xml: str) -> List[FormDFiling]:
        """
        Parse SEC EDGAR Atom feed for Form D filings.

        Atom feed structure:
        <feed>
          <entry>
            <title>D - Company Name (0001234567) (Filer)</title>
            <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=..."/>
            <id>urn:tag:sec.gov,2008:accession-number=1234567890-12-345678</id>
            <updated>2024-01-15T00:00:00-05:00</updated>
            <summary>...</summary>
          </entry>
        </feed>
        """
        filings: List[FormDFiling] = []

        try:
            # Parse XML
            root = ET.fromstring(atom_xml)

            # Atom namespace
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            # Extract entries
            for entry in root.findall("atom:entry", ns):
                try:
                    # Extract basic info from feed
                    title = entry.find("atom:title", ns)
                    title_text = title.text if title is not None else ""

                    # Parse title: "D - Company Name (CIK) (Filer)"
                    company_name, cik = self._parse_atom_title(title_text)

                    # Extract accession number from ID
                    id_elem = entry.find("atom:id", ns)
                    id_text = id_elem.text if id_elem is not None else ""
                    accession_number = self._extract_accession_number(id_text)

                    # Extract filing date
                    updated = entry.find("atom:updated", ns)
                    filing_date = self._parse_date(updated.text if updated is not None else "")

                    # Extract filing URL
                    link = entry.find("atom:link", ns)
                    filing_url = link.get("href", "") if link is not None else ""

                    if company_name and cik and accession_number:
                        filing = FormDFiling(
                            cik=cik,
                            company_name=company_name,
                            accession_number=accession_number,
                            filing_date=filing_date,
                            filing_url=filing_url,
                        )
                        filings.append(filing)

                except Exception as e:
                    logger.warning(f"Error parsing Atom entry: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing Atom XML: {e}")
            raise

        return filings

    def _parse_atom_title(self, title: str) -> tuple[str, str]:
        """
        Parse Atom entry title to extract company name and CIK.

        Format: "D - Company Name (0001234567) (Filer)"
        Returns: (company_name, cik)
        """
        # Pattern: "D - {name} ({cik}) (Filer)"
        match = re.search(r"D\s+-\s+(.+?)\s+\((\d+)\)\s+\(Filer\)", title)
        if match:
            company_name = match.group(1).strip()
            cik = match.group(2).strip()
            return company_name, cik

        return "", ""

    def _extract_accession_number(self, id_text: str) -> str:
        """
        Extract accession number from Atom ID.

        Format: "urn:tag:sec.gov,2008:accession-number=1234567890-12-345678"
        """
        match = re.search(r"accession-number=([0-9-]+)", id_text)
        if match:
            return match.group(1)
        return ""

    def _parse_date(self, date_str: str) -> datetime:
        """Parse ISO 8601 date string to datetime"""
        try:
            # Handle formats like "2024-01-15T00:00:00-05:00"
            # Python 3.7+ supports fromisoformat with timezone
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            # Fallback to current time if parsing fails
            return datetime.now(timezone.utc)

    async def _enrich_filing(self, filing: FormDFiling) -> None:
        """
        Enrich a filing with detailed data from the actual Form D XML.

        Form D XML contains:
        - Offering amount, securities sold, minimum investment
        - Issuer type, industry classification
        - Related persons (executives, directors)
        - Sometimes website/contact info
        """
        try:
            # Build URL to Form D primary document
            # Format: https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/primary_doc.xml
            cik_padded = filing.cik.zfill(10)
            accession_no_dashes = filing.accession_number.replace("-", "")

            # Construct the primary document URL
            # SEC stores Form D as "primary_doc.xml" in the filing directory
            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_padded}/"
                f"{accession_no_dashes}/primary_doc.xml"
            )

            logger.debug(f"Fetching Form D XML: {doc_url}")

            # Use rate limiter before making request
            await self.rate_limiter.acquire()

            # Wrap HTTP request with retry logic
            async def fetch_form_d_xml():
                response = await self._client.get(doc_url)

                # If primary_doc.xml doesn't exist, return None (don't retry 404s)
                if response.status_code == 404:
                    logger.debug(f"primary_doc.xml not found for {filing.accession_number}, skipping enrichment")
                    return None

                response.raise_for_status()
                return response.text

            response_text = await with_retry(fetch_form_d_xml, self.retry_config)

            # Parse Form D XML if we got content (not 404)
            if response_text:
                self._parse_form_d_xml(filing, response_text)

        except Exception as e:
            logger.warning(f"Could not enrich filing {filing.accession_number}: {e}")

    def _parse_form_d_xml(self, filing: FormDFiling, xml_content: str) -> None:
        """
        Parse Form D XML to extract detailed offering information.

        Key fields in Form D XML:
        - offeringData/totalOfferingAmount
        - offeringData/totalAmountSold
        - offeringData/minimumInvestmentAccepted
        - issuerData/industryGroup/industryGroupType
        - issuerData/issuerAddress/stateOrCountry
        - signatureBlock/authorizedRepresentative
        """
        try:
            root = ET.fromstring(xml_content)

            # Note: Form D XML doesn't use namespaces (as of 2024)

            # Extract offering amounts
            offering_data = root.find(".//offeringData")
            if offering_data is not None:
                total_offering = offering_data.find("totalOfferingAmount")
                if total_offering is not None and total_offering.text:
                    filing.offering_amount = float(total_offering.text)

                amount_sold = offering_data.find("totalAmountSold")
                if amount_sold is not None and amount_sold.text:
                    filing.offering_sold = float(amount_sold.text)

                min_investment = offering_data.find("minimumInvestmentAccepted")
                if min_investment is not None and min_investment.text:
                    filing.minimum_investment = float(min_investment.text)

            # Extract industry classification
            issuer_data = root.find(".//issuerData")
            if issuer_data is not None:
                # Industry group (SIC code approach)
                industry_group = issuer_data.find(".//industryGroupType")
                if industry_group is not None and industry_group.text:
                    sic_code = industry_group.text.strip()
                    filing.sic_code = sic_code
                    filing.industry_group = self._classify_industry(sic_code)

                # Issuer type
                entity_type = issuer_data.find(".//issuerEntityType")
                if entity_type is not None and entity_type.text:
                    filing.issuer_type = entity_type.text.strip()

                # Location
                issuer_address = issuer_data.find(".//issuerAddress")
                if issuer_address is not None:
                    state = issuer_address.find("stateOrCountry")
                    if state is not None and state.text:
                        filing.state = state.text.strip()

                    country = issuer_address.find("stateOrCountryDescription")
                    if country is not None and country.text:
                        filing.country = country.text.strip()

            # Try to extract website (sometimes in relatedPersons section)
            # Note: Form D doesn't consistently include website, but worth checking
            for related_person in root.findall(".//relatedPersonInfo"):
                relationship = related_person.find(".//relationship")
                if relationship is not None:
                    # Sometimes contact info includes company website
                    address = related_person.find(".//address")
                    if address is not None:
                        # This is a stretch - Form D rarely has website
                        # But we log it for potential manual enrichment
                        pass

            # Store raw XML for audit trail
            filing.raw_data["form_d_xml_parsed"] = True

        except Exception as e:
            logger.warning(f"Error parsing Form D XML: {e}")

    def _classify_industry(self, sic_code: str) -> Optional[str]:
        """
        Classify SIC code into thesis-fit industry groups.

        Returns:
            "healthtech", "cleantech", "ai_infrastructure", or None
        """
        if not sic_code:
            return None

        sic_code = sic_code.strip()

        if sic_code in HEALTHTECH_SIC_CODES:
            return "healthtech"
        elif sic_code in CLEANTECH_SIC_CODES:
            return "cleantech"
        elif sic_code in AI_INFRASTRUCTURE_SIC_CODES:
            return "ai_infrastructure"

        return None

    def get_filing_by_cik(self, cik: str) -> Optional[FormDFiling]:
        """
        Get a specific filing by CIK (for testing/debugging).

        This is a helper method for manual lookup.
        """
        # Would implement direct CIK lookup here if needed
        raise NotImplementedError("Direct CIK lookup not yet implemented")


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

async def example_usage():
    """Example demonstrating the SEC EDGAR collector"""

    collector = SECEdgarCollector(
        lookback_days=30,
        max_filings=50,
        target_sectors_only=True,
    )

    result = await collector.run(dry_run=True)

    print("=" * 50)
    print("SEC EDGAR COLLECTOR RESULT")
    print("=" * 50)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")
    print(f"Suppressed: {result.signals_suppressed}")
    print(f"Dry run: {result.dry_run}")

    if result.error_message:
        print(f"Error: {result.error_message}")


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run example
    asyncio.run(example_usage())
