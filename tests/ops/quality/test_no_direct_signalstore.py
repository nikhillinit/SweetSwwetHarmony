"""CI lint test: enforce centralized SignalStore construction.

Production code must not construct SignalStore directly — construction
should go through a known set of entrypoints to ensure identity_store
and use_thin_files are wired correctly.

Allowlisted files:
  - storage/signal_store.py (the class definition itself)
  - run_pipeline.py (CLI entrypoint — read-only ops, pipeline invocation)
  - workflows/pipeline.py (pipeline initializer — wires identity itself)
  - api/main.py (API server startup)
  - tests/ (all test directories are exempt)
  - Root-level test/verify scripts are exempt
"""

import os
import re

import pytest

# Root of the repository
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Pattern: "SignalStore(" at word boundary, not inside a comment or string import
# We match the constructor call, not the class definition or type hints.
CONSTRUCTOR_RE = re.compile(r"(?<!\w)SignalStore\s*\(")

# Files allowed to construct SignalStore directly (relative to repo root, forward slash).
# This list is a RATCHET: existing entrypoints are grandfathered, but any NEW file
# that constructs SignalStore will fail CI and must be explicitly justified here.
# Keep this list tight — every addition weakens the guarantee.
ALLOWLIST = {
    # --- Class definition ---
    "storage/signal_store.py",

    # --- Production pipeline (wires identity_store itself) ---
    "run_pipeline.py",
    "workflows/pipeline.py",

    # --- API / dashboard (read-only access, no save_signal calls) ---
    "api/main.py",
    "api/routers/actions.py",
    "api/routers/companies.py",
    "api/routers/entities.py",
    "api/routers/health.py",
    "api/routers/jobs.py",
    "api/routers/public.py",
    "dashboard/app.py",
    "dashboard/monitoring_page.py",

    # --- Infrastructure (MCP server, migrations, health) ---
    "discovery_engine/mcp_server.py",
    "discovery_engine/curated_scout.py",
    "storage/migrations.py",
    "storage/manual_test_signal_store.py",
    "utils/signal_health.py",

    # --- Batch utilities (read-only analytics) ---
    "utils/exit_predictor_batch.py",
    "utils/investor_profile_batch.py",
    "scripts/shadow_report.py",

    # --- Importers (controlled write paths, pre-identity) ---
    "importers/openvc_csv.py",
    "importers/pitchbook_csv.py",

    # --- Distribution (read-only digest building) ---
    "distribution/scheduler.py",
    "distribution/builders/digest_builder.py",

    # --- Profilers ---
    "profilers/pdf_profiler_cli.py",
    "profilers/url_profiler.py",

    # --- Workflow examples / integration test scripts ---
    "workflows/example_push_batch.py",
    "workflows/example_suppression_sync.py",
    "workflows/integration_test_pusher.py",
    "workflows/notion_pusher.py",
    "workflows/suppression_sync.py",
    "workflows/test_notion_pusher.py",

    # --- Root-level test/utility scripts ---
    "test_suppression_sync.py",
    "verify_pdf_profiler.py",
}


def _collect_production_python_files():
    """Yield (relative_path, absolute_path) for non-test Python files."""
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        # Skip test directories, hidden dirs, and venv/node_modules
        rel_dir = os.path.relpath(dirpath, REPO_ROOT).replace("\\", "/")
        skip_prefixes = ("tests", ".worktrees", ".git", "__pycache__",
                         "venv", ".venv", "node_modules", "quality_ops_skills_and_scripts")
        # Also skip any nested "tests" directories (e.g. workflows/tests/)
        if os.path.basename(dirpath) == "tests":
            dirnames.clear()
            continue
        if any(rel_dir == p or rel_dir.startswith(p + "/") for p in skip_prefixes):
            dirnames.clear()
            continue

        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, REPO_ROOT).replace("\\", "/")
            yield rel_path, abs_path


def test_no_direct_signalstore_construction():
    """No production file outside the allowlist constructs SignalStore directly."""
    violations = []

    for rel_path, abs_path in _collect_production_python_files():
        if rel_path in ALLOWLIST:
            continue

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                # Skip comments and lines that are clearly imports/type hints
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if stripped.startswith(("from ", "import ")):
                    continue
                # Skip type annotations like "-> SignalStore" or ": SignalStore"
                if "SignalStore" in line and "(" not in line:
                    continue

                if CONSTRUCTOR_RE.search(line):
                    violations.append(f"  {rel_path}:{lineno}: {stripped.rstrip()}")

    if violations:
        msg = (
            "Direct SignalStore() construction found outside allowlist.\n"
            "Use the pipeline's initialize() or a factory helper instead.\n"
            "Violations:\n" + "\n".join(violations) + "\n\n"
            f"Allowlist: {sorted(ALLOWLIST)}\n"
            "To add a legitimate exception, update ALLOWLIST in this test."
        )
        pytest.fail(msg)
