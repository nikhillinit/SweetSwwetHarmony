from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRIORITY_SCRIPTS = [
    ROOT / "scripts" / "e2e_batch_check.py",
    ROOT / "scripts" / "e2e_batch_approve.py",
    ROOT / "scripts" / "export_labeling_review.py",
    ROOT / "scripts" / "run_backfill.py",
]
BAD_PATHS = {
    "signals.db",
    "c:/dev/harmonic/signals.db",
    "c:\\dev\\harmonic\\signals.db",
}


def _docstring_nodes(tree: ast.AST) -> set[ast.AST]:
    ignored: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ignored.add(first.value)
    return ignored


def test_priority_scripts_do_not_hardcode_production_db_access() -> None:
    violations: list[str] = []

    for path in PRIORITY_SCRIPTS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        ignored = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or node in ignored:
                continue
            normalized = node.value.strip().replace("\\", "/").lower()
            if normalized in BAD_PATHS:
                violations.append(f"{path.name}:{node.lineno}: hard-coded production DB access literal {node.value!r}")

    assert violations == [], "Found hard-coded production DB access patterns:\n" + "\n".join(violations)
