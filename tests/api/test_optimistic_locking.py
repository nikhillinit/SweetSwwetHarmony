"""PR8 — Tests for optimistic locking correctness in entities router.

Validates that the stage update endpoint uses cursor.rowcount (statement-scoped)
instead of db.total_changes (connection-global) for version conflict detection.
"""

import ast
import os
import pytest


ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestOptimisticLockImplementation:
    """Verify entities.py uses cursor.rowcount, not db.total_changes."""

    def test_no_total_changes_in_entities(self):
        """entities.py must not use db.total_changes for version checks."""
        path = os.path.join(ROOT, "api", "routers", "entities.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename="entities.py")

        total_changes_refs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "total_changes":
                total_changes_refs.append(node.lineno)

        assert total_changes_refs == [], (
            f"entities.py references .total_changes at line(s) {total_changes_refs}. "
            f"Must use cursor.rowcount (statement-scoped) for optimistic lock checks."
        )

    def test_uses_cursor_rowcount(self):
        """entities.py must use cursor.rowcount for the version check."""
        path = os.path.join(ROOT, "api", "routers", "entities.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        # Check for cursor.rowcount usage
        assert "cursor.rowcount" in source, (
            "entities.py must use cursor.rowcount for optimistic lock conflict detection."
        )

    def test_update_captures_cursor(self):
        """The UPDATE entity_stages call must capture its cursor for rowcount check."""
        path = os.path.join(ROOT, "api", "routers", "entities.py")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename="entities.py")

        # Find the update_entity_stage function
        found_cursor_assign = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "cursor":
                        # Check if the value is an await of db.execute
                        if isinstance(node.value, ast.Await):
                            found_cursor_assign = True
                            break

        assert found_cursor_assign, (
            "entities.py must assign 'cursor = await db.execute(...)' "
            "to capture the cursor for rowcount checking."
        )


class TestOptimisticLockError:
    """Test OptimisticLockError behavior."""

    def test_optimistic_lock_error_default_message(self):
        from api.db import OptimisticLockError

        err = OptimisticLockError()
        assert "modified by another user" in err.message

    def test_optimistic_lock_error_custom_message(self):
        from api.db import OptimisticLockError

        err = OptimisticLockError("Custom conflict message")
        assert err.message == "Custom conflict message"

    def test_handle_optimistic_lock_error_raises_409(self):
        from fastapi import HTTPException
        from api.db import OptimisticLockError, handle_optimistic_lock_error

        with pytest.raises(HTTPException) as exc_info:
            handle_optimistic_lock_error(OptimisticLockError())
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["error"] == "conflict"


class TestConflictError:
    """Test ConflictError behavior."""

    def test_conflict_error_with_values(self):
        from api.db import ConflictError

        err = ConflictError("Version mismatch", local_value=3, remote_value=5)
        assert err.local_value == 3
        assert err.remote_value == 5

    def test_handle_conflict_error_raises_409(self):
        from fastapi import HTTPException
        from api.db import ConflictError, handle_conflict_error

        with pytest.raises(HTTPException) as exc_info:
            handle_conflict_error(ConflictError("Mismatch", local_value="a", remote_value="b"))
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["local_value"] == "a"
        assert exc_info.value.detail["remote_value"] == "b"
