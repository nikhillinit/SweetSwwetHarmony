# Findings: Founder Intel Bundle Evaluation

## Metadata
| Field | Value |
|-------|-------|
| **Owner** | @nikhi |
| **Last Updated** | 2026-01-31 |
| **Bundle Version** | founder_intel_canonical (generated 2026-01-31T10:59:26Z) |
| **DE Baseline** | main branch, commit dcf2014 |
| **Status** | Complete |
| **Decision** | **GO** - Build SHADOW infrastructure, deploy features for validation |

## Requirements
- Evaluate founder_intel_canonical spec bundle
- Assess fit with Discovery Engine architecture
- Identify integration points
- Document concerns and gaps
- Provide BUILD/ADAPT/DEFER/ABANDON recommendations

## Spec Bundle Inventory

### Core Specs
| File | Purpose | Lines |
|------|---------|-------|
| README_CANONICAL.md | Bundle overview | 15 |
| docs/specs/founder_intel_spine_v0_3.md | Main architecture (ACTIVE/SHADOW/OFF, 4 modes, scoring) | 209 |

### Configuration Files
| File | Purpose | Lines |
|------|---------|-------|
| config/default.yaml | Comprehensive config (sources, scoring, evidence) | 384 |
| config/keyword_sets/consumer_keywords.yaml | Consumer thesis keywords | 113 |
| config/smart_money/github_handles.yaml | Smart money watchlist (empty structure) | 21 |
| config/starter_kits/signatures.json | Boilerplate templates (10 templates) | 263 |
| config/starter_kits/signatures_candidate.json | Candidate signatures (empty) | - |
| config/starter_kits/signature_builder_rules.yaml | Tokenization rules for fingerprinting | 177 |

### Schemas
| File | Purpose |
|------|---------|
| docs/schemas/boilerplate_candidate_match_summary.schema.json | JSON schema for boilerplate matching |
| docs/schemas/boilerplate_candidate_match_summary_overlap_behavior.md | Deterministic overlap algorithm |

### Script Specs
| File | Purpose |
|------|---------|
| scripts/smart_money/add_handles.py.spec.md | Add verified GitHub handles to watchlist |
| scripts/starter_kits/promote_candidate.py.spec.md | Promote boilerplate templates to active |
| scripts/starter_kits/suggest_signatures.py.spec.md | Discover new boilerplate patterns from FPs |

---

## Architecture Findings

### Spine v0.3 Key Concepts

**1. Feature States (ACTIVE/SHADOW/OFF)**
- **ACTIVE**: Affects ranking/alerts
- **SHADOW**: Computed + logged, 0 weight (learning mode)
- **OFF**: Not computed (requires explicit enable + owner)

**2. Feature Classes (Kill-Switch Hierarchy)**
- **critical**: Legal/compliance risk (default OFF) - e.g., third-party person enrichment
- **ephemeral**: High drift/brittle parsing (default SHADOW) - e.g., issue linguistics
- **seasonal**: Thesis-dependent (default SHADOW/OFF) - e.g., registries, app stores
- **candidate**: Optional differentiators (default SHADOW) - e.g., taste graph

**3. Kill Switches (Required)**
```
KILL_OFF_PLATFORM_CRAWL
KILL_STARGAZER_EXPANSION
KILL_COMMUNITY_SOURCES
KILL_APP_STORE_SOURCES
KILL_REGISTRY_SOURCES
KILL_IDENTITY_ENRICHMENT
```

**4. Four Candidate Generation Modes**
| Mode | State | Purpose |
|------|-------|---------|
| Commercial Intent | ACTIVE | Homepage, README markers, commercial deps, legal maturity |
| Team Formation | ACTIVE | 2-5 core contributors, sustained activity |
| OSS Momentum | SHADOW | stars ≥50, pushed ≤90d |
| Founder Surfaces | SHADOW | Profile README + gists scanning |

