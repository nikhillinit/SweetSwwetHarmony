"""
NotionLPSync - LP database synchronization for relationship intelligence.

Syncs LP (Limited Partner) records from Notion database and extracts
firm-level relationship scores with attribution.

Key features:
- Status tier scoring (Docs Signed: 0.95, Verbal: 0.70, Engaged: 0.40, In DB: 0.25)
- Domain extraction from Website with email fallback
- Multi-LP merge (highest tier wins, concatenate attribution)
- Provider blocklist (gmail, yahoo, etc.)

Usage:
    sync = NotionLPSync(
        api_key=os.environ["NOTION_API_KEY"],
        database_id=os.environ["NOTION_LP_DATABASE_ID"],
    )
    relationships = await sync.sync()

    for rel in relationships:
        print(f"{rel.domain}: {rel.score:.2f} {rel.badge} ({rel.attribution})")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Notion LP scoring tiers (from design doc)
NOTION_SCORE_DOCS_SIGNED = 0.95
NOTION_SCORE_VERBAL = 0.70
NOTION_SCORE_ENGAGED = 0.40
NOTION_SCORE_IN_DB = 0.25

# Provider blocklist - same as LocalEmailScanner
LP_PROVIDER_BLOCKLIST = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
}


# =============================================================================
# LP STATUS ENUM
# =============================================================================

class LPStatus(str, Enum):
    """LP status with associated scores."""

    DOCS_SIGNED = "Docs Signed"
    VERBAL_CONFIRM = "Verbal Confirm"
    ENGAGEMENT_SENT = "Engagement Sent"
    IN_DATABASE = "In Database"
    DECLINED = "Declined"

    @property
    def score(self) -> float:
        """Get score for this status tier."""
        scores = {
            LPStatus.DOCS_SIGNED: NOTION_SCORE_DOCS_SIGNED,
            LPStatus.VERBAL_CONFIRM: NOTION_SCORE_VERBAL,
            LPStatus.ENGAGEMENT_SENT: NOTION_SCORE_ENGAGED,
            LPStatus.IN_DATABASE: NOTION_SCORE_IN_DB,
            LPStatus.DECLINED: 0.0,
        }
        return scores.get(self, NOTION_SCORE_IN_DB)

    @property
    def tier(self) -> int:
        """Get tier for ordering (higher = better)."""
        tiers = {
            LPStatus.DOCS_SIGNED: 4,
            LPStatus.VERBAL_CONFIRM: 3,
            LPStatus.ENGAGEMENT_SENT: 2,
            LPStatus.IN_DATABASE: 1,
            LPStatus.DECLINED: 0,
        }
        return tiers.get(self, 1)

    @classmethod
    def from_notion(cls, status_str: Optional[str]) -> "LPStatus":
        """Parse status from Notion string."""
        if not status_str:
            return cls.IN_DATABASE

        # Normalize for comparison
        normalized = status_str.strip()

        # Map Notion strings to enum values
        mapping = {
            "Docs Signed": cls.DOCS_SIGNED,
            "Verbal Confirm": cls.VERBAL_CONFIRM,
            "Engagement Sent": cls.ENGAGEMENT_SENT,
            "In Database": cls.IN_DATABASE,
            "Declined": cls.DECLINED,
            # Common variations
            "docs signed": cls.DOCS_SIGNED,
            "verbal": cls.VERBAL_CONFIRM,
            "engaged": cls.ENGAGEMENT_SENT,
            "declined": cls.DECLINED,
        }

        return mapping.get(normalized, cls.IN_DATABASE)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class LPRecord:
    """
    Individual LP record from Notion.

    Represents a single person (LP) with their status and contact info.
    """
    notion_id: str
    name: str
    firm: str
    email: Optional[str]
    status: LPStatus
    website: Optional[str] = None
    last_updated: Optional[datetime] = None

    @property
    def score(self) -> float:
        """Get score based on status."""
        return self.status.score

    @property
    def domain(self) -> Optional[str]:
        """
        Extract domain from website or email.

        Priority:
        1. Website URL -> domain
        2. Email address -> domain (if not provider)
        """
        # Try website first
        if self.website:
            domain = _extract_domain_from_url(self.website)
            if domain:
                return domain

        # Fallback to email
        if self.email:
            domain = _extract_domain_from_email(self.email)
            if domain and domain not in LP_PROVIDER_BLOCKLIST:
                return domain

        return None


@dataclass
class FirmRelationship:
    """
    Aggregated firm-level relationship.

    Merges multiple LPs from the same firm into a single relationship.
    """
    domain: str
    score: float
    status: LPStatus
    attribution: str
    notion_lp_ids: List[str] = field(default_factory=list)
    last_updated: Optional[datetime] = None

    @property
    def badge(self) -> str:
        """Get display badge for this relationship."""
        badges = {
            LPStatus.DOCS_SIGNED: "📝 LP - Docs Signed",
            LPStatus.VERBAL_CONFIRM: "📝 LP - Verbal",
            LPStatus.ENGAGEMENT_SENT: "📋 LP - Contacted",
            LPStatus.IN_DATABASE: "📋 LP - In Database",
            LPStatus.DECLINED: "⚠️ Previously declined",
        }
        return badges.get(self.status, "📋 LP")

    @property
    def confidence(self) -> str:
        """Get confidence level string."""
        if self.score >= 0.7:
            return "high"
        elif self.score >= 0.4:
            return "medium"
        else:
            return "low"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _extract_domain_from_url(url: str) -> Optional[str]:
    """Extract normalized domain from URL."""
    if not url:
        return None

    url = url.strip()
    if not url:
        return None

    # Add scheme if missing
    if "://" not in url:
        url = "https://" + url

    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        # Strip auth/port
        if "@" in host:
            host = host.split("@")[-1]
        if ":" in host:
            host = host.split(":")[0]

        # Strip www prefix
        if host.startswith("www."):
            host = host[4:]

        # Validate
        if "." not in host:
            return None

        return host
    except Exception:
        return None


def _extract_domain_from_email(email: str) -> Optional[str]:
    """Extract domain from email address."""
    if not email or "@" not in email:
        return None

    try:
        domain = email.split("@")[1].lower().strip()
        if "." not in domain:
            return None
        return domain
    except Exception:
        return None


# =============================================================================
# NOTION LP SYNC
# =============================================================================

class NotionLPSync:
    """
    Syncs LP records from Notion database.

    Extracts firm-level relationships with status-based scoring.
    """

    def __init__(
        self,
        api_key: str,
        database_id: str,
        transport: Optional[Any] = None,
    ):
        """
        Initialize NotionLPSync.

        Args:
            api_key: Notion API key
            database_id: Notion LP database ID
            transport: Optional custom transport (for testing)
        """
        self.api_key = api_key
        self.database_id = database_id

        if transport:
            self._transport = transport
        else:
            from connectors.notion_transport import NotionTransport
            self._transport = NotionTransport(api_key)

    async def sync(self) -> List[FirmRelationship]:
        """
        Sync LP records from Notion and return firm relationships.

        Returns:
            List of FirmRelationship objects grouped by domain
        """
        # Fetch all LP records
        records = await self._fetch_all_records()

        logger.info(f"Fetched {len(records)} LP records from Notion")

        # Group by domain
        by_domain: Dict[str, List[LPRecord]] = {}
        declined_records: List[LPRecord] = []

        for record in records:
            if record.status == LPStatus.DECLINED:
                # Keep declined records separate
                declined_records.append(record)
                continue

            domain = record.domain
            if domain:
                if domain not in by_domain:
                    by_domain[domain] = []
                by_domain[domain].append(record)

        # Merge records by domain
        relationships = []
        for domain, domain_records in by_domain.items():
            merged = self._merge_lp_records(domain_records)
            merged.domain = domain
            relationships.append(merged)

            if len(domain_records) > 1:
                logger.info(
                    f"Multiple LP records map to {domain}; "
                    f"merged using highest-tier rule"
                )

        # Handle declined records individually (don't let them affect firm score)
        for record in declined_records:
            domain = record.domain
            if domain:
                relationships.append(FirmRelationship(
                    domain=domain,
                    score=0.0,
                    status=LPStatus.DECLINED,
                    attribution=f"via {record.name}",
                    notion_lp_ids=[record.notion_id],
                    last_updated=record.last_updated,
                ))

        logger.info(f"Built {len(relationships)} firm relationships")

        return relationships

    async def _fetch_all_records(self) -> List[LPRecord]:
        """Fetch all LP records with pagination."""
        records = []
        cursor = None

        while True:
            # Build query
            query: Dict[str, Any] = {}
            if cursor:
                query["start_cursor"] = cursor

            # Query database
            response = await self._transport.post(
                f"/databases/{self.database_id}/query",
                json=query,
            )

            # Parse results
            for page in response.get("results", []):
                try:
                    record = self._parse_page(page)
                    if record:
                        records.append(record)
                except Exception as e:
                    logger.warning(f"Failed to parse LP page {page.get('id')}: {e}")

            # Check for more pages
            if response.get("has_more"):
                cursor = response.get("next_cursor")
            else:
                break

        return records

    def _parse_page(self, page: Dict[str, Any]) -> Optional[LPRecord]:
        """Parse a Notion page into an LPRecord."""
        props = page.get("properties", {})

        # Extract name (required)
        name = self._get_title(props.get("Name"))
        if not name:
            return None

        # Extract other fields
        firm = self._get_rich_text(props.get("Firm"))
        email = self._get_email(props.get("Email"))
        website = self._get_url(props.get("Website"))
        status_str = self._get_select(props.get("Status"))
        last_updated = self._get_date(props.get("Last Updated"))

        status = LPStatus.from_notion(status_str)

        return LPRecord(
            notion_id=page["id"],
            name=name,
            firm=firm or "",
            email=email,
            status=status,
            website=website,
            last_updated=last_updated,
        )

    def _merge_lp_records(self, records: List[LPRecord]) -> FirmRelationship:
        """
        Merge multiple LP records from the same firm.

        Rules:
        1. Status tier = highest tier among records
        2. Attribution = concatenated unique names
        3. Recency = max(last_updated)
        4. IDs = all notion_lp_ids preserved
        """
        if not records:
            raise ValueError("Cannot merge empty records list")

        # Sort by tier (highest first)
        sorted_records = sorted(records, key=lambda r: r.status.tier, reverse=True)

        # Use highest tier
        best_record = sorted_records[0]
        status = best_record.status
        score = best_record.score

        # Concatenate unique names
        names = []
        seen_names = set()
        for record in records:
            if record.name and record.name not in seen_names:
                names.append(record.name)
                seen_names.add(record.name)

        attribution = "via " + ", ".join(names) if names else ""

        # Collect all IDs
        notion_lp_ids = [r.notion_id for r in records]

        # Get most recent update
        last_updated = None
        for record in records:
            if record.last_updated:
                if last_updated is None or record.last_updated > last_updated:
                    last_updated = record.last_updated

        return FirmRelationship(
            domain="",  # Set by caller
            score=score,
            status=status,
            attribution=attribution,
            notion_lp_ids=notion_lp_ids,
            last_updated=last_updated,
        )

    # =========================================================================
    # NOTION PROPERTY EXTRACTORS
    # =========================================================================

    @staticmethod
    def _get_title(prop: Optional[Dict]) -> Optional[str]:
        """Extract text from title property."""
        if not prop:
            return None
        title_list = prop.get("title", [])
        if not title_list:
            return None
        return title_list[0].get("text", {}).get("content")

    @staticmethod
    def _get_rich_text(prop: Optional[Dict]) -> Optional[str]:
        """Extract text from rich_text property."""
        if not prop:
            return None
        text_list = prop.get("rich_text", [])
        if not text_list:
            return None
        return text_list[0].get("text", {}).get("content")

    @staticmethod
    def _get_email(prop: Optional[Dict]) -> Optional[str]:
        """Extract email from email property."""
        if not prop:
            return None
        return prop.get("email")

    @staticmethod
    def _get_url(prop: Optional[Dict]) -> Optional[str]:
        """Extract URL from url property."""
        if not prop:
            return None
        return prop.get("url")

    @staticmethod
    def _get_select(prop: Optional[Dict]) -> Optional[str]:
        """Extract value from select property."""
        if not prop:
            return None
        select = prop.get("select")
        if not select:
            return None
        return select.get("name")

    @staticmethod
    def _get_date(prop: Optional[Dict]) -> Optional[datetime]:
        """Extract datetime from date property."""
        if not prop:
            return None
        date_obj = prop.get("date")
        if not date_obj:
            return None
        start = date_obj.get("start")
        if not start:
            return None
        try:
            # Parse ISO format
            if "T" in start:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(start)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    # =========================================================================
    # DOMAIN EXTRACTION (for tests)
    # =========================================================================

    def _extract_domain_from_website(self, website: Optional[str]) -> Optional[str]:
        """Extract domain from website URL (exposed for testing)."""
        return _extract_domain_from_url(website)

    def _extract_domain_from_email(self, email: Optional[str]) -> Optional[str]:
        """Extract domain from email, excluding providers (exposed for testing)."""
        domain = _extract_domain_from_email(email)
        if domain and domain in LP_PROVIDER_BLOCKLIST:
            return None
        return domain
