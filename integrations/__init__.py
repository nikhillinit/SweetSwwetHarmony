"""
Integrations module for external LLM services.

This module provides integrations with:
- OpenAI API / Codex CLI for multi-LLM strategy iteration
- Consensus orchestration patterns for reducing hallucinations
"""

from .openai_mcp import OpenAIMCPServer
from .codex_wrapper import CodexCLI, CodexResponse
from .strategy_iterator import StrategyIterator

__all__ = [
    "OpenAIMCPServer",
    "CodexCLI",
    "CodexResponse",
    "StrategyIterator",
]
