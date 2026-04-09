# External Integrations

**Analysis Date:** 2026-04-07

## APIs & External Services

**LLM Classification (Thesis Filter):**
- Google Gemini (via google-genai SDK)
  - What it's used for: Consumer thesis classification (2-stage filter: keyword pre-filter + LLM)
  - SDK/Client: `google-genai>=1.0.0`
  - Auth: `GOOGLE_API_KEY` (free tier at `https://aistudio.google.com/apikey`)
  - Rate limit: 1.5M tokens/day, 15 RPM, 1500 RPD
  - Files: `consumer/thesis_filter/llm_classifier.py`, `ops/llm_classifier_v2.py`
  - Prompt version: `v1.6.0-employer-distribution-guard` (includes employer-distribution guard for 0.20-0.29 scores)
  - Mode control: `LLM_THESIS_MODE` env var (off/shadow/active), default=off (keyword-only)
  - Classification table: `llm_classifications` in DB stores audit trail

- OpenAI (optional fallback)
  - What it's used for: Multi-LLM consensus in Maestro workflow (forensic engineer pattern)
  - SDK/Client: `openai` SDK
  - Auth: `OPENAI_API_KEY` (get at `https://platform.openai.com/api-keys`)
  - Cost: ~$0.002/signal
  - Files: `integrations/openai_mcp.py`
  - Used only if explicitly invoked via Maestro CLI

- Kimi/Moonshot AI (alternative large-context LLM)
  - What it's used for: Forensic engineer workflow with large context windows
  - SDK/Client: Uses OpenAI SDK with Kimi endpoint
  - Auth: `KIMI_API_KEY` (get at `https://platform.moonshot.cn/console/api-keys`)
  - Cost: $0.60/M input, $2.50/M output
  - Models: kimi-k2.5, kimi-k2-thinking, moonshot-v1-128k (up to 256K tokens)
  - Files: `integrations/kimi_client.py`, `integrations/maestro.py`
  - Budget file: `.kimi_budget.json` (Tier0 daily limit: 1.5M tokens)

**CRM & Notion:**
- Notion API
  - What it's used for: CRM database, prospect management, suppression cache sync
  - SDK/Client: `notion-client>=2.0.0`
  - Auth: `NOTION_API_KEY`, `NOTION_DATABASE_ID` (required), `NOTION_INBOX_DATABASE_ID` (optional)
  - Schema contract (EXACT strings, enforced by `connectors/notion_connector_v2.py`):
    - **Statuses**: Source, Initial Meeting / Call, Dilligence, Tracking, Committed, Funded, Passed, Lost
    - **Stages**: Pre-Seed, Seed, Seed +, Series A, Series B, Series C, Series D
    - **Properties**: Discovery ID (Text), Canonical Key (Text), Confidence Score (Number 0.0-1.0), Signal Types (Multi-select), Why Now (Text), Sector (Text, free-form)
  - Files: `connectors/notion_connector_v2.py`, `connectors/notion_transport.py`, `workflows/notion_pusher.py`
  - Delivery modes: staging_only (default), manual_publish, batch_publish, auto_publish (controlled by `DELIVERY_MODE` env var)
  - Schema preflight: `verification/verification_gate_v2.py` validates Notion structure before pushes

**Data Sourcing Collectors (16 total):**

*Public APIs (no key required):*
- SEC EDGAR
  - Purpose: US Form D filings (pre-seed funding signals)
  - Collector: `collectors/sec_edgar.py`
  - API: SEC EDGAR public REST API
  - Signal strength: 0.6-0.8
  - No auth required

- Domain WHOIS Lookups
  - Purpose: Domain registration dates, registrar info (company discovery)
  - Collector: `collectors/domain_whois.py`
  - API: Public WHOIS services
  - Signal strength: 0.4-0.6
  - No auth required

- Job Postings (Greenhouse/Lever ATS)
  - Purpose: Public job board scraping (hiring velocity)
  - Collector: `collectors/job_postings.py`
  - API: Greenhouse and Lever public endpoints
  - Signal strength: 0.7-0.95
  - No auth required

- Hacker News
  - Purpose: Show HN launches, mentions in comments
  - Collector: `collectors/hacker_news.py`
  - API: HN API (public, no key)
  - Signal strength: 0.5-0.7
  - No auth required
  - Note: 100% false-positive rate with keyword-only (unclassified). Enable `LLM_THESIS_MODE=active` for LLM-based filtering.

