# KG Build-Out Implementation Errata

**Date:** 2026-03-19 (updated 2026-03-20)
**Status:** Correction record for KG Phases 1-2 implementation

---

## Corrections to the implementation summary

### 1. Extractor count: 13 registry entries, 11 unique functions

The `EXTRACTOR_REGISTRY` in `kg_signal_extractors.py:411-425` contains **13 keys** mapping to **11 unique extractor functions**:
- `github` and `github_activity` both map to `extract_github`
- `news_api` and `rss_feeds` both map to `extract_news`

The summary said "13 extractors covering all collectors" which conflates registry entries with unique functions. Precise count: 13 source_api dispatch entries, 11 distinct extraction functions.

### 2. Edge tagging uses `properties.layer`, not `source_table`

Edges are scoped via `properties.layer = "signal_etl"` (`kg_signal_builder.py:333,355,372,407`), **not** via a `source_table` column (edges don't have one).

Nodes use **both** mechanisms:
- `kg_nodes.source_table = "signal_etl"` (column-level, used by `_tombstone_stale` via `list_live_node_ids_by_source`)
- `kg_nodes.properties.layer = "signal_etl"` (JSON property, for consistency)

The stale-edge expiration logic in `_expire_stale` (`kg_signal_builder.py:441`) queries `json_extract(properties, '$.layer') = 'signal_etl'`, not a column filter.

### 3. CLI surface area does change

The summary claimed "no runtime behavior changes." This is true for the **pipeline runtime** (no existing code paths are modified), but:
- `ops/cli.py:1669-1670` imports and registers `register_graph_commands` at CLI startup
- `graph_cli.py:35` adds 9 new subcommands to the `graph` namespace

These are purely **opt-in CLI commands** that must be explicitly invoked. They do not affect the pipeline's `full`, `process`, `sync`, or `health` paths. However, the CLI parser is modified, which is a factual change to the ops surface.

### 4. `source_table` column vs `properties.layer` JSON — dual tagging

The implementation uses two parallel scoping mechanisms:

| Scope mechanism | Where used | Query pattern |
|---|---|---|
| `kg_nodes.source_table = "signal_etl"` | Nodes only | `list_live_node_ids_by_source()`, `_list_company_nodes()` |
| `properties.layer = "signal_etl"` | Nodes + Edges | `json_extract(properties, '$.layer')` in `_expire_stale()` |

This dual tagging is intentional (matches the architecture builder's pattern) but should be documented so future readers understand both paths.

### 5. Node ID derivation does not use `utils/canonical_keys.py`

The original plan said node ID derivation reuses `utils/canonical_keys.py`. In practice, the builder uses `kg_node_id()` from `storage/kg_store.py:130` for signal and location nodes, and `company_id` directly from `company_files` for company nodes. `canonical_keys.py` is not imported by the builder.

### 6. Sector extraction scope is narrower than planned

The plan said `in_sector` edges come from `thesis_classifications` or `raw_data`. The builder only derives sectors from extractor output applied to `signals.raw_data` (`kg_signal_builder.py:293`). It does **not** read `thesis_classifications`. If thesis-aware sector assignment is desired, it would be additional scope.

### 7. Phase 2 query descriptions overclaim

- The plan said "no raw SQL." The engine uses `_list_company_nodes()` and `_list_nodes_by_type()` which execute direct SQL on `kg_nodes` (`kg_queries.py:71,91`). Graph traversal uses KGStore primitives, but initial node listing does not.
- `sector_cluster()` returns `signal_count` and `weight`, not "evidence strength" as a named field.
- `find_duplicate_candidates()` uses location-plus-shared-edge heuristics, not founder/investor matching (that is Phase 3B).

### 8. Scale expectations are stale

The DB currently has 612 signals and 503 live company_files, not 657/~380. Expected node/edge counts from the plan (~790 nodes, ~1700 edges) should **not** be used as acceptance gates. Post-window validation should capture actual counts from a dry-run.

### 9. Four of 10 live source_api values missing from extractor registry; job domain extraction broken

- **Issue**: 4 of 10 live `source_api` values (`greenhouse_jobs`, `ashby_jobs`, `lever_jobs`, `manual_seed_buzz`) not in `EXTRACTOR_REGISTRY`. Additionally, `extract_job_postings` read `raw_data["domain"]` but the `job_postings.py` collector writes `company_domain` (`job_postings.py:180,307`), silently breaking domain extraction for all ATS signals.
- **Root cause**: `extract_job_postings` read `domain` but collector writes `company_domain`. ATS variants (`greenhouse_jobs`, `ashby_jobs`, `lever_jobs`) and synthetic source (`manual_seed_buzz`) were never added to the registry. Fixture-based tests used the `domain` field, masking the mismatch.
- **Fix**: Normalize both `domain` and `company_domain` candidates, prefer `domain` over `company_domain`. Add ATS aliases to registry pointing to `extract_job_postings`. Add `extract_manual_seed` extractor for `manual_seed_buzz`. Coverage: 6/10 → 10/10 live sources.
- **Commit**: (same commit as this errata update)

---

## Observation window impact assessment

| Change | Pipeline impact | Window safe to commit? | Window safe to execute? |
|---|---|---|---|
| 3 new modules (`kg_signal_extractors.py`, `kg_signal_builder.py`, `kg_queries.py`) | None — not imported by pipeline | Yes | N/A (no execution path) |
| 3 new test files (154 tests) | None — test-only | Yes | Yes (in-memory DBs) |
| `graph_cli.py` modified (9 new subcommands) | CLI parser expanded, no pipeline path changes | Yes | **No** — `graph etl` writes to live DB |
| `ops/cli.py:1669` existing import unchanged | Already present from prior work | N/A | N/A |

**Verdict:** Safe to commit code + tests to the observation branch. **Not safe to invoke `graph etl` against the live `signals.db` during the window.** All ETL/query execution is post-window work against a snapshot copy.

### Phase 3A is a real workflow change

The plan describes Phase 3A (Notion enrichment) as an opt-in injectable add-on. In practice, `NotionPusher` has no enricher dependency injection point today (`notion_pusher.py:179`), and the payload build path (`notion_pusher.py:552`) would need modification. This is a **post-window milestone**, not a low-risk add-on.
