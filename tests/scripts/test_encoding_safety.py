"""
Tests for Windows console encoding safety (cp1252/cp437).

Verifies that seed scripts don't crash when stdout is using a restricted
encoding like cp1252 (common on Windows).
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", [
    "scripts/seed_job_posting_domains.py",
    "scripts/seed_tier_c_domains.py",
])
def test_script_survives_cp1252_encoding(script, tmp_path):
    """Script should exit 0 (or 2 for argparse --help) under cp1252 encoding."""
    result = subprocess.run(
        [sys.executable, script, "--help"],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONIOENCODING": "cp1252",
        },
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        timeout=30,
    )
    # --help exits 0; argparse prints usage
    assert result.returncode == 0, (
        f"{script} crashed under cp1252:\nstderr={result.stderr}"
    )
    assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout


def test_seed_job_posting_no_crash_cp1252(tmp_path):
    """seed_job_posting_domains.py runs without crash on empty DB under cp1252."""
    import sqlite3

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS signals ("
                 "id INTEGER PRIMARY KEY, signal_type TEXT, source_api TEXT, "
                 "canonical_key TEXT, company_name TEXT, confidence REAL, "
                 "raw_data TEXT, detected_at TEXT, created_at TEXT)")
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, "scripts/seed_job_posting_domains.py",
         "--db", db_path],
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONIOENCODING": "cp1252",
        },
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Script crashed under cp1252:\nstderr={result.stderr}"
    )
    # Should output the env var line or "No domain: keys found"
    assert ("JOB_POSTING_DOMAINS=" in result.stdout
            or "No domain: keys found" in result.stdout
            or "Found 0" in result.stdout), (
        f"Unexpected output: {result.stdout[:500]}"
    )
