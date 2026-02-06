# Technical Plan: Thesis Matcher Recall Improvement (v2 - Revised)

## Revision Summary
**Date:** 2026-02-04
**Reason:** Critical analysis identified architectural risks in original plan.

### Key Changes from v1:
1. Extend `negative_keyword_policy.py` instead of creating new `rejection_controller.py`
2. Use explicit `HELD_SOFT` routing instead of `REJECTED` + metadata (avoids contradictory semantics)
3. Sequence: normalization → eval → saturation caps (not parallel)
4. Single rejection handler function to replace 5 scattered `mark_rejected()` calls
5. Add status freshness check for override safety
6. Add soft-reject escalation counter (prevents re-processing loop)
7. Derive `DEFAULT_CAP` from actual thesis statistics
8. Add migration step for existing 39 false negatives

---

## Architecture: Unified Authority

### Problem
Two filtering systems + 5 `mark_rejected()` call sites = split-brain risk.

### Solution
**Extend existing infrastructure** (not add new layers):

```
negative_keyword_policy.yaml (add severity field)
    ↓
negative_keyword_policy.py (add is_hard_negative(), rejection_handler())
    ↓
thesis_filter.py (uses policy)
consumer/thesis_filter/hard_disqualifiers.py (uses same policy)
```

---

## Hard/Soft Negative Classification (Explicit Criteria)

| Classification | Criteria | Examples |
|---------------|----------|----------|
| **HARD** | Unambiguously excludes consumer thesis | `blockchain`, `cryptocurrency`, `web3`, `nft`, `defi`, `b2b`, `enterprise saas`, `developer tool`, `devops`, `sdk`, `series c`, `series d` |
| **SOFT** | Ambiguous - could be consumer-facing | `platform`, `enterprise`, `saas`, `api`, `infrastructure`, `solution` |

**Rule:** When in doubt, classify as SOFT.

---

## Decision Waterfall

```
┌─────────────────────────────────────────────────────────────┐
│ Gate A: Status Override (non-droppable)                     │
│   IF status ∈ {Funded, Diligence, Initial Meeting}          │
│   AND status_updated_at > (now - 48h)                       │
│   → QUALIFIED + reason="STATUS_OVERRIDE"                    │
├─────────────────────────────────────────────────────────────┤
│ Gate B: Hard Negatives (certain reject)                     │
│   IF any HARD negative match                                │
│   → REJECTED + rejection_type="hard"                        │
│   → Write to suppression cache                              │
├─────────────────────────────────────────────────────────────┤
│ Gate C: Soft Negatives (throttle, not permanent)            │
│   IF any SOFT negative match AND score < hold_threshold     │
│   → HELD_SOFT + rejection_type="soft"                       │
│   → Increment soft_reject_count                             │
│   → NO suppression cache write                              │
│   IF soft_reject_count >= 3 → escalate to REJECTED          │
├─────────────────────────────────────────────────────────────┤
│ Gate D: Zero-Score Rescue (vocabulary miss handler)         │
│   IF no negatives AND best_score == 0.0                     │
│   → HELD + reason="ZERO_SCORE_TRIAGE"                       │
├─────────────────────────────────────────────────────────────┤
│ Default: Legacy routing thresholds                          │
│   Use existing hold_threshold=0.3, skip_llm_if_below=0.2    │
└─────────────────────────────────────────────────────────────┘
```

---

## Phase 0: Baseline + Prerequisites (No Behavior Change)

### Task 0.1: Run Baseline Evaluation
```bash
python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_baseline.jsonl
```
**Status:** ✅ DONE (4.1% recall, 81% precision)

### Task 0.2: Audit Infrastructure
- ✅ `negative_keyword_policy.py` can be extended (has categories, weights, validation)
- ✅ Consumer hard-disqualifier logic identified in `consumer/thesis_filter/hard_disqualifiers.py`
- ✅ 5 `mark_rejected()` call sites identified

---

## Phase 1: Scoring Prerequisites (Do First)

