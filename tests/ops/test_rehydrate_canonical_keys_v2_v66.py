"""Tests for scripts/rehydrate_canonical_keys_v2.py.

Verifies:
- Dry-run produces audit sample
- Fan-in gate enforcement
- Commit under threshold succeeds
"""

import json
import sqlite3

import pytest

from storage.signal_store import SignalStore


@pytest.fixture
async def db_with_signals(tmp_path):
    """Create a DB with signals that have NULL canonical_key_v2."""
    db_path = str(tmp_path / "rehydrate_test.db")
    store = SignalStore(db_path)
    await store.initialize()

    for i, (st, sa, ck, raw) in enumerate([
        ("github_spike", "github", "domain:acme.ai",
         {"company_name": "Acme"}),
        ("hiring_signal", "job_postings", "name_loc:beta-corp",
         {"company_name": "Beta Corp"}),
        ("news_mention", "news_api", "domain:gamma.io",
         {"company_name": "Gamma"}),
    ]):
        await store.save_signal(
            signal_type=st, source_api=sa, canonical_key=ck,
            confidence=0.7, raw_data=raw,
        )

    await store.close()

    # Clear canonical_key_v2 to simulate pre-v43 data
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE signals SET canonical_key_v2 = NULL")
    conn.commit()
    conn.close()

    return db_path


@pytest.mark.asyncio
async def test_dry_run_produces_audit_sample(db_with_signals, tmp_path):
    """Dry-run produces an audit sample file."""
    from scripts.rehydrate_canonical_keys_v2 import run

    audit_path = str(tmp_path / "audit.json")
    result = await run(
        db_path=db_with_signals, dry_run=True,
        audit_sample_out=audit_path,
    )

    assert result["dry_run"] is True
    assert result["rows_scanned"] == 3
    assert result["audit_sample_count"] > 0

    # Verify audit file written
    with open(audit_path) as f:
        samples = json.load(f)
    assert len(samples) > 0
    assert "canonical_key" in samples[0]
    assert "canonical_key_v2" in samples[0]


@pytest.mark.asyncio
async def test_commit_updates_rows(db_with_signals):
    """Commit mode updates canonical_key_v2 for eligible rows."""
    from scripts.rehydrate_canonical_keys_v2 import run

    result = await run(db_path=db_with_signals, dry_run=False)

    assert result["dry_run"] is False
    assert result["rows_updated"] > 0

    # Verify DB updated
    conn = sqlite3.connect(db_with_signals)
    non_null = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE canonical_key_v2 IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert non_null > 0


@pytest.mark.asyncio
async def test_fanin_gate(db_with_signals):
    """Fan-in violations are detected when threshold is very low."""
    from scripts.rehydrate_canonical_keys_v2 import run

    # First commit the rehydration
    await run(db_path=db_with_signals, dry_run=False)

    # Now run again with max_fanin=0 to trigger violations
    # (any key with count > 0 would violate)
    result = await run(db_path=db_with_signals, dry_run=False, max_fanin=0)

    assert len(result.get("fanin_violations", [])) > 0


@pytest.mark.asyncio
async def test_commit_fanin_gate_rolls_back_tentative_writes(tmp_path):
    """Commit mode rolls back if fan-in validation fails."""
    from scripts.rehydrate_canonical_keys_v2 import run

    db_path = str(tmp_path / "rehydrate_fanin_rollback.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY,
            canonical_key_v2 TEXT,
            signal_type TEXT,
            source_api TEXT,
            canonical_key TEXT,
            raw_data TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO signals (
            id, canonical_key_v2, signal_type, source_api, canonical_key, raw_data
        ) VALUES (?, NULL, 'github_spike', 'github', ?, ?)
        """,
        [
            (1, "name_loc:same-company", '{"company_name": "Same Company"}'),
            (2, "name_loc:same-company", '{"company_name": "Same Company"}'),
            (3, "name_loc:same-company", '{"company_name": "Same Company"}'),
        ],
    )
    conn.commit()
    conn.close()

    result = await run(db_path=db_path, dry_run=False, chunk_size=2, max_fanin=1)

    assert len(result.get("fanin_violations", [])) > 0

    conn = sqlite3.connect(db_path)
    non_null = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE canonical_key_v2 IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    assert non_null == 0, "Fan-in failure should roll back all tentative writes"
