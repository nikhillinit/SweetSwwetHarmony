"""
Tests for JSON canonicalization utilities.

Tests key sorting, volatile key detection, and deterministic JSON output.
"""

import json
import pytest

from monitoring.content_pipeline.json_canonical import (
    canonicalize_json,
    is_volatile_key,
    remove_volatile_keys,
    DEFAULT_VOLATILE_PATTERNS,
    CanonicalizeOptions,
)


class TestIsVolatileKey:
    """Tests for volatile key detection."""

    def test_timestamp_keys_are_volatile(self) -> None:
        """Test that timestamp-related keys are detected as volatile."""
        volatile_keys = [
            "timestamp",
            "created_at",
            "updated_at",
            "createdAt",
            "updatedAt",
            "lastModified",
            "modified_at",
        ]
        for key in volatile_keys:
            assert is_volatile_key(key), f"{key} should be volatile"

    def test_build_and_version_keys_are_volatile(self) -> None:
        """Test that build/version keys are detected as volatile."""
        volatile_keys = [
            "buildId",
            "build_id",
            "deploymentId",
            "version",
            "appVersion",
        ]
        for key in volatile_keys:
            assert is_volatile_key(key), f"{key} should be volatile"

    def test_session_and_auth_keys_are_volatile(self) -> None:
        """Test that session/auth keys are detected as volatile."""
        volatile_keys = [
            "sessionId",
            "session_id",
            "csrfToken",
            "csrf_token",
            "nonce",
            "token",
            "accessToken",
        ]
        for key in volatile_keys:
            assert is_volatile_key(key), f"{key} should be volatile"

    def test_request_tracking_keys_are_volatile(self) -> None:
        """Test that request tracking keys are detected as volatile."""
        volatile_keys = [
            "requestId",
            "request_id",
            "traceId",
            "trace_id",
            "correlationId",
        ]
        for key in volatile_keys:
            assert is_volatile_key(key), f"{key} should be volatile"

    def test_nextjs_runtime_keys_are_volatile(self) -> None:
        """Test that Next.js runtime keys are detected as volatile."""
        volatile_keys = [
            "__N_SSG",
            "__N_SSP",
            "__N_RSC",
            "rsc",
            "__flight",
        ]
        for key in volatile_keys:
            assert is_volatile_key(key), f"{key} should be volatile"

    def test_content_keys_are_not_volatile(self) -> None:
        """Test that content keys are not detected as volatile."""
        non_volatile_keys = [
            "title",
            "name",
            "description",
            "price",
            "products",
            "users",
            "id",  # Primary IDs are not volatile
            "slug",
            "url",
            "href",
        ]
        for key in non_volatile_keys:
            assert not is_volatile_key(key), f"{key} should not be volatile"

    def test_custom_patterns(self) -> None:
        """Test custom volatile patterns."""
        custom_patterns = [r"^custom_", r"_temp$"]
        assert is_volatile_key("custom_field", patterns=custom_patterns)
        assert is_volatile_key("data_temp", patterns=custom_patterns)
        assert not is_volatile_key("regular_field", patterns=custom_patterns)


class TestRemoveVolatileKeys:
    """Tests for volatile key removal."""

    def test_removes_top_level_volatile_keys(self) -> None:
        """Test removal of top-level volatile keys."""
        data = {
            "title": "Hello",
            "timestamp": 1234567890,
            "buildId": "abc123",
            "content": "Test",
        }
        result = remove_volatile_keys(data)

        assert "title" in result
        assert "content" in result
        assert "timestamp" not in result
        assert "buildId" not in result

    def test_removes_nested_volatile_keys(self) -> None:
        """Test removal of nested volatile keys."""
        data = {
            "page": {
                "title": "Test",
                "updatedAt": "2024-01-01",
                "data": {
                    "items": [1, 2, 3],
                    "sessionId": "xyz",
                },
            },
        }
        result = remove_volatile_keys(data)

        assert result["page"]["title"] == "Test"
        assert "updatedAt" not in result["page"]
        assert result["page"]["data"]["items"] == [1, 2, 3]
        assert "sessionId" not in result["page"]["data"]

    def test_handles_arrays(self) -> None:
        """Test volatile key removal in arrays."""
        data = {
            "items": [
                {"name": "A", "timestamp": 123},
                {"name": "B", "timestamp": 456},
            ]
        }
        result = remove_volatile_keys(data)

        assert result["items"][0]["name"] == "A"
        assert "timestamp" not in result["items"][0]
        assert result["items"][1]["name"] == "B"
        assert "timestamp" not in result["items"][1]

    def test_preserves_non_dict_values(self) -> None:
        """Test that non-dict values are preserved."""
        data = {
            "string": "hello",
            "number": 42,
            "boolean": True,
            "null": None,
            "array": [1, 2, 3],
        }
        result = remove_volatile_keys(data)
        assert result == data

    def test_empty_input(self) -> None:
        """Test with empty dict."""
        assert remove_volatile_keys({}) == {}

    def test_custom_patterns(self) -> None:
        """Test removal with custom patterns."""
        data = {
            "keep_this": "yes",
            "temp_data": "no",
            "session_id": "no",
        }
        # Only remove temp_ prefix
        result = remove_volatile_keys(data, patterns=[r"^temp_"])

        assert "keep_this" in result
        assert "temp_data" not in result
        assert "session_id" in result  # Default patterns not used


