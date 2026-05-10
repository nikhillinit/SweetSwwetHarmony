from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ops.collector_heartbeat import (
    SCHEMA_VERSION,
    initialize_collector_state,
    load_collector_state,
    record_collector_heartbeat,
)


def _write_config(path, body: str):
    path.write_text(body, encoding="utf-8")
    return path


def test_record_collector_heartbeat_creates_schema_v2_state(tmp_path):
    state_path = tmp_path / "state" / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    finished = datetime.now(timezone.utc)
    started = finished - timedelta(minutes=1)

    entry = record_collector_heartbeat(
        result=SimpleNamespace(
            collector="github",
            status="success",
            signals_found=12,
            signals_new=8,
            signals_suppressed=4,
            dry_run=False,
        ),
        started_at=started,
        finished_at=finished,
        duration_seconds=60.0,
        api_calls=7,
        retries=2,
        runner="test",
        state_path=state_path,
        config_path=config_path,
    )

    assert entry["schema_version"] == SCHEMA_VERSION
    assert entry["collector"] == "github"
    assert entry["configured_status"] == "enabled"
    assert entry["last_run_status"] == "success"
    assert entry["effective_status"] == "healthy"
    assert entry["health"] == "ok"
    assert entry["expected_cadence_hours"] == 24
    assert entry["signals_found"] == 12
    assert entry["signals_new"] == 8
    assert entry["signals_suppressed"] == 4
    assert entry["rows_inserted_this_iter"] == 8
    assert entry["collector_class"] == "github"
    assert entry["api_calls"] == 7
    assert entry["retries"] == 2
    assert entry["consecutive_failures"] == 0
    assert entry["last_success_at"] == finished.isoformat()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SCHEMA_VERSION
    assert persisted["collectors"]["github"] == entry


def test_record_collector_heartbeat_records_progress_proof_fields(tmp_path):
    state_path = tmp_path / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )

    entry = record_collector_heartbeat(
        result=SimpleNamespace(
            collector="arxiv",
            status="success",
            signals_found=5,
            signals_new=3,
            signals_suppressed=2,
            dry_run=False,
        ),
        data_version_before=10,
        data_version_after=12,
        rows_inserted_this_iter=3,
        rows_total_last_24h=7,
        collector_class="ArxivCollector",
        state_path=state_path,
        config_path=config_path,
        runner="test",
    )

    assert entry["data_version_before"] == 10
    assert entry["data_version_after"] == 12
    assert entry["rows_inserted_this_iter"] == 3
    assert entry["rows_total_last_24h"] == 7
    assert entry["collector_class"] == "ArxivCollector"

    reloaded = load_collector_state(state_path, config_path=config_path)
    assert reloaded["collectors"]["arxiv"]["data_version_after"] == 12


def test_record_collector_heartbeat_tracks_failure_and_recovery(tmp_path):
    state_path = tmp_path / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  rss_feeds:
    configured_status: enabled
    expected_cadence_hours: 6
""",
    )

    for _ in range(2):
        record_collector_heartbeat(
            result=SimpleNamespace(
                collector="rss_feeds",
                status="error",
                error_message="network timeout",
                dry_run=True,
            ),
            state_path=state_path,
            config_path=config_path,
            runner="test",
        )

    failed = load_collector_state(state_path, config_path=config_path)["collectors"]["rss_feeds"]
    assert failed["last_run_status"] == "error"
    assert failed["effective_status"] == "failing"
    assert failed["health"] == "failing"
    assert failed["consecutive_failures"] == 2
    assert failed["last_error_at"] is not None
    assert failed["error_messages"] == ["network timeout"]

    record_collector_heartbeat(
        result=SimpleNamespace(
            collector="rss_feeds",
            status="success",
            signals_found=3,
            dry_run=True,
        ),
        state_path=state_path,
        config_path=config_path,
        runner="test",
    )

    recovered = load_collector_state(state_path, config_path=config_path)["collectors"]["rss_feeds"]
    assert recovered["last_run_status"] == "success"
    assert recovered["effective_status"] == "healthy"
    assert recovered["health"] == "ok"
    assert recovered["consecutive_failures"] == 0
    assert recovered["last_success_at"] is not None
    # Last error is preserved for forensic display by the health CLI.
    assert recovered["last_error_at"] == failed["last_error_at"]


def test_record_collector_heartbeat_tracks_skips(tmp_path):
    state_path = tmp_path / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  telegram:
    configured_status: enabled
    expected_cadence_hours: 6
""",
    )

    record_collector_heartbeat(
        result=SimpleNamespace(
            collector="telegram",
            status="skipped",
            error_message="No TELEGRAM_API_ID or TELEGRAM_API_HASH configured",
            dry_run=True,
        ),
        state_path=state_path,
        config_path=config_path,
        runner="test",
    )

    skipped = load_collector_state(state_path, config_path=config_path)["collectors"]["telegram"]
    assert skipped["last_run_status"] == "skipped"
    assert skipped["effective_status"] == "skipped"
    assert skipped["health"] == "skipped"
    assert skipped["consecutive_failures"] == 0
    assert skipped["consecutive_skips"] == 1
    assert skipped["last_skip_at"] is not None


