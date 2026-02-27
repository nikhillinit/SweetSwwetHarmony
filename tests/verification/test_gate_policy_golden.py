from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from verification.verification_gate_v2 import Signal, VerificationGate, PushDecision


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "gate_policy_v2_golden.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _signals_from_case(case: dict) -> list[Signal]:
    now = datetime.now(timezone.utc)
    signals: list[Signal] = []
    for item in case["signals"]:
        signals.append(
            Signal(
                id=item["id"],
                signal_type=item["signal_type"],
                source_api=item["source_api"],
                confidence=float(item["confidence"]),
                detected_at=now - timedelta(days=int(item["age_days"])),
            )
        )
    return signals


def test_policy_fixture_version_matches_runtime_gate() -> None:
    fixture = _load_fixture()
    gate = VerificationGate()

    assert fixture["policy_version"] == gate.POLICY_VERSION
    assert pytest.approx(float(fixture["score_recalibration_factor"]), rel=1e-9) == gate.score_recalibration_factor


@pytest.mark.parametrize("case", _load_fixture()["cases"], ids=[c["id"] for c in _load_fixture()["cases"]])
def test_gate_policy_golden_cases(case: dict) -> None:
    gate = VerificationGate()
    signals = _signals_from_case(case)
    result = gate.evaluate(signals)
    expected = case["expected"]

    assert result.decision == PushDecision(expected["decision"])
    assert result.suggested_status == expected["suggested_status"]
    assert expected["score_min"] <= result.confidence_score <= expected["score_max"]


def test_gate_policy_reachability_contract() -> None:
    fixture = _load_fixture()
    gate = VerificationGate()

    decisions = set()
    for case in fixture["cases"]:
        result = gate.evaluate(_signals_from_case(case))
        decisions.add(result.decision)

    assert PushDecision.AUTO_PUSH in decisions
    assert PushDecision.NEEDS_REVIEW in decisions
