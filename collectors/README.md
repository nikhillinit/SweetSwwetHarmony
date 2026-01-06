# Signal Collectors

Automated signal collectors for the Discovery Engine. Each collector monitors a specific data source for early indicators of startup activity.

## Available Collectors

### GitHub Collector (`github.py`)

Finds repositories with recent star/fork spikes indicating developer tools gaining traction.

**Focus Areas:**
- AI Infrastructure (LLM frameworks, vector DBs, inference engines)
- Developer Tools (APIs, SDKs, DevOps)
- Machine Learning (training, serving, deployment)

**Strategy:**
1. Search GitHub for trending repos (stars > 100, recently pushed)
2. Filter by relevant topics (ai, ml, llm, infrastructure, developer-tools)
3. Identify the company/org behind the repo
4. Calculate spike metrics (growth rate, velocity)
5. Build canonical keys for deduplication
6. Return signals compatible with verification_gate_v2

**Configuration:**
- `MIN_STARS`: 100 (minimum stars to consider)
- `MIN_RECENT_STARS`: 20 (stars gained in lookback period)
- `MIN_GROWTH_RATE`: 0.1 (10% growth in lookback period)
- `lookback_days`: 30 (default, configurable)

**Output:**
- Signal type: `github_spike`
- Confidence: 0.5-0.95 (based on spike strength, org ownership, website presence)
- Canonical keys: `domain:*`, `github_org:*`, `github_repo:*`, or `name_loc:*`

## Usage

### Via MCP Server (Recommended)

```bash
# Through the Discovery Engine MCP server
/mcp__discovery-engine__run-collector collector=github dry_run=true
/mcp__discovery-engine__run-collector collector=sec_edgar dry_run=true
```

### Standalone CLI

```bash
# GitHub collector
export GITHUB_TOKEN=ghp_your_token_here
python collectors/github.py --dry-run

# SEC EDGAR collector (no API key needed - public data)
python collectors/sec_edgar.py

# Production run
python collectors/github.py --lookback-days 30 --max-repos 100
```

### Programmatic

```python
# GitHub collector
from collectors.github import GitHubCollector

collector = GitHubCollector(
    github_token="ghp_...",
    lookback_days=30,
    max_repos=100
)

result = await collector.run(dry_run=True)
print(f"Found {result.signals_found} signals")

# SEC EDGAR collector
from collectors.sec_edgar import SECEdgarCollector

collector = SECEdgarCollector(
    lookback_days=30,
    max_filings=100,
    target_sectors_only=True,
)

result = await collector.run(dry_run=True)
print(f"Found {result.signals_found} Form D filings")
```

## Environment Variables

Collectors require specific API credentials (where applicable):

```bash
# GitHub (required for GitHub collector)
GITHUB_TOKEN=ghp_xxx

# SEC EDGAR (no API key needed - public data)
# Just respects User-Agent requirement

# Future collectors
COMPANIES_HOUSE_API_KEY=xxx
WHOIS_API_KEY=xxx
```

## Output Format

All collectors return a `CollectorResult`:

```python
@dataclass
class CollectorResult:
    collector: str              # "github", "companies_house", etc.
    status: CollectorStatus     # SUCCESS, DRY_RUN, ERROR, NOT_FOUND
    signals_found: int          # Total signals found
    signals_new: int            # New signals (not suppressed)
    signals_suppressed: int     # Signals already in CRM
    dry_run: bool               # True if dry run
    error_message: Optional[str]
    timestamp: str
```

## Signal Structure

Signals are compatible with `verification_gate_v2.Signal`:

```python
Signal(
    id="github_spike_abc123",
    signal_type="github_spike",
    confidence=0.75,
    source_api="github",
    source_url="https://github.com/org/repo",
    source_response_hash="sha256...",
    detected_at=datetime.utcnow(),
    verified_by_sources=["github"],
    verification_status=VerificationStatus.SINGLE_SOURCE,
    raw_data={
        "repo_full_name": "org/repo",
        "canonical_key": "domain:acme.ai",
        "canonical_key_candidates": ["domain:acme.ai", "github_org:org"],
        "stars": 1500,
        "recent_stars": 250,
        "growth_rate": 0.20,
        "why_now": "Rapid adoption: +250 stars in 30 days; 20% growth rate",
        "thesis_fit": "AI Infrastructure",
        # ... more metadata
    }
)
```

