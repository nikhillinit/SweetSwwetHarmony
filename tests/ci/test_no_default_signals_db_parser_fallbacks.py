from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _is_parser_add_argument(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr == "add_argument"


def _targets_db_arg(call: ast.Call) -> bool:
    for arg in call.args:
        if isinstance(arg, ast.Constant) and arg.value in {"--db", "--db-path"}:
            return True
    return False


def _default_is_bad(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and node.value == "signals.db":
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and node.func.attr == "getenv":
            args = node.args
            if len(args) >= 2:
                second = args[1]
                return isinstance(second, ast.Constant) and second.value == "signals.db"
    return False


def test_scripts_do_not_use_literal_signals_db_parser_defaults() -> None:
    violations: list[str] = []

    for path in (ROOT / "scripts").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_parser_add_argument(node) or not _targets_db_arg(node):
                continue
            for keyword in node.keywords:
                if keyword.arg == "default" and _default_is_bad(keyword.value):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert violations == [], "Found parser defaults still hard-coded to signals.db:\n" + "\n".join(violations)
