# Phase 4 recovery -- summary

Status: Phase 4 (recovery) complete. Phases 5 (hardening) and 6 (MEMORY/wiki updates) remain separable follow-ups per the original incident-response brief's separability rule.

Companion to: `phase0-freeze-summary.md`, `attribution-report.md`, `attribution-report-addendum.md` (PR #151), `restore-candidates.local.md`, `restore-candidates.notion.md`.

GitHub issue: https://github.com/nikhillinit/SweetSwwetHarmony/issues/149
Phase 4 PR: this PR.
Origin brief: `.omx/context/signals-db-incident-response-20260507T160511Z.md` (separability rule line 19).

This doc records what was restored, against which baseline, and the verified outcome. It is intentionally narrow: no Phase 5 hardening (cloud backup / ACL / watcher) and no MEMORY.md or wiki updates land here.

## Salvage decision recap

The Phase 3 salvage decision was: restore from the newest verified local 612-row baseline (`signals.db.pre-step4b-promotion-20260404`). The bounded `restore-candidates.notion.md` inventory established that no CRM-trackable prospect data was lost in the post-R19 working window (0 created CRM pages with discovery identity since `2026-04-29`); the lost data is collector-corpus and is treated as accepted loss. VSS shadow-copy enumeration on `C:` returned `No items found` from an Administrator PowerShell session 2026-05-08 (per `attribution-report-addendum.md` line 45), so no newer Windows-local restore surface exists.

## Live-writers gate (pre-restore)

Verified before invoking the canonical restore script:

- Python processes referencing the DB: none (`tasklist.exe | grep -i python.exe` -> no matches).
- `sqlite` processes: none.
- `signals.db-wal` / `signals.db-shm` / `signals.db-journal`: absent.
- Exclusive open of `signals.db` from PowerShell: succeeded (no other reader/writer held the file).
- `HarmonicKeepAlive` scheduled task: `Disabled`. Last run `2026-05-07T19:51:44Z` (LastTaskResult `1`). Next run `N/A`.

The gate passed; the restore proceeded.

## Pre-restore live-state snapshot (neutral)

Captured prior to the restore for forensic completeness. This is recorded as a neutral pre-restore snapshot, not as a containment-failure claim. The watermark guard at `run_pipeline.py:229` protects guarded writer paths only; it cannot prevent file replacement, and was never claimed to.

| Field | Value |
|---|---|
| sampled_at_utc | `2026-05-08T06:08:02.737088Z` |
| size_bytes | `2113536` |
| sha256 | `c7ed87201f75254fcf5f3dfd31fffe10425fd82e401124933ad77e257cea12a7` |
| mtime_utc | `2026-05-08T04:18:33.499026Z` |
| rowcount(signals) | `4` |
| max(created_at) | `2026-01-10T12:18:09.035890+00:00` |
| integrity_check | `ok` |
| sidecars (wal/shm/journal) | absent / absent / absent |

The size, sha, and mtime drifted from the original Phase 0 freeze fingerprint (`447c1359...`, `1466368`, `2026-05-05T22:33:37Z`) by approximately 647 KB / 2.7 days. The truncated 4-row logical state was preserved across that drift, which is what bounded the salvage decision space. The most plausible explanation is the `python run_pipeline.py health --json` probe Codex ran during Phase 2A (timed out at 120s, recorded in PR #150 review).

## Restore command

```
cd C:/dev/Harmonic
python scripts/restore_db.py signals.db.pre-step4b-promotion-20260404 --db-path signals.db
```

No `--force`. Port 8000 was confirmed closed before invocation, so the API-reachability guard did not need to be bypassed.

Tool output (verbatim):

```
2026-05-07 23:08:47,439 [INFO] Acquired DB tool lock: signals.db.dbtool.lock
2026-05-07 23:08:47,439 [INFO] Validating backup integrity: signals.db.pre-step4b-promotion-20260404
2026-05-07 23:08:52,934 [INFO] Creating pre-restore backup: pre-restore-20260508-060852.db
2026-05-07 23:08:52,946 [INFO] Restoring from signals.db.pre-step4b-promotion-20260404 to signals.db
2026-05-07 23:08:53,091 [INFO] Restore complete: signals.db
2026-05-07 23:08:53,108 [INFO] Released DB tool lock: signals.db.dbtool.lock
Restore complete. Pre-restore backup: pre-restore-20260508-060852.db
```

The pre-restore safety backup `pre-restore-20260508-060852.db` is the captured `signals.db` immediately prior to overwrite (preserved on disk; gitignored).

## Local-only operational evidence (gitignored)

`scripts/restore_db.py` writes its own `db_ops_ledger.jsonl` row on every run via `utils.db_ops_ledger.append_db_ops_ledger`. The file `.omx/logs/db_ops_ledger.jsonl` is gitignored, so the row does not land in this PR. Quoted verbatim from line 50 of the local file (1 line newer than the 49-line state at session start, which ended at the R19 restore on `2026-04-29T07:55:35Z`):

```json
{"timestamp": "2026-05-08T06:08:53.092348+00:00", "pid": 40856, "tool_name": "restore_db", "db_path": "C:\\dev\\Harmonic\\signals.db", "action": "restore_backup", "status": "success", "details": {"backup_file": "signals.db.pre-step4b-promotion-20260404", "pre_restore_backup": "pre-restore-20260508-060852.db"}}
```

## Post-restore verification

| Check | Expected | Actual | Pass |
|---|---|---|---|
| source SHA-256 | `fcd06c6b...` | `fcd06c6bda36ca0106575b852a35a86ab857bb6b81ed3ce5a31232a79a8009d0` | yes |
| target SHA-256 matches source | true | true | yes |
| target size matches source | true | `9756672` = `9756672` | yes |
| integrity_check | `ok` | `ok` | yes |
| rowcount(signals) | `612` | `612` | yes |
| max(created_at) | `2026-03-01T19:33:33...` | `2026-03-01T19:33:33.650304+00:00` | yes |
| schema_version | non-error | `53` | yes |
| WAL/SHM/journal | absent | absent / absent / absent | yes |
| ledger row appended | success | success row at `2026-05-08T06:08:53Z` (line 50) | yes |
| pre-restore safety backup | created | `pre-restore-20260508-060852.db` | yes |

Source SHA-256 `fcd06c6b...` matches the `signals.db.pre-step4b-promotion-20260404` fingerprint recorded in MEMORY.md prior to this session.

## What this PR does NOT recover

This is a restoration to the newest verified local 612-row baseline. It is **not** full data recovery. Three load-bearing reframings:

1. **The 612-row baseline is a regression vs the post-R19 working window.** The backup file `signals.db.pre-step4b-promotion-20260404` was captured `2026-04-04`, which is older than the R19 boundary (`2026-04-29T07:55:35Z`). Wiki: any salvage candidate older than `2026-04-29T07:55Z` is a regression vs the post-R19 working state, regardless of internal row count.

2. **Concrete final-batch loss is enumerable.** Per `attribution-report-addendum.md` line 13 (PR #151): the 2026-05-05T16:43-16:44Z final successful collector batch alone documented `116` net-new persisted rows (arxiv=98, hacker_news=10, news_api=0, rss_feeds=8). Total enumerable loss across the `2026-05-02T13:00Z` -> `2026-05-05T16:43Z` window is higher pending walk-back of intermediate batches.

3. **Net unrecoverable signal floor is >=413.** From `909 + 116 - 612 = 413`. Per the addendum: the `signals_new` counter is post-dedup; the >=413 figure is therefore a floor, not a point estimate.

`news_api`, `rss_feeds`, and Hacker News content within the `2026-05-02` -> `2026-05-05` gap is treated as accepted loss and will not be replayed into production telemetry. Re-collection cannot reconstitute original `detected_at` timestamps or original thesis-classification routing decisions.

## Out of scope (intentionally)

Per the operator-stated separability rule in the origin brief: *"Recovery, hardening, and memory/postmortem updates should remain separable unless one depends on another."*

Not in this PR:

- Phase 5 hardening (cloud backup, watcher daemon for inode-level file-replacement detection, Windows SACL auditing on `signals.db`, ACL tightening). Cloud backup is the highest-priority Phase 5 item per Council consensus, because the incident proved local-only baselines are stale by weeks and co-resident with the writer that produced the incident.
- MEMORY.md update (Phase 6).
- `wiki/sessions/2026-05-08-harmonic-signals-db-incident-response.md` "Outstanding follow-ups" cleanup (Phase 6). Items #1 (32-ms triple-correlation finding) and #2 (VSS escalation) are already addressed by PR #151's addendum and should be flipped on the wiki list when Phase 6 lands.
- Re-baseline of drift monitoring (deferred until post-restore corpus has accumulated several days of fresh ingest).
- Re-run of the postponed Step 4B regret check on fresh post-restart data (was unblocked by R19 close 2026-05-05; was blocked again by this incident; now unblocked by this recovery, but requires several days of post-restart data first).
- Issue #148 (`news_api` 57h staleness) follow-up.
- Operator memory-recall reply on Issue #149 (does not block recovery; remains the cheapest open attribution signal for Phase 5 ACL design).

## Cross-references

- Issue #149 (durable incident record).
- PR #150 (evidence packet, merged `2026-05-08T01:28:39Z`, merge commit `97794b9`).
- PR #151 (attribution-report addendum, separate scope).
- `restore-candidates.local.md` (612-row baseline ranking).
- `restore-candidates.notion.md` (CRM-trackable delta = 0).
- `attribution-report-addendum.md` (32-ms triple-mtime cluster, `>=413` floor derivation).
- `phase0-freeze-summary.md` (Phase 0 evidence freeze).
- `.omx/context/signals-db-incident-response-20260507T160511Z.md` (operator-authored origin brief, separability rule).
- `.omx/plans/signals-db-incident-response-deliberate-2026-05-07.md` (canonical 7-phase RALPLAN-DR).
