#!/usr/bin/env python3
"""CI smoke-import gate: verify key modules are importable without error.

This script imports a curated allowlist of modules that must be importable
for the codebase to be considered healthy. It does NOT import every module —
only those known to be safe (no network, no DB mutation, no heavy side effects).

Exit codes:
    0 — all imports succeeded
    1 — one or more imports failed
"""

import importlib
import os
import sys
import traceback

# Ensure project root is on sys.path (CI scripts run from repo root,
# but Python adds the script's directory, not CWD, to sys.path).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Curated allowlist: each entry is a dotted module path.
# Selection criteria:
#   - No network calls at import time
#   - No DB writes at import time
#   - No file creation at import time
#   - Represents a critical subsystem (breakage = broken pipeline)
SAFE_IMPORTS = [
    # Core packages (have __init__.py)
    "api",
    "collectors",
    "consumer",
    "dashboard",
    "discovery_engine",
    "distribution",
    "enrichment",
    "importers",
    "intelligence",
    "monitoring",
    "ops",
    "profilers",
    "services",
    "storage",
    "visualization",
    "workflows",
    # Key submodules (critical runtime paths)
    "storage.signal_store",
    "storage.migrations",
    "utils.canonical_keys",
    "utils.canonical_key_v2",
    "utils.report_envelope",
    "utils.db_path_helper",
    "verification.evidence_families",
    "collectors.base",
    "collectors.github",
    "collectors.rss_feeds",
    "collectors.news_api",
    "collectors.hacker_news",
    "collectors.sec_edgar",
    "connectors.notion_connector_v2",
    "workflows.pipeline",
    "ops.storage",
]

# Intentionally excluded:
#   - integrations (load_dotenv side effect in openai_mcp)
#   - run_pipeline (argparse setup runs at import time)
#   - scripts/* (standalone entry points, not library modules)
#   - config (raw config files)


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    failures = []
    for module_path in SAFE_IMPORTS:
        try:
            importlib.import_module(module_path)
        except Exception:
            failures.append((module_path, traceback.format_exc()))

    if failures:
        print(f"FAIL: {len(failures)}/{len(SAFE_IMPORTS)} imports failed\n")
        for mod, tb in failures:
            print(f"--- {mod} ---")
            print(tb)
        return 1

    print(f"OK: {len(SAFE_IMPORTS)}/{len(SAFE_IMPORTS)} imports succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
