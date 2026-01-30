# Collector Evaluation: Phase 5 Research

**Date:** 2026-01-29
**Purpose:** Research API availability for proposed collectors and document BUILD/DEFER/ABANDON decisions.

---

## Summary Table

| Collector | Decision | Rationale |
|-----------|----------|-----------|
| Wellfound (AngelList Talent) | **ABANDON** | No public API; third-party scrapers deprecated; ToS prohibits scraping |
| Apple App Store | **DEFER** | iTunes Search API lacks "new apps" endpoint; App Store Connect requires dev account; 3rd-party APIs expensive ($25K+/yr) |
| Google Play Store | **DEFER** | No official public API for app listings; scraping violates ToS; 3rd-party services expensive |
| Press Release APIs | **ABANDON** | Enterprise pricing ($1,500-$3,000+/release); RSS feeds already implemented in `rss_feeds.py` |

---

## Detailed Findings

### 1. Wellfound (AngelList Talent)

**Decision: ABANDON**

#### Background
AngelList Talent was rebranded to Wellfound in late 2022. The platform connects startups with job seekers and is a potential source of early-stage company signals.

#### Research Findings

1. **No Public API Available**
   - According to [GetApp](https://www.getapp.com/hr-employee-management-software/a/angellist/), Wellfound does not offer a public API.
   - The current [AngelList API](https://docs.angellist.com/docs/overview) is a GraphQL API for Transactions (investment/venture platform), not the Talent/jobs marketplace.

2. **Third-Party Scrapers Deprecated**
   - [Apify Wellfound Scraper](https://apify.com/arlusm/wellfound-scraper/api) is marked as **DEPRECATED**
   - [AngelList Jobs Scraper](https://apify.com/jason_1bps/angellist-scraper/api) is also **DEPRECATED**
   - The [angel.co npm package](https://www.npmjs.com/package/angel.co) was last updated 11 years ago

3. **Terms of Service**
   - Scraping violates Wellfound's ToS
   - No partnership/data access program available for non-enterprise users

#### Alternative Data Sources
- **LinkedIn Collector** (already implemented): Company and job posting data via Proxycurl API
- **Job Postings Collector** (already implemented): Greenhouse/Lever ATS data
- **Crunchbase Collector** (already implemented): Startup funding and company data

#### Cost-Benefit Analysis
| Factor | Assessment |
|--------|------------|
| Signal Quality | Medium (0.5-0.7) - startup job postings indicate growth |
| Implementation Cost | High - requires reverse engineering, likely to break |
| Legal Risk | High - ToS violation |
| Maintenance Burden | Very High - no stable API |
| Alternative Coverage | **Good** - existing collectors cover similar signals |

**Recommendation:** ABANDON. Existing collectors (LinkedIn, job_postings, Crunchbase) provide sufficient coverage for startup employment signals without legal risk.

---

### 2. Apple App Store

**Decision: DEFER**

#### Background
New consumer app launches are a potential signal for early-stage companies, particularly in Health Tech (fitness, wellness apps) and Consumer Marketplaces.

#### Research Findings

1. **iTunes Search API**
   - [Apple's iTunes Search API](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/index.html) allows searching for content
   - **Limitation:** No endpoint for "recently launched" or "new apps"
   - Only searches existing catalog by keyword/developer/etc.
   - Returns JSON format, free to use

2. **App Store Connect API**
   - [Official API](https://developer.apple.com/documentation/appstoreconnectapi) is comprehensive (200+ endpoints)
   - [List Apps endpoint](https://developer.apple.com/documentation/appstoreconnectapi/get-v1-apps) exists
   - **Critical Limitation:** Requires Apple Developer Program membership ($99/year)
   - **Critical Limitation:** Only accesses YOUR apps, not third-party apps
   - Cannot discover apps from other developers

3. **Third-Party Services**
   - [Sensor Tower](https://sensortower.com/) (acquired data.ai): $25,000-$40,000/year
   - AppFollow: Starting at $179/month
   - [SearchAPI](https://www.searchapi.io/apple-app-store): Scraping-based, pricing varies
   - [SerpApi](https://serpapi.com/apple-app-store): Scraping-based, metered pricing

4. **App Store Changes (2026)**
   - Apple [expanding search ad placements](https://ppc.land/apple-expands-app-store-search-ads-with-multiple-placements-arriving-in-2026/)
   - iOS 26 introducing Mobile Software Competition Act compliance (Japan)
   - No new discovery APIs announced

#### Cost-Benefit Analysis
| Factor | Assessment |
|--------|------------|
| Signal Quality | Medium-High (0.5-0.8) - app launches indicate consumer product activity |
| Implementation Cost | Very High - enterprise pricing or scraping required |
| Legal Risk | Medium - Apple may restrict scraping |
| Maintenance Burden | High - no stable free API |
| Alternative Coverage | Partial - Product Hunt captures some app launches |

**Recommendation:** DEFER. The cost of third-party services ($25K+/year) does not justify the signal value at current pipeline scale. Revisit if:
- Pipeline volume increases significantly
- A cost-effective API emerges
- Apple releases public discovery APIs

**Workaround:** Monitor TechCrunch/Product Hunt for notable app launches (already covered by `rss_feeds.py` and `product_hunt.py`).

---

### 3. Google Play Store

**Decision: DEFER**

#### Background
Similar to App Store, Google Play app launches could signal early-stage consumer companies.

#### Research Findings

1. **No Official Public API for Discovery**
   - [Google Play Developer API](https://developers.google.com/android-publisher) exists but only for YOUR apps
   - Per [Quora discussion](https://www.quora.com/Is-there-any-official-API-to-get-app-details-from-the-Play-Store): "Google Play does not offer an official API to get app details"
   - The [Android Publisher REST API](https://developers.google.com/android-publisher/api-ref/rest) manages your own app's store listing, not discovery

2. **google-play-scraper npm Package**
   - [npm package](https://www.npmjs.com/package/google-play-scraper) available
   - **ToS Risk:** Scraping likely violates Google's Terms of Service
   - **Reliability:** Throttling at 503 with captcha when too many requests
   - Default caching: 1000 values, 5-minute expiry

3. **Third-Party Services**
   - [SerpApi Google Play API](https://serpapi.com/google-play-api): Paid scraping service
   - [DataForSEO](https://dataforseo.com/apis/app-data-api/google-play-store-api): Starting at $650/month
   - Sensor Tower (see App Store section): $25,000-$40,000/year

4. **2026 Play Store Trends**
   - [ASOMobile](https://asomobile.net/en/blog/app-listings-in-google-play-2026/) notes Google using ML for listing evaluation
   - Custom store listings (up to 50 per app) for A/B testing
   - No indication of public discovery APIs

#### Cost-Benefit Analysis
| Factor | Assessment |
|--------|------------|
| Signal Quality | Medium (0.4-0.7) - app launches indicate consumer activity |
| Implementation Cost | High - scraping or expensive 3rd-party |
| Legal Risk | High - Google ToS prohibits scraping |
| Maintenance Burden | Very High - scraping breaks frequently |
| Alternative Coverage | Partial - Product Hunt and tech press coverage |

**Recommendation:** DEFER. Same reasoning as App Store - cost exceeds value at current scale.

**Workaround:** Continue using Product Hunt and RSS feeds for app launch coverage.

---

### 4. Press Release APIs

**Decision: ABANDON**

#### Background
Press releases announce funding rounds, product launches, and partnerships - potentially valuable signals for early-stage companies.

#### Research Findings

1. **PR Newswire**
   - [Pricing](https://pressonify.ai/blog/press-release-distribution-pricing-comparison-2026): $350-$805 per release + $195 membership
   - No public API for consuming releases
   - Enterprise data feeds require custom contracts

2. **Business Wire**
   - [Pricing](https://www.prezly.com/academy/business-wire-pricing): Starting at $475 for 400 words
   - Per [Capterra](https://www.capterra.com/p/173807/Business-Wire/): No public API documented
   - Owned by Berkshire Hathaway, focuses on regulatory compliance

3. **GlobeNewswire**
   - Per [GetApp](https://www.getapp.com/marketing-software/a/globenewswire/): "GlobeNewswire does not have an API available"
   - Quote-based pricing, enterprise-focused
   - Part of Notified platform

4. **RTPR (Real-Time PR Wire API)**
   - [RTPR.io](https://www.rtpr.io/): $30/month for API access
   - Aggregates Business Wire, PR Newswire, GlobeNewswire
   - **Purpose:** Reading/consuming press releases (for traders)
   - **Limitation:** Designed for market-moving news, not startup discovery

5. **Existing Coverage**
   - `rss_feeds.py` already includes:
     - PR Newswire RSS: Consumer Products feed
     - GlobeNewswire RSS: Consumer Products feed
   - Signal strength: 0.35-0.65 (appropriate for press releases)

#### Cost-Benefit Analysis
| Factor | Assessment |
|--------|------------|
| Signal Quality | Medium (0.4-0.65) - press releases often promotional |
| Implementation Cost | Very High - enterprise pricing |
| Legal Risk | Low - if using official channels |
| Maintenance Burden | Low - if API available |
| Alternative Coverage | **Excellent** - RSS feeds already capture PR content |

**Recommendation:** ABANDON. The `rss_feeds.py` collector already ingests PR Newswire and GlobeNewswire feeds for free. Building a dedicated API integration adds cost without proportional signal quality improvement.

**What We Already Have:**
```python
# From collectors/rss_feeds.py
FEED_CATEGORIES = {
    "press_release": [
        "https://www.prnewswire.com/rss/consumer-products-retail-latest-news/consumer-products-retail-latest-news-list.rss",
        "https://www.globenewswire.com/RssFeed/subjectcode/12-Consumer%20Products/feedTitle/GlobeNewswire%20-%20Consumer%20Products",
    ],
    ...
}
```

---

## Recommendations Summary

### Immediate Actions
1. **No new collectors to build** - all proposed collectors either abandoned or deferred
2. **Maximize existing coverage** - ensure `rss_feeds.py` categories are optimized
3. **Monitor ecosystem** - revisit App Store/Play Store if affordable APIs emerge

### Future Considerations

| Trigger | Action |
|---------|--------|
| Sensor Tower launches affordable tier | Evaluate App Store + Play Store collectors |
| Apple releases public discovery API | Build App Store collector |
| Google releases public discovery API | Build Play Store collector |
| Pipeline scales to 1000+ signals/day | Justify $25K+/year data costs |

### Alternative Approaches Already Implemented

| Signal Type | Current Collector | Coverage |
|-------------|-------------------|----------|
| Startup jobs | `linkedin.py`, `job_postings.py` | Good |
| App launches | `product_hunt.py`, `rss_feeds.py` | Partial |
| Press releases | `rss_feeds.py` (RSS feeds) | Good |
| Funding rounds | `crunchbase.py`, `sec_edgar.py` | Excellent |
| Tech press | `rss_feeds.py`, `news_api.py` | Excellent |

---

## Research Sources

### Wellfound/AngelList
- [API Tracker - Wellfound](https://apitracker.io/a/wellfound)
- [Apify - Wellfound Scraper (DEPRECATED)](https://apify.com/arlusm/wellfound-scraper/api)
- [AngelList API Documentation](https://docs.angellist.com/docs/overview)

### Apple App Store
- [iTunes Search API](https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/index.html)
- [App Store Connect API](https://developer.apple.com/documentation/appstoreconnectapi)
- [Sensor Tower](https://sensortower.com/)

### Google Play Store
- [Google Play Developer API](https://developers.google.com/android-publisher)
- [google-play-scraper npm](https://www.npmjs.com/package/google-play-scraper)
- [DataForSEO](https://dataforseo.com/apis/app-data-api/google-play-store-api)

### Press Releases
- [RTPR.io](https://www.rtpr.io/)
- [PR Newswire Pricing](https://pressonify.ai/blog/press-release-distribution-pricing-comparison-2026)
- [Business Wire Pricing](https://www.prezly.com/academy/business-wire-pricing)

---

*Document created as part of Phase 5: Collector Evaluation sprint*
