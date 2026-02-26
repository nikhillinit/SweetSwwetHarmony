"""Tests for scripts/backfill_evidence_family.py.

Verifies:
- Dry-run produces report without writes
- Commit updates NULL rows
- Delta gate abort
- Rerun idempotent in NULL-only mode
"""

import sqlite3

import pytest

from storage.signal_store import SignalStore


@pytest.fixture
async def db_with_signals(tmp_path):
    """Create a DB with signals that have NULL evidence_family."""
    db_path = str(tmp_path / "backfill_test.db")
    store = SignalStore(db_path)
    await store.initialize()

    # Insert test signals
    for st, sa, ck in [
        ("github_spike", "github", "domain:acme.ai"),
        ("hiring_signal", "job_postings", "domain:beta.co"),
        ("news_mention", "news_api", "domain:gamma.io"),
    ]:
        await store.save_signal(
            signal_type=st, source_api=sa, canonical_key=ck,
            confidence=0.7, raw_data={"company_name": "Test"},
        )

    await store.close()

    # Clear evidence_family to simulate pre-v42 data
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE signals SET evidence_family = NULL")
    conn.commit()
    conn.close()

    return db_path


@pytest.mark.asyncio
async def test_dry_run_no_writes(db_with_signals):
    """Dry-run produces report but doesn't write to DB."""
    from scripts.backfill_evidence_family import run

    result = await run(db_path=db_with_signals, dry_run=True)

    assert result["dry_run"] is True
    assert result["rows_scanned"] == 3
    assert result["rows_updated"] == 3  # would-be updates

    # Verify DB unchanged
    conn = sqlite3.connect(db_with_signals)
    nulls = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE evidence_family IS NULL"
    ).fetchone()[0]
    conn.close()
    assert nulls == 3, "Dry-run should not have written anything"


@pytest.mark.asyncio
async def test_commit_updates_null_rows(db_with_signals):
    """Commit mode updates NULL rows."""
    from scripts.backfill_evidence_family import run

    result = await run(db_path=db_with_signals, dry_run=False)

    assert result["dry_run"] is False
    assert result["rows_updated"] == 3

    # Verify DB updated
    conn = sqlite3.connect(db_with_signals)
    nulls = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE evidence_family IS NULL"
    ).fetchone()[0]
    conn.close()
    assert nulls == 0, "All rows should have evidence_family set"


@pytest.mark.asyncio
async def test_delta_gate_abort(db_with_signals):
    """Delta gate triggers when unknown_rate exceeds threshold."""
    from scripts.backfill_evidence_family import run

    # First, insert a signal with an unknown type
    conn = sqlite3.connect(db_with_signals)
    conn.execute(
        "INSERT INTO signals (signal_type, source_api, canonical_key, company_name, "
        "confidence, raw_data, detected_at, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        ("totally_unknown", "test", "domain:test.com", "Test", 0.5, "{}"),
    )
    conn.commit()
    conn.close()

    result = await run(
        db_path=db_with_signals, dry_run=True,
        baseline_unknown_rate=0.0, unknown_delta_max_pp=5.0,
    )

    # With 1 unknown out of 4, rate is 25% > baseline 0% + 5pp
    assert result["delta_exceeded"] is True


@pytest.mark.asyncio
async def test_rerun_idempotent(db_with_signals):
    """Running commit twice is idempotent — second run finds no NULL rows."""
    from scripts.backfill_evidence_family import run

    await run(db_path=db_with_signals, dry_run=False)
    result = await run(db_path=db_with_signals, dry_run=False)

    assert result["rows_scanned"] == 0
    assert result["rows_updated"] == 0
