# Local restore candidates

Status: quarantine-only Phase 2B local scan.

All candidates below were inspected from private packet copies. The live DB path was represented only by the Phase 0 frozen copy.

## Summary

- Local SQLite candidates inspected: `24`
- Valid SQLite candidates with a `signals` table: `24`
- Small newest-content candidate: `artifacts/activation/hn_llm_active_rehearsal_2026-03-24.db`, `34` rows, newest `created_at` `2026-03-24T20:47:23.893484+00:00`, only `hacker_news` represented.
- Strongest baseline family: the `612`-row pre-Step-4B / restore-stage candidates with all four operational sources represented.
- Current frozen live DB and the known truncated backups have only `4` rows and no operational collector coverage.
- Important: the `612`-row baseline is a regression baseline, not a full restoration. It predates the working post-R19 ingest window visible in `state/collectors.json`.
- Private `state/collectors.json` recorded successful collector runs on `2026-05-05T16:43:54Z` through `2026-05-05T16:44:10Z`: `arxiv signals_new=98`, `hacker_news signals_new=10`, `rss_feeds signals_new=8`, and `news_api signals_suppressed=4`.

## Candidate table

| Candidate | Signals | Max created_at | Coverage | SHA-256 prefix | Restore posture |
|---|---:|---|---|---|---|
| `artifacts/activation/hn_llm_active_rehearsal_2026-03-24.db` | 34 | `2026-03-24T20:47:23.893484+00:00` | `hacker_news=34`, others `0` | `4ceb51c071ae` | Valid SQLite, but not a baseline corpus candidate. |
| `signals.db.pre-step4b-promotion-20260404` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `fcd06c6bda36` | Strong baseline candidate. |
| `signals.db.restore-stage-20260404T195300Z` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `fcd06c6bda36` | Strong baseline candidate; byte-identical to pre-Step-4B candidate. |
| `backups/signals-20260404-072102.db` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `0a9108a9bd6b` | Strong baseline candidate, different file hash. |
| `artifacts/activation/step4a_promotion_2026-03-16T19-05-16/signals-snapshot-2026-03-19.db` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `4cd422bc4e01` | Strong baseline candidate. |
| `signals.db.pre-step4a-promotion-20260316` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `69d7d33c1b13` | Strong baseline candidate. |
| `signals.db.backup-before-curated-backfill-20260303-022655` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `a52b1020f6b2` | Strong baseline candidate. |
| `signals.db.pre-labeling-campaign-20260304` | 612 | `2026-03-01T19:33:33.650304+00:00` | `hacker_news=192`, `arxiv=275`, `rss_feeds=87`, `news_api=14` | `a52b1020f6b2` | Strong baseline candidate; byte-identical to curated-backfill backup. |
| `current-live-frozen-copy` | 4 | `2026-01-10T12:18:09.035890+00:00` | all operational sources `0` | `447c1359918d` | Incident state, not recovery source. |
| `pre-restore-20260429-075534.db` | 4 | `2026-01-10T12:18:09.035890+00:00` | all operational sources `0` | `447c1359918d` | Truncated-state evidence, not recovery source. |
| `signals.db.pre-recovery-20260423-truncated` | 4 | `2026-01-10T12:18:09.035890+00:00` | all operational sources `0` | `447c1359918d` | Truncated-state evidence, not recovery source. |
| `signals.db.pre-restore-20260314` | 4 | `2026-01-10T12:18:09.035890+00:00` | all operational sources `0` | `447c1359918d` | Truncated-state evidence, not recovery source. |

## Recovery implication

No live restore has been performed. If no stronger remote/cloud/VSS/Notion delta source is validated, the 612-row pre-Step-4B family remains the strongest local baseline candidate, with `signals.db.pre-step4b-promotion-20260404` and `signals.db.restore-stage-20260404T195300Z` byte-identical.

That is not the same thing as recovery. Restoring to 612 rows would knowingly lose the post-R19 working window between the 2026-04-29 restore and the 2026-05-05T22:33Z reversion unless another source can reconstruct that delta.
