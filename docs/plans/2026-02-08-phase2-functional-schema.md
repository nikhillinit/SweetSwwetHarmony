# Phase 2: Functional Schema + Web3 Co-occurrence + Intelligence Visibility

**Status:** PLANNING (v4 — final amendments after second review)
**Created:** 2026-02-08
**Revised:** 2026-02-09
**Depends on:** Phase 1a (PR #29+#30), Phase 1b (PR #31)
**Estimated:** 14-18 hours (10 tasks)
**Branch:** `feature/phase2-functional-schema`
**Reviews:** `docs/plans/phase2-review-v3.md`, `docs/plans/phase2-review-v4.md`

---

## Goal

Add functional schema extraction, Web3 co-occurrence exclusion, and surface all intelligence in CSV/CLI immediately — so every pipeline run produces richer, more actionable output without waiting for a dashboard.

---

## Plan Invariants

> These assumptions bound the design. Violating any requires revisiting affected tasks.

1. **Extract-once per company.** Phase 2 extracts a schema on first encounter. Re-extraction on staleness or new evidence is Phase 3+ scope.
2. **Sequential processing per company_id.** Signal processing iterates company groups sequentially (`for canonical_key in groups`). Collectors run in parallel, but schema writes occur during sequential processing. No concurrent writes to the same `company_id`.
3. **Schema refresh is Phase 3+ scope.** `update_schema_on_new_evidence()`, conditional refresh triggers, and age-based re-extraction are explicitly deferred. Phase 2 creates immutable schema version rows.
4. **Production path is `utils/`.** The production pipeline uses `utils.thesis_filter.ThesisFilter` → `utils.thesis_matcher.ThesisMatcher`. The `consumer/thesis_filter/` package is supplementary (quality ops, tests). Production wiring is primary; consumer parity is optional.

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Schema storage | Versioned (history + active flag) | Companies evolve; need audit trail |
| Schema extraction | LLM-based via Gemini (reuse existing classifier infra) | Already have rate limiter, circuit breaker, API key |
| Schema extraction scope | Extract-once per company (Phase 2) | Re-extraction deferred to Phase 3 to keep scope bounded |
| Schema confidence gating | Advisory-only below 0.6 threshold | Low-confidence schemas inform but don't affect routing |
| Web3 detection | Co-occurrence window + rescue phrases | Prevents "access tokens" and "DAO pattern" false positives |
| Web3 module location | `utils/web3_detector.py` | Primary consumer is `utils/thesis_filter.py`; flat `utils/` matches codebase convention |
| Web3 production integration | Pre-check in `ThesisFilter.classify()` | Production path (`utils/thesis_filter.py`); consumer parity optional |
| LLM adjacent categories | Extend existing system prompt | Minimal change, high impact |
| CSV/CLI visibility | Extend existing export + triage commands | No new commands, just richer output |
| Advisory visibility | Always show, always flag (unified) | Consistent across CSV and triage CLI |
| Pipeline hook | Post-thesis-filter, pre-routing | Schema extraction is advisory, runs after pass/fail decided |

---

## Task Breakdown

### Task 2.1: Create `functional_schemas` table (v32 migration)
**File:** `storage/migrations/v32_functional_schema.py`
**Est:** 1.5h

```sql
CREATE TABLE IF NOT EXISTS functional_schemas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    problem_solved_text TEXT,
    customer_text TEXT,
    approach_text TEXT,
    customer_archetype TEXT,
    problem_archetypes TEXT,       -- JSON array
    schema_confidence REAL,
    is_advisory BOOLEAN NOT NULL DEFAULT 0,  -- True if confidence below threshold
    evidence_signal_ids TEXT,      -- JSON array of signal IDs that contributed
    extraction_model TEXT,         -- e.g. "gemini-2.0-flash"
    extraction_prompt_version TEXT,-- e.g. "v1.0.0-func-schema"
    is_active BOOLEAN NOT NULL DEFAULT 1,
    superseded_by INTEGER REFERENCES functional_schemas(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(company_id, schema_version)
);
CREATE INDEX IF NOT EXISTS idx_fs_company_active ON functional_schemas(company_id, is_active);
CREATE INDEX IF NOT EXISTS idx_fs_archetype ON functional_schemas(customer_archetype) WHERE is_active = 1;
```

**Rollback (execute in order if needed):**
```sql
DROP INDEX IF EXISTS idx_fs_archetype;
DROP INDEX IF EXISTS idx_fs_company_active;
DROP TABLE IF EXISTS functional_schemas;
-- Then reset: UPDATE schema_version SET version = 31 WHERE version = 32;
```

**Also:** Bump `CURRENT_SCHEMA_VERSION` to 32 in `signal_store.py` and register migration.

**Tests:**
- Migration applies cleanly on fresh DB
- Migration applies on v31 DB
- Schema table exists with correct columns (including `is_advisory`)
- Unique constraint enforced on (company_id, schema_version)
- Index created

---

### Task 2.2: Implement functional schema extractor
**File:** `consumer/functional_extractor.py` (NEW)
**Est:** 3h

Core class: `FunctionalExtractor`
- Reuses `LLMClassifier` infrastructure (Gemini client, rate limiter, circuit breaker)
- Extracts: problem_solved_text, customer_text, approach_text, customer_archetype, problem_archetypes
- Prompt asks LLM to decompose signal into functional schema
- Returns `FunctionalSchema` dataclass with confidence score

**Prompt design:**
```
Given this company signal, extract the functional schema:
1. Problem Solved: What customer problem does this company solve? (1 sentence)
2. Customer: Who is the target customer? (specific persona)
3. Approach: How do they solve it? (1 sentence)
4. Customer Archetype: One of: [creators, parents, fitness_enthusiasts, travelers, foodies, beauty_consumers, pet_owners, students, gamers, shoppers, patients, general_consumer, unknown]
5. Problem Archetypes: Array of: [content_monetization, meal_delivery, fitness_tracking, beauty_personalization, travel_booking, health_monitoring, marketplace, subscription, social_commerce, creator_economy, wellness, mental_health, other]
6. Confidence: How confident are you in this extraction? (0.0 = guessing from minimal info, 1.0 = clear and unambiguous). Output as a number.

Output JSON only. Example:
{"problem_solved": "...", "customer": "...", "approach": "...", "customer_archetype": "foodies", "problem_archetypes": ["meal_delivery", "subscription"], "schema_confidence": 0.85}
```

**Key behaviors:**
- If LLM unavailable → return None (graceful degradation)
- If response malformed → return None with warning log
- Confidence score derived from LLM's self-assessment
- Prompt version tracked for reproducibility

**Tests:**
- Successful extraction with mock LLM response
- Graceful fallback when LLM unavailable
- Malformed response handling
- Confidence score bounds [0.0, 1.0]
- Prompt version tracked in output

---

### Task 2.3: Schema confidence gating
**File:** `consumer/functional_extractor.py` (same file)
**Est:** 1h

Add confidence-based gating:
- `FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD` env var (default: 0.6)
- Below threshold: schema stored with `is_advisory = True` (still stored, but marked low-confidence)
- Above threshold: schema stored with `is_advisory = False`
- **Unified visibility policy:** Advisory schemas are always shown in CSV and triage CLI, always flagged with a marker (`*` suffix on archetype or `[advisory]` label). Verbose mode shows full evidence details, not a different filter.

**Tests:**
- High confidence schema → stored with `is_advisory = False`
- Low confidence schema → stored with `is_advisory = True`
- Threshold configurable via env var
- Advisory schemas appear in default queries with advisory marker

---

### Task 2.4: Wire schema extraction into pipeline
**File:** `workflows/pipeline.py` (modify)
**Est:** 2h

Hook point: After thesis filter classification, before routing decision.

```python
# In _process_signals_stage or equivalent:
# After thesis_filter.classify() completes successfully:
if schema_extractor and thesis_result.routing != "REJECT":
    try:
        schema = await schema_extractor.extract(signal_data, company_id)
        if schema:
            await store.save_functional_schema(schema)
    except Exception as e:
        logger.warning(f"Schema extraction failed (non-fatal): {e}")
```

**Key behaviors:**
- Schema extraction is **optional** (env var `ENABLE_FUNCTIONAL_SCHEMA`, default: `false`)
- Schema extraction failure is **non-fatal** (warning log, pipeline continues)
- Only extract for signals that pass thesis filter (don't waste LLM calls on rejects)
- Skip extraction if schema already exists for this company_id (avoid redundant LLM calls)

**Signal selection for extraction:** When a company group contains multiple passing signals, use the signal with the highest confidence score for schema extraction. If tied, prefer `sec_edgar` > `job_postings` > `news_api` > other sources. This is implemented as a sort before the extraction call within the company group loop, not a separate selection step.

**Tests:**
- Pipeline runs with schema extraction enabled (mock LLM)
- Pipeline runs with schema extraction disabled
- Schema extraction failure doesn't break pipeline
- Schema not extracted for rejected signals
- Schema skipped if company already has active schema
- Multi-signal company group: highest-confidence signal selected for extraction

---

### Task 2.5: Web3 co-occurrence detector
**File:** `utils/web3_detector.py` (NEW — flat in `utils/`, matching `utils/thesis_matcher.py` convention)
**Est:** 3h

**Import constraint:** `utils/web3_detector.py` must remain self-contained. No imports from `consumer/`, `workflows/`, or `storage/`. Only stdlib (`os`, `re`, `typing`).

Replaces simple keyword matching with context-aware co-occurrence + rescue phrases:

```python
class Web3Detector:
    """Deterministic Web3/crypto co-occurrence detector.

    Instead of flagging any mention of "token", "dao", etc.,
    checks if these ambiguous terms appear NEAR crypto-specific context.

    NOTE: dao, mining, metaverse are AMBIGUOUS (co-occurrence required),
    not unambiguous. This is intentional — "DAO pattern", "data mining",
    and "metaverse experiences" are legitimate non-crypto uses.
    """

    # Always crypto (no co-occurrence needed)
    UNAMBIGUOUS_CRYPTO = {
        "blockchain", "cryptocurrency", "bitcoin", "btc",
        "ethereum", "eth", "solana", "nft", "defi",
        "tokenomics", "smart contract", "dapp",
        "play to earn", "p2e", "yield farming",
        "metamask", "opensea", "staking",
        "web3",  # Thesis excludes Web3 categorically
    }

    # Ambiguous — only crypto if near CRYPTO_CONTEXT terms
    # (or if not neutralized by a rescue phrase)
    AMBIGUOUS_TERMS = {
        "token", "tokens",
        "dao",
        "wallet",
        "mining",
        "metaverse",
    }

    # Rescue phrases: when an ambiguous term appears inside one of
    # these phrases, that specific occurrence is neutralized.
    # Matched with \b word boundaries; supports pluralization.
    RESCUE_PHRASES = {
        "token":  ["access token", "access tokens", "auth token", "bearer token",
                    "session token", "refresh token", "api token", "loyalty token",
                    "loyalty tokens", "token ring", "tokenization"],
        "dao":    ["dao pattern", "data access object"],
        "mining": ["data mining", "mining insights", "process mining", "text mining"],
        "wallet": ["digital wallet", "mobile wallet", "e-wallet", "ewallet"],
    }

    # Context terms that make ambiguous terms crypto.
    # NOTE: Use specific phrases to avoid false positives
    # ("proof of concept", "mint condition" are non-crypto).
    CRYPTO_CONTEXT = {
        "blockchain", "ethereum", "solana", "crypto",
        "nft", "defi", "decentralized", "on-chain",
        "smart contract", "ledger", "consensus",
        "proof of work", "proof of stake",
        "gas fee", "mint nft", "minting tokens",
    }

    COOCCURRENCE_WINDOW = int(os.environ.get("WEB3_COOCCURRENCE_WINDOW", "100"))
```

**Decision algorithm (5 steps, in order):**
1. **Unambiguous crypto terms** (global scan) → immediate REJECT. Cannot be overridden by rescue phrases.
2. **Identify ambiguous term occurrences** — find all positions of AMBIGUOUS_TERMS in text.
3. **Apply local rescue** — for each ambiguous occurrence, check if it falls within a RESCUE_PHRASES span. If so, neutralize that specific occurrence only (not a global PASS).
4. **Co-occurrence check** — for remaining (non-rescued) ambiguous occurrences, scan ±WINDOW chars for CRYPTO_CONTEXT terms. If found → REJECT.
5. **Otherwise → PASS.**

Key: rescue phrases are **local** (neutralize one occurrence) not **global** (suppress all detection). Unambiguous terms always win regardless of rescue phrases.

**Rescue phrase matching:** Use `\b` word boundaries. Support pluralization (`token`/`tokens`). Hyphenated forms (`access-token`) handled by normalizing hyphens to spaces **during rescue phrase matching only** (not applied to the full input text, to avoid mangling "co-op", "well-being", etc.).

**Tests:**
- "blockchain startup" → REJECT (unambiguous)
- "OAuth access tokens" → PASS (rescue phrase neutralizes "tokens")
- "access token" → PASS (rescue phrase)
- "access-token" → PASS (hyphenated rescue phrase)
- "DAO pattern in code" → PASS (rescue phrase neutralizes "dao")
- "DAO governance token" → REJECT (no rescue + co-occurrence)
- "data mining for consumer insights" → PASS (rescue phrase)
- "mining Bitcoin" → REJECT (co-occurrence with unambiguous "bitcoin")
- "token on ethereum blockchain" → REJECT (ambiguous + co-occurrence)
- "loyalty tokens for customers" → PASS (rescue phrase)
- "decentralized token exchange" → REJECT (co-occurrence with "decentralized")
- "crypto wallet" → REJECT (unambiguous "crypto")
- "digital wallet for payments" → PASS (rescue phrase, no crypto context)
- "blockchain cryptography library" → REJECT (unambiguous "blockchain" overrides everything)
- "bitcoin access token" → REJECT (unambiguous "bitcoin" overrides rescue)
- "access token for ethereum wallet" → REJECT (unambiguous "ethereum" overrides rescue)
- Window boundary test (term at edge of text)
- "tokenization" → does not trigger token rules (rescue phrase handles it)
- "web3 marketplace for food delivery" → REJECT (unambiguous, thesis excludes Web3)
- "web 3.0 era consumer app" → PASS (`"web 3.0"` ≠ `"web3"`, word-boundary)
- "proof of concept token launch for loyalty" → requires actual crypto context to reject
- Empty string input → PASS (defensive guard)
- None input → PASS (defensive guard)

---

### Task 2.6: Integrate Web3 detector into production + consumer paths
**Est:** 1.5h

#### Primary: Production path (`utils/thesis_filter.py`)

Add a Web3 pre-check in `ThesisFilter.classify()` before `ThesisMatcher.score()`:

```python
# In ThesisFilter.classify(), before Stage 1 keyword matching:
from utils.web3_detector import Web3Detector

# In __init__:
self._web3_detector = Web3Detector()

# In classify():
web3_result = self._web3_detector.detect(text)
if web3_result.is_crypto:
    return ThesisFilterResult(
        routing=RoutingDecision.REJECTED,
        rejection_reason=web3_result.reason,
        negative_keywords=[web3_result.matched_term],
    )

# Then proceed to existing Stage 1 (keyword) + Stage 2 (LLM) as before
```

**Key behaviors:**
- Web3 check runs **before** keyword scoring (no wasted computation on crypto signals)
- Same `ThesisFilterResult` output shape — downstream pipeline code unchanged (verified: `rejection_reason` + `negative_keywords` fields exist at `utils/thesis_filter.py:53,59`)
- `ThesisMatcher.NEGATIVE_KEYWORDS` retains crypto entries for soft penalty scoring (backward compat)
- Rejection reason includes the matched term and co-occurrence details for operator debugging

#### Optional parity: Consumer path (`consumer/thesis_filter/hard_disqualifiers.py`)

Replace `is_crypto()` with `Web3Detector.detect()` in `HardDisqualifiers.check()`. This keeps the consumer filter consistent with production, but is not the primary deliverable.

**Tests:**
- **Production integration:** full pipeline flow via `workflows/pipeline.py` asserts:
  - "blockchain startup" → REJECTED with crypto reason
  - "OAuth access token startup" → NOT rejected for crypto
  - Rejection reason string is stable for operators
- All existing ThesisFilter tests still pass
- All existing hard_disqualifiers tests still pass (if consumer parity applied)
- New tests for co-occurrence edge cases via both paths

---

### Task 2.7: Add adjacent categories to LLM prompt
**File:** `consumer/thesis_filter/llm_classifier.py` (modify)
**Est:** 1h

Extend `CLASSIFIER_SYSTEM_PROMPT` to include adjacent/edge categories:

```
## Adjacent Categories (Edge Cases — Evaluate Carefully)
These categories are SOMETIMES in thesis depending on execution:
- Creator Economy: In thesis if consumer-facing (e.g., creator monetization tools for individual creators). Out of thesis if B2B SaaS for brands.
- Pet Tech: In thesis if consumer product (pet food DTC, pet health app). Out of thesis if B2B vet software.
- EdTech: In thesis if consumer learning app (language learning, tutoring marketplace). Out of thesis if enterprise LMS.
- FinTech: In thesis if consumer financial wellness (budgeting app, savings). Out of thesis if B2B payments infra.
- FoodTech: In thesis if consumer-facing (meal kit, restaurant, food delivery). Out of thesis if B2B food supply chain.

When classifying edge cases, ask: "Is the END USER an individual consumer making a personal purchase decision?"
```

Also bump `CLASSIFIER_PROMPT_VERSION` to `"v1.4.0-gemini-adjacent"`.

**Tests:**
- Prompt version updated
- System prompt contains "Adjacent Categories" section
- Edge case signal classified correctly (mock LLM response)

---

### Task 2.8: Schema storage methods (extract-once)
**File:** `storage/signal_store.py` (modify)
**Est:** 1.5h

Add methods to `SignalStore`:

```python
async def save_functional_schema(self, schema: dict) -> int:
    """Save a new functional schema for a company.

    Guards:
    - Verifies all signal_ids in evidence_signal_ids belong to company_id
    - Sets is_advisory based on schema_confidence vs threshold
    """

async def get_active_schema(self, company_id: str) -> Optional[dict]:
    """Get the current active schema for a company. Returns None if no schema exists."""

async def get_schema_history(self, company_id: str) -> List[dict]:
    """Get all schema versions for a company (audit trail), ordered by version."""

async def has_active_schema(self, company_id: str) -> bool:
    """Quick check if company already has an active schema (used by pipeline to skip extraction)."""
```

> **Deferred to Phase 3:** `update_schema_on_new_evidence()`, conditional refresh, and age-based re-extraction. See Plan Invariant #1 and #3.

**Key behaviors:**
- `save_functional_schema()` verifies `evidence_signal_ids` belong to the `company_id` (prevents silent data corruption)
- Sets `is_advisory` based on `schema_confidence < FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD`
- Schema rows are immutable once created (Phase 2 does not update in-place)
- `has_active_schema()` is a lightweight `EXISTS` query for pipeline skip logic

**Tests:**
- Save schema → retrieve active schema
- Save schema with advisory flag → `is_advisory = True`
- Schema history returns all versions ordered
- `has_active_schema()` returns True after save, False for unknown company
- Evidence signal_id validation: rejects signal_ids from wrong company
- Save with empty evidence_signal_ids → succeeds (evidence is optional)

---

### Task 2.9: Surface functional schema in CSV export
**File:** `run_pipeline.py` (modify `cmd_export_queue`)
**Est:** 1.5h

Extend CSV columns:
```
signal_id, company_name, canonical_key, confidence, signal_type, source_api, detected_at, status, company_id,
problem_solved, customer_archetype, schema_confidence, thesis_category, thesis_rationale
```

**Implementation:**
- LEFT JOIN `functional_schemas` on `company_id` WHERE `is_active = 1`
- LEFT JOIN `thesis_classifications` on `signal_id` for rationale
- New columns are nullable (empty string if no schema exists)

**Advisory visibility policy (unified with Task 2.10):**
- Advisory schemas (`is_advisory = 1`) are **always included** in CSV output (no filtering)
- `customer_archetype` column appends `*` suffix for advisory schemas (e.g., `creators*`)
- `schema_confidence` column shows the raw value regardless of advisory status
- No `--verbose` flag needed — all schemas visible by default, advisory flagged inline

**Tests:**
- CSV export includes new columns
- Signals without schema have empty values (not NULL text)
- Signals with schema show correct values
- Advisory schema shows `*` suffix on archetype
- Column count matches header count

---

### Task 2.10: Surface functional schema in triage CLI
**File:** `run_pipeline.py` (modify `cmd_triage_list`)
**Est:** 1.5h

Extend compact list format:
```
    ID  Company                    Problem                              Archetype     Conf  Source          Status
   --- ------------------------- -----------------------------------  ------------- ----- --------------- --------
   123  Acme Inc                  Creators monetize short-form video   creators      0.82  sec_edgar       pending
   124  Beta Corp                 [no schema]                          —             0.45  github          queued
   125  Gamma Ltd                 Healthy snack subscription           foodies*      0.52  domain_whois    pending
```

**Implementation:**
- LEFT JOIN `functional_schemas` on `company_id` WHERE `is_active = 1`
- Show `problem_solved_text` truncated to 40 chars
- Show `customer_archetype` (or "—" if no schema)

**Advisory visibility policy (unified with Task 2.9):**
- Advisory schemas (`is_advisory = 1`) are **always shown** in default triage view (no filtering)
- `customer_archetype` column appends `*` suffix for advisory schemas (e.g., `foodies*`)
- Verbose mode (`--verbose`) shows full schema details (approach_text, evidence_signal_ids, extraction_model) — not a different advisory filter

**Tests:**
- Triage list shows new columns
- Signals without schema show placeholder ("—")
- Advisory schema shows `*` suffix on archetype
- Verbose mode shows full details (approach_text, evidence)
- Column alignment maintained

---

## Dependency Graph

```
Task 2.1 (DDL) ──────────────────────────────┐
                                              ▼
Task 2.2 (Extractor) ──► Task 2.3 (Gating) ──► Task 2.4 (Pipeline wiring)
                                              │
Task 2.5 (Web3 detector) ──► Task 2.6 (production integration)
                                              │
Task 2.7 (LLM adjacent) ─────────────────────┤ (independent)
                                              │
Task 2.8 (Storage methods) ──► Task 2.9 (CSV) ──► Task 2.10 (Triage CLI)
```

**Critical path:** 2.1 → 2.2 → 2.3 → 2.4 (schema extraction chain)
**Parallel track:** 2.5 → 2.6 (Web3 detector — independent of schema)
**Independent:** 2.7 (LLM prompt update)
**Depends on 2.1+2.8:** 2.9, 2.10 (visibility — needs table + storage methods)

---

## Task Execution Order

| Order | Task | Rationale |
|-------|------|-----------|
| 1 | 2.1 | DDL first — everything else needs the table |
| 2 | 2.8 | Storage methods — needed by extractor and visibility tasks |
| 3 | 2.5 | Web3 detector — independent, can be tested standalone |
| 4 | 2.6 | Web3 integration — depends on 2.5 |
| 5 | 2.2 | Functional extractor — depends on 2.1, 2.8 |
| 6 | 2.3 | Confidence gating — extends 2.2 |
| 7 | 2.7 | LLM adjacent categories — independent, quick |
| 8 | 2.4 | Pipeline wiring — depends on 2.2, 2.3 |
| 9 | 2.9 | CSV export — depends on 2.1, 2.8 |
| 10 | 2.10 | Triage CLI — depends on 2.1, 2.8 |

---

## Environment Variables (New)

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_FUNCTIONAL_SCHEMA` | `false` | Enable schema extraction in pipeline |
| `FUNCTIONAL_SCHEMA_CONFIDENCE_THRESHOLD` | `0.6` | Below this → advisory only |
| `WEB3_COOCCURRENCE_WINDOW` | `100` | Character window (±) for ambiguous term co-occurrence scan |

---

## Success Criteria

- [ ] Functional schemas extracted with provenance + confidence + versioned history
- [ ] Schema influences explanation but not hard routing (safety)
- [ ] Web3 exclusion doesn't reject "access tokens" or "DAO pattern"
- [ ] Web3 exclusion rejects "DAO governance token" and "mining Bitcoin"
- [ ] LLM prompt includes adjacent categories (v1.4.0)
- [ ] CSV export includes functional schema columns with advisory `*` marker
- [ ] Triage CLI shows functional summary with advisory `*` marker
- [ ] Advisory visibility unified: always show, always flag (CSV and CLI consistent)
- [ ] All existing tests pass (zero regressions)
- [ ] 50+ new tests for Phase 2 code
- [ ] Performance: schema extraction < 3s per signal (LLM bound)
- [ ] Performance: CSV export < 2s for 500 signals (verified by smoke test)
- [ ] Performance smoke test passes in `tests/performance/test_phase2_slos.py`

---

## Files Created/Modified

### New Files
| File | Purpose |
|------|---------|
| `storage/migrations/v32_functional_schema.py` | DDL for functional_schemas table |
| `consumer/functional_extractor.py` | LLM-based schema extraction |
| `utils/web3_detector.py` | Co-occurrence Web3 detection (flat in `utils/`, matching codebase convention) |
| `tests/storage/test_v32_functional_schema.py` | Migration tests |
| `tests/consumer/test_functional_extractor.py` | Extractor tests |
| `tests/utils/test_web3_detector.py` | Web3 detector unit tests |
| `tests/utils/test_web3_integration.py` | Web3 production integration tests (ThesisFilter path) |
| `tests/performance/test_phase2_slos.py` | Performance smoke tests (CSV export with JOINs) |
| `tests/integration/test_phase2_intelligence.py` | End-to-end Phase 2 tests |

### Modified Files
| File | Change |
|------|--------|
| `storage/signal_store.py` | +v32 migration registration, +schema methods, bump version to 32 |
| `utils/thesis_filter.py` | Add Web3Detector pre-check in `classify()` (primary production path) |
| `consumer/thesis_filter/hard_disqualifiers.py` | (Optional parity) Replace `is_crypto()` with `Web3Detector` |
| `consumer/thesis_filter/llm_classifier.py` | Add adjacent categories to prompt, bump version |
| `workflows/pipeline.py` | Wire functional schema extraction |
| `run_pipeline.py` | Extend CSV export + triage list columns with advisory markers |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| LLM extraction adds latency | Optional (env var), non-fatal, skip if schema exists |
| Web3 detector false negatives | Keep unambiguous crypto terms as hard reject (no co-occurrence needed) |
| Web3 rescue phrase over-matching | Rescue phrases are local (neutralize one occurrence), not global. Unambiguous terms override rescue. |
| Schema table growth | One row per company (extract-once); bounded by company count, not signal count |
| Pipeline breakage | Schema extraction wrapped in try/except, full regression suite |
| Rate limit exhaustion | Schema extraction shares rate limiter with thesis classifier (sequential pipeline) |
| CSV export latency from JOINs | Performance smoke test validates < 2s for 500 signals with functional_schemas JOIN |

---

## Post-Phase 2 Validation

After all 10 tasks complete:
1. Run full test suite (target: 940+ tests, 0 failures)
2. Run pipeline with `ENABLE_FUNCTIONAL_SCHEMA=true` in dry-run mode
3. Export CSV and verify new columns present with advisory `*` markers
4. Run triage list and verify schema display with advisory `*` markers
5. Verify Web3 detector:
   - Passes: "access tokens", "DAO pattern", "data mining", "digital wallet"
   - Rejects: "ethereum token", "DAO governance token", "blockchain startup", "mining Bitcoin"
   - Override: "bitcoin access token" → REJECT (unambiguous overrides rescue)
6. Governance lint passes (no direct SignalStore construction in new files)
7. Performance smoke test passes: `pytest tests/performance/test_phase2_slos.py`
