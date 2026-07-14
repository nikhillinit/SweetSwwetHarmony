"""Contract tests for scripts/check_ruleset_parity.py.

Q1's acceptance criterion "live ruleset output and the runbook list the same
checks" is otherwise verified once, manually, then drifts forever -- the
drift class that left the ruleset requiring only Core Regression Suite out
of 7 green merge gates. The parity script diffs the active default-branch
ruleset (via gh api) against the check-list table in
docs/runbooks/branch-protection-setup.md and exits nonzero on drift.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.check_ruleset_parity import (
    diff_ruleset,
    extract_ruleset_checks,
    main,
    parse_runbook_checks,
)

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "branch-protection-setup.md"

EXPECTED_CHECKS = {
    "Core Regression Suite",
    "Docker Build & Smoke",
    "PR Evidence Gate",
    "Thesis Golden Set Gate",
    "SQLite Durability Smoke",
    "Hermes Ledger Audit",
    "Local Artifact Validation",
}


def _ruleset(
    checks: set[str] | None = None,
    *,
    enforcement: str = "active",
    strict: bool = True,
) -> dict:
    return {
        "id": 12778551,
        "enforcement": enforcement,
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request"},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": strict,
                    "required_status_checks": [
                        {"context": c, "integration_id": 15368}
                        for c in sorted(checks or EXPECTED_CHECKS)
                    ],
                },
            },
        ],
    }


def test_runbook_check_table_parses_to_the_seven_contexts() -> None:
    checks = parse_runbook_checks(RUNBOOK.read_text(encoding="utf-8"))
    assert set(checks) == EXPECTED_CHECKS
    assert len(checks) == 7


def test_extract_ruleset_checks_reads_required_status_check_contexts() -> None:
    assert set(extract_ruleset_checks(_ruleset())) == EXPECTED_CHECKS


def test_diff_reports_parity_when_lists_match() -> None:
    result = diff_ruleset(sorted(EXPECTED_CHECKS), _ruleset())
    assert result["parity"] is True
    assert result["missing_from_live"] == []
    assert result["extra_in_live"] == []
    assert result["enforcement"] == "active"
    assert result["strict"] is True


def test_diff_detects_missing_and_extra_checks() -> None:
    live = (EXPECTED_CHECKS - {"PR Evidence Gate"}) | {"Rogue Check"}
    result = diff_ruleset(sorted(EXPECTED_CHECKS), _ruleset(live))
    assert result["parity"] is False
    assert result["missing_from_live"] == ["PR Evidence Gate"]
    assert result["extra_in_live"] == ["Rogue Check"]


def test_diff_detects_enforcement_and_strict_drift() -> None:
    disabled = diff_ruleset(sorted(EXPECTED_CHECKS), _ruleset(enforcement="disabled"))
    assert disabled["parity"] is False

    lax = diff_ruleset(sorted(EXPECTED_CHECKS), _ruleset(strict=False))
    assert lax["parity"] is False


def test_main_exits_zero_on_parity(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "scripts.ci.check_ruleset_parity.fetch_live_ruleset",
        lambda repo, ruleset_id: _ruleset(),
    )
    exit_code = main(["--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["parity"] is True


def test_main_exits_nonzero_on_drift(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "scripts.ci.check_ruleset_parity.fetch_live_ruleset",
        lambda repo, ruleset_id: _ruleset(EXPECTED_CHECKS - {"Hermes Ledger Audit"}),
    )
    exit_code = main(["--json"])
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["parity"] is False
    assert payload["missing_from_live"] == ["Hermes Ledger Audit"]


def test_main_exits_two_when_fetch_fails(monkeypatch, capsys) -> None:
    def boom(repo, ruleset_id):
        raise RuntimeError("gh api failed: HTTP 404")

    monkeypatch.setattr("scripts.ci.check_ruleset_parity.fetch_live_ruleset", boom)
    exit_code = main([])
    assert exit_code == 2


def test_runbook_parser_fails_loudly_when_table_is_missing() -> None:
    with pytest.raises(ValueError):
        parse_runbook_checks("# a runbook with no required-checks table")
