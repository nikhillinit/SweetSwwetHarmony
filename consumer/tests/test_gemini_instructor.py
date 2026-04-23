"""Tests for Instructor Gemini compatibility helpers."""

import warnings
from unittest.mock import MagicMock, patch

from consumer._gemini_instructor import load_instructor_genai


class TestLoadInstructorGenAI:
    def test_suppresses_known_google_generativeai_deprecation_warning(self):
        instructor_module = MagicMock()
        types_module = MagicMock()

        def fake_import_module(module_name: str):
            if module_name == "instructor":
                warnings.warn(
                    (
                        "\n\nAll support for the `google.generativeai` package has ended. "
                        "It will no longer be receiving updates or bug fixes."
                    ),
                    FutureWarning,
                    stacklevel=1,
                )
                return instructor_module
            if module_name == "google.genai.types":
                return types_module
            raise ImportError(module_name)

        with patch(
            "consumer._gemini_instructor.importlib.import_module",
            side_effect=fake_import_module,
        ):
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                deps = load_instructor_genai()

        assert deps is not None
        assert deps.instructor is instructor_module
        assert deps.types is types_module
        assert captured == []

    def test_returns_none_when_dependencies_are_missing(self):
        with patch(
            "consumer._gemini_instructor.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            assert load_instructor_genai() is None
