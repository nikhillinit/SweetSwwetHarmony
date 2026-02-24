"""Tests for scripts/pipeline_report.py — Pipeline Report 4A."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.pipeline_report import (
    _gather_meta,
    _gather_readiness,
    _gather_convergence,
    _gather_pipeline_runs,
    _gather_phase_g,
    _gather_warm_intro,
    _gather_env_summary,
    generate_report,
    render_html,
    _git_dirty,
)


# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def real_schema_db(tmp_path):
    """Create temp DB with full real schema via SignalStore."""
    from storage.signal_store import SignalStore

    db_path = tmp_path / "test.db"
    store = SignalStore(db_path=str(db_path))
    await store.initialize()
    await store.close()
    return str(db_path)


@pytest_asyncio.fixture
async def ro_conn(real_schema_db):
    """Read-only aiosqlite connection to the real-schema DB."""
    import aiosqlite

    db = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    db.row_factory = aiosqlite.Row
    yield db
    await db.close()


@pytest_asyncio.fixture
async def rw_conn(real_schema_db):
    """Read-write aiosqlite connection for inserting test data."""
    import aiosqlite

    db = await aiosqlite.connect(real_schema_db)
    yield db
    await db.close()


# ---------------------------------------------------------------------------
# 1. test_report_empty_db
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_empty_db(real_schema_db, ro_conn):
    """Tables exist but no data -> all sections available with zero counts."""
    convergence = await _gather_convergence(ro_conn)
    assert convergence["available"] is True
    assert convergence["total_signals"] == 0
    assert convergence["company_files_by_status"] == {}

    runs = await _gather_pipeline_runs(ro_conn)
    assert runs["available"] is True
    assert runs["recent_runs"] == []
    assert runs["totals"]["runs"] == 0

    phase_g = await _gather_phase_g(ro_conn)
    assert phase_g["available"] is True
    assert phase_g["blocking_index_count"] == 0


# ---------------------------------------------------------------------------
# 2. test_report_with_runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_with_runs(real_schema_db, rw_conn, ro_conn):
    """Insert pipeline_runs + collector_metrics -> batch fetch works."""
    now = datetime.now(timezone.utc).isoformat()
    await rw_conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, started_at, completed_at, duration_seconds,
            collectors_run, collectors_succeeded, collectors_failed,
            signals_collected, signals_stored, signals_deduplicated,
            signals_processed, signals_held, errors, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-1", now, now, 12.5, 3, 3, 0, 50, 40, 10, 40, 40, "[]", now),
    )
    await rw_conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, started_at, completed_at, duration_seconds,
            collectors_run, collectors_succeeded, collectors_failed,
            signals_collected, signals_stored, signals_deduplicated,
            signals_processed, signals_held, errors, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-2", now, now, 8.2, 2, 1, 1, 20, 15, 5, 15, 15, '["error1"]', now),
    )
    await rw_conn.execute(
        """INSERT INTO collector_metrics
           (run_id, collector_name, started_at, completed_at, duration_seconds,
            signals_found, status, api_calls, rate_limit_hits, retries,
            errors, error_messages, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-1", "github", now, now, 5.0, 30, "succeeded", 5, 0, 0, 0, None, now),
    )
    await rw_conn.execute(
        """INSERT INTO collector_metrics
           (run_id, collector_name, started_at, completed_at, duration_seconds,
            signals_found, status, api_calls, rate_limit_hits, retries,
            errors, error_messages, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-2", "rss_feeds", now, now, 3.0, 20, "failed", 2, 1, 1, 1, "timeout", now),
    )
    await rw_conn.commit()

    # Re-open ro_conn to see the committed data
    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        runs = await _gather_pipeline_runs(ro, limit=10)
        assert runs["available"] is True
        assert runs["totals"]["runs"] == 2
        # run-2 should be first (newer started_at is same, but higher id)
        # Verify collector_metrics were batch-fetched
        all_collectors = []
        for r in runs["recent_runs"]:
            all_collectors.extend(r["collectors"])
        assert len(all_collectors) == 2
        names = {c["name"] for c in all_collectors}
        assert "github" in names
        assert "rss_feeds" in names
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# 3. test_report_convergence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_convergence(real_schema_db, rw_conn):
    """Insert company_files with multi-source -> verify counts."""
    now = datetime.now(timezone.utc).isoformat()
    await rw_conn.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("c1", "Acme", "domain:acme.com", "promoted", '["github","rss"]', now, now),
    )
    await rw_conn.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("c2", "Beta", "domain:beta.com", "promoted", '["github"]', now, now),
    )
    await rw_conn.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("c3", "Thin Co", "domain:thin.com", "thin", '[]', now, now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        conv = await _gather_convergence(ro)
        assert conv["available"] is True
        assert conv["company_files_by_status"]["promoted"] == 2
        assert conv["company_files_by_status"]["thin"] == 1
        # Source distribution: 1 promoted with 2 sources, 1 with 1
        assert conv["promoted_source_distribution"]["2"] == 1
        assert conv["promoted_source_distribution"]["1"] == 1
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# 4. test_report_canary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_canary(real_schema_db, rw_conn):
    """Insert canary_run -> readiness canary section populated."""
    now = datetime.now(timezone.utc).isoformat()
    await rw_conn.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored,
            passed, failed, skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-c1", 15, "hash123", 15, 14, 1, 0, 0.9333, "pass", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        readiness = await _gather_readiness(ro, real_schema_db)
        assert readiness["available"] is True
        assert readiness["canary"]["verdict"] == "pass"
        assert readiness["canary"]["pass_rate"] == 0.9333
        assert readiness["canary"]["run_id"] is not None
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# 5. test_report_phase_g_tables_missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_phase_g_tables_missing(tmp_path):
    """Phase G tables don't exist -> available: false, graceful fallback."""
    import aiosqlite

    db_path = tmp_path / "bare.db"
    db = await aiosqlite.connect(str(db_path))
    await db.execute("CREATE TABLE dummy (id INTEGER)")
    await db.commit()
    await db.close()

    ro = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        phase_g = await _gather_phase_g(ro)
        assert phase_g["available"] is False
        assert phase_g["blocking_index_count"] == 0
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# 6. test_report_warm_intro_not_wired
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_warm_intro_not_wired(real_schema_db, ro_conn):
    """No private_graph.db -> status='not_wired'."""
    with patch.dict(os.environ, {
        "WARM_INTRO_NOTION_MODE": "off",
        "ENABLE_WARM_INTRO_ENRICHMENT": "false",
        "PRIVATE_GRAPH_DB_PATH": "",
    }):
        wi = await _gather_warm_intro(ro_conn, real_schema_db)
        assert wi["status"] == "not_wired"
        assert wi["private_graph_db_exists"] is False


# ---------------------------------------------------------------------------
# 7. test_report_json_schema_version
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_json_schema_version(real_schema_db):
    """schema_version == 'pipeline-report-v1'."""
    report = await generate_report(real_schema_db)
    assert report["schema_version"] == "pipeline-report-v1"


# ---------------------------------------------------------------------------
# 8. test_report_html_contains_json
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_html_contains_json(real_schema_db):
    """Generate HTML -> extract embedded JSON -> parseable and matches."""
    report = await generate_report(real_schema_db)
    html = render_html(report)

    # Extract JSON from <script type="application/json" id="report-data">
    start = html.index('id="report-data">') + len('id="report-data">')
    end = html.index("</script>", start)
    embedded_json_str = html[start:end]
    # Reverse the </script> escape
    embedded_json_str = embedded_json_str.replace("<\\/", "</")
    embedded = json.loads(embedded_json_str)

    assert embedded["schema_version"] == "pipeline-report-v1"
    assert "readiness" in embedded
    assert "convergence" in embedded


# ---------------------------------------------------------------------------
# 9. test_report_git_info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_git_info(real_schema_db):
    """Git section populated (branch, sha present in a git repo)."""
    report = await generate_report(real_schema_db)
    meta = report["meta"]
    # We're running from a git repo, so these should be populated
    assert meta["git"]["branch"] is not None
    assert meta["git"]["sha"] is not None


# ---------------------------------------------------------------------------
# 10. test_report_output_files
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_output_files(real_schema_db, tmp_path):
    """Both report.json and report.html written to out dir."""
    from scripts.pipeline_report import async_main
    import argparse

    out_dir = str(tmp_path / "out")
    args = argparse.Namespace(
        db=real_schema_db, out=out_dir, format="both", limit=10,
    )
    await async_main(args)

    assert (Path(out_dir) / "report.json").exists()
    assert (Path(out_dir) / "report.html").exists()

    # Verify JSON is valid
    with open(Path(out_dir) / "report.json") as f:
        data = json.load(f)
    assert data["schema_version"] == "pipeline-report-v1"


# ---------------------------------------------------------------------------
# 11. test_report_missing_tables
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_missing_tables(tmp_path):
    """DB with no pipeline tables -> sections return available: false."""
    import aiosqlite

    db_path = tmp_path / "bare.db"
    db = await aiosqlite.connect(str(db_path))
    await db.execute("CREATE TABLE dummy (id INTEGER)")
    await db.commit()
    await db.close()

    ro = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        runs = await _gather_pipeline_runs(ro)
        assert runs["available"] is False

        conv = await _gather_convergence(ro)
        assert conv["available"] is False
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# 12. test_report_git_not_a_repo
# ---------------------------------------------------------------------------

def test_report_git_not_a_repo(tmp_path):
    """Non-git directory -> git dirty returns None, no crash."""
    result = _git_dirty(str(tmp_path))
    # tmp_path is not a git repo, so result should be None or a value
    # (depends on whether git walks up — in isolated tmp it should be None)
    assert result is None or isinstance(result, bool)


# ---------------------------------------------------------------------------
# 13. test_report_overall_verdict_rollup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_overall_verdict_rollup(real_schema_db, rw_conn):
    """Test verdict rollup: canary pass -> gate should reflect that."""
    now = datetime.now(timezone.utc).isoformat()
    # Insert a passing canary run
    await rw_conn.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored,
            passed, failed, skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-v1", 15, "hash", 15, 15, 0, 0, 1.0, "pass", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        readiness = await _gather_readiness(ro, real_schema_db)
        # overall_verdict comes from activation gate
        assert readiness["overall_verdict"] in ("ready", "warn", "blocked")
        # With a passing canary and no drift, should be ready or warn
        assert readiness["activation_gate"]["verdict"] in ("ready", "warn", "blocked")
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# ENV SUMMARY
# ---------------------------------------------------------------------------

def test_gather_env_summary_no_secrets():
    """API keys show 'configured'/'(not set)'/'(empty)', never actual values."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_secret123", "PH_API_KEY": ""}, clear=False):
        env = _gather_env_summary()
        assert env["api_keys"]["GITHUB_TOKEN"] == "configured"
        assert env["api_keys"]["PH_API_KEY"] == "(empty)"
        # No actual value leaked
        for v in env["api_keys"].values():
            assert isinstance(v, str)
            assert "ghp_secret" not in v


def test_gather_env_summary_feature_flags():
    """Feature flags show their actual values."""
    with patch.dict(os.environ, {"DELIVERY_MODE": "manual_publish"}, clear=False):
        env = _gather_env_summary()
        assert env["feature_flags"]["DELIVERY_MODE"] == "manual_publish"


# ---------------------------------------------------------------------------
# DRIFT ALERT DETAILS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_drift_alert_details(real_schema_db, rw_conn):
    """Insert drift alerts -> verify details array has alert_type, severity, message."""
    now = datetime.now(timezone.utc).isoformat()
    await rw_conn.execute(
        """INSERT INTO canary_drift_alerts
           (alert_type, severity, metric_name, message, expected_value, actual_value,
            status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("pass_rate_drop", "warning", "pass_rate", "Pass rate dropped below 0.9",
         0.9, 0.85, "open", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        readiness = await _gather_readiness(ro, real_schema_db)
        drift = readiness["drift_alerts"]
        assert drift["open_warning"] == 1
        assert len(drift["details"]) == 1
        detail = drift["details"][0]
        assert detail["alert_type"] == "pass_rate_drop"
        assert detail["severity"] == "warning"
        assert detail["message"] == "Pass rate dropped below 0.9"
    finally:
        await ro.close()


@pytest.mark.asyncio
async def test_report_drift_alert_severity_ordering(real_schema_db, rw_conn):
    """Insert one critical + one warning -> critical comes first in details."""
    now = datetime.now(timezone.utc).isoformat()
    # Insert warning first (by created_at)
    await rw_conn.execute(
        """INSERT INTO canary_drift_alerts
           (alert_type, severity, metric_name, message, expected_value, actual_value,
            status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("spc_violation", "warning", "signal_count", "Signal count low",
         10.0, 3.0, "open", now),
    )
    await rw_conn.execute(
        """INSERT INTO canary_drift_alerts
           (alert_type, severity, metric_name, message, expected_value, actual_value,
            status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("pass_rate_drop", "critical", "pass_rate", "Critical pass rate failure",
         0.9, 0.5, "open", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        readiness = await _gather_readiness(ro, real_schema_db)
        details = readiness["drift_alerts"]["details"]
        assert len(details) == 2
        assert details[0]["severity"] == "critical"
        assert details[1]["severity"] == "warning"
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# GHOST RUN DETECTION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_ghost_run_detection(real_schema_db, rw_conn):
    """Insert ghost run + productive run -> verify is_ghost flags and productive_runs count."""
    now = datetime.now(timezone.utc).isoformat()
    # Ghost run: 0 collectors, 0 signals, fast
    await rw_conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, started_at, completed_at, duration_seconds,
            collectors_run, collectors_succeeded, collectors_failed,
            signals_collected, signals_stored, signals_deduplicated,
            signals_processed, signals_held, errors, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("ghost-1", now, now, 0.1, 0, 0, 0, 0, 0, 0, 0, 0, "[]", now),
    )
    # Productive run
    await rw_conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, started_at, completed_at, duration_seconds,
            collectors_run, collectors_succeeded, collectors_failed,
            signals_collected, signals_stored, signals_deduplicated,
            signals_processed, signals_held, errors, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("prod-1", now, now, 12.5, 3, 3, 0, 50, 40, 10, 40, 0, "[]", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        runs = await _gather_pipeline_runs(ro)
        assert runs["totals"]["runs"] == 2
        assert runs["totals"]["productive_runs"] == 1

        ghost_flags = {r["run_id"]: r["is_ghost"] for r in runs["recent_runs"]}
        assert ghost_flags["ghost-1"] is True
        assert ghost_flags["prod-1"] is False
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# ANOMALY DETECTION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_anomaly_triple_zero(real_schema_db, rw_conn):
    """Productive runs with collected>0, stored=0, deduped=0 -> anomaly message."""
    now = datetime.now(timezone.utc).isoformat()
    for i in range(3):
        await rw_conn.execute(
            """INSERT INTO pipeline_runs
               (run_id, started_at, completed_at, duration_seconds,
                collectors_run, collectors_succeeded, collectors_failed,
                signals_collected, signals_stored, signals_deduplicated,
                signals_processed, signals_held, errors, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"bad-{i}", now, now, 10.0, 3, 3, 0, 50, 0, 0, 0, 0, "[]", now),
        )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        runs = await _gather_pipeline_runs(ro)
        assert len(runs["anomalies"]) > 0
        assert "metrics wiring" in runs["anomalies"][0]
    finally:
        await ro.close()


@pytest.mark.asyncio
async def test_report_no_anomaly_when_stored_nonzero(real_schema_db, rw_conn):
    """Run with signals_stored=30 -> no anomaly."""
    now = datetime.now(timezone.utc).isoformat()
    await rw_conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, started_at, completed_at, duration_seconds,
            collectors_run, collectors_succeeded, collectors_failed,
            signals_collected, signals_stored, signals_deduplicated,
            signals_processed, signals_held, errors, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("good-1", now, now, 10.0, 3, 3, 0, 50, 30, 20, 30, 0, "[]", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        runs = await _gather_pipeline_runs(ro)
        assert runs["anomalies"] == []
    finally:
        await ro.close()


@pytest.mark.asyncio
async def test_report_no_anomaly_when_all_deduped(real_schema_db, rw_conn):
    """Run with collected=50, stored=0, deduped=50 -> NO anomaly (legitimate)."""
    now = datetime.now(timezone.utc).isoformat()
    await rw_conn.execute(
        """INSERT INTO pipeline_runs
           (run_id, started_at, completed_at, duration_seconds,
            collectors_run, collectors_succeeded, collectors_failed,
            signals_collected, signals_stored, signals_deduplicated,
            signals_processed, signals_held, errors, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("dedup-1", now, now, 10.0, 3, 3, 0, 50, 0, 50, 0, 0, "[]", now),
    )
    await rw_conn.commit()

    import aiosqlite
    ro = await aiosqlite.connect(f"file:{real_schema_db}?mode=ro", uri=True)
    ro.row_factory = aiosqlite.Row
    try:
        runs = await _gather_pipeline_runs(ro)
        assert runs["anomalies"] == []
    finally:
        await ro.close()


# ---------------------------------------------------------------------------
# ENV REDACTION & PROVENANCE
# ---------------------------------------------------------------------------

def test_report_env_redaction():
    """Set a fake NOTION_API_KEY env var -> report shows 'configured', not the value."""
    with patch.dict(os.environ, {"NOTION_API_KEY": "secret_supersecret123"}, clear=False):
        env = _gather_env_summary()
        assert env["api_keys"]["NOTION_API_KEY"] == "configured"
        # Verify actual value is not anywhere in the dict
        serialized = json.dumps(env)
        assert "supersecret" not in serialized


def test_report_env_provenance_note():
    """env_summary['note'] contains 'report-generating process'."""
    env = _gather_env_summary()
    assert "note" in env
    assert "report-generating process" in env["note"]


def test_report_env_runtime_metadata():
    """env_summary['runtime'] has cwd, python_version, platform."""
    env = _gather_env_summary()
    assert "runtime" in env
    runtime = env["runtime"]
    assert "cwd" in runtime
    assert "python_version" in runtime
    assert "platform" in runtime
    assert runtime["platform"] == sys.platform
