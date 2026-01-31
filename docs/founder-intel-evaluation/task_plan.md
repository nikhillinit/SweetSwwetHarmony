# Task Plan: Founder Intel Integration

## Goal
Integrate founder_intel_canonical spec into Discovery Engine following the SHADOW-first approach.

## Current Phase
**Phase B: Quick Wins + SHADOW** (complete)

## Phases

### Phase 1: Document Discovery & Inventory
- [x] Read and catalog all spec files in founder_intel_canonical
- [x] Document file structure and relationships
- [x] Note key architectural concepts
- **Status:** complete

### Phase 2: Architecture Deep-Dive
- [x] Analyze spine v0.3 architecture (ACTIVE/SHADOW/OFF states)
- [x] Understand 4 candidate modes and scoring weights
- [x] Review evidence packet structure
- **Status:** complete

### Phase 3: Configuration Analysis
- [x] Review default.yaml (384 lines)
- [x] Analyze consumer_keywords.yaml alignment with thesis
- [x] Evaluate starter_kits/signatures.json (10 boilerplate templates)
- [x] Check smart_money/github_handles.yaml structure
- **Status:** complete

### Phase 4: Integration Assessment
- [x] Map founder intel components to Discovery Engine collectors
- [x] Identify overlapping functionality
- [x] Document integration points
- [x] Note gaps and conflicts
- **Status:** complete

### Phase 5: Recommendations & Synthesis
- [x] Summarize BUILD/ADAPT/DEFER/ABANDON decisions
- [x] Document concerns and risks
- [x] Propose integration roadmap
- **Status:** complete

## Key Questions
1. Does founder intel duplicate existing Discovery Engine functionality?
2. What new capabilities does it add vs what we already have?
3. How do the scoring weights align with our thesis matcher?
4. Are the starter kit signatures useful for boilerplate detection?
5. What's the effort to integrate vs build from scratch?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
|          |           |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|

## Notes
- Previous session fixed SEC EDGAR (9 signals) and GitHub consumer topics
- Memory-keeper checkpoint: founder-intel-review-paused
- Phase A (SHADOW Infrastructure) completed: 72 tests passing

---

## Phase A: SHADOW Logging Infrastructure ✅ COMPLETE

| Task | Description | Status |
|------|-------------|--------|
| A.1 | Create `utils/feature_states.py` with ACTIVE/SHADOW/OFF enum | complete |
| A.2 | Create `shadow_log` table (migration 23) | complete |
| A.3 | Add `log_shadow_computation()` method to SignalStore | complete |
| A.4 | Add `get_shadow_logs()` query method | complete |
| A.5 | Add `get_shadow_correlation_report()` metrics method | complete |
| A.6 | Wire FeatureRegistry into DiscoveryPipeline | complete |
| A.7 | Add shadow_logs_written to PipelineStats | complete |

**Tests:** 72 new tests (35 feature_states + 20 shadow_logging + 9 shadow_metrics + 8 pipeline_shadow)
**Exit Criteria:** All met

---

## Phase B: Quick Wins + SHADOW Logging ✅ COMPLETE

**Goal:** Merge consumer_keywords.yaml enhancements into thesis_matcher.py and wire SHADOW logging.

| Task | Description | Status |
|------|-------------|--------|
| B.1 | Add intent_phrases to thesis_matcher | complete |
| B.2 | Add regex patterns for domain matching (get*, try*, join*) | complete |
| B.3 | Add domain blacklist fragments | complete |
| B.4 | Add new negative keywords from spec | complete |
| B.5 | Create SHADOW logging for thesis matches | complete |
| B.6 | Write tests for all new functionality | complete |

**Tests:** 54 new tests (33 thesis_matcher + 21 thesis_filter Phase B)
**Total thesis tests:** 110 passing

**Source:** `consumer_keywords.yaml` from founder_intel_canonical bundle

**Intent Phrases to Add:**
- join waitlist, request access, private beta, coming soon
- book a demo, sign up, get started, pricing, subscribe

**New Negative Keywords:**
- boilerplate, starter, template, tutorial, workshop, course
- sdk, cli, library, framework, plugin, linter

**Regex Patterns:**
- `^get[a-z0-9-]{3,}$` - Domain starts with "get" (getmyapp.com)
- `^try[a-z0-9-]{3,}$` - Domain starts with "try" (tryproduct.io)
- `^join[a-z0-9-]{3,}$` - Domain starts with "join" (joincommunity.co)

**Domain Blacklist Fragments:**
- localhost, example, sample, test, staging, dev., internal

**Exit Criteria:**
- [x] Intent phrases boost thesis score when matched
- [x] Regex patterns match consumer-oriented domains
- [x] Blacklist fragments reject non-production domains
- [x] New negative keywords reduce false positives
- [x] SHADOW logging records all thesis match details
- [x] Tests cover all new functionality

**Phase B Summary (2026-01-31):**
- Extended `ThesisFit` dataclass with `intent_phrases_matched`, `domain_match`, `domain_blacklisted`
- Added `INTENT_PHRASES` dict (9 phrases with weights)
- Added `CONSUMER_DOMAIN_PATTERNS` list (3 regex patterns)
- Added `DOMAIN_BLACKLIST_FRAGMENTS` list (7 fragments)
- Added 15 new negative keywords from consumer_keywords.yaml
- Updated `ThesisMatcher.score()` to accept `domain_name` parameter
- Updated `ThesisFilter.classify()` to pass domain_name through
- Added SHADOW logging in pipeline for "thesis_match" feature
- Added "thesis_match" feature to DEFAULT_FEATURES (state=SHADOW)

---

## Phase C: Boilerplate Defense in SHADOW ✅ COMPLETE

| Task | Description | Status |
|------|-------------|--------|
| C.1 | Create `utils/boilerplate_detector.py` | complete |
| C.2 | Port signature_builder_rules.yaml tokenization | complete |
| C.3 | Port signatures.json (10 templates) | complete |
| C.4 | Implement containment similarity matching | complete |
| C.5 | Log matches to shadow_log (don't suppress) | complete |
| C.6 | Write tests | complete |

**Tests:** 71 new tests (57 unit + 14 integration)

**Summary:**
- Created `utils/boilerplate_detector.py` with BoilerplateSignature, BoilerplateMatch dataclasses
- Implemented containment similarity (threshold 0.80) for detecting starter kit matches
- 10 default signatures: Next.js basic, Next.js+Tailwind+Prisma+Auth, T3-stack, Supabase+Next.js, Expo, RN router, Stripe, Firebase, Django cookiecutter, Rails
- Token extraction from package.json, requirements.txt, Gemfile, config files
- SHADOW logging integrated in pipeline (before thesis filtering, so logs even for rejected/held signals)
- Tests verify detection, threshold behavior, pipeline integration, SHADOW logging

---

## Phase D: Team Shape Metrics in SHADOW (pending)

| Task | Description | Status |
|------|-------------|--------|
| D.1 | Extend github_activity.py with contributor analysis | pending |
| D.2 | Add concentration score | pending |
| D.3 | Add sustained activity detection | pending |
| D.4 | Log to shadow_log | pending |
| D.5 | Write tests | pending |

---

## Phase E: Founder Surfaces in SHADOW (pending)

| Task | Description | Status |
|------|-------------|--------|
| E.1 | Add profile README scanning | pending |
| E.2 | Add gist scanning | pending |
| E.3 | Extract founder intent markers | pending |
| E.4 | Log to shadow_log | pending |
| E.5 | Write tests | pending |
