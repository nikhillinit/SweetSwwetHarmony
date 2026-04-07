# Directory Structure

**Analysis Date:** 2026-04-08
**Repository Root:** `C:\dev\Harmonic`

## Top-Level Layout

```
Harmonic/
├── api/                      FastAPI HTTP layer (routers, middleware, dependency injection)
├── collectors/               Pluggable signal collectors (16 sources) + base classes
├── connectors/               External system adapters (Notion v1/v2, OpenVC importer, etc.)
├── consumer/                 Consumer-thesis specific logic (negative keywords, sector models)
├── dashboard/                Streamlit/web UI for inbox triage and operator views
├── data/                     Runtime working data (NOT signals.db) — shadow data, dead letter, fixtures
├── datasets/                 Curated golden sets, benchmark cohorts, eval inputs
├── discovery_engine/         Internal MCP server boundary + bootstrap + scheduler
├── distribution/             Outbound delivery rails (Notion outbox, batch publisher tooling)
├── docs/                     All project docs (claude/, plans/, archive/, runbooks)
├── enrichment/               Async enrichment pipelines (brand sentiment, community metrics)
├── evaluation/               Tier-1/Tier-2 evaluators, golden-set scoring, benchmark runners
├── frontend/                 Frontend assets (templates, static files, JS where present)
├── governance/               Two-lane state policies, audit triggers, regret-check tooling
├── importers/                One-shot bulk importers (OpenVC, founder seed, etc.)
├── integrations/             Multi-LLM collaboration (maestro, codex, kimi), MCP integrations
├── intelligence/             Knowledge graph builders, entity resolution, claim facts
├── monitoring/               SPC monitor, daily aggregator, drift alerts, health checks
├── ops/                      Quality ops package (14 CLI subcommands), schedulers, CLI registration
├── plans/                    Active workspace plans (separate from docs/plans)
├── profilers/                User profile / behavioral profile generators
├── scripts/                  One-shot scripts (red-team-hybrid/, backfills, recovery, hooks)
├── services/                 Long-running background services (workers, schedulers)
├── storage/                  SQLite signal store + versioned migrations (v27-v51)
├── telemetry/                Telemetry aggregators, event log writers
├── tests/                    Pytest test suite (~9500 tests; mirrors source layout)
├── utils/                    Shared utilities (canonical_keys, thesis_matcher, http helpers)
├── verification/             Multi-source verification gates and rules
├── visualization/            Plot generation, KG visualizers, report rendering
├── workflows/                Pipeline orchestrator (`pipeline.py`), Notion pusher, suppression sync
├── .planning/                GSD planning artifacts (STATE, codebase/, intel/)
├── .claude/                  Claude Code config (hooks/, rules/, settings.json)
├── .a5c/                     Babysitter project profile + run journals
└── .worktrees/               Git worktrees for sandboxed feature work
```

## Key File Locations

### Entry Points
- **Pipeline CLI:** `run_pipeline.py` (root) — wraps subcommands via argparse
- **Quality ops CLI:** `python -m ops.cli quality <subcommand>` — registered in `ops/cli.py`
- **MCP server:** `discovery_engine/mcp_server.py`
- **API server:** `api/main.py` (or `api/app.py` — FastAPI app instance)

### Core Pipeline
- **Orchestrator:** `workflows/pipeline.py` (~2000+ lines, Move 4 refactor candidate per R12)
- **Signal store:** `storage/signal_store.py` — SignalStore + WAL/FTS5 management
- **Notion routing:** `workflows/notion_pusher.py` + `workflows/batch_publisher.py`
- **Suppression sync:** `workflows/suppression_sync.py`
- **Delivery policy:** `workflows/delivery_policy.py` (env-backed gating)

### Collectors
- **Base class:** `collectors/base.py` — `BaseCollector` with `_processed_identities` dedupe
- **Per-source:** `collectors/<source>.py` (e.g. `github.py`, `sec_edgar.py`, `arxiv.py`)
- **Helpers:** `collectors/http_client.py`, `collectors/retry_strategy.py`, `collectors/provenance.py`

### Storage
- **Schema migrations:** `storage/migrations/v27_audit_log.py` … `v51_confidence_ledger.py`
- **Migration runner:** `storage/migrations/cli.py` (`python -m storage.migrations`)
- **Quality tables DDL:** `storage/migrations/quality_tables.py` (single source of truth)

### Quality Ops Package
- **CLI registration:** `ops/cli.py` (parent) + `ops/quality_cli.py` (14 subcommands)
- **Modules:** `ops/quality/{labels,stats,patterns,tuning,thesis,export,...}.py`
- **Scheduler:** `ops/scheduler.py`

### Governance
- **State policies:** `governance/state_policies.py` — two-lane (env-backed + feature-registry)
- **Audit triggers:** Wired via `storage/migrations/v47_governance_triggers.py`
- **Regret-check tooling:** `scripts/governance/` and `governance/cli.py`

