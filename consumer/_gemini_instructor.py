"""Compatibility helpers for Instructor's Gemini integration."""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass
from typing import Any, Optional

_GOOGLE_GENERATIVEAI_DEPRECATION_REGEX = (
    r"\s*All support for the `google\.generativeai` package has ended\."
)


@dataclass(frozen=True)
class GeminiInstructorDeps:
    """Imported Instructor and Google GenAI modules used for structured output."""

    instructor: Any
    types: Any


def load_instructor_genai() -> Optional[GeminiInstructorDeps]:
    """Load Instructor's Gemini adapter while suppressing its known deprecation warning."""

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=_GOOGLE_GENERATIVEAI_DEPRECATION_REGEX,
                category=FutureWarning,
            )
            instructor = importlib.import_module("instructor")
        types = importlib.import_module("google.genai.types")
    except ImportError:
        return None

    return GeminiInstructorDeps(instructor=instructor, types=types)
