# Domain/WHOIS Collector

Production-ready domain registration tracker using RDAP (Registration Data Access Protocol) for early-stage VC deal sourcing.

## Overview

**Purpose:** Identify startup formation signals through domain registration activity.

**Why This Matters:**
- Domain registration often precedes official incorporation
- Tech TLDs (.ai, .io, .tech) signal tech companies
- Registration freshness shows timing of company formation
- Can validate/enrich signals from other sources (GitHub, incorporations)

**Signal Strength:** Medium (0.3-0.8 depending on age and characteristics)

## Quick Start

```python
from collectors.domain_whois import DomainWhoisCollector

# Enrichment mode: Check specific domains
collector = DomainWhoisCollector(lookback_days=90)
result = await collector.run(
    domains=["acme.ai", "startup.io"],
    dry_run=True
)

# Single domain check
async with DomainWhoisCollector() as collector:
    registration = await collector.check_domain("anthropic.com")
    print(f"Age: {registration.age_days} days")
    print(f"Score: {registration.calculate_signal_score()}")
```

## Architecture

### Two Modes

**1. Enrichment Mode** (Recommended)
- Check registration data for domains found via other signals
- Cross-reference with GitHub repos, incorporations, etc.
- Validates timing and legitimacy

**2. Discovery Mode** (Limited)
- Monitor new domain registrations directly
- **Note:** Most RDAP servers don't provide "recently registered" feeds
- Use enrichment mode in practice

### RDAP Endpoints

RDAP is the modern successor to WHOIS with:
- Structured JSON responses (vs plain text)
- Standardized schema (RFC 9083)
- Better rate limiting
- Privacy compliance (GDPR)

Supported TLDs:
- `.com/.net`: Verisign RDAP
- `.io`: NIC.IO RDAP
- `.ai`: NIC.AI RDAP
- `.tech`: CentralNIC RDAP
- `.dev/.app`: Google RDAP
- Generic fallback: RDAP.org bootstrap service

## Signal Scoring

### Base Score (by age)

| Age | Score | Signal Strength |
|-----|-------|----------------|
| 0-7 days | 0.8 | Very fresh - strong signal |
| 8-30 days | 0.6 | Fresh - good signal |
| 31-90 days | 0.4 | Recent - moderate signal |
| 91-180 days | 0.3 | Somewhat recent - weak signal |
| 180+ days | 0.2 | Old - enrichment only |

### Bonuses

- **Tech TLD** (+0.1): .ai, .io, .tech, .dev, .app, .cloud, .health, .ml, .data
- **Premium Registrar** (+0.05): MarkMonitor, CSC, Safenames, Namecheap, Cloudflare, Google

### Penalties

- **Inactive Status** (0.0): pending delete, redemption period, expired, hold

## Data Extracted

### Core Fields
```python
DomainRegistration(
    domain="acme.ai",              # Normalized domain
    tld="ai",                       # Top-level domain
    registration_date=datetime(...), # When registered
    expiration_date=datetime(...),  # When expires
    age_days=15,                    # Days since registration
)
```

### Registrar Info
```python
registrar="Cloudflare, Inc."       # Registrar name
registrar_id="146"                  # IANA registrar ID
has_premium_registrar=True          # Is it a premium registrar?
```

### Technical Details
```python
nameservers=[                       # DNS nameservers
    "ns1.cloudflare.com",
    "ns2.cloudflare.com"
]
status=[                            # RDAP status codes
    "client transfer prohibited",
    "client update prohibited"
]
```

### Registrant (Often Redacted)
```python
registrant_name="John Doe"          # Usually redacted by GDPR
registrant_org="Acme Corp"          # Sometimes available
registrant_country="US"             # Country code
```

## Integration with Discovery Engine

### Canonical Keys

Domains generate strong canonical keys:
```python
domain="acme.ai" → canonical_key="domain:acme.ai"
```

This enables:
- Deduplication across signals
- Cross-referencing with GitHub, incorporations
- Suppression checks against Notion CRM

### Verification Gate Routing

| Confidence | Status | Action |
|------------|--------|--------|
| 0.7+ | "Source" | Auto-push to Notion CRM |
| 0.4-0.7 | "Tracking" | Push for review |
| <0.4 | Hold | Wait for additional signals |

**Multi-source boost:** Domain + GitHub spike = higher confidence

### Suppression Check

Before pushing to Notion:
1. Build canonical key: `domain:acme.ai`
2. Check suppression cache
3. Skip if already in CRM

## Rate Limiting

**RDAP servers have varying rate limits:**
- Built-in delay: 0.5s between requests (~120/minute)
- Retry logic: 3 attempts with exponential backoff
- Timeout: 10 seconds per request

**Be respectful:**
- RDAP is a free service
- Don't hammer servers
- Cache results locally

## Error Handling

### Common Errors

**404 Not Found**
- Domain not registered or outside registry
- Expected behavior - return `None`

**429 Rate Limited**
- Slow down requests
- Retry with exponential backoff

**500 Server Error**
- Registry issues
- Retry or skip

**Timeout**
- RDAP server slow/unavailable
- Retry or skip

### Fallback Strategy

```python
1. Try TLD-specific endpoint (.com → Verisign)
2. If 404/error, try generic endpoint (rdap.org)
3. If still fails, log warning and continue
```