### Task 1.1: Implement Normalization Update
**File:** `utils/thesis_matcher.py` lines 646-647
**Priority:** P0 (affects all match rates)

```python
def _normalize(self, text: str) -> str:
    import re
    text = text.lower().strip()
    # Normalize hyphens, slashes, underscores to spaces
    text = re.sub(r'[-/_]', ' ', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text
```

**Verification:**
```bash
python -c "from utils.thesis_matcher import ThesisMatcher; m = ThesisMatcher(); print(m._normalize('api-first platform'))"
# Expected: 'api first platform'
```

### Task 1.2: Run Post-Normalization Eval
```bash
python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_post_norm.jsonl
```
Record delta in 00-ITERATION-LOG.md.

### Task 1.3: Compute Saturation Caps from Post-Normalization State
**File:** `utils/thesis_matcher.py`

**Step 1:** Calculate current sums:
```python
# Run this to get actual values:
from utils.thesis_matcher import ThesisMatcher, ConsumerThesis
m = ThesisMatcher()
for thesis in ConsumerThesis:
    keywords = m.THESIS_KEYWORDS.get(thesis, {})
    print(f"{thesis.name}: {sum(keywords.values()):.1f}")
```

**Step 2:** Add frozen caps based on output:
```python
# Add after line 156 (values will be filled from Step 1)
THESIS_SATURATION_CAPS = {
    ConsumerThesis.CONSUMER_CPG: <actual_sum>,
    ConsumerThesis.CONSUMER_HEALTH_TECH: <actual_sum>,
    ConsumerThesis.TRAVEL_HOSPITALITY: <actual_sum>,
    ConsumerThesis.CONSUMER_MARKETPLACE: <actual_sum>,
}

# For unknown theses, use median of known caps (NOT arbitrary 5.0)
DEFAULT_CAP = statistics.median(THESIS_SATURATION_CAPS.values())
```

**Step 3:** Modify `_score_thesis` to use caps:
```python
max_possible = THESIS_SATURATION_CAPS.get(thesis, DEFAULT_CAP)
```

### Task 1.4: Invariance Test
Add test that adding keywords does NOT change scores for existing samples.

---

## Phase 2: Unified Routing Controller + Recoverable Suppression

### Task 2.1: Add Severity Field to negative_keyword_policy.py

**File:** `utils/negative_keyword_policy.py`

```python
class NegativeKeywordSeverity(str, Enum):
    """Severity classification for negative keywords."""
    HARD = "hard"  # Auto-reject, write to suppression
    SOFT = "soft"  # Throttle if low score, recoverable

@dataclass
class NegativeKeywordEntry:
    keyword: str
    weight: float
    category: NegativeKeywordCategory
    severity: NegativeKeywordSeverity = NegativeKeywordSeverity.SOFT  # Default safe

HARD_NEGATIVE_KEYWORDS = {
    # Unambiguous B2B
    "b2b", "enterprise saas", "developer tool", "devops", "sdk",
    # Crypto
    "blockchain", "crypto", "cryptocurrency", "web3", "nft", "defi",
    # Late stage
    "series c", "series d", "series e",
}

def is_hard_negative(keyword: str) -> bool:
    """Check if keyword triggers hard rejection."""
    return keyword.lower().strip() in HARD_NEGATIVE_KEYWORDS
```

### Task 2.2: Add HELD_SOFT to RoutingDecision Enum

**File:** `utils/thesis_filter.py`

```python
class RoutingDecision(str, Enum):
    QUALIFIED = "qualified"
    HELD = "held"
    HELD_SOFT = "held_soft"  # NEW: Soft negative, recoverable
    REJECTED = "rejected"
    SKIP = "skip"
```

### Task 2.3: Create Single Rejection Handler

**File:** `utils/negative_keyword_policy.py` (extend existing)

