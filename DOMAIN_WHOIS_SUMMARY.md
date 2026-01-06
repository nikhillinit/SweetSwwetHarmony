# Domain/WHOIS Collector - Implementation Summary

**Status:** ✅ Complete and Production Ready
**Date:** 2026-01-06
**Type:** Signal Collector for Discovery Engine

## What Was Built

A production-ready domain registration tracker using RDAP (Registration Data Access Protocol) for early-stage VC deal sourcing at Press On Ventures.

### Core Files Created

1. **`collectors/domain_whois.py`** (750+ lines)
   - Main collector implementation
   - RDAP client with retry logic
   - Signal scoring algorithm
   - Canonical key generation
   - Integration with verification gate

2. **`collectors/test_domain_whois.py`** (250+ lines)
   - Full test suite with unit and integration tests
   - Network-based RDAP tests
   - Async/await testing with pytest

3. **`collectors/test_domain_quick.py`** (130+ lines)
   - Quick unit tests (no network required)
   - Validates core functionality
   - Scoring algorithm tests

4. **`docs/DOMAIN_WHOIS_COLLECTOR.md`** (500+ lines)
   - Comprehensive documentation
   - Usage examples
   - Integration guides
   - Troubleshooting section

5. **`collectors/README_DOMAIN_WHOIS.md`** (300+ lines)
   - Quick reference guide
   - Common use cases
   - Production checklist

### Integration Points

- **MCP Server:** Registered in `ALLOWED_COLLECTORS`
- **Verification Gate:** Compatible with `verification_gate_v2.py`
- **Canonical Keys:** Uses `utils/canonical_keys.py` for deduplication
- **Notion CRM:** Ready for suppression checks and routing

## Key Features

### 1. RDAP-Based (Modern WHOIS)
- Structured JSON responses (vs plain text)
- Standardized schema (RFC 9083)
- Better rate limiting
- Privacy compliance (GDPR)

### 2. Multi-TLD Support
Supported TLDs with dedicated endpoints:
- `.com/.net` → Verisign RDAP
- `.io` → NIC.IO RDAP
- `.ai` → NIC.AI RDAP
- `.tech` → CentralNIC RDAP
- `.dev/.app` → Google RDAP
- Generic fallback → RDAP.org

### 3. Intelligent Scoring
**Age-based scoring:**
- 0-7 days: 0.8 (very fresh)
- 8-30 days: 0.6 (fresh)
- 31-90 days: 0.4 (recent)
- 91-180 days: 0.3 (somewhat recent)
- 180+ days: 0.2 (old)

**Bonuses:**
- Tech TLD: +0.1
- Premium registrar: +0.05

**Penalties:**
- Inactive status: 0.0

### 4. Two Modes

**Enrichment Mode (Recommended):**
- Check domains found via other signals
- Cross-reference with GitHub, incorporations
- Validate timing and legitimacy

**Discovery Mode (Limited):**
- Direct monitoring of new registrations
- Most RDAP servers don't provide feeds
- Included for future expansion

### 5. Production Features
- ✅ Rate limiting (0.5s delay, 120 req/min)
- ✅ Retry logic (3 attempts, exponential backoff)
- ✅ Error handling (404, timeout, rate limit)
- ✅ Async/await throughout
- ✅ Comprehensive logging
- ✅ Type hints everywhere
- ✅ Extensive documentation

## Architecture

### Data Flow

```
1. Input: List of domains (from other signals)
   ↓
2. RDAP Query: Fetch registration data
   ↓
3. Parse: Extract dates, registrar, nameservers
   ↓
4. Score: Calculate signal strength
   ↓
5. Convert: Generate Signal object
   ↓
6. Route: Verification gate → Notion CRM
```

### Class Structure

```python
DomainRegistration
├── domain: str
├── tld: str
├── registration_date: datetime
├── registrar: str
├── nameservers: List[str]
├── status: List[str]
├── calculate_signal_score() → float
└── to_signal() → Signal

DomainWhoisCollector
├── run(domains, dry_run) → CollectorResult
├── check_domain(domain) → DomainRegistration
├── _fetch_domain_rdap(domain) → DomainRegistration
└── _parse_rdap_response(data) → DomainRegistration
```

## Integration with Discovery Engine

### Canonical Keys
```python
domain="acme.ai" → canonical_key="domain:acme.ai"
```
- Enables deduplication
- Cross-referencing with other signals
- Suppression checks

### Verification Gate Routing
```python
# High confidence (0.7+) + multi-source
→ Status: "Source" (auto-push to Notion)

# Medium confidence (0.4-0.7)
→ Status: "Tracking" (push for review)

# Low confidence (<0.4)
→ Hold (wait for additional signals)
```

### Multi-Source Verification
```python
# Domain + GitHub = higher confidence
domain_signal = {"confidence": 0.6, "source": "rdap"}
github_signal = {"confidence": 0.7, "source": "github"}
→ combined_confidence = 0.85 (multi-source boost)
```

## Testing Results

