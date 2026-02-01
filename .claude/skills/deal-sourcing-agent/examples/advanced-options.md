# Advanced Options Example

Advanced usage patterns for power users.

## 1. Run Single Collector

**Scenario:** Only want GitHub signals today.

```
User: "Run just the GitHub collector"

Claude: Running github collector only...

Command:
python run_pipeline.py collect --collectors github

Output:
✓ github: 25 signals (20 new, 5 suppressed)
Duration: 45 seconds
```

## 2. Dry-Run Mode

**Scenario:** Test pipeline without writing to database or Notion.

```
User: "Test the pipeline in dry-run mode"

Claude: Running in dry-run mode (no database writes)...

Commands:
python run_pipeline.py collect --collectors github,sec_edgar --dry-run
python run_pipeline.py process --dry-run
python run_pipeline.py pipeline push --dry-run

Benefits:
- No database writes
- No Notion API calls
- Preview what would happen
- Safe for testing configuration changes
```

## 3. View Detailed Metrics

**Scenario:** Debug why collector is slow or failing.

```
User: "Show me detailed metrics for the SEC EDGAR collector"

Claude:
python run_pipeline.py metrics --collector sec_edgar

Output:
Collector: sec_edgar
Runtime: 2.3 minutes
Signals Found: 18
API Calls: 42 (feed fetch + 18 detail fetches)
Rate Limit Hits: 0
Retries: 3 (2 timeouts, 1 5xx error)
Errors: 1 (404 on detail fetch - gracefully skipped)
Avg API Latency: 1.8 seconds

Performance Breakdown:
- Feed fetch: 15% (6.5s)
- Detail fetches: 80% (110s @ 0.15s delay each)
- Parsing: 5% (7s)

Bottleneck: SEC EDGAR requires 0.15s delay between requests
Recommendation: Cannot parallelize, this is optimal performance
```

## 4. Health Check Before Run

**Scenario:** Pre-flight check before running expensive All preset.

```
User: "Check system health before running all collectors"

Claude:
python run_pipeline.py health

Output:
Database:           ✓ OK (signals.db accessible, 1,847 signals)
Notion API:         ✓ OK (schema valid, authenticated)
GitHub API:         ✓ OK (4,823/5,000 requests remaining)
SEC EDGAR:          ✓ OK (last request 2 hours ago)
Companies House:    ⚠ Disabled (missing API key)
Product Hunt:       ⚠ Disabled (missing API key)
Proxycurl:          ⚠ Disabled (missing API key)
Crunchbase:         ⚠ Disabled (missing API key)
OpenCorporates:     ⚠ Disabled (missing API key)
News API:           ✓ OK (GNEWS_API_KEY configured, 87/100 daily limit)
Anomaly Monitor:    ✓ No anomalies detected

Recommendation: 10/16 collectors available. Run with:
python run_pipeline.py collect --collectors github,sec_edgar,news_api,rss_feeds,job_postings,hacker_news,arxiv,uspto,domain_whois,github_activity
```

## 5. Sync Suppression Cache

**Scenario:** Just added companies manually to Notion, need to refresh cache.

```
User: "Sync the suppression cache from Notion"

Claude:
python run_pipeline.py sync

Output:
Syncing suppression cache from Notion...
✓ Fetched 347 prospects from Notion CRM
✓ Extracted canonical keys
✓ Updated suppression_cache table

Stats:
- Total prospects in Notion: 347
- Canonical keys cached: 289 (58 without canonical key)
- Suppression cache size: 289 entries
- Sync duration: 12.3 seconds

Next collection will skip these 289 companies automatically.
```

## 6. Import from CSV

**Scenario:** Partner sent list of companies from OpenVC export.

```
User: "Import companies from OpenVC CSV"

Claude:
python run_pipeline.py import-csv --source openvc companies.csv

Output:
Importing from OpenVC CSV format...
✓ Parsed 125 rows
✓ Normalized fields (name, website, description, stage, location)
✓ Built canonical keys (domain-based)
✓ Filtered by thesis (Consumer focus)
✓ Converted to signals (confidence: 0.65 - external source)

Results:
- Total rows: 125
- Thesis match: 87 (Consumer CPG, Health, Travel, Marketplace)
- Thesis excluded: 38 (B2B, crypto, cleantech)
- Signals created: 87 (ready for processing)

Next: python run_pipeline.py process
```

## 7. Custom Collector Subset

**Scenario:** Want specific mix of collectors for CPG focus.

```
User: "Run SEC EDGAR, News API, and RSS feeds only"

Claude:
python run_pipeline.py collect --collectors sec_edgar,news_api,rss_feeds

Output:
✓ sec_edgar: 12 signals (CPG SIC codes prioritized)
✓ news_api: 8 signals (consumer product news)
✓ rss_feeds: 15 signals (PR newswire, food industry)

Collection complete: 35 signals (33 new, 2 duplicates)
CPG focus: 94% (33/35 signals)
Duration: 2.1 minutes
```

## 8. Show Pipeline Status

**Scenario:** Want to see what's in the pipeline without processing.

