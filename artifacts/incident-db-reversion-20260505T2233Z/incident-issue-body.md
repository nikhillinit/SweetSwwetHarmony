# Verified facts

- R19 closure evidence from `2026-04-30` remains a separate, valid closure record; this incident starts from the later DB state change.
- Keepalive chronology:
  - `2026-05-01`: `news_api` stale while other operational collectors still had rows.
  - `2026-05-05T16:44:11Z`: `arxiv`, `hacker_news`, and `rss_feeds` were still fresh; `news_api` was stale at `73.73h`.
  - `2026-05-06T17:14:31Z`: all four operational collectors were `MISSING`.
  - `2026-05-07T19:53:06Z`: all four operational collectors remained `MISSING`.
- Phase 0 raw evidence was frozen before branch switching or live DB inspection.
- The frozen live `signals.db` copy is byte-identical to `signals.db.pre-recovery-20260423-truncated`:
  - SHA-256: `447c1359918da1a2f4abf31867d3e21bd1b5f855ad9e5336ea5b9c3c98c5940e`
  - Size: `1466368` bytes
  - Live file mtime: `2026-05-05T22:33:37Z`
- The frozen live `signals.db` copy is also byte-identical to `pre-restore-20260429-075534.db`, the safety backup captured immediately before the 2026-04-29 R19 restore.
- Quarantined inspection of the frozen live DB copy shows:
  - Integrity check: `ok`
  - Signal rows: `4`
  - Newest `signals.created_at`: `2026-01-10T12:18:09.035890+00:00`
  - Operational coverage: `hacker_news=0`, `arxiv=0`, `rss_feeds=0`, `news_api=0`
- Repo-local DB ops ledger evidence is scoped to repo-owned tools. It is not universal proof against external/manual file operations.
- The Task Scheduler operational-log query for the `2026-05-05T18:00Z` to `2026-05-06T06:00Z` window had no Harmonic/signals/keepalive hits.
- `HarmonicKeepAlive` has been disabled after evidence capture as a containment measure.
- Read-only Notion inventory fetched 599 CRM pages, 15 with discovery-owned identity, 6 created since `2026-03-01`, and 0 created or edited since `2026-04-29`.

# Hypotheses

- The DB appears to have been replaced or reverted at file level around `2026-05-05T22:33:37Z`.
- The byte identity supports a relationship to the known truncated DB state.
- The currently captured evidence does not prove the exact writer or mechanism.
- The specific mechanism "canonical `scripts/restore_db.py` copied from `signals.db.pre-recovery-20260423-truncated`" is weakened because `restore_db.py` uses `shutil.copy2`; copying from that known file would be expected to preserve the source mtime `2026-04-08T05:25:02Z`, not the observed live mtime `2026-05-05T22:33:37Z`.
- The empty Task Scheduler operational-log window makes a visible scheduled-task firing an unlikely writer for the 22:33Z event.

# Open questions

- What writer, scheduler, manual command, external tool, or sync surface caused the byte-identical DB state to appear at the May 5 mtime?
- Is there any remote/cloud/VSS/File History candidate newer or more complete than the local 612-row baseline family? VSS still requires a true Administrator PowerShell enumeration.
- Can any non-Notion mirror provide a bounded delta/provenance supplement for the post-R19 ingest window without pretending to reconstruct the raw corpus?
- Does GitHub issue `#148` remain a narrow `news_api` staleness track, or is it later superseded by this DB reversion once attribution is known?
- Does the DB guard block write commands on this truncated DB? `health --json` confirmed `catastrophic_drop_detected` for a read command but did not prove write-command blocking.

# Current recovery posture

- No live restore/reset has been performed.
- Local candidate validation found 24 valid SQLite candidates.
- The newest small candidate by `signals.created_at` is a 34-row Hacker News rehearsal DB and is not a baseline corpus candidate.
- The strongest local baseline family is the 612-row pre-Step-4B / restore-stage group with all four operational collectors represented.
- The 612-row family is a regression baseline, not full recovery. `state/collectors.json` shows successful post-R19 collector work through `2026-05-05T16:44:10Z`, including `arxiv signals_new=98`, `hacker_news signals_new=10`, and `rss_feeds signals_new=8`.
- Notion does not appear to cover the missing post-R19 local ingest window: 0 pages were created or edited since `2026-04-29`.
- Recovery execution should be opened as a separate bounded task after attribution is resolved or explicitly timeboxed as unresolved.
