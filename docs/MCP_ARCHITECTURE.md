# Discovery Engine: MCP & Agent Architecture

## Overview

This document defines the MCP server configuration, security policies, and agent architecture for the Discovery Engine.

**Key principle:** Minimize attack surface by wrapping operations in an internal MCP server rather than giving Claude direct access to databases/APIs.

---

## MCP Server Configuration

### Where Configs Live

| Environment | Config Location | Notes |
|-------------|-----------------|-------|
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` | Can import into Claude Code |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` | — |
| Claude Code | `.mcp.json` in project root | Project-specific |
| VS Code | `.vscode/mcp.json` | If using Copilot MCP |

### Our Project: `.mcp.json`

```json
{
  "mcpServers": {
    "discovery-engine": {
      "command": "python",
      "args": ["-m", "discovery_engine.mcp_server"],
      "env": {
        "DATABASE_URL": "${DATABASE_URL}",
        "NOTION_API_KEY": "${NOTION_API_KEY}",
        "NOTION_DATABASE_ID": "${NOTION_DATABASE_ID}"
      }
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-filesystem", "--allow-dir", "."],
      "env": {}
    }
  }
}
```

---

## Security Policy

### Threat Model

1. **Malicious MCP servers** can exfiltrate data via tool responses
2. **Prompt injection** from untrusted web content (if browser automation enabled)
3. **Credential sprawl** if each MCP server has its own API keys
4. **Over-privileged access** if read-write when read-only suffices

### Mitigations

#### 1. Allowlist MCP Servers

Only these MCP servers are approved:

| Server | Purpose | Risk Level | Approved |
|--------|---------|------------|----------|
| `discovery-engine` (internal) | All Discovery operations | Low (we control it) | ✅ |
| `@anthropic/mcp-server-filesystem` | Read project files | Low | ✅ |
| `@anthropic/mcp-server-postgres` | Direct DB access | Medium | ⚠️ Read-only mode only |
| Puppeteer/browser MCP | Web scraping | **High** | ❌ Not approved |
| Third-party Notion MCP | CRM access | Medium | ❌ Use internal server |

**Policy:** Any MCP server not on this list requires security review.

#### 2. Credential Isolation

| Credential | Access Level | Where Stored |
|------------|--------------|--------------|
| `DATABASE_URL` | Read-only for Claude Code | `.env` (not committed) |
| `DATABASE_URL_ADMIN` | Read-write for migrations | CI/CD only, never in `.env` |
| `NOTION_API_KEY` | Scoped to Venture Pipeline DB | `.env` |
| `GITHUB_TOKEN` | Public repo read only | `.env` |
| `COMPANIES_HOUSE_API_KEY` | Read-only | `.env` |

**Policy:** No write credentials in local development. All writes go through internal MCP server with validation.

#### 3. Content Sanitization

If we ever add browser automation (not recommended):
- **Domain allowlist:** Only `api.github.com`, `api.company-information.service.gov.uk`
- **Content limit:** Max 10KB per page
- **Strip HTML:** Return only text content
- **No JavaScript execution**

**Policy:** Prefer official APIs. Browser automation only as last resort with explicit approval.

---

## Internal MCP Server Design

Instead of giving Claude direct database/API access, expose safe operations:

```python
# discovery_engine/mcp_server.py

"""
Internal MCP server for Discovery Engine.

Exposes safe, validated operations as MCP prompts (slash commands).
All writes go through this server with proper validation.
"""

from mcp.server import Server
from mcp.server.stdio import stdio_server

server = Server("discovery-engine")

# =============================================================================
# PROMPTS (slash commands)
# =============================================================================

@server.prompt("run-collector")
async def run_collector(collector: str, dry_run: bool = True):
    """
    Run a signal collector.
    
    Usage: /mcp__discovery-engine__run-collector github --dry-run
    """
    if collector not in ALLOWED_COLLECTORS:
        return {"error": f"Unknown collector: {collector}"}
    
    # Import and run collector
    ...

@server.prompt("check-suppression")
async def check_suppression(domain: str = "", company_name: str = ""):
    """
    Check if a company is in the suppression list.
    
    Usage: /mcp__discovery-engine__check-suppression --domain acme.ai
    """
    ...

@server.prompt("push-to-notion")
async def push_to_notion(company_id: str, dry_run: bool = True):
    """
    Push a qualified prospect to Notion CRM.
    
    Usage: /mcp__discovery-engine__push-to-notion abc123 --dry-run
    """
    if dry_run:
        return {"status": "dry_run", "would_create": {...}}
    
    # Validate, then push
    ...

@server.prompt("explain-why-filtered")
async def explain_why_filtered(company_id: str):
    """
    Explain why a company was filtered out of results.
    
    Usage: /mcp__discovery-engine__explain-why-filtered abc123
    """
    ...

@server.prompt("sync-suppression-cache")
async def sync_suppression_cache():
    """
    Refresh suppression cache from Notion.
    
    Usage: /mcp__discovery-engine__sync-suppression-cache
    """
    ...

# =============================================================================
# TOOLS (for more complex operations)
# =============================================================================

@server.tool("search_founders")
async def search_founders(query: str, filters: dict = None):
    """Search founder watchlist"""
    ...

@server.tool("get_company_signals")
async def get_company_signals(company_id: str):
    """Get all signals for a company"""
    ...

@server.tool("get_ranking_explanation")
async def get_ranking_explanation(company_id: str):
    """Get detailed ranking breakdown"""
    ...


# =============================================================================
# MAIN
# =============================================================================

async def main():
    async with stdio_server() as streams:
        await server.run(
            streams[0],
            streams[1],
            server.create_initialization_options()
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Benefits

1. **Credential isolation:** Only the MCP server has write credentials
2. **Validation:** All inputs validated before DB/API calls
3. **Rate limiting:** Built into server, not spread across agents
4. **Audit logging:** Single point for all operations
5. **Safe defaults:** `dry_run=True` by default

---

## Subagent Architecture

### Approved Subagents

| Subagent | Purpose | Tool Access |
|----------|---------|-------------|
| **Collector Specialist** | Run signal collectors, validate data | `run-collector`, `check-suppression` |
| **Ranking Specialist** | Score and explain rankings | `search_founders`, `get_ranking_explanation` |
| **CRM Specialist** | Push to Notion, manage pipeline | `push-to-notion`, `sync-suppression-cache` |
| **SecOps Governor** | Manage MCP allowlists, audit access | `list-mcp-servers`, `check-credentials` |

### SecOps Governor (New)

This subagent doesn't exist in most setups but prevents security incidents:

```markdown
# .claude/agents/secops_governor.md

