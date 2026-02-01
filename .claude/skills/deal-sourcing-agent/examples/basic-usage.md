# Basic Usage Example

Simple deal sourcing workflow using the Fast preset.

## Scenario
User wants to find new deals quickly without specifying sectors or collectors.

## Conversation Flow

### Step 1: Initiation

**User:**
> "Find me some new deals"

**Claude (Skill Activates):**
> I'll help you source new deals. Which collectors should I run?
>
> **Options:**
> - **Fast** (github, sec_edgar, companies_house) - ~2 min, daily scan
> - **All** (16 collectors) - ~10 min, comprehensive weekly search
> - **Custom** - You choose specific collectors
>
> **Your choice?**

**User:**
> "Fast"

---

### Step 2: Collection

**Claude:**
> Running Fast collectors (github, sec_edgar, companies_house)...

```bash
python run_pipeline.py collect --collectors github,sec_edgar,companies_house
```

**Output:**
```
✓ github: 15 signals (12 new, 3 suppressed)
✓ sec_edgar: 8 signals (7 new, 1 suppressed)
⚠ companies_house: SKIPPED (missing COMPANIES_HOUSE_API_KEY)

Collection complete: 23 signals (19 new, 4 duplicates)
Duration: 1.8 minutes
```

**Claude:**
> Collection complete: 23 signals (19 new, 4 duplicates)
>
> **Next steps:**
> - **A)** Process these signals (verify + thesis filter)
> - **B)** Adjust collectors (add/remove sources)
> - **C)** Cancel
>
> **Your choice?**

**User:**
> "A"

---

### Step 3: Processing

**Claude:**
> Processing 19 new signals through verification gate and thesis filter...

```bash
python run_pipeline.py process
```

**Output:**
```
Pipeline Status:
  Qualified:   12 signals (ready for push)
  Held:         5 signals (need review)
  Rejected:     2 signals (excluded by thesis)
  Pushed:       0 signals (not yet synced)

Thesis breakdown:
  Consumer CPG:        6 signals
  Consumer Health:     4 signals
  Travel & Hosp:       2 signals
  Marketplaces:        0 signals

Processing complete: 12 qualified, 5 held, 2 rejected
Duration: 18 seconds
```

**Claude:**
> Processing complete: 12 qualified, 5 held, 2 rejected
>
> **Next steps:**
> - **A)** Review qualified signals before pushing
> - **B)** Push all qualified to Notion now
> - **C)** Review held signals (thesis fit < 0.3)
> - **D)** Show metrics breakdown
>
> **Your choice?**

**User:**
> "Show me the qualified ones first"

---

### Step 4: Review

**Claude:**
> Here are the top 12 qualified signals:

```bash
python run_pipeline.py pipeline qualified --limit 12
```

**Output:**
```
Company Name           | Canonical Key         | Confidence | Signal Types      | Why Now
-----------------------|-----------------------|------------|-------------------|----------------------------------
Acme Health AI         | domain:acme.health    | 0.78       | github, sec_edgar | Series A filing ($2.5M) + GitHub spike
VitaCPG Inc            | domain:vitacpg.co     | 0.72       | sec_edgar         | Form D filing ($1.8M, CPG SIC)
TravelNow              | github:travelnow/app  | 0.68       | github            | 500 stars in 7 days (travel tech)
FitLife Labs           | domain:fitlife.io     | 0.65       | github, sec_edgar | Health tech + $3M filing
BeautyBox Co           | domain:beautybox.com  | 0.62       | sec_edgar         | CPG SIC, $850K filing
HealthTrack            | github:healthtrack    | 0.58       | github            | 300 stars, wellness app
RestaurantOS           | domain:restos.tech    | 0.55       | github            | Hospitality SaaS, trending
FoodieMarket           | domain:foodie.market  | 0.52       | github            | Food marketplace, 200 stars
SnackCo                | sec_edgar_0001234567  | 0.50       | sec_edgar         | CPG SIC, $500K filing
WellnessAI             | github:wellness-ai    | 0.48       | github            | Mental health app, spike
TravelMatch            | domain:travelmatch.io | 0.45       | github            | Travel marketplace
GymTech                | domain:gymtech.app    | 0.42       | github            | Fitness tracker, 150 stars
```

**Claude:**
> 12 qualified signals ready for push. All meet thesis criteria (CPG, health tech, travel, or marketplaces).
>
> **Decision:**
> - **A)** Push all 12 to Notion
> - **B)** Filter further (show only confidence > 0.75)
> - **C)** Exclude specific companies
>
> **Your choice?**

**User:**
> "Push them"

---

### Step 5: Push to Notion

**Claude:**
> Pushing 12 qualified signals to Notion CRM...

