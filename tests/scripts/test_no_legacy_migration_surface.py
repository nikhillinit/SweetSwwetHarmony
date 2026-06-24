"""Guard test: the legacy migration surface stays gone and fenced off.

`scripts/run_migration.py` (and its test) were deleted on `main` after audit
confirmed no live consumer. The runner used a fabricated `schema_version` table
and a synthetic v52 that diverged from production. Migration truth on `main` is
`storage.signal_store.MIGRATIONS` + the `schema_migrations` table, with
`CURRENT_SCHEMA_VERSION == max(MIGRATIONS)`.

This guard prevents the legacy pattern from creeping back into operator-facing
surfaces. It is scoped to operator runbooks (`docs/runbooks/`) because that is
the operator-facing surface the acceptance criterion targets; historical
planning docs under `docs/plans/` may legitimately record the old design.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = ROOT / "docs" / "runbooks"

# `app_meta` was the legacy meta table holding a `schema_version` key.
APP_META = re.compile(r"\bapp_meta\b")
# A table literally named `schema_version` (the runner's fabricated table),
# matched only in SQL-identifier position so prose like "current schema version"
# is not flagged.
SCHEMA_VERSION_TABLE = re.compile(
    r"(?i)\b(from|into|update|table|join)\s+schema_version\b"
)
# A recommendation to run or (re-)create the deleted runner — NOT a past-tense
# "was deleted" mention.
RUN_MIGRATION_RECOMMENDATION = re.compile(
    r"(python\s+(-m\s+\S+\s+)?scripts/run_migration\.py"
    r"|scripts/run_migration\.py\s+--"
    r"|Create:\s*`?scripts/run_migration\.py"
    r"|git\s+add[^\n]*run_migration\.py)"
)


def _runbooks() -> list[Path]:
    return sorted(RUNBOOKS.rglob("*.md")) if RUNBOOKS.exists() else []


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    out = []
    for path in _runbooks():
        if pattern.search(path.read_text(encoding="utf-8")):
            out.append(path.relative_to(ROOT).as_posix())
    return out


def test_legacy_migration_runner_absent():
    assert not (ROOT / "scripts" / "run_migration.py").exists()
    assert not (ROOT / "tests" / "scripts" / "test_run_migration.py").exists()


def test_runbooks_do_not_reference_app_meta():
    offenders = _offenders(APP_META)
    assert offenders == [], f"operator runbooks still reference app_meta: {offenders}"


def test_runbooks_do_not_teach_schema_version_table():
    offenders = _offenders(SCHEMA_VERSION_TABLE)
    assert offenders == [], (
        f"operator runbooks still teach a schema_version-table pattern: {offenders}"
    )


def test_runbooks_do_not_recommend_legacy_runner():
    offenders = _offenders(RUN_MIGRATION_RECOMMENDATION)
    assert offenders == [], (
        f"operator runbooks still recommend scripts/run_migration.py: {offenders}"
    )


def test_storage_migrations_validate_succeeds_on_fresh_db(tmp_path):
    from storage.signal_store import SignalStore

    db = tmp_path / "fresh.db"

    async def _init() -> None:
        store = SignalStore(str(db))
        await store.initialize()
        await store.close()

    asyncio.run(_init())

    proc = subprocess.run(
        [sys.executable, "-m", "storage.migrations", "validate", str(db)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"validate failed:\n{proc.stdout}\n{proc.stderr}"
    assert "Schema is valid" in proc.stdout
