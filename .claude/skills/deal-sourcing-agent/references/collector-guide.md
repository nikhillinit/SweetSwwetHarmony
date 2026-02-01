# Collector Guide

Complete reference for all 16 Discovery Engine collectors.

## Fast Preset (Recommended for Daily Scans)

### github
**Signal Type:** `github_spike`
**Source:** GitHub trending repositories with spike detection
**API Key:** `GITHUB_TOKEN` (optional, but rate limits apply)
**Rate Limit:** 5,000/hr authenticated, 60/hr unauthenticated
**Signal Strength:** 0.5-0.7
**Best For:** Developer tools, AI/ML products, open-source foundations

**Configuration:**
```bash
GITHUB_TOKEN=ghp_xxx
```

**Lookback:** 30 days
**Max Results:** 100 repos

### sec_edgar
**Signal Type:** `funding_event`
**Source:** SEC Form D filings (fundraising announcements)
**API Key:** None (User-Agent required)
**Rate Limit:** ~6 requests/second
**Signal Strength:** 0.6-0.8
**Best For:** US-based companies raising capital

**Configuration:**
```bash
# User-Agent header automatically added
```

**Lookback:** 30 days
**Max Results:** 100 filings

### companies_house
**Signal Type:** `incorporation`
**Source:** UK Companies House company registrations
**API Key:** `COMPANIES_HOUSE_API_KEY` (required)
**Rate Limit:** 600/5min
**Signal Strength:** 0.6-0.8
**Best For:** UK startups, early-stage incorporations

**Configuration:**
```bash
COMPANIES_HOUSE_API_KEY=xxx
```

**Lookback:** 90 days
**Max Results:** 100 companies

---

## All Collectors (Comprehensive)

### product_hunt
**Signal Type:** `product_launch`
**Source:** Product Hunt launches and upvotes
**API Key:** `PH_API_KEY` (required)
**Rate Limit:** Unknown (conservative: 60/min)
**Signal Strength:** 0.5-0.7
**Best For:** Consumer products, SaaS tools

**Status:** ❌ API key not configured

### hacker_news
**Signal Type:** `hn_mention`
**Source:** Hacker News Show HN posts and top stories
**API Key:** None (public API)
**Rate Limit:** None
**Signal Strength:** 0.5-0.7
**Best For:** Tech products, developer tools

**Status:** ✅ Works without API key

### arxiv
**Signal Type:** `research_paper`
**Source:** ArXiv research papers (AI/ML/CS categories)
**API Key:** None (public API)
**Rate Limit:** 1 request/3 seconds
**Signal Strength:** 0.3-0.5
**Best For:** Research-backed startups, AI foundations

**Status:** ✅ Works without API key

### uspto
**Signal Type:** `patent_filing`
**Source:** USPTO patent applications
**API Key:** None (public API)
**Rate Limit:** Unknown
**Signal Strength:** 0.4-0.6
**Best For:** Deep tech, hardware, biotech

**Status:** ✅ Works without API key

### linkedin
**Signal Type:** `company_profile`
**Source:** LinkedIn company pages and job postings
**API Key:** `PROXYCURL_API_KEY` (required, paid service)
**Rate Limit:** Varies by plan
**Signal Strength:** 0.5-0.8
**Best For:** B2C companies with hiring signals

**Status:** ❌ API key not configured

### crunchbase
**Signal Type:** `funding_event`
**Source:** Crunchbase funding announcements
**API Key:** `CRUNCHBASE_API_KEY` (required, paid service)
**Rate Limit:** Varies by plan
**Signal Strength:** 0.6-0.9
**Best For:** VC-backed companies, funding verification

**Status:** ❌ API key not configured

### opencorporates
**Signal Type:** `incorporation`
**Source:** Global company registrations (200+ jurisdictions)
**API Key:** `OPENCORPORATES_API_KEY` (optional, free tier)
**Rate Limit:** 500/month free, 10,000/month paid
**Signal Strength:** 0.6-0.75
**Best For:** International incorporations, entity verification

