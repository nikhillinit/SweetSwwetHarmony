from __future__ import annotations

from pathlib import Path


def test_root_integrations_exports_hermes_symbols() -> None:
    from integrations import HermesRoutingConfig, HermesRunResult, run_hermes

    assert HermesRoutingConfig.__name__ == "RoutingConfig"
    assert HermesRunResult.__name__ == "HermesRunResult"
    assert callable(run_hermes)


def test_hermes_operator_docs_are_present() -> None:
    runbook = Path("docs/runbooks/hermes.md")
    dev_brain = Path(".claude/hermes/DEV_BRAIN.md")
    claude = Path("CLAUDE.md")

    assert "JSON-only" in runbook.read_text(encoding="utf-8")
    assert "high-risk execute" in runbook.read_text(encoding="utf-8")
    assert "deferred" in dev_brain.read_text(encoding="utf-8").lower()
    assert "python -m ops.cli hermes" in claude.read_text(encoding="utf-8")
