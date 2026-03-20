"""Per-source signal extraction registry for KG ETL.

Each extractor function takes a signal row dict and returns structured
attributes (company properties, sector classification, location) for
KG node/edge materialization.

Source priority for conflict resolution:
  crunchbase > sec_edgar > companies_house > jobs > news > hn > whois > github
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# Source priority: higher = more trusted
SOURCE_PRIORITY: Dict[str, int] = {
    "crunchbase": 100,
    "sec_edgar": 90,
    "companies_house": 80,
    "linkedin": 75,
    "job_postings": 70,
    "product_hunt": 65,
    "news_api": 60,
    "rss_feeds": 55,
    "hacker_news": 50,
    "domain_whois": 40,
    "github": 30,
    "github_activity": 25,
    "arxiv": 20,
}


@dataclass
class ExtractedAttrs:
    """Attributes extracted from a signal's raw_data."""
    company_name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    stage: Optional[str] = None
    employees: Optional[int] = None
    founded_year: Optional[int] = None
    sectors: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    source_api: str = ""
    priority: int = 0
    claims: Dict[str, Any] = field(default_factory=dict)


def _safe_json(raw_data: Any) -> Dict[str, Any]:
    """Parse raw_data JSON string or return dict as-is."""
    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(raw_data, dict):
        return raw_data
    return {}


# ---------------------------------------------------------------------------
# SIC code -> sector mapping
# ---------------------------------------------------------------------------

_SIC_SECTOR_MAP: Dict[str, str] = {
    # Food & beverage
    "20": "cpg", "2011": "cpg", "2013": "cpg", "2015": "cpg",
    "2021": "cpg", "2022": "cpg", "2023": "cpg", "2024": "cpg",
    "2026": "cpg", "2032": "cpg", "2033": "cpg", "2034": "cpg",
    "2035": "cpg", "2037": "cpg", "2038": "cpg", "2041": "cpg",
    "2043": "cpg", "2044": "cpg", "2045": "cpg", "2046": "cpg",
    "2047": "cpg", "2048": "cpg", "2051": "cpg", "2052": "cpg",
    "2053": "cpg", "2061": "cpg", "2062": "cpg", "2063": "cpg",
    "2064": "cpg", "2066": "cpg", "2067": "cpg", "2068": "cpg",
    "2082": "cpg", "2083": "cpg", "2084": "cpg", "2085": "cpg",
    "2086": "cpg", "2087": "cpg",
    # Beauty/personal care
    "2842": "cpg", "2844": "cpg",
    # Health tech / fitness / wellness
    "80": "health_tech", "8011": "health_tech", "8021": "health_tech",
    "8042": "health_tech", "8049": "health_tech", "8082": "health_tech",
    "8093": "health_tech", "7941": "health_tech", "7991": "health_tech",
    # Travel & hospitality
    "70": "travel", "7011": "travel", "7021": "travel",
    "4512": "travel", "4522": "travel", "4581": "travel",
    "5812": "travel", "5813": "travel",
    # Marketplaces
    "5961": "marketplace", "7311": "marketplace",
}


def _sic_to_sector(sic_code: str) -> Optional[str]:
    """Map SIC code to thesis sector."""
    if not sic_code:
        return None
    sic = str(sic_code).strip()
    if sic in _SIC_SECTOR_MAP:
        return _SIC_SECTOR_MAP[sic]
    if len(sic) >= 2 and sic[:2] in _SIC_SECTOR_MAP:
        return _SIC_SECTOR_MAP[sic[:2]]
    return None


# ---------------------------------------------------------------------------
# GitHub topic -> sector mapping
# ---------------------------------------------------------------------------

_TOPIC_SECTOR_KEYWORDS: Dict[str, str] = {
    "food": "cpg", "beverage": "cpg", "beauty": "cpg",
    "cosmetics": "cpg", "skincare": "cpg", "nutrition": "cpg",
    "health": "health_tech", "fitness": "health_tech",
    "wellness": "health_tech", "mental-health": "health_tech",
    "healthcare": "health_tech", "medtech": "health_tech",
    "travel": "travel", "hospitality": "travel", "hotel": "travel",
    "booking": "travel", "tourism": "travel",
    "marketplace": "marketplace", "ecommerce": "marketplace",
    "e-commerce": "marketplace",
}


def _topics_to_sectors(topics: List[str]) -> List[str]:
    sectors = set()
    for topic in topics:
        t = topic.lower().strip()
        if t in _TOPIC_SECTOR_KEYWORDS:
            sectors.add(_TOPIC_SECTOR_KEYWORDS[t])
    return sorted(sectors)


