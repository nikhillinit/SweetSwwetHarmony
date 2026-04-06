# scripts/data

Local data files used by Phase 0 scripts.

## founder_watchlist_manual_seed.csv

Manual fallback for `scripts/build_founder_watchlist.py` when the production
`founders` table is empty.

**Current state (2026-04-06):** the production `founders` and `founder_signals`
tables are empty (0 rows). Until they are populated, the GH negative-space
shadow collector cannot run, because it refuses to issue unbounded GitHub
API calls without a bounded watchlist.

**To populate:** add one row per founder you want the GH negative-space
collector to watch. Columns:

| Column | Required | Description |
|---|---|---|
| `founder_id` | yes | unique stable ID, e.g. `manual_001` |
| `full_name` | yes | human-readable name |
| `github_username` | yes | GitHub handle (no `@`) |
| `linkedin_url` | no | LinkedIn profile URL if known |
| `associated_company_id` | no | link to a company in `company_files` if known |

**Cap:** 500 rows. The GH API at 5000 req/hr authenticated × 40% budget
= 2000 req/hr. At ~3 calls per founder per scan, 500 founders fits one
scan in ~45 minutes.
