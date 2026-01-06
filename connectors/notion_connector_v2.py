"""
NotionConnector for Press On Ventures' Venture Pipeline

CORRECTED to match actual Notion database schema:

Investment Stage options:
- Pre-Seed, Seed, Seed +, Series A, Series B, Series C, Series D

Status options:
- Source, Initial Meeting / Call, Dilligence, Tracking, Committed, Funded, Passed, Lost

Required Notion properties to add:
- Discovery ID (Text) - stable link between systems
- Canonical Key (Text) - deterministic dedupe key (domain:, companies_house:, github_org:)
- Confidence Score (Number) - thesis fit 0.0-1.0
- Signal Types (Multi-select) - what triggered discovery
- Why Now (Text) - 1-sentence summary
"""

import httpx
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from tenacity import retry, stop_after_attempt, wait_exponential
import asyncio
import logging

# Import canonical key helpers from shared module
from utils.canonical_keys import (
    build_canonical_key,
    build_canonical_key_candidates,
    canonical_key_from_external_refs,
    canonical_key_from_signal,
    normalize_domain,
    is_strong_key,
    CanonicalKeyResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS - EXACT MATCH TO YOUR NOTION DATABASE
# =============================================================================

class InvestmentStage(str, Enum):
    """Investment stages - EXACT strings from Notion"""
    PRE_SEED = "Pre-Seed"
    SEED = "Seed"
    SEED_PLUS = "Seed +"
    SERIES_A = "Series A"
    SERIES_B = "Series B"
    SERIES_C = "Series C"
    SERIES_D = "Series D"


class DealStatus(str, Enum):
    """Deal statuses - EXACT strings from Notion (note: Dilligence has double L)"""
    SOURCE = "Source"
    INITIAL_MEETING = "Initial Meeting / Call"
    DILIGENCE = "Dilligence"  # Misspelled in Notion - must match exactly
    TRACKING = "Tracking"
    COMMITTED = "Committed"
    FUNDED = "Funded"
    PASSED = "Passed"
    LOST = "Lost"


class Sector(str, Enum):
    """Sectors from your Notion database"""
    HEALTHCARE = "Healthcare"
    CPG = "CPG"
    AI_ML = "AI / ML"
    HUMAN_PERFORMANCE = "Human Performance"
    TRAVEL_HOSPITALITY = "Travel & Hospitality"
    # Add more as you see them in your DB


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ProspectPayload:
    """Payload for pushing a prospect to Notion"""
    # Required fields
    discovery_id: str
    company_name: str
    canonical_key: str  # e.g., "domain:acme.ai" or "companies_house:12345678"
    stage: InvestmentStage
    
    # Optional: status override (defaults to "Source")
    status: Optional[str] = None
    
    # Optional identity (stealth prospects may not have website yet)
    website: str = ""
    
    # All canonical key candidates for multi-key lookup
    canonical_key_candidates: List[str] = field(default_factory=list)
    
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
    
    # External refs for canonical key generation
    external_refs: Dict[str, str] = field(default_factory=dict)
    
    def __post_init__(self):
        """Generate canonical key candidates if not provided"""
        if not self.canonical_key_candidates and self.external_refs:
            result = canonical_key_from_external_refs(
                self.external_refs,
                fallback_company_name=self.company_name,
                fallback_region=self.location
            )
            self.canonical_key_candidates = result.candidates
            if not self.canonical_key:
                self.canonical_key = result.canonical_key
    
    def idempotency_key(self) -> str:
        """Generate stable key for deduplication"""
        # Prefer canonical_key; fallback to website; fallback to name
        base = (self.canonical_key or "").strip().lower()
        if not base and self.website:
            base = normalize_domain(self.website)
        if not base:
            base = (self.company_name or "").strip().lower()
        return hashlib.sha256(base.encode()).hexdigest()[:16]


@dataclass
class SuppressionEntry:
    """Entry in the suppression cache"""
    discovery_id: Optional[str]
    canonical_key: Optional[str]
    website: str
    status: str
    notion_page_id: str


@dataclass
class ValidationResult:
    """Result of schema validation"""
    valid: bool
    missing_properties: List[str] = field(default_factory=list)
    missing_optional_properties: List[str] = field(default_factory=list)
    missing_status_options: List[str] = field(default_factory=list)
    missing_stage_options: List[str] = field(default_factory=list)
    wrong_property_types: Dict[str, str] = field(default_factory=dict)  # {prop_name: expected_type}
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __str__(self) -> str:
        """Human-readable validation report"""
        if self.valid:
            return "Schema validation PASSED - all required properties and options present"

        lines = ["Schema validation FAILED:"]

        if self.missing_properties:
            lines.append(f"\nMissing REQUIRED properties:")
            for prop in self.missing_properties:
                lines.append(f"  - {prop}")

        if self.missing_optional_properties:
            lines.append(f"\nMissing optional properties (recommended):")
            for prop in self.missing_optional_properties:
                lines.append(f"  - {prop}")

        if self.wrong_property_types:
            lines.append(f"\nWrong property types:")
            for prop, expected in self.wrong_property_types.items():
                lines.append(f"  - {prop}: expected {expected}")

        if self.missing_status_options:
            lines.append(f"\nMissing Status select options:")
            for opt in self.missing_status_options:
                lines.append(f"  - {opt}")

        if self.missing_stage_options:
            lines.append(f"\nMissing Investment Stage select options:")
            for opt in self.missing_stage_options:
                lines.append(f"  - {opt}")

        lines.append("\nFix these issues in Notion database settings, then retry.")
        return "\n".join(lines)


# =============================================================================
# NOTION CONNECTOR
# =============================================================================

class NotionConnector:
    """
    Integrates Discovery Engine with Press On Ventures' Notion CRM.
    
    Features:
    - Upsert prospects (create or update deals)
    - Canonical key deduplication (works for stealth companies without websites)
    - Single-query suppression (efficient OR filter)
    - Schema preflight validation (fail fast on drift)
    - Rate limit handling (3 req/sec)
    """
    
    # ==========================================================================
    # CONFIGURATION - MATCH YOUR NOTION EXACTLY
    # ==========================================================================
    
    # Statuses to suppress from Discovery results
    SUPPRESS_STATUSES: List[str] = [
        "Passed",
        "Lost",
        "Funded",
        "Committed",
        "Dilligence",  # Double L - matches your Notion
        "Initial Meeting / Call",
        "Source",
        # "Tracking",  # Uncomment to suppress tracking deals too
    ]
    
    # Hard suppress = don't even update discovery fields
    HARD_SUPPRESS_STATUSES: Set[str] = {"Passed", "Lost"}
    
    # Default status for new deals from Discovery
    DEFAULT_NEW_STATUS = "Source"
    
    # Notion property names (must match exactly)
    PROP_COMPANY_NAME = "Company Name"
    PROP_WEBSITE = "Website"
    PROP_INVESTMENT_STAGE = "Investment Stage"
    PROP_STATUS = "Status"
    PROP_SHORT_DESCRIPTION = "Short Description"
    PROP_SECTOR = "Sector"
    PROP_FOUNDER = "Founder"
    PROP_FOUNDER_LINKEDIN = "Founder LinkedIn"
    PROP_LOCATION = "Location"
    PROP_TARGET_RAISE = "Target Raise Amount"
    
    # Discovery-specific properties (you need to add these to Notion)
    PROP_DISCOVERY_ID = "Discovery ID"
    PROP_CANONICAL_KEY = "Canonical Key"
    PROP_CONFIDENCE_SCORE = "Confidence Score"
    PROP_SIGNAL_TYPES = "Signal Types"
    PROP_WHY_NOW = "Why Now"
    
    # Expected select options (for preflight validation)
    EXPECTED_STATUSES: Set[str] = {
        "Source", "Initial Meeting / Call", "Dilligence", "Tracking",
        "Committed", "Funded", "Passed", "Lost"
    }
    EXPECTED_STAGES: Set[str] = {
        "Pre-Seed", "Seed", "Seed +", "Series A", "Series B", "Series C", "Series D"
    }
    
    def __init__(
        self,
        api_key: str,
        database_id: str,
        cache_ttl_seconds: int = 900,  # 15 minutes
        rate_limit_delay: float = 0.35,  # Stay under 3 req/sec
        validate_schema_on_init: bool = False  # Set to True to fail fast on schema issues
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
        self.validate_schema_on_init = validate_schema_on_init

        # Suppression cache
        self._suppression_cache: Dict[str, SuppressionEntry] = {}
        self._cache_expires: Optional[datetime] = None

        # Schema cache
        self._schema_cache: Optional[Dict[str, Any]] = None
        self._schema_expires: Optional[datetime] = None
        self._schema_ttl = timedelta(hours=6)

        # Rate limiting
        self._last_request_time: Optional[datetime] = None

        # Validate schema on init if requested
        if validate_schema_on_init:
            asyncio.run(self._validate_schema_on_init())
    
    # =========================================================================
    # PUBLIC METHODS
    # =========================================================================
    
    async def upsert_prospect(self, prospect: ProspectPayload) -> Dict[str, Any]:
        """
        Create or update a deal in Notion.
        
        Deduplication logic (in order):
        1. Check suppression list using ALL canonical key candidates
        2. Check by Discovery ID (stable link)
        3. Check by all Canonical Key candidates (deterministic - works for stealth)
        4. Check by Website (fallback for legacy deals)
        5. If no match, create new
        
        Returns:
            {"status": "created"|"updated"|"skipped", "page_id": str, "reason": str}
        """
        async with httpx.AsyncClient() as client:
            # Preflight: validate schema
            await self._ensure_schema(client, strict=True)
            
            # 1. Build all keys to check (discovery ID + all canonical candidates + website)
            suppression = await self.get_suppression_list()
            keys_to_check: List[str] = []
            
            if prospect.discovery_id:
                keys_to_check.append(f"discovery:{prospect.discovery_id}")
            
            # Add ALL canonical key candidates (from shared module)
            for candidate in prospect.canonical_key_candidates:
                keys_to_check.append(f"canonical:{candidate}")
            
            # Fallback: single canonical key if no candidates
            if not prospect.canonical_key_candidates and prospect.canonical_key:
                keys_to_check.append(f"canonical:{prospect.canonical_key}")
            
            if prospect.website:
                keys_to_check.append(f"website:{normalize_domain(prospect.website)}")
            
            matched_entry: Optional[SuppressionEntry] = None
            matched_key: Optional[str] = None
            
            for k in keys_to_check:
                if k in suppression:
                    matched_entry = suppression[k]
                    matched_key = k
                    break
            
            if matched_entry:
                # Hard suppress = don't touch at all
                if matched_entry.status in self.HARD_SUPPRESS_STATUSES:
                    return {
                        "status": "skipped",
                        "page_id": matched_entry.notion_page_id,
                        "reason": f"Hard suppressed ({matched_entry.status}) via {matched_key}"
                    }
                
                # Soft suppress = update discovery fields only
                await self._update_page(client, matched_entry.notion_page_id, prospect)
                return {
                    "status": "updated",
                    "page_id": matched_entry.notion_page_id,
                    "reason": f"Updated in-pipeline deal ({matched_entry.status}) via {matched_key}"
                }
            
            # 2. Check for existing deal by Discovery ID
            existing = await self._find_by_discovery_id(client, prospect.discovery_id)
            
            # 3. Check by ALL Canonical Key candidates
            if not existing:
                for candidate in (prospect.canonical_key_candidates or [prospect.canonical_key] if prospect.canonical_key else []):
                    existing = await self._find_by_canonical_key(client, candidate)
                    if existing:
                        break
            
            # 4. Fallback: check by Website
            if not existing and prospect.website:
                existing = await self._find_by_website(client, prospect.website)
            
            if existing:
                result = await self._update_page(client, existing["id"], prospect)
                return {
                    "status": "updated",
                    "page_id": existing["id"],
                    "reason": "Matched existing deal"
                }
            else:
                result = await self._create_page(client, prospect)
                return {
                    "status": "created",
                    "page_id": result["id"],
                    "reason": "New deal created"
                }
    
    async def get_suppression_list(self, force_refresh: bool = False) -> Dict[str, SuppressionEntry]:
        """
        Get deals to suppress from Discovery results.
        
        Uses single OR query for efficiency (not one query per status).
        
        Returns dict keyed by:
        - "discovery:{id}"
        - "canonical:{normalized_key}"
        - "website:{normalized}"
        """
        if not force_refresh and self._cache_expires and datetime.utcnow() < self._cache_expires:
            return self._suppression_cache
        
        logger.info("Refreshing suppression cache from Notion...")
        suppression: Dict[str, SuppressionEntry] = {}
        
        async with httpx.AsyncClient() as client:
            await self._ensure_schema(client, strict=False)
            
            # Single query with OR filter
            pages = await self._query_by_statuses(client, self.SUPPRESS_STATUSES)
            
            for page in pages:
                props = page.get("properties", {})
                page_id = page["id"]
                
                status = self._extract_select(props.get(self.PROP_STATUS, {})) or ""
                discovery_id = self._extract_text(props.get(self.PROP_DISCOVERY_ID, {}))
                canonical_key = self._extract_text(props.get(self.PROP_CANONICAL_KEY, {}))
                website = props.get(self.PROP_WEBSITE, {}).get("url", "") or ""
                
                entry = SuppressionEntry(
                    discovery_id=discovery_id,
                    canonical_key=canonical_key,
                    website=website,
                    status=status,
                    notion_page_id=page_id
                )
                
                # Add to cache by all available keys
                if discovery_id:
                    suppression[f"discovery:{discovery_id}"] = entry
                if canonical_key:
                    suppression[f"canonical:{self._normalize_canonical_key(canonical_key)}"] = entry
                if website:
                    suppression[f"website:{self._normalize_website(website)}"] = entry
        
        self._suppression_cache = suppression
        self._cache_expires = datetime.utcnow() + self.cache_ttl
        
        logger.info(f"Suppression cache refreshed: {len(suppression)} entries")
        return suppression
    
    def invalidate_cache(self):
        """Invalidate suppression cache (call when notified of status change)"""
        self._cache_expires = None
        logger.info("Suppression cache invalidated")

    async def get_portfolio_companies(self) -> List[Dict[str, str]]:
        """Get list of portfolio companies (Funded status)"""
        async with httpx.AsyncClient() as client:
            pages = await self._query_by_statuses(client, ["Funded"])

            portfolio = []
            for page in pages:
                props = page.get("properties", {})
                portfolio.append({
                    "page_id": page["id"],
                    "company_name": self._extract_title(props.get(self.PROP_COMPANY_NAME, {})),
                    "website": props.get(self.PROP_WEBSITE, {}).get("url", ""),
                    "sector": self._extract_select(props.get(self.PROP_SECTOR, {}))
                })

            return portfolio

    async def validate_schema(self, force_refresh: bool = False) -> ValidationResult:
        """
        Validate that the Notion database schema matches expected structure.

        Checks:
        - All required properties exist
        - Property types match expectations
        - Select options include all required values (Status, Investment Stage)
        - Optional properties are present (warnings only)

        Args:
            force_refresh: If True, bypass cache and fetch fresh schema

        Returns:
            ValidationResult with details of any schema issues

        Example:
            >>> connector = NotionConnector(api_key, database_id)
            >>> result = await connector.validate_schema()
            >>> if not result.valid:
            >>>     print(result)  # Human-readable report
            >>>     raise ValueError("Notion schema validation failed")
        """
        async with httpx.AsyncClient() as client:
            schema = await self._get_database_schema(client, force_refresh=force_refresh)

        props = schema.get("properties", {})

        # Define required properties with expected types
        required_props_with_types = {
            self.PROP_COMPANY_NAME: "title",
            self.PROP_STATUS: "select",
            self.PROP_INVESTMENT_STAGE: "select",
            self.PROP_DISCOVERY_ID: "rich_text",
            self.PROP_CANONICAL_KEY: "rich_text",
            self.PROP_CONFIDENCE_SCORE: "number",
        }

        # Optional but recommended
        optional_props_with_types = {
            self.PROP_WEBSITE: "url",
            self.PROP_SIGNAL_TYPES: "multi_select",
            self.PROP_WHY_NOW: "rich_text",
        }

        # Check for missing properties
        missing_properties = []
        missing_optional_properties = []
        wrong_property_types = {}

        # Validate required properties
        for prop_name, expected_type in required_props_with_types.items():
            if prop_name not in props:
                missing_properties.append(prop_name)
            else:
                actual_type = props[prop_name].get("type")
                if actual_type != expected_type:
                    wrong_property_types[prop_name] = expected_type

        # Check optional properties
        for prop_name, expected_type in optional_props_with_types.items():
            if prop_name not in props:
                missing_optional_properties.append(prop_name)
            else:
                actual_type = props[prop_name].get("type")
                if actual_type != expected_type:
                    wrong_property_types[prop_name] = expected_type

        # Check select options
        def _get_select_options(prop_name: str) -> Set[str]:
            p = props.get(prop_name, {})
            if p.get("type") == "select":
                options = p.get("select", {}).get("options") or []
                return {o.get("name") for o in options if o.get("name")}
            return set()

        status_opts = _get_select_options(self.PROP_STATUS)
        stage_opts = _get_select_options(self.PROP_INVESTMENT_STAGE)

        missing_status_options = sorted(self.EXPECTED_STATUSES - status_opts) if status_opts else []
        missing_stage_options = sorted(self.EXPECTED_STAGES - stage_opts) if stage_opts else []

        # Build result
        valid = (
            not missing_properties
            and not wrong_property_types
            and not missing_status_options
            and not missing_stage_options
        )

        result = ValidationResult(
            valid=valid,
            missing_properties=missing_properties,
            missing_optional_properties=missing_optional_properties,
            missing_status_options=missing_status_options,
            missing_stage_options=missing_stage_options,
            wrong_property_types=wrong_property_types,
        )

        if not valid:
            logger.error(f"Schema validation failed:\n{result}")
        else:
            logger.info("Schema validation passed")
            if missing_optional_properties:
                logger.warning(f"Optional properties missing: {missing_optional_properties}")

        return result
    
    # =========================================================================
    # SCHEMA VALIDATION (PRIVATE)
    # =========================================================================

    async def _validate_schema_on_init(self) -> None:
        """Validate schema during initialization - raises on failure"""
        result = await self.validate_schema(force_refresh=True)
        if not result.valid:
            raise ValueError(f"Notion schema validation failed on init:\n{result}")

    async def _get_database_schema(
        self,
        client: httpx.AsyncClient,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """Fetch and cache Notion database schema"""
        if (not force_refresh and self._schema_cache and self._schema_expires
                and datetime.utcnow() < self._schema_expires):
            return self._schema_cache

        await self._rate_limit()
        resp = await client.get(
            f"{self.base_url}/databases/{self.database_id}",
            headers=self.headers
        )
        resp.raise_for_status()
        self._schema_cache = resp.json()
        self._schema_expires = datetime.utcnow() + self._schema_ttl
        return self._schema_cache

    async def _ensure_schema(self, client: httpx.AsyncClient, strict: bool = True) -> None:
        """
        Preflight validation: fail fast if required properties/options are missing.

        This prevents silent failures when Notion schema drifts.

        NOTE: This method now uses validate_schema() internally for consistency.
        """
        # Use the public validate_schema method for consistency
        result = await self.validate_schema(force_refresh=False)

        if strict and not result.valid:
            raise ValueError(f"Notion schema preflight FAILED.\n{result}")
    
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
        if not discovery_id:
            return None
            
        await self._rate_limit()
        
        response = await client.post(
            f"{self.base_url}/databases/{self.database_id}/query",
            headers=self.headers,
            json={
                "filter": {
                    "property": self.PROP_DISCOVERY_ID,
                    "rich_text": {"equals": discovery_id}
                },
                "page_size": 1
            }
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0] if results else None
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _find_by_canonical_key(
        self,
        client: httpx.AsyncClient,
        canonical_key: str
    ) -> Optional[Dict]:
        """Query Notion for existing deal by Canonical Key"""
        if not canonical_key:
            return None
            
        await self._rate_limit()
        ck = self._normalize_canonical_key(canonical_key)
        
        response = await client.post(
            f"{self.base_url}/databases/{self.database_id}/query",
            headers=self.headers,
            json={
                "filter": {
                    "property": self.PROP_CANONICAL_KEY,
                    "rich_text": {"equals": ck}
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
        if not website:
            return None
            
        await self._rate_limit()
        normalized = self._normalize_website(website)
        
        response = await client.post(
            f"{self.base_url}/databases/{self.database_id}/query",
            headers=self.headers,
            json={
                "filter": {
                    "property": self.PROP_WEBSITE,
                    "url": {"contains": normalized}
                },
                "page_size": 5
            }
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        
        # Find exact match (normalized)
        for result in results:
            url = result.get("properties", {}).get(self.PROP_WEBSITE, {}).get("url", "")
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
    async def _query_by_statuses(
        self,
        client: httpx.AsyncClient,
        statuses: List[str]
    ) -> List[Dict]:
        """
        Query all deals matching ANY of the given statuses.
        
        Uses single OR filter for efficiency (not one query per status).
        """
        if not statuses:
            return []
        
        all_results: List[Dict] = []
        has_more = True
        start_cursor = None
        
        # Build OR filter
        status_filters = [
            {"property": self.PROP_STATUS, "select": {"equals": s}}
            for s in statuses
        ]
        
        while has_more:
            await self._rate_limit()
            
            payload: Dict[str, Any] = {
                "filter": {"or": status_filters},
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
    # PROPERTY BUILDERS
    # =========================================================================
    
    def _build_create_properties(self, prospect: ProspectPayload) -> Dict:
        """Build Notion properties for creating a new deal"""
        props = {
            # Title (required)
            self.PROP_COMPANY_NAME: {
                "title": [{"text": {"content": prospect.company_name}}]
            },
            
            # Core fields
            self.PROP_INVESTMENT_STAGE: {"select": {"name": prospect.stage.value}},
            self.PROP_STATUS: {"select": {"name": prospect.status or self.DEFAULT_NEW_STATUS}},
            
            # Identity fields
            self.PROP_CANONICAL_KEY: {
                "rich_text": [{"text": {"content": self._normalize_canonical_key(prospect.canonical_key)}}]
            },
            self.PROP_DISCOVERY_ID: {
                "rich_text": [{"text": {"content": prospect.discovery_id}}]
            },
            
            # Discovery fields
            self.PROP_CONFIDENCE_SCORE: {"number": round(prospect.confidence_score, 2)},
        }
        
        # Website is optional (stealth companies may not have one yet)
        if prospect.website:
            props[self.PROP_WEBSITE] = {"url": prospect.website}
        
        # Optional fields
        if prospect.short_description:
            props[self.PROP_SHORT_DESCRIPTION] = {
                "rich_text": [{"text": {"content": prospect.short_description[:2000]}}]
            }
        
        if prospect.sector:
            props[self.PROP_SECTOR] = {"select": {"name": prospect.sector.value}}
        
        if prospect.founder_name:
            props[self.PROP_FOUNDER] = {
                "rich_text": [{"text": {"content": prospect.founder_name}}]
            }
        
        if prospect.founder_linkedin:
            props[self.PROP_FOUNDER_LINKEDIN] = {"url": prospect.founder_linkedin}
        
        if prospect.location:
            props[self.PROP_LOCATION] = {
                "rich_text": [{"text": {"content": prospect.location}}]
            }
        
        if prospect.target_raise:
            props[self.PROP_TARGET_RAISE] = {
                "rich_text": [{"text": {"content": prospect.target_raise}}]
            }
        
        if prospect.signal_types:
            props[self.PROP_SIGNAL_TYPES] = {
                "multi_select": [{"name": s} for s in prospect.signal_types[:5]]
            }
        
        if prospect.why_now:
            props[self.PROP_WHY_NOW] = {
                "rich_text": [{"text": {"content": prospect.why_now[:2000]}}]
            }
        
        return props
    
    def _build_update_properties(self, prospect: ProspectPayload) -> Dict:
        """Build Notion properties for updating - only Discovery-owned fields"""
        props = {
            # Always update Discovery ID and Canonical Key to ensure link
            self.PROP_DISCOVERY_ID: {
                "rich_text": [{"text": {"content": prospect.discovery_id}}]
            },
            self.PROP_CANONICAL_KEY: {
                "rich_text": [{"text": {"content": self._normalize_canonical_key(prospect.canonical_key)}}]
            },
            self.PROP_CONFIDENCE_SCORE: {"number": round(prospect.confidence_score, 2)},
        }
        
        # Update optional Discovery fields if provided
        if prospect.signal_types:
            props[self.PROP_SIGNAL_TYPES] = {
                "multi_select": [{"name": s} for s in prospect.signal_types[:5]]
            }
        
        if prospect.why_now:
            props[self.PROP_WHY_NOW] = {
                "rich_text": [{"text": {"content": prospect.why_now[:2000]}}]
            }
        
        # DO NOT update user-editable fields:
        # - Company Name, Website, Status, Investment Stage
        # - Sector, Founder, Location, etc.
        
        return props
    
    # =========================================================================
    # HELPERS
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
    def _normalize_canonical_key(key: str) -> str:
        """Normalize canonical key for matching"""
        return (key or "").strip().lower()
    
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
    """Test Notion connection with schema preflight"""
    connector = create_connector_from_env()

    print("Testing Notion connection...")
    print("=" * 50)

    # Test schema validation
    print("\n1. Schema Validation")
    print("-" * 50)
    try:
        result = await connector.validate_schema(force_refresh=True)
        if result.valid:
            print("✅ Schema validation passed")
        else:
            print(f"❌ Schema validation failed:\n{result}")
            return
    except Exception as e:
        print(f"❌ Schema validation error: {e}")
        return

    # Test suppression list
    print("\n2. Suppression List")
    print("-" * 50)
    suppression = await connector.get_suppression_list(force_refresh=True)
    print(f"✅ Suppression list: {len(suppression)} entries")

    # Show sample by status
    by_status: Dict[str, int] = {}
    for entry in suppression.values():
        by_status[entry.status] = by_status.get(entry.status, 0) + 1

    print("\nSuppression by status:")
    for status, count in sorted(by_status.items()):
        print(f"  - {status}: {count}")

    # Test portfolio
    print("\n3. Portfolio Companies")
    print("-" * 50)
    portfolio = await connector.get_portfolio_companies()
    print(f"✅ Portfolio companies: {len(portfolio)}")
    if portfolio:
        print("\nSample companies:")
        for co in portfolio[:3]:
            print(f"  - {co['company_name']}")

    print("\n" + "=" * 50)
    print("All tests completed successfully!")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_connection())
