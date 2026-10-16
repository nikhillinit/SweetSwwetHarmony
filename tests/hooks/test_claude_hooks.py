"""Contract tests for the cross-platform Claude Code hooks in .claude/hooks/*.py.

Each hook is a stdlib-only script driven by the Claude Code hook protocol:
JSON payload on stdin, optional JSON/text on stdout, exit code as decision.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"


def _run(
    script: str, payload, env: dict[str, str] | None = None, cwd: Path | None = None
):
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    merged = dict(os.environ)
    merged.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=merged,
        cwd=str(cwd or ROOT),
        timeout=60,
    )


# ---------------------------------------------------------------- guard_signals_db


def _ask_reason(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "ask"
    return hso["permissionDecisionReason"]


def _allowed(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    "command",
    [
        "cp backups/signals.db.pre-x signals.db",
        "cp -f x.db ./data/signals.db",
        "mv old.db signals.db-wal",
        "git reset --hard HEAD~1",
        "git clean -fdx",
        "git checkout -- signals.db",
        "git restore signals.db",
        "echo hi > signals.db",
        "cat x >> data/signals.db",
        "rm -f signals.db-wal",
        "del signals.db",
        'sqlite3 signals.db "DELETE FROM signals"',
        "sqlite3 signals.db 'DROP TABLE signals'",
        "Copy-Item .\\x.db -Destination .\\signals.db",
        "Remove-Item signals.db.checkpoint-1",
    ],
)
def test_guard_asks_on_destructive_bash(command: str) -> None:
    res = _run(
        "guard_signals_db.py", {"tool_name": "Bash", "tool_input": {"command": command}}
    )
    reason = _ask_reason(res)
    assert reason.startswith("[signals.db guard]")
    assert "/signals-db-checkpoint" in reason


def test_guard_copy_onto_protected_backup_name_asks() -> None:
    res = _run(
        "guard_signals_db.py",
        {
            "tool_name": "Bash",
            "tool_input": {"command": "cp signals.db backups/signals.db.pre-fix"},
        },
    )
    assert "DESTINATION" in _ask_reason(res)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cp signals.db /tmp/scratch.db",
        "cp signals.db backups/snapshot-2026.sqlite",
        "git clean -fd",
        "git status",
        "sqlite3 signals.db 'SELECT COUNT(*) FROM signals'",
        "python run_pipeline.py health --json",
        "rm -rf build/",
    ],
)
def test_guard_allows_benign_bash(command: str) -> None:
    _allowed(
        _run(
            "guard_signals_db.py",
            {"tool_name": "Bash", "tool_input": {"command": command}},
        )
    )


def test_guard_powershell_tool_is_also_guarded() -> None:
    res = _run(
        "guard_signals_db.py",
        {
            "tool_name": "PowerShell",
            "tool_input": {"command": "Remove-Item signals.db"},
        },
    )
    _ask_reason(res)


@pytest.mark.parametrize(
    "path",
    [
        "signals.db",
        "C:\\dev\\Harmonic\\signals.db",
        "/x/y/SIGNALS.DB-WAL",
        "a/signals.db.pre-1",
    ],
)
def test_guard_write_tool_protected_paths(path: str) -> None:
    res = _run(
        "guard_signals_db.py", {"tool_name": "Write", "tool_input": {"file_path": path}}
    )
    assert "Write tool" in _ask_reason(res)


def test_guard_write_tool_benign_path() -> None:
    _allowed(
        _run(
            "guard_signals_db.py",
            {"tool_name": "Write", "tool_input": {"file_path": "utils/foo.py"}},
        )
    )


def test_guard_filesystem_mcp_write_and_move() -> None:
    res = _run(
        "guard_signals_db.py",
        {
            "tool_name": "mcp__filesystem__write_file",
            "tool_input": {"path": "signals.db"},
        },
    )
    assert "filesystem MCP" in _ask_reason(res)
    res = _run(
        "guard_signals_db.py",
        {
            "tool_name": "mcp__filesystem__move_file",
            "tool_input": {"source": "a.db", "destination": "signals.db"},
        },
    )
    assert "moving onto" in _ask_reason(res)
    _allowed(
        _run(
            "guard_signals_db.py",
            {"tool_name": "mcp__filesystem__edit_file", "tool_input": {"path": "x.py"}},
        )
    )


@pytest.mark.parametrize(
    "payload",
    ["", "not json", "{}", json.dumps({"tool_name": "Edit", "tool_input": {}})],
)
def test_guard_never_interferes_on_bad_or_irrelevant_input(payload: str) -> None:
    _allowed(_run("guard_signals_db.py", payload))


# ---------------------------------------------------------------- inject_context


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(tmp_path: Path, branch: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", branch)
    _git(
        repo,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    return repo


def test_inject_context_emits_branch_and_rules(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "feature/x")
    res = _run(
        "inject_context.py", "{}", env={"CLAUDE_PROJECT_DIR": str(repo)}, cwd=tmp_path
    )
    assert res.returncode == 0, res.stderr
    assert "[harmonic session context]" in res.stdout
    assert "Branch: feature/x" in res.stdout
    assert "Canonical plan" not in res.stdout
    assert "Ownership rules" in res.stdout
    assert "[end context]" in res.stdout


def test_inject_context_resolves_single_canonical_plan(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "prep/q10-fallback-prep")
    (repo / "docs" / "plans" / "2026-07-14-q10-fallback").mkdir(parents=True)
    (repo / "docs" / "plans" / "2026-01-01-other").mkdir(parents=True)
    res = _run(
        "inject_context.py", "{}", env={"CLAUDE_PROJECT_DIR": str(repo)}, cwd=tmp_path
    )
    assert (
        "Canonical plan: docs/plans/2026-07-14-q10-fallback/00-strategy.md (or README.md)"
        in res.stdout
    )


def test_inject_context_flags_ambiguous_and_missing_plans(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, "release/thing")
    (repo / "docs" / "plans" / "a-thing").mkdir(parents=True)
    (repo / "docs" / "plans" / "b-thing").mkdir(parents=True)
    res = _run(
        "inject_context.py", "{}", env={"CLAUDE_PROJECT_DIR": str(repo)}, cwd=tmp_path
    )
    assert "Canonical plan: AMBIGUOUS (2 candidates match 'thing')" in res.stdout

    repo2 = _make_repo(tmp_path / "two", "prep/nothing")
    (repo2 / "docs" / "plans").mkdir(parents=True)
    res = _run(
        "inject_context.py", "{}", env={"CLAUDE_PROJECT_DIR": str(repo2)}, cwd=tmp_path
    )
    assert "Canonical plan: no docs/plans/*nothing* dir found" in res.stdout


def test_inject_context_silent_outside_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    res = _run(
        "inject_context.py", "{}", env={"CLAUDE_PROJECT_DIR": str(plain)}, cwd=plain
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""
    res = _run(
        "inject_context.py",
        "{}",
        env={"CLAUDE_PROJECT_DIR": str(tmp_path / "missing")},
        cwd=plain,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""


# ---------------------------------------------------------------- ruff_format_lint


def _ruff_path() -> Path:
    found = shutil.which("ruff") or str(Path(sys.executable).parent / "ruff")
    if not Path(found).exists():
        pytest.skip("ruff not available")
    return Path(found)


def _ruff_env(**extra: str) -> dict[str, str]:
    env = {"PATH": str(_ruff_path().parent) + os.pathsep + os.environ.get("PATH", "")}
    env.update(extra)
    return env


def _edit_payload(path: Path, tool: str = "Edit") -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": str(path)}}


def test_ruff_ignores_non_python_and_missing_files(tmp_path: Path) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("x\n")
    res = _run("ruff_format_lint.py", _edit_payload(txt), env=_ruff_env())
    assert (res.returncode, res.stderr) == (0, "")
    res = _run(
        "ruff_format_lint.py", _edit_payload(tmp_path / "gone.py"), env=_ruff_env()
    )
    assert (res.returncode, res.stderr) == (0, "")
    res = _run(
        "ruff_format_lint.py",
        {"tool_name": "Bash", "tool_input": {"command": "x"}},
        env=_ruff_env(),
    )
    assert (res.returncode, res.stderr) == (0, "")


def test_ruff_noop_when_ruff_absent(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n")
    res = _run("ruff_format_lint.py", _edit_payload(bad), env={"PATH": str(tmp_path)})
    assert (res.returncode, res.stderr) == (0, "")


def test_ruff_advisory_reports_without_editing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    src = "import os\nx=1\n"
    bad.write_text(src)
    res = _run("ruff_format_lint.py", _edit_payload(bad, "Write"), env=_ruff_env())
    assert res.returncode == 0
    assert res.stderr.startswith(f"[ruff advisory] {bad}")
    assert "HARMONIC_RUFF_MODE=fix" in res.stderr
    assert "- formatting:" in res.stderr
    assert "- lint:" in res.stderr
    assert "F401" in res.stderr
    assert bad.read_text() == src


def test_ruff_advisory_block_escalates_exit_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import os\n")
    res = _run(
        "ruff_format_lint.py",
        _edit_payload(bad),
        env=_ruff_env(HARMONIC_RUFF_BLOCK="1"),
    )
    assert res.returncode == 2
    assert "[ruff advisory]" in res.stderr


def test_ruff_fix_mode_rewrites_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import os\nx=1\n")
    res = _run(
        "ruff_format_lint.py",
        _edit_payload(bad),
        env=_ruff_env(HARMONIC_RUFF_MODE="FIX"),
    )
    assert res.returncode == 0
    assert res.stderr.startswith(f"[ruff] {bad} | reformatted")
    assert "fixed 1 lint issue(s)" in res.stderr
    assert bad.read_text().strip() == "x = 1"  # import removed, assignment formatted


def test_ruff_clean_file_is_silent(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("x = 1\n")
    res = _run(
        "ruff_format_lint.py",
        _edit_payload(good),
        env=_ruff_env(HARMONIC_RUFF_BLOCK="1"),
    )
    assert (res.returncode, res.stderr) == (0, "")
