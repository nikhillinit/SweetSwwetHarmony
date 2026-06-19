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


def _string_value(node: ast.AST, constants: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
    ):
        if node.args:
            return _string_value(node.args[0], constants)
    return None


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            value = _string_value(node.value, constants)
            if value is not None:
                constants[node.targets[0].id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = (
                _string_value(node.value, constants)
                if node.value is not None
                else None
            )
            if value is not None:
                constants[node.target.id] = value
    return constants


def _default_is_bad(node: ast.AST, constants: dict[str, str]) -> bool:
    if _string_value(node, constants) == "signals.db":
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.func.attr == "getenv"
        ):
            args = node.args
            if len(args) >= 2:
                return _string_value(args[1], constants) == "signals.db"
    return False


def test_scripts_do_not_use_literal_signals_db_parser_defaults() -> None:
    violations: list[str] = []

    for path in (ROOT / "scripts").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_string_constants(tree)
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not _is_parser_add_argument(node)
                or not _targets_db_arg(node)
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg == "default" and _default_is_bad(keyword.value, constants):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")

    assert violations == [], "Found parser defaults still hard-coded to signals.db:\n" + "\n".join(violations)
