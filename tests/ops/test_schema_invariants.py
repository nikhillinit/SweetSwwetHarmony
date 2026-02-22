"""Schema ownership invariants for the signals table.

Ensures OpsStorage never mutates SignalStore-owned schema.
Constants and helpers are private to this file; extract to
tests/ops/conftest.py only when a second consumer appears.
"""

import sqlite3
import pytest

# --- Constants ---

REQUIRED_SIGNALS_COLUMNS = frozenset({
    "canonical_key",   # v1 — dedup key
    "signal_type",     # v1 — signal category
    "source_api",      # v1 — collector source
    "raw_data",        # v1 — original payload
    "company_id",      # v28 — entity identity (NOT ops contamination)
})

PROHIBITED_OPS_COLUMNS = frozenset({
    "title",           # ops-layer column
    "description",     # ops-layer column
})


# --- Private helpers ---

def _table_exists(conn, name: str) -> bool:
    """Check sqlite_master for table existence."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _get_signals_defs(db_path: str) -> dict:
    """Return {col_name: (type, notnull, default, pk)} for signals table.

    Fails explicitly if signals table does not exist (prevents vacuous passes).
    Uses context manager to guarantee connection closure on assertion failure.
    """
    with sqlite3.connect(db_path) as conn:
        assert _table_exists(conn, "signals"), (
            f"signals table does not exist in {db_path} — "
            "SignalStore.initialize() may have failed silently"
        )
        cursor = conn.execute("PRAGMA table_info(signals)")
        defs = {}
        for row in cursor.fetchall():
            col_name = row[1].lower().strip()
            col_type = " ".join(row[2].lower().split()) if row[2] else ""
            defs[col_name] = (col_type, row[3], row[4], row[5])
    return defs


def _diff_schema(before: dict, after: dict) -> tuple[list, list, list]:
    """Return sorted (added, removed, altered) column lists."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    altered = sorted(
        c for c in before if c in after and before[c] != after[c]
    )
    return added, removed, altered


def _assert_ownership(defs: dict, context: str) -> None:
    """Assert required columns present and prohibited columns absent."""
    columns = set(defs)
    missing = sorted(REQUIRED_SIGNALS_COLUMNS - columns)
    assert not missing, f"[{context}] signals missing required columns: {missing}"
    leaked = sorted(PROHIBITED_OPS_COLUMNS & columns)
    assert not leaked, f"[{context}] ops columns leaked into signals: {leaked}"


# --- Tests ---

@pytest.mark.asyncio
async def test_first_init_ops_does_not_mutate_signals_schema(tmp_path):
    """OpsStorage must not add, remove, or alter signals table columns.

    Lifecycle: init SignalStore -> close -> snapshot -> init OpsStorage -> snapshot -> diff.
    try/finally ensures SignalStore connection is always released.
    """
    from storage.signal_store import SignalStore
    from ops.storage import OpsStorage

    db_path = str(tmp_path / "invariant_test.db")

    # Step 1: init SignalStore (try/finally for async resource safety)
    store = SignalStore(db_path)
    try:
        await store.initialize()
    finally:
        await store.close()

    # Step 2: snapshot BEFORE OpsStorage (connection released, no lock contention)
    before = _get_signals_defs(db_path)

    # Step 3: ownership contract holds after SignalStore init
    _assert_ownership(before, context="pre-OpsStorage")

    # Step 4: init OpsStorage
    OpsStorage(db_path)

    # Step 5: snapshot AFTER OpsStorage
    after = _get_signals_defs(db_path)

    # Step 6: zero mutation
    added, removed, altered = _diff_schema(before, after)
    assert before == after, (
        f"OpsStorage mutated signals table!\n"
        f"  Added:   {added or '(none)'}\n"
        f"  Removed: {removed or '(none)'}\n"
        f"  Altered: {altered or '(none)'}"
    )

    # Step 7: ownership contract still holds after OpsStorage
    _assert_ownership(after, context="post-OpsStorage")


def test_ops_storage_does_not_create_signalstore_tables_when_run_first(tmp_path):
    """OpsStorage on an empty DB must not create SignalStore-owned tables.

    Guards the architectural invariant documented in ops/storage.py:3:
    'This module does NOT create signals/companies tables.'
    """
    from ops.storage import OpsStorage

    db_path = str(tmp_path / "ops_only_test.db")
    OpsStorage(db_path)

    with sqlite3.connect(db_path) as conn:
        # SignalStore-owned tables must NOT exist
        assert not _table_exists(conn, "signals"), (
            "OpsStorage created a signals table on an empty DB — "
            "violates ownership invariant (signals owned by SignalStore)"
        )
        assert not _table_exists(conn, "signal_processing"), (
            "OpsStorage created signal_processing table"
        )
        assert not _table_exists(conn, "suppression_cache"), (
            "OpsStorage created suppression_cache table"
        )
        assert not _table_exists(conn, "company_files"), (
            "OpsStorage created company_files table"
        )

        # Ops tables MUST exist (guards against vacuous pass)
        assert _table_exists(conn, "user_actions"), (
            "OpsStorage should create user_actions"
        )
        assert _table_exists(conn, "memory_facts"), (
            "OpsStorage should create memory_facts"
        )
