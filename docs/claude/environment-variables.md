# Environment variables

Full catalog of environment variables used by the pipeline and integrations.

## Environment Variables Needed

```bash
NOTION_API_KEY=secret_xxx
NOTION_DATABASE_ID=xxx
DATABASE_URL=postgresql://... (read-only)
GITHUB_TOKEN=ghp_xxx (public repos only)
COMPANIES_HOUSE_API_KEY=xxx
PH_API_KEY=xxx (Product Hunt API key)
GOOGLE_API_KEY=xxx (Gemini - free at aistudio.google.com/apikey)
OPENCORPORATES_API_KEY=xxx (free tier at opencorporates.com/api_accounts/new)
DISCOVERY_DB_PATH=signals.db (default)

# Patent collector (required since legacy API retired May 2025)
PATENTSVIEW_API_KEY=xxx (request at patentsview.org/apis/keyrequest - rate limit 45 req/min)

# News collectors
GNEWS_API_KEY=xxx (free tier at gnews.io - 100 requests/day)
RSS_FEEDS=https://... (optional, comma-separated custom RSS feed URLs)
RSS_CATEGORIES=startup,health_tech,cpg (optional, filter feed categories)

# Website change monitoring (ABANDONED - use built-in monitoring/ instead)
# CHANGEDETECTION_URL=https://your-instance.local (not needed)
# CHANGEDETECTION_API_KEY=xxx (not needed)

# OpenAI Integration (for multi-LLM strategy iteration)
OPENAI_API_KEY=sk-xxx (get at platform.openai.com/api-keys)

# Startup config validation
STRICT_CONFIG_VALIDATION=false  # true = abort startup on config errors; false (default) = log and continue
```

## STRICT_CONFIG_VALIDATION Behavior

| STRICT_CONFIG_VALIDATION | Config errors present | Result |
|--------------------------|----------------------|--------|
| `false` (default) | yes | Log warnings, continue startup |
| `false` | no | Log info, continue startup |
| `true` | yes | Abort startup (RuntimeError in API / exit 1 in CLI) |
| `true` | no | Log info, continue startup |

Config validation runs automatically on both API and CLI startup. It checks:
- `DELIVERY_MODE` validity
- Confidence threshold bounds (0.0-1.0)
- Write feature env var values
- Notion key presence (errors when `DELIVERY_MODE` requires Notion)
