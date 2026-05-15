from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from verification.verification_gate_v2 import Signal


def load_probe_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "red-team-hybrid"
        / "job_postings_source_yield_probe.py"
    )
    spec = importlib.util.spec_from_file_location(
        "job_postings_source_yield_probe", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = load_probe_module()


FIXTURE_DIR = Path("tests/fixtures/job_postings_source_yield_probe")


def make_args(**overrides):
    defaults = {
        "domains_file": None,
        "domains": "dual.example",
        "sources": "greenhouse,ashby",
        "db": None,
        "state": None,
        "keepalive_artifact": None,
        "fixture_dir": str(FIXTURE_DIR),
        "max_domains": 20,
        "mode": "source-isolated",
        "timeout": 5.0,
        "json_output": True,
        "out": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def make_signal(
    *,
    canonical_key: str = "domain:dual.example",
    source_api: str = "greenhouse_jobs",
    source_url: str = "https://boards.greenhouse.io/dual/jobs/101",
    detected_at: datetime | None = None,
) -> Signal:
    return Signal(
        id="sig-test",
        signal_type="hiring_signal",
        confidence=0.8,
        source_api=source_api,
        source_url=source_url,
        detected_at=detected_at or datetime(2026, 5, 14, 12, tzinfo=timezone.utc),
        raw_data={"canonical_key": canonical_key, "company_name": "Dual"},
    )


def make_candidate(**overrides):
    signal = overrides.pop("signal", make_signal())
    canonical_key = overrides.pop("canonical_key", signal.raw_data["canonical_key"])
    evidence_key = overrides.pop(
        "evidence_key",
        probe.compute_evidence_key(signal.source_api, signal.source_url or ""),
    )
    return probe.SourceCandidate(
        domain=overrides.pop("domain", "dual.example"),
        source=overrides.pop("source", "greenhouse"),
        source_api=overrides.pop("source_api", signal.source_api),
        board_id=overrides.pop("board_id", "dual"),
        signal=signal,
        evidence_key=evidence_key,
        canonical_key=canonical_key,
        **overrides,
    )


def init_probe_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            canonical_key TEXT,
            signal_type TEXT,
            source_api TEXT,
            detected_at TEXT,
            evidence_key TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE suppression_cache (
            canonical_key TEXT,
            notion_page_id TEXT,
            status TEXT,
            company_name TEXT,
            cached_at TEXT,
            expires_at TEXT,
            metadata TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_resolve_domains_order_file_arg_env(tmp_path, monkeypatch):
    domains_file = tmp_path / "domains.txt"
    domains_file.write_text("FileA.com\n# skip\nwww.FileB.com\n", encoding="utf-8")
    monkeypatch.setenv("JOB_POSTING_DOMAINS", "env.example")

    domains, source = probe.resolve_domains(
        domains_file=str(domains_file),
        domains="arg.example",
    )

    assert domains == ["filea.com", "fileb.com"]
    assert source == "domains_file"


def test_resolve_domains_uses_job_posting_domains_not_domains(monkeypatch):
    monkeypatch.setenv("DOMAINS", "wrong.example")
    monkeypatch.setenv("JOB_POSTING_DOMAINS", "right.example")

    domains, source = probe.resolve_domains(domains_file=None, domains=None)

    assert domains == ["right.example"]
    assert source == "JOB_POSTING_DOMAINS"


def test_sqlite_ro_uri_and_write_failure(tmp_path):
    db_path = tmp_path / "signals.db"
    init_probe_db(db_path)

    uri = probe.sqlite_ro_uri(db_path)
    conn = sqlite3.connect(uri, uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE should_not_write(id INTEGER)")
    conn.close()

    assert "mode=ro" in uri


def test_missing_db_fails_without_creating_file(tmp_path):
    db_path = tmp_path / "missing.db"

    with pytest.raises(probe.ProbeError, match="does not exist"):
        with probe.ReadOnlySignalStoreProbe(db_path):
            pass

    assert not db_path.exists()


def test_read_only_duplicate_evidence_key_fast_path(tmp_path):
    db_path = tmp_path / "signals.db"
    init_probe_db(db_path)
    candidate = make_candidate()
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO signals(evidence_key) VALUES (?)", (candidate.evidence_key,))
    conn.commit()
    conn.close()

    with probe.ReadOnlySignalStoreProbe(db_path) as db:
        assert db.duplicate_stage(candidate) == ("db_duplicate", "evidence_key")


def test_read_only_duplicate_exact_tuple_fallback(tmp_path):
    db_path = tmp_path / "signals.db"
    init_probe_db(db_path)
    signal = make_signal(source_url="")
    candidate = make_candidate(signal=signal, evidence_key="")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO signals(canonical_key, signal_type, source_api, detected_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            candidate.canonical_key,
            signal.signal_type,
            signal.source_api,
            signal.detected_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    with probe.ReadOnlySignalStoreProbe(db_path) as db:
        assert db.duplicate_stage(candidate) == ("db_duplicate", "exact_tuple")


def test_read_only_duplicate_legacy_canonical_fallback(tmp_path):
    db_path = tmp_path / "signals.db"
    init_probe_db(db_path)
    candidate = make_candidate(evidence_key="")
    candidate.signal.signal_type = ""
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO signals(canonical_key) VALUES (?)", (candidate.canonical_key,))
    conn.commit()
    conn.close()

    with probe.ReadOnlySignalStoreProbe(db_path) as db:
        assert db.duplicate_stage(candidate) == ("db_duplicate", "canonical_key_legacy")


def test_read_only_suppression_cache_hit_and_expired_miss(tmp_path):
    db_path = tmp_path / "signals.db"
    init_probe_db(db_path)
    candidate = make_candidate()
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO suppression_cache(canonical_key, notion_page_id, status, cached_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            candidate.canonical_key,
            "notion-live",
            "Tracking",
            now.isoformat(),
            (now + timedelta(days=1)).isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO suppression_cache(canonical_key, notion_page_id, status, cached_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "domain:expired.example",
            "notion-expired",
            "Tracking",
            now.isoformat(),
            (now - timedelta(days=1)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    with probe.ReadOnlySignalStoreProbe(db_path) as db:
        assert db.suppression_stage(candidate) == (
            "notion_suppressed",
            "Tracking:notion-live",
        )
        expired = make_candidate(
            signal=make_signal(canonical_key="domain:expired.example"),
            canonical_key="domain:expired.example",
        )
        assert db.suppression_stage(expired) == (None, None)


def test_same_run_duplicate_attribution():
    first = make_candidate()
    second = make_candidate()
    domain_results = [
        {"greenhouse": probe.SourceProbeResult("dual.example", "greenhouse", candidate=first)},
        {"greenhouse": probe.SourceProbeResult("dual.example", "greenhouse", candidate=second)},
    ]

    probe.attribute_candidates(domain_results, None)

    assert first.stage == "would_insert"
    assert second.stage == "same_run_duplicate"


@pytest.mark.asyncio
async def test_run_probe_fetches_greenhouse_and_ashby_without_check_domain():
    with patch.object(
        probe.JobPostingsCollector,
        "check_domain",
        new_callable=AsyncMock,
        side_effect=AssertionError("check_domain must not be used"),
    ):
        payload = await probe.run_probe(make_args())

    greenhouse = payload["domains"][0]["sources"]["greenhouse_jobs"]["candidate"]
    ashby = payload["domains"][0]["sources"]["ashby_jobs"]["candidate"]
    assert greenhouse["stage"] == "would_insert"
    assert greenhouse["would_short_circuit"] is False
    assert ashby["stage"] == "would_insert"
    assert ashby["would_short_circuit"] is True
    assert payload["summary"]["greenhouse_jobs"]["normalized_candidates"] == 1
    assert payload["summary"]["ashby_jobs"]["normalized_candidates"] == 1


@pytest.mark.asyncio
async def test_ashby_short_circuit_false_when_greenhouse_absent():
    payload = await probe.run_probe(make_args(domains="ashbyonly.example"))

    ashby = payload["domains"][0]["sources"]["ashby_jobs"]["candidate"]
    assert payload["domains"][0]["sources"]["greenhouse_jobs"]["candidate"] is None
    assert ashby["would_short_circuit"] is False


def test_runtime_ordering_unknown_when_greenhouse_incomplete():
    ashby = make_candidate(source="ashby", source_api="ashby_jobs", signal=make_signal(source_api="ashby_jobs"))
    results = {
        "greenhouse": probe.SourceProbeResult(
            "dual.example",
            "greenhouse",
            attempts=[probe.FetchAttempt("greenhouse", "dual", "dual.example", "fetch_error")],
        ),
        "ashby": probe.SourceProbeResult("dual.example", "ashby", candidate=ashby),
    }

    probe.apply_runtime_ordering(results)

    assert ashby.would_short_circuit == "unknown"


@pytest.mark.asyncio
async def test_fetch_attempts_are_scoped_by_domain_even_with_same_board_id():
    recorder = probe.FetchRecorder()

    async def no_jobs(url, **kwargs):
        return {"jobs": []}

    recorder.current_domain = "one.example"
    await recorder.wrap_http_get(
        no_jobs,
        "https://boards-api.greenhouse.io/v1/boards/shared/jobs",
    )
    recorder.current_domain = "two.example"
    await recorder.wrap_http_get(
        no_jobs,
        "https://boards-api.greenhouse.io/v1/boards/shared/jobs",
    )
    recorder.current_domain = None

    assert len(recorder.attempts_for("greenhouse", "one.example", ["shared"])) == 1
    assert len(recorder.attempts_for("greenhouse", "two.example", ["shared"])) == 1


@pytest.mark.asyncio
async def test_http_404_records_not_found_not_incomplete_fetch_error():
    recorder = probe.FetchRecorder()

    async def raise_404(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "not found",
            request=request,
            response=httpx.Response(404, request=request),
        )

    with pytest.raises(httpx.HTTPStatusError):
        await recorder.wrap_http_get(
            raise_404,
            "https://boards-api.greenhouse.io/v1/boards/missing/jobs",
        )

    assert recorder.attempts[0].status == "not_found"


@pytest.mark.asyncio
async def test_fetch_recorder_records_no_jobs_branch():
    recorder = probe.FetchRecorder()

    async def no_jobs(url, **kwargs):
        return {"jobs": []}

    data = await recorder.wrap_http_get(
        no_jobs,
        "https://boards-api.greenhouse.io/v1/boards/empty/jobs",
    )

    assert data == {"jobs": []}
    assert recorder.attempts[0].status == "no_jobs"
    assert recorder.attempts[0].job_count == 0


@pytest.mark.asyncio
async def test_fetch_recorder_records_parse_error_branch():
    recorder = probe.FetchRecorder()

    async def bad_payload(url, **kwargs):
        return ["not", "a", "dict"]

    data = await recorder.wrap_http_get(
        bad_payload,
        "https://api.ashbyhq.com/posting-api/job-board/badpayload",
    )

    assert data == ["not", "a", "dict"]
    assert recorder.attempts[0].status == "parse_error"


@pytest.mark.asyncio
async def test_fetch_recorder_records_timeout_branch():
    recorder = probe.FetchRecorder()

    async def timeout(url, **kwargs):
        raise httpx.TimeoutException("slow")

    with pytest.raises(httpx.TimeoutException):
        await recorder.wrap_http_get(
            timeout,
            "https://api.ashbyhq.com/posting-api/job-board/slow",
        )

    assert recorder.attempts[0].status == "fetch_error"
    assert recorder.attempts[0].error == "TimeoutException"


@pytest.mark.asyncio
async def test_fetch_recorder_records_5xx_branch():
    recorder = probe.FetchRecorder()

    async def raise_500(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "server error",
            request=request,
            response=httpx.Response(503, request=request),
        )

    with pytest.raises(httpx.HTTPStatusError):
        await recorder.wrap_http_get(
            raise_500,
            "https://boards-api.greenhouse.io/v1/boards/fail/jobs",
        )

    assert recorder.attempts[0].status == "fetch_error"
    assert recorder.attempts[0].error == "http_503"


@pytest.mark.asyncio
async def test_run_probe_does_not_use_writable_signal_store_initialize():
    with patch("storage.signal_store.SignalStore.initialize", new_callable=AsyncMock) as init:
        payload = await probe.run_probe(make_args())

    init.assert_not_called()
    assert payload["summary"]["greenhouse_jobs"]["normalized_candidates"] == 1


@pytest.mark.asyncio
async def test_db_duplicate_attribution_in_run_probe(tmp_path):
    db_path = tmp_path / "signals.db"
    init_probe_db(db_path)
    payload_without_db = await probe.run_probe(make_args(domains="dual.example", sources="ashby"))
    ashby_candidate = payload_without_db["domains"][0]["sources"]["ashby_jobs"]["candidate"]

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO signals(evidence_key) VALUES (?)", (ashby_candidate["evidence_key"],))
    conn.commit()
    conn.close()

    payload = await probe.run_probe(
        make_args(domains="dual.example", sources="ashby", db=str(db_path))
    )

    candidate = payload["domains"][0]["sources"]["ashby_jobs"]["candidate"]
    assert candidate["stage"] == "db_duplicate"
    assert candidate["attribution_detail"] == "evidence_key"


def test_main_outputs_json_and_uses_async_entrypoint(capsys):
    exit_code = asyncio.run(
        probe.main(
            [
                "--sources",
                "greenhouse,ashby",
                "--domains",
                "dual.example",
                "--fixture-dir",
                str(FIXTURE_DIR),
                "--json",
            ]
        )
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "source-isolated"
    assert payload["runtime_order_scope"] == "greenhouse_ashby_only"


def test_max_domains_guard():
    args = make_args(domains="a.example,b.example", max_domains=1)

    with pytest.raises(probe.ProbeError, match="above --max-domains"):
        asyncio.run(probe.run_probe(args))


def test_runtime_mirror_mode_rejected_until_implemented():
    args = make_args(mode="runtime-mirror")

    with pytest.raises(probe.ProbeError, match="Only --mode source-isolated"):
        asyncio.run(probe.run_probe(args))
