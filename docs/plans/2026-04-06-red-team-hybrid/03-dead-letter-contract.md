# Dead-Letter Contract — Soft Schema Quarantine

**Date:** 2026-04-06
**Status:** Spec only — no code in Move 0
**Implements:** Move 1 quarantine layer (per strategy §1)
**Resolves:** Risks R5 + R14 (lifecycle of quarantine + v52 migration timing)

---

## 1. Why this exists

The strategy chose **soft schema-on-write** over strict schema-on-write because
strict validation has an asymmetric failure mode: you miss precisely the early
signals you care about when a source changes shape. Soft validation requires a
quarantine for the inputs that fail validation.

A quarantine without a contract is a graveyard. Six months from now, nobody
triages it, the schema-evolution signal it contains is lost, and the strategy
quietly degrades into "strict validation in disguise but with extra disk usage."
This contract exists to prevent that.

---

## 2. Storage location

### During the regret window (2026-04-06 → 2026-04-19)
**File-based, no migration:**
```
data/shadow/dead_letter/<yyyy-mm-dd>/<source_api>.jsonl
```

Example:
```
data/shadow/dead_letter/2026-04-20/hacker_news.jsonl
data/shadow/dead_letter/2026-04-20/news_api.jsonl
data/shadow/dead_letter/2026-04-21/hacker_news.jsonl
```

Why file-based: zero migration during the regret window. JSONL is greppable,
diffable, and naturally partitioned by date for retention.

### After 2026-04-19 (Move 1)
The collectors that have artifact capture wired (top 3 — likely
`hacker_news.py`, `news_api.py`, `rss_feeds.py` based on volume) write to the
JSONL path above when soft validation fails.

### After Move 3 (Postgres)
**Promoted to a `signals_dead_letter` table** in the Operational Core context
(per `02-bounded-context-map.md` §3.1). Schema:

```sql
CREATE TABLE signals_dead_letter (
    id BIGSERIAL PRIMARY KEY,
    source_api TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload JSONB NOT NULL,
    validation_error JSONB NOT NULL,        -- which schema rule failed
    blob_hash TEXT,                         -- pointer to raw bytes in BlobStore
    triage_status TEXT NOT NULL DEFAULT 'pending',  -- pending|reviewed|drift|drop
    triage_notes TEXT,
    triaged_at TIMESTAMPTZ,
    triaged_by TEXT,
    parser_update_proposed BOOLEAN NOT NULL DEFAULT FALSE,
    parser_update_proposal_id BIGINT REFERENCES parser_update_proposals(id)
);

CREATE INDEX idx_dl_source_received ON signals_dead_letter (source_api, received_at);
CREATE INDEX idx_dl_pending ON signals_dead_letter (triage_status) WHERE triage_status = 'pending';
```

The migration to this table is **NOT in Move 0 or Move 1.** It is Move 3. During
Move 1/2, the JSONL files are the source of truth.

### Forbidden during the regret window
- New SQLite tables (would require a v52 migration; blocked by R1)
- Writes to `signals.db` from any new code path
- Any modification to `storage/migrations/`

---

## 3. Record schema (JSONL)

Each line in the JSONL file is one quarantined record:

```json
{
  "schema_version": 1,
  "source_api": "hacker_news",
  "external_id": "ItemId:42999998",
  "received_at": "2026-04-20T14:23:11.456Z",
  "raw_payload": { /* full original payload */ },
  "validation_error": {
    "rule": "missing_required_field",
    "field": "by",
    "context": "HN /v0/item/42999998 returned object with no `by` field"
  },
  "blob_hash": "abc123...",
  "blob_uri": "data/blobs/ab/c1/abc123....zst",
  "collector_version": "hacker_news.py@v1.4.2",
  "triage_status": "pending"
}
```

**Fields:**
- `schema_version` — bump when the contract changes
- `source_api` — matches the collector's `source_api` constant
- `external_id` — stable ID from the source (HN item ID, RSS GUID, etc.)
  for dedup if a record gets quarantined twice
