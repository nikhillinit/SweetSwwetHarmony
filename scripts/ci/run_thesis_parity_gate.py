from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParityGateConfig:
    temperature: float = 0.0
    accuracy_delta_threshold: float = 0.02
    seed: int = 42
    max_retries: int = 2


@dataclass
class ParityGateResult:
    passed: bool
    cli_accuracy: float
    api_accuracy: float
    delta: float
    reason: str


class ParityGateError(RuntimeError):
    pass


class ParityGate:
    def __init__(self, config: ParityGateConfig | None = None) -> None:
        self.config = config or ParityGateConfig()

    def evaluate(self, cli_correct: int, api_correct: int, total: int) -> ParityGateResult:
        if total == 0:
            raise ParityGateError("total must be > 0")
        cli_acc = cli_correct / total
        api_acc = api_correct / total
        delta = abs(cli_acc - api_acc)
        passed = delta <= self.config.accuracy_delta_threshold
        reason = (
            f"delta={delta:.4f} within threshold={self.config.accuracy_delta_threshold}"
            if passed
            else f"delta={delta:.4f} exceeds threshold={self.config.accuracy_delta_threshold}"
        )
        return ParityGateResult(
            passed=passed,
            cli_accuracy=cli_acc,
            api_accuracy=api_acc,
            delta=delta,
            reason=reason,
        )
