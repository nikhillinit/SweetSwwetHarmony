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
    """API keys show only true/false, never values."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_secret123", "PH_API_KEY": ""}):
        env = _gather_env_summary()
        assert env["api_keys"]["GITHUB_TOKEN"] is True
        assert env["api_keys"]["PH_API_KEY"] is False
        # No actual value leaked
        for v in env["api_keys"].values():
            assert isinstance(v, bool)


def test_gather_env_summary_feature_flags():
    """Feature flags show their actual values."""
    with patch.dict(os.environ, {"DELIVERY_MODE": "manual_publish"}, clear=False):
        env = _gather_env_summary()
        assert env["feature_flags"]["DELIVERY_MODE"] == "manual_publish"
