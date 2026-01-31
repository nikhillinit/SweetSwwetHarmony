"""Golden-file tests for Content Pipeline.

These tests verify the content pipeline produces expected output for realistic HTML samples.
Golden files store expected PipelineResult.to_dict() output for regression detection.

Timing fields are ignored during comparison:
- total_time_ms
- fetch_time_ms
- extraction_time_ms
- fetched_at
- created_at

To regenerate golden files after intentional changes:
    REGENERATE_GOLDEN=1 pytest tests/monitoring/test_content_pipeline_golden.py -v

Test fixtures:
- pricing_page.html     -> Tests pricing_table_v1 preset
- blog_post.html        -> Tests blog_post_v1 preset
- careers_page.html     -> Tests default preset (careers page)
- minimal_page.html     -> Tests fallback behavior with minimal content
- landing_page.html     -> Tests default preset (homepage)
"""

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.content_pipeline.models import FetchArtifact, PipelineResult


# Path to fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "content_pipeline"

# Fields to ignore during comparison (timing and timestamps)
IGNORABLE_FIELDS = {
    "total_time_ms",
    "fetch_time_ms",
    "extraction_time_ms",
    "fetched_at",
    "created_at",
}


def strip_timing_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove timing-related fields from a nested dictionary.

    Recursively walks the structure to handle nested dicts and lists.

    Args:
        data: Dictionary to process

    Returns:
        New dictionary with timing fields removed
    """
    if isinstance(data, dict):
        return {
            k: strip_timing_fields(v)
            for k, v in data.items()
            if k not in IGNORABLE_FIELDS
        }
    elif isinstance(data, list):
        return [strip_timing_fields(item) for item in data]
    else:
        return data


def load_fixture(name: str) -> str:
    """
    Load an HTML fixture file.

    Args:
        name: Fixture file name (e.g., "pricing_page.html")

    Returns:
        HTML content as string
    """
    fixture_path = FIXTURES_DIR / name
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")
    return fixture_path.read_text(encoding="utf-8")


def load_expected(name: str) -> Optional[Dict[str, Any]]:
    """
    Load expected output JSON for a fixture.

    Args:
        name: Base fixture name (e.g., "pricing_page")

    Returns:
        Expected output dict or None if file doesn't exist
    """
    expected_path = FIXTURES_DIR / f"{name}.expected.json"
    if not expected_path.exists():
        return None
    return json.loads(expected_path.read_text(encoding="utf-8"))


def save_expected(name: str, data: Dict[str, Any]) -> None:
    """
    Save expected output JSON for a fixture.

    Args:
        name: Base fixture name (e.g., "pricing_page")
        data: Expected output dict to save
    """
    expected_path = FIXTURES_DIR / f"{name}.expected.json"
    expected_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8"
    )


def should_regenerate() -> bool:
    """Check if we should regenerate golden files."""
    return os.environ.get("REGENERATE_GOLDEN", "").lower() in ("1", "true", "yes")


def format_diff(expected: Dict[str, Any], actual: Dict[str, Any]) -> str:
    """
    Format a helpful diff message for test failures.

    Args:
        expected: Expected dictionary
        actual: Actual dictionary

    Returns:
        Formatted diff string
    """
    lines = ["", "=" * 60, "GOLDEN FILE MISMATCH", "=" * 60]

    expected_json = json.dumps(expected, indent=2, sort_keys=True)
    actual_json = json.dumps(actual, indent=2, sort_keys=True)

    lines.append("")
    lines.append("EXPECTED:")
    lines.append("-" * 40)
    lines.append(expected_json)
    lines.append("")
    lines.append("ACTUAL:")
    lines.append("-" * 40)
    lines.append(actual_json)
    lines.append("")
    lines.append("To regenerate golden files:")
    lines.append("  REGENERATE_GOLDEN=1 pytest tests/monitoring/test_content_pipeline_golden.py -v")
    lines.append("=" * 60)

    return "\n".join(lines)


def create_mock_fetch_artifact(url: str, content: str, status_code: int = 200) -> FetchArtifact:
    """
    Create a FetchArtifact for testing.

    Args:
        url: Request URL
        content: HTML content
        status_code: HTTP status code

    Returns:
        FetchArtifact instance
    """
    return FetchArtifact(
        url=url,
        status_code=status_code,
        headers={"content-type": "text/html"},
        content=content,
        fetch_time_ms=50,  # Fixed timing for reproducibility
        fetched_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestGoldenFilePricingPage:
    """Golden-file tests for pricing page extraction."""

    @pytest.mark.asyncio
    async def test_pricing_page_extraction(self):
        """
        Test pricing page extracts correctly with pricing_table_v1 preset.

        URL: https://acme-analytics.com/pricing
        Expected preset: pricing_table_v1 (auto-selected from URL)
        Expected selectors: #pricing, .pricing, etc.
        """
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        fixture_name = "pricing_page"
        html_content = load_fixture(f"{fixture_name}.html")
        url = "https://acme-analytics.com/pricing"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=1,
                url=url,
            )

            actual = strip_timing_fields(result.to_dict())

            if should_regenerate():
                save_expected(fixture_name, actual)
                pytest.skip(f"Regenerated golden file for {fixture_name}")

            expected = load_expected(fixture_name)
            if expected is None:
                save_expected(fixture_name, actual)
                pytest.skip(f"Created initial golden file for {fixture_name}")

            expected = strip_timing_fields(expected)

            assert actual == expected, format_diff(expected, actual)


class TestGoldenFileBlogPost:
    """Golden-file tests for blog post extraction."""

    @pytest.mark.asyncio
    async def test_blog_post_extraction(self):
        """
        Test blog post extracts correctly with blog_post_v1 preset.

        URL: https://acme-analytics.com/blog/introducing-3-0
        Expected preset: blog_post_v1 (auto-selected from /blog path)
        Expected selectors: article, .post-content, etc.
        """
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        fixture_name = "blog_post"
        html_content = load_fixture(f"{fixture_name}.html")
        url = "https://acme-analytics.com/blog/introducing-3-0"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=2,
                url=url,
            )

            actual = strip_timing_fields(result.to_dict())

            if should_regenerate():
                save_expected(fixture_name, actual)
                pytest.skip(f"Regenerated golden file for {fixture_name}")

            expected = load_expected(fixture_name)
            if expected is None:
                save_expected(fixture_name, actual)
                pytest.skip(f"Created initial golden file for {fixture_name}")

            expected = strip_timing_fields(expected)

            assert actual == expected, format_diff(expected, actual)


class TestGoldenFileCareersPage:
    """Golden-file tests for careers page extraction."""

    @pytest.mark.asyncio
    async def test_careers_page_extraction(self):
        """
        Test careers page extracts correctly with default preset.

        URL: https://acme-analytics.com/careers
        Expected preset: default (careers maps to default preset)
        Expected selectors: main, article, body (from default preset)
        """
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        fixture_name = "careers_page"
        html_content = load_fixture(f"{fixture_name}.html")
        url = "https://acme-analytics.com/careers"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=3,
                url=url,
            )

            actual = strip_timing_fields(result.to_dict())

            if should_regenerate():
                save_expected(fixture_name, actual)
                pytest.skip(f"Regenerated golden file for {fixture_name}")

            expected = load_expected(fixture_name)
            if expected is None:
                save_expected(fixture_name, actual)
                pytest.skip(f"Created initial golden file for {fixture_name}")

            expected = strip_timing_fields(expected)

            assert actual == expected, format_diff(expected, actual)


class TestGoldenFileMinimalPage:
    """Golden-file tests for minimal/broken page fallback behavior."""

    @pytest.mark.asyncio
    async def test_minimal_page_fallback(self):
        """
        Test minimal page triggers fallback behavior.

        URL: https://acme-analytics.com/coming-soon
        Expected preset: default (unknown page type)
        Expected behavior: Falls back to body extraction due to minimal content
        """
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        fixture_name = "minimal_page"
        html_content = load_fixture(f"{fixture_name}.html")
        url = "https://acme-analytics.com/coming-soon"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=4,
                url=url,
            )

            actual = strip_timing_fields(result.to_dict())

            if should_regenerate():
                save_expected(fixture_name, actual)
                pytest.skip(f"Regenerated golden file for {fixture_name}")

            expected = load_expected(fixture_name)
            if expected is None:
                save_expected(fixture_name, actual)
                pytest.skip(f"Created initial golden file for {fixture_name}")

            expected = strip_timing_fields(expected)

            assert actual == expected, format_diff(expected, actual)


class TestGoldenFileLandingPage:
    """Golden-file tests for landing/homepage extraction."""

    @pytest.mark.asyncio
    async def test_landing_page_extraction(self):
        """
        Test landing page extracts correctly with default preset.

        URL: https://acme-analytics.com/
        Expected preset: default (landing page maps to default)
        Expected selectors: main, article, body (from default preset)
        """
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        fixture_name = "landing_page"
        html_content = load_fixture(f"{fixture_name}.html")
        url = "https://acme-analytics.com/"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(
                watch_id=5,
                url=url,
            )

            actual = strip_timing_fields(result.to_dict())

            if should_regenerate():
                save_expected(fixture_name, actual)
                pytest.skip(f"Regenerated golden file for {fixture_name}")

            expected = load_expected(fixture_name)
            if expected is None:
                save_expected(fixture_name, actual)
                pytest.skip(f"Created initial golden file for {fixture_name}")

            expected = strip_timing_fields(expected)

            assert actual == expected, format_diff(expected, actual)


class TestGoldenFileHelpers:
    """Tests for helper functions used in golden-file testing."""

    def test_strip_timing_fields_removes_expected_fields(self):
        """strip_timing_fields should remove all timing-related fields."""
        data = {
            "watch_id": 1,
            "total_time_ms": 100,
            "fetch_artifact": {
                "url": "https://example.com",
                "fetch_time_ms": 50,
                "fetched_at": "2025-01-15T12:00:00Z",
            },
            "representations": [
                {
                    "content": "text",
                    "extraction_time_ms": 10,
                }
            ],
            "created_at": "2025-01-15T12:00:00Z",
        }

        result = strip_timing_fields(data)

        assert "watch_id" in result
        assert "total_time_ms" not in result
        assert "fetch_time_ms" not in result["fetch_artifact"]
        assert "fetched_at" not in result["fetch_artifact"]
        assert "extraction_time_ms" not in result["representations"][0]
        assert "created_at" not in result

    def test_strip_timing_fields_preserves_other_fields(self):
        """strip_timing_fields should preserve non-timing fields."""
        data = {
            "watch_id": 1,
            "url": "https://example.com",
            "success": True,
            "content": "Hello world",
            "nested": {
                "value": 42,
                "list": [1, 2, 3],
            }
        }

        result = strip_timing_fields(data)

        assert result == data

    def test_load_fixture_loads_html_file(self):
        """load_fixture should load HTML content from fixtures directory."""
        content = load_fixture("pricing_page.html")

        assert "<!DOCTYPE html>" in content
        assert "pricing" in content.lower()

    def test_load_fixture_raises_for_missing_file(self):
        """load_fixture should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            load_fixture("nonexistent_fixture.html")


