from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import LedgerConfig, PROJECT_ROOT

SENSITIVE_PAYLOAD_KEYS = {
    "api_key",
    "token",
    "secret",
    "authorization",
    "password",
}


@dataclass(frozen=True)
class HermesRun:
    run_id: str
    run_dir: Path
    created_at: str


class HermesLedger:
    def __init__(self, config: LedgerConfig, root: Path | str | None = None):
        self.config = config
        self.root = Path(root) if root is not None else PROJECT_ROOT / config.root
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.jsonl"

    def create_run(
        self,
        plan: dict[str, Any],
        prompt: str,
        metadata: dict[str, Any] | None = None,
    ) -> HermesRun:
        run_id = generate_run_id()
        created_at = datetime.now(timezone.utc).isoformat()
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)

        run = HermesRun(run_id=run_id, run_dir=run_dir, created_at=created_at)
        self.write_json_artifact(run, "plan.json", plan)
        self.write_text_artifact(run, "prompt.txt", prompt)

        ledger_payload = {
            "runId": run_id,
            "createdAt": created_at,
            "metadata": metadata or {},
            "artifacts": {
                "plan": "plan.json",
                "prompt": "prompt.txt",
                "ledger": "ledger.json",
            },
        }
        self.write_json_artifact(run, "ledger.json", ledger_payload)
        self.append_index(
            {
                "runId": run_id,
                "createdAt": created_at,
                "task": plan.get("task"),
                "runDir": str(run_dir),
                "metadata": metadata or {},
            }
        )
        return run

    def write_text_artifact(self, run: HermesRun, relative_path: str, text: str) -> Path:
        path = run.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            redact_text(text, self.config.redaction_patterns),
            encoding="utf-8",
        )
        return path

    def write_json_artifact(
        self,
        run: HermesRun,
        relative_path: str,
        payload: dict[str, Any],
    ) -> Path:
        redacted_payload = redact_payload(payload, self.config.redaction_patterns)
        redacted = json.dumps(redacted_payload, indent=2)
        path = run.run_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redacted + "\n", encoding="utf-8")
        return path

    def write_state(self, run: HermesRun, state_name: str, payload: dict[str, Any]) -> Path:
        return self.write_json_artifact(run, f"state/{state_name}.json", payload)

    def write_repair_prompt(
        self,
        run: HermesRun,
        *,
        failure_type: str,
        exit_code: int,
        routing_plan: dict[str, Any],
        state_paths: list[Path],
        next_action: str,
        command: list[str] | tuple[str, ...] | None = None,
        executor: str | None = None,
        arguments: dict[str, Any] | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> Path:
        lines = [
            "# Hermes Repair Prompt",
            "",
            f"Failure type: {failure_type}",
            f"Run ID: {run.run_id}",
            f"Exit code: {exit_code}",
        ]
        if executor:
            lines.append(f"Executor: {executor}")
        if command:
            lines.append(f"Command: {' '.join(command)}")
        if arguments:
            lines.extend(["", "Arguments:", "```json"])
            lines.append(json.dumps(redact_payload(arguments, self.config.redaction_patterns), indent=2))
            lines.append("```")

        lines.extend(["", "Artifacts:"])
        if stdout_path:
            lines.append(f"- stdout: {_relative_to_run(run, stdout_path)}")
        if stderr_path:
            lines.append(f"- stderr: {_relative_to_run(run, stderr_path)}")

        lines.extend(["", "State snapshots:"])
        for state_path in state_paths:
            lines.append(f"- {_relative_to_run(run, state_path)}")

        lines.extend(["", "Routing plan:", "```json"])
        lines.append(json.dumps(redact_payload(routing_plan, self.config.redaction_patterns), indent=2))
        lines.extend(["```", "", "Next safe operator action:", next_action, ""])
        return self.write_text_artifact(run, "repair_prompt.md", "\n".join(lines))

    def append_index(self, entry: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            redact_payload(entry, self.config.redaction_patterns),
            separators=(",", ":"),
        )
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def generate_run_id(prefix: str = "hermes") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def redact_text(text: str, patterns: list[str]) -> str:
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, "[REDACTED]", redacted)
    return redacted


def redact_payload(value: Any, patterns: list[str]) -> Any:
    return _redact_payload(value, patterns, redact_value=False)


def _redact_payload(value: Any, patterns: list[str], *, redact_value: bool) -> Any:
    if redact_value:
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value, patterns)
    if isinstance(value, list):
        return [_redact_payload(item, patterns, redact_value=False) for item in value]
    if isinstance(value, tuple):
        return [_redact_payload(item, patterns, redact_value=False) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _redact_payload(
                item,
                patterns,
                redact_value=_is_sensitive_key(key),
            )
            for key, item in value.items()
        }
    return value


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in SENSITIVE_PAYLOAD_KEYS


def _relative_to_run(run: HermesRun, path: Path) -> str:
    try:
        return str(path.relative_to(run.run_dir))
    except ValueError:
        return str(path)
