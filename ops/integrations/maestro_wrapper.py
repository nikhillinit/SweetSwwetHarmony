"""
OpsLayerMaestro: Wrapper for structured output from forensic workflows.

Handles:
- Dependency injection (shared Maestro state)
- Schema injection into prompts
- Robust JSON extraction from "chatty" LLM responses
- Single retry with error feedback
- Decision logging for observability
- Output versioning for compatibility
"""

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, TypeVar, Type, Generic, TYPE_CHECKING

# Lazy imports for optional dependencies
if TYPE_CHECKING:
    from pydantic import BaseModel
    from integrations.maestro import Maestro, ForensicResult

# Define KimiMode locally to avoid circular import through integrations/__init__.py
# This mirrors the enum in integrations/maestro.py
from enum import Enum

class KimiMode(str, Enum):
    """Kimi usage modes (mirrors integrations.maestro.KimiMode)."""
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"
    DUAL = "dual"

T = TypeVar("T")

DECISIONS_LOG_FILE = ".kimi_decisions.jsonl"


@dataclass
class ContextSizeDecision:
    """Decision record for observability."""
    timestamp: str
    file_count: int
    estimated_tokens: int
    context_text_tokens: int
    chosen_backend: str
    reason: str
    task_summary: str = ""
    latency_ms: Optional[int] = None
    retries: int = 0
    parse_success: bool = True
    verification_passed: Optional[bool] = None


@dataclass
class StructuredOutputMeta:
    """Versioning metadata for structured outputs."""
    schema_version: str = "1.0.0"
    producer_model: str = "unknown"
    raw_response: Optional[str] = None  # For audit/replay


@dataclass
class VersionedOutput(Generic[T]):
    """Wrapper adding versioning to any structured output."""
    data: T
    meta: StructuredOutputMeta


class OpsLayerMaestro:
    """Wrapper around Maestro with structured output support."""

    def __init__(self, maestro: "Maestro", force_kimi: bool = False):
        """
        Initialize with existing Maestro instance (dependency injection).

        Args:
            maestro: Existing Maestro instance (shares budget/connections)
            force_kimi: Override to always use Kimi for ingest (uses mode_override, not state mutation)
        """
        self.maestro = maestro
        self._force_kimi = force_kimi
        # NOTE: Do NOT mutate self.maestro.kimi_mode - use mode_override instead

    async def analyze_with_schema(
        self,
        task: str,
        context: str,
        output_schema: Type[T],
        context_files: Optional[list[str]] = None,
        context_text: Optional[str] = None,
        include_raw: bool = False,
    ) -> VersionedOutput[T]:
        """
        Run forensic analysis returning validated, versioned structured output.

        Args:
            task: Task description
            context: Context/constraints
            output_schema: Pydantic model class for response validation
            context_files: Optional file paths
            context_text: Optional raw text context
            include_raw: If True, include raw LLM response in meta for audit

        Returns:
            VersionedOutput containing validated data and metadata

        Raises:
            ValueError: If parsing fails after retry
            ImportError: If pydantic is not installed
        """
        # Lazy import pydantic (optional dependency)
        try:
            from pydantic import ValidationError
        except ImportError:
            raise ImportError(
                "pydantic is required for structured output. "
                "Install with: pip install pydantic"
            )

        start_time = time.time()

        # 1. Log decision before execution
        decision = self._create_decision_record(task, context_files, context_text)

        # 2. Inject schema into requirements
        schema_str = json.dumps(output_schema.model_json_schema(), indent=2)
        enhanced_reqs = (
            f"You MUST return a valid JSON object matching this exact schema:\n"
            f"```json\n{schema_str}\n```\n\n"
            f"Output ONLY the raw JSON object. No markdown code blocks. "
            f"No conversational text before or after."
        )

        # 3. Execute with retry loop
        max_retries = 1
        last_error: Optional[str] = None
        raw_response: Optional[str] = None

        for attempt in range(max_retries + 1):
            decision.retries = attempt

            current_reqs = enhanced_reqs
            if last_error:
                current_reqs += f"\n\n[PREVIOUS ATTEMPT FAILED]\nError: {last_error}\nFix the JSON format."

            # Use mode_override instead of mutating shared state
            mode_override = KimiMode.ALWAYS if self._force_kimi else None

            result = await self.maestro.forensic_collaborate(
                task=task,
                context=context,
                requirements=current_reqs,
                context_files=context_files,
                context_text=context_text,
                mode_override=mode_override,  # Per-request override (concurrency-safe)
            )

            raw_response = self._extract_response(result)
            if not raw_response:
                last_error = "Empty response from forensic workflow"
                continue

            try:
                cleaned_json = self._extract_json(raw_response)
                parsed = output_schema.model_validate_json(cleaned_json)

                # Success - finalize decision record
                decision.latency_ms = int((time.time() - start_time) * 1000)
                decision.parse_success = True
                self._log_decision(decision)

                return VersionedOutput(
                    data=parsed,
                    meta=StructuredOutputMeta(
                        schema_version="1.0.0",
                        producer_model=decision.chosen_backend,
                        raw_response=raw_response if include_raw else None,
                    )
                )

            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                last_error = str(e)
                continue

        # Failed - log failure
        decision.latency_ms = int((time.time() - start_time) * 1000)
        decision.parse_success = False
        self._log_decision(decision)

        raise ValueError(f"Failed to parse structured output after {max_retries + 1} attempts: {last_error}")

    def _create_decision_record(
        self,
        task: str,
        context_files: Optional[list[str]],
        context_text: Optional[str],
    ) -> ContextSizeDecision:
        """Create decision record for logging."""
        file_count, file_tokens = self.maestro._estimate_context_size(context_files, context_text)
        text_tokens = len(context_text) // 4 if context_text else 0
        total_tokens = file_tokens  # Already includes text_tokens after the fix

        # Determine backend (mirrors mode_override logic)
        if self._force_kimi:
            backend = "Kimi"
            reason = "forced: mode_override=ALWAYS"
        elif total_tokens >= 20000:
            backend = "Kimi"
            reason = f"auto: {total_tokens:,} tokens >= 20K threshold"
        elif file_count >= 5:
            backend = "Kimi"
            reason = f"auto: {file_count} files >= 5 threshold"
        else:
            backend = "Codex"
            reason = f"auto: {total_tokens:,} tokens, {file_count} files (below thresholds)"

        return ContextSizeDecision(
            timestamp=datetime.now(timezone.utc).isoformat(),
            file_count=file_count,
            estimated_tokens=total_tokens,
            context_text_tokens=text_tokens,
            chosen_backend=backend,
            reason=reason,
            task_summary=task[:100],
        )

    def _log_decision(self, decision: ContextSizeDecision) -> None:
        """Append decision to JSONL log file."""
        try:
            log_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                DECISIONS_LOG_FILE
            )
            with open(log_path, "a") as f:
                f.write(json.dumps(asdict(decision)) + "\n")
        except Exception:
            pass  # Don't fail on logging errors

    def _extract_response(self, result: "ForensicResult") -> Optional[str]:
        """Extract raw response text from ForensicResult."""
        if result.iterations:
            return result.iterations[-1].codex_response
        return None

    def _extract_json(self, text: str) -> str:
        """
        Robustly extract JSON from potentially wrapped text.

        Consistent with existing pattern at profilers/extractors/llm_extractor.py:292-299
        """
        # Strip and check for markdown code blocks
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            return cleaned.strip()

        # Try to find raw JSON object
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return match.group(0).strip()

        return text.strip()
