from __future__ import annotations

from integrations.codex_wrapper import CodexCLI, DEFAULT_MODEL


def test_codex_cli_prefers_model_environment_variable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("CODEX_MODEL", "env-model")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert CodexCLI().model == "env-model"


def test_codex_cli_reads_model_from_codex_config(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('model = "config-model"\n', encoding="utf-8")

    assert CodexCLI().model == "config-model"


def test_codex_cli_falls_back_to_supported_default(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    assert DEFAULT_MODEL == "gpt-5.5"
    assert CodexCLI().model == "gpt-5.5"
