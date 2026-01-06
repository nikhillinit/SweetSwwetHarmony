# UK Companies House Collector - Implementation Summary

## Overview

Built a production-ready UK Companies House collector for the Discovery Engine that identifies early-stage startups in Press On Ventures' target sectors (Healthtech, Cleantech, AI Infrastructure).

## Files Created

### 1. `collectors/companies_house.py` (750+ lines)

Main collector implementation with:

- **CompaniesHouseCollector class**
  - Async context manager for HTTP client lifecycle
  - Configurable lookback period (default: 90 days)
  - Rate limiting (600 requests per 5 minutes)
  - Automatic retry with exponential backoff
  - Pagination support for search results

- **CompanyProfile dataclass**
  - Full company information from Companies House API
  - Property methods: `is_active`, `is_target_sector`, `is_recent`, `age_days`
  - `to_signal()` method for verification gate compatibility
  - Stage estimation (Pre-Seed, Seed, Seed+) based on age

- **Industry Classification**
  - 50+ SIC codes mapped to thesis-fit sectors
  - Healthtech: 86xxx (health), 21xxx (pharma), 32xxx (medical devices), 72xxx (biotech R&D)
  - Cleantech: 35xxx (electricity), 38xxx (waste), 27xxx (electric motors)
  - AI/Software: 62xxx (programming), 63xxx (info services), 72xxx (tech R&D)

- **API Integration**
  - Search endpoint: Find companies by incorporation date
  - Company profile endpoint: Fetch full details including SIC codes
  - Officers endpoint: Get directors for verification

- **Confidence Scoring**
  - Base: 0.6 (authoritative data)
  - +0.2 for target sector match
  - +0.15 for very recent (< 30 days)
  - +0.1 for recent (< 90 days)
  - +0.05 for website present
  - +0.05 for 2+ officers
  - 0.0 for dissolved companies (hard kill)

### 2. `collectors/test_companies_house.py` (380+ lines)

Comprehensive test suite:

- **Unit Tests**
  - SIC code classification (healthtech/cleantech/AI mapping)
  - Company profile parsing from API JSON
  - Signal generation and confidence scoring
  - Dissolved company handling (0 confidence)
  - Canonical key building (companies_house number priority)

- **Integration Tests**
  - Mock API responses
  - Full collector run with pagination
  - Error handling and retry logic

- **Test Results**: All 6 tests passing
  - SIC code classification ✓
  - Company profile parsing ✓
  - Signal generation ✓
  - Dissolved company handling ✓
  - Canonical key building ✓
  - Collector with mock API ✓

### 3. `collectors/COMPANIES_HOUSE_README.md`

User-facing documentation:

- Setup instructions (API key registration)
- Usage examples (basic and advanced)
- API details (endpoints, rate limits, authentication)
- SIC code reference (all 50+ codes explained)
- Output format specification
- Integration with verification gate
- Troubleshooting guide
- Next steps for enhancement

### 4. `discovery_engine/mcp_server.py` (updated)

Added Companies House collector to MCP server:

```python
elif collector == "companies_house":
    from collectors.companies_house import CompaniesHouseCollector
    return await CompaniesHouseCollector().run(dry_run=dry_run)
```

## Key Design Decisions

### 1. SIC Code Priority

Some R&D codes (72110, 72190, 72200) appear in multiple categories. We prioritize:
1. Healthtech (highest - biotech R&D most relevant)
2. Cleantech (energy R&D)
3. AI/Software (tech R&D)

### 2. Canonical Keys

Uses Companies House number as primary key:
- Format: `companies_house:12345678`
- Normalized to lowercase, alphanumerics only
- Priority rank: 2nd (after domain, before Crunchbase)
- Stable across company lifecycle

### 3. Confidence Scoring

Dynamic scoring based on multiple factors:
- Dissolved companies get 0 confidence (hard kill)
- All boosts only apply to active companies
- Maximum confidence capped at 1.0
- Typical range: 0.6-1.0 for active target companies

### 4. Rate Limiting

Conservative approach to respect API limits:
- 0.6 seconds between requests (2 requests/second max)
- Companies House allows 600 requests per 5 minutes
- Automatic retry on failures (3 attempts max)
- Exponential backoff (2s, 4s, 8s)

### 5. Timezone Handling

All dates stored as timezone-aware UTC:
- API returns naive dates (YYYY-MM-DD)
- Automatically converted to UTC datetime
- Prevents timezone arithmetic errors
- Consistent with verification_gate_v2

## Integration Points

### 1. Verification Gate v2

Signals fully compatible:

