# Findings: Discovery Engine v1.1 — Architectural Analysis

**Related Docs:**
- [Task Plan v1.1](task_plan_v1.1.md) — Phased implementation roadmap
- [Baseline Task Plan](task_plan.md) — Current data bootstrap plan
- [v1.1 Proposal](C:\Users\nikhi\Downloads\Discovery Engine v1.1.txt) — Full specification

**Session:** discovery-engine-v1.1-implementation (cca1f8e0)
**Created:** 2026-02-08

---

## Finding 1: v1.1 Solves 10 Critical Issues Baseline Doesn't Address

Side-by-side comparison shows v1.1 wins in 12 of 13 dimensions:

| Dimension | Baseline | v1.1 | Winner |
|-----------|----------|------|--------|
| Notion pollution fix | ✅ CSV export + manual push | ✅ Delivery policy + batch | v1.1 (more sophisticated) |
| Flexible filtering | ✅ Lower thresholds + adjacent | ✅ Functional Deconstruction + ACH + exemplars | v1.1 (deeper understanding) |
| Web3 hard block | ✅ Heavy keyword penalty | ✅ Deterministic co-occurrence rules | v1.1 (more accurate) |
| Implementation time | ~3 hours | ~4-6 weeks (18 PRs) | Baseline (faster to value) |
| False negative prevention | ⚠️ Manual CSV review | ✅ Exemplar veto + canaries + thin files | v1.1 (systematic protection) |
| Operator time efficiency | ⚠️ Review entire CSV | ✅ Two-pass triage + progressive disclosure | v1.1 (scales with volume) |
| Learning from mistakes | ⚠️ Manual labels only | ✅ Anti-patterns + exemplars + drift alerts | v1.1 (continuous improvement) |
| Identity management | ⚠️ Assumes canonical_key stable | ✅ company_id + merges + aliases | v1.1 (handles complexity) |
| Decision semantics | ❌ Mutates AUTO_PUSH → REVIEW_QUEUE | ✅ Separates decision from delivery | v1.1 (cleaner architecture) |
| Runtime resilience | ❌ Requires LLM for all operations | ✅ Fallback paths (TF-IDF, cached) | v1.1 (works without LLM) |
| Collaboration | ⚠️ Single operator only | ✅ Operator + explorers with RBAC | v1.1 (scales with team) |
| Batch publishing | ⚠️ Manual one-by-one from CSV | ✅ Git-style preview→commit workflow | v1.1 (safer + faster) |
| Sparse signals | ❌ Either queue bloat or false negatives | ✅ Thin files accumulator | v1.1 (handles early-stage better) |

**Impact:** v1.1 is the right long-term architecture. Baseline has one advantage: speed to value (~3 hours vs 4-6 weeks).

---

## Finding 2: Baseline Plan Has 10 Critical Architectural Issues

### 2.1 Mutating Decision Semantics (Anti-Pattern)

**Baseline proposal:**
```
"Change AUTO_PUSH and NEEDS_REVIEW decisions to REVIEW_QUEUE"
```

**Why this is wrong:**
- Couples decision logic (what system recommends) to delivery policy (what actions allowed)
- Makes code harder to reason about: "what does AUTO_PUSH mean if it doesn't push?"
- If you later want auto-publish for high-confidence, must unmutate enums

**v1.1 solution:**
```python
# Decision enums stay stable (describe confidence/recommendation)
decision = AUTO_PUSH | NEEDS_REVIEW | HOLD | REJECT

# Delivery policy decides whether to write
delivery_action = assert_notion_write_allowed(
    intent="manual"|"auto",
    publish_context={mode:"single"|"batch", batch_id?}
)
```

**Impact:** v1.1 has cleaner separation of concerns, more maintainable codebase.

---

### 2.2 No Protection Against False Negatives at Scale

**Baseline approach:**
- Lower thresholds → bigger CSV → manual review
- Assumes human can catch everything

**Problems:**
- 500 entries/week? Will start skimming, miss good companies
- No systematic protection against "conservative drift" (over-filtering over time)
- No way to detect when anti-patterns are too aggressive

