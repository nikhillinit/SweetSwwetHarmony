# Quality Ops Architecture

This repo’s “quality ops” loop is an **event‑sourced feedback flywheel** that turns downstream outcomes (Notion statuses + human labels) into measurable quality metrics and actionable tuning proposals.

The goal is **lower false positives (FP)** without sacrificing recall, by continuously feeding real outcomes back into the pipeline.

## Core flywheel

1. **Observe**
   - Collect signals from sources (collectors) into `signals`.
   - Push to Notion (CRM) and track processing in `signal_processing`.
   - Periodically sync Notion statuses into `suppression_cache`.

2. **Label**
   - **Manual labels**: humans label individual signals as TP/FP/UNSURE.
   - **Outcome labels**: infer TP/FP from **Notion status transitions** (e.g., Passed → FP, Funded → TP) within an SLA window (default 30 days after push).

3. **Diagnose**
   - Run FP pattern mining over labeled data:
     - collector concentration
     - category concentration
     - duplicate descriptions / spam templates
     - temporal hotspots
     - weak canonical keys (`name_loc:`) overrepresented in FP

4. **Tune**
   - Generate a “tuning proposal” document (YAML) that contains:
     - safe, auto‑applicable patches (e.g., add negative keywords to `config/v2/negative_keyword_policy.yaml`)
     - human action items (e.g., adjust collector parsing, throttle schedules, strengthen keys)

5. **Measure**
   - Compute FP rates by collector/category/time window.
   - Compare pre/post tuning metrics and iterate.

## Data model

### Existing tables

- `signals` — raw signals with canonical keys and raw_data payloads.
- `signal_processing` — state machine for queued/pushed signals and the Notion page id.
- `suppression_cache` — current Notion status by canonical key (snapshot from sync).

### Quality Ops tables (new)

- `notion_status_events`
  - **Event log** produced by `sync_suppression` diffs.
  - Each row is a status transition observed at a timestamp.
- `quality_feedback`
  - Append‑only audit trail of manual label actions.
- `signal_quality_metrics`
  - Latest resolved label per signal (`TP`/`FP`/`UNSURE`), with provenance:
    - `label_source`: `manual`, `notion_status_event`, `notion_snapshot`, `auto`
    - linkage to the Notion page/status and (optionally) a status_event_id

## Jobs and entrypoints

### Ops CLI
Most workflows are available via:

- `python -m ops.cli quality ...`

### Scripts
Thin wrappers live in:

- `scripts/quality/`

## Guardrails

- **Manual labels override inferred labels** by default.
- Auto‑apply is intentionally narrow: only deterministic edits (like negative keyword policy updates) are supported.
- High‑risk operations (canonical key migrations, collector disabling) are produced as **suggestions**, not auto‑applied.

## Where to start

1. Sync and capture status events:
   - `python -m ops.cli quality sync-status-events`
2. Infer TP/FP outcomes from events:
   - `python -m ops.cli quality backfill-outcomes`
3. Inspect quality stats:
   - `python -m ops.cli quality stats --days 30`
4. Find patterns:
   - `python -m ops.cli quality find-patterns --out /tmp/patterns.json`
5. Propose tuning:
   - `python -m ops.cli quality propose-tuning --patterns /tmp/patterns.json --out /tmp/proposal.yaml`