class TestCanonicalizeJson:
    """Tests for JSON canonicalization."""

    def test_sorts_keys(self) -> None:
        """Test that keys are sorted alphabetically."""
        data = {"z": 1, "a": 2, "m": 3}
        result = canonicalize_json(data)

        # Keys should be in sorted order
        keys = list(json.loads(result).keys())
        assert keys == ["a", "m", "z"]

    def test_sorts_nested_keys(self) -> None:
        """Test that nested keys are also sorted."""
        data = {"outer": {"z": 1, "a": 2}, "inner": {"b": 3, "a": 4}}
        result = canonicalize_json(data)

        parsed = json.loads(result)
        assert list(parsed.keys()) == ["inner", "outer"]
        assert list(parsed["outer"].keys()) == ["a", "z"]
        assert list(parsed["inner"].keys()) == ["a", "b"]

    def test_removes_volatile_keys_by_default(self) -> None:
        """Test that volatile keys are removed by default."""
        data = {
            "title": "Test",
            "buildId": "abc123",
            "timestamp": 1234567890,
        }
        result = canonicalize_json(data)
        parsed = json.loads(result)

        assert "title" in parsed
        assert "buildId" not in parsed
        assert "timestamp" not in parsed

    def test_can_preserve_volatile_keys(self) -> None:
        """Test option to preserve volatile keys."""
        data = {
            "title": "Test",
            "buildId": "abc123",
        }
        options = CanonicalizeOptions(remove_volatile=False)
        result = canonicalize_json(data, options=options)
        parsed = json.loads(result)

        assert "title" in parsed
        assert "buildId" in parsed

    def test_deterministic_output(self) -> None:
        """Test that output is deterministic for same input."""
        data = {"z": 1, "a": {"y": 2, "x": 3}, "m": [1, 2, 3]}

        result1 = canonicalize_json(data)
        result2 = canonicalize_json(data)

        assert result1 == result2

    def test_handles_nextjs_data(self) -> None:
        """Test canonicalization of Next.js __NEXT_DATA__ structure."""
        data = {
            "props": {
                "pageProps": {
                    "products": [{"name": "A"}, {"name": "B"}],
                },
                "__N_SSG": True,
            },
            "page": "/products",
            "query": {},
            "buildId": "abc123",
            "rsc": "some-data",
        }
        result = canonicalize_json(data)
        parsed = json.loads(result)

        # Volatile keys should be removed
        assert "buildId" not in parsed
        assert "__N_SSG" not in parsed["props"]
        assert "rsc" not in parsed

        # Content should be preserved
        assert parsed["props"]["pageProps"]["products"][0]["name"] == "A"
        assert parsed["page"] == "/products"

    def test_compact_output_by_default(self) -> None:
        """Test that output is compact by default."""
        data = {"a": 1, "b": 2}
        result = canonicalize_json(data)

        # No extra whitespace
        assert result == '{"a": 1, "b": 2}'

    def test_pretty_output_option(self) -> None:
        """Test pretty-printed output option."""
        data = {"a": 1, "b": 2}
        options = CanonicalizeOptions(indent=2)
        result = canonicalize_json(data, options=options)

        # Should have newlines and indentation
        assert "\n" in result
        assert "  " in result

    def test_handles_empty_dict(self) -> None:
        """Test with empty dict."""
        result = canonicalize_json({})
        assert result == "{}"

    def test_handles_string_input(self) -> None:
        """Test with JSON string input."""
        json_str = '{"z": 1, "a": 2}'
        result = canonicalize_json(json_str)
        parsed = json.loads(result)

        assert list(parsed.keys()) == ["a", "z"]


class TestDefaultVolatilePatterns:
    """Tests for default volatile patterns."""

    def test_patterns_are_valid_regex(self) -> None:
        """Test that all default patterns are valid regex."""
        import re

        for pattern in DEFAULT_VOLATILE_PATTERNS:
            # Should not raise
            re.compile(pattern, re.IGNORECASE)

    def test_patterns_cover_common_cases(self) -> None:
        """Test that patterns cover common volatile key names."""
        expected_volatile = [
            "timestamp",
            "buildId",
            "sessionId",
            "csrfToken",
            "nonce",
            "__N_SSG",
            "createdAt",
            "updatedAt",
        ]
        for key in expected_volatile:
            assert is_volatile_key(key), f"Expected {key} to be volatile"
