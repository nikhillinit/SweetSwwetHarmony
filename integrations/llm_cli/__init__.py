"""Shared CLI-backed LLM generation wrappers."""

from .kimi import KimiCLIClient, KimiCLIResponse

__all__ = ["KimiCLIClient", "KimiCLIResponse"]
