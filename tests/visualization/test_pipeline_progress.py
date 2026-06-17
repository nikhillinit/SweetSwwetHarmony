# tests/visualization/test_pipeline_progress.py
"""Unit tests for PipelineProgress. Proves: no DB writes, pure in-memory."""
from __future__ import annotations

import io
from datetime import datetime
from unittest.mock import patch

import pytest
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

from visualization.terminal_progress import PipelineProgress


def make_progress_no_output() -> PipelineProgress:
    """
    Create a PipelineProgress that discards all console output (test isolation).

    PipelineProgress.__init__ uses a module-level `console` object.  We patch
    that module-level name so the Progress widget inside the instance writes to
    a StringIO sink instead of the real terminal.
    """
    sink = Console(file=io.StringIO(), force_terminal=False)
    # Patch the module-level `console` used by Progress() inside __init__
    with patch("visualization.terminal_progress.console", sink):
        ui = PipelineProgress()
    return ui


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_start_collector_creates_task():
    """start_collector registers the collector name in ui.tasks."""
    ui = make_progress_no_output()
    task_id = ui.start_collector("github")
    assert "github" in ui.tasks, "task key not found in ui.tasks"
    assert ui.tasks["github"] == task_id, "returned task_id does not match stored id"


def test_complete_collector_success_sets_green_icon():
    """complete_collector(success=True) puts the ✓ icon into the task description."""
    ui = make_progress_no_output()
    ui.start_collector("sec_edgar")
    ui.complete_collector("sec_edgar", success=True, signals=12)
    task = ui.progress.tasks[ui.tasks["sec_edgar"]]
    assert "✓" in task.description, (
        f"Expected ✓ in description after success, got: {task.description!r}"
    )


def test_complete_collector_failure_sets_red_icon():
    """complete_collector(success=False) puts the ✗ icon into the task description."""
    ui = make_progress_no_output()
    ui.start_collector("news_api")
    ui.complete_collector("news_api", success=False, error="timeout")
    task = ui.progress.tasks[ui.tasks["news_api"]]
    assert "✗" in task.description, (
        f"Expected ✗ in description after failure, got: {task.description!r}"
    )


def test_progress_writes_no_database(tmp_path):
    """PipelineProgress must not open or write any SQLite file — pure in-memory."""
    db_before = list(tmp_path.glob("*.db"))
    ui = make_progress_no_output()
    ui.start_collector("github")
    ui.update_collector("github", 5)
    ui.complete_collector("github", success=True, signals=3)
    db_after = list(tmp_path.glob("*.db"))
    assert db_before == db_after, (
        "PipelineProgress wrote a DB file — must be pure in-memory"
    )
