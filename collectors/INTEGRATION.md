# SEC EDGAR Collector - Integration Guide

This document explains how to integrate the SEC EDGAR collector with the Discovery Engine MCP server.

## Quick Start

The collector is already registered in `discovery_engine/mcp_server.py` under `ALLOWED_COLLECTORS`. To use it:

```bash
# Via MCP prompt
/mcp__discovery-engine__run-collector collector=sec_edgar dry_run=true
```

## Integration Steps (Already Complete)

### 1. Collector Registration

The collector is registered in `mcp_server.py`:

```python
ALLOWED_COLLECTORS = frozenset({
    "github",
    "companies_house",
    "domain_registration",
    "sec_edgar",  # ✓ Added
})
```

### 2. Dynamic Import

Update `_run_collector_impl` in `mcp_server.py`:

```python
async def _run_collector_impl(collector: str, dry_run: bool) -> CollectorResult:
    """Run the actual collector logic."""

    if collector == "sec_edgar":
        from collectors.sec_edgar import SECEdgarCollector
        async with SECEdgarCollector() as collector_instance:
            return await collector_instance.run(dry_run=dry_run)

    # ... other collectors

    raise NotImplementedError(f"Collector '{collector}' not yet implemented")
```

### 3. Verification Gate Integration

Signals flow through the verification gate automatically:

```python
from verification.verification_gate_v2 import VerificationGate

gate = VerificationGate()

# SEC EDGAR signals are already compatible
for filing in filings:
    signal = filing.to_signal()  # Returns verification_gate_v2.Signal
    result = gate.evaluate([signal])

    if result.decision == PushDecision.AUTO_PUSH:
        # Push to Notion with status "Source"
        pass
    elif result.decision == PushDecision.NEEDS_REVIEW:
        # Push to Notion with status "Tracking"
        pass
```

### 4. Canonical Key Building

The collector builds canonical keys for deduplication:

```python
from utils.canonical_keys import build_canonical_key_candidates

# In FormDFiling.to_signal()
external_refs = {
    "website": self.website or "",
    **self.external_refs
}

# This generates keys like:
# - domain:acme.ai (if website available)
# - name_loc:acme-health-inc|california (fallback)
```

### 5. Notion Push

Once integrated with the MCP server, signals automatically:

1. Get verified by the verification gate
2. Build canonical keys for dedupe checking
3. Check suppression (already in Notion?)
4. Push to Notion with appropriate status

## Full Flow Example

```python
# 1. Run collector via MCP
/mcp__discovery-engine__run-collector collector=sec_edgar dry_run=false

# 2. Collector fetches Form D filings
filings = await collector._fetch_recent_form_d_filings()

# 3. Filter for target sectors
filings = [f for f in filings if f.is_target_sector]

# 4. Convert to signals
signals = [f.to_signal() for f in filings]

# 5. Check suppression
for signal in signals:
    canonical_keys = build_canonical_key_candidates(
        domain_or_website=signal.raw_data.get("website"),
        fallback_company_name=signal.raw_data.get("company_name"),
        fallback_region=signal.raw_data.get("state"),
    )

    suppression = await connector.check_suppression(
        canonical_key_candidates=canonical_keys,
    )

    if not suppression.is_suppressed:
        # 6. Run through verification gate
        result = gate.evaluate([signal])

        if result.decision in [PushDecision.AUTO_PUSH, PushDecision.NEEDS_REVIEW]:
            # 7. Push to Notion
            await connector.upsert_prospect(
                ProspectPayload(
                    name=signal.raw_data["company_name"],
                    status=result.suggested_status,  # "Source" or "Tracking"
                    canonical_key=canonical_keys[0],
                    discovery_id=signal.id,
                    confidence_score=result.confidence_score,
                    signal_types=["funding_event"],
                    why_now=f"Filed Form D for ${signal.raw_data['offering_amount']:,.0f}",
                )
            )
```

## Signal Structure

SEC EDGAR signals have this structure:

```python
Signal(
    id="sec_edgar_1234567890-24-001234",
    signal_type="funding_event",
    confidence=0.85,  # High for healthtech with meaningful offering
    source_api="sec_edgar",
    source_url="https://www.sec.gov/...",
    detected_at=datetime(2024, 1, 15),
    verification_status=VerificationStatus.SINGLE_SOURCE,
    verified_by_sources=["sec_edgar"],
    raw_data={
        "cik": "0001234567",
        "company_name": "ACME HEALTH INC",
        "offering_amount": 2_500_000,
        "offering_sold": 1_500_000,
        "sic_code": "2834",
        "industry_group": "healthtech",
        "state": "CA",
        "country": "US",
        "stage_estimate": "Seed",
        "filing_date": "2024-01-15T00:00:00Z",
        "issuer_type": "Corporation",
        "website": None,  # Often not available in Form D
    }
)
```

## Confidence Scoring

The collector uses this confidence formula:

```
Base: 0.7 (Form D is authoritative)
+ 0.15 if target sector (healthtech/cleantech/AI)
+ 0.10 if offering_amount >= $500K
- 0.05 if 60-120 days old
- 0.10 if >120 days old

Final: min(max(score, 0.0), 1.0)
```

### Examples:

| Scenario | Base | Sector | Amount | Age | Final |
|----------|------|--------|--------|-----|-------|
| Healthtech, $2.5M, 30d old | 0.70 | +0.15 | +0.10 | 0 | 0.95 |
| Cleantech, $5M, 90d old | 0.70 | +0.15 | +0.10 | -0.05 | 0.90 |
| Other, $200K, 10d old | 0.70 | 0 | 0 | 0 | 0.70 |
| AI, $1M, 150d old | 0.70 | +0.15 | +0.10 | -0.10 | 0.85 |

