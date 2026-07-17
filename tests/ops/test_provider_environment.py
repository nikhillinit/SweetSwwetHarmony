"""Provider-scoped child-environment security contract."""

from __future__ import annotations

import json
import logging
import os
import sys

import pytest

from integrations.process_runtime import ProcessOutcome, run_process
from integrations.provider_environment import (
    ChildExecutionContext,
    ProviderIdentity,
    ToolCapability,
    build_provider_environment,
)


def _source_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PATHEXT": os.environ.get("PATHEXT", ".EXE;.CMD;.BAT"),
        "COMSPEC": r"C:\Windows\System32\cmd.exe",
        "SYSTEMROOT": r"C:\Windows",
        "HOME": "/home/hermes",
        "USERPROFILE": r"C:\Users\hermes",
        "APPDATA": r"C:\Users\hermes\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\hermes\AppData\Local",
        "TEMP": os.environ.get("TEMP", "/tmp"),
        "TMP": os.environ.get("TMP", "/tmp"),
        "LANG": "en_US.UTF-8",
        "CODEX_HOME": "/config/codex",
        "OPENAI_API_KEY": "openai-secret",
        "KIMI_API_KEY": "kimi-secret",
        "GOOGLE_API_KEY": "google-secret",
        "GEMINI_API_KEY": "gemini-secret",
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "GITHUB_TOKEN": "github-secret",
        "NOTION_API_KEY": "notion-secret",
        "NOTION_DATABASE_ID": "notion-db",
        "MCP_TOKEN": "mcp-secret",
        "HTTPS_PROXY": "https://proxy.invalid",
        "SSL_CERT_FILE": "/certs/ca.pem",
        "UNRELATED_SECRET": "must-not-pass",
    }


@pytest.mark.parametrize(
    ("provider", "expected_model_keys"),
    [
        (ProviderIdentity.CODEX, {"OPENAI_API_KEY"}),
        (ProviderIdentity.KIMI, {"KIMI_API_KEY"}),
        (
            ProviderIdentity.ANTIGRAVITY,
            {"GOOGLE_API_KEY", "GEMINI_API_KEY"},
        ),
    ],
)
def test_child_env_contains_only_selected_model_provider_credentials(
    provider: ProviderIdentity,
    expected_model_keys: set[str],
) -> None:
    child = build_provider_environment(provider, source_env=_source_environment())
    all_model_keys = {
        "OPENAI_API_KEY",
        "KIMI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
    }

    assert set(child) & all_model_keys == expected_model_keys
    assert "UNRELATED_SECRET" not in child


def test_generic_context_strips_all_non_model_service_credentials() -> None:
    child = build_provider_environment(
        ProviderIdentity.KIMI,
        source_env=_source_environment(),
    )

    for key in (
        "GITHUB_TOKEN",
        "NOTION_API_KEY",
        "NOTION_DATABASE_ID",
        "MCP_TOKEN",
        "HTTPS_PROXY",
        "SSL_CERT_FILE",
    ):
        assert key not in child


def test_typed_capabilities_authorize_only_the_named_service() -> None:
    context = ChildExecutionContext(
        tool_capabilities=frozenset({ToolCapability.GITHUB})
    )
    child = build_provider_environment(
        ProviderIdentity.CODEX,
        source_env=_source_environment(),
        execution_context=context,
    )

    assert child["GITHUB_TOKEN"] == "github-secret"
    assert "NOTION_API_KEY" not in child
    assert "MCP_TOKEN" not in child


def test_unknown_capability_fails_closed() -> None:
    with pytest.raises(TypeError, match="ToolCapability"):
        ChildExecutionContext(
            tool_capabilities=frozenset({"github"}),  # type: ignore[arg-type]
        )


def test_required_process_home_config_and_locale_variables_are_preserved() -> None:
    child = build_provider_environment(
        ProviderIdentity.CODEX,
        source_env=_source_environment(),
    )

    for key in (
        "PATH",
        "PATHEXT",
        "COMSPEC",
        "SYSTEMROOT",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "LANG",
        "CODEX_HOME",
    ):
        assert child[key] == _source_environment()[key]


def test_each_spawn_gets_a_fresh_mapping_without_global_env_mutation() -> None:
    before = dict(os.environ)
    first = build_provider_environment(
        ProviderIdentity.CODEX,
        source_env=_source_environment(),
    )
    second = build_provider_environment(
        ProviderIdentity.CODEX,
        source_env=_source_environment(),
    )

    first["LOCAL_ONLY"] = "changed"
    assert "LOCAL_ONLY" not in second
    assert dict(os.environ) == before


async def test_authorized_mcp_capability_reaches_a_real_child_only_by_name() -> None:
    source = dict(os.environ)
    source.update(_source_environment())
    context = ChildExecutionContext(
        tool_capabilities=frozenset({ToolCapability.MCP})
    )
    child_env = build_provider_environment(
        ProviderIdentity.KIMI,
        source_env=source,
        execution_context=context,
    )
    code = (
        "import json, os; print(json.dumps({"
        "'mcp': os.environ.get('MCP_TOKEN'), "
        "'github': os.environ.get('GITHUB_TOKEN')}))"
    )

    result = await run_process(
        [sys.executable, "-c", code],
        env=child_env,
        timeout_seconds=30,
    )

    assert result.outcome is ProcessOutcome.COMPLETED
    assert json.loads(result.stdout) == {"mcp": "mcp-secret", "github": None}


def test_proxy_and_ca_inputs_require_explicit_capabilities() -> None:
    context = ChildExecutionContext(
        tool_capabilities=frozenset(
            {ToolCapability.NETWORK_PROXY, ToolCapability.CUSTOM_CA}
        )
    )
    child = build_provider_environment(
        ProviderIdentity.CODEX,
        source_env=_source_environment(),
        execution_context=context,
    )

    assert child["HTTPS_PROXY"] == "https://proxy.invalid"
    assert child["SSL_CERT_FILE"] == "/certs/ca.pem"


def test_environment_log_contains_key_names_never_values(caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="provider-environment"):
        build_provider_environment(
            ProviderIdentity.CODEX,
            source_env=_source_environment(),
            execution_context=ChildExecutionContext(
                tool_capabilities=frozenset({ToolCapability.GITHUB})
            ),
        )

    log_text = caplog.text
    assert "OPENAI_API_KEY" in log_text
    assert "GITHUB_TOKEN" in log_text
    assert "openai-secret" not in log_text
    assert "github-secret" not in log_text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .cmd environment contract")
async def test_scoped_environment_runs_real_windows_cmd_wrapper(tmp_path) -> None:
    shim = tmp_path / "provider-probe.cmd"
    shim.write_text(
        "@echo off\r\n"
        "if \"%COMSPEC%\"==\"\" exit /b 10\r\n"
        "if \"%SYSTEMROOT%\"==\"\" exit /b 11\r\n"
        "where python >nul 2>&1 || exit /b 12\r\n"
        "python -c \"print('provider-cmd-ok')\"\r\n",
        encoding="ascii",
    )
    child_env = build_provider_environment(
        ProviderIdentity.CODEX,
        source_env=os.environ,
    )

    for key in ("PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT"):
        assert child_env[key]

    result = await run_process(
        [str(shim)],
        env=child_env,
        timeout_seconds=30,
    )

    assert result.outcome is ProcessOutcome.COMPLETED
    assert result.exit_code == 0
    assert result.stdout.strip() == b"provider-cmd-ok"
