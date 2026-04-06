"""Shadow CT-log collector — public Certificate Transparency monitor.

Phase 0 task `p0.7`. First-wave shadow collector. Writes only to
`data/shadow/discovery.db`. No production touchpoints.

Why CT logs:
  - Public certificate issuance (Let's Encrypt, DigiCert, etc.) is one of
    the earliest infrastructure-intent signals for a new company. Founders
    register a domain, set up DNS, and issue a TLS cert weeks before any
    public launch, news mention, or HN post.
  - Certificate transparency logs are PUBLIC by RFC 6962. No legal exposure.
  - Free to query via crt.sh (HTTP+JSON) or Cloudflare Merkle/CT API.

Implementation note (Phase 0):
  - This collector ships as a SCAFFOLD. The HTTP fetch is implemented as a
    pluggable callable so the collector can be unit-tested with a fake
    fetcher and so the live HTTP integration can be added in Phase 0 day 2
    or 3 without rewriting the surrounding scaffold.
  - The default fetcher is a no-op that returns an empty list. The Phase 0
    integration test exercises the persistence path with the fake fetcher.
  - When ready to enable live fetching, set `fetcher=crtsh_fetcher()` (a
    helper that wraps httpx). The fetcher protocol is documented below.

Fetcher protocol::

    def fetcher(query: str) -> List[Dict[str, Any]]:
        \"\"\"Return a list of certificate dicts with at least:
            - common_name: str
            - issuer:      str
            - not_before:  ISO 8601 datetime string
            - san:         List[str]
        \"\"\"

The collector classifies each certificate as INFRASTRUCTURE_INTENT and
records it under the canonical_key `domain:<common_name>`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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

COLLECTOR_NAME = "shadow_ct_log"

# Public CT-log query endpoints (documented for the fetcher implementation).
CRTSH_BASE_URL = "https://crt.sh/?q={query}&output=json"
CLOUDFLARE_CT_BASE_URL = "https://ct.cloudflare.com/logs/nimbus2026/ct/v1"

# Default search queries — tech-focused TLDs and consumer-friendly suffixes.
# Each entry is a crt.sh-compatible search query string.
DEFAULT_QUERIES: Sequence[str] = (
    "%.ai",
    "%.io",
    "%.co",
    "%.health",
    "%.app",
    "%.shop",
)


# Fetcher protocol: callable that takes a query string and returns a list of
# certificate dicts. Implementations should respect the rate budget passed
# from the collector.
Fetcher = Callable[[str], List[Dict[str, Any]]]


def null_fetcher(query: str) -> List[Dict[str, Any]]:
    """Default fetcher — returns nothing. Used until live HTTP is enabled."""
    return []


@dataclass
class CtLogConfig:
    """Configuration for the shadow CT-log collector."""

    queries: Sequence[str] = DEFAULT_QUERIES
    rate_per_hour: int = 60          # crt.sh recommends < 1 req/sec
    max_certs_per_run: int = 500      # cap to keep shadow DB bounded


def _build_canonical_key(common_name: str) -> str:
    """Build the canonical key for a CT cert. Mirrors utils.canonical_keys."""
    cn = (common_name or "").strip().lower()
    if cn.startswith("*."):
        cn = cn[2:]
    return f"domain:{cn}" if cn else ""


def _classify_confidence(cert: Dict[str, Any]) -> float:
    """Heuristic confidence for a CT-log cert.

    Higher confidence for:
      - tech TLDs
      - non-wildcard certs (suggests an actual product)
      - small SAN sets (one product, not a CDN bucket)
    """
    cn = (cert.get("common_name") or "").strip().lower()
    confidence = 0.4
    if cn and not cn.startswith("*."):
        confidence += 0.1
    if any(cn.endswith(t) for t in (".ai", ".io", ".health", ".app")):
        confidence += 0.1
    sans = cert.get("san") or []
    if isinstance(sans, list) and 0 < len(sans) <= 5:
        confidence += 0.05
    return min(confidence, 0.7)


def collect(
    sidecar: ShadowSidecar,
    *,
    config: Optional[CtLogConfig] = None,
    fetcher: Fetcher = null_fetcher,
) -> ShadowCollectorResult:
    """Run the CT-log shadow collector once. Returns a result summary.

    Safety:
      - Writes only via `persist_shadow_signal` (which writes only to
        the shadow DB through the sidecar contract).
      - Rate-limits the fetcher via RateBudget.
    """
    cfg = config or CtLogConfig()
    run_id = make_run_id(COLLECTOR_NAME)
    started_at = utcnow_iso()

    sidecar.begin_run(collector=COLLECTOR_NAME, run_id=run_id)

    budget = RateBudget(max_per_hour=cfg.rate_per_hour)
    persisted = 0
    collected = 0
    notes: List[str] = []

    for query in cfg.queries:
        if persisted >= cfg.max_certs_per_run:
            notes.append(f"hit max_certs_per_run cap ({cfg.max_certs_per_run})")
            break
        try:
            budget.acquire()
            certs = fetcher(query)
        except Exception as exc:
            logger.warning("CT-log fetcher failed for %r: %s", query, exc)
            notes.append(f"fetcher error for {query!r}: {exc}")
            continue

        for cert in certs:
            collected += 1
            if persisted >= cfg.max_certs_per_run:
                break
            cn = (cert.get("common_name") or "").strip()
            if not cn:
                continue
            canonical_key = _build_canonical_key(cn)
            if not canonical_key:
                continue
            persist_shadow_signal(
                sidecar,
                collector=COLLECTOR_NAME,
                canonical_key=canonical_key,
                company_name=None,  # CT logs don't carry company names
                confidence=_classify_confidence(cert),
                raw_data={
                    "query": query,
                    "common_name": cn,
                    "issuer": cert.get("issuer"),
                    "not_before": cert.get("not_before"),
                    "san": cert.get("san"),
                    "source": "crt.sh",
                },
            )
            persisted += 1

    completed_at = utcnow_iso()
    sidecar.end_run(run_id=run_id, items_collected=persisted)

    return ShadowCollectorResult(
        collector=COLLECTOR_NAME,
        run_id=run_id,
        items_collected=collected,
        items_persisted=persisted,
        started_at=started_at,
        completed_at=completed_at,
        notes=notes,
    )
