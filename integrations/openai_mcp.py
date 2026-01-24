"""
OpenAI MCP Server Integration for Discovery Engine.

This module provides an MCP server that bridges Claude Code with OpenAI's APIs,
enabling multi-LLM strategy iteration using your ChatGPT Pro subscription.

Architecture:
- Claude Code acts as the primary orchestrator
- OpenAI/Codex provides alternative perspectives via MCP tools
- Consensus patterns reduce hallucinations in strategy development

Usage:
    # As standalone MCP server
    python -m integrations.openai_mcp

    # Programmatically
    from integrations.openai_mcp import OpenAIMCPServer
    server = OpenAIMCPServer()
    await server.run()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
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
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# =============================================================================
# CONFIGURATION
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("openai-mcp")

# Default models for different tasks
DEFAULT_MODELS = {
    "strategy": "gpt-4o",  # Best for strategy/reasoning
    "code_review": "gpt-4o",  # Best for code analysis
    "quick": "gpt-4o-mini",  # Fast, cheaper for simple queries
}

# Reasoning effort levels (for o1/o3 models when available)
class ReasoningLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"  # Maximum reasoning capability


@dataclass
class OpenAIResponse:
    """Response from OpenAI API."""
    content: str
    model: str
    usage: dict[str, int]
    finish_reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "timestamp": self.timestamp,
        }


# =============================================================================
# OPENAI CLIENT
# =============================================================================

class OpenAIMCPServer:
    """
    MCP server providing OpenAI/Codex integration for strategy iteration.

    This server exposes:
    - Prompts (slash commands) for common operations
    - Tools for structured API access

    The server uses your OPENAI_API_KEY environment variable.
    With ChatGPT Pro, many operations have reduced/no incremental cost.
    """

    def __init__(self, api_key: Optional[str] = None):
        if not OPENAI_AVAILABLE:
            raise ImportError(
                "OpenAI package not installed. Run: pip install openai"
            )

        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable required. "
                "Get your key at: https://platform.openai.com/api-keys"
            )

        self.client = AsyncOpenAI(api_key=self.api_key)
        self._server: Optional[Server] = None

    async def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> OpenAIResponse:
        """
        Send a chat completion request to OpenAI.

        Args:
            prompt: User message/question
            system_prompt: Optional system context
            model: Model to use (default: gpt-4o)
            temperature: Creativity level 0-2 (default: 0.7)
            max_tokens: Max response length (default: 4096)

        Returns:
            OpenAIResponse with content and metadata
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        choice = response.choices[0]
        return OpenAIResponse(
            content=choice.message.content or "",
            model=response.model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            finish_reason=choice.finish_reason or "unknown",
        )

    async def analyze_strategy(
        self,
        context: str,
        question: str,
        thesis_context: Optional[str] = None,
    ) -> OpenAIResponse:
        """
        Get OpenAI's perspective on a strategy question.

        Args:
            context: Current state/code/data context
            question: Specific question to analyze
            thesis_context: Optional investment thesis context

        Returns:
            OpenAIResponse with strategy analysis
        """
        system_prompt = """You are a senior strategy advisor for a venture capital deal sourcing system.
Your role is to provide actionable insights on:
- Signal quality and relevance
- Thesis fit assessment
- Collection strategy optimization
- Deduplication and entity resolution patterns

Be specific, cite examples from the context, and provide concrete recommendations.
Focus on early-stage consumer companies (Pre-Seed to Series A).

Thesis categories: Consumer CPG, Consumer Health Tech, Travel & Hospitality, Consumer Marketplaces
Exclusions: B2B/Enterprise, crypto, cleantech, hardware-only"""

        if thesis_context:
            system_prompt += f"\n\nAdditional thesis context:\n{thesis_context}"

        prompt = f"""## Context
{context}

## Question
{question}

Provide a structured analysis with:
1. Key observations
2. Specific recommendations
3. Potential risks/considerations
4. Suggested next steps"""

        return await self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            model=DEFAULT_MODELS["strategy"],
            temperature=0.5,  # More focused for strategy
        )

    async def review_code(
        self,
        code: str,
        file_path: str,
        focus_areas: Optional[list[str]] = None,
    ) -> OpenAIResponse:
        """
        Get OpenAI code review for collector/pipeline code.

        Args:
            code: Source code to review
            file_path: Path for context
            focus_areas: Specific areas to focus on (e.g., ["error handling", "rate limiting"])

        Returns:
            OpenAIResponse with code review
        """
        system_prompt = """You are a senior Python engineer reviewing code for a VC deal sourcing system.
Focus on:
- Error handling and resilience
- Rate limiting and API quota management
- Data validation and normalization
- Security considerations (API keys, injection)
- Performance and efficiency
- Test coverage gaps

Be constructive and specific. Reference line numbers when possible."""

        focus_section = ""
        if focus_areas:
            focus_section = f"\n\nFocus areas: {', '.join(focus_areas)}"

        prompt = f"""Review this code from `{file_path}`:

```python
{code}
```{focus_section}

Provide:
1. Summary of code purpose
2. Issues found (critical, important, minor)
3. Specific improvement suggestions
4. Test recommendations"""

        return await self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            model=DEFAULT_MODELS["code_review"],
            temperature=0.3,  # More deterministic for code review
        )

    async def iterate_thesis(
        self,
        current_thesis: str,
        signals_summary: str,
        performance_data: Optional[str] = None,
    ) -> OpenAIResponse:
        """
        Iterate on investment thesis based on signal performance.

        Args:
            current_thesis: Current thesis keywords/categories
            signals_summary: Summary of recent signals and their outcomes
            performance_data: Optional metrics on signal quality

        Returns:
            OpenAIResponse with thesis refinement suggestions
        """
        system_prompt = """You are an investment thesis optimization specialist.
Your role is to refine thesis matching criteria based on signal performance data.

Consider:
- False positive patterns (signals that looked good but weren't thesis-fit)
- False negative risks (potential misses based on current criteria)
- Keyword effectiveness
- Category boundary clarity

Provide specific, actionable refinements to thesis criteria."""

        prompt = f"""## Current Thesis
{current_thesis}

## Recent Signals Summary
{signals_summary}

{f"## Performance Data" + chr(10) + performance_data if performance_data else ""}

Suggest thesis refinements:
1. Keywords to add (with rationale)
2. Keywords to remove or modify
3. Category boundary adjustments
4. New exclusion patterns to consider
5. Confidence threshold recommendations"""

        return await self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            model=DEFAULT_MODELS["strategy"],
            temperature=0.6,
        )

    # =========================================================================
    # MCP SERVER INTERFACE
    # =========================================================================

    def _create_server(self) -> Server:
        """Create and configure the MCP server."""
        if not MCP_AVAILABLE:
            raise ImportError(
                "MCP package not installed. Run: pip install mcp"
            )

        server = Server("openai-bridge")

        @server.list_prompts()
        async def list_prompts() -> list[Prompt]:
            return [
                Prompt(
                    name="openai-strategy",
                    description="Get OpenAI's perspective on a strategy question",
                    arguments=[
                        PromptArgument(
                            name="question",
                            description="Strategy question to analyze",
                            required=True,
                        ),
                        PromptArgument(
                            name="context",
                            description="Relevant context (code, data, etc.)",
                            required=False,
                        ),
                    ],
                ),
                Prompt(
                    name="openai-code-review",
                    description="Get OpenAI code review for a file",
                    arguments=[
                        PromptArgument(
                            name="file_path",
                            description="Path to file to review",
                            required=True,
                        ),
                        PromptArgument(
                            name="focus",
                            description="Comma-separated focus areas",
                            required=False,
                        ),
                    ],
                ),
                Prompt(
                    name="openai-thesis-iterate",
                    description="Iterate on investment thesis with OpenAI",
                    arguments=[
                        PromptArgument(
                            name="feedback",
                            description="Signal performance feedback/observations",
                            required=True,
                        ),
                    ],
                ),
            ]

        @server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
            arguments = arguments or {}

            if name == "openai-strategy":
                question = arguments.get("question", "")
                context = arguments.get("context", "No additional context provided")

                if not question:
                    return self._error_result("Missing required argument: question")

                response = await self.analyze_strategy(
                    context=context,
                    question=question,
                )
                return self._success_result(
                    f"OpenAI Strategy Analysis (model: {response.model})",
                    response.to_dict(),
                )

            elif name == "openai-code-review":
                file_path = arguments.get("file_path", "")
                focus = arguments.get("focus", "")

                if not file_path:
                    return self._error_result("Missing required argument: file_path")

                # Read file content
                try:
                    with open(file_path, "r") as f:
                        code = f.read()
                except FileNotFoundError:
                    return self._error_result(f"File not found: {file_path}")
                except Exception as e:
                    return self._error_result(f"Error reading file: {str(e)}")

                focus_areas = [f.strip() for f in focus.split(",")] if focus else None

                response = await self.review_code(
                    code=code,
                    file_path=file_path,
                    focus_areas=focus_areas,
                )
                return self._success_result(
                    f"OpenAI Code Review: {file_path}",
                    response.to_dict(),
                )

            elif name == "openai-thesis-iterate":
                feedback = arguments.get("feedback", "")

                if not feedback:
                    return self._error_result("Missing required argument: feedback")

                # Load current thesis from CLAUDE.md
                thesis_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)),
                    "CLAUDE.md"
                )
                try:
                    with open(thesis_path, "r") as f:
                        content = f.read()
                    # Extract thesis section (between "Investment Thesis" and next ##)
                    import re
                    match = re.search(
                        r"Investment Thesis.*?(?=\n##|\Z)",
                        content,
                        re.DOTALL
                    )
                    current_thesis = match.group(0) if match else "Thesis not found in CLAUDE.md"
                except Exception as e:
                    current_thesis = f"Could not load thesis: {str(e)}"

                response = await self.iterate_thesis(
                    current_thesis=current_thesis,
                    signals_summary=feedback,
                )
                return self._success_result(
                    "OpenAI Thesis Iteration Suggestions",
                    response.to_dict(),
                )

            else:
                return self._error_result(f"Unknown prompt: {name}")

        @server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="openai_chat",
                    description="Send a chat message to OpenAI API",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Message to send",
                            },
                            "system_prompt": {
                                "type": "string",
                                "description": "Optional system context",
                            },
                            "model": {
                                "type": "string",
                                "description": "Model to use (default: gpt-4o)",
                                "enum": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview"],
                            },
                            "temperature": {
                                "type": "number",
                                "description": "Creativity 0-2 (default: 0.7)",
                            },
                        },
                        "required": ["prompt"],
                    },
                ),
                Tool(
                    name="openai_consensus",
                    description="Get consensus between Claude and OpenAI on a question",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "Question to get consensus on",
                            },
                            "claude_answer": {
                                "type": "string",
                                "description": "Claude's current answer/perspective",
                            },
                            "context": {
                                "type": "string",
                                "description": "Shared context for both models",
                            },
                        },
                        "required": ["question", "claude_answer"],
                    },
                ),
            ]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            if name == "openai_chat":
                response = await self.chat(
                    prompt=arguments["prompt"],
                    system_prompt=arguments.get("system_prompt"),
                    model=arguments.get("model", "gpt-4o"),
                    temperature=arguments.get("temperature", 0.7),
                )
                return [TextContent(
                    type="text",
                    text=json.dumps(response.to_dict(), indent=2),
                )]

            elif name == "openai_consensus":
                question = arguments["question"]
                claude_answer = arguments["claude_answer"]
                context = arguments.get("context", "")

                system_prompt = """You are participating in a multi-LLM consensus process.
Another AI (Claude) has provided an answer. Your role is to:
1. Provide your independent analysis
2. Note areas of agreement
3. Highlight any disagreements with reasoning
4. Suggest a synthesized consensus view

Be specific and constructive."""

                prompt = f"""## Context
{context}

## Question
{question}

## Claude's Answer
{claude_answer}

Provide:
1. Your independent analysis
2. Agreement areas (with confidence)
3. Disagreement areas (with reasoning)
4. Suggested consensus synthesis"""

                response = await self.chat(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model="gpt-4o",
                    temperature=0.5,
                )

                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "openai_response": response.to_dict(),
                        "consensus_request": {
                            "question": question,
                            "claude_answer_length": len(claude_answer),
                        },
                    }, indent=2),
                )]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        return server

    def _success_result(self, message: str, data: dict[str, Any]) -> GetPromptResult:
        """Create a success prompt result."""
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

    def _error_result(self, message: str) -> GetPromptResult:
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

    async def run(self):
        """Run the MCP server."""
        if not MCP_AVAILABLE:
            raise ImportError("MCP package not installed")

        self._server = self._create_server()
        logger.info("Starting OpenAI Bridge MCP server")

        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Run the OpenAI MCP server."""
    server = OpenAIMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
