from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "red-team-hybrid" / "keepalive_monitor_ping.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("keepalive_monitor_ping", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _watchdog_payload(
    status: str = "PASS",
    exit_code: int = 0,
    *,
    min_created_at: str = "2026-05-13T15:00:00+00:00",
) -> dict:
    return {
        "checked_at": "2026-05-13T15:10:00+00:00",
        "threshold_hours": 12,
        "min_created_at": min_created_at,
        "exit_code": exit_code,
        "status": status,
        "collectors": [
            {
                "source_api": "greenhouse_jobs",
                "category": "operational",
                "last_created": "2026-05-13T15:00:22+00:00",
                "age_hours": 0.16,
                "status": "FRESH",
                "required_after": min_created_at,
            },
            {
                "source_api": "ashby_jobs",
                "category": "operational",
                "last_created": "2026-05-13T15:00:23+00:00",
                "age_hours": 0.15,
                "status": "FRESH",
                "required_after": min_created_at,
            },
        ],
        "failures": [] if status == "PASS" else ["greenhouse_jobs: stale"],
    }


def test_build_monitor_payload_carries_post_run_db_proof_fields() -> None:
    module = _load_module()

    payload = module.build_monitor_payload(
        _watchdog_payload(),
        task_name="HarmonicKeepAlive",
        artifact_path=Path("artifacts/keepalive/2026-05-13.json"),
    )

    assert payload["task_name"] == "HarmonicKeepAlive"
    assert payload["source_of_record"] == "signals.created_at"
    assert payload["artifact"] == "2026-05-13.json"
    assert payload["watchdog"]["threshold_hours"] == 12
    assert payload["watchdog"]["min_created_at"] == "2026-05-13T15:00:00+00:00"
    assert payload["watchdog"]["status"] == "PASS"
    assert payload["watchdog"]["sources"]["greenhouse_jobs"]["last_created"] == "2026-05-13T15:00:22+00:00"
    assert payload["watchdog"]["sources"]["greenhouse_jobs"]["required_after"] == "2026-05-13T15:00:00+00:00"
    assert payload["watchdog"]["sources"]["ashby_jobs"]["status"] == "FRESH"
    assert "watchdog.sources.<source_api>.last_created" in payload["post_run_db_proof_fields"]
    assert "watchdog.sources.<source_api>.required_after" in payload["post_run_db_proof_fields"]
    assert "watchdog.min_created_at" in payload["post_run_db_proof_fields"]


def test_build_monitor_payload_carries_no_post_run_rows_failure_reason() -> None:
    module = _load_module()
    watchdog = _watchdog_payload(status="FAIL", exit_code=1)
    watchdog["collectors"][0] = {
        **watchdog["collectors"][0],
        "last_created": "2026-05-13T08:53:22+00:00",
        "age_hours": 6.28,
        "status": "STALE",
        "stale_reason": "no_post_run_rows",
    }
    watchdog["failures"] = ["greenhouse_jobs: no_post_run_rows"]

    payload = module.build_monitor_payload(
        watchdog,
        task_name="HarmonicKeepAlive",
        artifact_path=Path("artifacts/keepalive/2026-05-13-HarmonicKeepAlive.json"),
    )

    source = payload["watchdog"]["sources"]["greenhouse_jobs"]
    assert payload["watchdog"]["status"] == "FAIL"
    assert payload["watchdog"]["min_created_at"] == "2026-05-13T15:00:00+00:00"
    assert source["status"] == "STALE"
    assert source["required_after"] == "2026-05-13T15:00:00+00:00"
    assert source["stale_reason"] == "no_post_run_rows"
    assert "watchdog.sources.<source_api>.stale_reason" in payload["post_run_db_proof_fields"]


def test_ping_url_uses_exit_status_suffix() -> None:
    module = _load_module()

    assert module.ping_url_for_exit_status("https://hc-ping.com/example", 0) == "https://hc-ping.com/example/0"
    assert (
        module.ping_url_for_exit_status("https://hc-ping.com/example?rid=abc", 1)
        == "https://hc-ping.com/example/1?rid=abc"
    )


def test_post_payload_sends_exit_status_suffix_and_json_body() -> None:
    module = _load_module()
    received: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["content_type"] = self.headers.get("Content-Type", "")
            received["body"] = self.rfile.read(length).decode("utf-8")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()

    try:
        status = module._post_payload(
            f"http://127.0.0.1:{server.server_port}/keepalive",
            1,
            {"source_of_record": "signals.created_at"},
            5.0,
        )
    finally:
        thread.join(timeout=10)
        server.server_close()

    assert status == 200
    assert received["path"] == "/keepalive/1"
    assert received["content_type"] == "application/json"
    assert json.loads(received["body"]) == {"source_of_record": "signals.created_at"}


def test_cli_dry_run_emits_payload_without_ping_url(tmp_path: Path) -> None:
    watchdog_path = tmp_path / "watchdog.json"
    watchdog_path.write_text(json.dumps(_watchdog_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--watchdog-json",
            str(watchdog_path),
            "--task-name",
            "HarmonicKeepAlive",
            "--dry-run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    emitted = json.loads(result.stdout)
    assert emitted["ping_exit_status"] == 0
    assert emitted["payload"]["watchdog"]["sources"]["greenhouse_jobs"]["status"] == "FRESH"