def test_initialize_collector_state_materializes_configured_collectors(tmp_path, monkeypatch):
    state_path = tmp_path / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
    required_env: [GITHUB_TOKEN]
  sec_edgar:
    configured_status: enabled
    expected_cadence_hours: 24
  community_keywords:
    configured_status: disabled_intentional
    expected_cadence_hours: 168
    disabled_reason: Keyword module only.
""",
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    state = initialize_collector_state(
        state_path,
        config_path=config_path,
        runner="test-bootstrap",
    )

    assert set(state["collectors"]) == {"github", "sec_edgar", "community_keywords"}
    github = state["collectors"]["github"]
    assert github["configured_status"] == "disabled_missing_key"
    assert github["last_run_status"] == "not_run"
    assert github["effective_status"] == "disabled_missing_key"
    assert github["health"] == "disabled"
    assert github["expected_cadence_hours"] == 24

    sec = state["collectors"]["sec_edgar"]
    assert sec["configured_status"] == "enabled"
    assert sec["last_run_status"] == "not_run"
    assert sec["effective_status"] == "not_run"
    assert sec["health"] == "not_run"

    module = state["collectors"]["community_keywords"]
    assert module["configured_status"] == "disabled_intentional"
    assert module["effective_status"] == "disabled_intentional"
    assert module["health"] == "disabled"


def test_heartbeat_does_not_overwrite_intentional_disabled_state(tmp_path):
    state_path = tmp_path / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "updated_at": None,
                "collectors": {
                    "github": {
                        "schema_version": SCHEMA_VERSION,
                        "collector": "github",
                        "configured_status": "disabled_intentional",
                        "configured_status_reason": "Operator pause during API incident.",
                        "expected_cadence_hours": 24,
                        "last_run_status": "not_run",
                        "effective_status": "disabled_intentional",
                        "health": "disabled",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    entry = record_collector_heartbeat(
        result=SimpleNamespace(
            collector="github",
            status="success",
            signals_found=3,
            dry_run=True,
        ),
        state_path=state_path,
        config_path=config_path,
        runner="test",
    )

    assert entry["configured_status"] == "disabled_intentional"
    assert entry["configured_status_reason"] == "Operator pause during API incident."
    assert entry["last_run_status"] == "success"
    assert entry["last_success_at"] is not None
    assert entry["effective_status"] == "disabled_intentional"
    assert entry["health"] == "disabled"


def test_successful_collector_becomes_stale_after_expected_cadence(tmp_path):
    state_path = tmp_path / "collectors.json"
    config_path = _write_config(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  rss_feeds:
    configured_status: enabled
    expected_cadence_hours: 1
""",
    )
    started = datetime.now(timezone.utc) - timedelta(hours=3, minutes=5)
    finished = datetime.now(timezone.utc) - timedelta(hours=3)

    entry = record_collector_heartbeat(
        result=SimpleNamespace(
            collector="rss_feeds",
            status="success",
            signals_found=1,
            dry_run=True,
        ),
        started_at=started,
        finished_at=finished,
        state_path=state_path,
        config_path=config_path,
        runner="test",
    )

    assert entry["last_run_status"] == "success"
    assert entry["effective_status"] == "stale"
    assert entry["health"] == "degraded"
