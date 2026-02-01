# Troubleshooting Guide

Common errors and solutions for the Deal Sourcing Agent skill.

## API Key Errors

### Missing GITHUB_TOKEN

**Error:**
```
⚠ github: SKIPPED (no GITHUB_TOKEN)
Rate limit: 60/hr (unauthenticated)
```

**Solution:**
1. Generate token at: https://github.com/settings/tokens
2. Permissions needed: `public_repo` (read-only)
3. Add to `.env`: `GITHUB_TOKEN=ghp_your_token_here`
4. Restart skill

**Alternative:** Run other collectors without GitHub

---

### Missing COMPANIES_HOUSE_API_KEY

**Error:**
```
⚠ companies_house: SKIPPED (no COMPANIES_HOUSE_API_KEY)
```

**Solution:**
1. Register at: https://developer.company-information.service.gov.uk/
2. Free, unlimited API key
3. Add to `.env`: `COMPANIES_HOUSE_API_KEY=your_key`
4. Restart skill

**Alternative:** Use Fast preset without Companies House

---

### Missing NOTION_API_KEY

**Error:**
```
✗ Notion push failed: Unauthorized (401)
```

**Solution:**
1. Create internal integration at: https://www.notion.so/my-integrations
2. Grant access to your database
3. Add to `.env`: `NOTION_API_KEY=secret_xxx`
4. Add to `.env`: `NOTION_DATABASE_ID=xxx`
5. Restart skill

**Critical:** Both API key AND database ID required

---

## Rate Limit Errors

### GitHub Rate Limit Exceeded

**Error:**
```
⚠ GitHub Rate Limit Exceeded
Current: 12 / 5,000 remaining
Resets at: 2026-01-31 14:30 UTC (in 42 minutes)
```

**Solutions:**

**Option A: Wait and retry**
```bash
# Check reset time
python run_pipeline.py health --json | grep github

# Wait, then rerun
python run_pipeline.py collect --collectors github
```

**Option B: Use authenticated token**
```bash
# Increases limit from 60/hr to 5,000/hr
GITHUB_TOKEN=ghp_xxx python run_pipeline.py collect --collectors github
```

**Option C: Run other collectors**
```bash
# Skip GitHub, use others
python run_pipeline.py collect --collectors sec_edgar,companies_house
```

---

### SEC EDGAR 429 Too Many Requests

**Error:**
```
✗ sec_edgar: ERROR (429 Too Many Requests)
```

**Cause:** SEC requires 0.15s delay between requests (~6 req/sec max)

**Solution:**
```bash
# Collector already implements delay
# This error means concurrent runs

# Check for other running processes
ps aux | grep run_pipeline

# Kill duplicate process
kill <PID>

# Retry
python run_pipeline.py collect --collectors sec_edgar
```

---

## Database Errors

### Database Locked

**Error:**
```
✗ ERROR: database is locked
```

**Cause:** Another pipeline process is running

**Solutions:**

**Option A: Wait for completion**
```bash
# Check running processes
ps aux | grep run_pipeline

# Wait for completion
# Or monitor with:
watch -n 5 'python run_pipeline.py stats'
```

**Option B: Stop other process**
```bash
# Find PID
ps aux | grep run_pipeline

# Stop gracefully
kill <PID>

# If unresponsive, force kill
kill -9 <PID>
```

**Option C: Use different database**
```bash
# Temporary database for testing
DISCOVERY_DB_PATH=test_signals.db python run_pipeline.py collect --collectors github
```

---

### Migration Version Mismatch

**Error:**
```
✗ ERROR: schema version mismatch
Expected: 21, Found: 19
```

**Solution:**
```bash
# Run migrations
python -c "from storage.signal_store import SignalStore; import asyncio; asyncio.run(SignalStore(':memory:')._run_migrations())"

# Or recreate database
mv signals.db signals.db.backup
python run_pipeline.py sync
```

---

## Notion Errors

### Notion Schema Drift

**Error:**
```
⚠ Notion schema validation failed
Missing field: Discovery ID
```

**Solution:**
```bash
# Validate schema
python run_pipeline.py validate-notion-schema

# If missing fields, add them in Notion UI:
# 1. Open Notion database
# 2. Add missing properties
# 3. Resync
python run_pipeline.py sync
```

**Required Fields:**
- Discovery ID (Text)
- Canonical Key (Text)
- Confidence Score (Number)
- Signal Types (Multi-select)
- Why Now (Text)

---

### Notion API Timeout

**Error:**
```
✗ Notion push failed: Read timeout (30s)
```

**Cause:** Notion API slow or network issue

**Solution:**
```bash
# Retry with longer timeout
NOTION_TIMEOUT=60 python run_pipeline.py pipeline push --confirm

# Or push in smaller batches
python run_pipeline.py pipeline qualified --limit 10
python run_pipeline.py pipeline push --confirm
```

---

## Collection Errors

### Zero Signals Collected

**Error:**
```
ℹ Collection complete: 0 signals
All collectors: 0 new, 0 duplicates
```

**Possible Causes:**

**1. Lookback window too short**
```bash
# Extend lookback period
# Edit collector config or use custom dates
```