### Quick Tests (No Network)
```bash
$ python test_domain_quick.py

============================================================
DOMAIN REGISTRATION TEST
============================================================
Domain: startup.ai
Age: 10 days (expected: ~10)
Is recently registered: True
Is tech TLD: True
Signal score: 0.75

[OK] All basic tests passed!
[OK] Signal conversion tests passed!
[OK] All scoring tests passed!
[OK] Tech TLD tests passed!

ALL TESTS PASSED!
```

### Full Test Suite
- ✅ Unit tests (age, scoring, TLD detection)
- ✅ Integration tests (real RDAP queries)
- ✅ Error handling tests
- ✅ Signal conversion tests

## Usage Examples

### Basic Usage
```python
from collectors.domain_whois import DomainWhoisCollector

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
    print(f"Age: {reg.age_days} days")
    print(f"Score: {reg.calculate_signal_score():.2f}")
```

### Via MCP Server
```bash
/mcp__discovery-engine__run-collector collector=domain_whois dry_run=true
```

### Enrichment from GitHub Signal
```python
# Found startup on GitHub
github_signal = {
    "github_org": "acme-ai",
    "website": "https://acme.ai"
}

# Check domain registration freshness
async with DomainWhoisCollector() as collector:
    reg = await collector.check_domain("acme.ai")

    if reg and reg.is_recently_registered:
        # Fresh domain + GitHub activity = strong signal!
        print(f"Domain registered {reg.age_days} days ago")
        print("Combined confidence: HIGH")
```

## Performance Characteristics

### Speed
- Single domain: ~0.5-2 seconds
- 100 domains: ~50 seconds
- 1000 domains: ~8 minutes

### Rate Limits
- Built-in: 0.5s delay between requests
- Effective: ~120 requests/minute
- Respectful of RDAP servers

### Caching Recommendations
- Fresh domains (<30d): Cache 1 day
- Old domains (>180d): Cache 7 days
- Failed lookups: Cache 1 hour

## Production Deployment

### Prerequisites
- Python 3.10+
- httpx (async HTTP client)
- tenacity (retry logic)

### Environment Variables
**None required!** RDAP is a public service.

### Recommended Schedule
**Enrichment mode:**
- Run after other collectors (GitHub, incorporations)
- Enrich domains found in last 7 days
- Frequency: Daily

**Discovery mode:**
- Not practical (no feeds available)
- Consider alternative services

### Monitoring
```python
import logging
logging.getLogger("domain_whois").setLevel(logging.INFO)
```

Watch for:
- Rate limiting errors (429)
- Timeout errors (increase timeout)
- 404s (expected for unregistered domains)

## Privacy & Compliance

### GDPR Compliance
- Most registrant data redacted since 2018
- Focus on technical details only
- Don't scrape personal information

### What's Available
✅ Registration/expiration dates
✅ Registrar information
✅ Nameservers, status codes
❌ Personal contact info (redacted)
⚠️ Organization name (sometimes)

## Future Enhancements

### Potential Additions

1. **Zone File Monitoring**
   - Monitor DNS zone files
   - Requires registry partnership

2. **Domain Marketplace Tracking**
   - Track domain sales/transfers
   - Sedo, Afternic, Flippa APIs

3. **SSL Certificate Tracking**
   - Certificate Transparency logs
   - SSL issuance = site going live

4. **Historical WHOIS**
   - Compare current vs historical
   - Detect ownership changes

5. **DNS Analysis**
   - Resolve nameservers
   - Check hosting providers
   - MX records for email setup

## References & Standards

### RDAP Standards
- RFC 9083: RDAP Response Format
- RFC 9082: RDAP Query Format
- ICANN RDAP: https://www.icann.org/rdap
- RDAP Bootstrap: https://data.iana.org/rdap/

### Project References
- `CLAUDE.md` - Project instructions
- `docs/TOOL_REFERENCE_CARD.md` - All tools
- `collectors/sec_edgar.py` - Pattern reference
- `collectors/github.py` - Pattern reference

## Files & Locations

```
C:\dev\Harmonic\
├── collectors/
│   ├── domain_whois.py              # Main collector (750 lines)
│   ├── test_domain_whois.py         # Full test suite (250 lines)
│   ├── test_domain_quick.py         # Quick tests (130 lines)
│   └── README_DOMAIN_WHOIS.md       # Quick reference (300 lines)
├── docs/
│   └── DOMAIN_WHOIS_COLLECTOR.md    # Full documentation (500 lines)
├── discovery_engine/
│   └── mcp_server.py                # Updated (registered collector)
└── DOMAIN_WHOIS_SUMMARY.md          # This file
```

## Summary

✅ **Production-ready** domain registration tracker
✅ **RDAP-based** (modern WHOIS successor)
✅ **Multi-TLD support** (.ai, .io, .tech, .dev, etc.)
✅ **Intelligent scoring** (age-based + bonuses)
✅ **Two modes** (enrichment + discovery)
✅ **Full integration** (verification gate, canonical keys, MCP server)
✅ **Comprehensive testing** (unit + integration)
✅ **Extensive documentation** (500+ lines)
✅ **Rate limiting** (respectful of RDAP servers)
✅ **Error handling** (404, timeout, rate limit)
✅ **Privacy compliant** (GDPR-aware)

**Ready for production deployment!**
