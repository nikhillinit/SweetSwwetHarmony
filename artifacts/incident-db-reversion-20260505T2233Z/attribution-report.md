# Attribution report

Status: initial Phase 2A pass  
Classification: `unresolved`

## Confirmed

- The frozen live `signals.db` copy is byte-identical to `signals.db.pre-recovery-20260423-truncated`.
- The frozen live DB has only `4` `signals` rows, newest `created_at` `2026-01-10T12:18:09.035890+00:00`.
- No `signals.db-wal` or `signals.db-shm` sidecar was present at freeze time.
- `state/collectors.json` still recorded successful operational collector activity at `2026-05-05T16:43:54Z` through `2026-05-05T16:44:10Z`.
- The `2026-05-05` keepalive still had `arxiv`, `hacker_news`, and `rss_feeds` as `FRESH`, with only `news_api` stale.
- The `2026-05-06` and `2026-05-07` keepalive files showed all four operational collectors as `MISSING`.
- The `pre-restore-20260429-075534.db` safety backup is also byte-identical to the current truncated state. That file was the pre-R19-restore snapshot captured before the 2026-04-29 restore.
- `HarmonicKeepAlive` was disabled after Phase 0 evidence capture so the next scheduled run cannot add another all-MISSING artifact or confuse attribution.
- A read-only-intended `python run_pipeline.py health --json` probe timed out after 120s. It emitted `catastrophic_drop_detected` and allowed a read command; logs showed SignalStore migration initialization, but a post-command DB copy still matched the original 4-row hash and mtime.

## Evidence matrix

| Surface | Status | Evidence |
|---|---|---|
| Raw DB hash identity | `hit` | Live frozen DB and known truncated backup share SHA-256 `447c1359918da1a2f4abf31867d3e21bd1b5f855ad9e5336ea5b9c3c98c5940e` and size `1466368`. |
| Repo DB ops ledger | `no hit for May 5-7 restore` | Ledger contains a successful `restore_db` entry against `signals.db` on `2026-04-29T07:55:35Z` from `signals.db.pre-step4b-promotion-20260404`; no May 5-7 repo-owned restore entry surfaced in the scanned ledger. |
| `scripts/restore_db.py` mtime mechanics | `weakens exact known-file restore mechanism` | The script uses `shutil.copy2`; restoring from the known truncated file would be expected to preserve the source mtime `2026-04-08T05:25:02Z`, not the observed live DB mtime `2026-05-05T22:33:37Z`. This weakens that specific mechanism but does not rule out another copy/restore source with identical bytes. |
| Task Scheduler metadata | `hit` | `HarmonicKeepAlive` exists. Captured task info shows last run `2026-05-07T19:51:44Z`, result `1`, next run `2026-05-08T15:00:00Z`. |
| Task Scheduler operational log window | `no hit` | Query for Harmonic/signals/keepalive messages around `2026-05-05T18:00Z` to `2026-05-06T06:00Z` returned no matching events. |
| `.a5c/runs` window | `no hit` | No files appeared in the private `.a5c/runs` window listing for `2026-05-05T18:00Z` through `2026-05-06T06:00Z`. |
| PowerShell history | `hit, not decisive` | Raw history was captured privately. Keyword scan had hits for `signals.db`, copy/move/remove terms, `run_pipeline.py`, `git checkout`, and `reset`, but PSReadLine history is not timestamped, so this is not attribution by itself. |
| `cmd` history | `no hit` | Current-process `doskey /history` capture did not provide attribution evidence. |
| WSL | `not inspected without mutation` | WSL is installed with stopped Ubuntu distro. Status/list were captured; crontabs were not read because doing so would start WSL and change the machine state. |
| Windows Object Access audit policy | `not available` | `auditpol` failed with required-privilege error. |
| Windows Security 4663 events | `no hit / limited` | Query found no matching 4663 events, but audit-policy availability could not be confirmed without privilege. |
| VSS | `blocked / potential gap` | `vssadmin list shadows /for=C:` still required a true Administrator shell after sandbox escalation. Shadow copies remain a plausible post-04-29 recovery surface until checked from elevated PowerShell. |
| File History | `not available` | File History service was stopped/manual; config listing did not identify an available restore source in this pass. |
| OneDrive | `no hit` | Project path `C:\dev\Harmonic` is not under the captured OneDrive root. |
| Notion mirror inventory | `bounded no hit after Apr 29` | Read-only inventory fetched 599 CRM pages, 15 with discovery-owned identity, 6 created since 2026-03-01, and 0 created or edited since 2026-04-29. |
| Health/DB guard probe | `partial` | `health --json` confirmed `catastrophic_drop_detected` for the truncated DB and allowed read commands. It did not prove write-command blocking, and the command timed out. |

## Current interpretation

The DB state strongly supports a file-level reversion or replacement to bytes matching the known truncated DB. The currently captured repo-owned ledger evidence does not support a May 5-7 canonical `restore_db.py` run against `signals.db`. The observed mtime also weakens the specific hypothesis that `scripts/restore_db.py` copied from `signals.db.pre-recovery-20260423-truncated`, because that script uses `shutil.copy2`.

The empty Task Scheduler operational-log window materially narrows attribution: the 22:33Z writer is unlikely to be `HarmonicKeepAlive` or another visible scheduled Harmonic task firing in that window. The live candidates now point more toward manual copy, external tool, non-scheduled script, or an unobserved scheduler surface.

Attribution remains unresolved because the absence of a repo-local ledger entry cannot prove that no external/manual copy or other non-ledgered writer touched the file.

## Containment and remaining gaps

- `HarmonicKeepAlive` is disabled as a containment measure.
- VSS is not cleared. It needs an Administrator PowerShell check before Phase 3 can say local shadow copies cannot help.
- The DB guard read path detected the catastrophic drop. A separate write-path proof is still needed before claiming the guard would block a collect/process writer on this truncated DB.