**Status:** ❌ API key not configured

### news_api
**Signal Type:** `news_mention`
**Source:** GNews API for consumer product news
**API Key:** `GNEWS_API_KEY` (required, free tier: 100/day)
**Rate Limit:** 100 requests/day (free)
**Signal Strength:** 0.4-0.75
**Best For:** Consumer brands, product launches, trending companies

**Status:** ✅ Configured

### rss_feeds
**Signal Type:** `news_mention`
**Source:** RSS feeds (TechCrunch, PR Newswire, etc.)
**API Key:** None (public feeds)
**Rate Limit:** None (self-imposed: 1 req/5 sec)
**Signal Strength:** 0.35-0.65
**Best For:** Broad news coverage, press releases

**Status:** ✅ Works without API key

### job_postings
**Signal Type:** `hiring_signal`
**Source:** Greenhouse/Lever public job boards
**API Key:** None (scraping public pages)
**Rate Limit:** None (self-imposed)
**Signal Strength:** 0.7-0.95
**Best For:** Growth-stage companies hiring aggressively

**Status:** ✅ Works without API key

### domain_whois
**Signal Type:** `domain_registration`
**Source:** WHOIS domain registration records
**API Key:** None (public WHOIS)
**Rate Limit:** Varies by registrar
**Signal Strength:** 0.4-0.6
**Best For:** Stealth mode companies, domain acquisitions

**Status:** ✅ Works without API key

### github_activity
**Signal Type:** `developer_activity`
**Source:** GitHub founder activity and repositories
**API Key:** `GITHUB_TOKEN` (optional)
**Rate Limit:** Same as github collector
**Signal Strength:** 0.5-0.7
**Best For:** Technical founders, developer-led companies

**Status:** ✅ Configured (shares GITHUB_TOKEN)

---

## Collector Status Summary

```
✅ Working (no key needed):   7 collectors
✅ Configured (key set):       3 collectors (github, github_activity, news_api)
❌ Disabled (missing key):     5 collectors
⛔ Abandoned:                  1 collector (changedetection)
───────────────────────────────────────────
Total Available:              10 collectors (63%)
Total Collectors:             16 collectors
```

## Enabling Disabled Collectors

### Product Hunt
```bash
# Get API key at: https://www.producthunt.com/v2/oauth/applications
PH_API_KEY=xxx
```

### Proxycurl (LinkedIn)
```bash
# Get API key at: https://nubela.co/proxycurl/
# Paid service: $29-99/month
PROXYCURL_API_KEY=xxx
```

### Crunchbase
```bash
# Get API key at: https://www.crunchbase.com/
# Paid service: $29-149/month
CRUNCHBASE_API_KEY=xxx
```

### OpenCorporates
```bash
# Get API key at: https://opencorporates.com/api_accounts/new
# Free tier: 500/month
OPENCORPORATES_API_KEY=xxx
```

### Companies House
```bash
# Get API key at: https://developer.company-information.service.gov.uk/
# Free, unlimited
COMPANIES_HOUSE_API_KEY=xxx
```

## Best Practices

**Daily Scans:**
- Use Fast preset (github, sec_edgar, companies_house)
- Low API costs, high signal quality
- 2-4 minute runtime

**Weekly Deep Dives:**
- Use All preset (all 10 working collectors)
- Comprehensive coverage
- 8-12 minute runtime

**Sector-Specific:**
- Consumer CPG: sec_edgar, news_api, rss_feeds
- Health Tech: arxiv, job_postings, linkedin
- Travel: news_api, rss_feeds, crunchbase
- Marketplaces: github, job_postings, sec_edgar

## Collector Health

Check collector status:
```bash
python run_pipeline.py health
```

**Healthy Indicators:**
- ✓ API connectivity
- ✓ Rate limits not hit
- ✓ No recent errors
- ✓ Signals found > 0

**Unhealthy Indicators:**
- ✗ Authentication failures
- ✗ Rate limit exceeded
- ✗ Network timeouts
- ✗ Zero signals (for >3 runs)