```python
signal = profile.to_signal()
# Signal(
#     id="companies_house_12345678",
#     signal_type="incorporation",
#     confidence=0.85,
#     verification_status=VerificationStatus.SINGLE_SOURCE,
#     verified_by_sources=["companies_house"],
#     ...
# )

gate = VerificationGate()
result = gate.evaluate([signal])
# If high confidence: PushDecision.AUTO_PUSH → "Source"
# If medium confidence: PushDecision.NEEDS_REVIEW → "Tracking"
```

### 2. Canonical Keys

Uses existing utilities:

```python
from utils.canonical_keys import build_canonical_key_candidates

candidates = build_canonical_key_candidates(
    companies_house_number="12345678",
    fallback_company_name="Acme Ltd",
    fallback_region="UK"
)
# Returns: ["companies_house:12345678", "name_loc:acme-ltd|uk"]
```

### 3. MCP Server

Accessible via internal MCP server:

```bash
# Run collector via MCP
/mcp__discovery-engine__run-collector collector=companies_house dry_run=true
```

## API Authentication

Companies House uses Basic auth:

```
Username: API_KEY
Password: (empty)
Authorization: Basic base64(API_KEY:)
```

Example:
```python
auth_string = f"{api_key}:"
auth_b64 = base64.b64encode(auth_string.encode()).decode()
headers = {"Authorization": f"Basic {auth_b64}"}
```

## Error Handling

### 1. API Errors
- 404 on company profile: Log warning, continue
- 429 rate limit: Automatic retry with backoff
- 500 server error: Retry up to 3 times

### 2. Data Validation
- Missing incorporation date: Use retrieved_at timestamp
- Missing SIC codes: No industry classification (won't match target sectors)
- Malformed address: Gracefully handle missing fields

### 3. Timezone Issues
- Naive datetimes converted to UTC
- All date arithmetic uses timezone-aware datetimes

## Testing Strategy

### Unit Tests
- Test each component in isolation
- Mock external dependencies
- Verify edge cases (dissolved companies, missing data)

### Integration Tests
- Mock API responses
- Test full collector flow
- Verify pagination and rate limiting

### Manual Testing
```bash
# Run collector with real API (requires API key)
export COMPANIES_HOUSE_API_KEY=your_key
python collectors/companies_house.py

# Run tests
python collectors/test_companies_house.py
```

## Performance

### Typical Run (90-day lookback)
- Search query: ~1 second
- Company profiles: 0.6s per company
- Officers data: 0.6s per company
- Total: ~1.2s per company + search overhead

### Example
- 50 companies found
- Time: ~60 seconds (50 * 1.2s)
- Well within rate limits (600/5min = 2/sec)

## Next Steps

### 1. Suppression Cache
Check Companies House number against Notion CRM before returning signals:

```python
connector = get_notion_connector()
suppression = await connector.check_suppression(
    canonical_key_candidates=["companies_house:12345678"]
)
if suppression.is_suppressed:
    continue  # Skip this company
```

### 2. Website Enrichment
Extract website from filing history or use external enrichment:

```python
# Fetch filing history
filings = await self._fetch_filing_history(company_number)
# Look for incorporation filing with website
website = extract_website_from_filing(filings[0])
```

### 3. Director Cross-Reference
Link directors to LinkedIn/GitHub profiles:

```python
for officer in profile.officers:
    linkedin = await linkedin_search(officer["name"])
    github = await github_search(officer["name"])
    # Add to external_refs for verification
```

### 4. Multi-Source Verification
Cross-reference with other collectors:

```python
# If Companies House signal + GitHub spike
# Confidence boost: multi-source verification
if len(sources) >= 2:
    confidence *= 1.15  # Multi-source boost
```

### 5. Stage Refinement
Use offering history or recent filings to estimate stage:

```python
# Check for recent Form D filing (if US subsidiary)
# Check for grant/subsidy announcements
# Check for recent charges (may indicate fundraising)
stage = estimate_stage_from_filings(company_number)
```

## Resources

- **API Docs**: https://developer-specs.company-information.service.gov.uk/
- **SIC Codes**: https://resources.companieshouse.gov.uk/sic/
- **API Key**: https://developer.company-information.service.gov.uk/
- **Rate Limits**: 600 requests per 5 minutes

## Conclusion

Production-ready UK Companies House collector that:
- ✓ Filters by incorporation date and thesis-fit sectors
- ✓ Extracts full company profiles with officers
- ✓ Builds canonical keys for deduplication
- ✓ Generates verification-gate-compatible signals
- ✓ Handles errors, rate limits, and edge cases
- ✓ Comprehensive test coverage (6/6 tests passing)
- ✓ Integrated with MCP server
- ✓ Full documentation for users and developers

Ready to deploy for Press On Ventures deal sourcing!
