"""
NotionConnector for Press On Ventures' Venture Pipeline

Tailored to the existing Notion database schema observed:
- Company Name (Title)
- Website (URL) - PRIMARY DEDUP KEY
- Short Description (Text)
- Investment Stage (Select): Pre-Seed, Seed, Seed+
- Status (Select): Diligence, Initial Meeting, Passed, etc.
- Sector (Select): Healthcare, CPG, AI/ML, etc.
- Founder (Text)
- Founder LinkedIn (URL)
- Location (Text)
- Added By (Person)
- Target Raise Amount (Text)
- Post Money Valuation (Text)

New properties to add for Discovery integration:
- Discovery ID (Text) - stable link between systems
- Confidence Score (Number) - thesis fit 0.0-1.0
- Signal Types (Multi-select) - what triggered discovery
- Why Now (Text) - 1-sentence summary
- Source (Select) - add "Discovery Engine" option
"""

import httpx
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
from tenacity import retry, stop_after_attempt, wait_exponential
import asyncio
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS - Match your Notion database exactly
# =============================================================================

class InvestmentStage(str, Enum):
    """Investment stages from your Notion database"""
    PRE_SEED = "Pre-Seed"
    SEED = "Seed"
    SEED_PLUS = "Seed +"
    SERIES_A = "Series A"  # Add if you have this


class DealStatus(str, Enum):
    """Deal statuses from your Notion database"""
    # Active pipeline - suppress from Discovery
    INITIAL_MEETING = "Initial Meeting / ..."
    DILIGENCE = "Diligence"
    COMMITTED = "Committed"
    FUNDED = "Funded"
    
    # Rejected - suppress from Discovery
    PASSED = "Passed"
    
    # Watching - maybe suppress (configurable)
    TRACKING = "Tracking"
    
    # New leads from Discovery
    LEAD = "Lead"  # Add this status if not exists


class Sector(str, Enum):
    """Sectors from your Notion database"""
    HEALTHCARE = "Healthcare"
    CPG = "CPG"
    AI_ML = "AI / ML"
    HUMAN_PERFORMANCE = "Human Performance"
    TRAVEL_HOSPITALITY = "Travel & Hospitality"
    # Add more as needed


class SourceType(str, Enum):
    """Deal source types - add "Discovery Engine" to your Notion"""
    DISCOVERY_ENGINE = "Discovery Engine"
    REFERRAL = "Referral"
    INBOUND = "Inbound"
    CONFERENCE = "Conference"
    COLD_OUTREACH = "Cold Outreach"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ProspectPayload:
    """Payload for pushing a prospect to Notion"""
    # Required fields
    discovery_id: str
    company_name: str
    website: str
    stage: InvestmentStage
    
    # Discovery-generated fields
    confidence_score: float = 0.0
    signal_types: List[str] = field(default_factory=list)
    why_now: str = ""
    
    # Optional enrichment
    short_description: str = ""
    sector: Optional[Sector] = None
    founder_name: str = ""
    founder_linkedin: str = ""
    location: str = ""
    target_raise: str = ""
    
    def idempotency_key(self) -> str:
        """Generate stable key for deduplication"""
        # Use website as primary key, normalized
        normalized = self.website.lower()
        normalized = normalized.replace("https://", "").replace("http://", "")
        normalized = normalized.rstrip("/").split("/")[0]
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]


@dataclass
class SuppressionEntry:
    """Entry in the suppression cache"""
    discovery_id: Optional[str]
    website: str
    status: str
    notion_page_id: str


# =============================================================================
# NOTION CONNECTOR
# =============================================================================