**5. Scoring Weights (v0.2)**
| Feature | Weight |
|---------|--------|
| Commercial Intent | 30% |
| Team Shape | 30% |
| Workflow Maturity | 25% |
| Legal Artifacts | 10% |
| Boilerplate Defense | 5% (penalty) |
| All SHADOW features | 0% |

**6. Evidence Packet Format**
```
WHAT: README snippet + stack signature + dependency evidence
WHO: Contributor summary + profile README snippet
STAGE: Legal artifacts + activity histogram
HYPE: Homepage snapshot + community mentions
RISKS: Boilerplate match summary + missingness
NEXT: Outreach angle + 3 diligence questions (LLM-generated)
```

**7. Learning Loop**
- Promotion SHADOW→ACTIVE requires: lift in precision@K, 95% compute success 3 weeks, maintenance ≤2 hrs/mo
- Max 2 promotions/demotions per month

---

## Consumer Keywords Analysis

**Alignment with Discovery Engine Thesis: STRONG**

| Category | Keywords | Thesis Match |
|----------|----------|--------------|
| Health Tech | wellness, fitness, health, nutrition, sleep, recovery, mindfulness, therapy | ✅ Perfect |
| CPG/F&B | matcha, coffee, snack, protein, supplement, meal, grocery, delivery | ✅ Perfect |
| Travel | travel, trip, itinerary, booking, hotel, flight, luggage | ✅ Perfect |
| Marketplace | shop, checkout, marketplace, resale, subscription, membership | ✅ Perfect |

**Intent Phrases**: join waitlist, request access, private beta, pricing, subscribe

**Negative Keywords (Exclusions)**:
- boilerplate, starter, template, tutorial, course
- sdk, cli, library, framework, plugin, linter

**Regex Patterns**: `^get[a-z0-9-]{3,}$`, `^try[a-z0-9-]{3,}$`, `^join[a-z0-9-]{3,}$`

---

## Boilerplate Signatures (10 Templates)