```python
def handle_rejection(
    signal_id: str,
    routing: RoutingDecision,
    rejection_type: Literal["hard", "soft", "none"],
    reason: str,
    signal_store: SignalStore,
) -> None:
    """Single point of control for ALL rejection handling.

    Ensures consistent suppression behavior across all 5 call sites.
    """
    if rejection_type == "hard":
        signal_store.mark_rejected(signal_id, reason)
        signal_store.add_to_suppression_cache(signal_id)
    elif rejection_type == "soft":
        # Recoverable: mark as held with soft-reject metadata
        signal_store.mark_held(
            signal_id,
            held_reason=f"soft_negative:{reason}",
            increment_soft_reject_count=True,
        )
        # NO suppression cache write
    # rejection_type == "none": no action needed
```

### Task 2.4: Update ThesisFilter Routing Logic

**File:** `utils/thesis_filter.py` (lines 168-169, 225-226)

```python
# BEFORE:
if keyword_fit.negative_keywords:
    routing = RoutingDecision.REJECTED

# AFTER:
from utils.negative_keyword_policy import is_hard_negative

hard_matches = [kw for kw in keyword_fit.negative_keywords if is_hard_negative(kw)]
soft_matches = [kw for kw in keyword_fit.negative_keywords if not is_hard_negative(kw)]

if hard_matches:
    routing = RoutingDecision.REJECTED
    rejection_type = "hard"
elif soft_matches and keyword_fit.score < self.hold_threshold:
    routing = RoutingDecision.HELD_SOFT
    rejection_type = "soft"
else:
    # Soft matches with high score: proceed normally
    rejection_type = "none"
```

### Task 2.5: Update All 5 mark_rejected() Call Sites

Replace direct calls with `handle_rejection()`:

| File | Line | Change |
|------|------|--------|
| `workflows/pipeline.py` | ~1580 | Use `handle_rejection()` |
| `workflows/notion_pusher.py` | ~TBD | Use `handle_rejection()` |
| `collectors/curated_scout.py` | ~TBD | Use `handle_rejection()` |
| `consumer/consumer_pipeline.py` | ~TBD | Use `handle_rejection()` |
| `consumer/pusher.py` | ~TBD | Use `handle_rejection()` |

### Task 2.6: Add Status Override with Freshness Check

**File:** `utils/thesis_filter.py`

```python
from datetime import datetime, timedelta

NON_DROPPABLE_STATUSES = {"Funded", "Dilligence", "Initial Meeting / Call"}
STATUS_FRESHNESS_HOURS = 48

async def classify(self, ...):
    # Gate A: Status override
    if signal.status in NON_DROPPABLE_STATUSES:
        if signal.status_updated_at and (
            datetime.now() - signal.status_updated_at < timedelta(hours=STATUS_FRESHNESS_HOURS)
        ):
            return ClassificationResult(
                routing=RoutingDecision.QUALIFIED,
                reason="STATUS_OVERRIDE",
                # Still compute negatives for observability
                negative_keywords=keyword_fit.negative_keywords,
            )
    # Continue to Gate B, C, D...
```

### Task 2.7: Add Feature Flag

**File:** `config/settings.py` or environment variable

```python
USE_HARD_SOFT_NEGATIVES = os.getenv("USE_HARD_SOFT_NEGATIVES", "false").lower() == "true"
```

Default OFF for safety. Enable in eval/staging first.

---

## Phase 3: Targeted Vocabulary Expansion

### Task 3.1: Add Keywords for 19 Zero-Score Companies

**File:** `utils/thesis_matcher.py` lines 62-155

Only add keywords that map to specific confirmed misses:

**CONSUMER_HEALTH_TECH:**
```python
"fertility": 0.5,        # Cofertility
"egg freezing": 0.7,     # Cofertility
"maternal": 0.5,         # SimpliFed
"breastfeeding": 0.6,    # SimpliFed
"pcos": 0.6,             # May Health
"longevity": 0.5,        # Healthspan AI, Medo Health
"respiratory": 0.4,      # Kivo Health
"asthma": 0.5,           # BioProxal
"sexual health": 0.6,    # TBD Health
"sti": 0.5,              # TBD Health
"wellbeing": 0.4,        # Reulay
"glp1": 0.6,             # Phyteau
```

