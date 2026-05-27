from __future__ import annotations

import asyncio

import pytest

from integrations.gemini_antigravity_client import (
    GeminiAntigravityClient,
    GeminiResponse,
)
from integrations.hermes.adapters import (
    GeminiHermesExecutor,
    build_executor,
    build_reviewer_executor,
)
from integrations.hermes.config import RoutingConfig
from integrations.hermes.providers import doctor

from .conftest import minimal_config_dict


class FakeGeminiClient:
    async def exec(self, prompt, context_files=None):
        return GeminiResponse(
            content='{"verdict":"approve","confidence":1}',
            execution_time_ms=5,
        )


def _config_with_active_gemini_reviewer() -> RoutingConfig:
    data = minimal_config_dict()
    data["deferredExecutors"].pop("gemini")
    data["executors"]["gemini"] = {
        "provider": "gemini",
        "displayName": "Gemini CLI",
        "enabled": True,
        "required": False,
        "binary": "gemini",
        "env": ["GEMINI_API_KEY"],
        "supportsExecute": False,
    }
    return RoutingConfig.model_validate(data)


async def test_gemini_executor_is_reviewer_compatible() -> None:
    executor = GeminiHermesExecutor(FakeGeminiClient())

    result = await executor.execute("review this", context_files=["plan.json"])

    assert result.executor == "gemini"
    assert result.success is True
    assert result.exit_code == 0
    assert "approve" in result.content


def test_reviewer_executor_allows_active_non_executable_gemini() -> None:
    config = _config_with_active_gemini_reviewer()

    executor = build_reviewer_executor(
        "gemini",
        config,
        gemini_client=FakeGeminiClient(),
    )

    assert isinstance(executor, GeminiHermesExecutor)


def test_reviewer_executor_still_rejects_deferred_gemini() -> None:
    config = RoutingConfig.model_validate(minimal_config_dict())

    with pytest.raises(ValueError, match="deferred executor"):
        build_reviewer_executor("gemini", config, gemini_client=FakeGeminiClient())


def test_execute_executor_rejects_non_executable_gemini() -> None:
    config = _config_with_active_gemini_reviewer()

    with pytest.raises(ValueError, match="does not support execute"):
        build_executor("gemini", config, gemini_client=FakeGeminiClient())


def test_provider_doctor_knows_optional_gemini_wrapper() -> None:
    config = _config_with_active_gemini_reviewer()

    report = doctor(config)

    assert report.providers["gemini"].checks_by_name["wrapper_import"].ok is True
    assert report.providers["gemini"].success is True


def test_gemini_client_missing_binary_returns_structured_failure() -> None:
    client = GeminiAntigravityClient(binary="definitely-missing-gemini-binary")

    response = asyncio.run(client.exec("hello"))

    assert response.success is False
    assert response.exit_code == 127
    assert "not found" in (response.error or "")
