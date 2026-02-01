# Notion Schema Reference

Complete field mappings and routing logic for Notion CRM integration.

## Required Fields

### Status (Select - EXACT strings)
**Critical:** Notion uses typo "Dilligence" (not "Diligence")

- `Source` - Multi-source, high confidence (≥0.7)
- `Initial Meeting / Call` - First contact made
- `Dilligence` - Due diligence in progress (note typo)
- `Tracking` - Single source, monitoring
- `Committed` - Investment committed
- `Funded` - Investment closed
- `Passed` - Decided not to invest
- `Lost` - Lost to another investor

### Investment Stage (Select)
- `Pre-Seed`
- `Seed`
- `Seed +`
- `Series A`
- `Series B`
- `Series C`
- `Series D`

## Discovery Engine Fields

### Discovery ID (Text)
Unique identifier for tracking signals across pipeline stages.

**Format:** `{source_api}_{unique_id}`

**Examples:**
- `sec_edgar_0001234567-26-000001`
- `github_owner/repo`
- `companies_house_12345678`

### Canonical Key (Text)
Primary deduplication key.

**Priority Order:**
1. `domain:example.com` (most stable)
2. `companies_house:12345678` (UK ID)
3. `crunchbase:abc123` (widely used)
4. `github_org:openai` (for dev tools)
5. `name_loc:acme|us-ca` (fallback)

**Example:** `domain:acme.health`

### Confidence Score (Number - 0.0 to 1.0)
Routing score from verification gate.

**Thresholds:**
- `≥ 0.7` → Status: "Source" (high confidence)
- `0.4-0.7` → Status: "Tracking" (needs review)
- `< 0.4` → Hold (don't push)

**Example:** `0.78`

### Signal Types (Multi-select)
All sources that detected this company.

**Options:**
- `github` - GitHub trending/spike
- `sec_edgar` - SEC Form D filing
- `companies_house` - UK incorporation
- `product_hunt` - Product Hunt launch
- `hacker_news` - HN mention
- `arxiv` - Research paper
- `uspto` - Patent filing
- `linkedin` - Company profile
- `crunchbase` - Funding announcement
- `opencorporates` - Global incorporation
- `news_api` - News mention
- `rss_feeds` - RSS feed
- `job_postings` - Hiring signal
- `domain_whois` - Domain registration
- `github_activity` - Founder activity

**Example:** `["github", "sec_edgar"]`

### Why Now (Text)
Narrative explaining timing and significance.

**Format:** 1-2 sentences, actionable insight

**Examples:**
- "Series A filing ($2.5M) with consumer health tech SIC code"
- "GitHub repo gained 500 stars in 7 days, trending in AI/ML"
- "UK incorporation (30 days old) in beauty SIC code with website"

## Optional Enrichment Fields

### Founding Date (Date)
Earliest date from signal sources (incorporation, domain registration, first commit).

**Format:** `YYYY-MM-DD`

**Example:** `2025-12-15`

### Social Proof Score (Number)
Aggregated engagement metrics (stars, votes, upvotes).

**Calculation:** Sum of normalized metrics across sources

**Example:** `850` (GitHub stars + Product Hunt upvotes)

### Company Description (Text)
Aggregated description from all sources.

**Format:** Merged narratives with source attribution

**Example:** "AI-powered fitness coach (GitHub). Personalized workout plans using ML (Product Hunt)."

### Website (URL)
Canonical website URL.

**Format:** `https://example.com`

**Example:** `https://acme.health`

## Routing Logic

### Multi-Source, High Confidence → "Source"
```
IF:
  - verified_by_sources.length >= 2
  - confidence >= 0.7
THEN:
  status = "Source"
```

**Rationale:** Multiple independent sources increase signal quality

### Single Source → "Tracking"
```
IF:
  - verified_by_sources.length == 1
  - confidence >= 0.4
THEN:
  status = "Tracking"
```

**Rationale:** Monitor until second source confirms

### Low Confidence → Hold
```
IF:
  - confidence < 0.4
THEN:
  Don't push to Notion (hold for batch review)
```

**Rationale:** Avoid false positives

## Hard Kill Signals

Immediately reject if any of these flags are present:

| Signal | Reason |
|--------|--------|
| `company_dissolved` | Company no longer active |
| `fraud_investigation` | Legal issues |
| `bankruptcy_filed` | Financial distress |
| `sanctioned_entity` | Regulatory block |

## Suppression Cache

Before pushing, check suppression cache to avoid duplicates:

```python
# Check if already in Notion
is_suppressed = store.check_suppression(canonical_key)

if is_suppressed:
    skip_push()
    increment_prospects_skipped()
```

**Cache Sync:**
```bash
python run_pipeline.py sync
```

Refreshes cache from Notion database.

## Schema Validation

Pre-flight check before operations:

```bash
python run_pipeline.py validate-notion-schema
```

**Validates:**
- All required fields exist
- Status options match exactly (including "Dilligence" typo)
- Investment stage options present
- Discovery Engine fields configured

## Field Mappings (Complete)

| Notion Field | Pipeline Source | Type | Required |
|--------------|-----------------|------|----------|
| Name | `raw_data.company_name` | Title | Yes |
| Status | Routing logic | Select | Yes |
| Discovery ID | `signal.id` | Text | Yes |
| Canonical Key | `raw_data.canonical_key` | Text | Yes |
| Confidence Score | `signal.confidence` | Number | Yes |
| Signal Types | `signal.verified_by_sources` | Multi-select | Yes |
| Why Now | `raw_data.why_now` | Text | Yes |
| Investment Stage | Inferred from funding | Select | No |
| Founding Date | `raw_data.founding_date` | Date | No |
| Social Proof Score | `raw_data.social_proof_score` | Number | No |
| Company Description | `raw_data.description` | Text | No |
| Website | `raw_data.website` | URL | No |

## Update Strategy

When pushing existing prospect (suppression cache hit):

```
IF canonical_key already exists in Notion:
  - Update confidence score (if higher)
  - Append new signal types (don't overwrite)
  - Update "Why Now" (merge narratives)
  - Don't change Status (preserve manual edits)
  - Increment prospects_updated
```

## Testing Schema

Test Notion integration:

```bash
# Dry run (preview only)
python run_pipeline.py pipeline push --dry-run

# Show what would be created
# Validates all fields before actual push
```

## Common Schema Issues

### Issue: "Dilligence" vs "Diligence"
**Problem:** Typo in Notion database
**Solution:** Use exact string `"Dilligence"` in code
**DO NOT:** Try to fix the typo (will break integration)

### Issue: Missing Discovery ID field
**Problem:** Field not added to Notion database
**Solution:** Add Text field named "Discovery ID"

### Issue: Multi-select options don't match
**Problem:** Signal type not in Notion multi-select
**Solution:** Add all 15 collector options to multi-select
