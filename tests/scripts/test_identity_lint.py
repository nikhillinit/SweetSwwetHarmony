"""Tests for scripts/lint_identity_patterns.py — identity/canonical governance lint.

All tests use inline synthetic Python files written to tmp_path (hermetic).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

# Ensure the scripts directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from lint_identity_patterns import (
    PHASE_G_TABLES,
    RULE1_ALLOWLIST,
    RULE2_ALLOWLIST,
    RULE3_ALLOWLIST,
    RULE3_TABLE_NAMES,
    SELF_EXCLUDE,
    SHA256_SHORT_RE,
    TOOL_VERSION,
    Violation,
    _build_logical_chunks,
    _collapse_snippet,
    _detect_rule1,
    _detect_rule2,
    _detect_rule3,
    _filter_paths,
    _get_docstring_ranges,
    _load_baseline,
    _write_baseline,
    main,
    scan_files,
)


# ── Helpers ────────────────────────────────────────────────────────

def _write_py(tmp_path, rel_path: str, content: str) -> str:
    """Write a synthetic Python file and return its relative path."""
    full = tmp_path / rel_path.replace("/", os.sep)
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return rel_path


def _make_baseline(
    tmp_path,
    rule1_gf: dict | None = None,
    rule2_files: dict | None = None,
) -> str:
    """Create a baseline JSON file and return its path."""
    r2_files = rule2_files or {}
    data = {
        "schema_version": 1,
        "metadata": {
            "generated_at": "2026-01-01T00:00:00Z",
            "generated_from_commit": "test000",
            "tool_version": TOOL_VERSION,
            "description": "test baseline",
        },
        "rule1_grandfathered": rule1_gf or {},
        "rule2_sha256_baseline": {
            "total_files": len(r2_files),
            "total_occurrences": sum(r2_files.values()),
            "files": r2_files,
        },
    }
    path = str(tmp_path / "baseline.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def _scan_single(tmp_path, rel_path: str, content: str, baseline=None):
    """Write a file and scan it, returning violations."""
    _write_py(tmp_path, rel_path, content)
    root = str(tmp_path)
    violations, scan_data = scan_files(root, [rel_path], baseline)
    return violations, scan_data


def _chunks_from_source(source: str):
    """Parse source and return logical chunks."""
    docstring_ranges = _get_docstring_ranges(source)
    lines = source.splitlines()
    return _build_logical_chunks(lines, docstring_ranges)


# ═══════════════════════════════════════════════════════════════════
# RULE 1 Tests
# ═══════════════════════════════════════════════════════════════════


def test_rule1_catches_direct_sql(tmp_path):
    """Test 1: Rule 1 catches direct SQL on protected tables."""
    code = '''
import sqlite3
conn = sqlite3.connect("test.db")
conn.execute("SELECT * FROM entity_aliases WHERE id = 1")
'''
    violations, _ = _scan_single(tmp_path, "some_module.py", code)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) >= 1
    assert "entity_aliases" in rule1[0].snippet.lower() or "entity_aliases" in rule1[0].snippet


def test_rule1_catches_multiline_sql(tmp_path):
    """Test 2: Rule 1 catches multiline SQL with continuations."""
    code = '''
query = (
    "INSERT INTO "
    "entity_migrations "
    "(from_id, to_id) VALUES (?, ?)"
)
'''
    violations, _ = _scan_single(tmp_path, "some_module.py", code)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) >= 1


def test_rule1_catches_mixed_case(tmp_path):
    """Test 3: Rule 1 catches mixed-case SQL keywords."""
    code = '''
cursor.execute("select * from Entity_Aliases")
'''
    violations, _ = _scan_single(tmp_path, "some_module.py", code)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) >= 1


def test_rule1_skips_comments(tmp_path):
    """Test 4: Rule 1 ignores SQL in comments."""
    code = '''
# conn.execute("SELECT * FROM entity_aliases")
x = 1
'''
    violations, _ = _scan_single(tmp_path, "some_module.py", code)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) == 0


def test_rule1_skips_ast_docstrings(tmp_path):
    """Test 5: Rule 1 ignores SQL in docstrings."""
    code = '''
def my_func():
    """SELECT * FROM entity_aliases WHERE id = 1"""
    pass
'''
    violations, _ = _scan_single(tmp_path, "some_module.py", code)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) == 0


def test_rule1_catches_triple_quoted_sql(tmp_path):
    """Test 6: Rule 1 catches SQL in triple-quoted non-docstring strings."""
    code = '''
x = 1
query = """SELECT * FROM entity_aliases WHERE id = ?"""
cursor.execute(query)
'''
    violations, _ = _scan_single(tmp_path, "some_module.py", code)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) >= 1


def test_rule1_allowlist_exempt(tmp_path):
    """Test 7: Rule 1 allowlisted files are exempt."""
    code = '''
conn.execute("DELETE FROM entity_aliases WHERE id = ?", (entity_id,))
'''
    # Use an allowlisted path
    for allowlisted_path in RULE1_ALLOWLIST:
        _write_py(tmp_path, allowlisted_path, code)
        violations, _ = scan_files(str(tmp_path), [allowlisted_path], None)
        rule1 = [v for v in violations if v.rule_id == "RULE1"]
        assert len(rule1) == 0, f"Allowlisted file {allowlisted_path} should be exempt"
        break  # Just test one


def test_rule1_grandfathered_within_count(tmp_path):
    """Test 8: Rule 1 grandfathered file within count passes."""
    code = '''
conn.execute("SELECT * FROM entity_aliases WHERE id = ?", (1,))
conn.execute("INSERT INTO entity_migrations (from_id) VALUES (?)", (2,))
'''
    rel = "legacy_module.py"
    _write_py(tmp_path, rel, code)
    baseline_path = _make_baseline(
        tmp_path,
        rule1_gf={rel: {"count": 2, "reason": "pre-existing"}},
    )
    baseline = _load_baseline(baseline_path)
    violations, _ = scan_files(str(tmp_path), [rel], baseline)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) == 0


def test_rule1_grandfathered_exceeds_count(tmp_path):
    """Test 9: Rule 1 grandfathered file exceeding count fails."""
    code = '''
conn.execute("SELECT * FROM entity_aliases WHERE id = ?", (1,))
conn.execute("INSERT INTO entity_migrations (from_id) VALUES (?)", (2,))
conn.execute("DELETE FROM claim_facts WHERE id = ?", (3,))
'''
    rel = "legacy_module.py"
    _write_py(tmp_path, rel, code)
    baseline_path = _make_baseline(
        tmp_path,
        rule1_gf={rel: {"count": 1, "reason": "pre-existing"}},
    )
    baseline = _load_baseline(baseline_path)
    violations, _ = scan_files(str(tmp_path), [rel], baseline)
    rule1 = [v for v in violations if v.rule_id == "RULE1"]
    assert len(rule1) >= 1


# ═══════════════════════════════════════════════════════════════════
# RULE 2 Tests
# ═══════════════════════════════════════════════════════════════════


def test_rule2_catches_new_file(tmp_path):
    """Test 10: Rule 2 catches sha256 pattern in a file not in baseline."""
    code = '''
import hashlib
entity_id = hashlib.sha256(name.encode()).hexdigest()[:16]
'''
    rel = "new_module.py"
    _write_py(tmp_path, rel, code)
    baseline_path = _make_baseline(tmp_path, rule2_files={})
    baseline = _load_baseline(baseline_path)
    violations, _ = scan_files(str(tmp_path), [rel], baseline)
    rule2 = [v for v in violations if v.rule_id == "RULE2"]
    assert len(rule2) >= 1


def test_rule2_catches_count_increase(tmp_path):
    """Test 11: Rule 2 catches per-file count increase."""
    code = '''
import hashlib
id1 = hashlib.sha256(a.encode()).hexdigest()[:16]
id2 = hashlib.sha256(b.encode()).hexdigest()[:16]
'''
    rel = "existing_module.py"
    _write_py(tmp_path, rel, code)
    baseline_path = _make_baseline(tmp_path, rule2_files={rel: 1})
    baseline = _load_baseline(baseline_path)
    violations, _ = scan_files(str(tmp_path), [rel], baseline)
    rule2 = [v for v in violations if v.rule_id == "RULE2"]
    assert len(rule2) >= 1


def test_rule2_allows_decrease(tmp_path):
    """Test 12: Rule 2 allows decrease in count."""
    code = '''
import hashlib
# removed one usage
'''
    rel = "existing_module.py"
    _write_py(tmp_path, rel, code)
    baseline_path = _make_baseline(tmp_path, rule2_files={rel: 2})
    baseline = _load_baseline(baseline_path)
    violations, _ = scan_files(str(tmp_path), [rel], baseline)
    rule2 = [v for v in violations if v.rule_id == "RULE2"]
    assert len(rule2) == 0


def test_rule2_allowlist_exempt(tmp_path):
    """Test 13: Rule 2 allowlisted files are exempt."""
    code = '''
import hashlib
entity_id = hashlib.sha256(name.encode()).hexdigest()[:16]
'''
    for allowlisted_path in RULE2_ALLOWLIST:
        _write_py(tmp_path, allowlisted_path, code)
        violations, _ = scan_files(str(tmp_path), [allowlisted_path], None)
        rule2 = [v for v in violations if v.rule_id == "RULE2"]
        assert len(rule2) == 0, f"Allowlisted file {allowlisted_path} should be exempt"
        break  # Just test one


# ═══════════════════════════════════════════════════════════════════
# RULE 3 Tests
# ═══════════════════════════════════════════════════════════════════


def test_rule3_catches_duplicate_table(tmp_path):
    """Test 14: Rule 3 catches CREATE TABLE for protected identity tables."""
    code = '''
conn.execute("CREATE TABLE entity_aliases (id INTEGER PRIMARY KEY)")
'''
    violations, _ = _scan_single(tmp_path, "bad_module.py", code)
    rule3 = [v for v in violations if v.rule_id == "RULE3"]
    assert len(rule3) >= 1


def test_rule3_catches_quoted_table(tmp_path):
    """Test 15: Rule 3 catches quoted table names."""
    code = '''
conn.execute('CREATE TABLE IF NOT EXISTS "entity_migrations" (id INTEGER)')
'''
    violations, _ = _scan_single(tmp_path, "bad_module.py", code)
    rule3 = [v for v in violations if v.rule_id == "RULE3"]
    assert len(rule3) >= 1


def test_rule3_catches_schematized_table(tmp_path):
    """Test 16: Rule 3 catches schema-qualified table creation."""
    code = '''
conn.execute("CREATE TABLE main.entity_key_aliases (id INTEGER PRIMARY KEY)")
'''
    violations, _ = _scan_single(tmp_path, "bad_module.py", code)
    rule3 = [v for v in violations if v.rule_id == "RULE3"]
    assert len(rule3) >= 1


def test_rule3_allowlist_exempt(tmp_path):
    """Test 17: Rule 3 allowlisted files are exempt."""
    code = '''
conn.execute("CREATE TABLE IF NOT EXISTS entity_aliases (id INTEGER)")
'''
    for allowlisted_path in RULE3_ALLOWLIST:
        _write_py(tmp_path, allowlisted_path, code)
        violations, _ = scan_files(str(tmp_path), [allowlisted_path], None)
        rule3 = [v for v in violations if v.rule_id == "RULE3"]
        assert len(rule3) == 0, f"Allowlisted file {allowlisted_path} should be exempt"
        break  # Just test one


# ═══════════════════════════════════════════════════════════════════
# Scanner Tests
# ═══════════════════════════════════════════════════════════════════


def test_scanner_git_success(tmp_path, monkeypatch):
    """Test 18: Scanner uses git ls-files when available."""
    # Create a clean file
    _write_py(tmp_path, "clean.py", "x = 1\n")

    # Mock git ls-files to return our file
    def mock_run(cmd, **kwargs):
        if cmd[0] == "git" and "ls-files" in cmd:
            result = type("R", (), {
                "returncode": 0,
                "stdout": b"clean.py\0",
                "stderr": b"",
            })()
            return result
        # For git rev-parse
        if cmd[0] == "git" and "rev-parse" in cmd:
            result = type("R", (), {
                "returncode": 0,
                "stdout": str(tmp_path).encode() + b"\n",
                "stderr": b"",
            })()
            return result
        return subprocess.run(cmd, **kwargs)

    baseline_path = _make_baseline(tmp_path)

    monkeypatch.setattr(subprocess, "run", mock_run)
    exit_code = main([
        "--check",
        "--baseline", baseline_path,
        "--root", str(tmp_path),
    ])
    assert exit_code == 0


def test_scanner_git_failure_check_mode(tmp_path, monkeypatch):
    """Test 19: --check mode exits 2 when git fails."""

    def mock_run(cmd, **kwargs):
        if cmd[0] == "git" and "ls-files" in cmd:
            result = type("R", (), {
                "returncode": 128,
                "stdout": b"",
                "stderr": b"fatal: not a git repo",
            })()
            return result
        return subprocess.run(cmd, **kwargs)

    baseline_path = _make_baseline(tmp_path)

    monkeypatch.setattr(subprocess, "run", mock_run)
    exit_code = main([
        "--check",
        "--baseline", baseline_path,
        "--root", str(tmp_path),
    ])
    assert exit_code == 2


def test_scanner_git_failure_write_mode(tmp_path, monkeypatch):
    """Test 20: --write-baseline mode falls back when git fails."""
    _write_py(tmp_path, "clean.py", "x = 1\n")

    def mock_run(cmd, **kwargs):
        if cmd[0] == "git" and "ls-files" in cmd:
            raise FileNotFoundError("git not found")
        if cmd[0] == "git" and "rev-parse" in cmd:
            raise FileNotFoundError("git not found")
        return subprocess.run(cmd, **kwargs)

    out_path = str(tmp_path / "out_baseline.json")
    monkeypatch.setattr(subprocess, "run", mock_run)
    exit_code = main([
        "--write-baseline", out_path,
        "--root", str(tmp_path),
    ])
    assert exit_code == 0
    assert os.path.exists(out_path)


def test_ast_parse_failure_path(tmp_path, monkeypatch, capsys):
    """Test 21: AST parse errors are handled per mode."""
    # Write an invalid Python file
    bad_file = tmp_path / "bad_syntax.py"
    bad_file.write_text("def foo(:\n    pass\n", encoding="utf-8")

    # ── check mode: exit 2 ──
    def mock_run_check(cmd, **kwargs):
        if cmd[0] == "git" and "ls-files" in cmd:
            result = type("R", (), {
                "returncode": 0,
                "stdout": b"bad_syntax.py\0",
                "stderr": b"",
            })()
            return result
        if cmd[0] == "git" and "rev-parse" in cmd:
            result = type("R", (), {
                "returncode": 0,
                "stdout": str(tmp_path).encode() + b"\n",
                "stderr": b"",
            })()
            return result
        return subprocess.run(cmd, **kwargs)

    baseline_path = _make_baseline(tmp_path)
    monkeypatch.setattr(subprocess, "run", mock_run_check)
    exit_code = main([
        "--check",
        "--baseline", baseline_path,
        "--root", str(tmp_path),
    ])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "bad_syntax" in captured.err


# ═══════════════════════════════════════════════════════════════════
# Baseline Tests
# ═══════════════════════════════════════════════════════════════════


def test_baseline_schema_consistent(tmp_path):
    """Test 22: Baseline schema invariants are validated."""
    # Valid baseline
    valid_path = _make_baseline(tmp_path, rule2_files={"a.py": 1, "b.py": 2})
    data = _load_baseline(valid_path)
    assert data["schema_version"] == 1

    # Invalid schema version
    bad_data = {
        "schema_version": 99,
        "metadata": {},
        "rule1_grandfathered": {},
        "rule2_sha256_baseline": {"total_files": 0, "total_occurrences": 0, "files": {}},
    }
    bad_path = str(tmp_path / "bad_version.json")
    with open(bad_path, "w") as f:
        json.dump(bad_data, f)
    with pytest.raises(ValueError, match="schema_version"):
        _load_baseline(bad_path)

    # Inconsistent total_files
    bad_data2 = {
        "schema_version": 1,
        "metadata": {},
        "rule1_grandfathered": {},
        "rule2_sha256_baseline": {"total_files": 5, "total_occurrences": 0, "files": {}},
    }
    bad_path2 = str(tmp_path / "bad_total.json")
    with open(bad_path2, "w") as f:
        json.dump(bad_data2, f)
    with pytest.raises(ValueError, match="total_files"):
        _load_baseline(bad_path2)


# ═══════════════════════════════════════════════════════════════════
# Integration Test
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.integration
def test_clean_codebase_passes():
    """Test 23: The real codebase passes lint with the checked-in baseline."""
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    baseline_path = os.path.join(repo_root, "scripts", "identity_lint_baseline.json")
    if not os.path.exists(baseline_path):
        pytest.skip("Baseline file not found (not in repo yet)")
    exit_code = main([
        "--check",
        "--baseline", baseline_path,
        "--root", repo_root,
    ])
    assert exit_code == 0, "Codebase has identity lint violations"
