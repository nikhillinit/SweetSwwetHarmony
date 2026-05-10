from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.collector_health import (
    DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR,
    DEFAULT_LOOKBACK_DAYS,
    aggregate_outbox_health,
    aggregate_signal_counts,
    build_health_report,
    main,
    render_table,
)


def _write_collectors_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _create_signals_schema(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT NOT NULL,
                source_api TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                company_name TEXT,
                confidence REAL NOT NULL,
                raw_data TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()


def _create_notion_outbox_schema(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS notion_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()


def _insert_signal(
    db_path: Path,
    *,
    signal_type: str,
    source_api: str,
    detected_at: datetime,
    canonical_key: str = "domain:test.example",
) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO signals(signal_type, source_api, canonical_key, company_name, "
            "confidence, raw_data, detected_at, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                signal_type,
                source_api,
                canonical_key,
                "Acme",
                0.5,
                "{}",
                detected_at.isoformat(),
                detected_at.isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()


def test_aggregate_signal_counts_groups_by_signal_type_and_source_api(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_signals_schema(db_path)
    now = datetime.now(timezone.utc)

    _insert_signal(db_path, signal_type="research_paper", source_api="arxiv", detected_at=now)
    _insert_signal(db_path, signal_type="research_paper", source_api="arxiv", detected_at=now)
    _insert_signal(
        db_path, signal_type="hacker_news_mention", source_api="hacker_news", detected_at=now
    )

    rows = aggregate_signal_counts(db_path)
    by_key = {(r["signal_type"], r["source_api"]): r["count"] for r in rows}
    assert by_key[("research_paper", "arxiv")] == 2
    assert by_key[("hacker_news_mention", "hacker_news")] == 1


def test_aggregate_signal_counts_respects_lookback_window(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_signals_schema(db_path)
    recent = datetime.now(timezone.utc)
    old = recent - timedelta(days=DEFAULT_LOOKBACK_DAYS + 5)

    _insert_signal(db_path, signal_type="press_release", source_api="rss_feeds", detected_at=recent)
    _insert_signal(db_path, signal_type="press_release", source_api="rss_feeds", detected_at=old)

    rows = aggregate_signal_counts(db_path)
    by_key = {(r["signal_type"], r["source_api"]): r["count"] for r in rows}
    assert by_key[("press_release", "rss_feeds")] == 1


def test_aggregate_signal_counts_returns_empty_for_missing_table(tmp_path):
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    rows = aggregate_signal_counts(db_path)
    assert rows == []


def test_aggregate_outbox_health_reports_due_failed_and_stale_work(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_notion_outbox_schema(db_path)
    now = datetime.now(timezone.utc)
    stale = now - timedelta(hours=2)
    future = now + timedelta(hours=2)

    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            """
            INSERT INTO notion_outbox(
                idempotency_key, payload_json, status, next_attempt_at,
                last_error, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?)
            """,
            [
                ("due", "{}", "pending", None, None, now.isoformat(), now.isoformat()),
                (
                    "future",
                    "{}",
                    "pending",
                    future.isoformat(),
                    None,
                    now.isoformat(),
                    now.isoformat(),
                ),
                (
                    "stale-processing",
                    "{}",
                    "processing",
                    None,
                    None,
                    stale.isoformat(),
                    stale.isoformat(),
                ),
                (
                    "failed",
                    "{}",
                    "failed",
                    None,
                    "nope",
                    now.isoformat(),
                    now.isoformat(),
                ),
                (
                    "sent",
                    "{}",
                    "sent",
                    None,
                    None,
                    stale.isoformat(),
                    now.isoformat(),
                ),
            ],
        )
        con.commit()
    finally:
        con.close()

    summary = aggregate_outbox_health(db_path, stale_processing_minutes=60)

    assert summary["available"] is True
    assert summary["pending_count"] == 2
    assert summary["pending_due_count"] == 1
    assert summary["processing_count"] == 1
    assert summary["stale_processing_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["last_successful_send_at"] == now.isoformat()


def test_build_health_report_marks_silent_enabled_collector(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
  sec_edgar:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    # Initialize so that both collectors are present in the state.
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")

    signal_counts = [
        {"signal_type": "research_paper", "source_api": "arxiv", "count": 10},
    ]

    report = build_health_report(
        state,
        signal_counts,
        expected_source_apis_by_collector={
            "arxiv": ("arxiv",),
            "sec_edgar": ("sec_edgar",),
        },
    )

    by_name = {c["name"]: c for c in report["collectors"]}
    assert by_name["arxiv"]["is_silent"] is False
    assert by_name["arxiv"]["observed_signal_count"] == 10
    assert by_name["sec_edgar"]["is_silent"] is True
    assert by_name["sec_edgar"]["observed_signal_count"] == 0
    assert any("sec_edgar" in w for w in report["summary"]["warnings"])
    assert report["summary"]["silent_count"] == 1


def test_build_health_report_marks_alive_but_no_db_progress(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state = {
        "schema_version": 2,
        "updated_at": None,
        "collectors": {
            "github": {
                "schema_version": 2,
                "collector": "github",
                "configured_status": "enabled",
                "expected_cadence_hours": 24,
                "last_run_status": "success",
                "last_finished_at": datetime.now(timezone.utc).isoformat(),
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_failures": 0,
                "effective_status": "healthy",
                "data_version_before": 10,
                "data_version_after": 10,
                "rows_inserted_this_iter": 0,
                "rows_total_last_24h": 0,
                "collector_class": "GitHubCollector",
            }
        },
    }

    report = build_health_report(
        state,
        signal_counts=[],
        config_path=config_path,
        expected_source_apis_by_collector={"github": ("github",)},
    )

    github = next(c for c in report["collectors"] if c["name"] == "github")
    assert github["is_alive_but_no_db_progress"] is True
    assert github["is_silent"] is True
    assert report["summary"]["alive_but_no_db_progress_count"] == 1
    assert report["summary"]["alive_but_no_db_progress_collectors"] == ["github"]
    assert any("no DB progress" in w for w in report["summary"]["warnings"])


def test_build_health_report_no_progress_ignores_historical_lookback_rows(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state = {
        "schema_version": 2,
        "updated_at": None,
        "collectors": {
            "github": {
                "schema_version": 2,
                "collector": "github",
                "configured_status": "enabled",
                "expected_cadence_hours": 24,
                "last_run_status": "success",
                "last_finished_at": datetime.now(timezone.utc).isoformat(),
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_failures": 0,
                "effective_status": "healthy",
                "data_version_before": 20,
                "data_version_after": 20,
                "rows_inserted_this_iter": 0,
                "rows_total_last_24h": 5,
                "collector_class": "GitHubCollector",
            }
        },
    }

    report = build_health_report(
        state,
        signal_counts=[
            {"signal_type": "github_repo", "source_api": "github", "count": 5}
        ],
        config_path=config_path,
        expected_source_apis_by_collector={"github": ("github",)},
        outbox_health={
            "available": True,
            "pending_due_count": 1,
            "failed_count": 0,
            "stale_processing_count": 0,
        },
    )

    github = next(c for c in report["collectors"] if c["name"] == "github")
    assert github["observed_signal_count"] == 5
    assert github["rows_total_last_24h"] == 5
    assert github["is_alive_but_no_db_progress"] is True
    assert report["summary"]["producer_progress_seen"] is False
    assert report["summary"]["db_progressed_but_drain_stalled"] is False


def test_build_health_report_marks_db_progressed_but_drain_stalled(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state = {
        "schema_version": 2,
        "updated_at": None,
        "collectors": {
            "arxiv": {
                "schema_version": 2,
                "collector": "arxiv",
                "configured_status": "enabled",
                "expected_cadence_hours": 24,
                "last_run_status": "success",
                "last_finished_at": datetime.now(timezone.utc).isoformat(),
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "consecutive_failures": 0,
                "effective_status": "healthy",
                "data_version_before": 10,
                "data_version_after": 12,
                "rows_inserted_this_iter": 2,
                "rows_total_last_24h": 2,
                "collector_class": "ArxivCollector",
            }
        },
    }

    report = build_health_report(
        state,
        signal_counts=[
            {"signal_type": "research_paper", "source_api": "arxiv", "count": 2}
        ],
        config_path=config_path,
        expected_source_apis_by_collector={"arxiv": ("arxiv",)},
        outbox_health={
            "available": True,
            "pending_due_count": 1,
            "failed_count": 0,
            "stale_processing_count": 0,
        },
    )

    assert report["summary"]["producer_progress_seen"] is True
    assert report["summary"]["db_progressed_but_drain_stalled"] is True
    assert any("Notion outbox" in w for w in report["summary"]["warnings"])


def test_build_health_report_does_not_flag_disabled_collectors_as_silent(tmp_path, monkeypatch):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
    required_env: [GITHUB_TOKEN]
  community_keywords:
    configured_status: disabled_intentional
    expected_cadence_hours: 168
    disabled_reason: Keyword module only.
""",
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")

    report = build_health_report(
        state,
        signal_counts=[],
        expected_source_apis_by_collector={
            "github": ("github",),
            "community_keywords": (),
        },
    )

    by_name = {c["name"]: c for c in report["collectors"]}
    assert by_name["github"]["effective_status"] == "disabled_missing_key"
    assert by_name["github"]["is_silent"] is False
    assert by_name["community_keywords"]["effective_status"] == "disabled_intentional"
    assert by_name["community_keywords"]["is_silent"] is False
    assert report["summary"]["silent_count"] == 0


def test_build_health_report_marks_stale_from_heartbeat_state(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  rss_feeds:
    configured_status: enabled
    expected_cadence_hours: 1
""",
    )
    state_path = tmp_path / "collectors.json"
    # Record a successful run that was 3 hours ago to force stale.
    from types import SimpleNamespace
    from ops.collector_heartbeat import record_collector_heartbeat

    finished = datetime.now(timezone.utc) - timedelta(hours=3)
    record_collector_heartbeat(
        result=SimpleNamespace(
            collector="rss_feeds",
            status="success",
            signals_found=1,
            dry_run=True,
        ),
        finished_at=finished,
        state_path=state_path,
        config_path=config_path,
        runner="test",
    )

    from ops.collector_heartbeat import load_collector_state

    state = load_collector_state(state_path, config_path=config_path)

    report = build_health_report(
        state,
        signal_counts=[
            {"signal_type": "press_release", "source_api": "rss_feeds", "count": 4}
        ],
        expected_source_apis_by_collector={"rss_feeds": ("rss_feeds",)},
    )

    rss = next(c for c in report["collectors"] if c["name"] == "rss_feeds")
    assert rss["effective_status"] == "stale"
    assert rss["is_stale"] is True
    assert rss["is_silent"] is False
    assert report["summary"]["stale_count"] == 1


def test_build_health_report_summary_breaks_down_by_effective_status(tmp_path, monkeypatch):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
  github:
    configured_status: enabled
    expected_cadence_hours: 24
    required_env: [GITHUB_TOKEN]
""",
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")

    report = build_health_report(
        state,
        signal_counts=[
            {"signal_type": "research_paper", "source_api": "arxiv", "count": 5}
        ],
        expected_source_apis_by_collector={"arxiv": ("arxiv",), "github": ("github",)},
    )

    by_status = report["summary"]["by_effective_status"]
    assert by_status["not_run"] == 1
    assert by_status["disabled_missing_key"] == 1
    assert report["summary"]["total"] == 2


def test_build_health_report_lists_unmapped_source_apis(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")

    report = build_health_report(
        state,
        signal_counts=[
            {"signal_type": "research_paper", "source_api": "arxiv", "count": 5},
            {"signal_type": "news_mention", "source_api": "manual_seed_buzz", "count": 3},
        ],
        expected_source_apis_by_collector={"arxiv": ("arxiv",)},
    )
    unmapped = report["summary"]["unmapped_source_apis"]
    assert "manual_seed_buzz" in unmapped


def test_render_table_includes_collector_status_and_signal_count(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")
    report = build_health_report(
        state,
        signal_counts=[{"signal_type": "research_paper", "source_api": "arxiv", "count": 7}],
        expected_source_apis_by_collector={"arxiv": ("arxiv",)},
    )

    rendered = render_table(report)
    assert "arxiv" in rendered
    assert "not_run" in rendered  # no heartbeat recorded yet
    # observed signal count should be reflected
    assert "7" in rendered


def test_main_emits_json_to_stdout_when_format_json(tmp_path, capsys, monkeypatch):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    db_path = tmp_path / "signals.db"
    _create_signals_schema(db_path)
    _insert_signal(
        db_path,
        signal_type="research_paper",
        source_api="arxiv",
        detected_at=datetime.now(timezone.utc),
    )

    monkeypatch.setenv("COLLECTOR_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("COLLECTOR_STATE_PATH", str(state_path))

    exit_code = main(
        [
            "--db",
            str(db_path),
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["db_path"].endswith("signals.db")
    assert payload["lookback_days"] == DEFAULT_LOOKBACK_DAYS
    assert any(c["name"] == "arxiv" for c in payload["collectors"])


def test_main_does_not_modify_database(tmp_path, monkeypatch):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    db_path = tmp_path / "signals.db"
    _create_signals_schema(db_path)
    _insert_signal(
        db_path,
        signal_type="research_paper",
        source_api="arxiv",
        detected_at=datetime.now(timezone.utc),
    )

    monkeypatch.setenv("COLLECTOR_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("COLLECTOR_STATE_PATH", str(state_path))

    before = db_path.stat().st_mtime_ns
    main(["--db", str(db_path), "--format", "json"])
    after = db_path.stat().st_mtime_ns
    assert before == after, "Health CLI must not modify the signals database"


def test_build_health_report_marks_override_when_state_overrides_yaml(tmp_path):
    """YAML says enabled, state stickily flipped to disabled_intentional → override_active=True."""
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    # Hand-craft a state that has stickily flipped arxiv to disabled_intentional.
    # In production this happens via heartbeat preserving sticky operator state.
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "updated_at": "2026-04-27T00:00:00+00:00",
                "collectors": {
                    "arxiv": {
                        "schema_version": 2,
                        "collector": "arxiv",
                        "configured_status": "disabled_intentional",
                        "configured_status_reason": "operator paused upstream API issue",
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

    from ops.collector_heartbeat import load_collector_state

    state = load_collector_state(state_path, config_path=config_path)
    report = build_health_report(
        state,
        signal_counts=[],
        config_path=config_path,
        expected_source_apis_by_collector={"arxiv": ("arxiv",)},
    )

    arxiv = next(c for c in report["collectors"] if c["name"] == "arxiv")
    assert arxiv["override_active"] is True
    assert report["summary"]["override_active_count"] == 1
    assert "arxiv" in report["summary"]["override_active_collectors"]
    assert any("operator override active" in w for w in report["summary"]["warnings"])


def test_build_health_report_no_override_when_state_matches_yaml(tmp_path):
    """YAML says disabled_intentional, state matches → override_active=False."""
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  community_keywords:
    configured_status: disabled_intentional
    expected_cadence_hours: 168
    disabled_reason: Module-only collector.
""",
    )
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")
    report = build_health_report(
        state,
        signal_counts=[],
        config_path=config_path,
        expected_source_apis_by_collector={"community_keywords": ()},
    )

    ck = next(c for c in report["collectors"] if c["name"] == "community_keywords")
    assert ck["configured_status"] == "disabled_intentional"
    assert ck["override_active"] is False
    assert report["summary"]["override_active_count"] == 0


def test_build_health_report_no_override_when_state_is_enabled(tmp_path):
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  arxiv:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")
    report = build_health_report(
        state,
        signal_counts=[],
        config_path=config_path,
        expected_source_apis_by_collector={"arxiv": ("arxiv",)},
    )

    arxiv = next(c for c in report["collectors"] if c["name"] == "arxiv")
    assert arxiv["override_active"] is False


def test_build_health_report_no_override_when_yaml_blocked_state_blocked(tmp_path):
    """Both YAML and state declare blocked_access — that's intent matching, not override."""
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  linkedin:
    configured_status: blocked_access
    expected_cadence_hours: 168
    disabled_reason: Anti-scraping defenses tripped 2026-03-01.
""",
    )
    state_path = tmp_path / "collectors.json"
    from ops.collector_heartbeat import initialize_collector_state

    state = initialize_collector_state(state_path, config_path=config_path, runner="test")
    report = build_health_report(
        state,
        signal_counts=[],
        config_path=config_path,
        expected_source_apis_by_collector={"linkedin": ("linkedin",)},
    )

    li = next(c for c in report["collectors"] if c["name"] == "linkedin")
    assert li["configured_status"] == "blocked_access"
    assert li["override_active"] is False


def test_render_table_shows_override_flag(tmp_path):
    """OVERRIDE flag should appear in the flags column when override_active is true."""
    config_path = _write_collectors_yaml(
        tmp_path / "collectors.yaml",
        """
schema_version: 1
collectors:
  github:
    configured_status: enabled
    expected_cadence_hours: 24
""",
    )
    state_path = tmp_path / "collectors.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "updated_at": "2026-04-27T00:00:00+00:00",
                "collectors": {
                    "github": {
                        "schema_version": 2,
                        "collector": "github",
                        "configured_status": "blocked_access",
                        "expected_cadence_hours": 24,
                        "last_run_status": "not_run",
                        "effective_status": "blocked_access",
                        "health": "disabled",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    from ops.collector_heartbeat import load_collector_state

    state = load_collector_state(state_path, config_path=config_path)
    report = build_health_report(
        state,
        signal_counts=[],
        config_path=config_path,
        expected_source_apis_by_collector={"github": ("github",)},
    )

    rendered = render_table(report)
    assert "OVERRIDE" in rendered
    assert "override_active=1" in rendered


def test_default_mapping_covers_known_collectors():
    # Sanity: the registry knows about the collectors most likely to be silent.
    assert "sec_edgar" in DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR
    assert "arxiv" in DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR
    assert "job_postings" in DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR
    # job_postings emits multiple source_api values today.
    job_apis = DEFAULT_EXPECTED_SOURCE_APIS_BY_COLLECTOR["job_postings"]
    assert "greenhouse_jobs" in job_apis
    assert "lever_jobs" in job_apis
