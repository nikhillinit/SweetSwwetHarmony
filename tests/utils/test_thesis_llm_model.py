from __future__ import annotations

from consumer.thesis_filter.llm_classifier import LLMClassifier, _ThesisClassifierResponse
from utils.thesis_evaluator import LLMEvaluator, ThesisEvaluator
from utils.thesis_llm_model import DEFAULT_THESIS_LLM_MODEL, THESIS_LLM_MODEL_ENV


def test_llm_classifier_defaults_to_stable_thesis_model(monkeypatch):
    monkeypatch.delenv(THESIS_LLM_MODEL_ENV, raising=False)

    classifier = LLMClassifier()

    assert classifier.model_name == DEFAULT_THESIS_LLM_MODEL
    assert classifier.model_name == "gemini-3.5-flash"


def test_llm_classifier_uses_thesis_llm_model_env(monkeypatch):
    monkeypatch.setenv(THESIS_LLM_MODEL_ENV, "gemini-test-env")

    classifier = LLMClassifier()

    assert classifier.model_name == "gemini-test-env"


def test_llm_classifier_explicit_model_wins_over_env(monkeypatch):
    monkeypatch.setenv(THESIS_LLM_MODEL_ENV, "gemini-test-env")

    classifier = LLMClassifier(model="gemini-explicit")

    assert classifier.model_name == "gemini-explicit"


def test_evaluator_defaults_follow_thesis_model_resolver(monkeypatch):
    monkeypatch.delenv(THESIS_LLM_MODEL_ENV, raising=False)

    evaluator = LLMEvaluator()
    orchestrator = ThesisEvaluator()

    assert evaluator.model == DEFAULT_THESIS_LLM_MODEL
    assert orchestrator.llm_evaluator.model == DEFAULT_THESIS_LLM_MODEL


def test_evaluator_explicit_model_wins_over_env(monkeypatch):
    monkeypatch.setenv(THESIS_LLM_MODEL_ENV, "gemini-test-env")

    evaluator = LLMEvaluator(model="gemini-explicit")
    orchestrator = ThesisEvaluator(llm_model="gemini-explicit")

    assert evaluator.model == "gemini-explicit"
    assert orchestrator.llm_evaluator.model == "gemini-explicit"


def test_thesis_response_schema_is_gemini_developer_api_compatible():
    schema = _ThesisClassifierResponse.model_json_schema()

    assert "additionalProperties" not in schema
