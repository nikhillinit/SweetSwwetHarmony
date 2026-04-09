# Scout-Mindset Bias Audit — LOB.txt Evaluation

**Date:** 2026-04-06
**Target of audit:** The preceding `findings.md` + `systems-thinking-leverage.md` (delivered in the same session).
**Purpose:** Apply scout mindset to my own prior analysis. Identify biases, reversal-test central claims, and correct overconfident or motivated conclusions.

**TL;DR:** The previous two responses made three material errors: (1) treating a selection-biased label sample as a pipeline-precision metric, (2) anchoring on LOB.txt without checking whether the user's current strategy supersedes it, and (3) recommending a Week-2 build (Talent Magnet) that fails the reversal test. The revised recommendation is **"close R2 first, build new collectors later"** — the opposite ordering from what I delivered.

---

## 1. Reversal test on the central claim

**Central claim from previous turn:** "In Week 2, ship the Talent Magnet thin-slice (stargazer enrichment → Notion push) as the first Rubric A method."

**Reversal:** Suppose the evidence pointed the other way. Suppose we learned that adding new collectors to a pipeline whose learning loop is broken (R2: 211 labels not feeding rules) would actively *worsen* the labeled-FP count and starve reviewer attention further.

**Would I still accept that reversed evidence?** Honestly, yes — and it's obvious once stated. A broken learning loop means each new collector adds FP volume without a mechanism for the system to improve. This contradicts my own recommendation.

