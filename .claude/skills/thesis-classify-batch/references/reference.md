# Reference: thesis-classify-batch

## Primary entrypoints

### Ops CLI

- `python -m ops.cli quality thesis-classify-batch --days 30 --limit 200`

### Scripts (wrappers)

- `scripts/quality/thesis_classify_batch.py`

## Relevant tables

- `notion_status_events` (event log)
- `quality_feedback` (append-only audit trail)
- `signal_quality_metrics` (latest label per signal)
- `signals`, `signal_processing`, `suppression_cache` (existing)

## Common SQL snippets

```sql
-- Latest label for a signal
SELECT * FROM signal_quality_metrics WHERE signal_id = 123;

-- Recent status events for a canonical key
SELECT * FROM notion_status_events WHERE canonical_key = 'domain:example.com' ORDER BY observed_at DESC LIMIT 20;
```

## File pointers

- `scripts/quality/thesis_classify_batch.py`
- `ops/quality/thesis.py`

## Gotchas

- Labeled metrics are computed over labeled signals only; unlabeled signals are excluded from rates.
- Notion status event timestamps are the *observation time* (sync cadence), not true edit times.
- Batch LLM classification requires `google-genai` and a valid API key (see `GEMINI_API_KEY`).
