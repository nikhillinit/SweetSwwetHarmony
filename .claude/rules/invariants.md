# Rule: Core invariants and prohibitions

These constraints are always-on. Treat them as hard requirements.

## Notion contract (must match exactly)

## Critical: Notion Schema

**Statuses (EXACT strings - note the typo in Dilligence):**
- Source, Initial Meeting / Call, Dilligence, Tracking, Committed, Funded, Passed, Lost

**Stages:**
- Pre-Seed, Seed, Seed +, Series A, Series B, Series C, Series D

**New properties needed:**
- Discovery ID (Text)
- Canonical Key (Text) - e.g., "domain:acme.ai"
- Confidence Score (Number)
- Signal Types (Multi-select)
- Why Now (Text)

## Architecture invariants

## Architecture Rules

1. **All external access through internal MCP server** - No direct DB/API from Claude
2. **Canonical keys for dedupe** - Works for stealth companies without websites
3. **Multi-source verification** - 2+ sources = "Source", 1 source = "Tracking"
4. **Hard kill signals** - company_dissolved = immediate reject
5. **Schema preflight** - Validate Notion properties before operations

## Prohibitions

## Don't Do

- Don't give Claude write DB credentials - read-only only
- Don't add Puppeteer/browser MCP - security risk
- Don't skip schema preflight - catches drift early

## Operational guardrail

- When unsure about schema or routing, run schema preflight / health checks before pushing changes.