| ID | Name | Key Deps |
|----|------|----------|
| nextjs_basic_template | Next.js basic | next, react, react-dom |
| nextjs_tailwind_prisma_auth | Next.js + Tailwind + Prisma + Auth | tailwindcss, prisma, next-auth |
| t3_stack_like | T3-stack-like | zod, @trpc/*, prisma, next-auth |
| supabase_nextjs_starter | Supabase + Next.js | @supabase/supabase-js |
| expo_react_native_template | Expo React Native | expo, react-native |
| react_native_router_template | RN app-router | expo-router |
| stripe_checkout_starter | Stripe checkout | stripe |
| firebase_web_app_starter | Firebase web app | firebase |
| django_cookiecutter_like | Django cookiecutter | django |
| rails_starter_like | Rails starter | rails |

**Fingerprinting Method**: Jaccard similarity on tokenized deps + paths + configs
**Threshold**: Suppress if similarity ≥ 0.80

---

## Integration Points

### Overlap with Existing Discovery Engine Components

| Founder Intel Component | Discovery Engine Equivalent | Status |
|-------------------------|----------------------------|--------|
| GitHub API wrapper | `collectors/github.py`, `github_activity.py` | ✅ Have basic |
| HN polling | `collectors/hacker_news.py` | ✅ Have |
| Reddit polling | Not implemented | ❌ Gap |
| App store RSS | Evaluated & DEFERRED in Phase 5 | ⏸️ Deferred |
| Domain/WHOIS | `collectors/domain_whois.py` | ✅ Have |
| Homepage crawl | Not implemented | ❌ Gap |
| Consumer keywords | `utils/thesis_matcher.py`, `consumer_keywords.yaml` | ✅ Have |
| Boilerplate defense | Not implemented | ❌ Gap |
| Smart money watchlist | Not implemented | ❌ Gap |
| Scoring with weights | `utils/exit_predictor.py` (different weights) | ⚠️ Different |
| Evidence packets | ProspectPayload (simpler) | ⚠️ Simpler |
| Feature flags (ACTIVE/SHADOW/OFF) | Feature-flagged components | ⚠️ Ad-hoc |

### New Capabilities (Not in Discovery Engine)

1. **Boilerplate Defense** - Token-based fingerprinting to filter noise
2. **Smart Money Sensor** - GitHub handle watchlist for signal boost
3. **Founder Surface Extraction** - Profile README + gists scanning
4. **Team Shape Metrics** - Core contributor analysis, concentration scores
5. **Workflow Maturity Scoring** - CI/tests/releases/security configs
6. **Certificate Transparency Monitoring** - Domain discovery via CT logs
7. **Stargazer Taste Graph** - Quality assessment via stargazer analysis
8. **ACTIVE/SHADOW/OFF Feature States** - Systematic experimentation framework

### Gaps/Conflicts

| Issue | Severity | Notes |
|-------|----------|-------|
| Scoring weights differ from exit_predictor | Medium | Spine uses Commercial 30%, Team 30%, Workflow 25%; Exit predictor uses different weights |
| No team shape analysis in DE | Medium | We track signals, not contributor patterns |
| Boilerplate defense missing | High | Major noise source not filtered |
| Smart money not implemented | Low | Nice-to-have, not critical |
| Evidence packet format richer | Low | Our ProspectPayload is simpler |
| No SHADOW mode in DE | Medium | We use feature flags but no systematic learning loop |

---

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Keywords align well | consumer_keywords.yaml matches our thesis perfectly |
| Boilerplate defense valuable | High-noise filtering would improve signal quality |
| Smart money low priority | Empty watchlist, manual curation needed |
| Team shape useful | Could improve GitHub collector enrichment |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Bundle location unknown | User provided: C:\Users\nikhi\Downloads\founder_intel_canonical |
| MCP filesystem access denied | Used Bash + Read tools instead |

---

## Key Questions — Answers

**1. Does founder intel duplicate existing Discovery Engine functionality?**
> **Partial overlap.** GitHub API wrapper, HN polling, consumer keywords, and domain/WHOIS all exist in DE. But the spec's approach (SHADOW-first, structured scoring, boilerplate defense) is more systematic than our ad-hoc collectors.

**2. What new capabilities does it add vs what we already have?**
> **Six net-new capabilities:** (1) Boilerplate fingerprinting, (2) Team shape metrics, (3) Founder surface extraction, (4) Smart money watchlist, (5) Workflow maturity scoring, (6) ACTIVE/SHADOW/OFF feature state model. Most valuable: boilerplate defense (noise reduction) and the experimentation framework.

**3. How do the scoring weights align with our thesis matcher?**
> **Different purpose, compatible.** Spine weights (Commercial 30%, Team 30%, Workflow 25%, Legal 10%, Boilerplate 5%) are for ranking GitHub candidates. Our `exit_predictor.py` weights (Founder 25%, Investor 20%, Thesis 20%, Traction 15%, etc.) are for exit probability. They can coexist - spine for GitHub-specific scoring, exit predictor for cross-source scoring.

**4. Are the starter kit signatures useful for boilerplate detection?**
> **Yes, high value.** 10 templates cover the most common false positives (Next.js, T3, Supabase, Expo, Django, Rails). Jaccard similarity at 0.80 threshold is well-calibrated. Should deploy in SHADOW first to measure FP reduction before suppressing.

**5. What's the effort to integrate vs build from scratch?**
> **Integrate is faster.** Porting boilerplate defense: 3-5 days (signatures.json + tokenization rules provided). Building SHADOW infrastructure: 2-3 days. Total: ~2 weeks for core value. Building from scratch without the spec would take 4-6 weeks and require rediscovering the same design patterns.

---

## Deviations / Changes Made During Evaluation

Changes made to Discovery Engine to enable evaluation (from previous session):

| Change | Why Needed | Where | Follow-up Action |
|--------|------------|-------|------------------|
| SEC EDGAR XML parsing fix | Collector was returning 0 signals due to malformed XML handling | `collectors/sec_edgar.py` | Keep - production fix |
| SEC EDGAR industry mapping | Industry codes weren't mapping to thesis categories | `collectors/sec_edgar.py` | Keep - production fix |
| GitHub collector consumer topics | Was filtering for tech/AI topics, missing consumer startups | `collectors/github.py` | Keep - thesis alignment |

**Note:** These were prerequisite fixes to make evaluation meaningful. All changes should be kept as they improve production signal quality.

---

## Recommendations (BUILD/ADAPT/DEFER/ABANDON)

### Decision Matrix (Traceable)

| Component | Decision | Evidence | Impact on DE | Top Risk | Effort | Confidence |
|-----------|----------|----------|--------------|----------|--------|------------|
| **SHADOW Infrastructure** | BUILD | spine_v0_3.md §1,7 | New `shadow_log` table, feature_states enum | Adds complexity without immediate value | S (2-3d) | High |
| **Boilerplate Defense** | BUILD | signatures.json (10 templates), signature_builder_rules.yaml | New `utils/boilerplate_detector.py`, GitHub collector filter | FP templates may not match our signal sources | M (3-5d) | High |
| **Team Shape Metrics** | BUILD | spine_v0_3.md §3 Mode 2 | Extend github_activity.py with contributor analysis | Requires additional API calls | S (2-3d) | Medium |
| **Founder Surfaces** | BUILD | spine_v0_3.md §3 Mode 4 | New profile README + gist scanner | Unknown signal value until measured | M (3-4d) | Low |
| **Consumer Keywords** | ADAPT | consumer_keywords.yaml | Merge into thesis_matcher.py | Minor - additive change | XS (1d) | High |
| **Scoring Weights** | ADAPT | default.yaml scoring section | Run parallel to exit_predictor in SHADOW | Weight conflicts if both active | S (2d) | Medium |
| **Evidence Packets** | ADAPT | spine_v0_3.md §6 | Extend ProspectPayload dataclass | Schema changes affect downstream | S (1-2d) | Medium |
| **Feature Flags** | ADAPT | spine_v0_3.md §1 | Replace ad-hoc env vars with enum | Migration of existing flags | S (1-2d) | High |
| **Smart Money Watchlist** | DEFER | github_handles.yaml (empty) | Would need manual curation | Maintenance burden, unclear lift | M (3d) | Low |
| **Certificate Transparency** | DEFER | default.yaml off_platform section | New collector | Niche use case | M (3-5d) | Low |
| **Reddit Polling** | DEFER | default.yaml community section | New collector | Lower quality than HN | S (2d) | Medium |
| **Stargazer Expansion** | ABANDON | spine_v0_3.md §1 KILL_STARGAZER_EXPANSION | N/A | High API cost, spec flags as risky | L (5d+) | N/A |
| **Person Enrichment** | ABANDON | spine_v0_3.md §1 OFF by default | N/A | Privacy/compliance risk | L (5d+) | N/A |
| **App Store Polling** | ABANDON | Already evaluated in DE Phase 5 | N/A | No viable API | L (5d+) | N/A |
| **UI Scraping** | ABANDON | spine_v0_3.md §0 non-goals | N/A | Brittle, breaks frequently | - | N/A |

**Effort key:** XS (<1d), S (1-3d), M (3-5d), L (5d+)

### BUILD Details

| Component | Effort | Value | Rationale |
|-----------|--------|-------|-----------|
| **SHADOW Logging Infrastructure** | 2-3 days | Critical | Enables safe experimentation for ALL features. Must build first. |
| **Boilerplate Defense** | 3-5 days | High | Major noise filter. Deploy in SHADOW, measure, then promote. |
| **Team Shape Metrics** | 2-3 days | Medium | 2-5 contributor detection. Deploy in SHADOW to test value. |
| **Founder Surfaces** | 3-4 days | Unknown | Profile README + gists. SHADOW mode will reveal if valuable. |

### ADAPT Details

| Component | Existing | Action | Rationale |
|-----------|----------|--------|-----------|
| **Consumer Keywords** | `utils/thesis_matcher.py` | Merge | consumer_keywords.yaml has good overlap but different structure. Import intent phrases, regex patterns. |
| **Scoring System** | `utils/exit_predictor.py` | Consider | Spine weights (Commercial 30%, Team 30%, Workflow 25%) differ from exit predictor. May want dual scoring or alignment study. |
| **Evidence Packets** | `ProspectPayload` | Extend | Spine's WHAT/WHO/STAGE/HYPE/RISKS/NEXT format is richer. Consider adding fields to our payload. |
| **Feature Flags** | Ad-hoc env vars | Systematize | ACTIVE/SHADOW/OFF model is cleaner than our current approach. Could improve experimentation. |

### SHADOW (Test Value First)

The spec's key insight: **"Build wide, activate narrow."** Deploy features in SHADOW mode to measure lift before committing.

| Component | Deploy as SHADOW | Measure | Promote if... |
|-----------|------------------|---------|---------------|
| **Boilerplate Defense** | Log matches, don't suppress yet | Correlation with "pass" outcomes | Suppressed repos rarely convert |
| **Team Shape Metrics** | Log contributor counts | 2-5 contributor conversion rate | Strong signal for quality |
| **Founder Surface Extraction** | Log profile README signals | Profile signals vs outcomes | Predicts "worth outreach" |
| **Smart Money Watchlist** | Log watchlist hits | Hit rate vs conversion | Watchlist hits = quality |
| **Taste Graph** | Log stargazer quality | Quality score vs outcomes | Worth API cost |

**Promotion criteria (from spec):**
- Lift in precision@K or "worth outreach" rate
- 95% compute success for 3 weeks
- Maintenance ≤ 2 hrs/mo
- Max 2 promotions/month

### DEFER (Genuinely Lower Priority)

| Component | Reason | When to Revisit |
|-----------|--------|-----------------|
| **Certificate Transparency** | Domain discovery via CT logs. Niche use case. | If domain signals prove valuable |
| **Reddit Polling** | Lower signal quality than HN. | If HN volume insufficient |

### ABANDON (Not Suitable)

| Component | Reason |
|-----------|--------|
| **Stargazer Expansion (Kill Switch)** | High API cost, unclear value, spec flags it as risky |
| **Third-party Person Enrichment** | Privacy/compliance risk, spec has it OFF by default |
| **App Store Polling** | Already evaluated and DEFERRED in Phase 5 (no viable API) |
| **UI Scraping** | Brittle, spec explicitly excludes |

---

## Recommended Integration Roadmap

### Phase A: Feature State Infrastructure (2-3 days)
**Build the SHADOW logging system first** - this enables safe experimentation for all subsequent features.

1. Create `utils/feature_states.py` with ACTIVE/SHADOW/OFF enum
2. Create `shadow_log` table (feature_name, canonical_key, computed_value, timestamp)
3. Add weekly metrics query: correlation of shadow features vs outcomes
4. Wire into pipeline orchestrator

### Phase B: Quick Wins + SHADOW (1-2 days)
1. **Merge consumer_keywords.yaml** into thesis_matcher.py
   - Add intent_phrases: "join waitlist", "private beta", "pricing"
   - Add regex patterns: `^(get|try|join)[a-z0-9-]+$`
   - Add domain blacklist fragments
2. **Log keyword matches to shadow_log** for measurement

### Phase C: Boilerplate Defense in SHADOW (3-5 days)
1. Create `utils/boilerplate_detector.py`
2. Port signature_builder_rules.yaml tokenization
3. Port signatures.json (10 templates)
4. Implement Jaccard similarity matching
5. **Log matches to shadow_log** (don't suppress yet)
6. After 2-3 weeks: analyze correlation with "pass" outcomes
7. Promote to ACTIVE (suppress) if validated

### Phase D: Team Shape Metrics in SHADOW (2-3 days)
1. Extend `collectors/github_activity.py`
2. Add contributor count, concentration score, sustained activity
3. **Log to shadow_log**
4. Analyze: do 2-5 contributor repos convert better?
5. Promote to ACTIVE scoring if validated

### Phase E: Founder Surfaces in SHADOW (3-4 days)
1. Add profile README + gist scanning
2. Extract founder intent markers
3. **Log to shadow_log**
4. Measure lift before promoting

---

## Risk Register

| Risk | Category | Likelihood | Impact | Mitigation | Owner | Trigger |
|------|----------|:----------:|:------:|------------|-------|---------|
| **No outcomes data for measuring lift** | Data | High | High | Build labeling workflow ("worth outreach" / "pass") before Phase C | @nikhi | Can't compute precision@K |
| **Scope creep from SHADOW features** | Operational | Medium | Medium | SHADOW mode = 0 weight; features don't affect output until promoted | @nikhi | >2 promotions/month |
| **Scoring weight conflicts** | Technical | Medium | Medium | Run spine weights parallel to exit_predictor; compare correlation | @nikhi | Disagreement on >20% of signals |
| **Boilerplate templates miss our FPs** | Technical | Medium | Low | Start in SHADOW, measure actual FP reduction before suppressing | @nikhi | <10% FP reduction after 3 weeks |
| **Maintenance burden from templates** | Operational | Medium | Medium | Cap at 10 templates initially; add suggest_signatures.py script | @nikhi | >2 hrs/month maintenance |
| **API budget exceeded** | Technical | Low | Medium | Spec budgets (4500 GraphQL/hr) generous; monitor usage | @nikhi | >80% budget utilization |
| **Premature promotion** | Process | Medium | High | Enforce: 3 weeks SHADOW, 95% compute success, documented lift | @nikhi | Feature promoted without data |
| **Schema drift in evidence packets** | Technical | Low | Medium | Version packet format; add migration path | @nikhi | Downstream consumers break |

---

## Summary Verdict

**The founder_intel_canonical bundle is well-designed - adopt its experimentation model.**

| Aspect | Assessment |
|--------|------------|
| **Thesis alignment** | ✅ Excellent - consumer keywords perfectly match our thesis |
| **Architecture quality** | ✅ Excellent - ACTIVE/SHADOW/OFF is the key insight |
| **Documentation** | ✅ Excellent - comprehensive specs, schemas, script specs |
| **Immediate value** | ✅ High - SHADOW mode lets us test everything safely |
| **Integration effort** | ⚠️ Medium - need shadow logging infrastructure first |
| **Recommendation** | **Build SHADOW infrastructure, then deploy features widely** |

The spec's core insight: **"Build wide, activate narrow."**

Instead of cherry-picking, we should:
1. **Build** SHADOW logging infrastructure first (enables safe experimentation)
2. **Deploy** multiple features in SHADOW mode (compute, don't act)
3. **Measure** correlation with outcomes over 2-3 weeks
4. **Promote** only features that show lift (max 2/month)
5. **Prune** features that add noise or cost without value

This approach lets us test the value of boilerplate defense, team shape, founder surfaces, and smart money **simultaneously** without risk.

---

## Next Steps

- [x] Decision review (this document)
- [x] **Sign-off**: GO decision approved
- [x] **Phase A**: Build SHADOW logging infrastructure ✅ COMPLETE (72 tests)
- [ ] **Labeling workflow**: Create "worth outreach" / "pass" outcome tracking (blocks Phase C measurement)
- [x] **Phase B**: Merge consumer keywords into thesis_matcher.py ✅ COMPLETE (54 tests)
- [ ] **Phase C**: Boilerplate defense in SHADOW (3-5 days)
- [ ] **Phase D**: Team shape metrics in SHADOW (2-3 days)
- [ ] **Phase E**: Founder surfaces in SHADOW (3-4 days)

**Next action:** Phase C - Boilerplate Defense in SHADOW mode.

---

## Resources
- Spec bundle: C:\Users\nikhi\Downloads\founder_intel_canonical
- Discovery Engine: C:\dev\Harmonic
- CLAUDE.md thesis: Consumer CPG, Health Tech, Travel, Marketplaces

---
*Last updated: 2026-01-31 - Evaluation complete*
