"""Identity/canonical governance lint — deterministic ratchet.

Scans tracked Python files for governance anti-patterns:

  RULE0  Parse-degraded file (syntax/tokenization) — actionable violation, not exit 2
  RULE1  Direct SQL on protected Phase G identity tables outside stores
  RULE2  Direct manual short-hash (sha256[:16]) IDs outside canonical stores
  RULE3  Duplicate identity table creation outside migration authority

Usage:
  python scripts/lint_identity_patterns.py --check --baseline scripts/identity_lint_baseline.json --root .
  python scripts/lint_identity_patterns.py --write-baseline scripts/identity_lint_baseline.json

  # Optional: control parse-degraded enforcement (RULE0)
  python scripts/lint_identity_patterns.py --check --fail-on-parse-degraded true
  python scripts/lint_identity_patterns.py --write-baseline out.json --fail-on-parse-degraded false

Exit codes: 0 clean, 1 violations, 2 runtime/config error.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tokenize
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
MAX_SCHEMA_VERSION = 1

# Regex safety cap. If a logical chunk is extremely large, match only bounded
# windows to prevent pathological runtime while still catching most real
# patterns.
MAX_CHUNK_LEN_FOR_REGEX = 65536

# ── Policy constants ────────────────────────────────────────────────

PHASE_G_TABLES = frozenset({
    "entity_aliases", "entity_migrations", "entity_key_aliases",
    "entity_blocking_index", "claim_facts",
})

SQL_STRONG_KEYWORDS = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE",
    "CREATE", "DROP", "ALTER",
})

RULE1_ALLOWLIST = frozenset({
    "storage/entity_identity_store.py",
    "storage/claim_fact_store.py",
    "storage/signal_store.py",
    "storage/merge_rollback.py",
})

RULE2_ALLOWLIST = frozenset({
    "storage/entity_identity_store.py",
    "utils/canonical_keys.py",
    "utils/canonical_key_v2.py",
})

RULE3_TABLE_NAMES = frozenset({
    "entity_aliases", "entity_migrations", "entity_key_aliases",
    "entity_blocking_index", "claim_facts",
    "entity_dedup", "company_merges", "name_aliases",
})

RULE3_ALLOWLIST = frozenset({
    "storage/signal_store.py",
})

# Parse-degraded files fail CI by default (RULE0). Allowlisting is acceptable
# only as short-lived, explicitly owned debt.
PARSE_DEGRADED_ALLOWLIST: dict[str, dict[str, str]] = {
    # "path/to/file.py": {"owner": "team", "remove_by": "YYYY-MM-DD", "reason": "..."}
}

SELF_EXCLUDE = frozenset({
    "scripts/lint_identity_patterns.py",
})

WALK_EXCLUDE_DIRS = frozenset({
    "tests", ".git", ".worktrees", ".worktree_notion", ".venv", "venv",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    "dist", "build", "quality_ops_skills_and_scripts",
})

# ── Detection patterns ─────────────────────────────────────────────

SHA256_SHORT_RE = re.compile(
    r"hashlib\.sha256\(.+?\)\.hexdigest\(\)\s*\[\s*:16\s*\]"
)


def _chunk_views_for_regex(text: str) -> list[str]:
    """Return one or two bounded views of a chunk for regex matching.

    - If len(text) <= MAX_CHUNK_LEN_FOR_REGEX: scan full text.
    - If len(text) <= 2*MAX_CHUNK_LEN_FOR_REGEX: scan full text (still bounded).
    - Else: scan prefix and suffix windows (non-overlapping).

    This is a reliability guard: it prevents catastrophic regex runtime on
    enormous strings while still catching the most common cases.
    """
    if len(text) <= MAX_CHUNK_LEN_FOR_REGEX:
        return [text]
    if len(text) <= 2 * MAX_CHUNK_LEN_FOR_REGEX:
        return [text]
    return [text[:MAX_CHUNK_LEN_FOR_REGEX], text[-MAX_CHUNK_LEN_FOR_REGEX:]]


def _build_rule3_re() -> re.Pattern:
    """Build regex for CREATE TABLE on protected identity table names."""
    names = "|".join(re.escape(n) for n in sorted(RULE3_TABLE_NAMES))
    return re.compile(
        r"CREATE\s+TABLE\s+"
        r"(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
        r'(?:"|`|\[)?'
        rf"(?P<table>{names})"
        r'(?:"|`|\])?'
        r"(?:\s|\(|$)",
        re.IGNORECASE,
    )


RULE3_RE = _build_rule3_re()


def _build_table_boundary_re(table: str) -> re.Pattern:
    """Boundary-safe match for a table name (supports quoted/schema-qualified)."""
    return re.compile(
        r"(?:^|[\s,.(\"'`\[])"
        + re.escape(table)
        + r"(?:$|[\s,.)\"'`\]])",
        re.IGNORECASE,
    )


# Precompile Rule 1 keyword and table patterns (avoid per-chunk recompilation).
SQL_KEYWORD_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(r"(?<![A-Za-z_])" + kw + r"(?![A-Za-z_])", re.IGNORECASE)
    for kw in sorted(SQL_STRONG_KEYWORDS)
)

PHASE_G_TABLE_PATTERNS: dict[str, re.Pattern] = {
    t: _build_table_boundary_re(t) for t in sorted(PHASE_G_TABLES)
}


# ── AST docstring extraction ───────────────────────────────────────

def _get_docstring_ranges(source: str) -> list[tuple[int, int]]:
    """Return (start_line, end_line) 1-indexed ranges for all docstrings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise
    ranges = []
    _collect_docstring_ranges(tree, ranges)
    return ranges