```
User: "What's the current pipeline status?"

Claude:
python run_pipeline.py pipeline status

Output:
Pipeline Status (signals.db):

Signals by Status:
  Collected:        1,847 total signals
  Qualified:         142 (ready for push)
  Held:               38 (thesis fit < 0.3)
  Rejected:           27 (excluded categories)
  Pushed:            347 (already in Notion)

Signals by Source:
  github:            523 signals
  sec_edgar:         412 signals
  companies_house:   287 signals
  news_api:          156 signals
  rss_feeds:         234 signals
  job_postings:       89 signals
  hacker_news:        78 signals
  arxiv:              42 signals
  others:             26 signals

Recent Activity (Last 7 Days):
  Signals collected:  156 new signals
  Qualified:           98 signals (63%)
  Pushed to Notion:    87 prospects
  Avg per day:         22 signals
```

## 9. View Qualified Signals by Confidence

**Scenario:** Only want to see high-confidence signals.

```
User: "Show me only signals with confidence > 0.75"

Claude:
python run_pipeline.py pipeline qualified --min-confidence 0.75 --limit 20

Output:
High-Confidence Signals (≥0.75):

Company Name       | Canonical Key       | Confidence | Signal Types       | Why Now
-------------------|---------------------|------------|--------------------|---------------------------
Acme Health AI     | domain:acme.health  | 0.85       | github, sec_edgar  | $2.5M Series A + 500 stars
VitaCPG Inc        | domain:vitacpg.co   | 0.82       | sec_edgar, news    | $1.8M + TechCrunch feature
FitLife Labs       | domain:fitlife.io   | 0.78       | github, sec_edgar  | $3M + trending
BeautyBox Co       | domain:beautybox.com| 0.76       | sec_edgar, rss     | $850K + launch PR

Total: 4 signals ≥0.75 confidence
```

## 10. Export Pipeline Report

**Scenario:** Generate HTML report for investor update.

```
User: "Generate a report of this week's pipeline activity"

Claude:
python run_pipeline.py report --period 7days --format html --output weekly_report.html

Output:
Generating pipeline report (last 7 days)...

Report Contents:
  - Executive Summary (signals, prospects, conversion rate)
  - Collector Performance (charts, metrics)
  - Thesis Breakdown (CPG, Health, Travel, Marketplace)
  - Top Prospects (confidence ≥0.7)
  - Held Signals Review (thesis fit 0.2-0.3)
  - Recommendations (API keys, thesis tuning, collector frequency)

✓ Report saved: weekly_report.html (2.3 MB)
✓ Open in browser: file://C:/dev/Harmonic/reports/weekly_report.html

Next: Share via email or Slack
```

---

## Advanced Flags Reference

```bash
# Dry-run mode (no writes)
--dry-run

# Collector selection
--collectors github,sec_edgar,...

# Confidence filtering
--min-confidence 0.75
--max-confidence 0.9

# Category filtering
--category qualified | held | rejected

# Limit results
--limit 20

# Output format
--output-json
--format html | json | csv

# Time period
--period 7days | 30days | 90days

# Health check
--json (machine-readable output)

# Metrics
--collector <name> (per-collector breakdown)
```

---

## Power User Workflows

### Daily Morning Routine
```bash
# 1. Health check
python run_pipeline.py health

# 2. Fast scan
python run_pipeline.py full --collectors github,sec_edgar,companies_house

# 3. Review qualified (confidence ≥0.7)
python run_pipeline.py pipeline qualified --min-confidence 0.7

# 4. Push to Notion
python run_pipeline.py pipeline push --confirm
```

### Weekly Deep Dive
```bash
# 1. Sync suppression cache
python run_pipeline.py sync

# 2. Run all collectors
python run_pipeline.py full --collectors all

# 3. Generate report
python run_pipeline.py report --period 7days --format html

# 4. Review held signals manually
python run_pipeline.py pipeline qualified --category held

# 5. Selective push (high confidence only)
python run_pipeline.py pipeline qualified --min-confidence 0.75
python run_pipeline.py pipeline push --confirm
```

### Debugging Low Signal Volume
```bash
# 1. Check health
python run_pipeline.py health

# 2. View metrics per collector
python run_pipeline.py metrics

# 3. Check pipeline status
python run_pipeline.py pipeline status

# 4. Review thesis rejections
python run_pipeline.py pipeline qualified --category rejected --limit 20

# 5. Test with dry-run
python run_pipeline.py collect --collectors github --dry-run
```

---

## Tips & Tricks

**Performance:**
- Run collectors in parallel (they're independent)
- Use dry-run for testing without DB writes
- Sync suppression cache before large runs

**Quality:**
- Focus on high-confidence signals (≥0.75) for immediate outreach
- Review held signals weekly (thesis fit 0.2-0.3)
- Monitor false positive rate (<15% ideal)

**Maintenance:**
- Health check before automated runs
- Metrics review weekly
- Suppression cache sync daily (if manually adding to Notion)

**Customization:**
- Environment variables for thresholds
- Collector subsets for sector focus
- Custom thesis keywords in `utils/thesis_matcher.py`
