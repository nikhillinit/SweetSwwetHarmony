"""
Structured JSON Diff Utilities.

Provides semantic diffing of JSON objects to show what changed
between snapshots in a human-readable format.

Features:
- Deep comparison of nested objects
- Array element tracking
- JSONPath-style paths for changes
- Human-readable summaries
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class ChangeType(str, Enum):
    """Types of changes detected in JSON diff."""

    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"


@dataclass
class JsonChange:
    """
    Represents a single change in a JSON diff.

    Attributes:
        path: JSONPath-style path to the changed value (e.g., "$.products[0].price")
        change_type: Type of change (ADDED, REMOVED, MODIFIED)
        old_value: Previous value (for REMOVED and MODIFIED)
        new_value: New value (for ADDED and MODIFIED)
    """

    path: str
    change_type: ChangeType
    old_value: Any = None
    new_value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "path": self.path,
            "type": self.change_type.value,
        }
        if self.old_value is not None:
            result["old_value"] = self.old_value
        if self.new_value is not None:
            result["new_value"] = self.new_value
        return result

    def __str__(self) -> str:
        """Human-readable string representation."""
        if self.change_type == ChangeType.ADDED:
            return f"+ {self.path}: {_format_value(self.new_value)}"
        elif self.change_type == ChangeType.REMOVED:
            return f"- {self.path}: {_format_value(self.old_value)}"
        else:  # MODIFIED
            return f"~ {self.path}: {_format_value(self.old_value)} -> {_format_value(self.new_value)}"


@dataclass
class DiffResult:
    """
    Result of a JSON diff operation.

    Attributes:
        changes: List of detected changes
    """

    changes: List[JsonChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if any changes were detected."""
        return len(self.changes) > 0

    @property
    def added_count(self) -> int:
        """Count of added keys/elements."""
        return sum(1 for c in self.changes if c.change_type == ChangeType.ADDED)

    @property
    def removed_count(self) -> int:
        """Count of removed keys/elements."""
        return sum(1 for c in self.changes if c.change_type == ChangeType.REMOVED)

    @property
    def modified_count(self) -> int:
        """Count of modified values."""
        return sum(1 for c in self.changes if c.change_type == ChangeType.MODIFIED)

    def summary(self) -> str:
        """
        Generate a human-readable summary of changes.

        Returns:
            Summary string like "3 added, 1 removed, 2 modified"
        """
        if not self.has_changes:
            return "No changes detected"

        parts = []
        if self.added_count > 0:
            parts.append(f"{self.added_count} added")
        if self.removed_count > 0:
            parts.append(f"{self.removed_count} removed")
        if self.modified_count > 0:
            parts.append(f"{self.modified_count} modified")

        return ", ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "has_changes": self.has_changes,
            "summary": self.summary(),
            "added_count": self.added_count,
            "removed_count": self.removed_count,
            "modified_count": self.modified_count,
            "changes": [c.to_dict() for c in self.changes],
        }

    def __str__(self) -> str:
        """Human-readable string representation."""
        if not self.has_changes:
            return "No changes"

        lines = [self.summary(), ""]
        for change in self.changes:
            lines.append(str(change))
        return "\n".join(lines)


def diff_json(
    obj1: Union[str, Dict[str, Any]],
    obj2: Union[str, Dict[str, Any]],
) -> DiffResult:
    """
    Compare two JSON objects and return the differences.

    Args:
        obj1: First JSON object (or JSON string)
        obj2: Second JSON object (or JSON string)

    Returns:
        DiffResult containing all detected changes
    """
    # Parse JSON strings if needed
    if isinstance(obj1, str):
        obj1 = json.loads(obj1)
    if isinstance(obj2, str):
        obj2 = json.loads(obj2)

    changes: List[JsonChange] = []
    _compare_values(obj1, obj2, "$", changes)

    return DiffResult(changes=changes)


def _compare_values(
    val1: Any,
    val2: Any,
    path: str,
    changes: List[JsonChange],
) -> None:
    """
    Recursively compare two values and collect changes.

    Args:
        val1: First value
        val2: Second value
        path: Current JSONPath
        changes: List to append changes to
    """
    # Same type and value
    if val1 == val2:
        return

    # Both are dicts - compare keys
    if isinstance(val1, dict) and isinstance(val2, dict):
        _compare_dicts(val1, val2, path, changes)
        return

    # Both are lists - compare elements
    if isinstance(val1, list) and isinstance(val2, list):
        _compare_lists(val1, val2, path, changes)
        return

    # Different types or values - it's a modification
    changes.append(
        JsonChange(
            path=path,
            change_type=ChangeType.MODIFIED,
            old_value=val1,
            new_value=val2,
        )
    )


def _compare_dicts(
    dict1: Dict[str, Any],
    dict2: Dict[str, Any],
    path: str,
    changes: List[JsonChange],
) -> None:
    """
    Compare two dictionaries and collect changes.

    Args:
        dict1: First dictionary
        dict2: Second dictionary
        path: Current JSONPath
        changes: List to append changes to
    """
    keys1 = set(dict1.keys())
    keys2 = set(dict2.keys())

    # Keys only in dict1 (removed)
    for key in keys1 - keys2:
        key_path = f"{path}.{key}"
        changes.append(
            JsonChange(
                path=key_path,
                change_type=ChangeType.REMOVED,
                old_value=dict1[key],
            )
        )

    # Keys only in dict2 (added)
    for key in keys2 - keys1:
        key_path = f"{path}.{key}"
        changes.append(
            JsonChange(
                path=key_path,
                change_type=ChangeType.ADDED,
                new_value=dict2[key],
            )
        )

    # Keys in both (may be modified)
    for key in keys1 & keys2:
        key_path = f"{path}.{key}"
        _compare_values(dict1[key], dict2[key], key_path, changes)


def _compare_lists(
    list1: List[Any],
    list2: List[Any],
    path: str,
    changes: List[JsonChange],
) -> None:
    """
    Compare two lists and collect changes.

    Uses index-based comparison. For arrays of objects with stable IDs,
    consider using a more sophisticated algorithm.

    Args:
        list1: First list
        list2: Second list
        path: Current JSONPath
        changes: List to append changes to
    """
    len1 = len(list1)
    len2 = len(list2)
    max_len = max(len1, len2)

    for i in range(max_len):
        item_path = f"{path}[{i}]"

        if i >= len1:
            # Element added
            changes.append(
                JsonChange(
                    path=item_path,
                    change_type=ChangeType.ADDED,
                    new_value=list2[i],
                )
            )
        elif i >= len2:
            # Element removed
            changes.append(
                JsonChange(
                    path=item_path,
                    change_type=ChangeType.REMOVED,
                    old_value=list1[i],
                )
            )
        else:
            # Compare elements at same index
            _compare_values(list1[i], list2[i], item_path, changes)


def _format_value(value: Any, max_length: int = 50) -> str:
    """
    Format a value for display, truncating if too long.

    Args:
        value: Value to format
        max_length: Maximum length before truncation

    Returns:
        Formatted string representation
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        if len(value) > max_length:
            return f'"{value[:max_length]}..."'
        return f'"{value}"'
    if isinstance(value, (dict, list)):
        s = json.dumps(value)
        if len(s) > max_length:
            return f"{s[:max_length]}..."
        return s
    return str(value)
