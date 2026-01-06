# SEC EDGAR Collector - Architecture

Visual guide to how the SEC EDGAR collector integrates with the Discovery Engine.

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DISCOVERY ENGINE                             │
│                                                                      │
│  ┌────────────────┐      ┌─────────────────┐      ┌──────────────┐ │
│  │   COLLECTORS   │──────▶│ VERIFICATION    │──────▶│   NOTION     │ │
│  │                │       │     GATE        │       │   CONNECTOR  │ │
│  │ • GitHub       │       │                 │       │              │ │
│  │ • SEC EDGAR    │       │ Confidence      │       │ Status:      │ │
│  │ • Companies    │       │ Multi-source    │       │ • Source     │ │
│  │   House        │       │ Routing logic   │       │ • Tracking   │ │
│  └────────────────┘       └─────────────────┘       └──────────────┘ │
│         │                          │                        │         │
│         │                          │                        │         │
│         ▼                          ▼                        ▼         │
│  ┌────────────────┐      ┌─────────────────┐      ┌──────────────┐ │
│  │  CANONICAL     │      │   SUPPRESSION   │      │   NOTION     │ │
│  │     KEYS       │      │     CACHE       │      │     CRM      │ │
│  │                │      │                 │      │              │ │
│  │ domain:*       │      │ Check existing  │      │ Companies    │ │
│  │ name_loc:*     │      │ Dedupe matches  │      │ Database     │ │
│  └────────────────┘      └─────────────────┘      └──────────────┘ │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## SEC EDGAR Collector Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                      SEC EDGAR COLLECTOR                             │
└─────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  1. Fetch Form D RSS Feed (Atom XML)   │
        │                                         │
        │  SEC EDGAR API                          │
        │  GET /browse-edgar?type=D&output=atom   │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  2. Parse Atom Feed                    │
        │                                         │
        │  Extract:                               │
        │  • Company name                         │
        │  • CIK (SEC identifier)                 │
        │  • Accession number                     │
        │  • Filing date                          │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  3. Filter by Date                     │
        │                                         │
        │  Only filings within lookback window    │
        │  (default: 30 days)                     │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  4. Fetch Individual Form D XML        │
        │                                         │
        │  For each filing:                       │
        │  GET /Archives/edgar/data/{cik}/...     │
        │  (Rate limited: 0.15s delay)            │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  5. Parse Form D XML                   │
        │                                         │
        │  Extract:                               │
        │  • Offering amount ($)                  │
        │  • Amount sold ($)                      │
        │  • SIC code                             │
        │  • Industry group                       │
        │  • State/country                        │
        │  • Issuer type                          │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  6. Classify Industry                  │
        │                                         │
        │  Map SIC code to:                       │
        │  • healthtech                           │
        │  • cleantech                            │
        │  • ai_infrastructure                    │
        │  • (or None if not target)              │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  7. Filter for Target Sectors          │
        │                                         │
        │  If target_sectors_only=True:           │
        │  Keep only healthtech/cleantech/AI      │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  8. Convert to Signals                 │
        │                                         │
        │  FormDFiling → Signal                   │
        │  • Build canonical keys                 │
        │  • Calculate confidence                 │
        │  • Estimate stage                       │
        └────────────────────────────────────────┘
                                 │
                                 ▼
        ┌────────────────────────────────────────┐
        │  9. Return CollectorResult             │
        │                                         │
        │  {                                      │
        │    collector: "sec_edgar",              │
        │    status: "dry_run",                   │
        │    signals_found: 15,                   │
        │    signals_new: 15                      │
        │  }                                      │
        └────────────────────────────────────────┘
```

## Signal Processing Pipeline

```
┌─────────────────┐
│  FormDFiling    │  Raw Form D data
└────────┬────────┘
         │
         │ to_signal()
         ▼
┌─────────────────┐
│     Signal      │  Standardized signal format
└────────┬────────┘
         │
         │ build_canonical_key_candidates()
         ▼
┌─────────────────┐
│  Canonical Keys │  ["domain:acme.ai", "name_loc:acme|ca"]
└────────┬────────┘
         │
         │ check_suppression()
         ▼
┌─────────────────┐
│  Not Suppressed │  New signal (not in Notion)
└────────┬────────┘
         │
         │ evaluate()
         ▼
┌─────────────────┐
│ Verification    │  Confidence: 0.85
│     Gate        │  Decision: AUTO_PUSH
└────────┬────────┘
         │
         │ upsert_prospect()
         ▼
