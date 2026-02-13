# Identity Charter

**Status:** Authoritative
**Created:** 2026-02-08
**Canonical path:** `docs/plans/identity-charter.md`
**Governs:** All identity decisions for signals, company_files, review_items

This is the single source of truth for entity identity in the Discovery Engine. All plans, implementations, and reviews must conform to this document.

---

## Definitions

| Term | Meaning |
|------|---------|
| **seed_key** | The input string to `entity_id_for_seed()`. Always the value of `signals.canonical_key`. |
| **canonical_key** | A structured identifier stored on `signals` (TEXT NOT NULL). Formats: `"domain:acme.ai"`, `"reg:companies_house:12345"`, `"name_loc:acme:london"`. Immutable after INSERT. |
| **strong_key** | Phase G term for `canonical_key` when used as a lookup key in `entity_aliases`. Same value, different context. |
| **entity_id** | The 16-char hex output of `entity_id_for_seed(seed_key)`. Stored in `entity_aliases.entity_id`. |
| **company_id** | Column on `signals`, `company_files`, `review_items` that stores an `entity_id`. Same value, same namespace — the name differs only because these tables predate Phase G. |

---

## ID Format

| Property | Value |
|----------|-------|
| **Type** | SHA256[:16] — 16-character hex string (8 bytes) |
| **Lexical contract** | Lowercase only. Regex: `^[0-9a-f]{16}$`. Enforced at application level (not DB CHECK). |
| **Generator** | `EntityIdentityStore.entity_id_for_seed(seed_key)` |
| **Location** | `storage/entity_identity_store.py` method `entity_id_for_seed` (line numbers advisory) |
| **Example** | `entity_id_for_seed("domain:acme.ai")` → `"a1b2c3d4e5f6a7b8"` |
| **NOT** | UUID5. UUID5 produces 36-char hyphenated strings incompatible with Phase G tables. |

```python
# Canonical implementation — do not duplicate
@staticmethod
def entity_id_for_seed(seed_key: str) -> str:
    return hashlib.sha256(seed_key.encode('utf-8')).hexdigest()[:16]
```

## Seed Key

- **Source:** `signals.canonical_key` column (TEXT NOT NULL)
- **Rule:** Never recomputed from raw_data — the stored value is the source of truth
- **Formats:** `"domain:acme.ai"`, `"reg:companies_house:12345"`, `"name_loc:acme:london"`
- **Uniqueness constraint:** `UNIQUE(canonical_key, signal_type, source_api, detected_at)` on signals table

## Phase G Integration

| Aspect | Decision |
|--------|----------|
| **Phase G status** | Tables (v19) REQUIRED for identity resolution |
| **Required tables** | `entity_aliases`, `entity_migrations` (migration 19) |
| **NOT required** | `entity_key_aliases`, `entity_blocking_index` (v20), `claim_facts` (v21) |
| **New alias tables** | NONE — `company_aliases` is NOT created |
| **Validation** | Check `entity_aliases` + `entity_migrations` exist before identity operations |

### Table Schemas (migration 19, read-only reference)

```sql
-- Strong key → entity_id map (authoritative)
CREATE TABLE entity_aliases (
    strong_key TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,           -- SHA256[:16]
    created_at TEXT NOT NULL,
    source_signal_id INTEGER,
    source_key TEXT
);

-- Merge history (append-only)
CREATE TABLE entity_migrations (
    from_entity_id TEXT NOT NULL,      -- Retired entity
    to_entity_id TEXT NOT NULL,        -- Absorbing entity
    merged_at TEXT NOT NULL,
    merge_reason TEXT,
    PRIMARY KEY (from_entity_id, to_entity_id, merged_at)
);
```

## Identity Resolution Flow

**Canonical methods** (all in `storage/entity_identity_store.py` — do not re-implement):

| Method | Purpose |
|--------|---------|
| `entity_id_for_seed(seed_key)` | Generate entity_id from canonical_key |
| `lookup_strong_keys(keys)` | Resolve canonical_key → entity_id via `entity_aliases` |
| `upsert_strong_key_bindings(bindings, tx)` | Register new strong_key → entity_id bindings; returns merge pairs |
| `resolve_entity_root(entity_id)` | Follow transitive chains in `entity_migrations` (max 10 hops) |
| `merge_entities(from_id, to_id, reason, tx)` | Record merge in `entity_migrations`; update `entity_aliases` |

**Transaction primitive** (in `storage/signal_store.py`): `transaction_immediate()` — BEGIN IMMEDIATE; all identity writes use this.

Used in `save_signal()` and backfill:

