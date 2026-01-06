# Domain/WHOIS Collector

**Status:** Production Ready
**Signal Type:** `domain_registration`
**Confidence Range:** 0.2-0.95

## Quick Summary

Monitors domain registrations using RDAP (modern WHOIS successor) to identify startup formation signals.

**Key Features:**
- ✅ RDAP-based (structured JSON, better than WHOIS)
- ✅ Supports major tech TLDs (.ai, .io, .tech, .dev, etc.)
- ✅ Age-based scoring (fresh domains = high signal)
- ✅ Premium registrar detection
- ✅ Integration with verification gate
- ✅ Canonical key generation for deduplication

## Files

```
collectors/
├── domain_whois.py           # Main collector (750 lines)
├── test_domain_whois.py      # Full test suite with network tests
├── test_domain_quick.py      # Quick unit tests (no network)
└── README_DOMAIN_WHOIS.md    # This file

docs/
└── DOMAIN_WHOIS_COLLECTOR.md # Comprehensive documentation
```

## Usage

### Basic Usage

```python
from collectors.domain_whois import DomainWhoisCollector

# Check specific domains (enrichment mode)
collector = DomainWhoisCollector(lookback_days=90)
result = await collector.run(
    domains=["acme.ai", "startup.io"],
    dry_run=True
)

print(f"Signals found: {result.signals_found}")
```

### Single Domain Check

```python
async with DomainWhoisCollector() as collector:
    reg = await collector.check_domain("anthropic.com")

    print(f"Domain: {reg.domain}")
    print(f"Age: {reg.age_days} days")
    print(f"Registrar: {reg.registrar}")
    print(f"Score: {reg.calculate_signal_score():.2f}")
```

### Via MCP Server

```bash
/mcp__discovery-engine__run-collector collector=domain_whois dry_run=true
```

## Signal Scoring

| Age | Score | Signal Strength |
|-----|-------|----------------|
| 0-7 days | 0.8 | Very fresh |
| 8-30 days | 0.6 | Fresh |
| 31-90 days | 0.4 | Recent |
| 91-180 days | 0.3 | Somewhat recent |
| 180+ days | 0.2 | Old |

**Bonuses:**
- Tech TLD (+0.1): .ai, .io, .tech, .dev, .app
- Premium registrar (+0.05): MarkMonitor, CSC, Cloudflare, Google

**Penalties:**
- Inactive status (0.0): pending delete, expired, hold

## Testing

### Quick Tests (No Network)
```bash
cd collectors
python test_domain_quick.py
```

### Full Tests (Requires Network)
```bash
python test_domain_whois.py
# or
pytest test_domain_whois.py -v -s
```

### Example Output
```
============================================================
DOMAIN REGISTRATION TEST
============================================================
Domain: startup.ai
Age: 10 days (expected: ~10)
Is recently registered: True
Is tech TLD: True
Signal score: 0.75

[OK] All tests passed!
```

## Integration Points

### 1. Canonical Keys
Generates strong canonical keys for deduplication:
```python
domain="acme.ai" → canonical_key="domain:acme.ai"
```

### 2. Verification Gate
Routes to appropriate Notion status:
- 0.7+ confidence → "Source" (auto-push)
- 0.4-0.7 confidence → "Tracking" (review)
- <0.4 → Hold (wait for more signals)

### 3. Multi-Source Verification
Combines with other signals:
- Domain + GitHub spike = higher confidence
- Domain + incorporation = timing validation

### 4. Suppression Check
Checks against Notion CRM before pushing:
```python
# Build canonical key
canonical_key = f"domain:{domain}"

# Check if already in CRM
is_suppressed = await connector.check_suppression(
    canonical_key_candidates=[canonical_key]
)
```

## RDAP Endpoints

Supported TLDs:
- `.com/.net` → Verisign RDAP
- `.io` → NIC.IO RDAP
- `.ai` → NIC.AI RDAP
- `.tech` → CentralNIC RDAP
- `.dev/.app` → Google RDAP
- Generic fallback → RDAP.org

## Rate Limiting

**Built-in Protection:**
- 0.5 second delay between requests (~120/min)
- 3 retry attempts with exponential backoff
- 10 second timeout per request

**Performance:**
- 100 domains = ~50 seconds
- 1000 domains = ~8 minutes

## Two Modes

### Enrichment Mode (Recommended)
Check registration data for domains found via other signals:
```python
# From GitHub signal
github_domains = ["acme.ai", "beta.io"]

collector = DomainWhoisCollector()
result = await collector.run(domains=github_domains, dry_run=True)
```

### Discovery Mode (Limited)
Monitor new registrations directly:
```python
# Note: Most RDAP servers don't provide feeds
result = await collector.run(domains=None, dry_run=True)
# Will complete successfully but find 0 signals
```

