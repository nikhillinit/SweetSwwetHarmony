# Context Analysis: Thesis Matcher Recall

## Ground Truth Findings (from Phase 0C)

### Dataset
- Total companies evaluated: 575
- POSITIVE labels: 413
- NEGATIVE labels: 162
- Human-verified (Funded/Meeting/Dilligence): 60

### Current Performance
| Metric | Value |
|--------|-------|
| Recall | 4% (16/413) |
| Precision | 81% |
| F1 | 0.07 |

### Confirmed Consumer Companies Being Missed (39)
See `confirmed_consumer_full.txt` for full descriptions.

**Score=0.0 (19 companies):** Need keyword expansion
- Cofertility, SimpliFed, May Health, Liti, Reulay, TBD Health
- Healthspan AI, Kivo Health, Medo Health, BioProxal, Wearlinq, Phyteau
- Bluu Suncare, FlyFlat, Repriced Ai, MintStars, Palazzo, Keybacker

**Near-threshold (16 companies):** Recoverable by threshold or keyword boost
- Score 0.32-0.38: EatWell, Augene Beauty, Voymond, Skylark, Worldia, OffWeGo
- Score 0.19-0.26: Multiple health tech companies

**Already passing (4 companies):**
- Joystik Life (0.58), Impulse (0.48), Sol Health (0.43), Elemind (0.43)

## Codebase State (Verified)

### ThesisFilter (utils/thesis_filter.py)
- **Lines 168-169:** Hard-reject on ANY negative keyword
- **Lines 225-226:** Same pattern in fallback path
- **Config:** hold_threshold=0.3, skip_llm_if_keyword_below=0.2

### ThesisMatcher (utils/thesis_matcher.py)
- **Lines 646-647:** Normalization is `text.lower().strip()` only
- **Line 667:** Scoring formula `total_weight / (max_possible * 0.15)`
- **Line 656:** `max_possible = sum(keywords.values())` - dilution risk

## Constraints
- Must not break existing pipeline behavior
- Must maintain precision above 55-60%
- Changes should be reversible via feature flags