```
1. lookup_strong_keys([canonical_key])
   ├─ Found → use returned entity_id as company_id
   └─ Not found:
       a. Generate: entity_id_for_seed(canonical_key)
       b. Register: upsert_strong_key_bindings([binding], tx)
       c. Handle merge pairs returned by (b) via cascade_merge()
```

**Critical:** Step (b) is mandatory. Without it, the next signal with the same canonical_key won't resolve via identity store.

### Root Resolution Failure Policy (strict)

`resolve_entity_root()` may fail on cycle detection or hop exhaustion (max 10). Policy:

- **Fail closed:** If root cannot be resolved, identity writes MUST raise `IdentityResolutionError` and abort the transaction. Do not store a stale/intermediate entity_id.
- **Operator repair:** Failed resolution requires manual investigation via `entity_migrations` table and audit_log.
- **Rationale:** "All stored IDs refer to the resolved root" is an auditable invariant only if we never store partial resolutions.

## Merge Semantics

| Property | Value |
|----------|-------|
| **Winner selection** | Lexicographically smallest entity_id (deterministic) |
| **Parameter semantics** | `merge_entities(a, b)` treats inputs as unordered; loser/winner determined by lexmin after root resolution. `cascade_merge()` must receive `(winner, loser)` — caller resolves direction. |
| **Implementation** | `EntityIdentityStore.merge_entities(from_id, to_id, reason, tx)` |
| **Location** | `storage/entity_identity_store.py` method `merge_entities` (line numbers advisory) |
| **Phase G records** | `entity_migrations` table (from → to) |
| **Phase 1a cascades to** | `signals.company_id`, `company_files.company_id`, `review_items.company_id` |
| **Root resolution** | `resolve_entity_root(entity_id)` — follows transitive chains, max 10 hops |

### Active Review Statuses (for merge collision resolution)

- **Active statuses:** `pending`, `approved`, `publish_queued`
- **Deactivation on merge:** loser's active review set to `status='rejected'` with `reason='merged_into:<winner_review_id>'`
- **Tie-break (both have active reviews):** precedence `publish_queued` > `approved` > `pending`, then newest `updated_at`
- **Evidence merge:** union of `signal_ids` from both evidence bundles (computed in Python via `json.loads` + set union)

### Merge Cascade Order (inside single transaction)

1. Resolve review_items UNIQUE collision (deactivate loser's active review per tie-break above)
2. Reassign loser's review_items to winner
3. Reassign loser's signals to winner
4. Merge loser's company_file into winner's (combine sources, timestamps)
5. Delete loser's company_file
6. Write audit_log entry

## Merge History Storage

| What | Where | Notes |
|------|-------|-------|
| Identity merges | `entity_migrations` (Phase G) | Automatic via `merge_entities()` |
| Operator-initiated merges | `audit_log` (v27) | Manual merge records with actor + reason |
| Strong key → entity_id | `entity_aliases` (Phase G) | Authoritative lookup table |

**No separate alias table.** `entity_aliases.strong_key` is the single authoritative map.

## Transaction Rules

- All identity writes use `transaction_immediate()` (BEGIN IMMEDIATE)
- `cascade_merge()` accepts optional `tx` parameter to participate in caller's transaction
- `upsert_strong_key_bindings(bindings, tx)` already requires `tx: aiosqlite.Connection`
- Never nest `transaction_immediate()` calls — pass `tx` to inner functions

## Column Contracts

| Table | Column | Value | Format |
|-------|--------|-------|--------|
| `signals` | `company_id` | Phase G entity_id | 16-char hex |
| `company_files` | `company_id` | Phase G entity_id | 16-char hex |
| `review_items` | `company_id` | Phase G entity_id | 16-char hex |

All three always refer to the **resolved root entity** (post-merge). Stale IDs are updated by cascade_merge().

### Nullability Transition (Phase 1a backfill)

- `signals.company_id` is added as `TEXT` (nullable) by v28 migration for backfill compatibility
- **All new signals** saved after v28 MUST have `company_id` set (enforced in `save_signal()`)
- **Backfill** must reach 0 NULL `company_id` rows before enabling thin files or review queues
- **Migration gate** (`check_identity_integrity()`) blocks pipeline if any NULLs remain
- Post-backfill, NULL `company_id` is treated as a data integrity violation

## Prohibitions

- Do NOT use UUID5 for company_id generation
- Do NOT create a `company_aliases` table (Phase G's `entity_aliases` is authoritative)
- Do NOT recompute `canonical_key` from `raw_data` during backfill
- Do NOT call `merge_entities()` without cascading to dependent tables
- Do NOT open `transaction_immediate()` inside an existing `transaction_immediate()`
- Do NOT store `distinct_source_count` as a column (derive from `source_apis` JSON at read time)