┌─────────────────┐
│     Notion      │  Status: "Source"
│      CRM        │  Stage: "Seed"
└─────────────────┘
```

## Data Model

### FormDFiling

```python
@dataclass
class FormDFiling:
    # Identifiers
    cik: str                    # SEC Central Index Key
    company_name: str           # e.g., "ACME HEALTH INC"
    accession_number: str       # e.g., "1234567890-24-001234"

    # Timing
    filing_date: datetime       # When filed with SEC

    # Offering details
    offering_amount: float      # Total offering ($)
    offering_sold: float        # Already sold ($)
    minimum_investment: float   # Min investment ($)

    # Classification
    sic_code: str               # e.g., "2834" (pharma)
    industry_group: str         # "healthtech", "cleantech", "ai_infrastructure"
    issuer_type: str            # "Corporation", "LLC", etc.

    # Location
    state: str                  # e.g., "CA"
    country: str                # e.g., "US"

    # External refs
    website: Optional[str]      # Often not in Form D
    external_refs: Dict         # For canonical key building

    # Metadata
    filing_url: str             # SEC EDGAR URL
    raw_data: Dict              # Full parsed data
```

### Signal (from verification_gate_v2)

```python
@dataclass
class Signal:
    id: str                     # "sec_edgar_1234567890-24-001234"
    signal_type: str            # "funding_event"
    confidence: float           # 0.0 - 1.0

    # Provenance
    source_api: str             # "sec_edgar"
    source_url: str             # SEC EDGAR URL
    source_response_hash: str   # For audit trail
    retrieved_at: datetime
    detected_at: datetime

    # Verification
    verified_by_sources: List[str]              # ["sec_edgar"]
    verification_status: VerificationStatus     # SINGLE_SOURCE

    raw_data: Dict              # Full filing data
```

## Confidence Calculation

```python
def calculate_confidence(filing: FormDFiling) -> float:
    """
    Base confidence: 0.7 (Form D is authoritative)

    Boosts:
    + 0.15 if industry_group in [healthtech, cleantech, ai_infrastructure]
    + 0.10 if offering_amount >= 500_000

    Penalties:
    - 0.05 if age_days > 60
    - 0.10 if age_days > 120

    Clamp: min(max(score, 0.0), 1.0)
    """
    base = 0.7

    if filing.is_target_sector:
        base += 0.15

    if filing.offering_amount and filing.offering_amount >= 500_000:
        base += 0.10

    if filing.age_days > 60:
        base -= 0.05
    if filing.age_days > 120:
        base -= 0.10

    return min(max(base, 0.0), 1.0)
```

## Canonical Key Strategy

```python
# Priority order for canonical keys:

1. domain:acme.ai              # If website in Form D (rare)
2. name_loc:acme-health-inc|ca # Fallback (always available)

# Example:
external_refs = {
    "website": None,  # Usually not in Form D
}

candidates = build_canonical_key_candidates(
    domain_or_website=external_refs.get("website"),
    fallback_company_name="ACME HEALTH INC",
    fallback_region="CA",
)

# Returns: ["name_loc:acme-health-inc|ca"]

