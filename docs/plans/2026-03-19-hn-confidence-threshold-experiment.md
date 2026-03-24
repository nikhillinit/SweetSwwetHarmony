# HN Confidence Threshold Experiment Design

Status: **design only** — no implementation during the Step 4A observation window (March 16-23, 2026). Execution blocked until March 24, 2026 at the earliest, and only after higher-priority fixes are evaluated.

This experiment design does not gate, accelerate, or delay Step 4B.

## 1. Objective

Measure the impact of an HN-specific confidence floor on false positive suppression. The candidate threshold is 0.65 (signals below this floor would be held instead of routed).

## 2. Current Evidence

Source: `artifacts/activation/step4a_promotion_2026-03-16T19-05-16/hn-fp-investigation-2026-03-19.md`

- **30-day HN FP rate:** 41/42 FP (100% of labeled HN signals are false positives)
- **90-day HN FP rate:** 151/157 FP (98.69%)
- **Confidence distribution:** 83% of the 12-signal coded FP sample had confidence <= 0.70. This figure supports a **0.70 cutoff**, not a 0.65 cutoff.
- **FP suppression rate at 0.65:** Unknown. This would need to be measured empirically by querying signal confidence scores at that threshold.
- **Known TPs:** 2 historical HN true positives — Wildex (confidence 0.72) and FlightDeepResearch (confidence 0.60).
- **Root cause:** All HN signals have thesis_category=UNKNOWN (unclassified by LLM). The primary driver of the 100% FP rate is missing thesis classification, not confidence scoring alone.

## 3. Proposed Experiment

### Design

Shadow-mode confidence floor: log what would happen if HN signals below a candidate threshold (0.65) were held instead of routed, without changing actual routing decisions.

### Metrics to capture

- **FP suppression rate:** What fraction of known FPs would be held?
- **TP loss rate:** What fraction of known TPs would be lost? (FlightDeepResearch at 0.60 would be lost under a 0.65 floor.)
- **Per-signal breakdown:** For each HN signal in the evaluation window, record original confidence, original routing decision, and shadow decision under the floor.

### Recommended analysis workflow (post-window)

Use the Quality Ops CLI rather than wrapper scripts:

```bash
# Keyword-vs-LLM disagreement analysis (identifies HN signals where keyword filter differs from LLM)
python -m ops.cli quality thesis-disagreement-report --days 30 --keyword-threshold 0.4 --out /tmp/hn-thesis-disagreements.md

# FP pattern detection (identifies collector/category concentration)
python -m ops.cli quality find-patterns --days 30 --min-count 10 --out /tmp/hn-patterns.json

# Before/after FP rate measurement
python -m ops.cli quality stats --days 30 --min-labeled 10
```

Optional, only if LLM classification coverage is sparse:

```bash
python -m ops.cli quality thesis-classify-batch --days 30 --limit 200
```

Note: `thesis-classify-batch` is quota-sensitive (consumes Gemini API quota).

## 4. Risks

- **TP loss at 0.65:** FlightDeepResearch (confidence 0.60) is a genuine consumer travel signal. A 0.65 floor would suppress it. This is the primary risk.
- **Insufficient evidence for 0.65 specifically:** The 83% figure applies to a 0.70 cutoff. The FP suppression rate at 0.65 is unmeasured and could be materially different.
- **Threshold alone won't fix the root cause:** HN signals are unclassified (thesis_category=UNKNOWN). A confidence floor addresses a symptom (low-quality signals pass through) rather than the root cause (missing thesis classification).

## 5. Dependencies and Missing Plumbing

- **No existing HN-specific threshold hook.** The codebase has generic shadow support via `FEATURE_<NAME>=active|shadow|off` in `utils/feature_states.py`, but there is no per-collector confidence floor mechanism. Implementing this would require new code in the verification gate or routing logic — which is blocked during the observation window and should be evaluated for necessity after higher-priority fixes.
- **Higher-priority fixes come first:**
  1. **Primary:** Enable `LLM_THESIS_MODE=active` for HN signals (addresses root cause: all HN signals are thesis_category=UNKNOWN)
  2. **Secondary:** Improve HN parser heuristics to reject non-startup content (addresses 25% parsing artifact FPs)
  3. **Tertiary:** Confidence threshold experiment (this document) — only if fixes 1 and 2 are insufficient

## 6. Decision Criteria

Proceed with threshold implementation only if:

- LLM thesis classification is active for HN signals AND parser improvements are deployed
- The combined FP rate after those fixes remains above an acceptable threshold (e.g., > 30%)
- The shadow experiment confirms that a specific threshold suppresses a meaningful fraction of remaining FPs without losing TPs
- The TP loss rate is acceptable (losing FlightDeepResearch-class signals is a known cost)

## 7. Status

**Design only.** This document may be written during the observation window. All implementation, testing, environment changes, and execution are blocked until:

- The Step 4A observation window closes (March 23, 2026)
- Higher-priority fixes (LLM activation, parser improvements) are evaluated
- The regret check passes (March 30, 2026)

Earliest possible execution: March 24, 2026, and only if the dependency chain above is satisfied.
