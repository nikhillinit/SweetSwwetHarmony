from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "recovery_sprint_canary_restore"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
EXPECTED_BACKUP_SHA256 = (
    "01ced671a3c1a3800646edad42c2fa9ef2841f587d8255b4049a7c6e3fdd0a26"
)


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for child in value.values():
            strings.extend(_walk_strings(child))
        return strings
    if isinstance(value, list):
        strings = []
        for child in value:
            strings.extend(_walk_strings(child))
        return strings
    return []


def test_recovery_canary_fixture_is_sanitized_and_scoped() -> None:
    manifest = _load_manifest()
    fixture_files = [path.relative_to(FIXTURE_DIR).as_posix() for path in FIXTURE_DIR.rglob("*") if path.is_file()]

    assert manifest["scope_guard"] == {
        "production_target": "signals.db",
        "canary_target": "signals.db.canary",
        "production_db_mutated": False,
        "live_restore_attempted": False,
        "keepalive_reactivated": False,
        "issue_149_closed": False,
        "raw_db_or_ai_logs_committed": False,
    }
    assert manifest["sanitization"] == {
        "absolute_paths_redacted": True,
        "secrets_redacted": True,
        "raw_database_files_omitted": True,
        "raw_ai_logs_omitted": True,
    }

    leaked_strings = [
        text
        for text in _walk_strings(manifest)
        if "C:\\" in text or "\\Users\\" in text or "nikhi" in text.lower()
    ]
    assert leaked_strings == []
    assert fixture_files == ["manifest.json"]


def test_recovery_canary_fixture_records_logical_restore_evidence() -> None:
    manifest = _load_manifest()

    assert manifest["source_backup"] == {
        "path_ref": "backups/signals-20260529-190655.db",
        "sha256": EXPECTED_BACKUP_SHA256,
        "size_bytes": 9756672,
        "integrity_check": "ok",
        "signals_row_count": 612,
        "schema_version": 53,
    }
    assert manifest["commands"]["dry_run"]["status"] == "dry_run_passed"
    assert manifest["commands"]["dry_run"]["mutation_committed"] is False
    assert manifest["commands"]["preflight"]["status"] == "preflight_passed"
    assert manifest["commands"]["execute"]["ack_risk"] == "RESTORE_DB"
    assert manifest["commands"]["execute"]["db_ops_ledger_status"] == "success"

    logical = manifest["logical_restore_evidence"]
    assert logical["target"] == "signals.db.canary"
    assert logical["integrity_check"] == "ok"
    assert logical["signals_row_count"] == 612
    assert logical["schema_version"] == 53
    assert logical["target_sha256_after"] == EXPECTED_BACKUP_SHA256
    assert logical["final_filesystem_sidecars_present"] == []


def test_recovery_canary_fixture_keeps_e3_blocker_explicit() -> None:
    manifest = _load_manifest()
    f6 = {assertion["name"]: assertion for assertion in manifest["f6_assertions"]}

    for name in (
        "backup_sha256_matches_phase0_source",
        "row_count_is_612",
        "schema_version_is_53",
        "integrity_check_ok",
        "db_ops_ledger_row_present",
        "hermes_run_dir_present",
    ):
        assert f6[name]["passed"] is True

    assert f6["no_unexpected_sidecars"]["passed"] is False
    assert f6["no_unexpected_sidecars"]["observed_by_execute_postflight"] == [
        "signals.db.canary-wal",
        "signals.db.canary-shm",
    ]
    assert f6["no_unexpected_sidecars"]["final_filesystem_sidecars_present"] == []
    assert f6["no_repair_prompt"] == {
        "name": "no_repair_prompt",
        "passed": False,
        "repair_prompt_ref": "runs/hermes_20260530_060707_3daf76be/repair_prompt.md",
    }
    assert manifest["commands"]["execute"]["status"] == "postflight_failed"
    assert manifest["commands"]["execute"]["repair_prompt_written"] is True
    assert manifest["blocker"]["kind"] == "restore_task_transient_wal_sidecar_postflight"
    assert manifest["blocker"]["not_fixed_in_this_pr"] is True
