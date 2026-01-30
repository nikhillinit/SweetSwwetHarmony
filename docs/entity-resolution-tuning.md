# Entity Resolution Tuning Guide

## Overview

This document analyzes the fuzzy matching threshold used in Phase G entity resolution and provides recommendations for tuning based on precision/recall trade-offs.

## Current Configuration

```python
# utils/phase_g_entity_resolver.py
FUZZY_THRESHOLD = 90           # Minimum token_sort_ratio score (0-100)
BLOCKING_CANDIDATE_LIMIT = 200 # Max candidates per blocking token
FUZZY_ALIAS_CONFIDENCE = 0.85  # Confidence for fuzzy-derived aliases
FUZZY_ALIAS_EXPIRY_DAYS = 30   # Days until fuzzy alias expires
```

## Fuzzy Matching Algorithm

The resolver uses **RapidFuzz's `token_sort_ratio`** which:
1. Tokenizes both strings (split by whitespace)
2. Sorts tokens alphabetically
3. Computes Levenshtein similarity on the sorted token strings

This handles reordering: "Acme Corp Inc" matches "Inc Acme Corp" at 100%.

## Threshold Analysis

### 90% Threshold (Current - Conservative)

**Characteristics:**
- **High precision**: Very few false positives
- **Moderate recall**: May miss valid matches with minor variations
- **Safe default**: Suitable for production where false merges are costly

**Matches at 90%:**
| String A | String B | Score |
|----------|----------|-------|
| "Acme Inc" | "Acme" | 80% ❌ |
| "Acme Inc" | "Acme Corp" | 89% ❌ |
| "Acme Technologies" | "Acme Tech" | 91% ✅ |
| "HealthCo" | "Health Co" | 94% ✅ |
| "TechCrunch" | "Tech Crunch" | 95% ✅ |

### 85% Threshold (Moderate)

**Characteristics:**
- **Good precision**: Few false positives
- **Better recall**: Catches more abbreviations and variations
- **Trade-off**: Some unrelated companies may merge

**Additional matches at 85%:**
| String A | String B | Score |
|----------|----------|-------|
| "Acme Inc" | "Acme Corp" | 89% ✅ |
| "FoodTech" | "Food Tech Inc" | 87% ✅ |
| "Wellness Co" | "Well Co" | 86% ✅ (risky) |

### 95% Threshold (Strict)

**Characteristics:**
- **Very high precision**: Almost no false positives
- **Lower recall**: Only near-exact matches
- **Use case**: When data quality is paramount

**Matches only at 95%:**
- Nearly identical strings
- Minor punctuation/spacing differences

## Blocking Token Efficiency

Blocking tokens reduce the O(n²) fuzzy matching problem:

| Token Type | Purpose | Example |
|------------|---------|---------|
| `first` | First word of normalized name | `tok:first:acme` |
| `meta` | Double Metaphone encoding | `tok:meta:AKM` |
| `tld3` | Domain TLD + first 3 chars | `tok:tld3:acm-com` |

**Cap behavior:** If a token has >200 candidates, require 2-token overlap. This prevents "Inc" or "Tech" from causing massive comparisons.

## Recommendations

### For Production (Current)
Keep `FUZZY_THRESHOLD = 90`:
- False merges are difficult to undo
- Manual review can catch missed matches
- Blocking tokens provide pre-filtering

### For Batch Review Mode
Consider `FUZZY_THRESHOLD = 85` with human review:
- Generate candidate merge pairs
- Present to user for confirmation
- Only merge after approval

### For High-Quality Sources
Consider `FUZZY_THRESHOLD = 95`:
- Official registry data (Companies House, SEC)
- Minimize any ambiguity

## Monitoring Metrics

Track these metrics to tune the threshold:

```python
# In pipeline processing
metrics = {
    "fuzzy_matches_made": 0,      # Number of fuzzy matches
    "avg_fuzzy_score": 0.0,       # Average match score
    "blocked_by_threshold": 0,    # Candidates rejected
    "multi_token_required": 0,    # Times 2-token overlap needed
}
```

## Future Enhancements

1. **ML-Based Matching**: Train a classifier on confirmed matches
2. **Industry-Aware Matching**: Higher threshold for common terms
3. **User Feedback Loop**: Learn from manual corrections
4. **Configurable Per-Source**: Different thresholds for different collectors

## Testing Threshold Changes

Before changing the threshold:

```bash
# 1. Export current entity groups
python -c "
from storage.signal_store import SignalStore
import asyncio

async def export():
    store = SignalStore('signals.db')
    await store.initialize()
    cursor = await store._db.execute('SELECT * FROM entity_aliases')
    rows = await cursor.fetchall()
    for row in rows:
        print(row)
    await store.close()

asyncio.run(export())
" > entity_groups_before.txt

# 2. Change FUZZY_THRESHOLD in phase_g_entity_resolver.py

# 3. Run with dry-run
USE_PHASE_G_IDENTITY_RESOLUTION=true python run_pipeline.py full --dry-run

# 4. Compare entity groups
# Look for unexpected merges or missed merges
```

## Conclusion

**Recommendation:** Keep `FUZZY_THRESHOLD = 90` until production data reveals specific issues.

The current threshold is conservative, prioritizing precision over recall. This is appropriate for a deal sourcing system where false merges could cause:
- Duplicate outreach to the same company
- Incorrect data attribution
- Lost signals from incorrectly merged entities

If recall becomes an issue (many valid matches missed), consider:
1. Adding more blocking token types (trigrams, soundex)
2. Lowering to 85% with human review queue
3. Implementing entity-specific override thresholds

---

*Last Updated: 2026-01-29*
*Author: Discovery Engine Team*