## Privacy & Compliance

**GDPR/WHOIS Privacy:**
- Most registrant data redacted since 2018
- Focus on registration date, registrar, technical details
- Don't scrape personal data

**What's Available:**
- ✅ Registration/expiration dates
- ✅ Registrar information
- ✅ Nameservers
- ✅ Status codes
- ❌ Personal contact info (usually redacted)
- ⚠️ Organization name (sometimes available)

## Usage Examples

### Example 1: Enrichment from GitHub Signal

```python
# Found startup on GitHub
github_signal = {
    "github_org": "acme-ai",
    "website": "https://acme.ai"
}

# Check domain registration freshness
collector = DomainWhoisCollector()
async with collector:
    reg = await collector.check_domain("acme.ai")

    if reg and reg.is_recently_registered:
        # Fresh domain + GitHub activity = strong signal!
        print(f"Domain registered {reg.age_days} days ago")
        print(f"Combined confidence: HIGH")
```

### Example 2: Batch Check from Incorporations

```python
# Found new incorporations
new_companies = [
    {"name": "Acme Corp", "domain": "acme.ai"},
    {"name": "Beta Inc", "domain": "beta.io"},
    {"name": "Gamma Ltd", "domain": "gamma.tech"},
]

domains = [c["domain"] for c in new_companies]

collector = DomainWhoisCollector(lookback_days=180)
result = await collector.run(domains=domains, dry_run=True)

print(f"Found {result.signals_found} recently registered domains")
```

### Example 3: Tech TLD Scan

```python
# Focus on tech TLDs only
collector = DomainWhoisCollector(
    lookback_days=30,
    tech_tlds_only=True,  # .ai, .io, .tech, .dev, etc.
)

result = await collector.run(domains=known_domains, dry_run=True)
print(f"Tech TLD signals: {result.signals_found}")
```

## Testing

### Run Unit Tests
```bash
cd collectors
python test_domain_whois.py
```

### Run Integration Tests (requires network)
```bash
pytest test_domain_whois.py -v -s
```

### Manual Testing
```python
# Check a specific domain
python -c "
import asyncio
from domain_whois import DomainWhoisCollector

async def test():
    async with DomainWhoisCollector() as c:
        reg = await c.check_domain('google.com')
        print(f'Age: {reg.age_days} days')
        print(f'Registrar: {reg.registrar}')

asyncio.run(test())
"
```

## MCP Server Integration

### Via MCP Prompt
```bash
# Run collector
/mcp__discovery-engine__run-collector collector=domain_whois dry_run=true
```

### Programmatic Usage
```python
from discovery_engine.mcp_server import _run_collector_impl

result = await _run_collector_impl("domain_whois", dry_run=True)
print(f"Status: {result.status}")
print(f"Signals: {result.signals_found}")
```

## Production Deployment

### Environment Variables
```bash
# No API keys needed - RDAP is public!
# (But respect rate limits)
```

### Recommended Schedule

**Enrichment mode:**
- Run after other collectors (GitHub, incorporations)
- Enrich domains found in last 7 days
- Frequency: Daily

**Discovery mode:**
- Not practical (no RDAP feeds available)
- Consider alternative: domain registration monitoring services

### Performance

**Speed:**
- ~2 requests/second (with 0.5s delay)
- 100 domains = ~50 seconds
- 1000 domains = ~8 minutes

**Optimization:**
- Cache results (TTL: 7 days for old domains, 1 day for fresh)
- Batch process domains from multiple signals
- Skip domains already checked recently

## Troubleshooting

### "Domain not found" for valid domain
- Check TLD support (some TLDs don't have RDAP yet)
- Try generic endpoint: rdap.org
- Verify domain is actually registered

### Rate limiting errors
- Increase `RDAP_REQUEST_DELAY` (default: 0.5s)
- Add jitter to avoid thundering herd
- Implement backoff strategy

### Timeout errors
- Some RDAP servers are slow
- Increase `RDAP_TIMEOUT` (default: 10s)
- Skip and continue on timeout

### Missing registration dates
- Some registries don't expose full history
- Fallback to `last_updated` date
- Or skip domain entirely

## Future Enhancements

### Potential Additions

1. **Zone file monitoring**
   - Monitor DNS zone files for new domains
   - Requires partnership with registries

2. **Domain marketplace tracking**
   - Track domain sales/transfers
   - Sedo, Afternic, Flippa APIs

3. **Historical WHOIS**
   - Compare current vs historical WHOIS
   - Detect ownership changes

4. **DNS analysis**
   - Resolve nameservers
   - Check for hosting provider signals
   - MX records for email setup timing

5. **SSL certificate tracking**
   - Certificate Transparency logs
   - SSL issuance = site going live

## References

- [RFC 9083: RDAP Response](https://www.rfc-editor.org/rfc/rfc9083.html)
- [RFC 9082: RDAP Query Format](https://www.rfc-editor.org/rfc/rfc9082.html)
- [ICANN RDAP](https://www.icann.org/rdap)
- [RDAP Bootstrap](https://data.iana.org/rdap/)

## Support

For questions or issues:
1. Check logs: `logging.getLogger("domain_whois")`
2. Run test suite: `python test_domain_whois.py`
3. Verify RDAP endpoint: https://rdap.org/domain/{your-domain}
4. Check CLAUDE.md for project context
