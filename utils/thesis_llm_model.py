"""Shared thesis LLM model resolution."""

from __future__ import annotations

import os
from typing import Optional


THESIS_LLM_MODEL_ENV = "THESIS_LLM_MODEL"
DEFAULT_THESIS_LLM_MODEL = "gemini-3.5-flash"


def resolve_thesis_llm_model(model: Optional[str] = None) -> str:
    """Return an explicit model or the env-configured/default thesis model."""
    if model is not None:
        return model
    return os.environ.get(THESIS_LLM_MODEL_ENV) or DEFAULT_THESIS_LLM_MODEL