- ArXiv
  - Purpose: Research papers in biotech, ML, materials science
  - Collector: `collectors/arxiv.py`
  - API: ArXiv public API
  - Signal strength: 0.3-0.5
  - No auth required

- RSS Feeds
  - Purpose: TechCrunch, PR Newswire, custom feeds
  - Collector: `collectors/rss_feeds.py`
  - API: RSS/Atom feed standards
  - Signal strength: 0.35-0.65
  - No auth required
  - Config: `RSS_FEEDS` (comma-separated URLs), `RSS_CATEGORIES` (startup, health_tech, cpg)

*APIs requiring keys (optional):*

- GitHub
  - Purpose: Trending repos with star/fork spikes (developer tool discovery)
  - Collector: `collectors/github.py`, `collectors/github_activity.py`
  - SDK/Client: httpx (no SDK, REST API via raw HTTP)
  - Auth: `GITHUB_TOKEN` (get at `https://github.com/settings/tokens`, scope: public_repo)
  - Rate limit: 5000 req/hour (public endpoint token auth)
  - Signal strength: 0.5-0.7
  - Status: ✅ Configured (as of 2026-01-30)

- SEC EDGAR (PostgreSQL read-only)
  - Purpose: Production filing data cache (optional, for volume queries)
  - Connector: Read-only credentials in `DATABASE_URL`
  - Auth: PostgreSQL read-only user
  - Note: Not used by default CLI (uses public SEC API)

- Companies House (UK Incorporations)
  - Purpose: UK company registration (Series A stage companies)
  - Collector: `collectors/companies_house.py`
  - Auth: `COMPANIES_HOUSE_API_KEY` (register at `https://developer.company-information.service.gov.uk/`)
  - Signal strength: 0.6-0.8
  - Status: ❌ Placeholder (as of 2026-01-30)

- Product Hunt
  - Purpose: Product launches and community feedback
  - Collector: `collectors/product_hunt.py`
  - Auth: `PH_API_KEY` (get at `https://api.producthunt.com/v2/oauth/applications`)
  - Signal strength: 0.5-0.7
  - Status: ❌ Missing (as of 2026-01-30)

- LinkedIn (via Proxycurl)
  - Purpose: Founder/employee activity, company info
  - Collector: `collectors/linkedin.py`
  - SDK/Client: httpx (Proxycurl REST API)
  - Auth: `PROXYCURL_API_KEY` (get at `https://nubela.co/proxycurl/`)
  - Signal strength: 0.5-0.8
  - Status: ❌ Missing (as of 2026-01-30)

- Crunchbase
  - Purpose: Funding rounds, investor networks
  - Collector: `collectors/crunchbase.py`
  - SDK/Client: httpx (Crunchbase REST API)
  - Auth: `CRUNCHBASE_API_KEY` (get at `https://data.crunchbase.com/docs/using-the-api`)
  - Signal strength: 0.6-0.9
  - Status: ❌ Missing (as of 2026-01-30)

- OpenCorporates (Global Incorporations)
  - Purpose: International company registration (worldwide startup discovery)
  - Collector: `collectors/opencorporates.py`
  - SDK/Client: httpx (OpenCorporates REST API)
  - Auth: `OPENCORPORATES_API_KEY` (free tier at `https://opencorporates.com/api_accounts/new`)
  - Signal strength: 0.6-0.75
  - Status: ❌ Missing (as of 2026-01-30)

- USPTO Patents (requires key since May 2025)
  - Purpose: Patent filings (hardware/biotech company discovery)
  - Collector: `collectors/uspto.py`
  - SDK/Client: httpx (PatentsView API, replaced legacy NASA API)
  - Auth: `PATENTSVIEW_API_KEY` (request at `https://patentsview.org/apis/keyrequest`, rate limit: 45 req/min)
  - Signal strength: 0.4-0.6
  - Status: ❌ Missing (as of 2026-01-30, mandatory since May 2025 API retirement)

- GNews (News API)
  - Purpose: Consumer news mentions, launches, funding news
  - Collector: `collectors/news_api.py`
  - SDK/Client: httpx (GNews REST API)
  - Auth: `GNEWS_API_KEY` (free tier at `https://gnews.io`, 100 requests/day)
  - Signal strength: 0.4-0.75
  - Status: ✅ Configured (as of 2026-01-30)

*Community/Deprecated:*