- `received_at` — ISO 8601 UTC
- `raw_payload` — the full payload that failed validation (NOT just the bad field)
- `validation_error` — structured: `{rule, field, context}`. Required fields:
  `rule` from a finite enum (see §4); other fields optional context
- `blob_hash` — content-address of the raw bytes if stored in BlobStore
- `blob_uri` — convenience pointer (sharded path)
- `collector_version` — for replay debugging
- `triage_status` — `pending` until reviewed; one of `pending|reviewed|drift|drop`

---

## 4. Validation rule taxonomy

Soft validation produces one of these failure types:

| Rule | Meaning | Action |
|---|---|---|
| `missing_required_field` | A field declared required by the parser is absent | Quarantine |
| `wrong_type` | A field is present but the wrong type | Quarantine |
| `invalid_format` | A field is the right type but malformed (URL, date, email) | Quarantine |
| `out_of_range` | Numeric field outside expected bounds | Quarantine |
| `unknown_field` | New field not in schema (potential schema drift) | Pass + log |
| `truncated_payload` | Payload appears cut off | Quarantine |

**`unknown_field` is special:** it does NOT quarantine. It passes the record
through but logs the unknown field for the schema-evolution feedback loop.
Repeated `unknown_field` for the same field is a strong signal of upstream
schema drift.

---

## 5. Triage cadence

**Weekly review:**
- Owner: rotating (the engineer who shipped the most code that week)
- Cadence: every Monday at the team's normal stand-up
- SLA: dead-letter rows older than 14 days must be triaged or escalated
- Tool: a new `quality dead-letter review` subcommand (Move 1 work — see §8)

**Daily auto-summary:**
- A scheduled job (Move 1 work) writes a one-line summary to a Slack channel:
  `"Dead-letter intake yesterday: hacker_news=12 news_api=3 rss_feeds=0; oldest pending = 4 days"`

---

## 6. Escalation rules

The dead-letter is a *signal*, not a graveyard. Escalation triggers:

| Trigger | Action |
|---|---|
| Single source's dead-letter rate > 10% of intake for 3 consecutive days | SPC alert (uses existing `monitoring/spc_monitor.py` infrastructure) |
| Dead-letter row uncategorized for 14 days | Auto-escalate to next stand-up |
| Same `validation_error.rule + field` recurs ≥ 5 times in 24 hours | Auto-create a parser update proposal (see §7) |
| Total dead-letter rows > 1000 untriaged | Block all merges to main until triage |

The 10%/3-day rule reuses the existing SPC infrastructure and zero-volume
alerting (commit `f6602c1`). It is the same shape as the existing checks, not
a new alerting system.

---

## 7. Schema-evolution feedback loop

This is the *signal* the soft-validation strategy was supposed to capture, and
the part that's most likely to be ignored if not made explicit.

### Detection
A "candidate parser update" is a quarantined pattern that meets:
- Same `source_api`
- Same `validation_error.rule`
- Same `validation_error.field`
- ≥5 occurrences in 24 hours OR ≥20 occurrences in 7 days

When detected, the system creates a row in a new file:
```
data/shadow/parser_update_proposals/<yyyy-mm-dd>-<source_api>-<field>.md
```

containing:
- The dead-letter rows that triggered it (limited to 5 examples)
- A diff of the proposed schema change
- A "decide by" date (default: 7 days)

### Decision
A human reviews the proposal weekly:
- **Accept:** the parser is updated; old quarantined rows are eligible for replay
  (Move 1 will provide replay tooling)
- **Reject:** the rows are marked `drop` and excluded from replay; if the
  pattern repeats, the dead-letter graduates to a permanent "known noise"
  filter
- **Investigate:** the dead-letter rows are examined individually; default if no
  decision in 7 days

### Replay
Accepted parser updates trigger a replay of the relevant dead-letter rows
through the new parser. Successful replays move the rows from dead-letter into
the normal pipeline as if they had arrived fresh; failed replays are re-quarantined
with a new validation_error and counted against the SPC alerting.

This closes the loop: soft validation captures the schema drift signal, the
team reviews it weekly, parser updates land, and dead-letter content gets a
second chance.

---

## 8. Tooling spec (for Move 1)

