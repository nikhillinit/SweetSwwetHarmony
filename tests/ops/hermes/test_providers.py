from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from integrations.hermes.config import RoutingConfig
from integrations.hermes.providers import ProviderReport, doctor

from .conftest import minimal_config_dict


def test_doctor_reports_imports_binaries_env_and_deferred_status(
    monkeypatch,
) -> None:
    data = minimal_config_dict()
    data["executors"]["codex"]["binary"] = "codex"
    config = RoutingConfig.model_validate(data)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    report = doctor(config)

    assert report.success is True
    codex = report.providers["codex"]
    assert codex.checks_by_name["wrapper_import"].ok is True
    assert codex.checks_by_name["binary"].detail == "/usr/bin/codex"
    kimi = report.providers["kimi"]
    assert kimi.checks_by_name["wrapper_import"].detail == "integrations.llm_cli.kimi"
    assert kimi.checks_by_name["binary"].detail == "/usr/bin/kimi-cli"
    assert "env:KIMI_API_KEY" not in kimi.checks_by_name
    assert report.deferred["gemini"].provider == "gemini"
    assert isinstance(report, ProviderReport)


def test_required_missing_binary_makes_report_unsuccessful(monkeypatch) -> None:
    data = minimal_config_dict()
    data["executors"]["codex"]["binary"] = "codex"
    data["executors"]["codex"]["required"] = True
    config = RoutingConfig.model_validate(data)
    monkeypatch.setattr("shutil.which", lambda name: None)

    report = doctor(config)

    assert report.success is False
    assert report.providers["codex"].checks_by_name["binary"].ok is False
    assert report.providers["codex"].checks_by_name["binary"].required is True


def test_optional_missing_binary_is_reported_without_failing_report(monkeypatch) -> None:
    data = minimal_config_dict()
    data["executors"]["kimi"]["required"] = False
    data["executors"]["codex"].pop("binary", None)
    config = RoutingConfig.model_validate(data)
    monkeypatch.setattr("shutil.which", lambda name: None)

    report = doctor(config)

    assert report.success is True
    binary_check = report.providers["kimi"].checks_by_name["binary"]
    assert binary_check.ok is False
    assert binary_check.required is False


def test_past_sunset_date_fails_required_provider() -> None:
    data = minimal_config_dict()
    data["executors"]["codex"].pop("binary", None)
    data["executors"]["codex"]["sunsetDate"] = (
        date.today() - timedelta(days=1)
    ).isoformat()
    config = RoutingConfig.model_validate(data)

    report = doctor(config)

    assert report.success is False
    assert report.providers["codex"].checks_by_name["sunset"].ok is False


def test_provider_report_json_and_text_shapes(monkeypatch) -> None:
    data = minimal_config_dict()
    data["executors"]["codex"].pop("binary", None)
    config = RoutingConfig.model_validate(data)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    report = doctor(config)
    payload = report.to_dict()
    text = report.to_text()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["providers"]["codex"]["provider"] == "codex"
    assert "Hermes Provider Doctor" in text
    assert "codex" in text


def test_cli_provider_doctor_json_uses_config_without_live_probes(tmp_path: Path) -> None:
    data = minimal_config_dict()
    for executor in data["executors"].values():
        executor.pop("binary", None)
        executor["env"] = []
    data["ledger"]["root"] = str(tmp_path / "ai-logs" / "hermes")
    config_path = tmp_path / "model-routing.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ops.cli",
            "hermes",
            "providers",
            "doctor",
            "--json",
            "--config",
            str(config_path),
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["providers"]["codex"]["provider"] == "codex"
    assert not (tmp_path / "ai-logs").exists()
