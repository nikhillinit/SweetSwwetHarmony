from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ADR = ROOT / "docs" / "decisions" / "0004-runner-liveness-reenable.md"
RUNBOOK = ROOT / "docs" / "runbooks" / "runner-liveness-reenable.md"
DB_POLICY = ROOT / "docs" / "runbooks" / "db-ops-policy.md"


def test_runner_liveness_has_sibling_adr_and_runbook() -> None:
    adr = ADR.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "Status: Accepted" in adr
    assert "ADR-043" in adr
    assert "outside DB durability" in adr
    assert "HarmonicKeepAlive" in adr
    assert "HarmonicFreezeDrill" in adr
    assert "Healthchecks.io" in adr
    assert "YYYY-MM-DD-<TaskName>.json" in adr
    assert "--min-created-at" in adr
    assert "no_post_run_rows" in adr
    assert "daily_heartbeat" in adr
    assert "strict_write_proof" in adr
    assert "WARN_DUPLICATE_ONLY" in adr
    assert "freshness_watchdog.py` stays strict" in adr
    assert "pre-monitor composite artifact" in adr
    assert "raw_watchdog_compat" in adr
    assert "--watchdog-json" in adr
    assert "monitor_delivery_status" in adr

    assert "artifacts/keepalive/2026-05-12-freeze-drill-readout.md" in runbook
    assert "signals.created_at" in runbook
    assert "YYYY-MM-DD-<TaskName>.json" in runbook
    assert "YYYY-MM-DD-<TaskName>.watchdog.json" in runbook
    assert "--min-created-at" in runbook
    assert "no_post_run_rows" in runbook
    assert "daily_heartbeat" in runbook
    assert "strict_write_proof" in runbook
    assert "WARN_DUPLICATE_ONLY" in runbook
    assert "pre-monitor composite artifact" in runbook
    assert "raw_watchdog_compat" in runbook
    assert "--watchdog-json" in runbook
    assert "finalizes the local" in runbook
    assert "required_after" in runbook
    assert "stale_reason" in runbook
    assert "keepalive.db_progress_status" in runbook
    assert "JOB_POSTING_DOMAINS" in runbook
    assert "greenhouse_jobs,ashby_jobs" in runbook
    assert "HARMONIC_KEEPALIVE_PING_URL" in runbook
    assert "HostMode" in runbook
    assert "MonitorAlertVerified" in runbook
    assert "collector_health" in runbook


def test_phase_5_2_policy_no_longer_owns_runner_liveness_details() -> None:
    policy = DB_POLICY.read_text(encoding="utf-8")
    phase_section = policy.split("## Phase 5.2 Cloud Durability Direction", maxsplit=1)[1]

    forbidden = [
        "HarmonicKeepAlive",
        "HarmonicFreezeDrill",
        "rss_feeds",
        "job_postings",
        "collector_health",
        "install_keepalive_task.ps1",
        "freshness_watchdog.py",
    ]
    for term in forbidden:
        assert term not in phase_section

    assert "runner-liveness ADR" in phase_section
    assert "remote-mounted SQLite" in phase_section