The tooling does NOT exist yet. Move 0 only specifies it. Move 1 builds it.

### `ops/cli quality dead-letter intake`
Reads `data/shadow/dead_letter/**/*.jsonl`. Outputs counts by source_api and
date. Used by the daily auto-summary job.

### `ops/cli quality dead-letter review`
Interactive subcommand. Walks through pending rows, prompts for triage_status
and notes. Writes to a separate "triaged" file (do NOT mutate the original
JSONL — keep raw intact).

### `ops/cli quality dead-letter propose-update`
Detects candidate parser updates per §7. Writes proposal markdown files.

### `ops/cli quality dead-letter replay`
After a parser update lands, reruns the relevant dead-letter rows through the
new parser. Reports successful/failed counts.

All four subcommands belong to the existing `ops/quality_cli.py` registration
surface (14 subcommands today, would add 4 more). They live in
`ops/quality/dead_letter.py`.

**These are Move 1 deliverables. Move 0 only has this spec.**

---

## 9. Interaction with existing zero-volume alerting

Per project memory (commit `f6602c1`):
> SPC LCL for count metrics could go negative, so zero-volume collectors never
> triggered alerts. Fix: SPCMonitor.check_zero_volume() + daily aggregator
> zero-backfill for absent collectors.

**The dead-letter must NOT be counted as "successful collection."** A flood of
quarantined inputs from a collector should look like:
- The collector's *dead-letter rate* is firing the SPC alert (per §6)
- The collector's *successful intake* is dropping (existing zero-volume alert
  may also fire)

These are two independent alerts on the same incident. They reinforce each
other; they do not double-count.

**Implementation note for Move 1:** when wiring soft validation, the metric
that feeds `daily_aggregator` is "successful parses," not "raw intake." This
prevents quarantined records from masking zero-volume failures.

---

## 10. Disk growth budget

Per red-team §3 SRE.3 Fermi estimate at current rates:
- ~3-5 signals/day × ~50 KB raw payload (avg) × 90 days × ~2x compression × ~30% dedup
- ≈ ~10-15 MB/quarter

For dead-letter specifically (subset of the above):
- Assume ≤10% of intake is quarantined ≈ ~1-2 MB/quarter
- 90-day retention is fine on any modern disk

**Retention policy:** 90 days for raw quarantined JSONL. After 90 days, rows
that are still `pending` get auto-escalated; rows that are `reviewed/drift/drop`
get archived to `data/shadow/dead_letter/_archive/` and gzipped. After 180 days,
the archive is purged.

A retention cron is a Move 1 deliverable. **The cron must land BEFORE soft
validation goes live**, otherwise the team will hit "we should have specified
eviction" exactly as the premortem warned.

---

## 11. Testing strategy

Move 1 tests (NOT Move 0):

1. **Synthetic quarantine:** feed the parser a known-bad payload, assert it
   lands in `data/shadow/dead_letter/<today>/<source>.jsonl` with the correct
   validation_error.
2. **Replay round-trip:** quarantine a payload, update the parser, run replay,
   assert the row moves into the normal pipeline.
3. **Daily aggregator interaction:** quarantine N payloads, assert
   `quality_metrics_daily.successful_parses` does NOT include them but
   `quality_metrics_daily.dead_letter_count` does.
4. **SPC alert path:** simulate 3 days of >10% quarantine, assert SPC alert
   fires.

Tests live in `tests/red-team-hybrid/test_dead_letter_contract.py` in Move 1
(NOT in Move 0).

---

## 12. Open questions

1. **Where does the parser_update_proposals workflow integrate with
   `ops/quality/dead_letter.py`?** Probably as a sub-module; finalize in Move 1.
2. **Can the dead-letter use the existing BlobStore?** Yes — `blob_hash` field
   in §3 already references it. The raw payload is stored in BlobStore, the
   dead-letter row carries the hash. This avoids duplicating raw bytes.
3. **What's the right SPC chart for "dead-letter rate"?** Likely the existing
   `publish_fp_rate` chart shape (PR #119) — proportion-based, with control
   limits. Decision: Move 1.
