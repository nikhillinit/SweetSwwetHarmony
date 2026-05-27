from __future__ import annotations

import json

import pytest

from integrations.hermes.config import RoutingConfig
from integrations.hermes.router import RoutingPlan, score_task_for_lane

from .conftest import minimal_config_dict


def _config_with_non_executable_docs() -> RoutingConfig:
    data = minimal_config_dict()
    data["executors"]["claude"] = {
        "provider": "claude",
        "displayName": "Claude CLI",
        "enabled": True,
        "required": False,
        "binary": "claude",
        "env": [],
        "supportsExecute": False,
    }
    data["specialists"]["docs"] = {
        "keywords": ["docs", "readme"],
        "risk": "low",
        "preferredExecutors": ["claude"],
        "fallbackExecutors": ["codex"],
    }
    data["phases"]["production"]["fallbackExecutors"].append("claude")
    data["routing"]["fallbackOrder"].append("claude")
    return RoutingConfig.model_validate(data)


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


def test_manual_override_rejects_when_disabled_by_config() -> None:
    data = minimal_config_dict()
    data["routing"]["manualOverrideAllowed"] = False
    config = RoutingConfig.model_validate(data)

    with pytest.raises(ValueError, match="manual model overrides are disabled"):
        score_task_for_lane(
            "schema migration",
            phase="production",
            config=config,
            manual_model="kimi",
        )


def test_disabled_preferred_executor_is_skipped() -> None:
    data = minimal_config_dict()
    data["executors"]["kimi"]["enabled"] = False
    config = RoutingConfig.model_validate(data)

    plan = score_task_for_lane("fix thesis filter", phase="production", config=config)

    assert plan.recommended_executor == "codex"
    assert "kimi" not in plan.alternatives


def test_manual_override_rejects_disabled_executor() -> None:
    data = minimal_config_dict()
    data["executors"]["kimi"]["enabled"] = False
    config = RoutingConfig.model_validate(data)

    with pytest.raises(ValueError, match="disabled manual model"):
        score_task_for_lane(
            "fix thesis filter",
            phase="production",
            config=config,
            manual_model="kimi",
        )


def test_advisory_route_can_surface_non_executable_executor_with_metadata() -> None:
    config = _config_with_non_executable_docs()

    plan = score_task_for_lane("update readme", phase="production", config=config)

    assert plan.recommended_executor == "claude"
    payload = plan.to_dict()
    assert payload["executorMetadata"]["claude"] == {
        "enabled": True,
        "supportsExecute": False,
    }
    assert payload["executorMetadata"]["codex"]["supportsExecute"] is True


def test_execute_route_skips_non_executable_preferred_executor() -> None:
    config = _config_with_non_executable_docs()

    plan = score_task_for_lane(
        "update readme",
        phase="production",
        config=config,
        require_execute=True,
    )

    assert plan.recommended_executor == "codex"
    assert "claude" not in plan.alternatives


def test_execute_route_rejects_non_executable_manual_override() -> None:
    config = _config_with_non_executable_docs()

    with pytest.raises(ValueError, match="does not support execute mode"):
        score_task_for_lane(
            "update readme",
            phase="production",
            config=config,
            manual_model="claude",
            require_execute=True,
        )


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