## Verification Gate Routing

Based on confidence and multi-source verification:

| Confidence | Sources | Decision | Notion Status |
|------------|---------|----------|---------------|
| 0.85 | SEC only | AUTO_PUSH | "Source" |
| 0.70 | SEC only | AUTO_PUSH (non-strict) | "Source" |
| 0.55 | SEC only | NEEDS_REVIEW | "Tracking" |
| 0.30 | SEC only | HOLD | (don't push) |

Note: In strict mode, multi-source verification is required for AUTO_PUSH.

## Stage Estimation

The collector estimates funding stage from offering amount:

| Offering Amount | Stage | Press On Fit? |
|----------------|-------|---------------|
| < $500K | Pre-Seed | Maybe (small) |
| $500K - $3M | Seed | ✓ Perfect fit |
| $3M - $10M | Seed+ | ✓ Good fit |
| $10M - $30M | Series A | Outside range |
| $30M+ | Series B+ | Outside range |

Press On Ventures focus: **$500K-$3M checks** at **Pre-Seed to Seed+** stage.

## Error Handling

The collector handles common errors gracefully:

```python
# Network errors
try:
    response = await self._client.get(url)
    response.raise_for_status()
except httpx.HTTPStatusError as e:
    logger.error(f"HTTP error {e.response.status_code}")
    return CollectorResult(status=CollectorStatus.ERROR, ...)
except httpx.RequestError as e:
    logger.error(f"Network error: {e}")
    return CollectorResult(status=CollectorStatus.ERROR, ...)

# XML parsing errors
try:
    root = ET.fromstring(xml_content)
except ET.ParseError as e:
    logger.warning(f"XML parse error: {e}")
    # Skip this filing, continue with others
```

## Rate Limiting

SEC EDGAR has generous rate limits, but we respect fair use:

- **SEC Policy:** ~10 requests/second max
- **Collector:** 0.15s delay = ~6 req/sec (well under limit)
- **User-Agent:** Required - set to "Press On Ventures Discovery Engine"

```python
REQUEST_DELAY_SECONDS = 0.15

for filing in filings:
    await self._enrich_filing(filing)
    await asyncio.sleep(REQUEST_DELAY_SECONDS)
```

## Testing

### Unit Tests

```bash
python -m pytest collectors/test_sec_edgar.py -v
```

Tests cover:
- Atom feed parsing
- Form D XML parsing
- SIC code classification
- Confidence scoring
- Signal conversion
- Error handling

### Integration Test (Dry Run)

```bash
# Run collector standalone
python collectors/sec_edgar.py

# Expected output:
# ==========================================
# SEC EDGAR COLLECTOR RESULT
# ==========================================
# Status: dry_run
# Signals found: 15
# New signals: 15
# Suppressed: 0
# Dry run: True
```

### Via MCP Server

```bash
# Through MCP (requires MCP server running)
/mcp__discovery-engine__run-collector collector=sec_edgar dry_run=true

# Expected JSON response:
{
  "collector": "sec_edgar",
  "status": "dry_run",
  "signals_found": 15,
  "signals_new": 15,
  "signals_suppressed": 0,
  "dry_run": true,
  "timestamp": "2024-01-15T12:00:00Z"
}
```

## Production Deployment

### Schedule

Run the collector daily to catch new filings:

```bash
# Cron job (daily at 9am EST - after SEC publishes overnight filings)
0 9 * * * /usr/bin/python /path/to/collectors/sec_edgar.py --lookback-days 7
```

### Monitoring

Monitor these metrics:

- Signals found per day (expect 5-20 in target sectors)
- HTTP errors (should be rare - SEC is reliable)
- Parse errors (track XML format changes)
- Rate limit warnings (shouldn't happen with 0.15s delay)

### Alerting

Set up alerts for:

- Zero signals found for 3+ days (possible API change)
- HTTP 403 errors (rate limit - increase delay)
- Parse errors >10% (SEC format change)

## Troubleshooting

### No signals found

```python
# Check if SEC EDGAR is accessible
curl "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=D&output=atom"

# Check date filter
collector = SECEdgarCollector(lookback_days=90)  # Increase lookback
```

### XML parse errors

```python
# SEC sometimes changes XML schema
# Check raw XML:
logger.debug(f"Raw XML: {xml_content[:500]}")

# Update parsing logic if needed
```

### Confidence too low

```python
# Adjust thresholds in to_signal():
base_confidence = 0.8  # Increase from 0.7

# Or relax verification gate:
gate = VerificationGate(strict_mode=False)
```

## Future Enhancements

Potential improvements:

1. **Website Enrichment:** Cross-reference CIK with other databases to find company websites
2. **Multi-Filing Detection:** Track multiple Form D amendments for same company
3. **Investor Network:** Extract related persons (investors, executives) for relationship mapping
4. **Historical Trends:** Track offering amounts over time to identify follow-on rounds
5. **Form 4 Integration:** Combine with Form 4 (insider trading) for additional signals

## References

- **SEC EDGAR:** https://www.sec.gov/edgar/searchedgar/accessing-edgar-data.htm
- **Form D Guide:** https://www.sec.gov/files/formd.pdf
- **SIC Codes:** https://www.sec.gov/corpfin/division-of-corporation-finance-standard-industrial-classification-sic-code-list
- **Rate Limits:** https://www.sec.gov/os/webmaster-faq#code-support
