# Rule: API Key Coverage Auto-Update

## Trigger
When ANY of the following occurs:
1. User mentions adding, removing, or changing an API key
2. User says a key is expired, invalid, or needs updating
3. User provides a new API key value
4. Claude reads `.env` and notices a discrepancy with CLAUDE.md coverage section
5. A collector fails due to missing/invalid API key

## Action
1. **Read `.env`** to get current key status
2. **Update CLAUDE.md** section "API Key Coverage (Auto-Updated)":
   - Change status from ❌ to ✅ (or vice versa)
   - Update the "Last verified" date
   - Recalculate the Collector Availability Summary counts
3. **If adding a key to `.env`**: Add it in the appropriate section with a comment

## Key Locations in .env
```
# Core LLM/CRM
GOOGLE_API_KEY, OPENAI_API_KEY, NOTION_API_KEY, NOTION_DATABASE_ID

# Collectors requiring keys
GITHUB_TOKEN, COMPANIES_HOUSE_API_KEY, PH_API_KEY, PROXYCURL_API_KEY,
CRUNCHBASE_API_KEY, OPENCORPORATES_API_KEY, GNEWS_API_KEY,
CHANGEDETECTION_API_KEY, CHANGEDETECTION_URL

# CI/CD
SLACK_WEBHOOK_URL
```

## Status Definitions
- **✅ Configured** = Key exists in .env with a real value (not "xxx" or placeholder)
- **❌ Placeholder** = Key exists but has "xxx" or obvious placeholder
- **❌ Missing** = Key not present in .env at all

## No User Intervention Required
Claude should:
- Proactively check `.env` when key-related topics arise
- Update CLAUDE.md without asking for permission
- Inform user of changes made

This eliminates the need for users to inform Claude about key availability.
