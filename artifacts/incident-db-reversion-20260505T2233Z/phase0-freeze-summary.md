# Phase 0 freeze summary

Private packet: `packet-20260508T001346Z`

## Checkout state

- Branch at freeze: `chore/post-r19-keepalive-may-2-5`
- HEAD at freeze: `430bd03689d615313ab7cbc5ddae63f56410e1f9`
- Upstream at freeze: `origin/chore/post-r19-keepalive-may-2-5`
- No branch switch was performed before raw file copy and hashing.
- Dirty chronology surfaces at freeze included `state/collectors.json`, `.agents/`, `.codex/`, `.omx/plans/signals-db-incident-response-deliberate-2026-05-07.md`, `.tmp/`, `artifacts/keepalive/2026-05-06.json`, and `artifacts/keepalive/2026-05-07.json`.

## Raw evidence captured privately

- `signals.db`
- `signals.db-wal` and `signals.db-shm` absence records
- `signals.db.pre-recovery-20260423-truncated`
- `state/collectors.json`
- Nine keepalive files from `2026-04-29` through `2026-05-07`
- `.omx/logs/db_ops_ledger.jsonl`
- `.a5c/runs/` window listing around `2026-05-05T22:33Z`
- Task Scheduler task metadata, task info, XML export, and operational-log query output
- PowerShell history path and private raw PowerShell history copy
- Current-process `cmd` history capture
- WSL status and distro listing
- Windows Object Access audit availability attempt and Security 4663 query output
- File History service/config listing, VSS query output, and OneDrive/repo-location metadata

## Byte identity proof

The frozen live `signals.db` copy and the frozen `signals.db.pre-recovery-20260423-truncated` copy are byte-identical:

- SHA-256: `447c1359918da1a2f4abf31867d3e21bd1b5f855ad9e5336ea5b9c3c98c5940e`
- Size: `1466368` bytes
- Frozen live DB mtime: `2026-05-05T22:33:37.416284Z`
- Known truncated backup mtime: `2026-04-08T05:25:02.343641Z`

## Quarantined DB inspection

Inspection was run only against private packet copies, not live `C:\dev\Harmonic\signals.db`.

`signals.db` frozen copy:

- Integrity check: `ok`
- Signal rows: `4`
- Source column: `source_api`
- Newest `signals.created_at`: `2026-01-10T12:18:09.035890+00:00`
- Operational coverage: `hacker_news=0`, `arxiv=0`, `rss_feeds=0`, `news_api=0`

`signals.db.pre-recovery-20260423-truncated` frozen copy:

- Integrity check: `ok`
- Signal rows: `4`
- Source column: `source_api`
- Newest `signals.created_at`: `2026-01-10T12:18:09.035890+00:00`
- Operational coverage: `hacker_news=0`, `arxiv=0`, `rss_feeds=0`, `news_api=0`

## Timeline anchors

- `2026-04-30`: R19 closure evidence exists separately and should not be retroactively invalidated by this later incident.
- `2026-05-01`: keepalive failed for `news_api` stale while other operational collectors still had rows.
- `2026-05-05T16:44:11Z`: keepalive still showed `arxiv`, `hacker_news`, and `rss_feeds` fresh; `news_api` was stale at `73.73h`.
- `2026-05-05T22:33:37Z`: frozen live DB mtime and suspected event window.
- `2026-05-06T17:14:31Z`: keepalive first captured all operational collectors as `MISSING`.
- `2026-05-07T19:53:06Z`: keepalive repeated all operational collectors as `MISSING`.

