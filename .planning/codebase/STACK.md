# Technology Stack

**Analysis Date:** 2026-04-07

## Languages

**Primary:**
- Python 3.11+ - Core discovery pipeline, collectors, processing workflows, MCP server, CLI tools

**Secondary:**
- JavaScript/TypeScript - Dashboard frontend (if present), Streamlit UI components
- SQL - Signal storage queries, schema migrations

## Runtime

**Environment:**
- Python 3.11+ (enforced via `pyproject.toml`: `requires-python = ">=3.11"`)
- asyncio - Async/await throughout entire codebase

**Package Manager:**
- pip (via requirements.txt)
- Lockfile: Not detected (pip-based, no poetry.lock or uv.lock)

## Frameworks

**Core:**
- MCP (Model Context Protocol) `>=1.0.0` - Internal server boundary at `discovery_engine/mcp_server.py` for validated external access
- FastAPI `>=0.109.0` - REST API backend (`api/main.py`, routers in `api/routers/`)
- Streamlit `>=1.30.0` - Dashboard UI (if dashboard module exists)

**Async HTTP:**
- httpx `>=0.25.0` - Async HTTP client for collectors
- aiohttp `>=3.9.0` - Alternative async HTTP library
- aiosqlite `>=0.19.0` - Async SQLite access

**Testing:**
- pytest `>=7.4.0` - Unit and integration test runner
- pytest-asyncio `>=0.21.0` - Async test support (asyncio_mode = auto in `pytest.ini`)
- respx `>=0.22.0` - Mock async HTTP responses

**Build/Dev:**
- setuptools `>=64` - Package discovery and installation

## Key Dependencies

**Critical:**

- **pydantic `>=2.0.0`** - Type validation, config models, request/response schemas
- **aiosqlite `>=0.19.0`** - Async SQLite access for signal storage (v53 schema, WAL mode, FTS5)
- **httpx `>=0.25.0`** - Async HTTP client for all 16 collectors (GitHub, SEC Edgar, Domain WHOIS, etc.)
- **tenacity `>=8.2.0`** - Retry logic with exponential backoff per-collector and per-API
- **python-dotenv `>=1.0.0`** - Environment variable loading (`.env` and `.env.production.template`)

**LLM & Classification:**

- **google-genai `>=1.0.0`** - Gemini LLM for thesis classification (unified SDK, replaces legacy `google-generativeai`)
  - Free tier: 1.5M tokens/day via `https://aistudio.google.com/apikey`
  - Used by `consumer/thesis_filter/llm_classifier.py` and quality ops via `GOOGLE_API_KEY`
  - Prompt version: `v1.6.0-employer-distribution-guard` (includes employer-distribution guard for ambiguous cases)
- **openai** (optional) - Multi-LLM consensus fallback (requires `OPENAI_API_KEY`)
  - Used by `integrations/openai_mcp.py`

**Infrastructure:**

- **notion-client `>=2.0.0`** - Notion SDK for CRM integration (`connectors/notion_connector_v2.py`)
  - Schema contract: Exact status strings (note typo: "Dilligence"), stages (Pre-Seed through Series D), properties (Discovery ID, Canonical Key, Confidence Score, Signal Types, Why Now, Sector)
  - Requires: `NOTION_API_KEY`, `NOTION_DATABASE_ID`, optional `NOTION_INBOX_DATABASE_ID`

**NLP & Entity Resolution:**

- **spacy `>=3.5.0`** - NER model for company/founder name extraction (`en_core_web_sm-3.7.1` model pinned)
- **scispacy `>=0.5.0`** - Medical entity extraction for health tech signals
- **tldextract `>=5.0.0`** - Domain extraction for canonical key v2 construction
- **rapidfuzz `>=3.0.0`** - Fuzzy string matching for identity resolution (Phase G)
- **metaphone `>=0.6` - Phonetic matching for founder names

**Data & ML:**

- **pandas `>=2.0.0`** - Dataframe operations for signal aggregation and analysis
- **scikit-learn `>=1.3.0`** - ML thesis classifier (supervised false-negative rescue)
- **joblib `>=1.3.0`** - Model serialization for sklearn classifiers