## Data Extracted

```python
DomainRegistration(
    domain="acme.ai",
    tld="ai",
    registration_date=datetime(...),
    expiration_date=datetime(...),
    age_days=15,
    registrar="Cloudflare, Inc.",
    has_premium_registrar=True,
    is_tech_tld=True,
    nameservers=["ns1.cloudflare.com", "ns2.cloudflare.com"],
    status=["ok"],
    # Registrant data usually redacted by GDPR
    registrant_org="Acme Corp",  # Sometimes available
    registrant_country="US",
)
```

## Common Use Cases

### 1. Validate GitHub Signals
```python
# Found startup on GitHub with website in bio
github_signal = {"github_org": "acme-ai", "website": "https://acme.ai"}

# Check domain registration freshness
reg = await collector.check_domain("acme.ai")
if reg and reg.is_recently_registered:
    # Fresh domain + GitHub = strong signal!
    confidence = 0.85
```

### 2. Cross-Reference Incorporations
```python
# Found new UK incorporation
incorporation = {"name": "Acme AI Ltd", "domain": "acme.ai"}

# Check if domain was registered around same time
reg = await collector.check_domain("acme.ai")
if abs(reg.age_days - incorporation_age_days) < 30:
    # Domain and incorporation timing match = validated!
    confidence = 0.9
```

### 3. Tech TLD Scan
```python
# Focus on AI/ML startups
collector = DomainWhoisCollector(
    lookback_days=30,
    tech_tlds_only=True,  # .ai, .io, .tech only
)
result = await collector.run(domains=all_domains, dry_run=True)
```

## Error Handling

### Common Errors

**404 Not Found**
- Domain not registered
- Returns `None` (expected behavior)

**429 Rate Limited**
- Too many requests
- Auto-retry with backoff

**Timeout**
- RDAP server slow
- Skip and continue

**Invalid Domain**
- Malformed domain
- Log warning and skip

## Privacy & Compliance

**GDPR/WHOIS Privacy:**
- Most registrant data redacted since 2018
- Focus on technical details (date, registrar, nameservers)
- Don't scrape personal information

**What's Available:**
- ✅ Registration/expiration dates
- ✅ Registrar information
- ✅ Nameservers, status codes
- ❌ Personal contact info (usually redacted)
- ⚠️ Organization name (sometimes available)

## Configuration

```python
DomainWhoisCollector(
    lookback_days=90,      # Only signal domains within this window
    max_domains=100,        # Maximum to check per run
    tech_tlds_only=False,   # Filter for tech TLDs only
)
```

## Environment Variables

**None required!** RDAP is a public service (but respect rate limits).

## Production Checklist

- [x] RDAP endpoint support for major TLDs
- [x] Rate limiting and retries
- [x] Error handling (404, timeouts, etc.)
- [x] Signal scoring algorithm
- [x] Canonical key generation
- [x] Integration with verification gate
- [x] Unit tests (no network)
- [x] Integration tests (with network)
- [x] MCP server registration
- [x] Documentation

## Future Enhancements

1. **Zone File Monitoring**
   - Monitor DNS zone files for new registrations
   - Requires registry partnership

2. **Domain Marketplace Tracking**
   - Track domain sales/transfers
   - Sedo, Afternic APIs

3. **SSL Certificate Tracking**
   - Certificate Transparency logs
   - SSL issuance = site going live soon

4. **Historical WHOIS**
   - Compare current vs historical
   - Detect ownership changes

5. **DNS Analysis**
   - Resolve nameservers
   - Check hosting provider signals
   - MX records for email setup

## Troubleshooting

### Domain Not Found for Valid Domain
- Check TLD support (some TLDs lack RDAP)
- Try generic endpoint: https://rdap.org/domain/{domain}
- Verify domain is actually registered

### Rate Limiting
- Increase `RDAP_REQUEST_DELAY` (default: 0.5s)
- Add jitter to avoid patterns
- Implement cache

### Missing Registration Dates
- Some registries don't expose dates
- Fallback to `last_updated`
- Or skip domain

## Support & References

**Documentation:**
- Full docs: `docs/DOMAIN_WHOIS_COLLECTOR.md`
- Project context: `CLAUDE.md`

**Standards:**
- RFC 9083: RDAP Response Format
- RFC 9082: RDAP Query Format
- ICANN RDAP: https://www.icann.org/rdap

**Logging:**
```python
import logging
logging.getLogger("domain_whois").setLevel(logging.DEBUG)
```

## License & Credits

Part of the Discovery Engine for Press On Ventures.

Built following patterns from:
- `collectors/sec_edgar.py` (SEC Form D collector)
- `collectors/github.py` (GitHub spike collector)
- `verification/verification_gate_v2.py` (routing logic)
