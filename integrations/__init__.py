"""
Integrations module for external LLM services.

This module provides integrations with:
- OpenAI API / Codex CLI for multi-LLM strategy iteration
- Maestro orchestrator for iterative Claude + Codex collaboration
- Consensus orchestration patterns for reducing hallucinations
"""

from .openai_mcp import OpenAIMCPServer
from .codex_wrapper import CodexCLI, CodexResponse
from .strategy_iterator import StrategyIterator
from .maestro import Maestro, ConsensusResult, ConsensusState

__all__ = [
    "OpenAIMCPServer",
    "CodexCLI",
    "CodexResponse",
    "StrategyIterator",
    "Maestro",
    "ConsensusResult",
    "ConsensusState",
]
