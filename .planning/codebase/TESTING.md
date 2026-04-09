# Testing

**Analysis Date:** 2026-04-08

## Framework

- **Runner:** pytest (`>= 6.0` per `pytest.ini` `minversion`)
- **Async support:** `pytest-asyncio` with `asyncio_mode = auto` — async tests do **not** need `@pytest.mark.asyncio`
- **Plugin loading:** `conftest.py:28` — `pytest_plugins = ["pytest_asyncio"]`
- **Strict mode:** `--strict-markers --strict-config` (unknown markers and config errors fail collection)

## Configuration

### `pytest.ini` (project root)

Key settings:
- `asyncio_mode = auto`
- `python_files = test_*.py *_test.py` — both naming conventions accepted
- `python_classes = Test*`
- `python_functions = test_*`
- `addopts = -ra --strict-markers --strict-config --showlocals`

### Registered Markers
- `integration` — tests requiring network access (deselectable)
- `slow` — long-running tests
- `asyncio` — auto-handled by `pytest-asyncio` (registered in `conftest.py:21` to suppress warnings)

### Test Paths (`pytest.ini` `testpaths`)
```
tests
collectors
consumer/tests
storage/tests
workflows/tests
utils
```

Tests live in two patterns:
1. **Top-level mirror:** `tests/<package>/test_<unit>.py` (e.g. `tests/storage/test_signal_store.py`)
2. **Co-located:** `<package>/tests/test_<unit>.py` (e.g. `workflows/tests/test_pipeline.py`)

Both patterns coexist; new tests should follow the existing convention for that subsystem.

## Test Suite Scale

- **Last reported:** 9402 / 9548 passing (Step 4A observation window close, 2026-03-23)
- **Test count growth:** +320 tests in Day 1 of obs window alone (commit `d26e3d2`)
- **Coverage tracked:** Yes, but no enforced threshold gate

## Conftest Layout

| File | Scope | Purpose |
|---|---|---|
| `conftest.py` (root) | Session | Registers `integration`/`asyncio` markers, loads `pytest_asyncio`, defines `event_loop_policy` fixture |
| `tests/storage/conftest.py` | tests/storage | `store`, `store_with_signals`, `sample_signal_data*`, `temp_db_path` fixtures (temp file-backed `SignalStore`) |
| `tests/connectors/conftest.py` | tests/connectors | Connector mocks (Notion, etc.) |
| `tests/ops/quality/conftest.py` | tests/ops/quality | Quality ops fixtures (labels, exports, etc.) |

### Common Fixtures

From `tests/storage/conftest.py`:
- `store` (async) — fresh temp-file-backed `SignalStore` with cleanup
- `store_with_signals` — pre-populated with `sample_signal_data` + `sample_signal_data_2`
- `sample_signal_data`, `sample_signal_data_2`, `sample_signal_data_github` — dict literals for canonical signal types (sec_edgar funding, product_hunt launch, github_spike)
- `temp_db_path` — caller-managed temp file path

Pattern: storage tests use **real SQLite over temp files**, not mocks. This is intentional — see feedback memory `feedback_ownership_vs_laziness.md` and the project rule against mocking the DB.

## Mocking Strategy

- **HTTP / external APIs:** Mocked with `unittest.mock.MagicMock` / `AsyncMock` (see collector tests)
- **DB:** **NOT mocked** for storage layer — uses temp file `SignalStore` instances
- **LLM calls:** Mocked at the classifier boundary (`utils/thesis_matcher.py` and `consumer/llm_classifier.py`)
- **Notion:** Mocked at the connector level for unit tests; integration tests can hit a sandbox database when `NOTION_API_KEY` is set

## Test Categories

| Category | Marker / Location | When run |
|---|---|---|
| Unit tests | Default (no marker) | Always |
| Integration tests | `@pytest.mark.integration` | Opt-in via `-m integration` |
| Slow tests | `@pytest.mark.slow` | Opt-out by default; opt-in via `-m slow` |
| CLI smoke tests | `tests/cli/`, `tests/smoke/` | Always |
| End-to-end | `tests/e2e/` | Always (some may be marked integration) |
| Performance | `tests/performance/` | Manual / scheduled |

## Running Tests

```bash
# Full suite
pytest

# Specific subsystem
pytest tests/storage/
pytest workflows/tests/test_pipeline.py

# Single test
pytest tests/storage/test_signal_store.py::TestSaveSignal::test_dedupe_by_canonical_key

# Skip slow / integration
pytest -m "not slow and not integration"

# Only integration
pytest -m integration

# With coverage
pytest --cov=storage --cov=workflows --cov-report=term-missing
```

## TDD Discipline

The project enforces **Test-Driven Development** (per `docs/claude/cli-commands.md` "The Iron Law" section):

1. Write failing test
2. Verify RED
3. Implement minimal code
4. Verify GREEN
5. Commit

**Red flags requiring restart:**
- Code written before failing tests
- Tests passing immediately upon writing
- Tests marked for "later" addition

## Pre-commit / CI Test Runs

- **Atomic commits:** Each phase task expected to commit when tests pass (`gsd-executor` enforces)
- **Coverage:** Tracked but not gated; new code expected to maintain or improve coverage
- **CI:** Babysitter integration deferred (workflow file `.github/workflows/babysitter.yml` not yet committed)

## Known Test Issues

- `tests/KNOWN_FAILURES.md` — file exists at root of `tests/` documenting accepted failures
- ~146 tests fail in the most recent full run (9402 pass / 9548 total). Investigate before claiming a test gate.

## File Naming Quick Reference

```
collectors/
├── github.py                    # source
└── tests/test_github.py         # NOT used here — collectors live in tests/collectors/

tests/collectors/
├── test_github.py               # unit tests
└── test_github_integration.py   # integration

storage/
├── signal_store.py
└── tests/test_signal_store.py   # co-located (storage uses both patterns)

tests/storage/
├── conftest.py                  # storage fixtures
└── test_signal_store.py         # mirrored unit tests
```

When in doubt, follow the existing layout for the subsystem you're touching rather than introducing a new pattern.
