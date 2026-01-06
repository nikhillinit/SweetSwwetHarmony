# Domain/WHOIS Collector - Quick Start

**One-minute guide to get started with the Domain/WHOIS collector.**

## What It Does

Tracks domain registrations via RDAP to identify startup formation signals. Fresh domains (.ai, .io, .tech) = potential new companies.

## Quick Test

```bash
cd C:\dev\Harmonic
python collectors\test_domain_quick.py
```

**Expected output:**
```
Domain: startup.ai
Age: 10 days
Signal score: 0.75
[OK] All tests passed!
```

## Basic Usage

```python
from collectors.domain_whois import DomainWhoisCollector

# Check specific domains
collector = DomainWhoisCollector()
result = await collector.run(
    domains=["acme.ai", "startup.io"],
    dry_run=True
)

print(f"Signals: {result.signals_found}")
```

## Single Domain

```python
async with DomainWhoisCollector() as collector:
    reg = await collector.check_domain("anthropic.com")
    print(f"Age: {reg.age_days} days")
    print(f"Score: {reg.calculate_signal_score():.2f}")
```

## Via MCP

```bash
/mcp__discovery-engine__run-collector collector=domain_whois dry_run=true
```

## Scoring Cheat Sheet

| Age | Score | Meaning |
|-----|-------|---------|
| 0-7d | 0.8 | 🔥 Very fresh - strong signal |
| 8-30d | 0.6 | ✨ Fresh - good signal |
| 31-90d | 0.4 | 📈 Recent - moderate signal |
| 90-180d | 0.3 | 📊 Somewhat recent - weak signal |
| 180d+ | 0.2 | 📁 Old - enrichment only |

**Bonuses:**
- Tech TLD (.ai, .io, .tech): +0.1
- Premium registrar (Cloudflare, Google): +0.05

## Common Use Cases

### 1. Validate GitHub Signal
```python
# Found on GitHub, check domain freshness
reg = await collector.check_domain("acme.ai")
if reg and reg.age_days <= 30:
    print("Fresh domain + GitHub = strong signal!")
```

### 2. Batch Check
```python
# Check multiple domains from incorporations
domains = ["acme.ai", "beta.io", "gamma.tech"]
result = await collector.run(domains=domains, dry_run=True)
```

### 3. Tech TLDs Only
```python
collector = DomainWhoisCollector(tech_tlds_only=True)
result = await collector.run(domains=all_domains, dry_run=True)
```

## Files

- **Main:** `collectors/domain_whois.py`
- **Quick Tests:** `collectors/test_domain_quick.py`
- **Full Tests:** `collectors/test_domain_whois.py`
- **Docs:** `docs/DOMAIN_WHOIS_COLLECTOR.md`
- **README:** `collectors/README_DOMAIN_WHOIS.md`

## Need Help?

1. **Run tests:** `python test_domain_quick.py`
2. **Read docs:** `docs/DOMAIN_WHOIS_COLLECTOR.md`
3. **Check logs:** `logging.getLogger("domain_whois")`
4. **Project context:** `CLAUDE.md`

## Configuration

```python
DomainWhoisCollector(
    lookback_days=90,      # Signal window
    max_domains=100,        # Limit per run
    tech_tlds_only=False,   # Filter for tech TLDs
)
```

## No Setup Required!

- ✅ No API keys needed
- ✅ No environment variables
- ✅ RDAP is public
- ✅ Just run it!

## Rate Limits

- Built-in: 0.5s delay between requests
- Speed: ~120 domains/minute
- Be respectful of RDAP servers

## Integration

**Canonical Keys:**
```python
"acme.ai" → "domain:acme.ai"
```

**Verification Gate:**
- 0.7+ → "Source" (auto-push)
- 0.4-0.7 → "Tracking" (review)
- <0.4 → Hold

**Multi-Source:**
Domain + GitHub = confidence boost!

## Production Ready

✅ Rate limiting
✅ Retry logic
✅ Error handling
✅ Async/await
✅ Full tests
✅ Documentation

**Start using it now!**