def _collect_docstring_ranges(node: ast.AST, ranges: list[tuple[int, int]]) -> None:
    """Recursively collect docstring line ranges from an AST."""
    # Check if this node has a docstring
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, (ast.Constant, ast.Str))
        ):
            expr_node = body[0]
            ranges.append((expr_node.lineno, expr_node.end_lineno or expr_node.lineno))

    # Recurse into child nodes
    for child in ast.iter_child_nodes(node):
        _collect_docstring_ranges(child, ranges)


def _is_in_docstring(line: int, docstring_ranges: list[tuple[int, int]]) -> bool:
    """Check if a 1-indexed line falls within any docstring range."""
    for start, end in docstring_ranges:
        if start <= line <= end:
            return True
    return False


# ── Chunk normalization ────────────────────────────────────────────

def _build_logical_chunks(lines: list[str], docstring_ranges: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Build logical chunks from source lines.

    Joins explicit backslash continuations and implicit continuations
    (open parens/brackets/braces). Strips comments and excludes docstring lines.

    Returns list of (start_line_1indexed, normalized_chunk_text).
    """
    chunks: list[tuple[int, str]] = []
    current_parts: list[str] = []
    chunk_start = 0
    paren_depth = 0

    for i, raw_line in enumerate(lines, 1):
        # Skip docstring lines
        if _is_in_docstring(i, docstring_ranges):
            if current_parts and paren_depth == 0:
                chunks.append((chunk_start, " ".join(current_parts)))
                current_parts = []
            continue

        # Strip inline comment (naive but sufficient for governance lint)
        line = _strip_comment(raw_line)
        stripped = line.rstrip()

        if not stripped:
            if current_parts and paren_depth == 0:
                chunks.append((chunk_start, " ".join(current_parts)))
                current_parts = []
            continue

        if not current_parts:
            chunk_start = i

        # Track paren/bracket/brace depth for implicit continuation
        for ch in stripped:
            if ch in "([{":
                paren_depth += 1
            elif ch in ")]}":
                paren_depth = max(0, paren_depth - 1)

        # Handle explicit backslash continuation
        if stripped.endswith("\\"):
            current_parts.append(stripped[:-1].rstrip())
            continue

        current_parts.append(stripped)

        if paren_depth == 0:
            chunks.append((chunk_start, " ".join(current_parts)))
            current_parts = []

    # Flush remaining
    if current_parts:
        chunks.append((chunk_start, " ".join(current_parts)))

    return chunks


def _strip_comment(line: str) -> str:
    """Strip trailing # comment from a line, respecting string literals."""
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            i += 2
            continue
        if ch == "'" and not in_double:
            # Check for triple quote
            if line[i : i + 3] == "'''":
                # Skip triple-quoted string
                end = line.find("'''", i + 3)
                if end != -1:
                    i = end + 3
                    continue
                else:
                    return line  # unclosed, return as-is
            in_single = not in_single
        elif ch == '"' and not in_single:
            if line[i : i + 3] == '"""':
                end = line.find('"""', i + 3)
                if end != -1:
                    i = end + 3
                    continue
                else:
                    return line
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
        i += 1
    return line


# ── Rule detection ─────────────────────────────────────────────────

class Violation:
    __slots__ = ("rule_id", "file", "line", "snippet", "fix_hint")

    def __init__(self, rule_id: str, file: str, line: int, snippet: str, fix_hint: str):
        self.rule_id = rule_id
        self.file = file
        self.line = line
        self.snippet = snippet
        self.fix_hint = fix_hint

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet,
            "fix_hint": self.fix_hint,
        }