class NotionConnector:
    """
    Integrates Discovery Engine with Press On Ventures' Notion CRM.
    
    Features:
    - Upsert prospects (create or update deals)
    - Suppression list (don't resurface passed/active deals)
    - Rate limit handling (3 req/sec)
    - Caching for efficiency
    """
    
    # Statuses to suppress from Discovery results
    SUPPRESS_STATUSES = [
        "Passed",
        "Diligence", 
        "Initial Meeting / ...",
        "Committed",
        "Funded",
        # "Tracking",  # Uncomment if you want to suppress Tracking too
    ]
    
    def __init__(
        self,
        api_key: str,
        database_id: str,
        cache_ttl_seconds: int = 900,  # 15 minutes
        rate_limit_delay: float = 0.35  # Stay under 3 req/sec
    ):
        self.api_key = api_key
        self.database_id = database_id
        self.base_url = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.rate_limit_delay = rate_limit_delay
        
        # Suppression cache
        self._suppression_cache: Dict[str, SuppressionEntry] = {}
        self._cache_expires: Optional[datetime] = None
        
        # Rate limiting
        self._last_request_time: Optional[datetime] = None
    
    # =========================================================================
    # PUBLIC METHODS
    # =========================================================================
    
    async def upsert_prospect(self, prospect: ProspectPayload) -> Dict[str, Any]:
        """
        Create or update a deal in Notion.
        
        Deduplication logic:
        1. Check by Discovery ID (stable link)
        2. Check by Website (primary business key)
        3. If no match, create new
        
        Returns:
            {"status": "created"|"updated"|"skipped", "page_id": str, "reason": str}
        """
        async with httpx.AsyncClient() as client:
            # 1. Check if already in suppression list (skip if passed/active)
            suppression = await self.get_suppression_list()
            website_key = self._normalize_website(prospect.website)
            
            if f"website:{website_key}" in suppression:
                entry = suppression[f"website:{website_key}"]
                return {
                    "status": "skipped",
                    "page_id": entry.notion_page_id,
                    "reason": f"Already in pipeline with status: {entry.status}"
                }
            
            # 2. Check for existing deal by Discovery ID
            existing = await self._find_by_discovery_id(client, prospect.discovery_id)
            
            # 3. Fallback: check by website
            if not existing:
                existing = await self._find_by_website(client, prospect.website)
            
            if existing:
                # Update existing deal (only Discovery-owned fields)
                result = await self._update_page(client, existing["id"], prospect)
                return {
                    "status": "updated",
                    "page_id": existing["id"],
                    "reason": "Matched existing deal"
                }
            else:
                # Create new deal
                result = await self._create_page(client, prospect)
                return {
                    "status": "created",
                    "page_id": result["id"],
                    "reason": "New deal created"
                }
    
    async def get_suppression_list(self, force_refresh: bool = False) -> Dict[str, SuppressionEntry]:
        """
        Get deals to suppress from Discovery results.
        
        Returns dict keyed by:
        - "discovery:{id}" for Discovery ID matches
        - "website:{normalized}" for website matches
        
        Cached for 15 minutes by default.
        """
        if not force_refresh and self._cache_expires and datetime.utcnow() < self._cache_expires:
            return self._suppression_cache
        
        logger.info("Refreshing suppression cache from Notion...")
        suppression: Dict[str, SuppressionEntry] = {}
        
        async with httpx.AsyncClient() as client:
            for status in self.SUPPRESS_STATUSES:
                pages = await self._query_by_status(client, status)
                
                for page in pages:
                    props = page.get("properties", {})
                    page_id = page["id"]
                    
                    # Extract fields
                    discovery_id = self._extract_text(props.get("Discovery ID", {}))
                    website = props.get("Website", {}).get("url", "")
                    
                    entry = SuppressionEntry(
                        discovery_id=discovery_id,
                        website=website,
                        status=status,
                        notion_page_id=page_id
                    )
                    
                    # Add to cache by both keys
                    if discovery_id:
                        suppression[f"discovery:{discovery_id}"] = entry
                    
                    if website:
                        normalized = self._normalize_website(website)
                        suppression[f"website:{normalized}"] = entry
        
        self._suppression_cache = suppression
        self._cache_expires = datetime.utcnow() + self.cache_ttl
        
        logger.info(f"Suppression cache refreshed: {len(suppression)} entries")
        return suppression
    
    def invalidate_cache(self):
        """Invalidate suppression cache (call when notified of status change)"""
        self._cache_expires = None
        logger.info("Suppression cache invalidated")
    
    async def get_portfolio_companies(self) -> List[Dict[str, str]]:
        """Get list of portfolio companies (Funded status) for exclusion"""
        async with httpx.AsyncClient() as client:
            pages = await self._query_by_status(client, "Funded")
            
            portfolio = []
            for page in pages:
                props = page.get("properties", {})
                portfolio.append({
                    "page_id": page["id"],
                    "company_name": self._extract_title(props.get("Company Name", {})),
                    "website": props.get("Website", {}).get("url", ""),
                    "sector": self._extract_select(props.get("Sector", {}))
                })
            
            return portfolio
    
    # =========================================================================
    # PRIVATE: NOTION API CALLS
    # =========================================================================
    
    async def _rate_limit(self):
        """Enforce rate limit (3 req/sec = 333ms between requests)"""
        if self._last_request_time:
            elapsed = (datetime.utcnow() - self._last_request_time).total_seconds()
            if elapsed < self.rate_limit_delay:
                await asyncio.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = datetime.utcnow()
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _find_by_discovery_id(
        self, 
        client: httpx.AsyncClient, 
        discovery_id: str
    ) -> Optional[Dict]:
        """Query Notion for existing deal by Discovery ID"""
        await self._rate_limit()
        
        response = await client.post(
            f"{self.base_url}/databases/{self.database_id}/query",
            headers=self.headers,
            json={
                "filter": {
                    "property": "Discovery ID",
                    "rich_text": {"equals": discovery_id}
                },
                "page_size": 1
            }
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0] if results else None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _find_by_website(
        self, 
        client: httpx.AsyncClient, 
        website: str
    ) -> Optional[Dict]:
        """Query Notion for existing deal by Website URL"""
        await self._rate_limit()
        
        # Normalize for matching
        normalized = self._normalize_website(website)
        
        response = await client.post(
            f"{self.base_url}/databases/{self.database_id}/query",
            headers=self.headers,
            json={
                "filter": {
                    "property": "Website",
                    "url": {"contains": normalized}
                },
                "page_size": 5  # Check a few in case of partial matches
            }
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        
        # Find exact match (normalized)
        for result in results:
            url = result.get("properties", {}).get("Website", {}).get("url", "")
            if self._normalize_website(url) == normalized:
                return result
        
        return None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _create_page(
        self, 
        client: httpx.AsyncClient, 
        prospect: ProspectPayload
    ) -> Dict:
        """Create new deal page in Notion"""
        await self._rate_limit()
        
        properties = self._build_create_properties(prospect)
        
        response = await client.post(
            f"{self.base_url}/pages",
            headers=self.headers,
            json={
                "parent": {"database_id": self.database_id},
                "properties": properties
            }
        )
        response.raise_for_status()
        
        logger.info(f"Created Notion page for: {prospect.company_name}")
        return response.json()
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _update_page(
        self, 
        client: httpx.AsyncClient, 
        page_id: str,
        prospect: ProspectPayload
    ) -> Dict:
        """Update existing deal page - only Discovery-owned fields"""
        await self._rate_limit()
        
        properties = self._build_update_properties(prospect)
        
        response = await client.patch(
            f"{self.base_url}/pages/{page_id}",
            headers=self.headers,
            json={"properties": properties}
        )
        response.raise_for_status()
        
        logger.info(f"Updated Notion page {page_id} for: {prospect.company_name}")
        return response.json()
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _query_by_status(
        self, 
        client: httpx.AsyncClient, 
        status: str
    ) -> List[Dict]:
        """Query all deals with given status (handles pagination)"""
        all_results = []
        has_more = True
        start_cursor = None
        
        while has_more:
            await self._rate_limit()
            
            payload = {
                "filter": {
                    "property": "Status",
                    "select": {"equals": status}
                },
                "page_size": 100
            }
            if start_cursor:
                payload["start_cursor"] = start_cursor
            
            response = await client.post(
                f"{self.base_url}/databases/{self.database_id}/query",
                headers=self.headers,
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            
            all_results.extend(data.get("results", []))
            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")
        
        return all_results
    
    # =========================================================================
    # PRIVATE: PROPERTY BUILDERS
    # =========================================================================
    
    def _build_create_properties(self, prospect: ProspectPayload) -> Dict:
        """Build Notion properties for creating a new deal"""
        props = {
            # Title (required)
            "Company Name": {
                "title": [{"text": {"content": prospect.company_name}}]
            },
            
            # Core fields
            "Website": {"url": prospect.website},
            "Investment Stage": {"select": {"name": prospect.stage.value}},
            "Status": {"select": {"name": "Lead"}},  # New deals start as Lead
            
            # Discovery fields
            "Discovery ID": {
                "rich_text": [{"text": {"content": prospect.discovery_id}}]
            },
            "Confidence Score": {"number": round(prospect.confidence_score, 2)},
        }
        
        # Optional: Source (add "Discovery Engine" to your Notion select options)
        # Uncomment when you've added the Source property
        # props["Source"] = {"select": {"name": "Discovery Engine"}}
        
        # Optional fields (only if provided)
        if prospect.short_description:
            props["Short Description"] = {
                "rich_text": [{"text": {"content": prospect.short_description[:2000]}}]
            }
        
        if prospect.sector:
            props["Sector"] = {"select": {"name": prospect.sector.value}}
        
        if prospect.founder_name:
            props["Founder"] = {
                "rich_text": [{"text": {"content": prospect.founder_name}}]
            }
        
        if prospect.founder_linkedin:
            props["Founder LinkedIn"] = {"url": prospect.founder_linkedin}
        
        if prospect.location:
            props["Location"] = {
                "rich_text": [{"text": {"content": prospect.location}}]
            }
        
        if prospect.target_raise:
            props["Target Raise Amount"] = {
                "rich_text": [{"text": {"content": prospect.target_raise}}]
            }
        
        if prospect.signal_types:
            props["Signal Types"] = {
                "multi_select": [{"name": s} for s in prospect.signal_types[:5]]
            }
        
        if prospect.why_now:
            props["Why Now"] = {
                "rich_text": [{"text": {"content": prospect.why_now[:2000]}}]
            }
        
        return props
    
    def _build_update_properties(self, prospect: ProspectPayload) -> Dict:
        """Build Notion properties for updating - only Discovery-owned fields"""
        props = {
            # Always update Discovery ID to ensure link
            "Discovery ID": {
                "rich_text": [{"text": {"content": prospect.discovery_id}}]
            },
            "Confidence Score": {"number": round(prospect.confidence_score, 2)},
        }
        
        # Update optional Discovery fields if provided
        if prospect.signal_types:
            props["Signal Types"] = {
                "multi_select": [{"name": s} for s in prospect.signal_types[:5]]
            }
        
        if prospect.why_now:
            props["Why Now"] = {
                "rich_text": [{"text": {"content": prospect.why_now[:2000]}}]
            }
        
        # DO NOT update these on existing deals (user may have edited):
        # - Company Name
        # - Website
        # - Status
        # - Investment Stage
        # - Sector
        # - Founder
        # - etc.
        
        return props
    
    # =========================================================================
    # PRIVATE: HELPERS
    # =========================================================================
    
    @staticmethod
    def _normalize_website(url: str) -> str:
        """Normalize website URL for matching"""
        if not url:
            return ""
        normalized = url.lower()
        normalized = normalized.replace("https://", "").replace("http://", "")
        normalized = normalized.replace("www.", "")
        normalized = normalized.rstrip("/").split("/")[0]
        return normalized
    
    @staticmethod
    def _extract_text(prop: Dict) -> Optional[str]:
        """Extract text content from Notion rich_text property"""
        rich_text = prop.get("rich_text", [])
        if rich_text:
            return rich_text[0].get("text", {}).get("content", "")
        return None
    
    @staticmethod
    def _extract_title(prop: Dict) -> str:
        """Extract text from Notion title property"""
        title = prop.get("title", [])
        if title:
            return title[0].get("text", {}).get("content", "")
        return ""
    
    @staticmethod
    def _extract_select(prop: Dict) -> Optional[str]:
        """Extract value from Notion select property"""
        select = prop.get("select")
        if select:
            return select.get("name")
        return None


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_connector_from_env() -> NotionConnector:
    """Create connector from environment variables"""
    import os
    
    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("NOTION_DATABASE_ID")
    
    if not api_key or not database_id:
        raise ValueError(
            "Missing required environment variables: "
            "NOTION_API_KEY, NOTION_DATABASE_ID"
        )
    
    return NotionConnector(api_key=api_key, database_id=database_id)


# =============================================================================
# TESTING / CLI
# =============================================================================

async def test_connection():
    """Test Notion connection and list sample deals"""
    connector = create_connector_from_env()
    
    print("Testing Notion connection...")
    
    # Test suppression list
    suppression = await connector.get_suppression_list(force_refresh=True)
    print(f"Suppression list: {len(suppression)} entries")
    
    # Show sample
    for key, entry in list(suppression.items())[:5]:
        print(f"  - {key}: {entry.status}")
    
    # Test portfolio
    portfolio = await connector.get_portfolio_companies()
    print(f"\nPortfolio companies: {len(portfolio)}")
    for co in portfolio[:3]:
        print(f"  - {co['company_name']}: {co['website']}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_connection())