- Telegram (Community)
  - Purpose: Community growth signals
  - Collector: `collectors/telegram.py`
  - Auth: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
  - Status: Available (not commonly used)

- Discord (Community)
  - Purpose: Community growth signals
  - Collector: `collectors/discord.py`
  - Auth: `DISCORD_BOT_TOKEN`
  - Status: Available (not commonly used)

- Changedetection.io (Website Monitoring)
  - Purpose: Website change tracking (ABANDONED)
  - Collector: `collectors/changedetection.py`
  - Status: ⛔ Abandoned (use built-in `monitoring/` module instead, free, more features)

**Web Search & Research:**

- Tavily AI (Curated Discovery)
  - Purpose: Web search for company research (Phase 2)
  - SDK/Client: `tavily-python>=0.3.0`
  - Auth: `TAVILY_API_KEY` (get at `https://tavily.com`, free tier available)
  - Files: Used in Phase 2 curated discovery workflows

## Data Storage

**Databases:**

- SQLite (Primary)
  - Purpose: Signal storage, processing state, suppression cache, quality ops audit trail
  - Path: `signals.db` (configurable via `DISCOVERY_DB_PATH`, default in cwd)
  - Mode: WAL (Write-Ahead Logging) for concurrent access
  - Features: FTS5 full-text search, custom functions
  - Schema version: 53 (as of 2026-04-07)
  - Client: `aiosqlite>=0.19.0` (async access)
  - Tables:
    - `signals`: Raw signals from collectors
    - `signal_processing`: Processing state, Notion linkage, status tracking
    - `suppression_cache`: Local cache of Notion DB entries (avoids duplicates)
    - `schema_migrations`: Applied migration tracking
    - `llm_classifications`: LLM thesis classification audit trail
    - `audit_events`: Governance audit log (feature promotion, state changes)
    - `confidence_ledger`: Confidence score provenance tracking
    - `knowledge_graph`: Phase G entity relationships
  - File: `storage/signal_store.py` (schema manager)

- PostgreSQL (Optional)
  - Purpose: Production read-only replica, volume queries
  - Connection: `DATABASE_URL=postgresql://readonly:xxx@host:5432/discovery`
  - Auth: Read-only credentials only (enforced)
  - Used by: Optional SEC EDGAR collector variant
  - Status: Not actively used in CLI/API (SQLite primary)

**File Storage:**

- Local filesystem only
  - DB backup files (pre-upgrade snapshots): `signals.db.pre-*`
  - Artifacts: `artifacts/` directory (decision logs, monitoring checklists, plans)
  - No cloud storage integration detected

**Caching:**

- Suppression cache (in-memory + SQLite)
  - Purpose: Avoid duplicate Notion pushes
  - Refreshed by: `workflows/suppression_sync.py` / `python run_pipeline.py sync`
  - Table: `suppression_cache` in SQLite
  - Sync interval: Manual or scheduled (Phase 3 scheduler)

## Authentication & Identity

**Auth Provider:**

- Custom JWT-based (internal only)
  - Implementation: `api/auth/jwt_auth.py`
  - Seed users: Default users created on API startup (dev-only)
  - Scope: API endpoints only (not collectors or MCP server)
  - Algorithm: HS256 (via pyjwt)

- No external auth provider (e.g., Auth0, Okta)
- No OAuth2 required for API (JWT bearer tokens)

## Monitoring & Observability

**Error Tracking:**

- None detected (no Sentry, Rollbar, etc.)
- Error logging: Standard Python `logging` module
- Files: `utils/logging_config.py` configures structured logging

**Logs:**

- Console output (default)
- Structured JSON logging via custom formatter in `utils/logging_config.py`
- Log levels: DEBUG, INFO, WARNING, ERROR
- No centralized log aggregation detected

**Health Checks:**

- Built-in health CLI: `python run_pipeline.py health` / `python run_pipeline.py health --json`
- Checks: DB connectivity, API key presence, anomaly detection
- Files: Health check logic in `workflows/pipeline.py` and related modules

**Monitoring/SPC (Statistical Process Control):**

- Real-time monitoring: `monitoring/spc_monitor.py`
- Daily aggregation: `monitoring/daily_aggregator.py`
- Drift detection: `monitoring/drift_detector.py`
- Canary tracking: `monitoring/canary_checker.py`
- Activation gates: `monitoring/activation_gate.py`
- Config: `SPC_ZERO_VOLUME_ALERTING` (default=true), `SPC_MIN_BASELINE_DAYS`, `SPC_MIN_LABELED_PER_DAY`

