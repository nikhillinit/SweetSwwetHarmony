#!/usr/bin/env python3
"""PreToolUse hook -- signals.db write-guard (cross-platform port of guard_signals_db.ps1).

Fires on ALL branches. Purpose: stop Claude's own tool calls from overwriting or
deleting the live signals.db or its recovery backups -- the failure mode behind the
2026-05-05 909->4-row reversion (Issue #149 / MEMORY Known Issue #10), where a
truncated backup was copied onto the live DB.

Scope & limits (deliberately honest):
  * Guards Claude's Bash / PowerShell / Write / filesystem-MCP tool calls only.
  * Does NOT guard commands the operator types directly in their own terminal
    (that was the actual incident vector). It narrows CLAUDE's blast radius.
  * Decision is "ask" (user-overridable), never a hard deny -- a false positive
    costs one keypress; a false negative could cost the corpus.

Protocol: emit PreToolUse JSON on stdout with hookSpecificOutput.permissionDecision.
  "ask"  -> user is prompted to confirm or cancel the tool call
  exit 0 with no stdout -> allow (default)
"""

from __future__ import annotations

import json
import re
import sys

LIVE_DB = {"signals.db", "signals.db-wal", "signals.db-shm"}
_OPERATOR_NAMES = re.compile(
    r"^(cp|copy|copy-item|mv|move|move-item|rm|del|erase|remove-item)$", re.I
)
_TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")


def is_protected_basename(name: str) -> bool:
    if not name:
        return False
    base = re.split(r"[\\/]", name)[-1].lower()
    return base in LIVE_DB or base.startswith(
        "signals.db."
    )  # signals.db.pre-*, .checkpoint-*, etc.


def last_operand(cmd: str) -> str | None:
    """Last path-like operand of a copy/move command = its destination."""
    cand = None
    for tok in (m.group(0).strip("\"'") for m in _TOKEN.finditer(cmd)):
        if tok.startswith("-") or _OPERATOR_NAMES.match(tok):
            continue
        cand = tok
    return cand


def check_shell(cmd: str) -> str | None:
    low = cmd.lower()
    if re.search(r"git\s+reset\s+--hard", low):
        return (
            "git reset --hard desyncs the working tree -- the 2026-05-05 incident "
            "co-occurred with a hard reset in the same shell session."
        )
    if re.search(r"git\s+clean\b[^|;&]*\s-[a-z]*x", low):
        return "git clean -x deletes gitignored files, including the live signals.db."
    if re.search(r"git\s+(checkout|restore)\b[^|;&]*signals\.db", low):
        return "git checkout/restore targeting signals.db."
    if re.search(r">>?\s*[^|;&]*signals\.db", low):
        return "Shell redirection writing onto a signals.db path."
    if re.search(r"\b(rm|del|erase)\b", low) or "remove-item" in low:
        if "signals.db" in low:
            return "Deletion command references a protected signals.db file (live DB or recovery backup)."
        return None
    if (
        re.search(r"\b(cp|copy|mv|move)\b", low)
        or "copy-item" in low
        or "move-item" in low
    ):
        if "signals.db" in low:
            dest = last_operand(cmd)
            if is_protected_basename(dest or ""):
                return (
                    f"Copy/move with a protected signals.db file as DESTINATION ({dest}). "
                    "This is exactly how the 909->4-row reversion happened: a truncated "
                    "backup copied onto the live DB."
                )
        return None
    if "signals.db" in low and re.search(
        r"drop\s+table|delete\s+from|vacuum\s+into", low
    ):
        return "Destructive SQL (DROP TABLE / DELETE FROM / VACUUM INTO) against signals.db."
    return None


def check(tool_name: str, tool_input: dict) -> str | None:
    if tool_name in ("Bash", "PowerShell"):
        cmd = str(tool_input.get("command") or "")
        return check_shell(cmd) if cmd else None
    if tool_name == "Write":
        fp = str(tool_input.get("file_path") or "")
        if is_protected_basename(fp):
            return f"Write tool targeting a protected file ({fp}). The live signals.db must never be written as a flat file."
        return None
    if tool_name in ("mcp__filesystem__write_file", "mcp__filesystem__edit_file"):
        fp = str(tool_input.get("path") or "")
        return (
            f"filesystem MCP writing a protected file ({fp})."
            if is_protected_basename(fp)
            else None
        )
    if tool_name == "mcp__filesystem__move_file":
        dst = str(tool_input.get("destination") or "")
        return (
            f"filesystem MCP moving onto a protected file ({dst})."
            if is_protected_basename(dst)
            else None
        )
    return None


def main() -> int:
    raw = sys.stdin.read()
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0  # unparseable -> never interfere
    if not isinstance(payload, dict):
        return 0
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not tool_name or not isinstance(tool_input, dict):
        return 0

    reason = check(tool_name, tool_input)
    if not reason:
        return 0

    msg = "\n".join(
        [
            f"[signals.db guard] {reason}",
            "",
            "If this is intentional, approve to proceed. To snapshot signals.db first",
            "(hashed + ledgered, non-destructive), run:  /signals-db-checkpoint",
        ]
    )
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": msg,
        }
    }
    sys.stdout.write(json.dumps(out, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
