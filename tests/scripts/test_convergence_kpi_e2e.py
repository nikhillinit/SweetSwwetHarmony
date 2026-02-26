"""
E2E convergence KPI integration test.

Proves that two signals with the same canonical_key but different source_apis
and evidence_families produce a nonzero convergence KPI, confirming the
full pipeline from save_signal() → convergence_kpi.run().
"""

from __future__ import annotations

import pytest

from storage.signal_store import SignalStore


@pytest.fixture
async def proof_db(tmp_path):
    """Create a v43 DB with a known-converging key (domain:proof.com).

    Signal 1: hiring (greenhouse_jobs)
    Signal 2: public_buzz (manual_seed_buzz / news_mention)
    """
    db_path = str(tmp_path / "proof.db")
    store = SignalStore(db_path)
    await store.initialize()

    # Signal 1: hiring family via greenhouse_jobs
    await store.save_signal(
        signal_type="hiring_signal",
        source_api="greenhouse_jobs",
        canonical_key="domain:proof.com",
        confidence=0.7,
        raw_data={
            "company_url": "https://proof.com",
            "company_name": "Proof Inc",
        },
    )

    # Signal 2: public_buzz family via manual_seed_buzz
    await store.save_signal(
        signal_type="news_mention",
        source_api="manual_seed_buzz",
        canonical_key="domain:proof.com",
        confidence=0.5,
        raw_data={
            "company_url": "https://proof.com",
            "company_name": "Proof Inc",
        },
    )

    await store.close()
    return db_path


@pytest.mark.asyncio
async def test_convergence_kpi_nonzero_with_two_sources(proof_db):
    """Two signals on same key from different source_apis → KPI > 0."""
    from scripts.convergence_kpi import run

    result = await run(db_path=proof_db, days=30)

    assert result["ok"] is True, f"KPI run failed: {result}"
    assert result["keys_with_2plus_source_apis"] >= 1, (
        f"Expected >= 1 key with 2+ source APIs, got {result['keys_with_2plus_source_apis']}"
    )


@pytest.mark.asyncio
async def test_convergence_kpi_nonzero_with_two_families(proof_db):
    """Two signals on same key from different families → KPI > 0."""
    from scripts.convergence_kpi import run

    result = await run(db_path=proof_db, days=30)

    assert result["ok"] is True, f"KPI run failed: {result}"
    assert result["keys_with_2plus_families"] >= 1, (
        f"Expected >= 1 key with 2+ families, got {result['keys_with_2plus_families']}"
    )


@pytest.mark.asyncio
async def test_evidence_families_populated_at_insert(proof_db):
    """save_signal() should auto-populate evidence_family and canonical_key_v2."""
    import sqlite3

    conn = sqlite3.connect(proof_db)
    rows = conn.execute(
        "SELECT evidence_family, canonical_key_v2 FROM signals "
        "WHERE canonical_key = 'domain:proof.com' ORDER BY id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2, f"Expected 2 signals, got {len(rows)}"

    # Signal 1: hiring_signal → hiring
    assert rows[0][0] == "hiring", f"Expected 'hiring', got {rows[0][0]}"
    assert rows[0][1] is not None, "canonical_key_v2 should be populated"

    # Signal 2: news_mention → public_buzz
    assert rows[1][0] == "public_buzz", f"Expected 'public_buzz', got {rows[1][0]}"
    assert rows[1][1] is not None, "canonical_key_v2 should be populated"


@pytest.mark.asyncio
async def test_same_canonical_key_v2_for_same_domain(proof_db):
    """Both signals for domain:proof.com should get the same canonical_key_v2."""
    import sqlite3

    conn = sqlite3.connect(proof_db)
    rows = conn.execute(
        "SELECT DISTINCT canonical_key_v2 FROM signals "
        "WHERE canonical_key = 'domain:proof.com' "
        "AND canonical_key_v2 IS NOT NULL"
    ).fetchall()
    conn.close()

    # Both should resolve to the same domain-based v2 key
    assert len(rows) == 1, (
        f"Expected 1 unique canonical_key_v2, got {len(rows)}: "
        f"{[r[0] for r in rows]}"
    )


@pytest.mark.asyncio
async def test_three_source_convergence(tmp_path):
    """Three different source_apis on same key → even stronger convergence."""
    from scripts.convergence_kpi import run

    db_path = str(tmp_path / "triple.db")
    store = SignalStore(db_path)
    await store.initialize()

    signals = [
        ("hiring_signal", "greenhouse_jobs"),      # hiring
        ("news_mention", "news_api"),               # public_buzz
        ("github_spike", "github"),                 # developer
    ]
    for sig_type, src_api in signals:
        await store.save_signal(
            signal_type=sig_type,
            source_api=src_api,
            canonical_key="domain:triple.io",
            confidence=0.6,
            raw_data={
                "company_url": "https://triple.io",
                "company_name": "Triple Co",
            },
        )

    await store.close()

    result = await run(db_path=db_path, days=30)
    assert result["ok"] is True
    assert result["keys_with_2plus_families"] >= 1
    assert result["keys_with_2plus_source_apis"] >= 1
    assert result["total_signals"] == 3
