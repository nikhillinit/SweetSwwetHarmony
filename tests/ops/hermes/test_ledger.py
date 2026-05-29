from __future__ import annotations

import json
import re
from pathlib import Path

from integrations.hermes.config import RoutingConfig
from integrations.hermes.ledger import (
    HermesLedger,
    generate_run_id,
    redact_payload,
    redact_text,
)

from .conftest import minimal_config_dict


def _ledger(tmp_path: Path) -> HermesLedger:
    config = RoutingConfig.model_validate(minimal_config_dict())
    return HermesLedger(config.ledger, root=tmp_path / "ai-logs" / "hermes")


def test_generate_run_id_uses_utc_timestamp_and_short_hex_suffix() -> None:
    run_id = generate_run_id()

    assert re.match(r"^hermes_\d{8}_\d{6}_[0-9a-f]{8}$", run_id)


def test_redact_text_applies_all_configured_patterns() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    redacted = redact_text(
        (
            "token=abc123 sk-test-secret secret_NotionKey "
            "ghp_1234567890 xoxb-123-456 AKIA1234567890ABCDEF "
            "Bearer abc.def.ghi ya29.oauth-token"
        ),
        config.ledger.redaction_patterns,
    )

    assert "abc123" not in redacted
    assert "sk-test-secret" not in redacted
    assert "secret_NotionKey" not in redacted
    assert "ghp_1234567890" not in redacted
    assert "xoxb-123-456" not in redacted
    assert "AKIA1234567890ABCDEF" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "ya29.oauth-token" not in redacted
    assert redacted.count("[REDACTED]") == 8


def test_redact_payload_preserves_sensitive_key_names_and_redacts_values() -> None:
    payload = {
        "api_key": "plain-api-key",
        "token": "plain-token",
        "secret": {"nested": "value"},
        "authorization": "Basic abc123",
        "password": "plain-password",
        "safe": "token=abc123",
        "nested": [{"password": "nested-password", "note": "keep me"}],
    }

    redacted = redact_payload(payload, [r"token=abc123"])

    assert set(redacted) == set(payload)
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["token"] == "[REDACTED]"
    assert redacted["secret"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["safe"] == "[REDACTED]"
    assert redacted["nested"] == [
        {"password": "[REDACTED]", "note": "keep me"},
    ]


def test_create_run_writes_redacted_plan_prompt_and_index(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    run = ledger.create_run(
        plan={"task": "schema migration", "secret": "token=abc123"},
        prompt="Use sk-test-secret for nothing",
        metadata={"mode": "dry-run"},
    )

    assert run.run_id.startswith("hermes_")
    assert run.run_dir.exists()
    assert json.loads((run.run_dir / "plan.json").read_text(encoding="utf-8"))[
        "secret"
    ] == "[REDACTED]"
    assert "sk-test-secret" not in (run.run_dir / "prompt.txt").read_text(
        encoding="utf-8"
    )

    ledger_payload = json.loads((run.run_dir / "ledger.json").read_text(encoding="utf-8"))
    assert ledger_payload["runId"] == run.run_id
    assert ledger_payload["metadata"] == {"mode": "dry-run"}

    index_lines = ledger.index_path.read_text(encoding="utf-8").splitlines()
    assert len(index_lines) == 1
    assert json.loads(index_lines[0])["runId"] == run.run_id


def test_write_state_redacts_and_preserves_valid_json(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    run = ledger.create_run(plan={"task": "x"}, prompt="prompt", metadata={})

    state_path = ledger.write_state(run, "S0_initial", {"env": "api_key=secret-value"})

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["env"] == "[REDACTED]"
    assert state_path == run.run_dir / "state" / "S0_initial.json"


def test_index_is_valid_jsonl_for_multiple_runs(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    ledger.create_run(plan={"task": "one"}, prompt="one", metadata={})
    ledger.create_run(plan={"task": "two"}, prompt="two", metadata={})

    lines = ledger.index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["task"] for line in lines] == ["one", "two"]


def test_write_repair_prompt_redacts_and_records_artifact_paths(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    run = ledger.create_run(plan={"task": "schema"}, prompt="prompt", metadata={})
    state_path = ledger.write_state(run, "S2_preflight", {"status": "failed"})

    repair_path = ledger.write_repair_prompt(
        run,
        failure_type="preflight",
        command=["python", "-m", "pytest"],
        arguments={"env": "api_key=secret-value"},
        exit_code=4,
        routing_plan={"task": "schema", "secret": "token=hidden"},
        state_paths=[state_path],
        stdout_path=run.run_dir / "gates" / "preflight.json",
        stderr_path=None,
        next_action="Fix the failing gate and rerun Hermes.",
    )

    text = repair_path.read_text(encoding="utf-8")
    assert "Hermes Repair Prompt" in text
    assert "python -m pytest" in text
    assert "S2_preflight.json" in text
    assert "gates/preflight.json" in text.replace("\\", "/")
    assert "secret-value" not in text
    assert "token=hidden" not in text
