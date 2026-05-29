# Discovery Engine

## 10-Line Briefing
1. Consumer deal-sourcing engine for Press On Ventures (Pre-Seed → Series A, US/UK).
2. Collects multi-source signals (GitHub, filings, domains, jobs, launches, news, etc.).
3. Filters for thesis fit using keyword pre-filter + Gemini LLM classification.
4. Dedupes via canonical keys; supports stealth companies.
5. Routes prospects to Notion CRM with confidence-based status logic.
6. Maintains suppression cache to avoid repeats.
7. Uses multi-source verification gates to reduce false positives.
8. All external access goes through the internal MCP server.
9. Schema preflight is mandatory before Notion operations.
10. Monitoring/health checks protect signal quality and pipeline reliability.

## Active Work

- **Current context:** see `@docs/claude/active-sprint.md` for the refreshed operational handoff, rebuilt on 2026-05-29 from live `main` / `origin/main` at `2a71d02`.
- **Hermes Track A:** merged through PR #233 (`fix: harden hermes low-risk edges`); no GitHub PRs were open at the last live refresh. Treat older PR #191/#192 notes as historical, not current active work.
- **Dirty checkout caution:** the primary checkout often has local state, `.omx` plans, and keepalive artifacts. Do not reset, clean, stage, or commit that dirt; use fresh `.worktrees/...` lanes from `origin/main` for new slices and stage exact paths only.
- **Session-start rule:** run `git fetch origin main --prune`, check `git status --short --branch`, and inspect relevant PR state before acting. This repo often has local artifacts and branch drift; do not infer active work from stale sprint text.
- **Hermes code work:** no next PR10/code slice is named by current evidence. Future Hermes code changes must begin from fresh live discovery of the registry, CLI, emitted artifacts, and PR state.

## Investment Thesis
**One-liner:** Press On Ventures invests in Pre-Seed to Series A consumer companies in CPG (food, beverage, beauty), health tech (fitness, wellness, mental health), travel & hospitality, and consumer marketplaces — excluding B2B, enterprise SaaS, developer tools, crypto, and hardware.

**Categories:** Consumer CPG • Consumer Health Tech • Travel & Hospitality • Consumer Marketplaces
**Exclusions:** B2B/Enterprise (including tools sold to consumer industries) • developer tools • crypto/Web3 • cleantech/climate • services/agencies • Series B+ • hardware-only

## Routing Logic
```
HIGH confidence (0.7+) + multi-source → Status: "Source"
MEDIUM confidence (0.4-0.7) → Status: "Tracking"
LOW confidence (<0.4) → Don't push (hold for batch review)
Hard kill signal → Reject entirely
```

## Critical Files
- `run_pipeline.py` — Main CLI entrypoint
- `workflows/pipeline.py` — Pipeline orchestrator
- `workflows/notion_pusher.py` — Notion routing + batch push
- `workflows/suppression_sync.py` — Notion → local suppression
- `storage/signal_store.py` — Signal storage + cache
- `discovery_engine/mcp_server.py` — Internal MCP server boundary
- `connectors/notion_connector_v2.py` — Notion integration (v2)
- `ops/quality_cli.py` — Quality ops CLI registration (14 subcommands)
- `ops/quality/` — Quality ops package (labels, stats, patterns, tuning, thesis, export)
- `storage/migrations/quality_tables.py` — Quality tables DDL (single source of truth)
- `.github/workflows/discovery-pipeline.yml` — Daily Pipeline workflow, including DB artifact and watermark persistence
- `utils/db_guard.py` — External production DB signal-count watermark guard
- `integrations/hermes/` — Hermes routing, gates, locks, ledger, providers, adapters, and run orchestration
- `ops/hermes_cli.py` — Hermes CLI registration under `python -m ops.cli hermes`
- `docs/runbooks/hermes.md` — Hermes operator runbook

## Quick Commands
```bash
python run_pipeline.py full --collectors github,sec_edgar --dry-run
python run_pipeline.py process --dry-run
python run_pipeline.py sync
python run_pipeline.py health --json

# Quality ops
python -m ops.cli quality stats --db signals.db --days 30
python -m ops.cli quality label 123 FP --reason "B2B SaaS"
python -m ops.cli quality find-patterns --days 30 --out patterns.json
python -m ops.cli quality export --days 90 --format csv --out dataset.csv

# Quality ops with LLM classification
LLM_THESIS_MODE=active python run_pipeline.py full --collectors github
LLM_THESIS_MODE=shadow python run_pipeline.py process

# Quality scheduler management
python -m ops.cli schedule create quality-sync --cron "0 */6 * * *"
python -m ops.cli schedule create quality-classify --cron "0 2 * * *"
python -m ops.cli schedule create quality-patterns --cron "0 3 * * 0"

# Daily pipeline DB guard
python run_pipeline.py init-watermark

# Hermes
python -m ops.cli hermes route --json --phase production --task "fix thesis filter"
python -m ops.cli hermes run --dry-run --phase production --task "schema migration"
python -m ops.cli hermes providers doctor --json
```

## Reference Docs

### Always-on (via `.Codex/rules/`)
- `invariants.md` — Core constraints and prohibitions
- `api-key-coverage.md` — Auto-update rule for API key status

### On-demand (load when needed)

