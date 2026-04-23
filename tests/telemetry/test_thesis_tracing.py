"""Tests for thesis tracing helpers."""

from __future__ import annotations

from telemetry.thesis_tracing import (
    MemoryThesisTracer,
    create_thesis_tracer,
    get_success_sample_rate,
    get_trace_backend,
    redact_error_message,
    should_include_raw_traces,
    should_sample_success,
    summarize_text_payload,
)


class TestTraceBackend:
    def test_default_backend_is_noop(self, monkeypatch):
        monkeypatch.delenv("LLM_TRACE_BACKEND", raising=False)
        assert get_trace_backend() == "noop"

    def test_invalid_backend_falls_back_to_noop(self, monkeypatch):
        monkeypatch.setenv("LLM_TRACE_BACKEND", "bogus")
        assert get_trace_backend() == "noop"

    def test_known_backend_is_accepted(self, monkeypatch):
        monkeypatch.setenv("LLM_TRACE_BACKEND", "phoenix")
        assert get_trace_backend() == "phoenix"

    def test_create_tracer_returns_noop_safe_instance(self, monkeypatch):
        monkeypatch.setenv("LLM_TRACE_BACKEND", "langsmith")
        tracer = create_thesis_tracer()
        assert tracer.backend == "noop"


class TestRawTracingFlags:
    def test_raw_tracing_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("LLM_TRACE_INCLUDE_RAW", raising=False)
        assert should_include_raw_traces() is False

    def test_raw_tracing_enabled(self, monkeypatch):
        monkeypatch.setenv("LLM_TRACE_INCLUDE_RAW", "true")
        assert should_include_raw_traces() is True


class TestSampling:
    def test_default_sample_rate(self, monkeypatch):
        monkeypatch.delenv("LLM_TRACE_SUCCESS_SAMPLE_RATE", raising=False)
        assert get_success_sample_rate() == 0.10

    def test_invalid_sample_rate_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_TRACE_SUCCESS_SAMPLE_RATE", "not-a-number")
        assert get_success_sample_rate() == 0.10

    def test_sample_rate_is_clamped(self, monkeypatch):
        monkeypatch.setenv("LLM_TRACE_SUCCESS_SAMPLE_RATE", "2.5")
        assert get_success_sample_rate() == 1.0

    def test_success_sampling_uses_bucket(self):
        assert should_sample_success(0.5, random_value=0.1) is True
        assert should_sample_success(0.5, random_value=0.9) is False


class TestPayloadSummaries:
    def test_summarize_text_payload_redacts_by_default(self):
        payload = summarize_text_payload("secret prompt body")
        assert payload["present"] is True
        assert payload["chars"] == len("secret prompt body")
        assert "preview" not in payload
        assert "sha256" in payload

    def test_summarize_text_payload_can_include_preview(self):
        payload = summarize_text_payload("secret prompt body", include_raw=True, preview_chars=6)
        assert payload["preview"] == "secret"

    def test_redact_error_message_clamps_length(self):
        message = "x" * 500
        redacted = redact_error_message(message, max_chars=50)
        assert len(redacted) == 50


class TestMemoryTracer:
    def test_memory_tracer_records_finished_span_and_errors(self):
        tracer = MemoryThesisTracer()
        span = tracer.start_span("thesis.llm.classify", component="test")
        tracer.annotate(span, cache_hit=False)
        tracer.record_error(span, error_kind="parse", message="parse error: body")
        tracer.finish(span, label="needs_review")

        assert len(tracer.finished_spans) == 1
        recorded = tracer.finished_spans[0]
        assert recorded.name == "thesis.llm.classify"
        assert recorded.attributes["component"] == "test"
        assert recorded.attributes["cache_hit"] is False
        assert recorded.attributes["label"] == "needs_review"
        assert recorded.finished is True
        assert recorded.errors[0]["error_kind"] == "parse"