# ---------------------------------------------------------------------------
# Crunchbase category -> sector mapping
# ---------------------------------------------------------------------------

_CB_CATEGORY_SECTOR: Dict[str, str] = {
    "food and beverage": "cpg", "food": "cpg", "beverage": "cpg",
    "beauty": "cpg", "personal care": "cpg", "consumer goods": "cpg",
    "health and wellness": "health_tech", "fitness": "health_tech",
    "mental health": "health_tech", "healthtech": "health_tech",
    "digital health": "health_tech",
    "travel": "travel", "hospitality": "travel",
    "marketplace": "marketplace", "e-commerce": "marketplace",
    "consumer": "marketplace",
}


def _cb_categories_to_sectors(categories: List[str]) -> List[str]:
    sectors = set()
    for cat in categories:
        c = cat.lower().strip()
        if c in _CB_CATEGORY_SECTOR:
            sectors.add(_CB_CATEGORY_SECTOR[c])
    return sorted(sectors)


# ---------------------------------------------------------------------------
# Location normalization
# ---------------------------------------------------------------------------

_US_STATE_ABBREVS: Dict[str, str] = {
    "AL": "us-alabama", "AK": "us-alaska", "AZ": "us-arizona",
    "AR": "us-arkansas", "CA": "us-california", "CO": "us-colorado",
    "CT": "us-connecticut", "DE": "us-delaware", "FL": "us-florida",
    "GA": "us-georgia", "HI": "us-hawaii", "ID": "us-idaho",
    "IL": "us-illinois", "IN": "us-indiana", "IA": "us-iowa",
    "KS": "us-kansas", "KY": "us-kentucky", "LA": "us-louisiana",
    "ME": "us-maine", "MD": "us-maryland", "MA": "us-massachusetts",
    "MI": "us-michigan", "MN": "us-minnesota", "MS": "us-mississippi",
    "MO": "us-missouri", "MT": "us-montana", "NE": "us-nebraska",
    "NV": "us-nevada", "NH": "us-new-hampshire", "NJ": "us-new-jersey",
    "NM": "us-new-mexico", "NY": "us-new-york", "NC": "us-north-carolina",
    "ND": "us-north-dakota", "OH": "us-ohio", "OK": "us-oklahoma",
    "OR": "us-oregon", "PA": "us-pennsylvania", "RI": "us-rhode-island",
    "SC": "us-south-carolina", "SD": "us-south-dakota", "TN": "us-tennessee",
    "TX": "us-texas", "UT": "us-utah", "VT": "us-vermont",
    "VA": "us-virginia", "WA": "us-washington", "WV": "us-west-virginia",
    "WI": "us-wisconsin", "WY": "us-wyoming", "DC": "us-dc",
}

_COUNTRY_NORMALIZE: Dict[str, str] = {
    "united states": "us", "usa": "us", "us": "us", "u.s.": "us",
    "united kingdom": "uk", "uk": "uk", "gb": "uk", "great britain": "uk",
    "england": "uk-england", "scotland": "uk-scotland", "wales": "uk-wales",
}

_JURISDICTION_NORMALIZE: Dict[str, str] = {
    "england-wales": "uk-england",
    "scotland": "uk-scotland",
    "northern-ireland": "uk-northern-ireland",
    "wales": "uk-wales",
}


def normalize_location(raw: str) -> Optional[str]:
    """Normalize a location string to a stable KG location key."""
    if not raw:
        return None
    loc = raw.strip()
    if not loc:
        return None
    upper = loc.upper().strip()
    if upper in _US_STATE_ABBREVS:
        return _US_STATE_ABBREVS[upper]
    lower = loc.lower().strip()
    if lower in _COUNTRY_NORMALIZE:
        return _COUNTRY_NORMALIZE[lower]
    if lower in _JURISDICTION_NORMALIZE:
        return _JURISDICTION_NORMALIZE[lower]
    slug = lower.replace(" ", "-").replace(",", "").replace(".", "")
    return slug if slug else None


# ---------------------------------------------------------------------------
# Per-source extractors
# ---------------------------------------------------------------------------

