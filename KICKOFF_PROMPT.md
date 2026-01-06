# Discovery Engine - Claude CLI Kickoff Prompt

Copy and paste this into Claude CLI to continue the project:

---

```
You are continuing development of Discovery Engine, a deal sourcing system for Press On Ventures (early-stage VC fund focused on Healthtech, Cleantech, AI Infrastructure - Pre-Seed to Seed+, $500K-$3M checks, US/UK focus).

## Project Context

This is an automated prospect discovery system that:
1. Collects signals from GitHub, Companies House, WHOIS, SEC EDGAR, etc.
2. Ranks prospects by thesis fit using multi-source verification
3. Pushes qualified deals to Notion CRM (their "Venture Pipeline" database)
4. Maintains suppression lists to avoid duplicate outreach

## Key Files in This Directory

### Core Implementation
- `connectors/notion_connector_v2.py` - Notion integration with CORRECT status strings:
  - Statuses: Source, Initial Meeting / Call, Dilligence (double L!), Tracking, Committed, Funded, Passed, Lost
  - Stages: Pre-Seed, Seed, Seed +, Series A, Series B, Series C, Series D
  - Uses canonical key deduplication for stealth companies

- `verification/verification_gate_v2.py` - Verification gate with:
  - Anti-inflation scoring (max one contribution per signal type)
  - Hard kill signals (company_dissolved → reject)
  - Correct routing: HIGH → "Source", MEDIUM → "Tracking", LOW → hold

- `utils/canonical_keys.py` - Multi-candidate canonical key generation:
  - Priority: domain > companies_house > crunchbase > pitchbook > github_org > github_repo > name_loc
  - `build_canonical_key_candidates()` returns ALL keys for dedupe
  - `is_strong_key()` determines if auto-merge is safe

### Documentation
- `docs/MCP_ARCHITECTURE.md` - Security policy, internal MCP server design
- `docs/PLUGINS_AGENTS_SKILLS.md` - Core 19 MCP servers, 10 agents, 10 skills
- `docs/EXTENDED_RECOMMENDATIONS.md` - Additional agents (due diligence, market intel, outreach)
- `docs/TOOL_REFERENCE_CARD.md` - Quick reference for all tools

## Architecture Decisions Made

1. **Internal MCP Server** - All external access goes through discovery-engine MCP (not direct DB/API access)
2. **Canonical Key System** - Deterministic dedupe that works for stealth companies
3. **Glass.AI Methodology** - Multi-source verification, full provenance tracking
4. **No MiroThinker** - Too expensive ($3.5K/month), adopted verification principle only
5. **Notion as CRM** - Simplified from Updog, direct integration

## Notion Properties to Add

The Venture Pipeline database needs these new properties:
- Discovery ID (Text) - stable link to Discovery Engine
- Canonical Key (Text) - e.g., "domain:acme.ai" or "companies_house:12345678"
- Confidence Score (Number) - 0.0-1.0 thesis fit
- Signal Types (Multi-select) - github_spike, incorporation, domain_registration, etc.
- Why Now (Text) - 1-sentence timing summary

## Next Priority Tasks

1. **Build internal discovery-engine MCP server** - Wrap all operations with validation
2. **Implement SEC EDGAR Form D collector** - Free API, shows funding before announcements
3. **Create .claude/agents/ directory** - Start with collector_specialist, ranking_specialist
4. **Create .claude/skills/ directory** - Start with signal_quality, thesis_matching
5. **Add schema preflight test** - Validate Notion properties before any operations

## Commands to Explore

- `cat docs/TOOL_REFERENCE_CARD.md` - See all planned tools at a glance
- `cat connectors/notion_connector_v2.py` - Review Notion integration
- `cat utils/canonical_keys.py` - Review canonical key system
- `python utils/canonical_keys.py` - Run canonical key tests

## What Would You Like to Work On?

Options:
1. Build the internal MCP server skeleton
2. Implement SEC EDGAR Form D collector
3. Create the .claude/agents/ and .claude/skills/ structure
4. Build a specific collector (GitHub, Companies House, WHOIS)
5. Set up the database schema (Postgres + Weaviate)
6. Something else from the roadmap

Please start by reading the relevant files for context, then propose a plan before implementing.
```

---

## Alternative: Shorter Version

If you prefer a more concise kickoff:

```
Continue Discovery Engine development for Press On Ventures (VC deal sourcing).

Key files:
- connectors/notion_connector_v2.py - Notion CRM integration
- verification/verification_gate_v2.py - Signal verification
- utils/canonical_keys.py - Deduplication system
- docs/TOOL_REFERENCE_CARD.md - Full tool inventory

Architecture: Internal MCP server wraps all external access. Canonical keys enable stealth company tracking. Multi-source verification before pushing to Notion.

Notion statuses (EXACT strings): Source, Initial Meeting / Call, Dilligence, Tracking, Committed, Funded, Passed, Lost

Next: Build internal MCP server, then SEC EDGAR collector, then .claude/agents/ structure.

Read docs/TOOL_REFERENCE_CARD.md first, then propose what to build.
```

---

## Usage

```bash
# Navigate to your project directory
cd ~/discovery-engine

# Start Claude CLI with the prompt
claude

# Then paste the kickoff prompt above
```

Or save as a file and use:

```bash
claude --prompt "$(cat KICKOFF_PROMPT.md)"
```