### Multi-LLM Integrations
- **Orchestrator:** `integrations/maestro.py` (collaborate + forensic_collaborate)
- **Codex wrapper:** `integrations/codex_wrapper.py`
- **Kimi client:** `integrations/kimi_client.py`

### Tests
- **Top-level:** `tests/` (mirrors source: `tests/collectors/`, `tests/workflows/`, etc.)
- **Co-located:** `<package>/tests/` (e.g. `workflows/tests/`, `storage/tests/`, `consumer/tests/`)
- **Root conftest:** `conftest.py` — registers asyncio markers + pytest_asyncio plugin
- **Sub-conftest:** `tests/storage/conftest.py`, `tests/connectors/conftest.py`, `tests/ops/quality/conftest.py`

### Configuration
- **Build/packaging:** `pyproject.toml` (setuptools, namespace packages)
- **Pytest:** `pytest.ini` (asyncio_mode=auto, strict markers, 6 testpaths)
- **Project deps:** `requirements.txt`
- **CLAUDE config:** `CLAUDE.md` (always-on) + `docs/claude/*.md` (on-demand)
- **Branch rules:** `.claude/rules/*.md` (invariants, api-key-coverage, plan-verification, session-start)

### Active Sprint Artifacts
- **Canonical plan:** `docs/plans/2026-04-06-red-team-hybrid/00-strategy.md`
- **Risk register:** `docs/plans/2026-04-06-red-team-hybrid/10-risk-register.md`
- **Move 0 charter:** `docs/plans/2026-04-06-red-team-hybrid/01-move-0-charter.md`
- **Protected paths guard:** `scripts/red-team-hybrid/check_protected_paths.sh`
- **Hooks:** `.claude/hooks/postedit_protected_paths.ps1`, `inject_context.ps1`, `stop_verify.ps1`

## Naming Conventions

### Files & Modules
- **Modules:** snake_case (`signal_store.py`, `notion_pusher.py`)
- **Test files:** `test_<unit>.py` or `<unit>_test.py` (per `pytest.ini` discovery patterns)
- **Migrations:** `v<NN>_<short_name>.py` (sequential, never renumbered)
- **CLIs:** `<package>/cli.py` or `<package>_cli.py`

### Classes & Functions
- **Classes:** CamelCase (`SignalStore`, `BaseCollector`, `DiscoveryPipeline`)
- **Functions/methods:** snake_case (`save_signal`, `process_pending`, `run_collectors`)
- **Constants:** UPPER_SNAKE (`DELIVERY_MODE`, `DEFAULT_DB_PATH`)

### Database
- **Tables:** snake_case singular or plural per pre-existing convention (`signals`, `signal_processing`, `audit_events`, `confidence_ledger`)
- **Columns:** snake_case (`canonical_key`, `detected_at`, `signal_type`)

## Where New Code Goes

| Type of work | Destination | Why |
|---|---|---|
| New collector | `collectors/<name>.py` | Subclass `BaseCollector`, register in pipeline |
| New thesis check | `consumer/` or `utils/thesis_matcher.py` | Sector logic stays in consumer/ |
| New CLI subcommand | `ops/quality_cli.py` (or appropriate `<area>_cli.py`) | Registered via argparse |
| New schema change | `storage/migrations/v<next>_<name>.py` | Sequential, never modify old migrations |
| New workflow stage | `workflows/<stage>.py` + wire in `workflows/pipeline.py` | Pipeline orchestrator owns ordering |
| New connector | `connectors/<system>_<version>.py` | Versioned (v2 etc.) when contracts change |
| New monitoring metric | `monitoring/spc_monitor.py` or `monitoring/daily_aggregator.py` | Centralized; SPC owns alerting |
| New test | `tests/<source_path>/test_<unit>.py` (mirror layout) | Match pytest.ini discovery |

## Worktrees & Sandboxes

The repo uses Git worktrees for sandboxed feature work (`.worktrees/`):
- `db-hardening-remaining-delta-sandbox/`
- `thesis-refresh-latest-sandbox/`
- `review-findings-followup-sandbox/`
- `gsd-integration/`

There is also a parallel `.worktree_notion/` checkout. Treat these as duplicates of root layout — `pyproject.toml`, `pytest.ini`, etc. exist inside each.

## Generated / Excluded From Source Layout

- `__pycache__/`, `*.egg-info/` — Python build artifacts
- `tmp/`, `tmp_kg_builder_tests/` — scratch
- `artifacts/` — pipeline run outputs (canary, promotion, monitoring checklists)
- `backups/` — DB backups (e.g. `signals.db.pre-step4a-promotion-20260316`)
- `models/` — trained model checkpoints (if any)