def extract_github(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("owner", {}).get("login") if isinstance(raw_data.get("owner"), dict) else raw_data.get("company_name")
    attrs.domain = raw_data.get("homepage") or raw_data.get("domain")
    attrs.description = raw_data.get("description")
    topics = raw_data.get("topics", [])
    if isinstance(topics, list):
        attrs.sectors = _topics_to_sectors(topics)
    attrs.claims = {"topics": topics, "source": source_api}
    return attrs


def extract_sec_edgar(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("entityName")
    attrs.domain = raw_data.get("domain") or raw_data.get("website")
    form_type = raw_data.get("formType", "")
    if "D" in str(form_type):
        attrs.stage = "Pre-Seed"
    sic = raw_data.get("sic_code") or raw_data.get("industryGroupType")
    if sic:
        sector = _sic_to_sector(str(sic))
        if sector:
            attrs.sectors = [sector]
    state = raw_data.get("state") or raw_data.get("stateOrCountry")
    country = raw_data.get("country") or raw_data.get("stateOrCountryDescription")
    if state:
        loc = normalize_location(str(state))
        if loc:
            attrs.locations = [loc]
    elif country:
        loc = normalize_location(str(country))
        if loc:
            attrs.locations = [loc]
    attrs.claims = {"sic_code": sic, "state": state, "form_type": form_type}
    return attrs


def extract_companies_house(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("title")
    attrs.domain = raw_data.get("domain") or raw_data.get("website")
    if raw_data.get("company_status") == "active":
        attrs.stage = "Pre-Seed"
    sic_codes = raw_data.get("sic_codes", [])
    if isinstance(sic_codes, list):
        for sic in sic_codes:
            sector = _sic_to_sector(str(sic))
            if sector:
                attrs.sectors.append(sector)
        attrs.sectors = sorted(set(attrs.sectors))
    jurisdiction = raw_data.get("jurisdiction")
    if jurisdiction:
        loc = normalize_location(str(jurisdiction))
        if loc:
            attrs.locations = [loc]
    attrs.claims = {"sic_codes": sic_codes, "jurisdiction": jurisdiction}
    return attrs


def extract_crunchbase(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("name")
    attrs.domain = raw_data.get("domain") or raw_data.get("homepage_url")
    attrs.description = raw_data.get("short_description")
    stage_raw = raw_data.get("last_funding_type") or raw_data.get("funding_round_type")
    stage_map = {
        "pre_seed": "Pre-Seed", "seed": "Seed", "series_a": "Series A",
        "angel": "Pre-Seed", "convertible_note": "Pre-Seed",
    }
    if stage_raw:
        attrs.stage = stage_map.get(stage_raw.lower().replace(" ", "_"), stage_raw)
    emp = raw_data.get("num_employees_enum") or raw_data.get("employee_count")
    if isinstance(emp, (int, float)):
        attrs.employees = int(emp)
    founded = raw_data.get("founded_on") or raw_data.get("founded_year")
    if founded:
        try:
            attrs.founded_year = int(str(founded)[:4])
        except (ValueError, TypeError):
            pass
    categories = raw_data.get("categories", [])
    if isinstance(categories, str):
        categories = [c.strip() for c in categories.split(",") if c.strip()]
    if isinstance(categories, list):
        attrs.sectors = _cb_categories_to_sectors(categories)
    location = raw_data.get("location") or raw_data.get("city")
    country = raw_data.get("country_code") or raw_data.get("country")
    if location:
        loc = normalize_location(str(location))
        if loc:
            attrs.locations.append(loc)
    if country:
        loc = normalize_location(str(country))
        if loc and loc not in attrs.locations:
            attrs.locations.append(loc)
    attrs.claims = {"categories": categories, "location": location, "stage": stage_raw}
    return attrs


def extract_hacker_news(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name")
    attrs.domain = raw_data.get("domain") or raw_data.get("url_domain")
    attrs.claims = {"title": raw_data.get("title")}
    return attrs


def extract_job_postings(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("company")
    attrs.domain = raw_data.get("domain")
    locations = raw_data.get("locations", [])
    if isinstance(locations, list):
        for loc_raw in locations:
            loc = normalize_location(str(loc_raw))
            if loc:
                attrs.locations.append(loc)
    elif isinstance(locations, str) and locations:
        loc = normalize_location(locations)
        if loc:
            attrs.locations = [loc]
    location = raw_data.get("location")
    if location and not attrs.locations:
        loc = normalize_location(str(location))
        if loc:
            attrs.locations = [loc]
    attrs.claims = {"locations": locations}
    return attrs


def extract_news(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name")
    attrs.domain = raw_data.get("domain") or raw_data.get("promoted_domain")
    attrs.claims = {"title": raw_data.get("title")}
    return attrs


def extract_domain_whois(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.domain = raw_data.get("domain") or raw_data.get("domain_name")
    attrs.company_name = raw_data.get("registrant_org") or raw_data.get("registrant_name")
    country = raw_data.get("country") or raw_data.get("registrant_country")
    if country:
        loc = normalize_location(str(country))
        if loc:
            attrs.locations = [loc]
    attrs.claims = {"registrant_org": attrs.company_name}
    return attrs


def extract_product_hunt(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("name")
    attrs.domain = raw_data.get("domain") or raw_data.get("website")
    attrs.description = raw_data.get("tagline") or raw_data.get("description")
    attrs.claims = {"tagline": raw_data.get("tagline")}
    return attrs


def extract_arxiv(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name")
    attrs.claims = {"title": raw_data.get("title")}
    return attrs


def extract_linkedin(raw_data: Dict[str, Any], source_api: str) -> ExtractedAttrs:
    attrs = ExtractedAttrs(source_api=source_api, priority=SOURCE_PRIORITY.get(source_api, 0))
    attrs.company_name = raw_data.get("company_name") or raw_data.get("name")
    attrs.domain = raw_data.get("domain") or raw_data.get("website")
    attrs.description = raw_data.get("description")
    emp = raw_data.get("employee_count") or raw_data.get("company_size")
    if isinstance(emp, (int, float)):
        attrs.employees = int(emp)
    location = raw_data.get("headquarters") or raw_data.get("location")
    if location:
        loc = normalize_location(str(location))
        if loc:
            attrs.locations = [loc]
    attrs.claims = {"industry": raw_data.get("industry")}
    return attrs


# ---------------------------------------------------------------------------
# Extractor registry
# ---------------------------------------------------------------------------

EXTRACTOR_REGISTRY: Dict[str, Callable[[Dict[str, Any], str], ExtractedAttrs]] = {
    "github": extract_github,
    "github_activity": extract_github,
    "sec_edgar": extract_sec_edgar,
    "companies_house": extract_companies_house,
    "crunchbase": extract_crunchbase,
    "hacker_news": extract_hacker_news,
    "job_postings": extract_job_postings,
    "news_api": extract_news,
    "rss_feeds": extract_news,
    "domain_whois": extract_domain_whois,
    "product_hunt": extract_product_hunt,
    "arxiv": extract_arxiv,
    "linkedin": extract_linkedin,
}


def extract_signal(source_api: str, raw_data: Any) -> ExtractedAttrs:
    """Extract attributes from a signal using the registered extractor."""
    data = _safe_json(raw_data)
    extractor = EXTRACTOR_REGISTRY.get(source_api)
    if extractor is None:
        logger.warning("No extractor registered for source_api=%s", source_api)
        return ExtractedAttrs(source_api=source_api, priority=0)
    return extractor(data, source_api)


def merge_attrs(attrs_list: List[ExtractedAttrs]) -> Dict[str, Any]:
    """Merge multiple ExtractedAttrs by source priority.

    Higher priority sources win for scalar fields.
    Collection fields (sectors, locations) are unioned.
    All claims are preserved with source attribution.
    """
    if not attrs_list:
        return {}
    sorted_attrs = sorted(attrs_list, key=lambda a: a.priority, reverse=True)
    merged: Dict[str, Any] = {
        "company_name": None,
        "domain": None,
        "description": None,
        "stage": None,
        "employees": None,
        "founded_year": None,
        "sectors": [],
        "locations": [],
        "claims": {},
    }
    for attrs in sorted_attrs:
        if attrs.company_name and not merged["company_name"]:
            merged["company_name"] = attrs.company_name
        if attrs.domain and not merged["domain"]:
            merged["domain"] = attrs.domain
        if attrs.description and not merged["description"]:
            merged["description"] = attrs.description
        if attrs.stage and not merged["stage"]:
            merged["stage"] = attrs.stage
        if attrs.employees and not merged["employees"]:
            merged["employees"] = attrs.employees
        if attrs.founded_year and not merged["founded_year"]:
            merged["founded_year"] = attrs.founded_year
    all_sectors: set[str] = set()
    all_locations: set[str] = set()
    all_claims: Dict[str, Any] = {}
    for attrs in sorted_attrs:
        all_sectors.update(attrs.sectors)
        all_locations.update(attrs.locations)
        if attrs.claims:
            all_claims[attrs.source_api] = attrs.claims
    merged["sectors"] = sorted(all_sectors)
    merged["locations"] = sorted(all_locations)
    merged["claims"] = all_claims
    return merged