**2. All signals suppressed (already in Notion)**
```bash
# Check suppression cache
python run_pipeline.py stats

# If cache stale, resync
python run_pipeline.py sync
```

**3. Thesis filter too aggressive**
```bash
# Check thesis rejection rate
python run_pipeline.py pipeline status

# If high rejection, review held signals
python run_pipeline.py pipeline qualified --category held
```

**4. Collector failures**
```bash
# Check health
python run_pipeline.py health

# Review errors
python run_pipeline.py metrics --collector github
```

---

### High Duplicate Rate

**Observation:**
```
Collection complete: 50 signals (5 new, 45 duplicates)
Duplicate rate: 90%
```

**Cause:** Running collectors too frequently

**Solution:**
```bash
# Check last run time
python run_pipeline.py stats

# Recommended frequency:
# - Fast preset: Daily
# - All preset: Weekly
# - Individual collectors: Varies

# Clear suppression cache to reprocess
# (Only if intentional)
# DELETE FROM suppression_cache WHERE created_at < '2026-01-01';
```

---

## Processing Errors

### All Signals Rejected by Thesis Filter

**Error:**
```
ℹ Processing complete
Qualified: 0, Held: 0, Rejected: 50 (100%)
```

**Cause:** Signals don't match consumer thesis

**Solutions:**

**Option A: Review rejected signals**
```bash
# See why rejected
python run_pipeline.py pipeline qualified --category rejected --limit 10
```

**Option B: Adjust thesis thresholds**
```bash
# Lower qualification threshold
# Edit utils/thesis_matcher.py
# Change THESIS_QUALIFIED_THRESHOLD from 0.3 to 0.2
```

**Option C: Run different collectors**
```bash
# Use consumer-focused collectors
python run_pipeline.py collect --collectors news_api,rss_feeds,job_postings
```

---

### All Signals Held (Low Confidence)

**Error:**
```
ℹ Processing complete
Qualified: 0, Held: 25 (100%), Rejected: 0
```

**Cause:** Confidence scores < 0.4

**Solutions:**

**Option A: Review held signals manually**
```bash
# See held signals
python run_pipeline.py pipeline qualified --category held --limit 25

# If valid, manually push selected ones
# (Requires code change to allow held → Notion)
```

**Option B: Lower confidence threshold**
```bash
# WARNING: May increase false positives
# Edit verification/verification_gate_v2.py
# Change NEEDS_REVIEW_THRESHOLD from 0.4 to 0.3
```

**Option C: Enable enrichment boost**
```bash
# Boosts confidence from metadata
ENABLE_ENRICHMENT_BOOST=true python run_pipeline.py process
```

---

## Push Errors

### Dry Run Shows Zero Creates

**Observation:**
```
Dry run: Would create 0 prospects
All signals suppressed (already in Notion)
```

**Cause:** All qualified signals already in Notion

**Solution:**
```bash
# Verify suppression cache is current
python run_pipeline.py sync

# Check Notion database directly
# Search for canonical keys

# If false positive suppression:
# DELETE FROM suppression_cache WHERE canonical_key = 'domain:example.com';
```

---

### Push Confirms but Zero Created

**Error:**
```
Push complete: 0 created, 0 updated, 12 skipped
```

**Cause:** Suppression cache hits or validation failures

**Solutions:**

**Check logs:**
```bash
# Enable debug logging
LOG_LEVEL=DEBUG python run_pipeline.py pipeline push --confirm

# Review validation errors
```

**Verify Notion connection:**
```bash
# Test Notion API
python -c "from connectors.notion_connector_v2 import NotionConnector; import asyncio; asyncio.run(NotionConnector().test_connection())"
```

---

## Health Check Failures

### Anomaly Detected: Signal Volume Drop

**Warning:**
```
⚠ Anomaly: Signal volume 80% below average
Expected: ~50 signals, Actual: 10 signals
```

**Investigation:**

**1. Check collector health**
```bash
python run_pipeline.py health
# Look for failed collectors
```

**2. Check API rate limits**
```bash
python run_pipeline.py metrics
# Look for rate_limit_hits > 0
```

**3. Review recent changes**
```bash
git log --oneline -10
# Did recent commits change collection logic?
```

---

## Skill-Specific Errors

### Skill Doesn't Trigger

**Problem:** User says "find deals" but skill doesn't activate

**Solutions:**

**1. Check skill is loaded**
```bash
# List loaded skills
ls .claude/skills/
# Should show: deal-sourcing-agent/
```

**2. Verify SKILL.md exists**
```bash
cat .claude/skills/deal-sourcing-agent/SKILL.md
# Should show YAML frontmatter
```

**3. Try explicit invocation**
```
/deal-sourcing-agent
```

**4. Check trigger phrases**
Look at `description` in YAML frontmatter - does it match what user said?

---

## Getting Help

If error persists:

1. **Check logs:**
   ```bash
   tail -f logs/pipeline.log
   ```

2. **Run health check:**
   ```bash
   python run_pipeline.py health --json > health_report.json
   ```

3. **Collect diagnostics:**
   ```bash
   python run_pipeline.py stats > stats.txt
   python run_pipeline.py metrics > metrics.txt
   ```

4. **Report issue:**
   Include health_report.json, stats.txt, metrics.txt, and error message
