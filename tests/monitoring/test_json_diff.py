"""
Tests for structured JSON diff utilities.

Tests semantic JSON diffing for comparing snapshots.
"""

import json
import pytest

from monitoring.content_pipeline.json_diff import (
    diff_json,
    DiffResult,
    ChangeType,
    JsonChange,
)


class TestDiffJsonBasic:
    """Basic JSON diff tests."""

    def test_identical_objects_have_no_changes(self) -> None:
        """Test that identical objects produce no changes."""
        obj1 = {"a": 1, "b": 2}
        obj2 = {"a": 1, "b": 2}

        result = diff_json(obj1, obj2)

        assert result.has_changes is False
        assert len(result.changes) == 0

    def test_added_key_detected(self) -> None:
        """Test detection of added keys."""
        obj1 = {"a": 1}
        obj2 = {"a": 1, "b": 2}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 1
        change = result.changes[0]
        assert change.change_type == ChangeType.ADDED
        assert change.path == "$.b"
        assert change.new_value == 2

    def test_removed_key_detected(self) -> None:
        """Test detection of removed keys."""
        obj1 = {"a": 1, "b": 2}
        obj2 = {"a": 1}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 1
        change = result.changes[0]
        assert change.change_type == ChangeType.REMOVED
        assert change.path == "$.b"
        assert change.old_value == 2

    def test_modified_value_detected(self) -> None:
        """Test detection of modified values."""
        obj1 = {"a": 1}
        obj2 = {"a": 2}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 1
        change = result.changes[0]
        assert change.change_type == ChangeType.MODIFIED
        assert change.path == "$.a"
        assert change.old_value == 1
        assert change.new_value == 2


class TestDiffJsonNested:
    """Tests for nested object diffs."""

    def test_nested_added_key(self) -> None:
        """Test detection of nested added keys."""
        obj1 = {"outer": {"a": 1}}
        obj2 = {"outer": {"a": 1, "b": 2}}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 1
        assert result.changes[0].path == "$.outer.b"
        assert result.changes[0].change_type == ChangeType.ADDED

    def test_deeply_nested_change(self) -> None:
        """Test detection of deeply nested changes."""
        obj1 = {"a": {"b": {"c": {"d": 1}}}}
        obj2 = {"a": {"b": {"c": {"d": 2}}}}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 1
        assert result.changes[0].path == "$.a.b.c.d"
        assert result.changes[0].change_type == ChangeType.MODIFIED

    def test_multiple_nested_changes(self) -> None:
        """Test multiple changes at different nesting levels."""
        obj1 = {"a": 1, "b": {"c": 2, "d": 3}}
        obj2 = {"a": 10, "b": {"c": 2, "d": 30}}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 2
        paths = {c.path for c in result.changes}
        assert "$.a" in paths
        assert "$.b.d" in paths


class TestDiffJsonArrays:
    """Tests for array diffs."""

    def test_array_element_added(self) -> None:
        """Test detection of added array elements."""
        obj1 = {"items": [1, 2]}
        obj2 = {"items": [1, 2, 3]}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        # Should detect the array changed
        changes_at_items = [c for c in result.changes if "items" in c.path]
        assert len(changes_at_items) >= 1

    def test_array_element_removed(self) -> None:
        """Test detection of removed array elements."""
        obj1 = {"items": [1, 2, 3]}
        obj2 = {"items": [1, 2]}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True

    def test_array_element_modified(self) -> None:
        """Test detection of modified array elements."""
        obj1 = {"items": [1, 2, 3]}
        obj2 = {"items": [1, 20, 3]}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        # Should detect change at items[1]
        changes_at_items = [c for c in result.changes if "items" in c.path]
        assert len(changes_at_items) >= 1

    def test_array_of_objects_change(self) -> None:
        """Test detection of changes in arrays of objects."""
        obj1 = {"products": [{"id": 1, "price": 10}, {"id": 2, "price": 20}]}
        obj2 = {"products": [{"id": 1, "price": 15}, {"id": 2, "price": 20}]}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        # Should detect price change in first product
        price_changes = [c for c in result.changes if "price" in c.path]
        assert len(price_changes) == 1


class TestDiffJsonTypeChanges:
    """Tests for type change detection."""

    def test_type_change_string_to_number(self) -> None:
        """Test detection of type changes."""
        obj1 = {"value": "123"}
        obj2 = {"value": 123}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert result.changes[0].change_type == ChangeType.MODIFIED

    def test_type_change_null_to_value(self) -> None:
        """Test change from null to a value."""
        obj1 = {"value": None}
        obj2 = {"value": "hello"}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert result.changes[0].change_type == ChangeType.MODIFIED

    def test_type_change_object_to_array(self) -> None:
        """Test change from object to array."""
        obj1 = {"data": {"a": 1}}
        obj2 = {"data": [1, 2, 3]}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True


class TestDiffResultFormatting:
    """Tests for DiffResult formatting."""

    def test_summary_no_changes(self) -> None:
        """Test summary for no changes."""
        result = DiffResult(changes=[])
        summary = result.summary()

        assert "no changes" in summary.lower()

    def test_summary_with_changes(self) -> None:
        """Test summary with changes."""
        changes = [
            JsonChange(path="$.a", change_type=ChangeType.ADDED, new_value=1),
            JsonChange(path="$.b", change_type=ChangeType.REMOVED, old_value=2),
        ]
        result = DiffResult(changes=changes)
        summary = result.summary()

        assert "1 added" in summary.lower()
        assert "1 removed" in summary.lower()

    def test_to_dict(self) -> None:
        """Test conversion to dict."""
        changes = [
            JsonChange(path="$.a", change_type=ChangeType.MODIFIED, old_value=1, new_value=2),
        ]
        result = DiffResult(changes=changes)
        d = result.to_dict()

        assert "changes" in d
        assert len(d["changes"]) == 1
        assert d["changes"][0]["path"] == "$.a"
        assert d["changes"][0]["type"] == "modified"


class TestDiffJsonStringInput:
    """Tests for JSON string input."""

    def test_accepts_json_strings(self) -> None:
        """Test that diff_json accepts JSON strings."""
        json1 = '{"a": 1}'
        json2 = '{"a": 2}'

        result = diff_json(json1, json2)

        assert result.has_changes is True
        assert result.changes[0].path == "$.a"

    def test_mixed_string_and_dict_input(self) -> None:
        """Test mixed string and dict input."""
        json1 = '{"a": 1}'
        obj2 = {"a": 2}

        result = diff_json(json1, obj2)

        assert result.has_changes is True


class TestDiffJsonEdgeCases:
    """Edge case tests."""

    def test_empty_objects(self) -> None:
        """Test diff of empty objects."""
        result = diff_json({}, {})
        assert result.has_changes is False

    def test_one_empty_one_populated(self) -> None:
        """Test diff where one object is empty."""
        result = diff_json({}, {"a": 1})

        assert result.has_changes is True
        assert result.changes[0].change_type == ChangeType.ADDED

    def test_boolean_changes(self) -> None:
        """Test detection of boolean changes."""
        obj1 = {"active": True}
        obj2 = {"active": False}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert result.changes[0].old_value is True
        assert result.changes[0].new_value is False

    def test_large_objects(self) -> None:
        """Test diff of larger objects."""
        obj1 = {f"key_{i}": i for i in range(100)}
        obj2 = {f"key_{i}": i + 1 for i in range(100)}

        result = diff_json(obj1, obj2)

        assert result.has_changes is True
        assert len(result.changes) == 100
