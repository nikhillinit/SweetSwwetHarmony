"""
Golden-file tests for ATS (Applicant Tracking System) detection.

Tests ATS embed detection against realistic career page HTML fixtures.
Each fixture has a corresponding .expected.json with expected detection results.
"""

import json
from pathlib import Path

import pytest

from monitoring.content_pipeline.ats_discovery import (
    ATSSignatureDetector,
    ATSDiscoveryResult,
)

# Path to ATS fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "content_pipeline" / "ats"


def get_ats_fixtures():
    """
    Discover all ATS test fixtures.

    Yields tuples of (fixture_name, html_path, expected_path).
    """
    if not FIXTURES_DIR.exists():
        return

    for html_file in sorted(FIXTURES_DIR.glob("*.html")):
        expected_file = html_file.with_suffix(".expected.json")
        if expected_file.exists():
            yield (
                html_file.stem,  # e.g., "greenhouse_embed"
                html_file,
                expected_file,
            )


class TestATSGoldenFiles:
    """Golden-file tests for ATS detection."""

    @pytest.fixture
    def detector(self) -> ATSSignatureDetector:
        """Create detector instance."""
        return ATSSignatureDetector()

    @pytest.mark.parametrize(
        "fixture_name,html_path,expected_path",
        list(get_ats_fixtures()),
        ids=[f[0] for f in get_ats_fixtures()],
    )
    def test_ats_detection_matches_expected(
        self,
        detector: ATSSignatureDetector,
        fixture_name: str,
        html_path: Path,
        expected_path: Path,
    ) -> None:
        """Test that ATS detection matches expected results for each fixture."""
        # Load HTML
        html = html_path.read_text(encoding="utf-8")

        # Load expected result
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        # Run detection
        result = detector.detect(html)

        # Verify detection status
        if expected["detected"]:
            assert result is not None, (
                f"Expected ATS to be detected in {fixture_name}, but got None"
            )
            assert result.provider.value == expected["provider"], (
                f"Provider mismatch in {fixture_name}: "
                f"expected {expected['provider']}, got {result.provider.value}"
            )
            assert result.board_id == expected["board_id"], (
                f"Board ID mismatch in {fixture_name}: "
                f"expected {expected['board_id']}, got {result.board_id}"
            )
            assert result.api_url == expected["api_url"], (
                f"API URL mismatch in {fixture_name}: "
                f"expected {expected['api_url']}, got {result.api_url}"
            )
        else:
            assert result is None, (
                f"Expected no ATS detection in {fixture_name}, "
                f"but got {result.provider.value if result else 'None'}"
            )


class TestATSFixtureIntegrity:
    """Tests to ensure fixture files are valid and complete."""

    def test_all_html_files_have_expected_json(self) -> None:
        """Every HTML fixture should have a corresponding expected JSON."""
        if not FIXTURES_DIR.exists():
            pytest.skip("ATS fixtures directory not found")

        html_files = list(FIXTURES_DIR.glob("*.html"))
        assert len(html_files) > 0, "No HTML fixtures found"

        missing = []
        for html_file in html_files:
            expected_file = html_file.with_suffix(".expected.json")
            if not expected_file.exists():
                missing.append(html_file.name)

        assert not missing, f"Missing expected.json for: {', '.join(missing)}"

    def test_expected_json_files_are_valid(self) -> None:
        """All expected JSON files should be valid JSON with required fields."""
        if not FIXTURES_DIR.exists():
            pytest.skip("ATS fixtures directory not found")

        expected_files = list(FIXTURES_DIR.glob("*.expected.json"))
        assert len(expected_files) > 0, "No expected JSON files found"

        required_fields = {"provider", "board_id", "api_url", "detected"}

        for expected_file in expected_files:
            try:
                data = json.loads(expected_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in {expected_file.name}: {e}")

            missing_fields = required_fields - set(data.keys())
            assert not missing_fields, (
                f"Missing required fields in {expected_file.name}: {missing_fields}"
            )

    def test_fixture_count(self) -> None:
        """Verify we have the expected number of fixtures."""
        if not FIXTURES_DIR.exists():
            pytest.skip("ATS fixtures directory not found")

        fixtures = list(get_ats_fixtures())
        # We created 5 fixtures: greenhouse, lever, ashby, workable, no_ats
        assert len(fixtures) >= 5, (
            f"Expected at least 5 ATS fixtures, found {len(fixtures)}"
        )


class TestATSPlannerIntegration:
    """Integration tests for ATS detection with PipelinePlanner."""

    @pytest.mark.parametrize(
        "fixture_name,html_path,expected_path",
        list(get_ats_fixtures()),
        ids=[f[0] for f in get_ats_fixtures()],
    )
    def test_planner_uses_ats_preset_when_detected(
        self,
        fixture_name: str,
        html_path: Path,
        expected_path: Path,
    ) -> None:
        """Test that PipelinePlanner uses ats_api_v1 preset when ATS is detected."""
        from monitoring.content_pipeline.planner import PipelinePlanner

        # Load fixtures
        html = html_path.read_text(encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        # Plan with careers URL and HTML content
        planner = PipelinePlanner()
        result = planner.plan(f"https://example.com/careers", html=html)

        if expected["detected"]:
            assert result.preset == "ats_api_v1", (
                f"Expected ats_api_v1 preset for {fixture_name}, got {result.preset}"
            )
            assert result.metadata.get("ats_provider") == expected["provider"], (
                f"Provider metadata mismatch in {fixture_name}"
            )
            assert result.metadata.get("ats_board_id") == expected["board_id"], (
                f"Board ID metadata mismatch in {fixture_name}"
            )
            assert result.metadata.get("ats_api_url") == expected["api_url"], (
                f"API URL metadata mismatch in {fixture_name}"
            )
        else:
            assert result.preset == "default", (
                f"Expected default preset for {fixture_name} (no ATS), got {result.preset}"
            )
            assert "ats_provider" not in result.metadata, (
                f"Unexpected ats_provider in metadata for {fixture_name}"
            )