You are the SecOps Governor for Discovery Engine.

## Responsibilities
1. Maintain MCP server allowlist
2. Audit credential scope and rotation
3. Review new MCP server requests
4. Monitor for suspicious tool usage patterns

## Tool Access
- read-only filesystem access
- `list-mcp-servers` (internal)
- `check-credentials` (internal)
- NO write access to anything

## When to Invoke
- Before adding any new MCP server
- When reviewing credential changes
- During security audits
- If unusual tool usage patterns detected
```

---

## Skills vs MCP Prompts

**Skills** = Instruction bundles Claude loads and applies (good for "how to think")
**MCP Prompts** = Deterministic commands (good for "what to do")

| Task | Approach | Why |
|------|----------|-----|
| "How to evaluate a founder" | Skill | Needs judgment, context |
| "Run GitHub collector" | MCP Prompt | Deterministic command |
| "How to write a ranking explanation" | Skill | Output format guidance |
| "Push company X to Notion" | MCP Prompt | Action with validation |
| "What makes a good signal" | Skill | Domain knowledge |
| "Check if company in suppression" | MCP Prompt | Simple lookup |

### Our Skills (`.claude/skills/`)

```
.claude/skills/
├── founder_evaluation.md      # How to assess founder signals
├── thesis_matching.md         # How to match companies to investment thesis
├── signal_quality.md          # What makes a signal high-quality
└── ranking_explanation.md     # How to explain ranking decisions
```

### Our MCP Prompts (via internal server)

```
/mcp__discovery-engine__run-collector
/mcp__discovery-engine__check-suppression
/mcp__discovery-engine__push-to-notion
/mcp__discovery-engine__explain-why-filtered
/mcp__discovery-engine__sync-suppression-cache
```

---

## Observability

### Recommended: Sentry MCP (if using Sentry)

```json
{
  "mcpServers": {
    "sentry": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-server-sentry"],
      "env": {
        "SENTRY_AUTH_TOKEN": "${SENTRY_AUTH_TOKEN}",
        "SENTRY_ORG": "press-on-ventures"
      }
    }
  }
}
```

This lets Claude query job failures/alerts directly:
- "What collectors failed in the last 24 hours?"
- "Show me the error for job abc123"
- "What's the success rate for GitHub collector this week?"

---

## Migration from Direct Access

### Before (risky)

```
Claude → Postgres MCP (read-write) → Database
Claude → Notion MCP (third-party) → Notion
Claude → GitHub MCP → GitHub API
```

### After (secure)

```
Claude → Discovery Engine MCP Server → {
  Postgres (read-only for queries, validated writes)
  Notion API (scoped to Venture Pipeline)
  GitHub API (public repos only)
}
```

---

## Implementation Checklist

### Phase 1: Foundation
- [ ] Create internal MCP server skeleton
- [ ] Implement `check-suppression` prompt
- [ ] Implement `run-collector` prompt (dry-run only)
- [ ] Add to `.mcp.json`

### Phase 2: Full Operations
- [ ] Implement `push-to-notion` prompt
- [ ] Implement `sync-suppression-cache` prompt
- [ ] Add audit logging
- [ ] Add rate limiting

### Phase 3: Observability
- [ ] Add Sentry MCP (optional)
- [ ] Implement `explain-why-filtered` prompt
- [ ] Add SecOps Governor subagent

---

## References

- [Claude Code MCP Docs](https://code.claude.com/docs/en/mcp)
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code Skills](https://code.claude.com/docs/en/skills)
- [MCP Security Considerations](https://modelcontextprotocol.io/docs/concepts/security)
