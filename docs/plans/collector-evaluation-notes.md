# Collector Evaluation Research Notes

## Overview

This document tracks tools, APIs, and services to evaluate for Phase 5 (Collector Evaluation).

---

## meter-sdk (https://github.com/reverse/meter-sdk)

**Status:** DEFERRED - evaluate during collector research phase

**What it is:** Python SDK for [meter.sh](https://meter.sh), an AI-powered web scraping platform.

**Key Features:**
- LLM-powered extraction - describe data needs in natural language, no CSS selectors
- Antibot bypass - handles Cloudflare, PerimeterX, DataDome automatically
- Change detection - semantic diffing to detect meaningful content changes
- Webhook notifications - POST updates when content changes
- Batch operations and scheduling

**Potential Use Cases:**
| Use Case | Fit | Notes |
|----------|-----|-------|
| URL Profiler | Medium | Could improve extraction reliability for company websites with antibot protection |
| Company monitoring | High | Track website changes for companies in pipeline (funding announcements, pivots) |
| Job board scraping | High | Many job boards have aggressive antibot |
| News monitoring | Medium | Could supplement existing collectors for press release detection |

**Cost:** Free tier (10 strategies) or $29/month Pro (unlimited, antibot bypass, webhooks)

**Decision Criteria:**
- [ ] Evaluate against current URL profiler accuracy
- [ ] Test antibot bypass on problematic sites (LinkedIn pages, Crunchbase)
- [ ] Compare cost vs Proxycurl for similar functionality
- [ ] Assess change detection value for portfolio monitoring

**Added:** 2026-01-25

---

## Wellfound (AngelList Talent)

**Status:** LIKELY ABANDON

**Issue:** API deprecated in 2023, scraping violates ToS

---

## App Store / Play Store

**Status:** LIKELY DEFER

**Issues:**
- iTunes Search API limited - no new app listings endpoint
- Play Store has NO official API
- 3rd-party services expensive

---

## Press Releases

**Status:** LIKELY ABANDON

**Issue:** Enterprise subscription required for most services (PR Newswire, Business Wire, GlobeNewswire)

---

## Future Candidates

*(Add new tools/APIs to evaluate here)*
