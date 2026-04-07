# LLM Structured-Outputs Failure Mode Decision

**Date:** 2026-04-06
**Status:** Decision made — implementation in Move 1
**Resolves:** Risk R10 (LLM structured-outputs failure mode unspecified)

---

## 1. The decision

**Soft-fail with retain-raw.**

When the LLM emits a schema-invalid response:
1. Parse what you can (fields that match the Pydantic schema get used)
2. Log the unparseable portion to the artifact store via BlobStore (replayable)
3. The signal goes to the existing `held` queue with reason `parse_failure`
4. The LLM call is NOT retried automatically (avoids latency + cost spiral)
5. The artifact + the partial parse + the raw response are all preserved

**Do NOT silently drop the signal.**

---

## 2. The three alternatives

| Option | Behavior on schema-invalid LLM response | Failure mode |
|---|---|---|
| **A. Hard-fail** | Discard the response entirely; downstream sees nothing | False negative on recall — the silent drop is invisible to the analyst |
| **B. Soft-fail with retain-raw (CHOSEN)** | Parse what you can, log unparseable to artifact store, route to `held` with reason | Signal appears in held queue; analyst sees it; replayable |
| **C. Retry with stricter prompt** | Re-call the LLM with "JSON only, no text" guard | Latency + cost; still might fail; no replay benefit; couples LLM cost to validation |

---

## 3. Why B composes with the rest of the strategy

| Property | A | B | C |
|---|---|---|---|
| Replay-friendly (Move 1 evidence lake) | No (data lost) | **Yes** (raw retained in BlobStore) | Partial (only the retry result) |
| Analyst-visible (Move 1 tooltip in §5.6) | No (signal vanished) | **Yes** (held queue + reason) | Yes (held queue, but no rationale) |
| Non-destructive | No | **Yes** | Yes |
| Cost-bounded | Yes | **Yes** | No (retry spiral risk) |
| Debug-friendly | No | **Yes** (validation_error structured) | Partial |
| Composes with dead-letter contract (`03-dead-letter-contract.md`) | No (no record kept) | **Yes** (same JSONL shape) | No (different path) |

Option B is the only one that earns the existing `held` queue + the analyst
tooltip + the dead-letter contract simultaneously. A and C each create silent
recall losses or operational fragility.

---

## 4. The integration with the dead-letter contract

LLM parse failures are **a special case of dead-letter records.** They use the
same JSONL format and the same triage cadence:

```json
{
  "schema_version": 1,
  "source_api": "thesis_classifier_llm",
  "external_id": "signal_id:42999998",
  "received_at": "2026-04-25T14:23:11.456Z",
  "raw_payload": {
    "model": "gemini-2.0-flash",
    "prompt_version": "v1.6.0-employer-distribution-guard",
    "raw_response": "<the LLM's actual output as a string>"
  },
  "validation_error": {
    "rule": "wrong_type",
    "field": "thesis_match",
    "context": "Expected float in [0,1], got string 'high'"
  },
  "blob_hash": "abc123...",
  "blob_uri": "data/blobs/ab/c1/abc123....zst",
  "collector_version": "thesis_classifier@v1.6.0",
  "triage_status": "pending"
}
```

**Storage path during the regret window:**
```
data/shadow/dead_letter/<yyyy-mm-dd>/thesis_classifier_llm.jsonl
```

**Storage path after Move 3:**
The same `signals_dead_letter` table (per `03-dead-letter-contract.md` §2). The
`source_api` field disambiguates LLM parse failures from collector-source
quarantines.

This means: **the team builds one set of triage tooling, not two.** The same
`ops cli quality dead-letter review` command handles both collector-source
quarantines and LLM parse failures.

---

## 5. The partial parse rule

When the LLM response is partially valid:

| Scenario | Behavior |
|---|---|
| All required fields present and valid | Normal path; signal goes through pipeline |
| Some required fields missing | Soft-fail; held with `parse_failure`; raw retained |
| Optional fields invalid | Use defaults for invalid optionals; signal proceeds; log a `wrong_type` event but don't quarantine |
| Required field present but wrong type | Soft-fail; held with `parse_failure`; raw retained |
| Confidence score outside [0,1] | Soft-fail; held with `parse_failure` (trust failure) |
| Free-text rationale field unparseable | Use empty string; signal proceeds; log a `wrong_type` event |

The principle: **the more load-bearing the field, the harder the fail.**
Confidence scores and category labels are load-bearing; rationale text is not.