```bash
# First, dry run to preview
python run_pipeline.py pipeline push --dry-run
```

**Output (Dry Run):**
```
Would create:
  • 10 new prospects (Status: "Source" - multi-source or high confidence)
  • 2 new prospects (Status: "Tracking" - single source)

Notion fields to populate:
  - Discovery ID
  - Canonical Key
  - Confidence Score
  - Signal Types
  - Why Now
  - Investment Stage (inferred)
  - Founding Date (if available)

Suppression check: 0 duplicates detected
```

**Claude:**
> Preview: Would create 12 new prospects (10 "Source", 2 "Tracking").
>
> **Proceed with push?** (yes/no)

**User:**
> "yes"

**Claude:**
```bash
python run_pipeline.py pipeline push --confirm
```

**Output:**
```
✓ Created 12 new prospects in Notion

Stats:
  - prospects_created: 12
  - prospects_updated: 0
  - prospects_skipped: 0

Notion Database URL: https://notion.so/database/xyz123...

Breakdown by status:
  - "Source" (multi-source, conf >= 0.7): 10 prospects
  - "Tracking" (single source): 2 prospects
```

**Claude:**
> ✓ Successfully pushed 12 new prospects to Notion CRM!
>
> **View in Notion:** https://notion.so/database/xyz123
>
> **Breakdown:**
> - 10 prospects at "Source" status (high confidence)
> - 2 prospects at "Tracking" status (single source, monitoring)
>
> Next: Health check to review system status?

**User:**
> "Yes"

---

### Step 6: Health Check

**Claude:**
> Running post-execution health check...

```bash
python run_pipeline.py health --json
```

**Output:**
```json
{
  "database": {
    "status": "healthy",
    "path": "signals.db",
    "size_mb": 12.3,
    "signals_count": 1847
  },
  "notion_api": {
    "status": "healthy",
    "schema_valid": true,
    "rate_limit": "2.8/3 req/sec"
  },
  "github_api": {
    "status": "healthy",
    "rate_limit": "4,823/5,000 remaining",
    "reset_at": "2026-01-31 15:00:00 UTC"
  },
  "sec_edgar": {
    "status": "healthy",
    "last_request": "0.15s ago"
  },
  "companies_house": {
    "status": "disabled",
    "reason": "missing COMPANIES_HOUSE_API_KEY"
  },
  "anomaly_monitor": {
    "status": "healthy",
    "anomalies_detected": 0
  }
}
```

**Claude:**
> **Health Check Results:**
>
> ✅ **All systems healthy**
>
> **Component Status:**
> - Database: ✓ OK (1,847 signals stored)
> - Notion API: ✓ OK (schema valid)
> - GitHub API: ✓ OK (4,823/5,000 requests remaining)
> - SEC EDGAR: ✓ OK
> - Companies House: ⚠ Disabled (missing API key)
> - Anomaly Monitor: ✓ No issues detected
>
> **Recommendations:**
> 1. **Review 5 held signals** - Low thesis fit, may need manual review
> 2. **Schedule next run:** Tomorrow at 9am (GitHub rate limit resets)
> 3. **Optional:** Add `COMPANIES_HOUSE_API_KEY` for UK company coverage
>
> **Next actions:**
> - A) Review held signals now
> - B) Schedule automated run
> - C) View detailed metrics
> - D) Done

**User:**
> "Done"

**Claude:**
> Perfect! You've successfully sourced 12 new deals:
> - 10 high-confidence prospects ("Source" status)
> - 2 single-source prospects ("Tracking" status)
>
> **Summary:**
> - Time: 3.2 minutes total
> - Signals collected: 23 (19 new)
> - Qualified: 12 (thesis fit)
> - Pushed to Notion: 12 new prospects
>
> Happy deal sourcing! 🚀

---

## Key Takeaways

### What Worked Well
- Fast preset completed in <2 minutes (collection phase)
- High qualification rate (12/19 = 63%)
- Clear decision points throughout workflow
- Preview before actual Notion push

### Common Variations
- **Skip review:** Go directly from processing to push
- **Review held signals:** Examine thesis_fit < 0.3 before finishing
- **Metrics deep dive:** Check per-collector performance

### Time Breakdown
| Phase | Duration | Percentage |
|-------|----------|------------|
| Collection | 1.8 min | 56% |
| Processing | 0.3 min | 9% |
| Review | 0.5 min | 16% |
| Push | 0.4 min | 13% |
| Health Check | 0.2 min | 6% |
| **Total** | **3.2 min** | **100%** |

### Next Steps
- Run daily with Fast preset
- Run weekly with All preset for comprehensive coverage
- Review held signals weekly
- Monitor GitHub rate limits (resets hourly)
