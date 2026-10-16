"""Cross-platform launcher for the discovery-engine MCP server.

Replaces run_discovery_engine.bat: loads .env from the repo root (python-dotenv is a
runtime dependency), then runs `python -m discovery_engine.mcp_server`. Referenced
from .mcp.json; interpreter is `${HARMONIC_PYTHON:-python3}` -- set HARMONIC_PYTHON
on hosts where the interpreter is not named python3 (e.g. `python` on Windows).
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    try:
        from dotenv import load_dotenv
    except (
        ImportError
    ):  # pragma: no cover - deps not installed; server import will fail loudly
        pass
    else:
        load_dotenv(ROOT / ".env")
    runpy.run_module("discovery_engine.mcp_server", run_name="__main__")


if __name__ == "__main__":
    main()
