from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "discovery-pipeline.yml"


def test_daily_pipeline_persists_db_guard_watermark() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    restore = "artifact-tmp/.omx/state/db_watermark.json"
    live_path = ".omx/state/db_watermark.json"

    assert restore in workflow
    assert f"cp {restore} {live_path}" in workflow
    assert "Bootstrap DB guard watermark" in workflow
    assert "python run_pipeline.py init-watermark" in workflow
    assert workflow.index("Bootstrap DB guard watermark") < workflow.index(
        "Sync portfolio companies"
    )

    upload_latest = workflow.split("name: signals-db-latest", maxsplit=1)[1]
    upload_latest = upload_latest.split("      - name: Upload timestamped", maxsplit=1)[0]
    assert live_path in upload_latest
    assert "include-hidden-files: true" in upload_latest

    upload_timestamped = workflow.split("name: signals-db-${{ github.run_number }}", maxsplit=1)[1]
    assert live_path in upload_timestamped
    assert "include-hidden-files: true" in upload_timestamped
