from __future__ import annotations

from ops.collector_config import load_collector_config


def test_load_collector_config_resolves_required_env(tmp_path, monkeypatch):
    path = tmp_path / "collectors.yaml"
    path.write_text(
        """
schema_version: 1
collectors:
  github_activity:
    configured_status: enabled
    expected_cadence_hours: 12
    required_env:
      any_of: [GITHUB_ACTIVITY_USERNAMES, GITHUB_ACTIVITY_ORGS]
  telegram:
    configured_status: enabled
    expected_cadence_hours: 6
    required_env: [TELEGRAM_API_ID, TELEGRAM_API_HASH]
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("GITHUB_ACTIVITY_USERNAMES", raising=False)
    monkeypatch.setenv("GITHUB_ACTIVITY_ORGS", "presson")
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)

    configs = load_collector_config(path)

    assert configs["github_activity"].resolved_configured_status()[0] == "enabled"
    status, reason = configs["telegram"].resolved_configured_status()
    assert status == "disabled_missing_key"
    assert "TELEGRAM_API_HASH" in reason
