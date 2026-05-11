# Thesis Filter Survivor Analysis Results - 2026-05-11

## Execution

- Plan: `.omx/plans/thesis-filter-survivor-p1-20260510.md`
- Script: `scripts/thesis_filter_survivor_analysis.py`
- Artifact: `artifacts/thesis_filter_survivor_analysis_614.json`
- Test: `tests/test_thesis_filter_survivor_analysis.py`

## Verified Runtime Facts

- Live `signals.db`: 614 `signals` rows
- Distinct `canonical_key`: 588
- Pending rows from `signals` joined to `signal_processing`: 34
- Pending canonical companies: 34
- `PRAGMA user_version`: 0
- `MAX(schema_migrations.version)`: 53
- Canonical unit: consolidated pending company
- Canonical entrypoint: `ThesisFilter.classify(skip_llm=True)`
- Canonical `domain_name`: omitted for production parity
- LLM calls made: 0
- Matcher runtime: `v2_enablement=disabled`, `ml_enablement=disabled`

## Canonical Results

- Canonical companies analyzed: 34
- Qualified: 21
- Held: 13
- Rejected: 0
- LLM-eligible by keyword score threshold: 26
- Held and LLM-eligible: 5
- Held and not LLM-eligible: 8
- Decision paths: `qualify_sector=21`, `hold_default=13`
- Negative keyword clusters: none

## Phase 3 Patch Selection

No behavior patch is selected from this artifact alone.

Rationale:

- The canonical pending lane produced no thesis-filter rejections.
- The only repeated non-qualified motif is `hold_default`, not a hard reject or negative-keyword failure.
- The held set mixes missing descriptions, unknown company names, low keyword scores, and low-information RSS/news rows.
- This looks more like corpus/content-quality or pending-backlog diagnosis than a proven filter over-rejection bug.

## Held-Set Manual Review

The 13 held companies split into these mechanisms:

- 2 hiring-signal rows have no description text: `domain:openai.com`, `domain:10beauty.com`.
- 6 RSS/news rows have unknown or weak company identity.
- 3 rows are the same Wonderbelly article under bad or duplicate keys: `domain:inc.com`, `news_9f117929355f`, `name_loc:lessons-that-helped`.
- 2 rows are the same SOLLOS/Yerba Mate article under duplicate keys: `news_65a775980f1f`, `name_loc:yerba-mate-inc`.
- Labeled rows do not support a global thesis-threshold patch:
  - `signal_id=41` / `domain:inc.com` is labeled `TP` with note `CPG wellness brand (Wonderbelly)`.
  - `signal_id=43` / `domain:interestingengineering.com` is labeled `FP` in `signal_quality_metrics`.
  - `signal_id=97` / `rss_863f463ee0fe` is labeled `FP` with note `Pet grooming franchise expansion, not early-stage startup`.

Conclusion: the strongest repeated failure mode is upstream identity extraction / canonical-key assignment for news and RSS rows, not thesis-filter routing. Lowering `hold_threshold` or `skip_llm_if_keyword_below` would risk rescuing known FPs along with the Wonderbelly TP.

## Deferred Hypotheses

- LLM skip threshold may matter for the 8 held companies below `skip_llm_if_keyword_below=0.2`, but the artifact does not prove these are false holds.
- Low or missing descriptions may be suppressing consumer keyword evidence for some companies.
- RSS/news extraction quality may be creating low-signal pending companies with generic names or article-like canonical keys.
- Any threshold or rescue change should wait for labeled examples or a stronger repeated mechanism.

## Recommended Next Gate

Do not enter Phase 4 thesis-filter behavior-patch work yet. Open a bounded identity/extraction cleanup slice for news/RSS rows:

- reproduce Wonderbelly extraction failures from `signal_id` 41, 178, and 210;
- reproduce Yerba Mate duplicate-key behavior from `signal_id` 175 and 474;
- add fixture tests for the company-name / canonical-key extraction path;
- patch extraction only if the fix does not promote publisher domains like `inc.com` or generic title fragments like `Lessons That Helped`.

## Identity Cleanup Slice

Implemented after this gate:

- Added default-mode extractor regressions for Wonderbelly and SOLLOS/Yerba Mate.
- Added `news_api` and `rss_feeds` collector conversion regressions proving:
  - `inc.com` and `foxbusiness.com` are not promoted as company domains;
  - generic title fragments like `Lessons That Helped` are not used as company names;
  - fallback keys become `name_loc:wonderbelly` and `name_loc:sollos-yerba-mate`.
- Added narrow subject-context regex fallbacks in `utils/company_name_extractor.py` for:
  - `behind <Company> ...`
  - `partner in <Company>, a/an/the ...`
- Kept legal-suffix stripping scoped to the new subject-context fallback only. A broader strip regressed RSS DNS promotion by changing `Acme Inc` to `Acme`, so it was narrowed before final verification.

Verification:

- `pytest tests/utils/test_company_name_extractor.py tests/collectors/test_news_api.py tests/collectors/test_rss_feeds.py -q` -> 342 passed
- `pytest tests/collectors/test_news_api_publisher_leakage.py tests/utils/test_company_extraction_integration.py tests/collectors/test_rss_promotion_integration.py -q` -> 44 passed