Deep reference material lives in `docs/claude/` and is **not** loaded by default.

When needed for a task, temporarily paste one or more import lines near the top of this file (not inside a code block), e.g.:

- @docs/claude/collectors-reference.md
- @docs/claude/environment-variables.md
- @docs/claude/cli-commands.md
- @docs/claude/codex-collaboration.md

After the task, remove those lines (or run `git restore AGENTS.md`) so they don't become always-on.

### Archive
- `docs/archive/sprint-history.md` — Historical sprint notes


## gstack

Installed at `~/.Codex/skills/gstack/` (33 skills). **For all web browsing, use the gstack `/browse` skill — never use `mcp__claude-in-chrome__*`.** Skill index + commands: `~/.Codex/skills/gstack/README.md`. For current work, prefer `/investigate` for state mysteries and `/careful` for high-stakes pipeline or Hermes changes. The old Move 0 `/freeze`/`/guard`/`/unfreeze` protected-path flow is not active unless a task explicitly reopens the April red-team plan.


## API Key Coverage (Auto-Updated)

> **Last verified:** 2026-01-30
> **Rule:** `.Codex/rules/api-key-coverage.md` - Codex auto-updates this section when keys change

### Core Services (Required)
| Key | Status | Collector/Service | Impact if Missing |
|-----|--------|-------------------|-------------------|
| GOOGLE_API_KEY | ✅ Configured | LLM thesis classification | No LLM classification (keyword-only) |
| NOTION_API_KEY | ✅ Configured | CRM integration | Cannot push to Notion |
| NOTION_DATABASE_ID | ✅ Configured | CRM database | Cannot push to Notion |
| GITHUB_TOKEN | ✅ Configured | github.py, github_activity.py | Rate limited to 60 req/hr |

### Optional Services
| Key | Status | Collector/Service | Impact if Missing |
|-----|--------|-------------------|-------------------|
| OPENAI_API_KEY | ✅ Configured | Multi-LLM consensus | Maestro unavailable |
| COMPANIES_HOUSE_API_KEY | ❌ Placeholder | companies_house.py | UK incorporations disabled |
| PH_API_KEY | ❌ Missing | product_hunt.py | Product Hunt disabled |
| PROXYCURL_API_KEY | ❌ Missing | linkedin.py | LinkedIn collector disabled |
| CRUNCHBASE_API_KEY | ❌ Missing | crunchbase.py | Crunchbase collector disabled |
| OPENCORPORATES_API_KEY | ❌ Missing | opencorporates.py | Global incorporations disabled |
| GNEWS_API_KEY | ✅ Configured | news_api.py | News API enabled |
| PATENTSVIEW_API_KEY | ❌ Missing | uspto.py | USPTO patents disabled (API key required since May 2025) |
| CHANGEDETECTION_API_KEY | ⛔ ABANDONED | changedetection.py | Use built-in `monitoring/` instead (free, more features) |
| SLACK_WEBHOOK_URL | ❌ Missing | CI/CD notifications | No Slack alerts |

### Collectors Working Without Keys
These collectors work without API keys:
- `sec_edgar.py` - SEC EDGAR (public API)
- `domain_whois.py` - WHOIS lookups (public)
- `job_postings.py` - Greenhouse/Lever (public)
- `hacker_news.py` - HN API (public)
- `arxiv.py` - ArXiv (public)
- `rss_feeds.py` - RSS feeds (public)

### Collector Availability Summary
```
Fully operational:  6 collectors (no key needed)
Configured:         3 collectors (github, github_activity, news_api)
Disabled:           6 collectors (missing keys, includes uspto)
Abandoned:          1 collector (changedetection - use built-in monitoring/)
─────────────────────────────────────────────
Total:             16 collectors
Working:            9 collectors (56%)
```

---

## Babysitter

### Overview
Babysitter is configured for this project with semi-autonomous mode. It orchestrates complex multi-step workflows with event-sourced state management and human-in-the-loop approval at key decision points.

### Quick Commands
```bash
# Run babysitter project setup (re-run to update profile)
/babysitter:project-install

# Orchestrate a development task
/babysitter:babysit

# Plan a babysitter run
/babysitter:plan

# Resume a paused run
/babysitter:resume
```

### Installed Processes
- `cradle/project-install` — Project onboarding and profile setup
- `gsd/execute` — Execute implementation tasks with quality gates
- `gsd/verify` — Verification before marking work complete
- `gsd/plan` — Plan-driven development workflow
- `gsd/iterative-convergence` — Iterative refinement loops

### Methodology
**TDD with Evolutionary Architecture** — Enforces test-first discipline while allowing schema and module structure to evolve incrementally. Matches the project's migration-heavy development pattern.

### CI/CD Integration
Babysitter is configured to trigger on **PR events** via GitHub Actions. Workflow file creation deferred — activate by creating `.github/workflows/babysitter.yml` when ready.

### Project Profile
- Location: `.a5c/project-profile.json`
- Readable version: `.a5c/project-profile.md`
- Quality gates: `.a5c/quality-gates.json`

### Conventions Enforced
- Conventional commit format (feat/fix/docs/test/ci/chore)
- Auto-delete merged branches (branch cleanup)
- Minimum test coverage thresholds on new code
- Existing governance lint ratchets preserved
