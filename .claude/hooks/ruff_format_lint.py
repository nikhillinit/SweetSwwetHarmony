#!/usr/bin/env python3
"""PostToolUse hook -- ruff format/lint for edited Python files (port of ruff_format_lint.ps1).

Fires after Edit/Write. Acts ONLY on *.py files. Designed to add linter visibility
WITHOUT forcing reformat churn on legacy files the first time they're touched.

Modes (env HARMONIC_RUFF_MODE):
  advisory (default) -- report what ruff WOULD change/flag; never edits, never blocks.
  fix                -- apply `ruff format` + `ruff check --fix` to the edited file.

Escalation (env HARMONIC_RUFF_BLOCK=1):
  In advisory mode, surface findings as a blocking PostToolUse error (exit 2) that
  Claude must address, instead of a non-blocking breadcrumb.

Skips silently if ruff is not on PATH (so it's a no-op in environments without ruff).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def ruff(exe: str, *args: str) -> tuple[int, str]:
    res = subprocess.run([exe, *args], capture_output=True, text=True, timeout=120)
    return res.returncode, (res.stdout + res.stderr)


def main() -> int:
    raw = sys.stdin.read()
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in (
        "Edit",
        "Write",
    ):
        return 0
    tool_input = payload.get("tool_input") or {}
    file = (
        str(tool_input.get("file_path") or "") if isinstance(tool_input, dict) else ""
    )
    if not file or not file.endswith(".py") or not Path(file).is_file():
        return 0

    exe = shutil.which("ruff")
    if not exe:
        return 0  # ruff absent -> no-op

    mode = (os.environ.get("HARMONIC_RUFF_MODE") or "advisory").lower()
    block = os.environ.get("HARMONIC_RUFF_BLOCK") == "1"

    if mode == "fix":
        _, fmt_out = ruff(exe, "format", file)
        _, chk_out = ruff(exe, "check", "--fix", file)
        parts = [
            f"[ruff] {file}",
            "reformatted" if "reformatted" in fmt_out else "format-clean",
        ]
        m = re.search(r"(\d+)\s+fixed", chk_out)
        if m:
            parts.append(f"fixed {m.group(1)} lint issue(s)")
        sys.stderr.write(" | ".join(parts) + "\n")
        return 0

    # --- advisory mode: report only, no edits ---
    fmt_exit, _ = ruff(exe, "format", "--check", file)
    lint_exit, lint_out = ruff(exe, "check", file)
    if fmt_exit == 0 and lint_exit == 0:
        return 0  # clean

    lines = [f"[ruff advisory] {file}  (set HARMONIC_RUFF_MODE=fix to auto-apply)"]
    if fmt_exit != 0:
        lines.append("  - formatting: 'ruff format' would reformat this file")
    if lint_exit != 0:
        lines.append("  - lint:")
        count = 0
        for line in lint_out.splitlines():
            if not line.strip():
                continue
            if count >= 20:
                lines.append(f'      ... (truncated; run: ruff check "{file}")')
                break
            lines.append(f"      {line}")
            count += 1
    sys.stderr.write("\n".join(lines) + "\n")
    return 2 if block else 0  # blocking escalation vs advisory (visible, non-blocking)


if __name__ == "__main__":
    sys.exit(main())