---

## 6. Held queue interaction

The current pipeline already has a `held` status. Per project memory:
- Status transitions: `pending → processing → held / pushed / rejected`
- `held` is the human-review queue
- The analyst sees `held` signals in the inbox view

LLM parse failures route to `held` with reason `parse_failure` (a new reason
code). The analyst sees them in the inbox alongside other held signals.

**The new reason code is the only schema change.** It is a new value in an
existing enum, not a new column or table. **It is still a Move 1 change**, not
a Move 0 change, because it touches the production held-status pipeline.

---

## 7. Cost and latency considerations

| Concern | Impact under Option B |
|---|---|
| LLM cost | Unchanged — no retry, no extra calls |
| Latency | Unchanged — no retry, no extra round-trip |
| Disk cost | +1 dead-letter row + 1 BlobStore entry per failure (~1 KB compressed) |
| Triage cost | 1 row to review per failure during the weekly triage cadence |

The cost is dominated by the existing LLM call (which already happened); the
quarantine adds bytes, not roundtrips.

---

## 8. Failure mode example walkthrough

Scenario: the Gemini API returns a malformed thesis classification for signal #501.

```
1. Pipeline runs LLM thesis classifier on signal #501
2. Response received: "thesis_match": "high" (string, expected float)
3. Pydantic schema fails: ValidationError on `thesis_match` field
4. Soft-fail handler:
   a. Attempts partial parse — gets `category: consumer_cpg` (valid),
      `confidence: 0.8` (valid), `rationale: "..."` (valid)
   b. `thesis_match` is a load-bearing required field — flagged as load-bearing fail
5. Raw response stored in BlobStore — hash: "abc123..."
6. Dead-letter JSONL row appended:
   data/shadow/dead_letter/2026-04-25/thesis_classifier_llm.jsonl
7. Signal #501.status = "held", reason = "parse_failure"
8. Signal #501 appears in analyst inbox
9. Analyst clicks the "Why was this held?" tooltip (Move 1 deliverable)
10. Tooltip shows:
    - Reason: parse_failure
    - Validation error: thesis_match was "high" (expected float in [0,1])
    - Raw response (link to BlobStore)
    - Partial parse: {category: consumer_cpg, confidence: 0.8}
    - Action: "Review and label" or "Skip — parser drift"
```

The analyst gets a recoverable view of what went wrong, and the team gets the
schema-drift signal in the dead-letter triage queue.

---

## 9. What happens if the partial parse is "good enough"?

Edge case: the LLM returns a malformed thesis_match but a valid category +
confidence. Could the pipeline route on the partial parse?

**No.** The decision is to route on the *complete* parse or quarantine. Routing
on partials would:
- Create two parallel decision paths (full vs partial) that drift apart
- Mask the schema-drift signal (the partial parses look like noise, not drift)
- Make the held queue silently shrink as more signals "almost work"

The held queue is the right place for almost-works signals. Don't try to route
them automatically.

---

## 10. Move 1 deliverables (NOT Move 0)

- [ ] `LLMResponseValidator` Pydantic model in
      `analytics/llm_response_schema.py` (NEW file, not in protected paths)
- [ ] Soft-fail handler in `workflows/pipeline.py:1637+` (PROTECTED PATH —
      Move 1, not Move 0)
- [ ] New `parse_failure` reason in held-status enum (PROTECTED PATH — Move 1)
- [ ] Dead-letter writer that uses the contract from `03-dead-letter-contract.md`
- [ ] Tooltip integration in `api/routers/triage.py` (NOT in protected paths,
      can be Move 1 day 1)
- [ ] Tests in `tests/red-team-hybrid/test_llm_soft_fail.py`

Move 0 produces this spec only.

---

## 11. Open questions

1. **Should the partial parse be exposed to the analyst tooltip?**
   Yes — see §8 step 10. The partial gives the analyst a fast triage signal.
2. **Is `gemini-2.0-flash` actually returning malformed responses today?**
   Per project memory, "model metadata field null for excluded path — minor
   gap." Worth measuring during the Move 1 dry-run on the existing 612 signals.
3. **Should there be a confidence threshold below which we skip the LLM call
   entirely?** Already exists: `THESIS_SKIP_LLM_BELOW=0.0` (currently 0.0,
   meaning never skip). This is orthogonal — soft-fail handles the calls that
   *do* happen and produce malformed responses.