## Rate Limits

**GitHub:**
- Authenticated: 5,000 requests/hour
- Search API: 30 requests/minute
- Collector implements proactive rate limiting (1 req/sec)
- Automatic backoff on 403 rate limit errors

**Best Practices:**
- Run collectors on a schedule (not continuously)
- Use dry_run mode for testing
- Monitor rate limit headers
- Implement exponential backoff

## Testing

```bash
# GitHub collector
python collectors/github.py --dry-run --max-repos 10 --lookback-days 7

# SEC EDGAR collector
python collectors/sec_edgar.py  # Runs in dry_run mode by default

# Run unit tests
python -m pytest collectors/test_sec_edgar.py -v
```

### SEC EDGAR Collector (`sec_edgar.py`)

Identifies companies raising money through Form D filings (Regulation D private placements).

**Focus Areas:**
- Healthtech (pharmaceutical, medical devices, health services)
- Cleantech (electric services, EVs, renewable energy)
- AI Infrastructure (software, systems design, data processing)

**Why Form D?**
- Filed when companies raise capital via private placement
- Often filed BEFORE public announcements
- Strong signal of active fundraising
- Authoritative data directly from SEC

**Strategy:**
1. Fetch recent Form D filings from SEC EDGAR RSS feed
2. Parse Atom XML to extract company identifiers (CIK, name)
3. Fetch individual Form D XML for detailed offering data
4. Filter by SIC code for thesis fit (healthtech/cleantech/AI)
5. Extract offering amount, industry, location, filing date
6. Build canonical keys (CIK-based or name-based)
7. Return funding_event signals

**Configuration:**
- `lookback_days`: 30 (how far back to search)
- `max_filings`: 100 (maximum filings to process)
- `target_sectors_only`: True (filter for thesis fit)

**Output:**
- Signal type: `funding_event`
- Confidence: 0.7-0.95 (based on sector fit, offering amount, recency)
- Stage estimate: Pre-Seed to Series B (based on offering amount)
- Canonical keys: `domain:*` (if available), or `name_loc:*` fallback

**SIC Code Coverage:**
- Healthtech: 2834 (pharma), 3841 (medical devices), 8071 (labs), etc.
- Cleantech: 4911 (electric), 3711 (EVs), 4931 (utilities), etc.
- AI Infrastructure: 7372 (software), 7373 (systems design), 7371 (programming), etc.

**Rate Limiting:**
- SEC fair use policy: ~10 requests/second max
- Built-in delay: 0.15s between requests (~6 req/sec)
- User-Agent required: "Press On Ventures Discovery Engine"

## Future Collectors

Planned collectors (not yet implemented):

- `companies_house.py` - UK company incorporations
- `domain_registration.py` - New domain registrations
- `patent_filing.py` - Patent applications

## Architecture

```
collectors/
├── __init__.py           # Package exports
├── README.md             # This file
├── github.py             # GitHub spike collector
├── sec_edgar.py          # SEC Form D collector
├── test_sec_edgar.py     # SEC collector tests
└── [future collectors]

Each collector:
1. Inherits common patterns (async, rate limiting, retries)
2. Returns CollectorResult
3. Generates Signal objects
4. Builds canonical keys for deduplication
5. Respects dry_run mode
```

## Integration

Collectors integrate with the Discovery Engine via:

1. **MCP Server** (`discovery_engine/mcp_server.py`)
   - Exposes collectors as MCP prompts
   - Handles collector routing
   - Validates permissions

2. **Verification Gate** (`verification/verification_gate_v2.py`)
   - Evaluates signal confidence
   - Routes to Notion statuses
   - Multi-source verification

3. **Canonical Keys** (`utils/canonical_keys.py`)
   - Deduplication across signals
   - Multi-identifier matching
   - Stub promotion

4. **Notion Connector** (`connectors/notion_connector_v2.py`)
   - Pushes qualified prospects to CRM
   - Suppression checking
   - Status management