**CONSUMER_CPG:**
```python
"suncare": 0.6,          # Bluu Suncare
"sunscreen": 0.5,        # Bluu Suncare
```

**TRAVEL_HOSPITALITY:**
```python
"luxury travel": 0.7,    # FlyFlat, Voymond
"concierge": 0.5,        # FlyFlat
"rebooking": 0.6,        # Repriced Ai
"flights": 0.4,          # Repriced Ai
```

**CONSUMER_MARKETPLACE:**
```python
"creator": 0.4,          # MintStars
"home design": 0.5,      # Palazzo
"homebuyer": 0.5,        # Keybacker
"2-sided": 0.5,          # Palazzo
```

---

## Phase 4: Migration + Final Verification

### Task 4.1: Migrate Existing False Negatives

The 39 confirmed consumer companies are currently in REJECTED state. Batch update:

```python
# One-time migration script
from storage.signal_store import SignalStore

CONFIRMED_CONSUMER_IDS = [...]  # Load from confirmed_consumer.txt

store = SignalStore()
for signal_id in CONFIRMED_CONSUMER_IDS:
    store.mark_pending(signal_id, reason="MIGRATION_RECALL_FIX")
    store.remove_from_suppression_cache(signal_id)
```

### Task 4.2: Run Final Evaluation

```bash
python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_results_v2.jsonl
```

**Targets:**
- Recall: 50%+ (from 4.1%)
- Precision: 60%+ (from 81%, acceptable trade-off)

### Task 4.3: Spot-Check for B2B Leakage

Review any new false positives for B2B terms that should be HARD negatives.

---

## Test Plan (Minimal but High Coverage)

| Test | Validates |
|------|-----------|
| `test_status_override_never_rejects` | Gate A: In-flight deals protected |
| `test_hard_negative_triggers_reject` | Gate B: Hard negatives auto-reject |
| `test_soft_negative_low_score_held_soft` | Gate C: Soft + low → HELD_SOFT |
| `test_soft_negative_high_score_qualified` | Gate C: Soft + high → proceeds |
| `test_zero_score_held_triage` | Gate D: Zero-score → HELD |
| `test_unknown_thesis_uses_default_cap` | Scoring: No unknown-thesis trap |
| `test_saturation_invariance` | Scoring: Adding keywords doesn't change prior scores |
| `test_soft_reject_escalation` | After 3 soft-rejects → hard reject |

---

## Verification Commands

```bash
# Phase 1 verification
python -c "from utils.thesis_matcher import ThesisMatcher; m = ThesisMatcher(); print(m._normalize('api-first platform'))"

# Phase 2 verification
python -c "
from utils.negative_keyword_policy import is_hard_negative
assert is_hard_negative('blockchain') == True
assert is_hard_negative('platform') == False
print('Hard/soft classification working')
"

# Full test suite
python -m pytest tests/utils/test_thesis_filter.py -v

# Final evaluation
python scripts/thesis_eval.py --ground-truth ground_truth.jsonl --out eval_results_v2.jsonl
```

---

## Files to Modify (Summary)

| File | Changes |
|------|---------|
| `utils/negative_keyword_policy.py` | Add NegativeKeywordSeverity, HARD_NEGATIVE_KEYWORDS, is_hard_negative(), handle_rejection() |
| `utils/thesis_filter.py` | Add HELD_SOFT enum, status override with freshness, hard/soft routing logic |
| `utils/thesis_matcher.py` | Normalization (lines 646-647), saturation caps, keywords |
| `workflows/pipeline.py` | Replace mark_rejected() with handle_rejection() |
| `workflows/notion_pusher.py` | Replace mark_rejected() with handle_rejection() |
| `collectors/curated_scout.py` | Replace mark_rejected() with handle_rejection() |
| `consumer/consumer_pipeline.py` | Replace mark_rejected() with handle_rejection() |
| `consumer/pusher.py` | Replace mark_rejected() with handle_rejection() |
| `tests/utils/test_thesis_filter.py` | Add parameterized hard/soft tests |
| `config/settings.py` | Add USE_HARD_SOFT_NEGATIVES feature flag |
