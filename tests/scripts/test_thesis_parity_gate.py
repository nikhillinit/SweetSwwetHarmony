import pytest

from scripts.ci.run_thesis_parity_gate import ParityGate, ParityGateConfig, ParityGateError


def test_gate_uses_temperature_zero_by_default():
    config = ParityGateConfig()
    assert config.temperature == 0.0


def test_gate_rejects_delta_exceeding_threshold():
    config = ParityGateConfig(accuracy_delta_threshold=0.02)
    gate = ParityGate(config)
    result = gate.evaluate(cli_correct=60, api_correct=58, total=64)
    assert not result.passed
    assert "delta" in result.reason.lower()


def test_gate_passes_within_threshold():
    config = ParityGateConfig(accuracy_delta_threshold=0.02)
    gate = ParityGate(config)
    result = gate.evaluate(cli_correct=62, api_correct=63, total=64)
    assert result.passed


def test_gate_passes_when_exactly_equal():
    config = ParityGateConfig(accuracy_delta_threshold=0.02)
    gate = ParityGate(config)
    result = gate.evaluate(cli_correct=61, api_correct=61, total=64)
    assert result.passed


def test_config_documents_seed_and_retries():
    config = ParityGateConfig()
    assert config.seed is not None
    assert config.max_retries >= 1