# Note: name_loc is a WEAK key (needs human review for merging)
# But good enough for initial deduplication
```

## SIC Code Mapping

```
┌──────────────┬──────────────────────────────┬────────────────┐
│  SIC Code    │  Industry                     │  Press On Fit  │
├──────────────┼──────────────────────────────┼────────────────┤
│ 2834         │ Pharmaceutical Preparations   │ healthtech     │
│ 3841         │ Surgical & Medical Devices    │ healthtech     │
│ 8071         │ Medical Laboratories          │ healthtech     │
│ 8082         │ Home Health Care Services     │ healthtech     │
├──────────────┼──────────────────────────────┼────────────────┤
│ 4911         │ Electric Services             │ cleantech      │
│ 3711         │ Motor Vehicles (EVs)          │ cleantech      │
│ 4931         │ Electric & Other Utilities    │ cleantech      │
│ 4953         │ Refuse Systems                │ cleantech      │
├──────────────┼──────────────────────────────┼────────────────┤
│ 7372         │ Prepackaged Software          │ ai_infra       │
│ 7373         │ Computer Systems Design       │ ai_infra       │
│ 7371         │ Computer Programming Services │ ai_infra       │
│ 7389         │ Business Services (AI/ML)     │ ai_infra       │
└──────────────┴──────────────────────────────┴────────────────┘
```

## Verification Gate Routing

```
┌─────────────┬──────────┬──────────────┬─────────────────┐
│ Confidence  │ Sources  │ Decision     │ Notion Status   │
├─────────────┼──────────┼──────────────┼─────────────────┤
│ 0.95        │ 1 (SEC)  │ AUTO_PUSH    │ "Source"        │
│ 0.85        │ 1 (SEC)  │ AUTO_PUSH    │ "Source"        │
│ 0.70        │ 1 (SEC)  │ AUTO_PUSH    │ "Source"        │
│ 0.55        │ 1 (SEC)  │ NEEDS_REVIEW │ "Tracking"      │
│ 0.30        │ 1 (SEC)  │ HOLD         │ (don't push)    │
└─────────────┴──────────┴──────────────┴─────────────────┘

Notes:
- Form D is authoritative → single source OK for AUTO_PUSH
- In strict_mode=True, would require 2+ sources
```

## Error Handling Strategy

```
┌──────────────────────────┬─────────────────────────────────┐
│  Error Type              │  Handling Strategy               │
├──────────────────────────┼─────────────────────────────────┤
│ Network timeout          │ Retry with exponential backoff   │
│ HTTP 403 (rate limit)    │ Increase delay, retry            │
│ HTTP 404 (missing XML)   │ Skip filing, continue            │
│ XML parse error          │ Log warning, skip filing         │
│ Invalid SIC code         │ Classify as None, continue       │
│ Missing offering amount  │ Set to None, lower confidence    │
│ Date parse error         │ Use current time as fallback     │
└──────────────────────────┴─────────────────────────────────┘
```

## Performance Characteristics

```
┌────────────────────────┬─────────────────────────────────┐
│  Metric                │  Typical Value                   │
├────────────────────────┼─────────────────────────────────┤
│ Filings per day        │ 50-200 (all sectors)             │
│ Target filings/day     │ 5-20 (healthtech/cleantech/AI)   │
│ Processing time        │ 30-60 seconds (100 filings)      │
│ API calls              │ 1 + N (RSS feed + N XML docs)    │
│ Rate limit delay       │ 0.15s per filing (~6/sec)        │
│ Memory usage           │ ~10MB (lightweight)              │
│ Network bandwidth      │ ~100KB/filing (XML is small)     │
└────────────────────────┴─────────────────────────────────┘
```

## Deployment Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         PRODUCTION                           │
│                                                              │
│  ┌──────────────┐       ┌──────────────┐                    │
│  │  Cron Job    │───────▶│  Collector   │                   │
│  │  (Daily 9am) │       │  sec_edgar   │                    │
│  └──────────────┘       └──────┬───────┘                    │
│                                 │                            │
│                                 ▼                            │
│                    ┌────────────────────┐                    │
│                    │  Discovery Engine  │                    │
│                    │    MCP Server      │                    │
│                    └────────┬───────────┘                    │
│                             │                                │
│              ┌──────────────┼──────────────┐                 │
│              ▼              ▼              ▼                 │
│     ┌────────────┐  ┌──────────────┐  ┌────────────┐        │
│     │ Postgres   │  │ Verification │  │   Notion   │        │
│     │  (Signals) │  │     Gate     │  │    CRM     │        │
│     └────────────┘  └──────────────┘  └────────────┘        │
│                                                              │
│  Monitoring:                                                 │
│  • Cloudwatch logs                                           │
│  • Error rate alerts                                         │
│  • Signal volume tracking                                    │
└─────────────────────────────────────────────────────────────┘
```

## Integration Points

### 1. MCP Server (`discovery_engine/mcp_server.py`)

```python
# Register collector
ALLOWED_COLLECTORS = frozenset({"sec_edgar", ...})

# Dynamic import
async def _run_collector_impl(collector: str, dry_run: bool):
    if collector == "sec_edgar":
        from collectors.sec_edgar import SECEdgarCollector
        async with SECEdgarCollector() as c:
            return await c.run(dry_run=dry_run)
```

### 2. Verification Gate (`verification/verification_gate_v2.py`)

```python
# Signals are already compatible
gate = VerificationGate()
result = gate.evaluate([signal])  # Signal from FormDFiling.to_signal()
```

### 3. Canonical Keys (`utils/canonical_keys.py`)

```python
# Build keys for deduplication
candidates = build_canonical_key_candidates(
    domain_or_website=filing.website,
    fallback_company_name=filing.company_name,
    fallback_region=filing.state,
)
```

### 4. Notion Connector (`connectors/notion_connector_v2.py`)

```python
# Push to Notion CRM
await connector.upsert_prospect(
    ProspectPayload(
        name=filing.company_name,
        status="Source",  # or "Tracking"
        canonical_key=candidates[0],
        discovery_id=signal.id,
        confidence_score=signal.confidence,
        signal_types=["funding_event"],
        stage=filing.stage_estimate,
    )
)
```

## Security Considerations

```
┌────────────────────────┬──────────────────────────────────┐
│  Security Aspect       │  Implementation                   │
├────────────────────────┼──────────────────────────────────┤
│ No API key required    │ SEC EDGAR is public data          │
│ User-Agent required    │ Set to identify your org          │
│ Rate limiting          │ 0.15s delay respects fair use     │
│ Input validation       │ Sanitize company names, SIC codes │
│ SQL injection          │ Use parameterized queries         │
│ XSS prevention         │ Escape output in Notion           │
│ HTTPS only             │ All SEC requests use HTTPS        │
└────────────────────────┴──────────────────────────────────┘
```

## Future Enhancements

```
┌────────────────────────────────────────────────────────────┐
│  Enhancement               │  Benefit                       │
├────────────────────────────┼────────────────────────────────┤
│ CIK → Domain mapping       │ Stronger canonical keys        │
│ Multi-filing tracking      │ Detect follow-on rounds        │
│ Investor extraction        │ Relationship mapping           │
│ Form 4 integration         │ Insider trading signals        │
│ Historical trend analysis  │ Offering size patterns         │
│ Form D amendments          │ Track changes over time        │
└────────────────────────────┴────────────────────────────────┘
```
