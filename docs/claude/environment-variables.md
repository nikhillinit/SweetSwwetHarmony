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

# News collectors
GNEWS_API_KEY=xxx (free tier at gnews.io - 100 requests/day)
RSS_FEEDS=https://... (optional, comma-separated custom RSS feed URLs)
RSS_CATEGORIES=startup,health_tech,cpg (optional, filter feed categories)

# Website change monitoring (ABANDONED - use built-in monitoring/ instead)
# CHANGEDETECTION_URL=https://your-instance.local (not needed)
# CHANGEDETECTION_API_KEY=xxx (not needed)

# OpenAI Integration (for multi-LLM strategy iteration)
OPENAI_API_KEY=sk-xxx (get at platform.openai.com/api-keys)
```
