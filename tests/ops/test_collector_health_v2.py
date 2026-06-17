import os
import sqlite3
from pathlib import Path

import pytest

from ops.collector_health import REPORT_SCHEMA_VERSION, CollectorHealthReport
from storage.collector_suspension import SuspensionStore  # direct module import, avoids storage/__init__


def test_report_schema_version_is_2():
    assert REPORT_SCHEMA_VERSION == 2


def test_api_shape_changed_is_valid_status():
    report = CollectorHealthReport(
        collector="github",
        status="api_shape_changed",
        detail="field 'stars_count' missing from response",
    )
    assert report.status == "api_shape_changed"


def test_fresh_empty_expected_is_valid_status():
    report = CollectorHealthReport(
        collector="arxiv",
        status="fresh_empty_expected",
        detail="no new papers today",
    )
    assert report.status == "fresh_empty_expected"


def test_suspension_persists_to_file(tmp_path):
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="api_shape_changed: field missing")
    assert store.is_suspended("github")
    store2 = SuspensionStore(tmp_path / "suspensions.json")
    assert store2.is_suspended("github")


def test_suspension_not_written_in_scratch_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONIC_SCRATCH_DB", "1")
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="api_shape_changed")
    assert not (tmp_path / "suspensions.json").exists()
    assert not store.is_suspended("github")


def test_suspension_reset_writes_audit_entry(tmp_path):
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="api_shape_changed: test")
    store.reset("github", reset_by="operator")
    assert not store.is_suspended("github")
    audit = store.audit_log()
    assert any(e["action"] == "reset" and e["collector"] == "github" for e in audit)