**v1.1 solutions:**
1. **Exemplar veto (§10.3):** High-similarity to known wins → cannot be auto-dropped
2. **Canary regression checks (§10.4):** Known-good edge cases → if quarantined, fail closed
3. **Drift alerts (§16.3):** SPC monitoring detects when FP rate or quarantine regret shifts
4. **Thin files with sampling (§5.1):** Sparse signals accumulate, 5-10% always surfaced
5. **Two-pass triage (§13.2):** Fast pass for obvious, deep review for ambiguous

**Impact:** v1.1 systematically prevents system from becoming too conservative.

---

### 2.3 Web3 Keyword Approach Creates False Positives

**Baseline approach:**
```python
("token", 0.8),  # Can appear in non-crypto contexts
("dao", 0.8),    # Can mean "data access object"
```

**Problems:**
- Ambiguous terms reject legitimate companies:
  - "Access tokens for API authentication"
  - "DAO pattern in software architecture"
  - "Loyalty tokens for consumer apps"

**v1.1 solution (§14):**
```python
def is_crypto_context(text, term):
    # Only flag "token" as crypto if near: blockchain, ethereum, NFT
    crypto_context_window = 100 chars
    if term == "token":
        return any(crypto_term in nearby_text for crypto_term in CRYPTO_TERMS)
    return True  # Other terms always count
```

**Impact:** Baseline creates false positives. v1.1 is more accurate.

---

### 2.4 No Identity Stability Strategy

**Baseline assumption:** canonical_key is stable

**Doesn't handle:**
- Company rebrands
- Domain changes
- Stealth companies that later reveal domain
- Duplicate detection across sources

**v1.1 solution (§4):**
- `company_id`: immutable UUID (never changes)
- `canonical_key`: deterministic upsert key (can evolve via merges)
- Alias tracking for merged keys
- Clustered merge suggestions (§12)

**Real-world scenario:**
```
Signal A: "Stealth Health Tech" → key: n:stealth_health_tech::github
Signal B: "Stealth Health Tech" (adds domain) → key: d:stealthhealth.com
Baseline: Creates 2 separate CSV entries
v1.1: Suggests merge, operator approves → consolidated under one company_id
```

**Impact:** Without identity management, duplicates and lost signal context.

---

### 2.5 No Systematic Learning from History

**Baseline:**
- Manual labels → `signal_quality_metrics`
- No mechanism to learn reusable patterns

**v1.1 solutions:**

1. **Functional anti-patterns (§11.1-11.3):**
   - Extract patterns from FP labels: `{customer: engineering_teams, problem: infra_monitoring}` → quarantine
   - Human-in-loop approval before affecting routing
   - Decay if unused (prevents stale rules)

2. **Thesis Exemplar Library (§11.4):**
   - Positive patterns from wins: `{customer: creators, problem: content_creation}` → priority boost
   - Prevents over-filtering drift
   - Exemplar veto stops auto-drops

3. **Case-law precedents (§7):**
   - Comparative reasoning: "This looks like X (win) and Y (loss)"
   - Recency warnings for old precedents
   - TF-IDF retrieval (works without LLM)

**Impact:** v1.1 builds institutional memory that improves over time. Baseline relies on human memory.

---

### 2.6 CSV is Not a State Machine

**Baseline:** "CSV export of review-worthy candidates"