class TestGoldenFileContentVerification:
    """Tests that verify expected content characteristics in golden files."""

    @pytest.mark.asyncio
    async def test_pricing_page_uses_correct_preset(self):
        """Pricing page should auto-select pricing_table_v1 preset."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        html_content = load_fixture("pricing_page.html")
        url = "https://acme-analytics.com/pricing"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(watch_id=1, url=url)

            assert result.preset_used == "pricing_table_v1"
            assert result.success is True
            assert len(result.representations) > 0

    @pytest.mark.asyncio
    async def test_blog_post_uses_correct_preset(self):
        """Blog post should auto-select blog_post_v1 preset."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        html_content = load_fixture("blog_post.html")
        url = "https://acme-analytics.com/blog/introducing-3-0"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(watch_id=2, url=url)

            assert result.preset_used == "blog_post_v1"
            assert result.success is True

    @pytest.mark.asyncio
    async def test_careers_page_extracts_job_content(self):
        """Careers page should extract job-related content."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        html_content = load_fixture("careers_page.html")
        url = "https://acme-analytics.com/careers"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(watch_id=3, url=url)

            assert result.success is True
            assert len(result.representations) > 0

            # Content should include job-related terms
            content = result.get_primary_content() or ""
            content_lower = content.lower()
            assert any(term in content_lower for term in ["hiring", "team", "positions", "engineer"])

    @pytest.mark.asyncio
    async def test_minimal_page_has_low_confidence(self):
        """Minimal page should have low confidence due to limited content."""
        from monitoring.content_pipeline.orchestrator import ContentPipeline

        html_content = load_fixture("minimal_page.html")
        url = "https://acme-analytics.com/coming-soon"

        mock_artifact = create_mock_fetch_artifact(url, html_content)

        with patch(
            "monitoring.content_pipeline.orchestrator.TransportEscalator"
        ) as MockEscalator:
            mock_escalator = MagicMock()
            mock_escalator.fetch = AsyncMock(return_value=mock_artifact)
            MockEscalator.return_value = mock_escalator

            pipeline = ContentPipeline()
            result = await pipeline.process_url(watch_id=4, url=url)

            assert result.success is True
            # With minimal content, confidence should be low
            if result.representations:
                # Low confidence expected for very short content
                assert result.representations[0].confidence <= 0.7
