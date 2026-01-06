"""
Internal MCP server for Discovery Engine.

Exposes safe, validated operations as MCP prompts (slash commands).
All external access goes through this server with proper validation.

Prompts:
  - run-collector: Run a signal collector
  - check-suppression: Check if a company is in the suppression list
  - push-to-notion: Push a qualified prospect to Notion CRM
  - sync-suppression-cache: Refresh suppression cache from Notion
  - validate-notion-schema: Validate Notion database schema matches expected structure

Usage:
  python -m discovery_engine.mcp_server
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors.notion_connector_v2 import (
    NotionConnector,
    ProspectPayload,
    ValidationResult,
)
from verification.verification_gate_v2 import (
    VerificationGate,
    PushDecision,
    ConfidenceBreakdown,
    Signal,
)
from utils.canonical_keys import (
    build_canonical_key,
    build_canonical_key_candidates,
    CanonicalKeyResult,
)
from workflows.suppression_sync import SuppressionSync
from storage.signal_store import SignalStore

# =============================================================================
# CONFIGURATION
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("discovery-engine-mcp")

# Allowed collectors - extend as new collectors are added
ALLOWED_COLLECTORS = frozenset({
    "github",
    "companies_house",
    "domain_registration",
    "domain_whois",
    "sec_edgar",
})


class CollectorStatus(str, Enum):
    """Status of a collector run."""
    SUCCESS = "success"
    DRY_RUN = "dry_run"
    ERROR = "error"
    NOT_FOUND = "not_found"


@dataclass
class CollectorResult:
    """Result from running a collector."""
    collector: str
    status: CollectorStatus
    signals_found: int = 0
    signals_new: int = 0
    signals_suppressed: int = 0
    dry_run: bool = True
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "status": self.status.value,
            "signals_found": self.signals_found,
            "signals_new": self.signals_new,
            "signals_suppressed": self.signals_suppressed,
            "dry_run": self.dry_run,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }


# =============================================================================
# MCP SERVER
# =============================================================================

server = Server("discovery-engine")

# Lazy-initialized connectors (created on first use)
_notion_connector: Optional[NotionConnector] = None
_verification_gate: Optional[VerificationGate] = None
_signal_store: Optional[SignalStore] = None


def get_notion_connector() -> NotionConnector:
    """Get or create the Notion connector instance."""
    global _notion_connector
    if _notion_connector is None:
        api_key = os.environ.get("NOTION_API_KEY")
        database_id = os.environ.get("NOTION_DATABASE_ID")
        if not api_key or not database_id:
            raise ValueError("NOTION_API_KEY and NOTION_DATABASE_ID must be set")
        _notion_connector = NotionConnector(
            api_key=api_key,
            database_id=database_id,
        )
    return _notion_connector


def get_verification_gate() -> VerificationGate:
    """Get or create the verification gate instance."""
    global _verification_gate
    if _verification_gate is None:
        _verification_gate = VerificationGate()
    return _verification_gate


async def get_signal_store() -> SignalStore:
    """Get or create the signal store instance."""
    global _signal_store
    if _signal_store is None:
        db_path = os.environ.get("SIGNAL_DB_PATH", "signals.db")
        _signal_store = SignalStore(db_path=db_path)
        await _signal_store.initialize()
    return _signal_store


# =============================================================================
# PROMPTS (slash commands)
# =============================================================================

@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """List all available prompts."""
    return [
        Prompt(
            name="run-collector",
            description="Run a signal collector to find new prospects",
            arguments=[
                PromptArgument(
                    name="collector",
                    description=f"Collector to run. Options: {', '.join(sorted(ALLOWED_COLLECTORS))}",
                    required=True,
                ),
                PromptArgument(
                    name="dry_run",
                    description="If true, don't persist results (default: true)",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="check-suppression",
            description="Check if a company is in the suppression list (already in CRM)",
            arguments=[
                PromptArgument(
                    name="domain",
                    description="Company domain (e.g., acme.ai)",
                    required=False,
                ),
                PromptArgument(
                    name="canonical_key",
                    description="Canonical key (e.g., domain:acme.ai)",
                    required=False,
                ),
                PromptArgument(
                    name="company_name",
                    description="Company name (fallback if no domain)",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="push-to-notion",
            description="Push a qualified prospect to Notion CRM",
            arguments=[
                PromptArgument(
                    name="discovery_id",
                    description="Internal discovery ID for the prospect",
                    required=True,
                ),
                PromptArgument(
                    name="dry_run",
                    description="If true, validate but don't create (default: true)",
                    required=False,
                ),
            ],
        ),
        Prompt(
            name="sync-suppression-cache",
            description="Refresh the local suppression cache from Notion",
            arguments=[],
        ),
        Prompt(
            name="validate-notion-schema",
            description="Validate Notion database schema matches expected structure",
            arguments=[
                PromptArgument(
                    name="force_refresh",
                    description="If true, bypass cache and fetch fresh schema (default: false)",
                    required=False,
                ),
            ],
        ),
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    """Handle prompt requests."""
    arguments = arguments or {}

    if name == "run-collector":
        return await _handle_run_collector(arguments)
    elif name == "check-suppression":
        return await _handle_check_suppression(arguments)
    elif name == "push-to-notion":
        return await _handle_push_to_notion(arguments)
    elif name == "sync-suppression-cache":
        return await _handle_sync_suppression_cache(arguments)
    elif name == "validate-notion-schema":
        return await _handle_validate_notion_schema(arguments)
    else:
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"Unknown prompt: {name}",
                    ),
                ),
            ],
        )


# =============================================================================
# PROMPT HANDLERS
# =============================================================================

async def _handle_run_collector(arguments: dict[str, str]) -> GetPromptResult:
    """
    Run a signal collector.

    Usage: /mcp__discovery-engine__run-collector collector=github dry_run=true
    """
    collector = arguments.get("collector", "").lower()
    dry_run = arguments.get("dry_run", "true").lower() in ("true", "1", "yes")

    # Validate collector
    if not collector:
        return _error_result("Missing required argument: collector")

    if collector not in ALLOWED_COLLECTORS:
        return _error_result(
            f"Unknown collector: {collector}. "
            f"Allowed: {', '.join(sorted(ALLOWED_COLLECTORS))}"
        )

    logger.info(f"Running collector: {collector} (dry_run={dry_run})")

    # Import collector module dynamically
    # NOTE: Collectors are not yet implemented - this is the skeleton
    try:
        result = await _run_collector_impl(collector, dry_run)
        return _success_result(
            f"Collector '{collector}' completed",
            result.to_dict(),
        )
    except NotImplementedError:
        return _success_result(
            f"Collector '{collector}' is not yet implemented",
            CollectorResult(
                collector=collector,
                status=CollectorStatus.NOT_FOUND,
                dry_run=dry_run,
                error_message="Collector not yet implemented",
            ).to_dict(),
        )
    except Exception as e:
        logger.exception(f"Error running collector {collector}")
        return _error_result(f"Collector error: {str(e)}")


async def _run_collector_impl(collector: str, dry_run: bool) -> CollectorResult:
    """
    Run the actual collector logic.

    This is a stub - implement actual collector logic here.
    Collectors should be in collectors/{name}.py
    """
    # Import and run collectors
    if collector == "github":
        from collectors.github import GitHubCollector
        return await GitHubCollector().run(dry_run=dry_run)
    elif collector == "sec_edgar":
        from collectors.sec_edgar import SECEdgarCollector
        return await SECEdgarCollector().run(dry_run=dry_run)
    elif collector == "companies_house":
        from collectors.companies_house import CompaniesHouseCollector
        return await CompaniesHouseCollector().run(dry_run=dry_run)
    elif collector in ("domain_whois", "domain_registration"):
        from collectors.domain_whois import DomainWhoisCollector
        # In enrichment mode, would pass domains from other signals
        # For now, run in discovery mode (will report unavailable)
        return await DomainWhoisCollector().run(dry_run=dry_run)

    raise NotImplementedError(f"Collector '{collector}' not yet implemented")


async def _handle_check_suppression(arguments: dict[str, str]) -> GetPromptResult:
    """
    Check if a company is in the suppression list.

    Usage: /mcp__discovery-engine__check-suppression domain=acme.ai
    """
    domain = arguments.get("domain", "").strip()
    canonical_key = arguments.get("canonical_key", "").strip()
    company_name = arguments.get("company_name", "").strip()

    if not domain and not canonical_key and not company_name:
        return _error_result(
            "At least one identifier required: domain, canonical_key, or company_name"
        )

    logger.info(
        f"Checking suppression: domain={domain}, "
        f"canonical_key={canonical_key}, company_name={company_name}"
    )

    try:
        connector = get_notion_connector()

        # Build candidates for lookup
        candidates: list[str] = []
        if canonical_key:
            candidates.append(canonical_key)
        if domain:
            # Also add normalized domain as canonical key
            candidates.append(f"domain:{domain.lower().replace('www.', '')}")

        # Check suppression
        result = await connector.check_suppression(
            canonical_key_candidates=candidates if candidates else None,
            website=f"https://{domain}" if domain else None,
        )

        return _success_result(
            "Suppression check complete",
            {
                "is_suppressed": result.is_suppressed,
                "suppression_type": result.suppression_type.value if result.suppression_type else None,
                "existing_status": result.existing_status,
                "existing_page_id": result.existing_page_id,
                "matched_on": result.matched_on,
                "query": {
                    "domain": domain,
                    "canonical_key": canonical_key,
                    "company_name": company_name,
                },
            },
        )
    except Exception as e:
        logger.exception("Error checking suppression")
        return _error_result(f"Suppression check error: {str(e)}")


async def _handle_push_to_notion(arguments: dict[str, str]) -> GetPromptResult:
    """
    Push a qualified prospect to Notion CRM.

    Usage: /mcp__discovery-engine__push-to-notion discovery_id=abc123 dry_run=true
    """
    discovery_id = arguments.get("discovery_id", "").strip()
    dry_run = arguments.get("dry_run", "true").lower() in ("true", "1", "yes")

    if not discovery_id:
        return _error_result("Missing required argument: discovery_id")

    logger.info(f"Push to Notion: discovery_id={discovery_id} (dry_run={dry_run})")

    try:
        connector = get_notion_connector()
        gate = get_verification_gate()

        # NOTE: This requires a prospect storage system to look up by discovery_id
        # For now, return a skeleton response showing what would happen

        # In production, this would:
        # 1. Look up prospect by discovery_id from internal storage
        # 2. Run through verification gate to get routing decision
        # 3. Push to Notion with appropriate status

        if dry_run:
            return _success_result(
                "Dry run - would push to Notion",
                {
                    "status": "dry_run",
                    "discovery_id": discovery_id,
                    "would_create": True,
                    "message": "Prospect lookup and verification gate integration pending",
                },
            )
        else:
            # When implemented:
            # prospect = await storage.get_prospect(discovery_id)
            # decision = gate.route(prospect.signals)
            # if decision.action == "push":
            #     result = await connector.upsert_prospect(prospect.to_payload())
            #     return _success_result("Pushed to Notion", result)

            return _error_result(
                "Live push not yet implemented - use dry_run=true for validation"
            )

    except Exception as e:
        logger.exception("Error pushing to Notion")
        return _error_result(f"Push error: {str(e)}")


async def _handle_sync_suppression_cache(arguments: dict[str, str]) -> GetPromptResult:
    """
    Refresh suppression cache from Notion using the SuppressionSync job.

    Usage: /mcp__discovery-engine__sync-suppression-cache
    """
    logger.info("Syncing suppression cache using SuppressionSync job")

    try:
        connector = get_notion_connector()
        store = await get_signal_store()

        # Run the suppression sync job
        sync = SuppressionSync(
            notion_connector=connector,
            signal_store=store,
            ttl_days=7,  # Default TTL
        )

        stats = await sync.sync(dry_run=False)

        # Return comprehensive stats
        return _success_result(
            "Suppression cache synced successfully",
            stats.to_dict(),
        )
    except Exception as e:
        logger.exception("Error syncing suppression cache")
        return _error_result(f"Cache sync error: {str(e)}")


async def _handle_validate_notion_schema(arguments: dict[str, str]) -> GetPromptResult:
    """
    Validate Notion database schema.

    Usage: /mcp__discovery-engine__validate-notion-schema force_refresh=true
    """
    force_refresh = arguments.get("force_refresh", "false").lower() in ("true", "1", "yes")

    logger.info(f"Validating Notion schema (force_refresh={force_refresh})")

    try:
        connector = get_notion_connector()

        # Run schema validation
        result = await connector.validate_schema(force_refresh=force_refresh)

        if result.valid:
            return _success_result(
                "Schema validation PASSED",
                {
                    "valid": True,
                    "timestamp": result.timestamp.isoformat(),
                    "message": "All required properties and select options are present",
                    "optional_missing": result.missing_optional_properties,
                },
            )
        else:
            return _success_result(
                "Schema validation FAILED",
                {
                    "valid": False,
                    "timestamp": result.timestamp.isoformat(),
                    "missing_properties": result.missing_properties,
                    "missing_optional_properties": result.missing_optional_properties,
                    "wrong_property_types": result.wrong_property_types,
                    "missing_status_options": result.missing_status_options,
                    "missing_stage_options": result.missing_stage_options,
                    "report": str(result),
                },
            )
    except Exception as e:
        logger.exception("Error validating schema")
        return _error_result(f"Schema validation error: {str(e)}")


# =============================================================================
# TOOLS (for more complex operations)
# =============================================================================

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_company_signals",
            description="Get all signals for a company by discovery ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "discovery_id": {
                        "type": "string",
                        "description": "Internal discovery ID",
                    },
                },
                "required": ["discovery_id"],
            },
        ),
        Tool(
            name="get_routing_decision",
            description="Get routing decision for a set of signals",
            inputSchema={
                "type": "object",
                "properties": {
                    "discovery_id": {
                        "type": "string",
                        "description": "Internal discovery ID",
                    },
                },
                "required": ["discovery_id"],
            },
        ),
        Tool(
            name="build_canonical_key",
            description="Build canonical key(s) from company identifiers",
            inputSchema={
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Company domain"},
                    "companies_house_number": {"type": "string", "description": "UK Companies House number"},
                    "crunchbase_id": {"type": "string", "description": "Crunchbase organization ID"},
                    "github_org": {"type": "string", "description": "GitHub organization name"},
                    "name": {"type": "string", "description": "Company name (fallback)"},
                    "location": {"type": "string", "description": "Company location (for name_loc fallback)"},
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""

    if name == "get_company_signals":
        # Placeholder - requires signal storage
        return [TextContent(
            type="text",
            text="Signal storage not yet implemented. "
                 "This tool will return all signals for a given discovery_id.",
        )]

    elif name == "get_routing_decision":
        # Placeholder - requires signal storage
        return [TextContent(
            type="text",
            text="Signal storage not yet implemented. "
                 "This tool will run signals through the verification gate.",
        )]

    elif name == "build_canonical_key":
        try:
            primary_key = build_canonical_key(
                domain_or_website=arguments.get("domain", ""),
                companies_house_number=arguments.get("companies_house_number", ""),
                crunchbase_id=arguments.get("crunchbase_id", ""),
                github_org=arguments.get("github_org", ""),
                fallback_company_name=arguments.get("name", ""),
                fallback_region=arguments.get("location", ""),
            )
            candidates = build_canonical_key_candidates(
                domain_or_website=arguments.get("domain", ""),
                companies_house_number=arguments.get("companies_house_number", ""),
                crunchbase_id=arguments.get("crunchbase_id", ""),
                github_org=arguments.get("github_org", ""),
                fallback_company_name=arguments.get("name", ""),
                fallback_region=arguments.get("location", ""),
            )
            key_type = primary_key.split(":")[0] if primary_key and ":" in primary_key else "none"
            has_strong = not primary_key.startswith("name_loc:") if primary_key else False
            return [TextContent(
                type="text",
                text=f"Primary key: {primary_key}\n"
                     f"Key type: {key_type}\n"
                     f"Has strong key: {has_strong}\n"
                     f"All candidates: {', '.join(candidates)}",
            )]
        except Exception as e:
            return [TextContent(type="text", text=f"Error building key: {str(e)}")]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# =============================================================================
# HELPERS
# =============================================================================

def _success_result(message: str, data: dict[str, Any]) -> GetPromptResult:
    """Create a success prompt result."""
    import json
    return GetPromptResult(
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"{message}\n\n```json\n{json.dumps(data, indent=2)}\n```",
                ),
            ),
        ],
    )


def _error_result(message: str) -> GetPromptResult:
    """Create an error prompt result."""
    return GetPromptResult(
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"Error: {message}",
                ),
            ),
        ],
    )


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Run the MCP server."""
    logger.info("Starting Discovery Engine MCP server")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
