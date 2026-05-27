from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from integrations.hermes.config import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    RoutingConfig,
    load_config,
)
from integrations.hermes.ledger import redact_text

from .conftest import minimal_config_dict


def test_loads_minimal_valid_config(minimal_config_path: Path) -> None:
    config = load_config(minimal_config_path)

    assert config.schema_version == 1
    assert set(config.executors) == {"codex", "kimi"}
    assert config.phases["production"].preferred_executors == ["codex"]
    assert config.specialists["schema"].risk == "high"


def test_load_config_prefers_explicit_path_over_env(
    minimal_config_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_config = minimal_config_dict()
    env_config["executors"]["extra"] = {
        "provider": "extra",
        "displayName": "Extra",
        "enabled": True,
    }
    env_path = tmp_path / "env-routing.json"
    env_path.write_text(json.dumps(env_config), encoding="utf-8")
    monkeypatch.setenv("HERMES_CONFIG", str(env_path))

    config = load_config(minimal_config_path)

    assert set(config.executors) == {"codex", "kimi"}


def test_load_config_uses_env_when_no_explicit_path(
    minimal_config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMES_CONFIG", str(minimal_config_path))

    config = load_config()

    assert config.executors["codex"].provider == "codex"


def test_default_config_path_points_to_checked_in_config() -> None:
    assert DEFAULT_CONFIG_PATH == Path(".claude/hermes/model-routing.json")


def test_rejects_unknown_top_level_keys() -> None:
    data = minimal_config_dict()
    data["surprise"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RoutingConfig.model_validate(data)


def test_rejects_unknown_schema_version() -> None:
    data = minimal_config_dict()
    data["schemaVersion"] = 2

    with pytest.raises(ValidationError):
        RoutingConfig.model_validate(data)


def test_rejects_empty_executor_map() -> None:
    data = minimal_config_dict()
    data["executors"] = {}

    with pytest.raises(ValueError, match="executors must not be empty"):
        RoutingConfig.model_validate(data)


def test_rejects_unknown_executor_references() -> None:
    data = minimal_config_dict()
    data["specialists"]["schema"]["preferredExecutors"] = ["claude"]

    with pytest.raises(ValueError, match="unknown executor reference"):
        RoutingConfig.model_validate(data)


def test_rejects_deferred_fallback_references() -> None:
    data = minimal_config_dict()
    data["routing"]["fallbackOrder"] = ["codex", "gemini"]

    with pytest.raises(ValueError, match="deferred executor"):
        RoutingConfig.model_validate(data)


def test_rejects_unsupported_modes() -> None:
    data = minimal_config_dict()
    data["modes"] = ["dry-run", "network-probe"]

    with pytest.raises(ValidationError):
        RoutingConfig.model_validate(data)


def test_checked_in_schema_matches_model_schema() -> None:
    schema_path = PROJECT_ROOT / ".claude/hermes/model-routing.schema.json"
    checked_in = json.loads(schema_path.read_text(encoding="utf-8"))

    assert checked_in == RoutingConfig.model_json_schema()


def test_default_redaction_patterns_cover_repo_secret_formats() -> None:
    config = load_config(PROJECT_ROOT / DEFAULT_CONFIG_PATH)

    redacted = redact_text(
        (
            "secret_NotionKey ghp_1234567890 xoxb-123-456 "
            "AKIA1234567890ABCDEF Bearer abc.def.ghi ya29.oauth-token"
        ),
        config.ledger.redaction_patterns,
    )

    assert "secret_NotionKey" not in redacted
    assert "ghp_1234567890" not in redacted
    assert "xoxb-123-456" not in redacted
    assert "AKIA1234567890ABCDEF" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "ya29.oauth-token" not in redacted