RULE0_HINT = (
    "This file could not be parsed by the lint tool's interpreter. "
    "Fix the syntax/tokenization issue (or temporarily allowlist with owner + removal date)."
)

RULE1_HINT = (
    "Do not issue direct SQL against protected identity tables here. "
    "Use canonical store APIs or move logic to allowlisted store module via reviewed change."
)
RULE2_HINT = (
    "Do not mint manual 16-char sha256 IDs here. "
    "Use canonical key/store APIs; propose helper abstraction in follow-up if needed."
)
RULE3_HINT = (
    "Do not create identity tables outside migration authority. "
    "Table creation is restricted to allowlisted migration module(s)."
)


def _detect_rule1(
    chunks: list[tuple[int, str]],
    rel_path: str,
) -> tuple[list[Violation], int]:
    """Detect Rule 1 violations: direct SQL on protected Phase G tables.

    Returns (violations, count) where count is the number of unique
    (chunk, table) pairs found.
    """
    violations: list[Violation] = []
    count = 0

    for chunk_start, chunk_text in chunks:
        views = _chunk_views_for_regex(chunk_text)

        # Must contain at least one strong SQL keyword
        has_keyword = any(
            pat.search(view)
            for view in views
            for pat in SQL_KEYWORD_PATTERNS
        )
        if not has_keyword:
            continue

        # Check each protected table
        tables_found: set[str] = set()
        for table, pattern in PHASE_G_TABLE_PATTERNS.items():
            if any(pattern.search(view) for view in views):
                tables_found.add(table)

        for table in sorted(tables_found):
            count += 1
            snippet = _collapse_snippet(chunk_text)
            violations.append(Violation("RULE1", rel_path, chunk_start, snippet, RULE1_HINT))

    return violations, count


def _detect_rule2(
    chunks: list[tuple[int, str]],
    rel_path: str,
) -> tuple[list[Violation], int]:
    """Detect Rule 2: direct manual short-hash pattern."""
    violations: list[Violation] = []
    count = 0

    for chunk_start, chunk_text in chunks:
        views = _chunk_views_for_regex(chunk_text)
        match_count = sum(len(SHA256_SHORT_RE.findall(view)) for view in views)
        if match_count:
            count += match_count
            snippet = _collapse_snippet(chunk_text)
            violations.append(Violation("RULE2", rel_path, chunk_start, snippet, RULE2_HINT))

    return violations, count


