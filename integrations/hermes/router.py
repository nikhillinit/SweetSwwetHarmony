from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import RoutingConfig, RiskLevel


@dataclass(frozen=True)
class ExecutorMetadata:
    enabled: bool
    supports_execute: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "supportsExecute": self.supports_execute,
        }


@dataclass(frozen=True)
class LaneRecommendation:
    specialist: str | None
    risk: RiskLevel
    score: int
    matched_keywords: tuple[str, ...]
    preferred_executors: tuple[str, ...]
    fallback_executors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "specialist": self.specialist,
            "risk": self.risk,
            "score": self.score,
            "matchedKeywords": list(self.matched_keywords),
            "preferredExecutors": list(self.preferred_executors),
            "fallbackExecutors": list(self.fallback_executors),
        }


@dataclass(frozen=True)
class RoutingPlan:
    task_text: str
    phase: str
    recommended_executor: str
    risk: RiskLevel
    specialist: str | None
    score: int
    matched_keywords: tuple[str, ...]
    alternatives: tuple[str, ...]
    manual_model: str | None
    lane: LaneRecommendation
    executor_metadata: tuple[tuple[str, ExecutorMetadata], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task_text,
            "phase": self.phase,
            "recommendedExecutor": self.recommended_executor,
            "risk": self.risk,
            "specialist": self.specialist,
            "score": self.score,
            "matchedKeywords": list(self.matched_keywords),
            "alternatives": list(self.alternatives),
            "manualModel": self.manual_model,
            "lane": self.lane.to_dict(),
            "executorMetadata": {
                name: metadata.to_dict()
                for name, metadata in self.executor_metadata
            },
        }


def score_task_for_lane(
    task_text: str,
    phase: str,
    config: RoutingConfig,
    manual_model: str | None = None,
    require_execute: bool = False,
) -> RoutingPlan:
    normalized_task = task_text.strip()
    if not normalized_task:
        raise ValueError("task text must not be empty")

    if phase not in config.phases:
        raise ValueError(f"unknown phase {phase!r}")

    if manual_model is not None:
        if not config.routing.manual_override_allowed:
            raise ValueError("manual model overrides are disabled")
        if manual_model not in config.executors:
            raise ValueError(f"unknown manual model {manual_model!r}")
        if not config.executors[manual_model].enabled:
            raise ValueError(f"disabled manual model {manual_model!r}")
        if require_execute and not config.executors[manual_model].supports_execute:
            raise ValueError(
                f"manual model {manual_model!r} does not support execute mode"
            )

    phase_config = config.phases[phase]
    enabled_executors = {
        name
        for name, executor in config.executors.items()
        if executor.enabled and (not require_execute or executor.supports_execute)
    }
    lane = _select_lane(normalized_task, phase, config)
    recommended = manual_model or _first_executor(
        lane,
        phase_config.preferred_executors,
        enabled_executors,
    )
    alternatives = _alternatives(
        recommended,
        enabled_executors,
        lane.preferred_executors,
        lane.fallback_executors,
        config.routing.fallback_order,
    )

    return RoutingPlan(
        task_text=normalized_task,
        phase=phase,
        recommended_executor=recommended,
        risk=lane.risk,
        specialist=lane.specialist,
        score=lane.score,
        matched_keywords=lane.matched_keywords,
        alternatives=alternatives,
        manual_model=manual_model,
        lane=lane,
        executor_metadata=_executor_metadata(config),
    )


def _select_lane(task_text: str, phase: str, config: RoutingConfig) -> LaneRecommendation:
    phase_config = config.phases[phase]
    task_lower = task_text.lower()
    scored: list[LaneRecommendation] = []

    for name, specialist in config.specialists.items():
        matched = tuple(
            keyword
            for keyword in specialist.keywords
            if keyword.lower() in task_lower
        )
        if not matched:
            continue

        risk = _escalated_risk(task_lower, specialist.risk, config)
        scored.append(
            LaneRecommendation(
                specialist=name,
                risk=risk,
                score=len(matched),
                matched_keywords=matched,
                preferred_executors=tuple(specialist.preferred_executors),
                fallback_executors=tuple(specialist.fallback_executors),
            )
        )

    if scored:
        return min(
            scored,
            key=lambda item: (
                -item.score,
                _risk_rank(item.risk, phase_config.risk_order),
                item.specialist or "",
            ),
        )

    return LaneRecommendation(
        specialist=None,
        risk=_escalated_risk(task_lower, config.risk_defaults.no_specialist, config),
        score=0,
        matched_keywords=(),
        preferred_executors=tuple(phase_config.preferred_executors),
        fallback_executors=tuple(phase_config.fallback_executors),
    )


def _risk_rank(risk: RiskLevel, risk_order: list[RiskLevel]) -> int:
    try:
        return risk_order.index(risk)
    except ValueError:
        return len(risk_order)


def _escalated_risk(
    task_lower: str,
    default_risk: RiskLevel,
    config: RoutingConfig,
) -> RiskLevel:
    if any(keyword.lower() in task_lower for keyword in config.risk_defaults.high_risk_keywords):
        return "high"
    return default_risk


def _first_executor(
    lane: LaneRecommendation,
    phase_preferred: list[str],
    enabled_executors: set[str],
) -> str:
    for executor in (*lane.preferred_executors, *phase_preferred):
        if executor in enabled_executors:
            return executor
    raise ValueError("routing config has no available executor for selected lane")


def _alternatives(
    recommended: str,
    enabled_executors: set[str],
    *executor_lists: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    alternatives: list[str] = []
    for executors in executor_lists:
        for executor in executors:
            if (
                executor in enabled_executors
                and executor != recommended
                and executor not in alternatives
            ):
                alternatives.append(executor)
    return tuple(alternatives)


def _executor_metadata(config: RoutingConfig) -> tuple[tuple[str, ExecutorMetadata], ...]:
    return tuple(
        (
            name,
            ExecutorMetadata(
                enabled=executor.enabled,
                supports_execute=executor.supports_execute,
            ),
        )
        for name, executor in sorted(config.executors.items())
    )
