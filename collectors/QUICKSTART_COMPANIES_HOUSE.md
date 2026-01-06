# Quick Start: UK Companies House Collector

## 1. Get API Key (5 minutes)

1. Visit https://developer.company-information.service.gov.uk/
2. Click "Register" (top right)
3. Fill in email and create password
4. Verify email
5. Click "Your applications" → "Create an application"
6. Note your API key (starts with lowercase letters/numbers)

## 2. Set Environment Variable

**Linux/Mac:**
```bash
export COMPANIES_HOUSE_API_KEY="your_api_key_here"
```

**Windows:**
```powershell
$env:COMPANIES_HOUSE_API_KEY="your_api_key_here"
```

**Or add to `.env` file:**
```
COMPANIES_HOUSE_API_KEY=your_api_key_here
```

## 3. Run the Collector

### Test it works:
```bash
cd C:\dev\Harmonic
python collectors/test_companies_house.py
```

You should see:
```
[SUCCESS] ALL TESTS PASSED
```

### Run the collector:
```bash
python collectors/companies_house.py
```

Output:
```
Starting Companies House collector (lookback=90d, max_companies=50, dry_run=True)
Searching for incorporations between 2025-10-08 and 2026-01-06
Fetched 45 companies (page 1)
Found 45 recent incorporations
Filtered to 12 companies in target sectors
Filtered to 12 active companies
```

## 4. What It Does

The collector:

1. **Searches** for companies incorporated in last 90 days
2. **Filters** by SIC codes matching Press On thesis:
   - Healthtech: 86xxx, 21xxx, 32xxx (health, pharma, devices)
   - Cleantech: 35xxx, 38xxx, 27xxx (electricity, waste, motors)
   - AI/Software: 62xxx, 63xxx (programming, info services)
3. **Enriches** with company details:
   - Company name, number, status
   - Directors and officers
   - Registered address
   - Industry classification
4. **Scores** confidence (0.0-1.0) based on:
   - Active status (dissolved = 0)
   - Target sector match
   - Recency of incorporation
   - Data completeness
5. **Returns** signals for verification gate

## 5. Typical Output

```
CompanyProfile(
    company_number='12345678',
    company_name='Acme Health AI Ltd',
    company_status='active',
    incorporation_date='2024-11-15',
    sic_codes=['62012', '86900'],
    industry_group='healthtech',
    officers=[
        {'name': 'SMITH, John', 'officer_role': 'director'},
        {'name': 'DOE, Jane', 'officer_role': 'director'}
    ]
)

Signal(
    id='companies_house_12345678',
    signal_type='incorporation',
    confidence=0.95,
    source_api='companies_house',
    verification_status=SINGLE_SOURCE,
    raw_data={
        'company_name': 'Acme Health AI Ltd',
        'age_days': 52,
        'stage_estimate': 'Pre-Seed',
        ...
    }
)
```

## 6. Common Issues

### "API key required"
- Check environment variable is set: `echo $COMPANIES_HOUSE_API_KEY`
- Make sure no quotes in API key itself
- Try restarting terminal

### "No results found"
- Try shorter lookback: `lookback_days=30`
- Check with `target_sectors_only=False` to see all companies
- UK has fewer incorporations than US - this is normal

### Rate limit errors
- Collector auto-retries, but if persistent:
- Wait 5 minutes
- Reduce `max_companies` parameter

## 7. Integration

### With Verification Gate:
```python
from collectors.companies_house import CompaniesHouseCollector
from verification.verification_gate_v2 import VerificationGate

# Collect signals
collector = CompaniesHouseCollector(lookback_days=90)
result = await collector.run(dry_run=True)

# Verify and route
gate = VerificationGate()
for signal in result.signals:
    decision = gate.evaluate([signal])

    if decision.decision == PushDecision.AUTO_PUSH:
        print(f"Push to Notion: {signal.raw_data['company_name']}")
```

### With MCP Server:
```bash
# Via internal MCP server
/mcp__discovery-engine__run-collector collector=companies_house dry_run=true
```

## 8. Next Steps

1. ✓ Verify tests pass
2. ✓ Run collector in dry-run mode
3. ☐ Enable suppression cache (check against Notion CRM)
4. ☐ Run collector in live mode (`dry_run=False`)
5. ☐ Schedule daily runs (cron or similar)
6. ☐ Cross-reference with other collectors (SEC, GitHub)

## 9. Support

- **Docs**: `collectors/COMPANIES_HOUSE_README.md`
- **Implementation**: `collectors/COMPANIES_HOUSE_IMPLEMENTATION.md`
- **API Docs**: https://developer-specs.company-information.service.gov.uk/
- **SIC Codes**: https://resources.companieshouse.gov.uk/sic/

## 10. Advanced Usage

### Custom date range:
```python
collector = CompaniesHouseCollector(
    lookback_days=30,      # Last 30 days only
    max_companies=200,     # Process up to 200
    target_sectors_only=False  # Include all sectors
)
```

### Specific SIC codes:
Edit `HEALTHTECH_SIC_CODES`, `CLEANTECH_SIC_CODES`, `AI_INFRASTRUCTURE_SIC_CODES` in `collectors/companies_house.py`

### Custom confidence scoring:
Edit the `to_signal()` method in `CompanyProfile` class

---

**Time to first signal: < 5 minutes** ⚡
