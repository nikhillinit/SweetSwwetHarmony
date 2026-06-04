# tests/ci/test_check_thesis_gate_artifact.py
import json
from pathlib import Path

import pytest

from scripts.ci.check_thesis_gate_artifact import GateError, check_gate

MANIFEST_FP = "536e081d4ceec265a27cf037f7bb33ae88831895554bf8ebdbc29bf578d392fc"


def _manifest(tmp_path: Path, fingerprint: str = MANIFEST_FP) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"dataset_fingerprint": fingerprint, "sample_count": 64}),
                 encoding="utf-8")
    return p


def test_structural_sensitive_without_label_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "structural"}), encoding="utf-8")
    with pytest.raises(GateError, match="structural"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=None, sensitive=True, labels=[],
                   min_accuracy=0.9)


def test_structural_sensitive_with_dispatch_label_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "structural"}), encoding="utf-8")
    # thesis-label-drift-approved authorizes proceeding without live eval
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=None, sensitive=True,
               labels=["thesis-label-drift-approved"], min_accuracy=0.9)


def test_non_sensitive_structural_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "structural"}), encoding="utf-8")
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=None, sensitive=False, labels=[], min_accuracy=0.9)


def test_fingerprint_mismatch_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"dataset_fingerprint": "deadbeef", "accuracy": 1.0}),
                    encoding="utf-8")
    with pytest.raises(GateError, match="fingerprint"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_accuracy_below_floor_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"dataset_fingerprint": MANIFEST_FP, "accuracy": 0.5}),
                    encoding="utf-8")
    with pytest.raises(GateError, match="accuracy"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_live_eval_above_floor_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"dataset_fingerprint": MANIFEST_FP, "accuracy": 0.95}),
                    encoding="utf-8")
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_non_sensitive_gold_without_gate_output_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    # Non-thesis PR: the workflow produces no gate output; it must still pass.
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=None, sensitive=False, labels=[], min_accuracy=0.9)


def test_non_sensitive_hermes_without_gate_output_passes(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "hermes"}), encoding="utf-8")
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=None, sensitive=False, labels=[], min_accuracy=0.9)


def test_real_producer_keys_above_floor_passes(tmp_path):
    # The real eval artifact uses benchmark_fingerprint + llm_accuracy + decision.
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"decision": "go", "benchmark_fingerprint": MANIFEST_FP,
                                "llm_accuracy": 0.95}), encoding="utf-8")
    check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
               gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_real_producer_no_go_decision_blocks(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"decision": "no_go", "benchmark_fingerprint": MANIFEST_FP,
                                "llm_accuracy": None,
                                "blocked_reasons": ["LLM accuracy 50.0% is below the 90% gate."]}),
                    encoding="utf-8")
    with pytest.raises(GateError, match="no_go"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)


def test_real_producer_benchmark_fingerprint_mismatch_fails(tmp_path):
    decision = tmp_path / "d.json"
    decision.write_text(json.dumps({"mode": "gold"}), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"decision": "go", "benchmark_fingerprint": "deadbeef",
                                "llm_accuracy": 1.0}), encoding="utf-8")
    with pytest.raises(GateError, match="fingerprint"):
        check_gate(decision_path=decision, manifest_path=_manifest(tmp_path),
                   gate_output_path=gate, sensitive=True, labels=[], min_accuracy=0.9)
