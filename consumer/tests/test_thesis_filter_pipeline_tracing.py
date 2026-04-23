"""Tests for ThesisFilterPipeline route tracing."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from consumer.thesis_filter.hard_disqualifiers import DisqualifyResult
from consumer.thesis_filter.llm_classifier import ThesisClassification
from consumer.thesis_filter.pipeline import (
    FilterResultType,
    ThesisFilterPipeline,
)
from telemetry.thesis_tracing import MemoryThesisTracer


class TestThesisFilterPipelineTracing:
    @pytest.mark.asyncio
    async def test_hard_reject_records_route_span(self):
        tracer = MemoryThesisTracer()
        pipeline = ThesisFilterPipeline(tracer=tracer)
        pipeline.hard_disqualifiers = Mock()
        pipeline.hard_disqualifiers.check.return_value = DisqualifyResult(
            passed=False,
            reason="B2B/Enterprise focus: 'enterprise'",
            category="b2b",
        )

        result = await pipeline.filter(
            {
                "title": "Enterprise API platform",
                "source_api": "test",
                "source_context": "Infrastructure for operators",
            }
        )

        assert result.result_type == FilterResultType.AUTO_REJECT
        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.name == "thesis.pipeline.route"
        assert span.attributes["stage1_passed"] is False
        assert span.attributes["llm_stage_ran"] is False
        assert span.attributes["result_type"] == "auto_reject"
        assert span.attributes["category"] == "excluded"

    @pytest.mark.asyncio
    async def test_skip_llm_records_route_span_without_llm_stage(self):
        tracer = MemoryThesisTracer()
        pipeline = ThesisFilterPipeline(skip_llm=True, tracer=tracer)
        pipeline.hard_disqualifiers = Mock()
        pipeline.hard_disqualifiers.check.return_value = DisqualifyResult(passed=True)

        result = await pipeline.filter(
            {
                "title": "Consumer meal kit startup",
                "source_api": "test",
                "source_context": "Healthy meal delivery for households",
            }
        )

        assert result.result_type == FilterResultType.LLM_REVIEW
        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.attributes["stage1_passed"] is True
        assert span.attributes["llm_stage_ran"] is False
        assert span.attributes["result_type"] == "llm_review"
        assert span.attributes["score"] == 0.5

    @pytest.mark.asyncio
    async def test_llm_route_records_result_attributes(self):
        tracer = MemoryThesisTracer()
        pipeline = ThesisFilterPipeline(tracer=tracer)
        pipeline.hard_disqualifiers = Mock()
        pipeline.hard_disqualifiers.check.return_value = DisqualifyResult(passed=True)

        classification = ThesisClassification(
            thesis_match=True,
            thesis_fit_score=0.91,
            category="consumer_cpg",
            stage_estimate="seed",
            confidence="high",
            company_name="MealBox",
            rationale="Strong consumer meal kit fit.",
            key_signals=["meal kit", "consumer"],
            prompt_version="v1.6.0",
            model="gemini-2.0-flash",
            classification_status="success",
        )

        pipeline._llm_classifier = Mock()
        pipeline._llm_classifier.classify = AsyncMock(return_value=classification)

        result = await pipeline.filter(
            {
                "title": "Consumer meal kit startup",
                "source_api": "test",
                "source_context": "Healthy meal delivery for households",
            }
        )

        assert result.result_type == FilterResultType.LLM_AUTO_APPROVE
        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.attributes["stage1_passed"] is True
        assert span.attributes["llm_stage_ran"] is True
        assert span.attributes["result_type"] == "llm_auto"
        assert span.attributes["classification_status"] == "success"
        assert span.attributes["llm_model"] == "gemini-2.0-flash"
        assert span.attributes["llm_prompt_version"] == "v1.6.0"

    def test_lazy_classifier_reuses_pipeline_tracer(self):
        tracer = MemoryThesisTracer()
        pipeline = ThesisFilterPipeline(tracer=tracer)

        assert pipeline.llm_classifier._tracer is tracer

    @pytest.mark.asyncio
    async def test_stage1_exception_records_error_and_finishes_span(self):
        tracer = MemoryThesisTracer()
        pipeline = ThesisFilterPipeline(tracer=tracer)
        pipeline.hard_disqualifiers = Mock()
        pipeline.hard_disqualifiers.check.side_effect = RuntimeError("stage1 boom")

        with pytest.raises(RuntimeError, match="stage1 boom"):
            await pipeline.filter(
                {
                    "title": "Consumer meal kit startup",
                    "source_api": "test",
                    "source_context": "Healthy meal delivery for households",
                }
            )

        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.name == "thesis.pipeline.route"
        assert span.attributes["stage1_passed"] is None
        assert span.attributes["llm_stage_ran"] is False
        assert span.errors[0]["error_kind"] == "stage1"

    @pytest.mark.asyncio
    async def test_llm_exception_records_error_and_finishes_span(self):
        tracer = MemoryThesisTracer()
        pipeline = ThesisFilterPipeline(tracer=tracer)
        pipeline.hard_disqualifiers = Mock()
        pipeline.hard_disqualifiers.check.return_value = DisqualifyResult(passed=True)
        pipeline._llm_classifier = Mock()
        pipeline._llm_classifier.classify = AsyncMock(side_effect=RuntimeError("llm boom"))

        with pytest.raises(RuntimeError, match="llm boom"):
            await pipeline.filter(
                {
                    "title": "Consumer meal kit startup",
                    "source_api": "test",
                    "source_context": "Healthy meal delivery for households",
                }
            )

        assert len(tracer.finished_spans) == 1
        span = tracer.finished_spans[0]
        assert span.name == "thesis.pipeline.route"
        assert span.attributes["stage1_passed"] is True
        assert span.attributes["llm_stage_ran"] is True
        assert span.errors[0]["error_kind"] == "llm"