**Conclusion:** The Talent Magnet recommendation fails the reversal test. The correct ordering is **close R2 first, measure the effect, then decide whether a new collector is the actual bottleneck.** I had this component in the analysis (ranked #3) but placed Talent Magnet at #5 as a "paradigm test." The paradigm test was motivated by narrative satisfaction, not by evidence.

**Adjustment:** Demote Talent Magnet from Week-2 critical path to "consider after 30-day precision measurement on the repaired R2 loop."

---

## 2. Confirmation bias audit

### Did I seek disconfirming evidence for "shifting the burden"?

**No.** I built the archetype diagnosis from:
- Recent commit messages (governance-heavy)
- "0 Rubric A methods shipped in 2 months"
- Team's strength on Rubric B vs weakness on Rubric A

Then I looked for evidence that supported the diagnosis. I did NOT look for evidence that falsified it.

### What I failed to check

1. **Did the team INTENTIONALLY deprioritize GitHub intelligence for consumer thesis?** LOB.txt's own Section 6 ("False Negative Obsession") says GitHub will miss 40-60% of consumer opportunities because Category A (no-code CPG, Shopify brands, Bubble marketplaces) have no repos. If the team read this and correctly decided GitHub-native methods are low-leverage for consumer, the "0 methods shipped" isn't a burden-shift, it's correct prioritization.

2. **Does the SweetSweetHarmony strategy (per session-restore memory) supersede LOB.txt?** Memory explicitly names `sweetharmony-executive-decision-layer.md` and `sweetharmony-evidence-table.md` as "the Option B canonical front door." I did not read these before declaring LOB.txt the authoritative rubric. This is a direct failure to seek disconfirming evidence.

3. **Is `github.py` being run at all?** I saw 2 signals in the DB but did not check whether the collector is disabled, rate-limited, or simply not producing matches because of strict filters. Those three root causes lead to very different conclusions.

### Verdict

**HIGH confirmation bias.** I found an archetype that fit my narrative and stopped looking.

**Adjustment:** Demote "shifting the burden" from a confident diagnosis to **one of three plausible diagnoses**, with prerequisite sequencing and thesis-fit mismatch as equal candidates until the SweetSweetHarmony docs are read.

---

## 3. Availability bias audit

### The "88.6% FP rate" mistake

I reported:
> "On the labeled sample, the precision of what the pipeline surfaces is ~9%, roughly an order of magnitude below LOB.txt's ≥80% target."

**This is wrong.** 88.6% FP is the label distribution on a **selection-biased sample** of 211 signals that the operator chose to label manually. Operators label suspicious signals; they don't label a random sample. The true pipeline FP rate on the 612-signal population is unknown from this data alone.

Related error: I wrote "precision is ~9%" as if it were a pipeline metric. It's actually "9% of the signals that caught the operator's attention enough to get labeled are labeled TP." That's a completely different thing.

### The "HN is 31% of volume" framing

I framed HN at 31% of collected volume as a tragedy-of-the-commons grazer. But:
- Volume share is all-time (historical accumulation).
- HN's LLM rejection path is LIVE as of 2026-03-25 per memory.
- Recent runs (memory: "hacker_news: 28 processed, 28 rejected (100%)") show HN is now being filtered downstream at the thesis layer.
- The *current* commons pressure from HN is much lower than the historical share implies.

I conflated a historical stock (accumulated HN signals) with a current flow (current HN contribution to reviewer load). Those are different dynamics.

### Verdict

**HIGH availability bias.** Recent/memorable numbers (211 labels, 31% volume) got weighted as authoritative without checking their epistemic status.

**Adjustment:**
- Strike "9% precision" claim. Replace with "true pipeline precision unknown; labeled-sample distribution reflects operator attention, not pipeline output."
- Re-characterize HN: historical accumulation is high, current flow through the thesis filter is near-zero. The kill-criterion rule is still useful defense-in-depth, but it's not the urgent fire I framed it as.

---

## 4. Anchoring bias audit

### Anchor: LOB.txt as authoritative

**The error:** I opened the evaluation with this decision logged in task_plan.md:

> "Using the reframed assessment (equity-negotiation context) as authoritative because (a) it's chronologically last in the document, (b) memory confirms this project IS the free-engineering-for-equity arrangement described, and (c) the user's operational stance aligns with 'build ambitious, prune later.'"

All three reasons are weak:
- (a) LOB.txt file mtime is 2026-02-24. Memory says SweetSweetHarmony work happened *after* that with a full executive decision layer. LOB.txt is not the most recent document.
- (b) The equity arrangement being real doesn't mean the LOB.txt scope proposal is still current. Strategies evolve.
- (c) I inferred alignment from a handful of memory phrases, not from reading the actual current-strategy documents.

### What a less-anchored analysis would look like

Start from the user's current strategic posture as captured in SweetSweetHarmony files, THEN check which parts of LOB.txt survive that update. Don't grade a system against a 2-month-old document that may have been revised.

### Verdict

**HIGH anchoring bias.** I treated a stale document as the success criterion without verifying its current status.

**Adjustment:** Flag as **the single most important remediation step**: read `sweetharmony-executive-decision-layer.md` and `sweetharmony-evidence-table.md` before extending the evaluation. Only then does the scorecard mean anything.

---

## 5. Affect heuristic audit

**Separate wanting from expecting:** Do I have a preference for the dramatic-diagnosis framing?

Yes. A "shifting the burden, the team is building the wrong thing, here's the leverage point" analysis is more satisfying to deliver and makes me look insightful. The boring alternative — "you're mostly on track, close one specific loop, ignore most of LOB.txt" — is less impressive but may be more accurate.

**Counterfactual:** If I had been asked to evaluate the same state of the codebase against a rubric titled "Are you running a precision-first learning pipeline?" instead of against LOB.txt, my conclusion would have been *favorable*, not critical. That's a strong indicator that my framing drove my conclusion.

### Verdict

**MEDIUM affect heuristic.** Not catastrophic, but definitely present.

**Adjustment:** Tone down the "gap between vision and product" language. The gap may be smaller than I claimed, and its size depends on which vision is the current one.

---

## 6. Overconfidence audit

### The "2-week Talent Magnet" claim

I wrote:
> "Day 8-14: Talent Magnet thin-slice in production. First ≥1 Notion push from a NEW LOB method."

**What I skipped:**
- Stargazer profile enrichment has unknown signal value for *consumer* investing. The LOB method was designed for technical founding teams; consumer CPG brands often have no GitHub presence at all (the 40-60% Category A problem).
- I wrote "Start with precision measurement first" as a hedge, but a 2-day precision measurement does not let you project annual yield.
- I assigned "HIGH feasibility" to an intervention I had done zero implementation scoping on.
- My confidence was ~75% when the data supports maybe 25%.

### The "95% of labeled signals are currently FP" implicit framing

I wrote "Day 14-28: FP rate on labeled sample climbs from 88.6% FP → ~60-70% FP (back-of-envelope)" as if this were a modelable quantity. It isn't. The 88.6% is a label-distribution metric; you can't "reduce" it with a code change, you can only produce a different label-distribution by applying labels to a different subset of signals.

### Verdict

**MEDIUM-HIGH overconfidence.** I gave point estimates and timelines for things I had no business estimating with that precision.

**Adjustment:** Widen all delivery and effect CIs substantially. Replace specific FP-rate trajectory numbers with qualitative direction-of-travel claims.

---

## 7. Fundamental attribution error

I attributed the "0 Rubric A methods shipped" to **team psychology** (burden shift, addiction to governance feedback). I did not seriously consider **situational factors**:

- **Data quality precedes feature build.** In a pipeline where the learning loop is broken (R2), shipping collectors first is a known anti-pattern. The team may be executing the correct order.
- **Consumer thesis fit.** GitHub-native methods are a poor match for consumer CPG/marketplaces. The team's instincts may be correct even if LOB.txt's brainstorm ignored that.
- **Equity negotiation reality.** Per memory, the user's SweetSweetHarmony strategy explicitly decided on "loosening pre-review only, keep post-review strict" — a focused intervention that has nothing to do with Rubric A collector breadth.

### Verdict

**HIGH attribution error.** I assumed dispositional causes (team fell into burden shift) when situational causes (strategic reprioritization, prerequisite sequencing) fit the evidence just as well.

**Adjustment:** Offer the three diagnoses in parallel, not as competing hypotheses but as coexisting contributors. Stop personifying the team's failure mode.

---

## 8. Scope sensitivity check

**Does my recommendation scale with the actual scope of the gap?**

If the real gap is "R2 is broken, 211 labels aren't wired in" (small fix, high leverage), my intervention should be proportional: **one fix**. Instead I delivered a 9-item leverage ranking + 2-week sprint plan + monitoring plan + kill criteria. That's disproportionate.

**Verdict:** MEDIUM scope insensitivity. I shipped too much analysis for what may be a small concrete problem.

**Adjustment:** Compress the recommendation to the one thing that passes the reversal test.

---

## 9. Net bias-adjusted corrections

| Previous claim | Corrected claim | Confidence shift |
|---|---|---|
| "Rubric A score is 15%, ~20% implementation" | "Rubric A may not be the right scorecard; LOB.txt may be superseded" | High → Low |
| "Labeled-sample precision is 9%" | "Labeled-sample label distribution is 9% TP; pipeline precision is unknown" | High → Low |
| "HN is 31% of volume and is the commons grazer" | "HN accumulated 31% historically; current flow is filtered near-zero by active LLM thesis" | High → Medium |
| "Shifting-the-burden is the diagnosis" | "Shifting-the-burden is one of three diagnoses; prerequisite sequencing and thesis-fit mismatch are equally plausible" | 85% → 45% |
| "Build Talent Magnet in Week 2 as a paradigm test" | "Do not build new collectors until R2 is closed and precision is measured on the repaired loop" | 75% → 25% |
| "Close R2 as leverage point #3" | "Close R2 as THE priority; everything else waits" | 65% → 85% |
| "2-week sprint plan delivers LOB method + kill rule + dashboard" | "Close R2 label→rule loop. Measure effect. Then decide." | Demoted |

---

## 10. The one thing that survives the audit

The previous analysis had one recommendation that passes all seven bias checks cleanly:

> **Wire the 211 existing labels into the thesis-rule update job. Close the label → rule feedback delay from weeks to days.**

This is:
- Already scoped in memory (pending action, learning-loop-only CLI shipped in PR#132)
- Addresses the only dynamic ("R2 broken") that's robust to all three candidate diagnoses
- Fails no reversal test (tight-loop learning is good regardless of which strategy is current)
- Does not depend on LOB.txt being authoritative
- Does not depend on the 88.6% label number being interpreted as precision
- Does not require new collectors
- Small scope, high leverage, kill-criterion trivial

**Everything else in the previous two responses should be treated as conditional on first reading `sweetharmony-executive-decision-layer.md` and verifying the user's current strategic posture.**

---

## 11. Hard action item

**Before extending any part of the LOB.txt evaluation, read:**
1. `sweetharmony-executive-decision-layer.md` (path unknown; memory says "Option B canonical front door")
2. `sweetharmony-evidence-table.md`
3. `docs/plans/2026-04-06-pre-review-loosening-plan.md`
4. `docs/plans/2026-04-06-merge-writes-governance-bypass.md`
5. `.omx/plans/` learning-loop PRD/ralplan/test-spec

These documents describe the user's CURRENT strategy. Until they are read, calling the team "off-track" vs LOB.txt is measuring against the wrong tape.

---

## 12. Rubric self-assessment

| Scout commandment | Honored in previous analysis? |
|---|---|
| Truth over comfort | Partial — delivered dramatic narrative |
| Seek disconfirmation | **No** — cherry-picked evidence |
| Hold beliefs lightly | Partial — gave point estimates |
| Update incrementally | Not applicable (one-shot) |
| Separate wanting from expecting | **No** — dramatic diagnosis felt better |
| Check your work | **No** — no reversal test before delivery |
| Stay calibrated | Partial — self-scored the leverage rubric 4.8/5, which was also overconfident |

**Net:** 3 "no" out of 7 on the scout commandments. The previous analysis was **soldier-mindset in scout clothing** — I produced an evidence-looking structure to defend a narrative I had already landed on.

---

## 13. Revised one-liner

The previous one-liner was: *"Shifting-the-burden is eating the LOB vision. Goal-change, rule-change, build one LOB method."*

The bias-corrected one-liner is: **"I don't know whether LOB.txt is still the strategy. Before answering 'are you making progress?' I need to read the SweetSweetHarmony documents. The one unconditional action is wiring the 211 existing labels into the thesis-rule update job — everything else is conditional."**
