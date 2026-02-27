# Session Checkpoint — v3 Execution Plan

## Baseline Reproduction (2026-02-26)

| # | Command | Result | Error Snippet |
|---|---------|--------|---------------|
| 1 | `python -m compileall -q visualization` | **FAIL** | `SyntaxError: 'return' with value in async generator` (line 125) |
| 2 | `python -c "import visualization"` | **FAIL** | Same SyntaxError cascades from `terminal_progress.py:125` |
| 3 | `python scripts/ci_smoke_imports.py` | **FAIL** | `[Errno 2] No such file or directory` — script does not exist |
| 4 | `python storage/migrations.py --help` | **FAIL** | `ModuleNotFoundError: No module named 'storage'` (relative import from `signal_store`) |
| 5 | `python -m storage.migrations --help` | **FAIL** | `No module named storage.migrations.__main__; 'storage.migrations' is a package` |
| 6 | `python -c "import storage.migrations as m; print(hasattr(m,'list_migrations'))"` | **FAIL** | Prints `False` — empty `__init__.py` |
| 7 | `python storage/verify_installation.py --help` | **FAIL** | `ERROR: Failed to import migration tools: cannot import name 'list_migrations'` |
| 8 | `pytest -q tests/test_export_queue.py tests/cli/test_csv_export_schema.py` | **PARTIAL** | 27 tests collected; hangs on execution (known full-suite issue) |
| 9 | `python -c "... DISCOVERY_DB_PATH=<temp> ... SignalStore().db_path"` | **FAIL** | Prints `signals.db` — ignores env var |
| 10 | `pip install -e .` | **FAIL** | `does not appear to be a Python project: neither 'setup.py' nor 'pyproject.toml' found` |
| 11 | `python --version` | **PASS** | `Python 3.11.9` |
| 12 | `pip --version` | **PASS** | `pip 25.2` |

---

## PR Status Log

### Step A: Plan/spec documentation
- **Status:** COMPLETE
- **Files changed:** findings.md (ADR-1 through ADR-6), task_plan.md (locked config decisions)
- **Skills invoked:** docs-architect (ADR structure), architecture-decision-records (env-var precedence, rollout policy)
- **Evidence:** findings.md ADR section, task_plan.md locked config section
- **Gates:** N/A (spec-only, no runtime)
- **Residual risks:** None — pure documentation

### PR1: Visualization importability
- **Status:** COMPLETE (branch: fix/visualization-importability)
- **Files changed:** visualization/terminal_progress.py, visualization/__init__.py
- **Skills invoked:** async-python-patterns (PEP 525 async generator rules)
- **Gates:** compileall PASS, import PASS, PipelineProgress class PASS
- **Residual risks:** None
- **Next:** PR2