def _detect_rule3(
    chunks: list[tuple[int, str]],
    rel_path: str,
) -> list[Violation]:
    """Detect Rule 3: duplicate identity table creation."""
    violations: list[Violation] = []

    for chunk_start, chunk_text in chunks:
        views = _chunk_views_for_regex(chunk_text)
        if any(RULE3_RE.search(view) for view in views):
            snippet = _collapse_snippet(chunk_text)
            violations.append(Violation("RULE3", rel_path, chunk_start, snippet, RULE3_HINT))

    return violations


def _collapse_snippet(text: str, max_len: int = 120) -> str:
    """Collapse whitespace and trim to max_len."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) > max_len:
        return collapsed[: max_len - 3] + "..."
    return collapsed


# ── File scanning ──────────────────────────────────────────────────

def _git_tracked_files(root: str) -> list[str]:
    """Get tracked .py files via git ls-files."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        capture_output=True,
        cwd=root,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git ls-files failed (exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    raw = result.stdout.decode(errors="replace")
    paths = [p for p in raw.split("\0") if p]
    return paths


def _walk_fallback(root: str) -> list[str]:
    """Controlled os.walk fallback for --write-baseline when git unavailable."""
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        # Prune excluded directories
        dirnames[:] = [
            d for d in dirnames
            if d not in WALK_EXCLUDE_DIRS and not d.startswith(".")
        ]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
            paths.append(rel_path)
    return sorted(paths)


def _filter_paths(paths: list[str]) -> list[str]:
    """Filter out excluded directories and self-exclude paths."""
    filtered = []
    for p in paths:
        norm = p.replace("\\", "/")
        # Skip self
        if norm in SELF_EXCLUDE:
            continue
        # Skip excluded directories
        parts = norm.split("/")
        if any(part in WALK_EXCLUDE_DIRS for part in parts):
            continue
        filtered.append(norm)
    return filtered


def _resolve_root(args_root: str | None) -> str:
    """Resolve repository root directory."""
    if args_root:
        return os.path.abspath(args_root)
    # Try git
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            cwd=os.getcwd(),
        )
        if result.returncode == 0:
            return result.stdout.decode(errors="replace").strip()
    except FileNotFoundError:
        pass
    # Fallback from script location
    return str(Path(__file__).resolve().parent.parent)


def _get_commit_hash(root: str) -> str:
    """Get current git commit hash, or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            cwd=root,
        )
        if result.returncode == 0:
            return result.stdout.decode().strip()[:7]
    except FileNotFoundError:
        pass
    return "unknown"


# ── Baseline I/O ───────────────────────────────────────────────────

def _load_baseline(path: str) -> dict[str, Any]:
    """Load and validate baseline JSON."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    sv = data.get("schema_version")
    if not isinstance(sv, int) or sv > MAX_SCHEMA_VERSION:
        raise ValueError(
            f"Baseline schema_version {sv!r} exceeds MAX_SCHEMA_VERSION {MAX_SCHEMA_VERSION}"
        )

    # Validate rule2 invariants
    r2 = data.get("rule2_sha256_baseline", {})
    files = r2.get("files", {})
    if r2.get("total_files") != len(files):
        raise ValueError("Baseline rule2_sha256_baseline.total_files != len(files)")
    if r2.get("total_occurrences") != sum(files.values()):
        raise ValueError("Baseline rule2_sha256_baseline.total_occurrences != sum(files)")

    return data


