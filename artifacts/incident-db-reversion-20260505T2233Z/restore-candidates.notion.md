# Notion mirror inventory

Status: read-only bounded inventory. This is a delta/provenance source, not a corpus reconstruction source.

## Scope

- Queried Notion CRM statuses: `Source`, `Initial Meeting / Call`, `Dilligence`, `Tracking`, `Committed`, `Funded`, `Passed`, `Lost`.
- Counted Notion page `created_time` and `last_edited_time`; these are Notion page timestamps, not original `signals.created_at` values.
- `Discovery identity` means the page has `Discovery ID` or `Canonical Key`.
- Raw page IDs and company names were written only to the private evidence packet, not this repo-facing summary.

## Counts

- Total pages fetched: `599`
- Pages with `Discovery ID`: `15`
- Pages with `Canonical Key`: `15`
- Pages with either discovery-owned identity: `15`

| Window | Created pages | Created with discovery identity | Last-edited pages | Last-edited with discovery identity |
|---|---:|---:|---:|---:|
| `2026-03-01T00:00:00+00:00` | 6 | 6 | 6 | 6 |
| `2026-04-29T00:00:00+00:00` | 0 | 0 | 0 | 0 |
| `2026-05-05T22:33:37+00:00` | 0 | 0 | 0 | 0 |

## Status counts for created pages with discovery identity

### since_2026_03_01
- `Tracking`: 6

### since_2026_04_29
- none

### post_reversion_window_after_2026_05_05_2233
- none

## Recovery implication

Notion can bound routed/pushed prospect deltas and provide page-level provenance for CRM-visible items. It cannot recreate suppressed/raw collector rows, original raw payloads, or signals that never reached Notion.