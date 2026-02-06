import re
import unicodedata
import html
import os
import logging
import json
from typing import Optional, Any

logger = logging.getLogger(__name__)


class InputSanitizer:
    """Security-first sanitizer for internal team use."""

    @staticmethod
    def sanitize_for_llm(text: str, max_length: int = 1000) -> str:
        if not text or not isinstance(text, str):
            return ""

        text = unicodedata.normalize("NFKC", text)
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\x81\x8D\x8F\x90\x9D]", "", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Escape code blocks instead of deleting them (preserves technical context)
        # Replace backtick fences with safe markers
        text = re.sub(r"```", "[CODE_FENCE]", text)

        # Mark potential injection attempts but don't delete entire blocks
        injection_patterns = [
            (r"(?i)ignore (?:previous|above|all) instructions", "[REDACTED_INSTRUCTION]"),
            (r"(?i)system prompt", "[REDACTED_SYSTEM]"),
            (r"(?i)hidden instructions", "[REDACTED_HIDDEN]"),
            (r"(?i)do not tell anyone", "[REDACTED_SECRET]"),
        ]

        for pattern, replacement in injection_patterns:
            text = re.sub(pattern, replacement, text)

        if len(text) > max_length:
            truncated = text[:max_length]
            last_space = truncated.rfind(" ")
            if last_space > max_length * 0.8:
                text = truncated[:last_space] + "..."
            else:
                text = truncated + "..."

        return text.strip()

    @staticmethod
    def sanitize_for_display(text: str, max_length: int = 500) -> str:
        if not text:
            return ""

        text = html.escape(text)
        text = re.sub(r"\s+", " ", text)

        if len(text) > max_length:
            text = text[:max_length] + "..."

        return text

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        if not filename or not isinstance(filename, str):
            return "unnamed"

        filename = unicodedata.normalize("NFKC", filename).replace("\x00", "")
        # os.path.basename handles path separators cross-platform
        filename = os.path.basename(filename)
        filename = re.sub(r'[<>:"|?*]', "_", filename)
        filename = filename.lstrip(".").strip()
        if not filename:
            filename = "unnamed"

        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[: 255 - len(ext)] + ext

        return filename

    @staticmethod
    def sanitize(text: str, max_length: int = 1000) -> str:
        return InputSanitizer.sanitize_for_llm(text, max_length)


def parse_json_relaxed(raw: str) -> Optional[Any]:
    """Best-effort JSON parsing for LLM outputs.

    Handles common "almost JSON" formats:
    - Markdown code fences
    - Leading/trailing commentary
    - Extracting the first balanced JSON object/array

    Returns parsed Python object on success, or None on failure.
    """
    if not raw or not isinstance(raw, str):
        return None

    s = raw.strip()

    # Strip fenced blocks if present.
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)

    # First try direct JSON.
    try:
        return json.loads(s)
    except Exception:
        pass

    # Fallback: extract first balanced {...} or [...].
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = s.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = s[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break

    return None


class TokenEstimator:
    PRICING = {
        "input": 0.075,
        "output": 0.30,
    }

    # System instruction overhead calculated from actual system instructions.
    # Extractor SYSTEM_INSTRUCTION: ~180 tokens
    # Classifier SYSTEM_INSTRUCTION: ~160 tokens
    # Use conservative estimate that covers both
    DEFAULT_SYSTEM_INSTRUCTION_OVERHEAD = 200  # tokens (updated from 120)

    _genai_client = None  # cached across calls

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimation (char count / 4).
        
        Fixed bug: now uses clean_text instead of original text.
        """
        if not text:
            return 0

        clean_text = re.sub(r"\s+", " ", text)
        return max(1, len(clean_text) // 4)  # FIX: was len(text)

    @classmethod
    def _get_genai_client(cls):
        """Return a cached genai.Client, creating one on first use."""
        if cls._genai_client is not None:
            return cls._genai_client

        try:
            from google import genai

            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                return None

            cls._genai_client = genai.Client(api_key=api_key)
            return cls._genai_client
        except Exception as e:
            logger.debug(f"Failed to create genai client for token counting: {e}")
            return None

    @classmethod
    def count_tokens_accurate(cls, text: str, model: str = "gemini-2.5-flash") -> Optional[int]:
        client = cls._get_genai_client()
        if client is None:
            return cls.estimate_tokens(text)

        try:
            result = client.models.count_tokens(model=model, contents=text)
            return result.total_tokens
        except Exception as e:
            logger.debug(f"Token API unavailable, using estimate: {e}")
            return cls.estimate_tokens(text)

    @classmethod
    def estimate_cost_from_token_counts(cls, input_tokens: int, output_tokens: int = 0) -> float:
        input_cost = (max(0, input_tokens) / 1_000_000) * cls.PRICING["input"]
        output_cost = (max(0, output_tokens) / 1_000_000) * cls.PRICING["output"]
        return round(input_cost + output_cost, 6)

    @classmethod
    def estimate_cost(
        cls,
        prompt: str,
        response: str = "",
        system_overhead: int = DEFAULT_SYSTEM_INSTRUCTION_OVERHEAD,
    ) -> float:
        input_tokens = cls.estimate_tokens(prompt) + system_overhead
        output_tokens = cls.estimate_tokens(response)
        return cls.estimate_cost_from_token_counts(input_tokens, output_tokens)

    @classmethod
    def estimate_cost_upper_bound(
        cls,
        prompt: str,
        max_output_tokens: int,
        system_overhead: int = DEFAULT_SYSTEM_INSTRUCTION_OVERHEAD,
    ) -> float:
        input_tokens = cls.estimate_tokens(prompt) + system_overhead
        return cls.estimate_cost_from_token_counts(input_tokens, max_output_tokens)

    @classmethod
    def check_budget(cls, spent_today: float, estimated_cost: float, daily_budget: float = 5.0) -> bool:
        return (spent_today + estimated_cost) <= daily_budget