**Content Extraction:**

- **trafilatura `>=1.6.0`** - HTML to text extraction for URL profiler
- **parsel `>=1.8.0`** - CSS/XPath selectors for monitoring content pipeline
- **inscriptis `>=2.5.0`** - HTML to text with layout preservation
- **extruct `>=0.17.0`** - Structured data extraction (JSON-LD, microdata, OpenGraph)
- **pymupdf `>=1.23.0`** - PDF profiler (Phase 1, local extraction)
- **pdfplumber `>=0.11.0`** - PDF text/table extraction

**Monitoring & Scheduling:**

- **croniter `>=2.0.0`** - Cron expression parsing for scheduler (Phase 3)
- **python-dateutil `>=2.8.0`** - Date parsing for WHOIS and collector timestamps

**API & Web Search:**

- **tavily-python `>=0.3.0`** - Curated discovery web search (Phase 2)

**Web Framework:**

- **uvicorn[standard] `>=0.27.0`** - ASGI server for FastAPI
- **fastapi `>=0.109.0`** - REST API framework
- **pyjwt `>=2.8.0`** - JWT auth for API endpoints
- **python-multipart `>=0.0.6`** - Form/multipart data parsing

**Observability & Distribution:**

- **jinja2 `>=3.1.0`** - Email template rendering for digest distribution
- **resend `>=2.0.0`** - Email transport service (optional, default: console)
- **rich `>=13.0.0`** - Terminal output formatting (CLI, dashboards)
- **altair `>=5.0.0`** - Data visualization for Streamlit dashboard

**Evaluation & Collaboration:**

- **inspect-ai `>=0.3.0`** - Evaluation framework for thesis classifier benchmarking
- **inspect-flow `>=0.1.0`** - Flow control for evaluation experiments

**Config Management:**

- **pyyaml `>=6.0` - Policy file parsing (Phase 0A: negative keyword policies v2)

## Configuration

**Environment:**

- Configuration via `.env` file (not committed, `.gitignore` excludes `*.env*`)
- Template: `.env.example` documents all variables with examples and ranges
- Default: `DISCOVERY_DB_PATH=signals.db` (SQLite, relative to cwd)
- Validation: `utils/config_validator.py` runs on API/CLI startup
  - Strict mode: `STRICT_CONFIG_VALIDATION=true` aborts on config errors
  - Default: `STRICT_CONFIG_VALIDATION=false` (logs warnings, continues)

**Build:**

- No separate build config (pure Python, no compilation)
- Package discovery: `setuptools` with namespace packages in `pyproject.toml`
- Test config: `pytest.ini` with asyncio_mode=auto, testpaths=(tests, collectors, consumer/tests, storage/tests, etc.)

**Schema Migration:**

- Single source of truth: `storage/migrations/quality_tables.py` (DDL for quality tables)
- Schema versioning: `CURRENT_SCHEMA_VERSION = 53` in `storage/signal_store.py`
- Migrations: MIGRATIONS dict in `signal_store.py` applied in order
- v51: confidence_ledger (latest as of 2026-04-07)
- v50: knowledge_graph
- v40: merge_lifecycle

## Platform Requirements

**Development:**

- Windows 11 (primary development platform)
- Python 3.11+
- Git (worktrees used for branch isolation: `.worktrees/` directory)
- SQLite 3 (bundled with Python)
- API keys for: Notion, Google (Gemini), GitHub (optional others)

**Production:**

- Docker support (`.env` references `DISCOVERY_DB_PATH=/app/data/signals.db` for container)
- Optional PostgreSQL (read-only credentials in `DATABASE_URL`, not used in CLI by default)
- Async event loop capable (asyncio-first design)
- 3.11+ runtime

**External Services (not local):**

- Google AI Studio (Gemini LLM) — mandatory for thesis classification
- Notion API — mandatory for CRM push
- GitHub API — optional (rate limited, public repos only)
- 15 other optional collectors (Companies House, Product Hunt, SEC Edgar, etc.)

---

*Stack analysis: 2026-04-07*