**Problems:**
- No audit trail (who approved what, when?)
- No state transitions (pending → approved → published)
- No cooldown logic (don't resurface rejected every week)
- Merge conflicts if multiple people edit CSV

**v1.1 solution (§5.2, §15.1):**
- `ReviewItem` table is system of record with lifecycle state machine
- CSV is deterministic export snapshot (optional once dashboard exists)
- `publish_batches` table with preview→commit workflow
- Audit logging built-in

**Impact:** Baseline CSV doesn't scale beyond 1 operator.

---

### 2.7 No Ambiguity Handling

**Baseline:** Binary decision: CSV or reject

**v1.1 solution (§8):**

1. **ACH matrix (Analysis of Competing Hypotheses):**
   - Structured reasoning: evidence × hypotheses matrix
   - Shows which evidence supports/contradicts each hypothesis
   - Partially non-LLM (deterministic rules + retrieval)
   - Persisted as audit trail

2. **Adversarial Tribunal (optional wrapper):**
   - Bull case + bear case + differentiators
   - Only for rare high-leverage ambiguity
   - Must cite evidence spans

**Real-world scenario:**
```
Company: "AI-powered meal planning app for enterprise cafeterias"
Ambiguity: Consumer health tech OR B2B food service?
Baseline: Put in CSV, operator guesses
v1.1: ACH matrix shows evidence for both hypotheses, operator decides with context
```

**Impact:** v1.1 helps operator make better-informed decisions on edge cases.

---

### 2.8 No Upstream Query Improvement

**Baseline:** Collectors run fixed queries, triage filters results

**v1.1 solution (§9):**
- **Active Hunter:** Generates targeted queries from:
  - Win precedents
  - Thesis exemplars
  - Functional schema patterns
- Sandbox + promotion rules (must prove quality before production)
- Query budget prevents explosion

**Example:**
```
Win: "DTC clean beauty brand using AI for personalized skincare"
Active Hunter generates: "AI skincare personalization consumer DTC"
Runs in sandbox → 60% qualified rate → promoted to production
```

**Impact:** v1.1 improves signal quality at source, not just filtering. Baseline only filters.

---

### 2.9 No Operator Efficiency Features

**Baseline:** Review entire CSV manually

**v1.1 efficiency features:**

1. **Two-pass triage (§13.2):**
   - Fast pass: 1-line summary, approve/reject/deep-review
   - Deep review: Full evidence, ACH, case-law only when needed
   - Keyboard shortcuts for speed

2. **Priority ordering:**
   - Exemplar similarity → top of queue
   - High confidence + multi-source → top
   - Disagreement flags → middle (needs attention)
   - Low confidence → bottom

3. **Progressive disclosure:**
   - Compact view by default
   - Expand on demand for details

**Impact:** At 100 signals/week, baseline requires 1-2 hours/day. v1.1 targets <30 min/day via triage.

---

### 2.10 No Collaboration Model

**Baseline:** Single operator (you)

**v1.1 (§13.1):**
- **Operator:** Approve/reject, publish, approve constraints
- **Explorers:** Browse, search, comment, flag ("take a look at this one")
- **RBAC + audit logging**

**Real-world scenario:**
```
Analyst finds interesting company in queue
Flags it: "This looks like the robotics refurbishment model we discussed"
Operator sees flag, reviews with context, approves
Without this: Analyst emails/Slacks, context is lost
```

**Impact:** v1.1 supports 5-person fund collaboration. Baseline is solo-only.

---

## Finding 3: v1.1 Has 4 Breakthrough Innovations

### 3.1 Functional Deconstruction (§6)

**Baseline:** Categories (`consumer_cpg`, `consumer_health_tech`)

**v1.1:** Functional schema:
```json
{
  "problem_solved_text": "Creators struggle to monetize short-form video",
  "customer_text": "TikTok/Instagram creators with 10k-100k followers",
  "approach_text": "AI-powered brand matching + automated sponsorship contracts",
  "customer_archetype": "creators",
  "problem_archetypes": ["content_monetization", "creator_economy"]
}
```

**Why this matters:**
- **Solves label trap:** "AI video editing for developers" looks like dev tool, but functional schema shows `customer: creators` → thesis fit
- **Enables similarity matching:** Find companies solving similar problems
- **Powers Active Hunter:** Generate queries from problem/customer patterns
- **Better than categories:** Captures **how** not just **what**

**Impact:** This is the biggest upgrade. Baseline uses shallow categories; v1.1 uses deep functional understanding.

---

### 3.2 Thin Files Pattern (§5.1)

**Problem:** Early-stage signals are sparse:
```
Week 1: HN mention
Week 2: Nothing
Week 3: Nothing
Week 4: GitHub repo created
→ Create 2 ReviewItems (queue bloat) or drop first signal (false negative)?
```

**v1.1 solution:**
- `CompanyFile` accumulator (thin file)
- Status: `thin|promoted|archived`
- Promotion rules: 2+ sources OR trusted source OR high exemplar similarity OR operator manual
- 60-day archive if no new evidence
- Always sample 5-10% of thin files in exports

**Impact:** Handles sparse early-stage signals without false negatives or queue bloat.

---

### 3.3 Batch Publish with Preview (§15.2)

**Baseline:**
```bash
push-from-csv --signal-ids 123,124,125
```

**v1.1:**
```bash
publish batch create --from approved --limit 10
# Returns batch_id

publish batch preview <batch_id>
# Shows diff: 3 new, 7 updates

publish batch commit <batch_id>
# Guarded write (DELIVERY_MODE=batch_publish required)
```

**Why this is better:**
- **Preview before commit:** See what will change (Git-style workflow)
- **Atomic batch:** All succeed or all fail
- **Audit trail:** `publish_batches` table tracks who published what when
- **Compensate:** Optional compensating actions if something goes wrong

**Impact:** Safer, faster, more auditable than baseline CSV approach.

---

### 3.4 Runtime Without LLM (§3.3)

**Baseline:** Requires Gemini API for all LLM classification

**v1.1 fallback paths:**
- **Functional schema:** Use cached if exists, else light heuristic
- **Similarity:** TF-IDF vectorizer (built in builder mode, frozen for runtime)
- **ACH:** Deterministic rules + retrieval (no LLM needed for partial matrix)
- **Tribunal:** Skip if LLM unavailable

**Why this matters:**
- API outages don't break pipeline
- Budget constraints don't stop operations
- Can run offline/air-gapped

**Impact:** v1.1 is production-grade resilient. Baseline is fragile to API issues.

---

## Finding 4: Baseline Plan Has 3 Strengths to Preserve

### 4.1 Speed to Value
- Baseline: ~3 hours implementation
- v1.1: ~4-6 weeks (18 PRs)

**Recommendation:** Start with baseline Phase 0 quick wins, then adopt v1.1 incrementally.

---

### 4.2 Simplicity
- Easy to understand
- Easy to maintain (initially)
- Solves immediate problem (Notion pollution)

**Recommendation:** Use baseline's immediate fixes (CSV export, manual push) while building v1.1 foundations.

---

### 4.3 Correctness on Core Issues
- ✅ Identified Notion pollution problem
- ✅ Correctly flags Web3 as hard exclusion
- ✅ Recognizes adjacent categories need
- ✅ CSV export is right direction (just not complete solution)

**Recommendation:** These insights inform v1.1 Phase 0 priorities.

---

## Finding 5: Phased Adoption Strategy is Optimal

**Hybrid approach merges baseline quick wins with v1.1 architecture:**

```
Phase 0 (Week 1): Baseline Quick Wins
├─ ✅ Disable auto-push (delivery policy guard)
├─ ✅ CSV export function
├─ ✅ Manual push command
└─ ✅ Lower thresholds
   Difference from baseline: Use v1.1's delivery policy layer instead of mutating enums

Phase 1 (Week 2): v1.1 Foundations
├─ ✅ Canonical identity (company_id + canonical_key algorithm)
├─ ✅ ReviewItem state machine
├─ ✅ CompanyFile / thin files
└─ ✅ Batch publish scaffolding

Phase 2 (Week 3-4): v1.1 Intelligence
├─ ✅ Functional schema extraction (confidence-gated)
├─ ✅ Web3 co-occurrence detector
└─ ✅ Adjacent categories in LLM prompt (corrected)

Phase 3 (Week 5-6): v1.1 Learning
├─ ✅ Case-law corpus + TF-IDF retrieval
├─ ✅ Anti-patterns (propose → approve)
└─ ✅ Thesis exemplars + exemplar veto

Phase 4+ (Month 2+): v1.1 Advanced
├─ ✅ Dashboard with two-pass triage
├─ ✅ ACH matrix
├─ ✅ Active Hunter
├─ ✅ Drift alerts + canaries
└─ ✅ Entity resolution
```

**Key insight:** Can get Notion pollution fix in Week 1 while building toward mature system.

---

## Finding 6: Integration with Baseline Plan is Clean

**No conflicts detected.** Baseline plan (data bootstrap) complements v1.1 (governance + intelligence).

**Parallel tracks:**
- **Baseline Phase 4 (ML training):** Continues while v1.1 Phase 0-1 implements
- **Baseline Phase 5 (tuning):** Enhanced by v1.1 Phase 3 (anti-patterns + exemplars)
- **Baseline Phase 6 (live mode):** Delayed until v1.1 Phase 2 (intelligence layer) complete

**Critical path merge:**
```
NOW: Baseline Phase 4 (ML) + v1.1 Phase 0 (delivery policy)
Week 2: Baseline Phase 4 done → v1.1 Phase 1 (identity + queue)
Week 3-4: v1.1 Phase 2 (functional + Web3) → enables Baseline Phase 5 (tuning)
Week 5-6: v1.1 Phase 3 (case-law + exemplars) → enhances Baseline Phase 6 (live)
```

---

## Finding 7: v1.1 Addresses Real Problems You'll Hit

These aren't hypothetical:

1. **Duplicate detection:** Same company from GitHub + HN + SEC filing
2. **Conservative drift:** Anti-patterns become too aggressive, FN rate increases
3. **Operator fatigue:** 500 signals/week in CSV, start skimming
4. **False positives:** "Access tokens" rejected by keyword penalty
5. **Lost institutional knowledge:** Onboard new team member, patterns forgotten
6. **Identity instability:** Stealth company reveals domain, need merge
7. **Sparse signals:** Week 1 HN mention, Week 4 GitHub repo → same company?
8. **Ambiguity paralysis:** Consumer health tech OR B2B? Need structured reasoning
9. **No audit trail:** Who approved what? When? Why?
10. **Single point of failure:** Only you can review, publish, approve

**Impact:** v1.1 solves problems before they become critical.

---

## Finding 8: Implementation Risk is Low

**v1.1 safety mechanisms:**
- Confidence gating (low confidence = advisory only, no hard routing)
- Shadow → quarantine → sampled exposure → only then drop
- Human-in-loop approval for constraints
- Exemplar veto prevents autoimmune false negatives
- Canary regression checks block bad changes
- Rollback/compensate paths for batch publish
- Non-LLM fallbacks for runtime resilience

**No breaking changes:**
- Baseline data bootstrap continues
- ML classification unchanged
- Existing collectors/skills work as-is
- CSV export still supported

**Incremental adoption:**
- Each phase delivers value independently
- Can pause at any phase if priorities change
- No "big bang" rewrite

---

## Finding 9: Resource Requirements are Reasonable

**Phase 0 (Week 1):** 4-6 hours
- Delivery policy layer
- CSV export + manual push
- One developer, no dependencies

**Phase 1 (Week 2):** 12-16 hours
- Canonical identity + migrations
- ReviewItem + CompanyFile
- Thin file logic
- One developer, DB migrations

**Phase 2 (Week 3-4):** 16-20 hours
- Functional schema extraction
- Web3 co-occurrence
- LLM prompt updates
- One developer, LLM API

**Phase 3 (Week 5-6):** 20-24 hours
- Case-law corpus + TF-IDF
- Exemplar library + veto logic
- Anti-pattern propose → approve
- One developer, ML/NLP libraries

**Total Phase 0-3:** ~60-70 hours (~2 weeks full-time or 1.5 months part-time)

---

## Finding 10: Deferred Items are Truly Optional

**Phase 4+ features can wait:**
- Dashboard: CSV + CLI sufficient initially
- ACH matrix: Manual reasoning works for low volume
- Active Hunter: Existing collectors sufficient for bootstrap
- Entity resolution: Manual merge for initial scale
- Drift monitoring: Manual QA sufficient initially

**When to add:**
- Dashboard: >50 signals/week review volume
- ACH: Ambiguity cases >10/week
- Active Hunter: Qualified rate <30% from exploration
- Entity resolution: >100 companies with merge candidates
- Drift monitoring: After 3+ months of operation

---

## Bottom Line

**Discovery Engine v1.1 is the right architecture.** It addresses issues you haven't hit yet but will:
- Duplicate detection when same company appears across sources
- Conservative drift when anti-patterns become too aggressive
- Operator fatigue when CSV has 500 entries/week
- False positives from ambiguous Web3 keywords
- Loss of institutional knowledge when you onboard new team members

**Start with baseline Phase 0 to fix Notion pollution immediately, then adopt v1.1 incrementally.**

**Time to value:**
- Week 1: Notion pollution fixed (v1.1 Phase 0)
- Week 2: Identity stability + thin files (v1.1 Phase 1)
- Week 4: Functional intelligence + Web3 accuracy (v1.1 Phase 2)
- Week 6: Learning from history (v1.1 Phase 3)
- Month 2+: Advanced features (v1.1 Phase 4+)

**No conflicts with baseline plan.** ML training continues in parallel.
