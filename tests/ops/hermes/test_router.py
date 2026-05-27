from __future__ import annotations

import json

import pytest

from integrations.hermes.config import RoutingConfig
from integrations.hermes.router import RoutingPlan, score_task_for_lane

from .conftest import minimal_config_dict


def test_routes_to_specialist_preferred_executor_by_keyword_score() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    plan = score_task_for_lane(
        "Fix thesis filter false positive regression",
        phase="production",
        config=config,
    )

    assert plan.specialist == "thesis"
    assert plan.risk == "medium"
    assert plan.recommended_executor == "kimi"
    assert plan.score == 4
    assert plan.matched_keywords == ("thesis", "filter", "false positive", "regression")
    assert plan.alternatives == ("codex",)


def test_routes_schema_migration_as_high_risk_codex_work() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    plan = score_task_for_lane("schema migration", phase="production", config=config)

    assert plan.specialist == "schema"
    assert plan.risk == "high"
    assert plan.recommended_executor == "codex"


def test_uses_no_specialist_default_when_keywords_do_not_match() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    plan = score_task_for_lane("summarize recent notes", phase="planning", config=config)

    assert plan.specialist is None
    assert plan.risk == "medium"
    assert plan.recommended_executor == "codex"
    assert plan.score == 0
    assert plan.matched_keywords == ()


def test_high_risk_keywords_escalate_no_specialist_risk() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    plan = score_task_for_lane("rotate production token", phase="planning", config=config)

    assert plan.specialist is None
    assert plan.risk == "high"


def test_tie_breaks_by_phase_risk_order_then_specialist_name() -> None:
    data = minimal_config_dict()
    data["specialists"] = {
        "zeta": {
            "keywords": ["shared"],
            "risk": "medium",
            "preferredExecutors": ["kimi"],
            "fallbackExecutors": ["codex"],
        },
        "alpha": {
            "keywords": ["shared"],
            "risk": "high",
            "preferredExecutors": ["codex"],
            "fallbackExecutors": ["kimi"],
        },
        "aardvark": {
            "keywords": ["shared"],
            "risk": "high",
            "preferredExecutors": ["codex"],
            "fallbackExecutors": ["kimi"],
        },
    }
    config = RoutingConfig.model_validate(data)

    plan = score_task_for_lane("shared task", phase="production", config=config)

    assert plan.specialist == "aardvark"
    assert plan.risk == "high"


def test_manual_override_selects_known_executor() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    plan = score_task_for_lane(
        "schema migration",
        phase="production",
        config=config,
        manual_model="kimi",
    )

    assert plan.recommended_executor == "kimi"
    assert plan.manual_model == "kimi"
    assert plan.specialist == "schema"


@pytest.mark.parametrize("manual_model", ["gemini", "missing"])
def test_manual_override_rejects_deferred_or_unknown_executor(manual_model: str) -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    with pytest.raises(ValueError, match="unknown manual model"):
        score_task_for_lane(
            "schema migration",
            phase="production",
            config=config,
            manual_model=manual_model,
        )


def test_rejects_empty_task_text() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    with pytest.raises(ValueError, match="task text must not be empty"):
        score_task_for_lane("  ", phase="production", config=config)


def test_rejects_unknown_phase() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    with pytest.raises(ValueError, match="unknown phase"):
        score_task_for_lane("schema migration", phase="launch", config=config)


def test_routing_plan_to_dict_is_json_serializable() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())
    plan = score_task_for_lane("schema migration", phase="production", config=config)

    payload = plan.to_dict()

    assert payload["recommendedExecutor"] == "codex"
    assert payload["matchedKeywords"] == ["schema", "migration"]
    assert json.loads(json.dumps(payload)) == payload
    assert isinstance(plan, RoutingPlan)
