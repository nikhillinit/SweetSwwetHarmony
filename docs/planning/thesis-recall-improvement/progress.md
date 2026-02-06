# Progress Log: Forensic Engineer Validation

## Session: 2026-02-04

### Context Session
- Memory Keeper Session ID: bcd926c7-ce21-4041-9e2e-8bee65f90660
- Channel: forensic-engineer-va
- Project: C:\dev\Harmonic

### Starting State
- Plan v4 written to: `~/.claude/plans/reactive-cooking-dusk.md`
- Findings from 3 review agents documented in: `docs/planning/thesis-recall-improvement/findings.md`
- Checkpoint: hard-soft-strategy-v4-reviewed

---

## Forensic Phase 1: ANALYZE

### 1.1 ThesisFilterResult (thesis_filter.py:46-66)
**CONFIRMED:** `rejection_type` field is MISSING
- Has `rejection_reason: Optional[str] = None` (line 59)
- Does NOT have `rejection_type`
- Plan correctly identifies this as needing addition

### 1.2 negative_keyword_policy.py
**CONFIRMED:** `is_hard_negative()` DOES NOT EXIST
- File has 206 lines
- Contains: NegativeKeywordCategory enum, NegativeKeywordPolicy class, validate_negative_keyword_policy()
- No normalization helper, no hard/soft classification function

### 1.3 Normalization Helper
**UPDATED:** `_norm_kw()` DOES NOT EXIST in negative_keyword_policy.py
- thesis_matcher.py `_normalize()` (lines 646-649) ALREADY handles `-/_` via regex
- But negative_keyword_policy.py has NO normalization at all
- Plan adds `_norm_kw()` for keyword index lookup - this is CORRECT

### 1.4 Thesis Filter Routing Logic
**CONFIRMED:** Hard-reject on ANY negative keyword
- Line 168-169: `if keyword_fit.negative_keywords: routing = RoutingDecision.REJECTED`
- Line 225-226: Same pattern in fallback branch
- This is the ROOT CAUSE of 4% recall

### 1.5 Pipeline Rejection Block
**CONFIRMED:** Line 1575-1578
```python
await self._store.mark_rejected(
    sig.id,
    f"Thesis rejected: negative keywords {thesis_result.negative_keywords}",
)
```
- No differentiation between hard/soft
- All rejections go to suppression cache

### 1.6 update_signal_status() Signature
**CONFIRMED:** Lines 2626-2631 in signal_store.py
```python
async def update_signal_status(
    self,
    canonical_key: str,  # NOT signal_id
    status: str,
    error_message: Optional[str] = None,
) -> bool:
```
- Takes `canonical_key`, not `signal_id`
- Plan v4 correctly uses canonical_key

### 1.7 YAML Policy
**CONFIRMED:** `config/v2/negative_keyword_policy.yaml` exists
- 40 keywords across 6 categories
- B2B_ENTERPRISE: 12 keywords (enterprise, b2b, saas platform, developer tool, api platform, api management, devops, infrastructure, logistics platform, logistics, data platform, sdk)
- CRYPTO_WEB3: 6 keywords (blockchain, crypto, web3, nft, defi, token)
- SERVICES: 3 keywords (consulting, agency, services firm)
- STAGES: 4 keywords (series b, series c, series d, aggregator)
- EDUCATIONAL: 10 keywords (boilerplate, starter, template, tutorial, workshop, course, homework, assignment, example, demo repo)
- DEVTOOLS: 5 keywords (cli, library, framework, plugin, linter)

**IMPORTANT OBSERVATIONS:**
- "series b" has weight 0.3 in STAGES - confirms need to keep STAGES soft
- "devops" and "sdk" are in B2B_ENTERPRISE (not DEVTOOLS)
- "library", "framework", "plugin" are in DEVTOOLS - plan correctly removes these from hard overrides

---

## Discrepancies Found

| Item | Plan Assumption | Actual | Severity |
|------|----------------|--------|----------|
| rejection_type | Missing | CONFIRMED missing | OK |
| is_hard_negative | Missing | CONFIRMED missing | OK |
| _norm_kw | Missing | CONFIRMED missing | OK |
| Hard-reject logic | Lines 168-169, 225-226 | CONFIRMED | OK |
| update_signal_status | Uses canonical_key | CONFIRMED | OK |

---

## Next Steps
1. Verify YAML policy file location and contents
2. Complete ANALYZE phase checklist
3. Move to PLAN phase refinement