def _write_baseline(path: str, data: dict[str, Any]) -> None:
    """Write baseline JSON."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")


# ── Main scan logic ────────────────────────────────────────────────

def scan_files(
    root: str,
    paths: list[str],
    baseline: dict[str, Any] | None,
    verbose: bool = False,
    fail_on_parse_degraded: bool = True,
) -> tuple[list[Violation], dict[str, Any]]:
    """Scan files and return (violations, scan_data).

    scan_data contains the raw counts needed for baseline generation
    and ratchet checking.
    """
    all_violations: list[Violation] = []
    rule2_violations: list[Violation] = []  # collected separately for ratchet
    rule1_counts: dict[str, int] = {}  # non-allowlisted file -> count
    rule2_counts: dict[str, int] = {}  # non-allowlisted file -> count
    rule1_allowlisted_counts: dict[str, int] = {}
    rule2_allowlisted_counts: dict[str, int] = {}
    parse_degraded_files: list[str] = []
    parse_degraded_errors: dict[str, str] = {}

    for rel_path in paths:
        abs_path = os.path.join(root, rel_path.replace("/", os.sep))
        try:
            with tokenize.open(abs_path) as f:
                source = f.read()
        except (OSError, SyntaxError):
            # File read/encoding errors are non-fatal for scan; let AST error handle
            source = None

        if source is None:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
            except OSError:
                continue

        # Parse AST docstrings. If parsing fails, continue in a degraded mode.
        parse_degraded = False
        parse_error_str: str | None = None
        try:
            docstring_ranges = _get_docstring_ranges(source)
        except SyntaxError as exc:
            parse_degraded = True
            docstring_ranges = []
            msg = getattr(exc, "msg", str(exc))
            lineno = getattr(exc, "lineno", None)
            parse_error_str = f"SyntaxError: {msg}" + (f" (line {lineno})" if lineno else "")
        except Exception as exc:
            parse_degraded = True
            docstring_ranges = []
            parse_error_str = f"ParseError: {type(exc).__name__}: {exc}"

        lines = source.splitlines()
        chunks = _build_logical_chunks(lines, docstring_ranges)

        # RULE0: parse-degraded files (actionable violation; exit 1, not exit 2).
        if parse_degraded:
            parse_degraded_files.append(rel_path)
            if parse_error_str:
                parse_degraded_errors[rel_path] = parse_error_str

            allowlisted = rel_path in PARSE_DEGRADED_ALLOWLIST
            if fail_on_parse_degraded and not allowlisted:
                snippet = parse_error_str or "File could not be parsed."
                all_violations.append(Violation("RULE0", rel_path, 1, snippet, RULE0_HINT))
            elif allowlisted and verbose:
                meta = PARSE_DEGRADED_ALLOWLIST.get(rel_path, {})
                owner = meta.get("owner", "unknown")
                remove_by = meta.get("remove_by", "unknown")
                print(
                    f"[verbose] RULE0 allowlisted parse-degraded: {rel_path} "
                    f"owner={owner} remove_by={remove_by} (suppressed)",
                    file=sys.stderr,
                )

        # ── Rule 1 ──
        r1_suppressed = False
        if rel_path in RULE1_ALLOWLIST:
            _, count = _detect_rule1(chunks, rel_path)
            if count > 0:
                rule1_allowlisted_counts[rel_path] = count
        else:
            r1_viols, count = _detect_rule1(chunks, rel_path)
            if count > 0:
                rule1_counts[rel_path] = count
                # Check grandfathering
                if baseline:
                    gf = baseline.get("rule1_grandfathered", {})
                    if rel_path in gf:
                        allowed = gf[rel_path].get("count", 0)
                        if count <= allowed:
                            r1_suppressed = True
                            if verbose:
                                print(
                                    f"[verbose] RULE1 grandfathered: {rel_path} "
                                    f"findings={count} <= baseline={allowed} (suppressed)",
                                    file=sys.stderr,
                                )
                if not r1_suppressed:
                    all_violations.extend(r1_viols)

        # ── Rule 2 ──
        if rel_path in RULE2_ALLOWLIST:
            _, count = _detect_rule2(chunks, rel_path)
            if count > 0:
                rule2_allowlisted_counts[rel_path] = count
        else:
            r2_viols, count = _detect_rule2(chunks, rel_path)
            if count > 0:
                rule2_counts[rel_path] = count
                rule2_violations.extend(r2_viols)

        # ── Rule 3 ──
        if rel_path not in RULE3_ALLOWLIST:
            r3_viols = _detect_rule3(chunks, rel_path)
            all_violations.extend(r3_viols)

    # ── Rule 2 ratchet check ──
    if baseline:
        r2_baseline = baseline.get("rule2_sha256_baseline", {})
        baseline_files = r2_baseline.get("files", {})
        baseline_total = r2_baseline.get("total_occurrences", 0)
        current_total = sum(rule2_counts.values())

        # Verbose logging for files within baseline
        if verbose:
            for f, count in rule2_counts.items():
                if f in baseline_files and count <= baseline_files[f]:
                    print(
                        f"[verbose] RULE2 within baseline: {f} "
                        f"findings={count} <= baseline={baseline_files[f]} (suppressed)",
                        file=sys.stderr,
                    )

        # Determine which rule2 violations to keep
        for v in rule2_violations:
            f = v.file
            file_count = rule2_counts.get(f, 0)
            file_baseline = baseline_files.get(f, None)

            if file_baseline is None:
                # New file — always a violation
                all_violations.append(v)
            elif file_count > file_baseline:
                # Per-file increase — violation
                all_violations.append(v)
            elif current_total > baseline_total:
                # Total increased (even if this file is fine) — keep all
                all_violations.append(v)
            # else: within baseline, suppress
    else:
        # No baseline — rule2 violations are informational for --write-baseline
        pass

    # Verbose output for allowlisted files
    if verbose:
        for f, count in sorted(rule1_allowlisted_counts.items()):
            print(
                f"[verbose] RULE1 allowlisted: {f} findings={count} (suppressed)",
                file=sys.stderr,
            )
        for f, count in sorted(rule2_allowlisted_counts.items()):
            print(
                f"[verbose] RULE2 allowlisted: {f} findings={count} (suppressed)",
                file=sys.stderr,
            )

    scan_data = {
        "rule1_counts": rule1_counts,
        "rule2_counts": rule2_counts,
        "parse_degraded_files": parse_degraded_files,
        "parse_degraded_errors": parse_degraded_errors,
    }
    return all_violations, scan_data


# ── Output formatting ──────────────────────────────────────────────

def _format_text(violations: list[Violation]) -> str:
    """Format violations as human-readable text."""
    if not violations:
        return "All identity lint rules passed.\n"

    sorted_v = sorted(violations, key=lambda v: (v.rule_id, v.file, v.line))
    lines = [f"Found {len(sorted_v)} violation(s):\n"]
    for v in sorted_v:
        lines.append(f"  {v.rule_id} {v.file}:{v.line}")
        lines.append(f"    snippet: {v.snippet}")
        lines.append(f"    fix: {v.fix_hint}")
        lines.append("")
    return "\n".join(lines)


def _format_json(
    violations: list[Violation],
    exit_code: int,
    parse_degraded_files: list[str] | None = None,
) -> str:
    """Format output as JSON."""
    sorted_v = sorted(violations, key=lambda v: (v.rule_id, v.file, v.line))

    rules_map: dict[str, dict] = {}
    for v in sorted_v:
        if v.rule_id not in rules_map:
            rules_map[v.rule_id] = {
                "rule_id": v.rule_id,
                "status": "fail",
                "violations": [],
            }
        rules_map[v.rule_id]["violations"].append(v.to_dict())

    # Add passing rules
    for rid in ("RULE0", "RULE1", "RULE2", "RULE3"):
        if rid not in rules_map:
            rules_map[rid] = {
                "rule_id": rid,
                "status": "pass",
                "violations": [],
            }

    output = {
        "exit_code": exit_code,
        "tool_version": TOOL_VERSION,
        "parse_degraded_files": sorted(parse_degraded_files or []),
        "rules": [rules_map[k] for k in sorted(rules_map)],
    }
    return json.dumps(output, indent=2)


# ── Main entrypoint ───────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Identity/canonical governance lint ratchet."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Strict CI mode (default).",
    )
    parser.add_argument(
        "--write-baseline",
        metavar="PATH",
        help="Generate/refresh baseline JSON.",
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help="Path to baseline JSON (default: <root>/scripts/identity_lint_baseline.json).",
    )
    parser.add_argument("--root", metavar="PATH", help="Repository root.")
    parser.add_argument("--verbose", action="store_true", help="Show suppressed counts.")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--fail-on-parse-degraded",
        choices=("true", "false"),
        help=(
            "Whether parse-degraded files fail as violations (RULE0). "
            "Defaults: true in --check mode, false in --write-baseline mode."
        ),
    )
    args = parser.parse_args(argv)

    is_write_mode = args.write_baseline is not None

    # Determine parse-degraded enforcement.
    if args.fail_on_parse_degraded is None:
        fail_on_parse_degraded = not is_write_mode
    else:
        fail_on_parse_degraded = args.fail_on_parse_degraded == "true"

    # Resolve root
    root = _resolve_root(args.root)
    if not os.path.isdir(root):
        print(f"Error: root directory not found: {root}", file=sys.stderr)
        return 2

    # Enumerate files
    try:
        raw_paths = _git_tracked_files(root)
    except (RuntimeError, FileNotFoundError) as e:
        if is_write_mode:
            print(f"Warning: git unavailable ({e}), using fallback walk.", file=sys.stderr)
            raw_paths = _walk_fallback(root)
        else:
            print(f"Error: git enumeration failed in --check mode: {e}", file=sys.stderr)
            return 2

    paths = _filter_paths(raw_paths)

    # Load baseline
    baseline: dict[str, Any] | None = None
    if not is_write_mode:
        baseline_path = args.baseline
        if not baseline_path:
            baseline_path = os.path.join(root, "scripts", "identity_lint_baseline.json")
        if not os.path.exists(baseline_path):
            print(f"Error: baseline not found: {baseline_path}", file=sys.stderr)
            return 2
        try:
            baseline = _load_baseline(baseline_path)
        except (json.JSONDecodeError, ValueError, OSError) as e:
            print(f"Error: invalid baseline: {e}", file=sys.stderr)
            return 2

    # Scan
    violations, scan_data = scan_files(
        root,
        paths,
        baseline,
        verbose=args.verbose,
        fail_on_parse_degraded=fail_on_parse_degraded,
    )

    # Write baseline mode
    if is_write_mode:
        # Baseline generation should be resilient: don't fail on per-file parse
        # issues by default, but surface them loudly.
        pd_files = scan_data.get("parse_degraded_files", [])
        if pd_files:
            print(
                f"Warning: encountered {len(pd_files)} parse-degraded file(s) while writing baseline.",
                file=sys.stderr,
            )
            if args.verbose:
                errors = scan_data.get("parse_degraded_errors", {})
                for f in sorted(pd_files)[:20]:
                    msg = errors.get(f, "")
                    print(f"  - {f}: {msg}", file=sys.stderr)

        r1_counts = scan_data["rule1_counts"]
        r2_counts = scan_data["rule2_counts"]

        baseline_data = {
            "schema_version": 1,
            "metadata": {
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "generated_from_commit": _get_commit_hash(root),
                "tool_version": TOOL_VERSION,
                "description": "Ratchet state for identity/canonical anti-patterns.",
            },
            "rule1_grandfathered": {
                f: {"count": c, "reason": "pre-existing"}
                for f, c in sorted(r1_counts.items())
            },
            "rule2_sha256_baseline": {
                "total_files": len(r2_counts),
                "total_occurrences": sum(r2_counts.values()),
                "files": dict(sorted(r2_counts.items())),
            },
        }
        _write_baseline(args.write_baseline, baseline_data)
        print(f"Baseline written to {args.write_baseline}")
        print(f"  Rule 1 grandfathered: {len(r1_counts)} file(s)")
        print(f"  Rule 2 baseline: {len(r2_counts)} file(s), {sum(r2_counts.values())} occurrence(s)")
        return 0

    # Check mode - report violations
    exit_code = 1 if violations else 0
    if args.format == "json":
        print(_format_json(violations, exit_code, scan_data.get("parse_degraded_files")))
    else:
        print(_format_text(violations))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