### PR2: Migrations package-of-truth
- **Status:** COMPLETE (branch: fix/migrations-package, PR #75)
- **Files changed:** storage/migrations/__init__.py, storage/migrations/__main__.py, storage/migrations/cli.py, storage/migrations.py (shim)
- **Skills invoked:** database-schema-designer
- **Agent:** sqlite-expert
- **Gates:** compileall PASS, list_migrations callable PASS, --help PASS, verify_installation PASS
- **Next:** PR3

### PR3: Export queue runtime resilience
- **Status:** COMPLETE (branch: fix/export-queue-resilience, PR #76)
- **Files changed:** run_pipeline.py, tests/cli/test_csv_export_schema.py, storage/migrations.py (PR2a shim fix)
- **Skills invoked:** database-schema-designer
- **Agent:** sqlite-expert
- **Gates:** compileall PASS, 27/27 tests PASS, export-queue --help PASS
- **Next:** PR4

### PR4: CI hard gate
- **Status:** COMPLETE (branch: ci/hard-gate, PR #77)
- **Files changed:** .github/workflows/regression-gate.yml, scripts/ci_smoke_imports.py
- **Agent:** SecOps Governor
- **Gates:** compileall PASS, 32/32 smoke imports PASS
- **Next:** PR5

### PR5: Minimal packaging baseline
- **Status:** COMPLETE (branch: packaging/minimal-baseline, PR #78)
- **Files changed:** pyproject.toml
- **Gates:** pip install -e . PASS, curated imports PASS
- **Next:** PR6

### PR6: Dependency/Gemini convergence
- **Status:** COMPLETE (branch: deps/gemini-convergence, commit f257fea)
- **Files changed:** utils/news_digest.py, requirements.txt, tests/utils/test_news_digest.py
- **Skills invoked:** verification-before-completion, python-testing-patterns
- **Gates:**
  - `compileall -q utils consumer monitoring` — PASS
  - `import utils.news_digest` — PASS
  - `ci_smoke_imports.py` — PASS (32/32)
  - `pytest tests/utils/test_news_digest.py` — PASS (34/34)
  - `pip check` — **NOT AUTHORITATIVE** (ran against global site-packages, not project venv)
- **Known issue:** `pip check` ran in global Python env (`C:\Users\nikhi\AppData\Local\Programs\Python\Python311`), not an isolated venv. Reported conflict (`llama-index-llms-openai` vs `openai`) is global env pollution — `llama-index` is not in `requirements.txt`. A clean venv-based `pip check` is required for authoritative results (see PR-depclean below).
- **Reproducibility:** commit f257fea, Python 3.11.9, Windows 11, local
- **Residual risks:** None for PR6 scope; venv hygiene addressed by PR-depclean
- **Next:** PR7

### PR7: DB path unification
- **Status:** COMPLETE (branch: fix/db-path-unification, commit 08d0727)
- **Files changed:** utils/db_path_helper.py, storage/signal_store.py, discovery_engine/mcp_server.py, tests/test_db_path_resolution.py (new)
- **Skills invoked:** database-schema-designer, verification-before-completion
- **Agent:** sqlite-expert (path resolution, SignalStore env tests)
- **Gates:**
  - `compileall -q storage utils api discovery_engine distribution` — PASS
  - `pytest tests/test_db_path_resolution.py` — PASS (20/20)
  - `ci_smoke_imports.py` — PASS (32/32)
  - Env probe: `DISCOVERY_DB_PATH=/tmp/test_signals.db SignalStore().db_path` — PASS (outputs expected temp path)
- **Baseline #9 resolved:** SignalStore() now correctly reads DISCOVERY_DB_PATH
- **Reproducibility:** commit 08d0727, Python 3.11.9, Windows 11, local
- **Residual risks:** Existing callers passing explicit `"signals.db"` string still bypass env (by design — explicit wins)
- **Next:** PR8

### PR8: API store lifecycle + correctness
- **Status:** PENDING

### PR9: Canonical foundation + no-regress tests
- **Status:** PENDING

### PR10a: Collector key hygiene
- **Status:** PENDING

### PR10b: DNS promotion delivery
- **Status:** PENDING

### PR11: Enrichment SKIP semantics
- **Status:** PENDING

### PR12: Diagnostic scoping + metrics contract
- **Status:** PENDING

### PR13: Identity/canonical lint harness + CI
- **Status:** PENDING

---

## Backlog: Dependency Hygiene (PR-depclean)

**Priority:** Post-PR13, or opportunistic when CI venv is set up.

### Problem
1. All local `pip check` runs use global site-packages (`C:\Users\nikhi\AppData\Local\Programs\Python\Python311`), not an isolated project venv. This means results include non-project packages (e.g., `llama-index-llms-openai`) and may miss project-specific conflicts.
2. `llama-index-llms-openai 0.6.5` requires `openai<2`, but global env has `openai 2.17.0`. This is global env pollution — neither `llama-index` nor `openai` appear in `requirements.txt`.

### Actions
1. **Create project venv and run authoritative `pip check`:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   pip check
   ```
2. **Add `pip check` to CI regression gate** (in `.github/workflows/regression-gate.yml`), running inside the CI venv where only `requirements.txt` deps are installed. This becomes the source of truth.
3. **If `llama-index` is intentionally used anywhere**, add it to `requirements.txt` with compatible `openai` pin. If not, confirm it's safe to ignore (global-only).
4. **Clean global env** (optional, user discretion): `pip uninstall llama-index-llms-openai` from global Python to eliminate noise in local runs.
