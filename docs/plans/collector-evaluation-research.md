# Collector Evaluation Research

**Date:** 2026-01-25
**Status:** COMPLETE

## Executive Summary

| Collector | Decision | Rationale |
|-----------|----------|-----------|
| Wellfound (AngelList) | **ABANDON** | No API, scraping violates ToS |
| App Store (iOS) | **DEFER** | iTunes Search API is search-only, no new app discovery |
| Play Store (Android) | **DEFER** | No official API, 3rd-party services expensive |
| Press Releases | **DEFER** | RTPR.io at $30/mo is viable alternative |
| meter-sdk | **EVALUATE** | $29/mo Pro tier worth testing for URL profiler |

---

## Detailed Analysis

### 1. Wellfound (AngelList Talent)

**Decision: ABANDON**

**Research Findings:**
- API was deprecated in 2023
- No official developer API available
- Platform uses aggressive anti-scrape protection (Cloudflare, etc.)
- Scraping violates Terms of Service
- Third-party integrations limited to ATS systems (Workable, Lever, Greenhouse)

**Alternatives Considered:**
- Clearout Chrome Extension (manual, not automated)
- ScrapFly scraping service (ToS violation risk)

**Recommendation:** Do not pursue. The legal/ToS risk outweighs potential signal value.

**Sources:**
- [API Tracker - Wellfound](https://apitracker.io/a/wellfound)
- [ScrapFly Wellfound Guide](https://scrapfly.io/blog/posts/how-to-scrape-wellfound-aka-angellist)

---

### 2. Apple App Store (iOS Apps)

**Decision: DEFER**

**Research Findings:**
- **iTunes Search API** exists but is search-only (by keyword/ID)
- No endpoint for discovering NEW apps or browsing categories
- Rate limited to ~20 calls/minute
- **App Store Connect API** is only for managing your own apps
- **Enterprise Partner Feed (EPF)** may have new app data but requires enterprise agreement

**Pricing:**
- iTunes Search API: Free but limited
- EPF: Enterprise pricing (not publicly disclosed)

**Alternatives:**
| Service | Pricing | Notes |
|---------|---------|-------|
| 42matters | Enterprise | Acquired by Similarweb, 14M+ apps |
| AppTweak | $99/mo+ | ASO focused |
| AppstoreSpy | $19/mo+ | Free tier available |
| Sensor Tower | Enterprise | Market leader |

**Recommendation:** Defer unless we identify a specific use case worth $99+/month. Consumer mobile apps are not core to our thesis (CPG, health tech, travel).

**Sources:**
- [iTunes Search API](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/index.html)
- [App Store Connect API](https://developer.apple.com/app-store-connect/api/)

---

### 3. Google Play Store (Android Apps)

**Decision: DEFER**

**Research Findings:**
- **NO official public API** for app discovery
- Google Play Developer API is only for managing your own apps
- Play Developer Reporting API is only for your own app analytics

**Third-Party Options:**
| Service | Pricing | Notes |
|---------|---------|-------|
| 42matters | Enterprise | Best coverage |
| Cloudsway | Unknown | Web search API wrapper |
| AppTweak | $99/mo+ | Cross-platform |

**Recommendation:** Same as App Store - defer unless specific use case emerges. Mobile apps outside our current thesis focus.

**Sources:**
- [Google Play Developer API](https://developer.android.com/google/play/developer-api)
- [42matters](https://42matters.com)

---

### 4. Press Releases (PR Newswire, Business Wire, GlobeNewswire)

**Decision: DEFER (with viable path)**

**Research Findings:**
- No free API tier from major wire services
- All require enterprise subscriptions for direct API access
- PR Newswire pricing: Membership required + per-release fees

**Viable Alternative Found:**

**RTPR.io (Real-Time PR Wire)**
- **Pricing:** $30/month
- **Coverage:** Business Wire, PR Newswire, GlobeNewswire
- **Features:** Real-time API access, no enterprise pricing
- **Use case:** Funding announcements, product launches, executive hires

**Other Options:**
| Service | Pricing | Notes |
|---------|---------|-------|
| Benzinga API | Unknown | Aggregates multiple wires |
| Apify PR Newswire Scraper | Pay-per-event | 100 items free |

**Recommendation:** RTPR.io at $30/month is viable if we want press release signals. Worth evaluating ROI after core collectors are stable.

**Sources:**
- [RTPR.io](https://www.rtpr.io/)
- [Benzinga Press Releases API](https://www.benzinga.com/apis/cloud-product/press-releases/)

---

### 5. meter-sdk (Web Scraping Platform)

**Decision: EVALUATE**

**Research Findings:**

**What it is:** AI-powered web scraping platform with:
- LLM-generated extraction strategies (describe in natural language)
- Antibot bypass (Cloudflare, PerimeterX, DataDome)
- Change detection (semantic diffing, not just HTML diff)
- Webhook notifications for content changes

**Pricing:**
| Tier | Price | Features |
|------|-------|----------|
| Free | $0/mo | 10 strategies, basic scraping |
| Pro | $29/mo | Unlimited strategies, antibot, webhooks |

**Key Insight:** After AI generates extraction strategy, it uses CSS selectors for subsequent scrapes (no ongoing LLM costs).

**Potential Use Cases for Discovery Engine:**

| Use Case | Fit | Value |
|----------|-----|-------|
| URL Profiler enhancement | HIGH | Better extraction from antibot-protected sites |
| Company monitoring | MEDIUM | Track portfolio/pipeline company changes |
| Job board scraping | MEDIUM | Many job boards have aggressive antibot |

**Evaluation Criteria:**
1. Test against current URL profiler accuracy on 20 sample companies
2. Measure antibot bypass success rate on LinkedIn company pages
3. Compare extraction quality vs our current approach
4. Assess change detection value for pipeline monitoring

**Recommendation:** Worth a $29/month trial. If it improves URL profiler accuracy by 10%+ on protected sites, it's a good investment.

**Sources:**
- [meter-sdk GitHub](https://github.com/reverse/meter-sdk)
- [meter.sh](https://meter.sh)

---

## Other Potential Collectors Evaluated

### Startup Databases (Crunchbase Alternatives)

We already have Crunchbase collector. Other options reviewed:

| Service | Pricing | API | Notes |
|---------|---------|-----|-------|
| OpenVC | Free | No API | Manual only, investor-focused |
| Growjo | Free | Limited | Fastest-growing startups |
| Apollo.io | $39/mo+ | Yes | Sales-focused, good company data |
| Dealroom | Enterprise | Yes | European focus |
| Tracxn | Enterprise | Yes | Good for early-stage |
| PitchBook | $12k+/yr | Yes | Most comprehensive |

**Recommendation:** Stick with Crunchbase. Apollo.io could be worth exploring for contact enrichment at $39/mo if needed.

---

## Final Recommendations

### Immediate Actions (This Sprint)
1. **ABANDON** Wellfound collector - no viable path
2. **Document** App Store/Play Store as future consideration only

### Near-Term Evaluation (Next Sprint)
1. **Trial** meter-sdk Pro ($29/mo) for URL profiler enhancement
2. **Evaluate** RTPR.io ($30/mo) for press release signals

### Future Consideration (When Needed)
1. App Store data via AppstoreSpy ($19/mo) - only if mobile app thesis emerges
2. Apollo.io ($39/mo) - for contact enrichment if outreach features added

---

## Cost Summary

| Collector | Monthly Cost | Priority |
|-----------|--------------|----------|
| meter-sdk Pro | $29 | HIGH - evaluate |
| RTPR.io | $30 | MEDIUM - if press releases valuable |
| AppstoreSpy | $19 | LOW - future consideration |
| Apollo.io | $39 | LOW - future consideration |

**Total potential monthly spend:** $59-117 depending on needs
