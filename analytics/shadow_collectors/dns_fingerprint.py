"""Shadow DNS fingerprint collector — extends domain_whois logic.

Phase 0 task `p0.8`. First-wave shadow collector. Writes only to
`data/shadow/discovery.db`. No production touchpoints.

Why DNS fingerprinting:
  - DNS records (MX, TXT, NS) reveal infrastructure-intent signals that
    pre-date public launches:
      * MX records → real email backend (Google Workspace, Microsoft 365)
        suggests a working business, not a parked domain
      * TXT records → SPF, DKIM, business-verification tokens (Google,
        Stripe, Brevo, etc.) — each verification cluster signals the
        founder is wiring up a real stack
      * NS delegation to Cloudflare / Vercel / Fly → modern stack
  - All public DNS data. No legal risk.

Relationship to existing `collectors/domain_whois.py`:
  - The production domain_whois collector queries RDAP for registration
    metadata. It does NOT inspect MX/TXT/NS records.
  - This shadow collector is the *enrichment* surface that the strategy doc
    proposed. In Phase 3, after shadow comparison validates value, the
    relevant logic could be folded into `collectors/domain_whois.py` as a
    second pass — extending, not duplicating.
  - For Phase 0, this lives entirely in the shadow tree.

Resolver protocol::

    def resolver(domain: str, record_type: str) -> List[str]:
        \"\"\"Return a list of record values, or [] if none / lookup failed.\"\"\"

The default resolver is a no-op that returns []. Plug in dnspython or any
other DNS library when ready to go live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from analytics.shadow_collectors.base import (
    RateBudget,
    ShadowCollectorResult,
    make_run_id,
    persist_shadow_signal,
    utcnow_iso,
)
from analytics.shadow_sidecar import ShadowSidecar

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "shadow_dns_fingerprint"

# Resolver protocol — see module docstring.
Resolver = Callable[[str, str], List[str]]


def null_resolver(domain: str, record_type: str) -> List[str]:
    """Default resolver — returns nothing. Used until live DNS is enabled."""
    return []


@dataclass
class DnsFingerprintConfig:
    """Configuration for the DNS fingerprint shadow collector."""

    record_types: Sequence[str] = ("MX", "TXT", "NS")
    rate_per_hour: int = 600         # 10 lookups/min — conservative
    max_domains_per_run: int = 200


# Vendor strings that increase confidence the domain is a real running stack.
# Strictly heuristic — these are weights, not gates.
INFRASTRUCTURE_VENDOR_HINTS = {
    # Email infrastructure (MX)
    "google.com": ("MX", 0.10),
    "googlemail": ("MX", 0.10),
    "outlook.com": ("MX", 0.05),
    "mailgun": ("MX", 0.10),
    "amazonses": ("MX", 0.10),
    # SaaS verification tokens (TXT)
    "google-site-verification": ("TXT", 0.05),
    "stripe-verification": ("TXT", 0.10),
    "brevo-code": ("TXT", 0.05),
    "loops-verification": ("TXT", 0.05),
    # Modern hosting (NS)
    "cloudflare.com": ("NS", 0.05),
    "vercel-dns": ("NS", 0.05),
    "fly.io": ("NS", 0.05),
}


def _build_canonical_key(domain: str) -> str:
    return f"domain:{domain.strip().lower()}" if domain else ""


def _score_records(records_by_type: Dict[str, List[str]]) -> float:
    """Score a domain based on its DNS records."""
    base = 0.35  # has at least one record we asked about
    bonus = 0.0
    for record_type, values in records_by_type.items():
        for value in values:
            v = (value or "").lower()
            for hint, (rtype, weight) in INFRASTRUCTURE_VENDOR_HINTS.items():
                if rtype == record_type and hint in v:
                    bonus += weight
    return min(base + bonus, 0.85)


@dataclass
class DnsFingerprintInput:
    """Input domain to fingerprint."""

    domain: str
    company_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def collect(
    sidecar: ShadowSidecar,
    domains: Sequence[DnsFingerprintInput],
    *,
    config: Optional[DnsFingerprintConfig] = None,
    resolver: Resolver = null_resolver,
) -> ShadowCollectorResult:
    """Run the DNS fingerprint shadow collector over a list of domains.

    Each input domain is queried for the configured record types and the
    aggregated result is persisted as one shadow_signal row.
    """
    cfg = config or DnsFingerprintConfig()
    run_id = make_run_id(COLLECTOR_NAME)
    started_at = utcnow_iso()

    sidecar.begin_run(collector=COLLECTOR_NAME, run_id=run_id)

    budget = RateBudget(max_per_hour=cfg.rate_per_hour)
    persisted = 0
    notes: List[str] = []

    for inp in domains[: cfg.max_domains_per_run]:
        records_by_type: Dict[str, List[str]] = {}
        try:
            for rtype in cfg.record_types:
                budget.acquire()
                values = resolver(inp.domain, rtype)
                if values:
                    records_by_type[rtype] = values
        except Exception as exc:
            logger.warning("DNS resolver failed for %s: %s", inp.domain, exc)
            notes.append(f"resolver error for {inp.domain}: {exc}")
            continue

        if not records_by_type:
            continue

        confidence = _score_records(records_by_type)
        canonical_key = _build_canonical_key(inp.domain)
        persist_shadow_signal(
            sidecar,
            collector=COLLECTOR_NAME,
            canonical_key=canonical_key,
            company_name=inp.company_name,
            confidence=confidence,
            raw_data={
                "domain": inp.domain,
                "records": records_by_type,
                "score_components": {
                    rt: len(v) for rt, v in records_by_type.items()
                },
                "input_metadata": inp.metadata,
            },
        )
        persisted += 1

    completed_at = utcnow_iso()
    sidecar.end_run(run_id=run_id, items_collected=persisted)

    return ShadowCollectorResult(
        collector=COLLECTOR_NAME,
        run_id=run_id,
        items_collected=persisted,
        items_persisted=persisted,
        started_at=started_at,
        completed_at=completed_at,
        notes=notes,
    )
