# Attribution report -- addendum

Status: Phase 2A residual narrowing
Companion to: `attribution-report.md`
GitHub issue: https://github.com/nikhillinit/SweetSwwetHarmony/issues/149

This addendum captures (1) the strongest available narrowing of the 2026-05-05T22:33:37Z `signals.db` writer surfaced after the Phase 0 / 1 evidence packet merged via PR #150, and (2) the primary-source-attested data-loss disclosure derived from that evidence. It does not change the parent report's `unresolved` classification; it tightens it.

## Confirmed pre-incident corpus

909 unique rows on 2026-05-02T13:00:00Z (live `COUNT(*)` from `signals` table; `max(detected_at) = 2026-05-02T13:00Z`). Independently attested across 7 Claude Code session transcripts (`afd8f14e`, `4e990379`, `82a53d2a`, `c21bc790`, `4664b307`, `6b85be3e`, `0246b42a`).

Continued ingest through 2026-05-05T16:43-16:44Z. The final successful collector batch alone documented 116 net-new persisted rows (`state/collectors.json`: arxiv=98, hacker_news=10, news_api=0, rss_feeds=8). The `signals_new` counter is post-dedup; see "How `signals_new` is gated" below.

## Truncation event

Live `signals.db` reverted to a 4-row truncated state at **2026-05-05T22:33:37Z UTC** (`15:33:37` America/Los_Angeles), exactly **11 minutes** after R19 PR #145 merged at `22:22:37Z`. Live DB is byte-identical to `signals.db.pre-recovery-20260423-truncated` and `pre-restore-20260429-075534.db` (SHA-256 `447c1359918da1a2f4abf31867d3e21bd1b5f855ad9e5336ea5b9c3c98c5940e`, size `1466368`).

Mechanism per Phase 2A: file-level replacement (raw copy, move, or restore from outside the DB engine), inconsistent with an active-SQLite-connection write. `signals.db-wal` and `signals.db-shm` sidecars were absent at freeze. Almost certainly a manual or external-tool operation co-occurring with the operator's `git stash` -> `git reset --hard origin/main` -> `git stash pop` session at `22:33:37Z`. Not a scheduled task. Not canonical `scripts/restore_db.py`.

## File-cluster decomposition (new narrowing)

The repo-safe `artifact-index.redacted.md` per-file mtimes show a 4-file write group at the incident moment:

| File | Source mtime UTC | Tracked at origin/main? | git-reset can write? |
|---|---|---|---|
| `artifacts/keepalive/2026-04-30.json` | `2026-05-05T22:33:37.3845784Z` | yes (PR #145) | yes |
| `artifacts/keepalive/2026-05-01.json` | `2026-05-05T22:33:37.3860322Z` | yes (PR #145) | yes |
| `state/collectors.json` | `2026-05-05T22:33:40.8271815Z` | yes | yes |
| **`db/signals.db`** | **`2026-05-05T22:33:37.4162844Z`** | **no (`.gitignore:54`)** | **no** |

Three tracked files within a 3.4-second span are fully explained by `git stash` -> `git reset --hard origin/main` -> `git stash pop`. The two keepalive files were added by PR #145 and absent from local commit `dd7c749`; the reset pulled them in with the reset-time mtime. `state/collectors.json` was modified locally and was rewritten on `stash pop`. This is the expected, selective git-reset behavior: only files added between `dd7c749` and `origin/main` got the reset mtime; the 04-29 keepalive and the 05-02 through 05-05 keepalives (also untracked at `origin/main` HEAD) retained their natural daily mtimes.

`signals.db` is gitignored at `.gitignore:54` and has been untracked since commit `bc324cb chore: remove signals.db from tracking (already in .gitignore)`. **`git reset --hard` cannot have written `signals.db`.** Its `22:33:37.4162844Z` mtime, 31.7 ms after the keepalive cluster, requires a separate, co-occurring file-level operation in the same shell session. The selective-rewrite test confirms `git reset` worked exactly as expected and excludes it as the writer.

This narrows attribution to: a manual copy command (`cp` / `Copy-Item` / drag-and-drop / a script invoking such a copy) executed alongside the git-reset sequence in the same shell session. PowerShell history (private packet) had keyword hits for `signals.db`, copy/move/remove, `git checkout`, and `reset`, corroborating the cluster but suggestive only -- PSReadLine history is not timestamped.

## Recoverable from local backups

612 signals via `signals.db.pre-step4b-promotion-20260404`. Primary-verified via `sqlite3` against four independent backup files (pre-step4b, pre-step4a, pre-labeling-campaign, backup-before-curated-backfill): all 612 rows / `max(created_at) = 2026-03-01T19:33:33.650304+00:00` / 0 rows post-2026-05-02. Per-source breakdown: `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14`, plus ~44 from non-operational collectors.

## Other restore-candidate surfaces

- Notion mirror delta since the R19 boundary (2026-04-29): `0` (PR #150, `restore-candidates.notion.md`). Lost data is collector-corpus, not CRM-trackable.
- VSS shadow copies on `C:`: `0`. Elevated `vssadmin list shadows /for=C:` returned `No items found` 2026-05-08. Verified Administrator (`IsAdmin = True`, title `Administrator: Windows PowerShell`). Output captured at `packet-20260508T001346Z/vssadmin-list-shadows-C-20260508-elevated.txt` (SHA-256 `B848CC8913F893D8C25EEF15C241E1CEAF356A79222FC2786F399FF9DF4FDB97`). The VSS gap is permanent.

## Net unrecoverable signals: floor of >=413 (post-dedup, primary-source-attested)

Floor decomposes as `909 + 116 - 612 = 413`. The `signals_new` counter is post-dedup. Likely total higher pending enumeration of intermediate collector batches across the 2026-05-02T13:00Z through 2026-05-05T16:43Z window.

## How `signals_new` is gated

Verified at `collectors/base.py:299-398` against `origin/main` HEAD `97794b9`:

`self._signals_new += 1` (line `385`) is reachable only after four sequential gates pass:

1. **In-run identity** (lines `337-340`): skip if already processed in this run.
2. **`is_duplicate()` check** (lines `343-355`): evidence_key fast path + tuple fallback.
3. **Notion suppression cache** (lines `358-366`): skip if already in Notion.
4. **Successful `save_signal()` write** (lines `369-378`).

The schema-level guarantee against duplicate `evidence_key` rows is provided by the v46 partial UNIQUE index at `storage/migrations/v46_evidence_key_unique.py:9-11`:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_evidence_key
    ON signals(evidence_key)
    WHERE evidence_key IS NOT NULL AND evidence_key != '';
```

The 116-row `signals_new` count from the final batch is therefore not a raw-observation count -- it is post-dedup persisted count. The 909 attestation is a live `COUNT(*)`, not an inferred figure.

## Recovery posture

Recovery is bounded by the 612-row local backup; the >=413-signal delta is treated as accepted loss. Future re-ingest of overlapping sources (`github`, `sec_edgar`, `job_postings`) will recover organically as those collectors run on schedule; `news_api`, `rss_feeds`, and Hacker News content within the 2026-05-02 through 2026-05-05 gap is not recoverable from authoritative timestamps and **will not be replayed into production telemetry**. Re-collection cannot reconstitute original `detected_at` timestamps or original thesis-classification routing decisions, and would inject synthetic timestamps into SPC drift baselines and break long-term cohort analysis.

## Tracking

- Issue #149: `incident: signals.db reverted or truncated around 2026-05-05T22:33Z UTC`. Open. This addendum lands the strongest 22:33Z narrowing produced so far.
- Evidence packet PR #150: `docs: add signals db incident evidence packet`. Merged 2026-05-08T01:28:39Z (merge commit `97794b9`).
- Phase 4 recovery PR: separate, not yet opened.
- Phase 5 hardening: cloud backup is the highest-priority item because the incident proved local-only baselines are (a) stale by weeks and (b) co-resident with the writer.

## Disclaimers

- This addendum is doc-only. No recovery has been performed. No salvage path has been executed.
- The parent `attribution-report.md` `unresolved` classification stands. The 32-ms triple-mtime cluster narrows attribution to a manual or external file operation in the same shell session as the git-reset sequence; it does not identify the operator action or external tool.
- Operator memory recall on whether the 22:33Z file operation was a manual `Copy-Item` / drag-and-drop or an external tool is the cheapest signal still available and is being requested via Issue #149 comment alongside this addendum -- intentionally not in this forensic record.
