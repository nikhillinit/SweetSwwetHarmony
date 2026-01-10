"""
Domain/WHOIS Collector for Discovery Engine

Monitors domain registrations using RDAP (Registration Data Access Protocol),
the modern successor to WHOIS.

Why this matters for early-stage VC:
- New domain registration often signals startup formation
- Tech-focused TLDs (.ai, .io, .tech, .health) indicate tech companies
- Registration date freshness shows timing of company formation
- Premium registrars can indicate serious companies

Focus areas:
- Tech TLDs: .ai, .io, .tech, .health, .dev
- Recently registered domains (< 30 days = high signal)
- Domains associated with other signals (incorporation, GitHub, etc.)

RDAP endpoints:
- .com/.net: https://rdap.verisign.com/com/v1/domain/{domain}
- .io: https://rdap.nic.io/domain/{domain}
- .ai: https://rdap.nic.ai/domain/{domain}
- .tech: https://rdap.centralnic.com/tech/domain/{domain}
- .dev: https://rdap.google.com/domain/{domain}
- Generic: https://rdap.org/domain/{domain} (bootstrap service)

Two modes:
1. Enrichment mode: Check registration freshness for known domains from other signals
2. Discovery mode: Monitor new registrations (if feed available)

Note: Most RDAP servers don't provide "recently registered" feeds.
This collector is best used for enrichment of domains discovered via other signals.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

import httpx

# Add parent directory to path for imports
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors.base import BaseCollector
from collectors.retry_strategy import with_retry, RetryConfig
from discovery_engine.mcp_server import CollectorResult, CollectorStatus
from storage.signal_store import SignalStore
from utils.rate_limiter import get_rate_limiter
from utils.canonical_keys import build_canonical_key, build_canonical_key_candidates, normalize_domain
from verification.verification_gate_v2 import Signal, VerificationStatus

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Tech-focused TLDs that signal startup activity
TECH_TLDS = {
    ".ai", ".io", ".tech", ".dev", ".app", ".cloud",
    ".health", ".healthcare", ".bio", ".ml", ".data"
}

# Premium registrars that indicate serious companies (not domain squatters)
PREMIUM_REGISTRARS = {
    "markmonitor", "cscglobal", "safenames", "cscdbs",
    "namecheap", "google", "cloudflare", "aws", "gandi"
}

# RDAP endpoints by TLD
RDAP_ENDPOINTS = {
    "com": "https://rdap.verisign.com/com/v1/domain/",
    "net": "https://rdap.verisign.com/net/v1/domain/",
    "io": "https://rdap.nic.io/domain/",
    "ai": "https://rdap.nic.ai/domain/",
    "tech": "https://rdap.centralnic.com/tech/domain/",
    "dev": "https://rdap.google.com/domain/",
    "app": "https://rdap.google.com/domain/",
    "health": "https://rdap.nic.health/domain/",
    # Generic fallback
    "generic": "https://rdap.org/domain/",
}

# Rate limiting
RDAP_REQUEST_DELAY = 0.5  # seconds between requests (be respectful)
RDAP_TIMEOUT = 10.0  # seconds


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class DomainRegistration:
    """Parsed RDAP domain registration data"""

    # Core identifiers
    domain: str  # Normalized domain (e.g., "acme.ai")
    tld: str     # Top-level domain (e.g., "ai")

    # Registration data
    registration_date: Optional[datetime] = None
    expiration_date: Optional[datetime] = None
    last_updated: Optional[datetime] = None

    # Registrar/registry
    registrar: Optional[str] = None
    registrar_id: Optional[str] = None

    # Technical details
    nameservers: List[str] = field(default_factory=list)
    status: List[str] = field(default_factory=list)

    # Registrant (often redacted by GDPR/privacy)
    registrant_name: Optional[str] = None
    registrant_org: Optional[str] = None
    registrant_country: Optional[str] = None

    # Provenance
    rdap_endpoint: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def age_days(self) -> int:
        """Days since registration"""
        if not self.registration_date:
            return 999999  # Unknown = very old
        return (datetime.now(timezone.utc) - self.registration_date).days

    @property
    def is_recently_registered(self) -> bool:
        """Registered within last 30 days"""
        return self.age_days <= 30

    @property
    def is_tech_tld(self) -> bool:
        """Uses a tech-focused TLD"""
        return f".{self.tld.lower()}" in TECH_TLDS

    @property
    def has_premium_registrar(self) -> bool:
        """Uses a premium/enterprise registrar"""
        if not self.registrar:
            return False
        registrar_lower = self.registrar.lower()
        return any(premium in registrar_lower for premium in PREMIUM_REGISTRARS)

    @property
    def days_until_expiration(self) -> Optional[int]:
        """Days until domain expires"""
        if not self.expiration_date:
            return None
        return (self.expiration_date - datetime.now(timezone.utc)).days

    @property
    def is_active(self) -> bool:
        """Domain is active (not expired or pending delete)"""
        if not self.status:
            return True  # Assume active if no status

        inactive_statuses = {
            "pending delete", "redemption period", "expired",
            "client hold", "server hold"
        }

        status_lower = {s.lower() for s in self.status}
        return not bool(status_lower & inactive_statuses)

    def calculate_signal_score(self) -> float:
        """
        Calculate signal strength based on domain characteristics.

        Scoring:
        - Recently registered (< 30 days): high signal
        - Tech TLD: bonus
        - Premium registrar: slight bonus
        - Active status: required
        """
        if not self.is_active:
            return 0.0

        # Base score depends on registration age
        if self.age_days <= 7:
            base_score = 0.8  # Very fresh
        elif self.age_days <= 30:
            base_score = 0.6  # Fresh
        elif self.age_days <= 90:
            base_score = 0.4  # Recent
        elif self.age_days <= 180:
            base_score = 0.3  # Somewhat recent
        else:
            base_score = 0.2  # Old (still useful for enrichment)

        # Boost for tech TLDs
        if self.is_tech_tld:
            base_score += 0.1

        # Slight boost for premium registrars
        if self.has_premium_registrar:
            base_score += 0.05

        # Cap at 1.0
        return min(base_score, 1.0)

    def to_signal(self) -> Signal:
        """Convert domain registration to a Signal for verification gate"""

        confidence = self.calculate_signal_score()

        # Build source URL
        source_url = f"{self.rdap_endpoint}{self.domain}"

        # Build canonical key from domain
        canonical_key = f"domain:{self.domain}"

        # Hash raw data for provenance
        raw_json = str(sorted(self.raw_data.items()))
        response_hash = hashlib.sha256(raw_json.encode()).hexdigest()[:16]

        return Signal(
            id=f"domain_whois_{self.domain.replace('.', '_')}",
            signal_type="domain_registration",
            confidence=confidence,
            source_api="rdap",
            source_url=source_url,
            source_response_hash=response_hash,
            detected_at=self.registration_date or self.retrieved_at,
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=["rdap"],
            raw_data={
                "domain": self.domain,
                "tld": self.tld,
                "registration_date": self.registration_date.isoformat() if self.registration_date else None,
                "age_days": self.age_days,
                "is_tech_tld": self.is_tech_tld,
                "registrar": self.registrar,
                "has_premium_registrar": self.has_premium_registrar,
                "nameservers": self.nameservers,
                "status": self.status,
                "registrant_org": self.registrant_org,
                "registrant_country": self.registrant_country,
                "expiration_date": self.expiration_date.isoformat() if self.expiration_date else None,
                "canonical_key": canonical_key,
            }
        )


# =============================================================================
# DOMAIN/WHOIS COLLECTOR
# =============================================================================

class DomainWhoisCollector(BaseCollector):
    """
    Collects domain registration data via RDAP to identify new startups.

    Usage:
        # Enrichment mode: Check specific domains
        collector = DomainWhoisCollector(store=signal_store)
        result = await collector.run(
            domains=["acme.ai", "example.io"],
            dry_run=False
        )

        # Discovery mode: Monitor new registrations (limited availability)
        # Most RDAP servers don't provide feeds, so this is mainly for enrichment
    """

    def __init__(
        self,
        store: Optional[SignalStore] = None,
        lookback_days: int = 90,
        max_domains: int = 100,
        tech_tlds_only: bool = False,
    ):
        """
        Args:
            store: Optional SignalStore instance for persistence
            lookback_days: Only flag domains registered within this window
            max_domains: Maximum number of domains to check
            tech_tlds_only: Only return signals for tech TLDs
        """
        super().__init__(store=store, collector_name="domain_whois")

        self.lookback_days = lookback_days
        self.max_domains = max_domains
        self.tech_tlds_only = tech_tlds_only

        self._client: Optional[httpx.AsyncClient] = None
        self._processed_domains: Set[str] = set()
        self._domains_to_check: Optional[List[str]] = None

    async def __aenter__(self):
        """Async context manager entry"""
        self._client = httpx.AsyncClient(
            timeout=RDAP_TIMEOUT,
            follow_redirects=True,
            headers={
                "Accept": "application/rdap+json,application/json",
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self._client:
            await self._client.aclose()

    async def run(
        self,
        domains: Optional[List[str]] = None,
        dry_run: bool = True
    ) -> CollectorResult:
        """
        Run the collector and return results.

        Args:
            domains: List of domains to check (enrichment mode)
                    If None, would use discovery mode (not widely available)
            dry_run: If True, don't persist to database

        Returns:
            CollectorResult with domain counts and signals
        """
        # Store domains for _collect_signals to use
        self._domains_to_check = domains

        # Use parent's run method which calls _collect_signals
        return await super().run(dry_run=dry_run)

    async def _collect_signals(self) -> List[Signal]:
        """
        Collect signals from domain registrations.

        Returns:
            List of Signal objects
        """
        registrations: List[DomainRegistration] = []

        if self._domains_to_check:
            # Enrichment mode: check provided domains
            logger.info(f"Enrichment mode: checking {len(self._domains_to_check)} domains")
            registrations = await self._check_domains(self._domains_to_check[:self.max_domains])
        else:
            # Discovery mode: would need a feed source
            logger.warning(
                "Discovery mode not available - most RDAP servers don't provide "
                "new registration feeds. Use enrichment mode with specific domains."
            )
            return []

        logger.info(f"Retrieved {len(registrations)} domain registrations")

        # Filter by lookback window
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        recent_registrations = [
            r for r in registrations
            if r.registration_date and r.registration_date >= cutoff_date
        ]

        logger.info(
            f"Filtered to {len(recent_registrations)} domains registered "
            f"within {self.lookback_days} days"
        )

        # Filter for tech TLDs if requested
        if self.tech_tlds_only:
            recent_registrations = [r for r in recent_registrations if r.is_tech_tld]
            logger.info(f"Filtered to {len(recent_registrations)} tech TLD domains")

        # Convert to signals and detect changes
        signals = []
        for registration in recent_registrations:
            # Save raw data and detect changes
            if self.asset_store:
                is_new, changes = await self._save_asset_with_change_detection(
                    source_type=self.SOURCE_TYPE,
                    external_id=registration.domain,
                    raw_data=registration.to_dict() if hasattr(registration, 'to_dict') else vars(registration),
                )

                # Skip unchanged registrations
                if not is_new and not changes:
                    logger.debug(f"Skipping unchanged domain: {registration.domain}")
                    continue

            signals.append(registration.to_signal())

        # Filter out low-confidence signals
        high_confidence_signals = [s for s in signals if s.confidence >= 0.3]

        logger.info(
            f"Generated {len(high_confidence_signals)} signals "
            f"(from {len(signals)} total registrations)"
        )

        return high_confidence_signals

    async def _check_domains(self, domains: List[str]) -> List[DomainRegistration]:
        """
        Check RDAP data for a list of domains.

        Args:
            domains: List of domain names to check

        Returns:
            List of DomainRegistration objects
        """
        registrations: List[DomainRegistration] = []

        for domain in domains:
            # Normalize domain
            normalized = normalize_domain(domain)
            if not normalized:
                logger.warning(f"Invalid domain: {domain}")
                continue

            if normalized in self._processed_domains:
                logger.debug(f"Already processed: {normalized}")
                continue

            try:
                registration = await self._fetch_domain_rdap(normalized)
                if registration:
                    registrations.append(registration)
                    self._processed_domains.add(normalized)

                # Rate limiting
                await asyncio.sleep(RDAP_REQUEST_DELAY)

            except Exception as e:
                logger.warning(f"Error checking domain {normalized}: {e}")
                continue

        return registrations

    async def _fetch_domain_rdap(self, domain: str) -> Optional[DomainRegistration]:
        """
        Fetch RDAP data for a single domain.

        Args:
            domain: Normalized domain name (e.g., "acme.ai")

        Returns:
            DomainRegistration object or None if not found
        """
        # Extract TLD
        parts = domain.split(".")
        if len(parts) < 2:
            logger.warning(f"Invalid domain format: {domain}")
            return None

        tld = parts[-1].lower()

        # Choose RDAP endpoint
        rdap_base = RDAP_ENDPOINTS.get(tld, RDAP_ENDPOINTS["generic"])
        rdap_url = f"{rdap_base}{domain}"

        logger.debug(f"Fetching RDAP data: {rdap_url}")

        try:
            # Use rate limiter before making request
            await self.rate_limiter.acquire()

            # Wrap HTTP request with retry logic
            async def fetch_rdap():
                response = await self._client.get(rdap_url)

                # 404 is expected for non-registered domains - don't retry
                if response.status_code == 404:
                    logger.debug(f"Domain not found in RDAP: {domain}")
                    return None

                response.raise_for_status()
                return response.json()

            rdap_data = await with_retry(fetch_rdap, self.retry_config)

            # If we got None from 404, return early
            if rdap_data is None:
                return None

            return self._parse_rdap_response(domain, tld, rdap_url, rdap_data)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Domain not registered: {domain}")
                return None
            logger.error(f"HTTP error fetching RDAP for {domain}: {e}")
            raise

        except Exception as e:
            logger.error(f"Error fetching RDAP for {domain}: {e}")
            raise

    def _parse_rdap_response(
        self,
        domain: str,
        tld: str,
        rdap_url: str,
        rdap_data: Dict[str, Any]
    ) -> Optional[DomainRegistration]:
        """
        Parse RDAP JSON response into DomainRegistration.

        RDAP schema reference:
        - https://www.rfc-editor.org/rfc/rfc9083.html

        Key fields:
        - events: registration, expiration, last changed
        - entities: registrar, registrant
        - nameservers
        - status
        """
        try:
            # Extract registration/expiration dates from events
            registration_date = None
            expiration_date = None
            last_updated = None

            for event in rdap_data.get("events", []):
                event_action = event.get("eventAction", "").lower()
                event_date_str = event.get("eventDate")

                if not event_date_str:
                    continue

                try:
                    event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
                except Exception:
                    continue

                if event_action == "registration":
                    registration_date = event_date
                elif event_action == "expiration":
                    expiration_date = event_date
                elif event_action in ("last changed", "last update of rdap database"):
                    last_updated = event_date

            # Extract registrar from entities
            registrar = None
            registrar_id = None
            registrant_name = None
            registrant_org = None
            registrant_country = None

            for entity in rdap_data.get("entities", []):
                roles = entity.get("roles", [])

                if "registrar" in roles:
                    # Found registrar entity
                    registrar = entity.get("vcardArray", [[]])[1] if "vcardArray" in entity else None
                    if isinstance(registrar, list) and len(registrar) > 0:
                        # vCard format is complex, try to extract name
                        for vcard_field in registrar:
                            if isinstance(vcard_field, list) and len(vcard_field) >= 4:
                                if vcard_field[0] == "fn":  # Full name
                                    registrar = vcard_field[3]
                                    break

                    # Also check publicIds for registrar ID
                    for public_id in entity.get("publicIds", []):
                        if public_id.get("type") == "IANA Registrar ID":
                            registrar_id = public_id.get("identifier")

                elif "registrant" in roles:
                    # Found registrant entity (often redacted by GDPR)
                    if "vcardArray" in entity and len(entity["vcardArray"]) > 1:
                        vcard = entity["vcardArray"][1]
                        for field in vcard:
                            if isinstance(field, list) and len(field) >= 4:
                                field_name = field[0]
                                field_value = field[3]

                                if field_name == "fn":
                                    registrant_name = field_value
                                elif field_name == "org":
                                    registrant_org = field_value
                                elif field_name == "adr":
                                    # Address field contains country
                                    if isinstance(field_value, dict):
                                        registrant_country = field_value.get("country")

            # Extract nameservers
            nameservers = []
            for ns in rdap_data.get("nameservers", []):
                ns_name = ns.get("ldhName") or ns.get("unicodeName")
                if ns_name:
                    nameservers.append(ns_name)

            # Extract status
            status = rdap_data.get("status", [])

            return DomainRegistration(
                domain=domain,
                tld=tld,
                registration_date=registration_date,
                expiration_date=expiration_date,
                last_updated=last_updated,
                registrar=registrar,
                registrar_id=registrar_id,
                nameservers=nameservers,
                status=status,
                registrant_name=registrant_name,
                registrant_org=registrant_org,
                registrant_country=registrant_country,
                rdap_endpoint=rdap_url.replace(domain, ""),
                raw_data=rdap_data,
            )

        except Exception as e:
            logger.error(f"Error parsing RDAP response for {domain}: {e}")
            return None

    async def check_domain(self, domain: str) -> Optional[DomainRegistration]:
        """
        Check a single domain (convenience method for external use).

        Args:
            domain: Domain to check

        Returns:
            DomainRegistration or None
        """
        async with self:
            normalized = normalize_domain(domain)
            if not normalized:
                return None

            return await self._fetch_domain_rdap(normalized)


# =============================================================================
# USAGE EXAMPLE
# =============================================================================

async def example_usage():
    """Example demonstrating the Domain/WHOIS collector"""

    # Example 1: Check specific domains (enrichment mode)
    domains_to_check = [
        "anthropic.com",
        "openai.com",
        "acme.ai",
        "example.io",
    ]

    collector = DomainWhoisCollector(
        lookback_days=90,
        max_domains=50,
        tech_tlds_only=False,
    )

    result = await collector.run(domains=domains_to_check, dry_run=True)

    print("=" * 50)
    print("DOMAIN/WHOIS COLLECTOR RESULT")
    print("=" * 50)
    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")
    print(f"Suppressed: {result.signals_suppressed}")
    print(f"Dry run: {result.dry_run}")

    if result.error_message:
        print(f"Error: {result.error_message}")

    # Example 2: Check a single domain
    print("\n" + "=" * 50)
    print("SINGLE DOMAIN CHECK")
    print("=" * 50)

    async with DomainWhoisCollector() as collector:
        registration = await collector.check_domain("anthropic.com")

        if registration:
            print(f"Domain: {registration.domain}")
            print(f"TLD: {registration.tld}")
            print(f"Registration date: {registration.registration_date}")
            print(f"Age: {registration.age_days} days")
            print(f"Is tech TLD: {registration.is_tech_tld}")
            print(f"Registrar: {registration.registrar}")
            print(f"Premium registrar: {registration.has_premium_registrar}")
            print(f"Status: {registration.status}")
            print(f"Nameservers: {registration.nameservers}")
            print(f"Signal score: {registration.calculate_signal_score():.2f}")
        else:
            print("Domain not found or error occurred")


if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Run example
    asyncio.run(example_usage())