## CI/CD & Deployment

**Hosting:**

- Docker-ready (Dockerfile pattern exists)
- Container env vars: `DISCOVERY_DB_PATH=/app/data/signals.db`
- No Kubernetes manifests detected

**CI Pipeline:**

- None detected (no GitHub Actions, Jenkins, CircleCI config committed)
- Optional: Babysitter orchestration available (`.a5c/` directory)

## Environment Configuration

**Required env vars:**

- `GOOGLE_API_KEY` — Gemini LLM (mandatory for thesis classification)
- `NOTION_API_KEY` — Notion API key (mandatory for CRM push)
- `NOTION_DATABASE_ID` — Notion database ID (mandatory for CRM push)
- `GITHUB_TOKEN` — GitHub API (optional, enables github collector)

**Optional env vars:**

- Collector keys: `COMPANIES_HOUSE_API_KEY`, `PH_API_KEY`, `PROXYCURL_API_KEY`, `CRUNCHBASE_API_KEY`, `OPENCORPORATES_API_KEY`, `GNEWS_API_KEY`, `PATENTSVIEW_API_KEY`
- LLM: `OPENAI_API_KEY`, `KIMI_API_KEY`
- Database: `DATABASE_URL` (PostgreSQL, read-only)
- Distribution: `RESEND_API_KEY`, `SMTP_*` vars
- Scheduler: For Phase 3, via `python -m ops.cli schedule create`
- PDF Privacy: `ALLOW_CLOUD_LLM`, `ALLOW_CLOUD_VISION` (default both false for NDA protection)
- Feature flags: `LLM_THESIS_MODE`, `V2_ENABLEMENT`, `DELIVERY_MODE`, `ML_ENABLEMENT`, various `*_ENABLED` flags

**Secrets location:**

- `.env` file (git-ignored, never committed)
- `.env.production.template` (template with placeholders)
- GitHub Actions secrets (if CI enabled)
- No secrets manager integration (e.g., AWS Secrets, HashiCorp Vault)

## Webhooks & Callbacks

**Incoming:**

- Notion webhooks (if configured externally)
  - Handled by: `connectors/notion_webhook_handler.py`
  - Purpose: Listen for Notion updates (e.g., deal status changes)
  - Not actively used (manual push mode primary)

**Outgoing:**

- Notion database pushes
  - Via: `connectors/notion_connector_v2.py` (ProspectPayload schema)
  - Trigger: Verification gate approval (confidence >= threshold)
  - Delivery modes: staging_only, manual_publish, batch_publish, auto_publish

- Email distribution
  - Via: `distribution/` module (Jinja2 templates)
  - Transport: Resend (production) or SMTP/console (dev)
  - Purpose: Weekly digest emails to LPs

- Slack (optional)
  - CI/CD notifications via `SLACK_WEBHOOK_URL`
  - Not configured by default

## Internal Services

**MCP Server (Internal Boundary):**

- Location: `discovery_engine/mcp_server.py`
- Purpose: Provide safe, validated operations as MCP prompts (slash commands)
- Prompts:
  - `run-collector`: Execute a signal collector
  - `check-suppression`: Check if company in suppression list
  - `push-to-notion`: Push qualified prospect to Notion
  - `sync-suppression-cache`: Refresh suppression cache from Notion
  - `validate-notion-schema`: Validate Notion database schema
- Usage: MCP interface with all external access validation

**Quality Ops CLI (16 subcommands):**

- Location: `ops/quality_cli.py`
- Commands: stats, label, find-patterns, export, thesis, tune, etc.
- Purpose: Off-line QA, pattern analysis, LLM classification tuning
- Usage: `python -m ops.cli quality <subcommand>`

**Governance CLI:**

- Location: `governance/state_policies.py`
- Purpose: Feature promotion, state management (two-lane policy system)
- Commands: register state, promote, demote, etc.
- Usage: `python -m governance.cli <command>`

## Collector HTTP Client

**Base Configuration:**

- Location: `collectors/http_client.py`
- Client: httpx (async-first)
- Retry logic: `tenacity` with exponential backoff
- Timeout config: `collectors/timeout_config.py` (per-operation timeouts)
- Rate limiter: `utils/rate_limiter.py` (per-API rate limits)
- Retry strategy: `collectors/retry_strategy.py` (RetryConfig dataclass)

---

*Integration audit: 2026-04-07*
