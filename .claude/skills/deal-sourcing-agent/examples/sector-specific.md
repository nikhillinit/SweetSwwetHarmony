# Sector-Specific Sourcing Example

Filtering deal flow by investment thesis categories.

## Scenario
User wants to focus on Consumer CPG companies only.

## Conversation Flow

**User:**
> "Source consumer CPG companies"

**Claude:**
> I'll help you source Consumer CPG companies. The thesis filter will emphasize food, beverage, beauty, and household products.
>
> **Which collectors should I run?**
>
> **Recommended for CPG:**
> - sec_edgar (SIC codes: 20xx food, 28xx cosmetics)
> - news_api (product launches, brand news)
> - rss_feeds (PR newswire, food industry news)
> - job_postings (CPG hiring signals)
>
> **Or:**
> - Fast preset (github, sec_edgar, companies_house)
> - All preset (comprehensive)
>
> **Your choice?**

**User:**
> "Use the recommended ones"

**Claude:**
```bash
python run_pipeline.py collect --collectors sec_edgar,news_api,rss_feeds,job_postings
```

**Output:**
```
✓ sec_edgar: 12 signals (10 new, SIC filter: CPG only)
✓ news_api: 8 signals (consumer product news)
✓ rss_feeds: 15 signals (food/beauty PR)
✓ job_postings: 5 signals (CPG hiring)

Collection complete: 40 signals (38 new, 2 duplicates)
CPG focus: 100% match
Duration: 3.2 minutes
```

**Claude:**
> Collection complete: 40 signals, all CPG-focused.
>
> **Processing these signals...**

```bash
python run_pipeline.py process
```

**Output:**
```
Thesis breakdown:
  Consumer CPG:        35 signals (87.5% qualified)
  Consumer Health:      0 signals
  Travel & Hosp:        0 signals
  Marketplaces:         0 signals
  Excluded/Held:        5 signals (B2B, enterprise)

Pipeline Status:
  Qualified:   35 signals (ready for push)
  Held:         3 signals (thesis fit 0.2-0.3)
  Rejected:     2 signals (B2B food tech)
```

**Claude:**
> **35 qualified CPG signals ready!**
>
> **Top Categories:**
> - Food & Beverage: 18 signals
> - Beauty & Personal Care: 12 signals
> - Household Products: 5 signals
>
> **Push to Notion?** (yes/no)

**User:**
> "yes"

**Claude:**
```bash
python run_pipeline.py pipeline push --confirm
```

**Output:**
```
✓ Created 35 new prospects in Notion

CPG Breakdown:
  - Food: 18 prospects
  - Beauty: 12 prospects
  - Household: 5 prospects

Status Distribution:
  - "Source" (multi-source): 22 prospects
  - "Tracking" (single source): 13 prospects
```

---

## Other Sector Examples

### Consumer Health Tech
```
User: "Find health tech companies"

Collectors:
- arxiv (health/wellness research)
- job_postings (health tech hiring)
- github (fitness/wellness apps)
- sec_edgar (health tech SIC codes)

Thesis Keywords:
- Fitness, wellness, mental health
- Wearables, health tracking
- Supplements, nutrition
- Telemedicine, digital health
```

### Travel & Hospitality
```
User: "Source travel and hospitality startups"

Collectors:
- news_api (travel industry news)
- rss_feeds (hospitality press releases)
- github (travel booking apps)
- job_postings (restaurant/hotel tech)

Thesis Keywords:
- Travel booking, experiences
- Restaurant tech, POS systems
- Hotel management, hospitality
- Tours, activities marketplaces
```

### Consumer Marketplaces
```
User: "Find marketplace companies"

Collectors:
- github (marketplace platforms)
- job_postings (two-sided market hiring)
- sec_edgar (marketplace SIC codes)
- product_hunt (consumer marketplace launches)

Thesis Keywords:
- Two-sided markets, platforms
- Peer-to-peer, C2C
- Local services, gig economy
- Rental, sharing economy
```

---

## Thesis Filter Behavior

**High Thesis Fit (≥0.7):**
- Strong keyword matches (e.g., "food delivery", "beauty subscription")
- Consumer-facing product/service
- Direct consumer revenue model
- **Result:** Auto-qualifies, high confidence boost

**Medium Thesis Fit (0.3-0.7):**
- Some keyword matches
- Mixed B2B/B2C model
- Consumer adjacent (e.g., restaurant SaaS)
- **Result:** Qualifies, standard confidence

**Low Thesis Fit (<0.3):**
- No keyword matches
- Pure B2B/enterprise
- Developer tools, infrastructure
- **Result:** Held for manual review

**Excluded (Automatic Rejection):**
- B2B SaaS, enterprise software
- Crypto/Web3, blockchain
- Cleantech, climate (unless consumer CPG)
- Hardware-only (no consumer software)
- **Result:** Filtered out, not pushed

---

## Customizing Thesis Filters

**Via Environment Variables:**
```bash
# Lower qualification threshold (more permissive)
THESIS_QUALIFIED_THRESHOLD=0.2 python run_pipeline.py process

# Disable thesis filter entirely (not recommended)
USE_THESIS_FILTER=false python run_pipeline.py process
```

**Via Code (Advanced):**
```python
# Edit utils/thesis_matcher.py

# Add custom keywords
CONSUMER_CPG_KEYWORDS = [
    "food", "beverage", "snacks",
    "beauty", "cosmetics", "skincare",
    # Add your custom keywords here
    "pet food", "plant-based"
]

# Adjust scoring weights
def calculate_thesis_fit(text):
    # Custom scoring logic
    pass
```

---

## Sector-Specific Success Metrics

| Sector | Avg Signals/Week | Qualification Rate | False Positive Rate |
|--------|------------------|-------------------|---------------------|
| Consumer CPG | 40-60 | 75-85% | 10-15% |
| Health Tech | 25-35 | 65-75% | 15-20% |
| Travel & Hosp | 15-25 | 60-70% | 20-25% |
| Marketplaces | 20-30 | 70-80% | 15-20% |

**Notes:**
- CPG has highest signal volume (most SIC codes)
- Health Tech has moderate volume but high relevance
- Travel has lower volume due to niche focus
- Marketplaces benefit from strong keyword matching
