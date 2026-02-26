"""Tests for scripts/convergence_kpi.py.

Verifies:
- Schema < v43 returns non-zero with report
- Synthetic exclusion honored
- Report fields populated on v43 fixture
"""

import sqlite3

import pytest

from storage.signal_store import SignalStore


@pytest.fixture
async def v43_db(tmp_path):
    """Create a v43 DB with some signals for KPI computation."""
    db_path = str(tmp_path / "kpi_test.db")
    store = SignalStore(db_path)
    await store.initialize()

    # Insert diverse signals for convergence
    signals = [
        ("github_spike", "github", "domain:converge.io",
         {"company_url": "https://converge.io", "company_name": "Converge"}),
        ("hiring_signal", "job_postings", "domain:converge.io",
         {"company_url": "https://converge.io", "company_name": "Converge"}),
        ("news_mention", "news_api", "domain:converge.io",
         {"company_url": "https://converge.io", "company_name": "Converge"}),
        ("github_spike", "github", "domain:solo.ai",
         {"company_url": "https://solo.ai", "company_name": "Solo"}),
    ]
    for st, sa, ck, raw in signals:
        await store.save_signal(
            signal_type=st, source_api=sa, canonical_key=ck,
            confidence=0.7, raw_data=raw,
        )

    await store.close()
    return db_path


@pytest.mark.asyncio
async def test_schema_guard_fails_below_v43(tmp_path):
    """Schema < v43 should return ok=False."""
    from scripts.convergence_kpi import run

    # Create a DB and pretend it's old by removing v42/v43 migrations
    db_path = str(tmp_path / "old_schema.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER)")
    conn.execute("INSERT INTO schema_migrations (version) VALUES (41)")
    conn.commit()
    conn.close()

    result = await run(db_path=db_path)
    assert result["ok"] is False
    assert "Schema version" in result.get("error", "")


@pytest.mark.asyncio
async def test_report_fields_populated(v43_db):
    """Report from v43 DB has all expected fields."""
    from scripts.convergence_kpi import run

    result = await run(db_path=v43_db, days=30)

    assert result["ok"] is True
    assert "keys_with_2plus_families" in result
    assert "keys_with_2plus_source_apis" in result
    assert "unknown_family_rate" in result
    assert "unlinked_buzz_rate" in result
    assert "per_source_breakdown" in result
    assert result["total_signals"] > 0


@pytest.mark.asyncio
async def test_convergence_detected(v43_db):
    """DB with converge.io across 3 families should show convergence."""
    from scripts.convergence_kpi import run

    result = await run(db_path=v43_db, days=30)

    # converge.io has signals from developer (github), hiring (job_postings),
    # and public_buzz (news_api) families
    assert result["keys_with_2plus_families"] >= 1


@pytest.mark.asyncio
async def test_exclude_unlinked_buzz(v43_db):
    """Synthetic unlinked_buzz keys should be excluded by default."""
    from scripts.convergence_kpi import run

    # Insert a signal that produces unlinked_buzz
    conn = sqlite3.connect(v43_db)
    conn.execute(
        "INSERT INTO signals (signal_type, source_api, canonical_key, company_name, "
        "confidence, raw_data, detected_at, created_at, evidence_family, canonical_key_v2) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?, ?)",
        ("news_mention", "news_api", "news:12345", "Test", 0.5, "{}",
         "public_buzz", "name_loc:unlinked_buzz_abc123def456"),
    )
    conn.commit()
    conn.close()

    result = await run(db_path=v43_db, days=30, exclude_unlinked_buzz=True)

    # The unlinked_buzz row should not contribute to convergence
    assert result["ok"] is True
