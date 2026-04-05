from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESTORE_DB = ROOT / "scripts" / "restore_db.py"


def _has_import(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "utils.db_path_helper":
            if any(alias.name == name for alias in node.names):
                return True
    return False


def _has_call(tree: ast.AST, func_name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func_name:
            return True
    return False


def test_restore_db_uses_shared_db_path_helper_contract() -> None:
    tree = ast.parse(RESTORE_DB.read_text(encoding="utf-8"))

    assert _has_import(tree, "add_db_path_args")
    assert _has_import(tree, "resolve_db_path")
    assert _has_call(tree, "add_db_path_args")
    assert _has_call(tree, "resolve_db_path")


def test_restore_db_no_longer_declares_local_default_db_constant() -> None:
    tree = ast.parse(RESTORE_DB.read_text(encoding="utf-8"))
    names = {
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }

    assert "DEFAULT_DB" not in names
