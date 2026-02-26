"""CI guard: check all docs/*.md files for UTF-8 validity and mojibake.

Walks docs/**/*.md, decodes as UTF-8, checks for mojibake signatures
(U+FFFD replacement character, double-encoded sequences).

Exit 0 if clean, exit 1 if errors found.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Common mojibake patterns (double-encoded UTF-8)
_MOJIBAKE_PATTERNS = [
    "\ufffd",       # U+FFFD REPLACEMENT CHARACTER
    "\xc3\xa2",     # Double-encoded â
    "\xc3\xa9",     # Double-encoded é
    "\xc3\xab",     # Double-encoded ë
    "\xc2\xa0",     # Double-encoded NBSP
    "\xc2\xab",     # Double-encoded «
    "\xc2\xbb",     # Double-encoded »
]


def check_file(path: Path) -> list[str]:
    """Check a single file for UTF-8 issues. Returns list of error strings."""
    errors = []

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{path}: UTF-8 decode error: {exc}")
        return errors

    for i, line in enumerate(content.splitlines(), 1):
        for pattern in _MOJIBAKE_PATTERNS:
            if pattern in line:
                errors.append(
                    f"{path}:{i}: mojibake detected (contains {repr(pattern)})"
                )
                break  # one error per line is enough

    return errors


def main(docs_dir: str = "docs") -> int:
    """Check all .md files under docs_dir. Returns exit code."""
    root = Path(docs_dir)
    if not root.exists():
        print(f"WARNING: {docs_dir} directory not found, skipping check")
        return 0

    all_errors: list[str] = []
    file_count = 0

    for md_file in sorted(root.rglob("*.md")):
        file_count += 1
        all_errors.extend(check_file(md_file))

    if all_errors:
        print(f"UTF-8 check FAILED: {len(all_errors)} issues in {file_count} files")
        for err in all_errors:
            print(f"  {err}")
        return 1

    print(f"UTF-8 check passed: {file_count} files clean")
    return 0


if __name__ == "__main__":
    docs_path = sys.argv[1] if len(sys.argv) > 1 else "docs"
    sys.exit(main(docs_path))
