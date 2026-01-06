# UK Companies House Collector

## Overview

Collects signals from UK Companies House API for recent incorporations. New incorporations signal early-stage startups in target sectors.

## Key Features

- **Industry Filtering**: Automatically filters by SIC codes matching Press On Ventures thesis
  - Healthtech (SIC codes: 86xxx, 21xxx, 32xxx, 87xxx)
  - Cleantech (SIC codes: 35xxx, 38xxx, 27xxx, 39xxx)
  - AI/Software (SIC codes: 62xxx, 63xxx, 72xxx)

- **Company Enrichment**: Fetches full company profiles including:
  - Company name, number, status, type
  - Incorporation date
  - SIC codes and industry classification
  - Registered address
  - Company officers (directors, secretaries)

- **Canonical Keys**: Builds stable deduplication keys using Companies House numbers

- **Confidence Scoring**: Dynamic confidence based on:
  - Active status (dissolved = 0 confidence)
  - Industry match (target sector = +0.2)
  - Recency (< 30 days = +0.15, < 90 days = +0.1)
  - Data completeness (website = +0.05, 2+ officers = +0.05)

## Setup

### 1. Get API Key

Register for a free API key at: https://developer.company-information.service.gov.uk/

### 2. Set Environment Variable

```bash
export COMPANIES_HOUSE_API_KEY="your_api_key_here"
```

Or add to `.env` file:

```
COMPANIES_HOUSE_API_KEY=your_api_key_here
```

## Usage

### Basic Usage

```python
import asyncio
from collectors.companies_house import CompaniesHouseCollector

async def run():
    collector = CompaniesHouseCollector(
        lookback_days=90,      # Search last 90 days
        max_companies=100,     # Process up to 100 companies
        target_sectors_only=True  # Only return companies in target sectors
    )

    result = await collector.run(dry_run=True)

    print(f"Status: {result.status.value}")
    print(f"Signals found: {result.signals_found}")
    print(f"New signals: {result.signals_new}")

asyncio.run(run())
```

### Command Line

```bash
# Run with default settings
python collectors/companies_house.py

# Run tests
python collectors/test_companies_house.py
```

## API Details

### Endpoints Used

1. **Search API** (`/search/companies`)
   - Filters by incorporation date range
   - Filters by company status (active)
   - Paginated results (50 per page)

2. **Company Profile API** (`/company/{number}`)
   - Full company details
   - SIC codes
   - Registered address

3. **Officers API** (`/company/{number}/officers`)
   - Directors and secretaries
   - Appointment dates
   - Used for verification and confidence scoring

### Rate Limits

- **600 requests per 5 minutes** (2 requests per second)
- Collector implements conservative rate limiting (0.6 seconds between requests)
- Automatic retry with exponential backoff on failures

### Authentication

Basic auth with API key as username and empty password:

```
Authorization: Basic base64(api_key:)
```

## SIC Code Mapping

### Healthtech (86xxx, 21xxx, 32xxx, 87xxx)
- 86101-86900: Healthcare activities
- 21100-21200: Pharmaceutical manufacturing
- 32501-32502: Medical devices
- 72110, 72190, 72200: Biotech R&D

### Cleantech (35xxx, 38xxx, 27xxx)
- 35110-35140: Electricity generation
- 35220-35230: Power distribution
- 38110-38320: Waste management
- 27110-27120: Electric motors/generators

### AI/Software (62xxx, 63xxx)
- 62011-62090: Computer programming
- 63110-63990: Information services
- 72110, 72190, 72200: Tech R&D

## Output Format

Returns `CollectorResult` with:

```python
CollectorResult(
    collector="companies_house",
    status=CollectorStatus.DRY_RUN,
    signals_found=50,
    signals_new=45,
    signals_suppressed=5,
    dry_run=True
)
```

Each signal includes:

```python
Signal(
    id="companies_house_12345678",
    signal_type="incorporation",
    confidence=0.85,
    source_api="companies_house",
    source_url="https://api.company-information.service.gov.uk/company/12345678",
    verification_status=VerificationStatus.SINGLE_SOURCE,
    verified_by_sources=["companies_house"],
    raw_data={
        "company_number": "12345678",
        "company_name": "Acme Health AI Ltd",
        "sic_codes": ["62012", "86900"],
        "industry_group": "healthtech",
        "incorporation_date": "2024-01-15T00:00:00+00:00",
        "age_days": 30,
        "stage_estimate": "Pre-Seed",
        ...
    }
)
```

## Integration with Verification Gate

Signals are automatically compatible with `verification_gate_v2`:

```python
from verification.verification_gate_v2 import VerificationGate

gate = VerificationGate()
result = gate.evaluate(signals)

if result.decision == PushDecision.AUTO_PUSH:
    # Push to Notion with status "Source"
    pass
elif result.decision == PushDecision.NEEDS_REVIEW:
    # Push to Notion with status "Tracking"
    pass
```

## Testing

Run the test suite:

```bash
python collectors/test_companies_house.py
```

Tests cover:
- SIC code classification
- Company profile parsing
- Signal generation
- Confidence scoring
- Canonical key building
- Mock API integration

## Troubleshooting

### "API key required" error
- Ensure `COMPANIES_HOUSE_API_KEY` environment variable is set
- Or pass `api_key` parameter to constructor

### Rate limit errors
- Collector automatically retries with backoff
- If persistent, increase `REQUEST_DELAY_SECONDS`

### No results found
- Check date range (use shorter `lookback_days`)
- Verify SIC codes are correct for your region
- Try `target_sectors_only=False` to see all incorporations

### Timezone issues
- All dates are stored as timezone-aware UTC
- Naive datetimes are automatically converted to UTC

## Next Steps

1. **Add to MCP Server**: Enable via internal MCP server for Claude access
2. **Suppression Cache**: Check against Notion CRM before returning signals
3. **Website Enrichment**: Cross-reference with domain registration data
4. **Social Signals**: Link to founder LinkedIn/GitHub profiles
5. **Funding Data**: Cross-reference with Crunchbase/SEC filings

## References

- [Companies House API Docs](https://developer-specs.company-information.service.gov.uk/)
- [SIC 2007 Code List](https://resources.companieshouse.gov.uk/sic/)
- [Press On Ventures Thesis](../../CLAUDE.md)
