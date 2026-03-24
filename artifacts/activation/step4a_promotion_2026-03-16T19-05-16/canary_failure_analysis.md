# Canary Failure Analysis — Step 4A Observation Window

**Date:** 2026-03-19
**DB Snapshot:** `signals-snapshot-2026-03-19.db` (post-checkpoint, includes canary run #48)
**Canary Pass Rate:** 93.14% (163/200 passed, 12 failed, 25 skipped)
**Verdict:** pass (threshold: 80%)

---

## A1: 12 Canary True Failures

### Taxonomy Summary

| Category | Count | Signal IDs |
|----------|-------|------------|
| Gate interpretation (LLM overscore) | 4 | 97, 193, 39, 43 |
| Parser edge case (keyword gap) | 6 | 101, 139, 478, 595, 95, 594 |
| Routing mismatch (dedup miss) | 1 | 104 |
| Malformed input | 1 | 125 |
| External dependency | 0 | — |
| Unknown | 0 | — |

### Category 1: Gate Interpretation — LLM Overscores FP-Labeled Signals (4)

These are signals labeled FP by humans but scored HIGH by the LLM thesis classifier. The canary expects FP signals to have confidence ≤0.4, but the LLM gave them 0.75–0.85.

| Signal | Key | Source | Thesis Fit | Category | Why FP |
|--------|-----|--------|-----------|----------|--------|
| 97 | rss_863f463ee0fe | rss_feeds | 0.75 | consumer_cpg | Pet grooming franchise expansion, not early-stage startup |
| 193 | rss_2c10d5e1d6cc | rss_feeds | 0.85 | travel_hospitality | Alinea is an established high-end restaurant, not an investable target |
| 39 | domain:fastcompany.com | news_api | 0.80 | consumer_cpg | Savor makes butter from CO₂ — cleantech, excluded category |
| 43 | domain:interestingengineering.com | news_api | 0.75 | consumer_health_tech | Reconcept's immersive wellness system — B2B hardware |

**Root cause:** The LLM correctly identifies surface-level thesis fit (consumer product, wellness) but misses deeper exclusion criteria: franchise models, established businesses, cleantech origin, B2B hardware. These are judgment calls where keyword pre-filter would pass and LLM needs more nuanced stage/exclusion reasoning.

**Impact on Step 4B:** Low. These are quality-of-classification issues, not hot-path defects. Would benefit from prompt tuning (out of scope for this window).

### Category 2: Parser Edge Case — Keyword Matcher Gaps for TP Signals (6)

These are signals labeled TP by humans but with keyword_score=0.0 or very low. No LLM classification exists (LLM_THESIS_MODE=shadow). The canary expects TP signals to have confidence ≥0.6.

| Signal | Key | Source | Keyword Score | Company | Notes |
|--------|-----|--------|--------------|---------|-------|
| 101 | domain:foxbusiness.com | news_api | 0.211 | — | CPG beverage startup (yerba mate); news domain as key |
| 139 | domain:flightdeepresearch.com | hacker_news | 0.0 | Deep Research for Flights | AI flight search; keyword matcher missed travel terms |
| 478 | name_loc:wildbrine | rss_feeds | 0.0 | Wildbrine | Fermented foods CPG brand; keyword 0 |
| 595 | name_loc:ultrahuman | rss_feeds | 0.0 | Ultrahuman | Health tech wearable; keyword 0 |
| 95 | rss_4a2ea65300be | rss_feeds | 0.0 | — | Funding announcement; RSS hash key |
| 594 | name_loc:temple | rss_feeds | 0.163 | Temple | Generic name, low keyword match |

**Root cause:** These signals were labeled TP during manual labeling campaigns but never received LLM classification (shadow mode). The keyword matcher alone cannot recognize these as thesis-fit companies. The canary uses `thesis_fit_score ?? keyword_score` as the scoring signal, and keyword_score is insufficient for these.

**Impact on Step 4B:** Medium. Enabling LLM_THESIS_MODE=active would likely resolve most of these (LLM would score them correctly). This is the expected behavior under shadow mode — these 6 failures are a direct consequence of not having LLM classification active.

### Category 3: Routing Mismatch — Dedup Miss (1)

| Signal | Key | Source | Thesis Fit | Notes |
|--------|-----|--------|-----------|-------|
| 104 | rss_f927a29bb3c6 | rss_feeds | 0.75 | Labeled FP: "Duplicate of Dorsia signal" |

**Root cause:** Signal 104 is a duplicate of another Dorsia signal that wasn't caught by canonical key dedup (RSS hash keys are unique per article). LLM correctly scored it as travel_hospitality (0.75), but it's a duplicate and should have been suppressed.

**Impact on Step 4B:** Low. Known limitation of RSS hash keys for dedup. Would be addressed by Step 3B entity resolution (deferred).

### Category 4: Malformed Input (1)

| Signal | Key | Source | Keyword Score | Company | Notes |
|--------|-----|--------|--------------|---------|-------|
| 125 | domain:apps.apple.com | hacker_news | 0.0 | Wildex | App Store URL extracted as domain key |

**Root cause:** The canonical key is `domain:apps.apple.com` — the App Store URL was extracted as the company domain rather than the actual app developer's domain. This is a known weak-key pattern for HN signals that link to app stores.

**Impact on Step 4B:** Low. Canonical key remediation would fix this, but it's a pre-existing issue.

---

## A2: 25 Skipped Signals

All 25 signals have reason `no_thesis_score` — neither `thesis_fit_score` nor `keyword_score` exists in `thesis_classifications`.

### Breakdown

| Expected Label | Count | Representative Keys |
|---------------|-------|---------------------|
| FP | 16 | acme.com (×2), github_org:acme (×2), storozhenko98.github.io, gitlab.com, github.com, faire.com, stockx.com, poshmark.com, mercari.com, olaplex.com, offerup.com, flyr.com, hackersmacker.org, playlinex.com, better-hub.com |
| TP | 9 | tonal.com, hopper.com, glossier.com, calm.com, olipop.com, oura.com, hungryroot.com, talkiatry.com |

### Classification

**Intended skips (all 25):** These signals were added to the golden set via manual labeling but were never processed through the thesis classification pipeline. They have no row in `thesis_classifications` at all. This is expected because:
- IDs 1–4 are test/seed signals (acme.com, github_org:acme)
- IDs 430–447 are ground-truth labels added during a labeling campaign without pipeline reprocessing
- IDs 581–605 are recently labeled signals that haven't been backfill-classified

**Suspicious skips:** 0. All skips are accounted for by the "no thesis pipeline run" explanation.

**Recommendation:** When LLM_THESIS_MODE is promoted to `active`, a backfill pass should be run on these 25 signals to bring them into the canary scoring population. This would increase the scorable golden set from 175 to 200 and improve canary representativeness.

---

## A3: Label Distribution Audit

### Overall: 187 FP / 19 TP / 5 UNSURE (211 total)

**FP:TP ratio = 9.8:1** — The golden set is heavily FP-skewed. This means the canary is primarily testing "can we correctly score known FPs low?" rather than balanced TP/FP scoring.

### By Source API

| Source | FP | TP | UNSURE | FP Rate | Notes |
|--------|----|----|--------|---------|-------|
| hacker_news | 151 | 2 | 4 | 96.2% | Dominates FP volume |
| rss_feeds | 20 | 7 | 1 | 71.4% | More balanced |
| greenhouse_jobs | 6 | 5 | 0 | 54.5% | Best TP yield |
| news_api | 5 | 2 | 0 | 71.4% | Small sample |
| ashby_jobs | 1 | 3 | 0 | 25.0% | Best precision |
| github | 2 | 0 | 0 | 100% | Only FPs labeled |
| product_hunt | 2 | 0 | 0 | 100% | Only FPs labeled |

**Sampling bias detected:** hacker_news accounts for 151/187 FPs (80.7%). The high FP rate is driven almost entirely by HN signals. Job posting collectors (greenhouse, ashby) have much better precision. This is structural: HN surfaces many tangentially-related discussions that aren't investable companies.

### By Labeling Day

| Day | FP | TP | UNSURE |
|-----|----|----|--------|
| 2026-02-08 | 23 | 0 | 1 |
| 2026-02-19 | 7 | 0 | 0 |
| 2026-03-01 | 102 | 15 | 3 |
| 2026-03-02 | 55 | 4 | 1 |

Labels are concentrated in 2 labeling campaigns (Mar 1-2 = 85% of all labels). The Feb 8 batch was FP-only. No temporal drift in FP rate across campaigns.

### By Confidence Bucket

| Bucket | FP | TP | UNSURE | FP Rate |
|--------|----|----|--------|---------|
| high (≥0.7) | 69 | 9 | 1 | 87.3% |
| medium (0.4-0.7) | 117 | 10 | 4 | 89.3% |
| low (<0.4) | 1 | 0 | 0 | 100% |

FP rate is stable across confidence buckets (~87-89%). This suggests the confidence score is **not discriminating well** between TP and FP — both buckets have similar FP rates. The funnel is wide at all confidence levels.

### By Thesis Category

| Category | FP | TP | UNSURE | FP Rate |
|----------|----|----|--------|---------|
| NO_CLASSIFICATION | 547 | 78 | 17 | 85.2% |
| consumer_cpg | 14 | 25 | 5 | 31.8% |
| consumer_health_tech | 6 | 13 | 0 | 31.6% |
| consumer_marketplace | 0 | 2 | 0 | 0% |
| excluded | 51 | 0 | 1 | 98.1% |
| travel_hospitality | 16 | 8 | 0 | 66.7% |

**Key finding:** When the LLM does classify, thesis categories have much better FP rates (~32% for CPG and health tech) compared to unclassified signals (85%). The `excluded` category correctly catches 51 FPs with 0 TPs. This strongly supports promoting LLM_THESIS_MODE to active.

---

## A4: SPC Required-vs-Optional Contract Review

### Contract Comparison

| Metric | Step 3 | Step 4 | Consistent? |
|--------|--------|--------|-------------|
| collector_volume | **required** | **required** | Yes |
| overall_fp_rate | **required** | **required** | Yes |
| confidence_calibration_ece | optional | optional | Yes |
| quarantine_regret | optional | optional | Yes |
| publish_fp_rate | — | optional | Yes (Step 4 only) |

### Active vs Default SPC Settings

| Setting | Active (current) | Default |
|---------|-----------------|---------|
| SPC_MIN_BASELINE_DAYS | 7 | 14 |
| SPC_MIN_LABELED_PER_DAY | 3 | 10 |
| SPC_MIN_TOTAL_SAMPLES | 100 | 100 |

Under active overrides: `overall_fp_rate` = ok (sufficient data)
Under defaults: `overall_fp_rate` = insufficient_data (needs 14 baseline days)

### Verdict

**No defect found.** The same metrics are required in the same steps under both active and default SPC settings. The only difference is the data sufficiency threshold — active overrides lower the bar so `overall_fp_rate` has enough baseline data to compute control limits. This is the documented bootstrap exception from the Step 4A promotion decision.

The SPC override decision tool correctly identifies this as `proceed_with_exception` and documents exactly which metrics would be lost under defaults.

---

## Decision Gate

### Hot-path defects found: 0

No defects in canary scoring logic, SPC baselines, thesis/classification logic, delivery policy, confidence routing, or governance contracts.

### Findings summary

| Finding | Severity | Action |
|---------|----------|--------|
| 4 LLM overscores on FP signals | Low | Prompt tuning (out of scope) |
| 6 TP signals with keyword_score=0 | Medium | Resolve with LLM_THESIS_MODE=active |
| 1 dedup miss (RSS hash key) | Low | Step 3B entity resolution |
| 1 malformed canonical key | Low | Canonical key remediation |
| 25 skipped signals (no thesis pipeline) | Medium | Backfill when LLM active |
| HN dominates FP volume (80.7%) | Informational | Known structural characteristic |
| Confidence not discriminating TP/FP | Informational | Supports LLM promotion |
| SPC bootstrap override documented | Expected | Regret check on 2026-03-30 |

### Step 4B Assessment

Step 4B (MERGE_WRITES_ENABLED=active) remains plausible. No blocking defects. The 12 canary failures are all explainable and stable (same count across runs #45-48). The main improvement opportunity is enabling LLM_THESIS_MODE=active, which would likely resolve 6 of the 12 failures and reduce skips from 25 to near-zero.

**Proceed with safe test work (Milestones B–F).**
