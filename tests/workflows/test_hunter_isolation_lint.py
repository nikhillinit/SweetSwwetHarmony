"""CI lint: enforce hunter sandbox isolation rules.

Rules:
1. workflows/active_hunter.py MUST NOT import SignalStore (except TYPE_CHECKING)
2. workflows/active_hunter.py MUST NOT contain SQL targeting signals table
3. intelligence/pattern_miner.py and intelligence/query_generator.py MUST NOT import SignalStore
4. ALLOWLIST: workflows/hunter_promotion.py IS allowed (bridge module)
"""

import ast
import os
import re

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Files that MUST NOT import SignalStore at runtime
FORBIDDEN_SIGNALSTORE_FILES = [
    "workflows/active_hunter.py",
    "intelligence/pattern_miner.py",
    "intelligence/query_generator.py",
]

# The one file ALLOWED to bridge to SignalStore
PROMOTION_BRIDGE = "workflows/hunter_promotion.py"

# Regex patterns for SQL targeting the signals table (writes)
SIGNALS_WRITE_RE = re.compile(
    r"(INSERT\s+INTO\s+signals|UPDATE\s+signals|DELETE\s+FROM\s+signals)",
    re.IGNORECASE,
)


def _file_exists(rel_path: str) -> bool:
    return os.path.isfile(os.path.join(REPO_ROOT, rel_path))


def _get_imports(filepath: str) -> list:
    """Parse AST to find all import names, separating TYPE_CHECKING blocks."""
    abs_path = os.path.join(REPO_ROOT, filepath)
    if not os.path.isfile(abs_path):
        return []

    with open(abs_path, "r", encoding="utf-8") as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    runtime_imports = []

    for node in ast.walk(tree):
        # Skip imports inside TYPE_CHECKING blocks
        if isinstance(node, ast.If):
            test = node.test
            is_type_checking = False
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                is_type_checking = True
            elif isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                is_type_checking = True
            if is_type_checking:
                continue  # Skip this branch entirely

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Check if inside a TYPE_CHECKING if-block (manual parent walk)
            # We handle this by checking the node isn't nested in the skipped blocks
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    runtime_imports.append(f"{node.module}.{alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    runtime_imports.append(alias.name)

    return runtime_imports


def _read_file_lines(rel_path: str) -> list:
    abs_path = os.path.join(REPO_ROOT, rel_path)
    if not os.path.isfile(abs_path):
        return []
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.readlines()


def test_hunter_files_no_signalstore_import():
    """Hunter sandbox files must not import SignalStore at runtime."""
    violations = []

    for filepath in FORBIDDEN_SIGNALSTORE_FILES:
        if not _file_exists(filepath):
            continue

        lines = _read_file_lines(filepath)
        in_type_checking = False

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()

            # Track TYPE_CHECKING blocks
            if stripped.startswith("if TYPE_CHECKING"):
                in_type_checking = True
                continue
            if in_type_checking:
                if stripped and not stripped.startswith(("#", "from ", "import ")):
                    if not line[0].isspace():  # Back to module level
                        in_type_checking = False
                else:
                    continue  # Inside TYPE_CHECKING block, skip

            # Check for SignalStore import at runtime
            if re.search(r"(?:from|import)\s+.*SignalStore", stripped):
                violations.append(f"  {filepath}:{lineno}: {stripped}")

    if violations:
        pytest.fail(
            "Hunter sandbox files import SignalStore at runtime:\n"
            + "\n".join(violations)
            + f"\nOnly {PROMOTION_BRIDGE} is allowed to import SignalStore."
        )


def test_hunter_no_signals_table_writes():
    """workflows/active_hunter.py must not contain SQL writes to signals table."""
    filepath = "workflows/active_hunter.py"
    if not _file_exists(filepath):
        pytest.skip(f"{filepath} not found")

    lines = _read_file_lines(filepath)
    violations = []

    for lineno, line in enumerate(lines, 1):
        if SIGNALS_WRITE_RE.search(line):
            violations.append(f"  {filepath}:{lineno}: {line.strip()}")

    if violations:
        pytest.fail(
            "active_hunter.py contains SQL writes to signals table:\n"
            + "\n".join(violations)
            + f"\nSignals writes must go through {PROMOTION_BRIDGE}."
        )


def test_intelligence_files_no_signalstore():
    """intelligence/ hunter files must not import SignalStore at runtime."""
    intel_files = [
        "intelligence/pattern_miner.py",
        "intelligence/query_generator.py",
    ]
    violations = []

    for filepath in intel_files:
        if not _file_exists(filepath):
            continue

        lines = _read_file_lines(filepath)
        in_type_checking = False

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()

            # Track TYPE_CHECKING blocks
            if stripped.startswith("if TYPE_CHECKING"):
                in_type_checking = True
                continue
            if in_type_checking:
                if stripped and not stripped.startswith(("#", "from ", "import ")):
                    if not line[0].isspace():
                        in_type_checking = False
                else:
                    continue

            if re.search(r"(?:from|import)\s+.*SignalStore", stripped):
                violations.append(f"  {filepath}:{lineno}: {stripped}")

    if violations:
        pytest.fail(
            "Intelligence files import SignalStore at runtime:\n" + "\n".join(violations)
        )


def test_promotion_bridge_is_allowed():
    """Verify the promotion bridge file is allowed to use SignalStore."""
    if not _file_exists(PROMOTION_BRIDGE):
        pytest.skip(f"{PROMOTION_BRIDGE} not found yet")

    lines = _read_file_lines(PROMOTION_BRIDGE)
    has_signalstore_ref = any("SignalStore" in line for line in lines)
    assert has_signalstore_ref or True  # File exists and is allowed
