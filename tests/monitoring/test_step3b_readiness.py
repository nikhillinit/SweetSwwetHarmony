"""
Tests for monitoring/step3b_readiness.py -- Step 3B activation readiness gate.

Covers:
- All-pass -> ready
- Multi-source below threshold -> blocked
- Canary fail -> blocked
- Canary degraded -> ready (degraded is acceptable)
- Phase G blocked -> blocked
- No canary data -> blocked
- Custom threshold parameter
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = SignalStore(db_path=path)
    await s.initialize()
    yield s
    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


_run_counter = 0


async def _insert_canary_run(store, verdict="pass", pass_rate=1.0, created_at=None):
    """Insert a canary_runs row for testing."""
    global _run_counter
    _run_counter += 1
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    db = store._db
    run_id = f"s3b-run-{_run_counter}-{verdict}"
    await db.execute(
        "INSERT OR IGNORE INTO run_history (id, run_type, status, started_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, "canary", "completed", created_at, created_at),
    )
    await db.execute(
        """INSERT INTO canary_runs
           (run_id, golden_set_size, golden_set_hash, total_scored, passed, failed,
            skipped, pass_rate, verdict, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            10, "abc123", 10,
            int(pass_rate * 10), int((1 - pass_rate) * 10), 0,
            pass_rate, verdict, created_at,
        ),
    )
    await db.commit()


async def _insert_company_file(store, company_id, source_apis, status="promoted"):
    """Insert a company_files row for testing."""
    now = datetime.now(timezone.utc).isoformat()
    db = store._db
    await db.execute(
        """INSERT INTO company_files
           (company_id, company_name, canonical_key, status, source_apis,
            first_seen_at, last_seen_at, promoted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            f"Company {company_id}",
            f"domain:{company_id}.com",
            status,
            json.dumps(source_apis),
            now,
            now,
            now if status == "promoted" else None,
        ),
    )
    await db.commit()


# =============================================================================
# TESTS
# =============================================================================

class TestStep3BReadiness:

    @pytest.mark.asyncio
    async def test_ready_when_all_predicates_pass(self, store):
        """All 3 predicates met -> ready."""
        from monitoring.step3b_readiness import check_step3b_readiness

        # Predicate 1: 5 multi-source promoted files
        for i in range(5):
            await _insert_company_file(store, f"co-{i}", ["github", "sec_edgar"])

        # Predicate 2: canary pass
        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        # Predicate 3: Phase G tables exist (created by initialize()),
        # blocking_index empty gives "warn" which is != "ready" —
        # but with 0 merge suggestions and 0 orphans the remaining checks pass.
        # Blocking index empty => Phase G verdict is "warn", not "ready".
        # So we need to insert at least one blocking index row.
        db = store._db
        await db.execute(
            "INSERT INTO entity_blocking_index (blocking_token, token_type, entity_id, alias_key) VALUES (?, ?, ?, ?)",
            ("testtoken", "first", "ent-1", "alias-1"),
        )
        await db.commit()

        result = await check_step3b_readiness(store)
        assert result.verdict == "ready"
        assert result.can_proceed is True
        assert result.blockers == []
        assert result.metrics["multi_source_promoted"] == 5

    @pytest.mark.asyncio
    async def test_blocked_insufficient_multi_source(self, store):
        """Only 2 multi-source files (need 5) -> blocked."""
        from monitoring.step3b_readiness import check_step3b_readiness

        # 2 multi-source + 3 single-source
        await _insert_company_file(store, "co-0", ["github", "sec_edgar"])
        await _insert_company_file(store, "co-1", ["news_api", "hacker_news"])
        await _insert_company_file(store, "co-2", ["github"])
        await _insert_company_file(store, "co-3", ["sec_edgar"])
        await _insert_company_file(store, "co-4", ["news_api"])

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        result = await check_step3b_readiness(store)
        assert result.verdict == "blocked"
        assert result.metrics["multi_source_promoted"] == 2
        assert any("multi-source" in b.lower() for b in result.blockers)

    @pytest.mark.asyncio
    async def test_blocked_canary_fail(self, store):
        """Canary verdict 'fail' -> blocked."""
        from monitoring.step3b_readiness import check_step3b_readiness

        for i in range(5):
            await _insert_company_file(store, f"co-{i}", ["github", "sec_edgar"])

        await _insert_canary_run(store, verdict="fail", pass_rate=0.4)

        result = await check_step3b_readiness(store)
        assert result.verdict == "blocked"
        assert any("canary" in b.lower() for b in result.blockers)

    @pytest.mark.asyncio
    async def test_canary_degraded_is_acceptable(self, store):
        """Canary 'degraded' is acceptable for Step 3B (not a blocker)."""
        from monitoring.step3b_readiness import check_step3b_readiness

        for i in range(5):
            await _insert_company_file(store, f"co-{i}", ["github", "sec_edgar"])

        await _insert_canary_run(store, verdict="degraded", pass_rate=0.7)

        # Phase G: insert blocking index row so it reports "ready"
        db = store._db
        await db.execute(
            "INSERT INTO entity_blocking_index (blocking_token, token_type, entity_id, alias_key) VALUES (?, ?, ?, ?)",
            ("testtoken", "first", "ent-1", "alias-1"),
        )
        await db.commit()

        result = await check_step3b_readiness(store)
        # No canary blocker (degraded is acceptable)
        canary_blockers = [b for b in result.blockers if "canary" in b.lower()]
        assert canary_blockers == []

    @pytest.mark.asyncio
    async def test_blocked_no_canary_data(self, store):
        """No canary runs at all -> blocked."""
        from monitoring.step3b_readiness import check_step3b_readiness

        for i in range(5):
            await _insert_company_file(store, f"co-{i}", ["github", "sec_edgar"])

        result = await check_step3b_readiness(store)
        assert result.verdict == "blocked"
        assert any("no canary" in b.lower() for b in result.blockers)

    @pytest.mark.asyncio
    async def test_blocked_phase_g_not_ready(self, store):
        """Phase G tables missing required data -> blocked."""
        from monitoring.step3b_readiness import check_step3b_readiness

        for i in range(5):
            await _insert_company_file(store, f"co-{i}", ["github", "sec_edgar"])
        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        # Phase G blocking_index is empty -> verdict "warn" (not "ready")
        result = await check_step3b_readiness(store)
        assert result.verdict == "blocked"
        assert any("phase g" in b.lower() for b in result.blockers)

    @pytest.mark.asyncio
    async def test_custom_threshold(self, store):
        """Custom multi_source_threshold works."""
        from monitoring.step3b_readiness import check_step3b_readiness

        # Only 2 multi-source files, but threshold is 2
        await _insert_company_file(store, "co-0", ["github", "sec_edgar"])
        await _insert_company_file(store, "co-1", ["news_api", "hacker_news"])

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        db = store._db
        await db.execute(
            "INSERT INTO entity_blocking_index (blocking_token, token_type, entity_id, alias_key) VALUES (?, ?, ?, ?)",
            ("testtoken", "first", "ent-1", "alias-1"),
        )
        await db.commit()

        result = await check_step3b_readiness(store, multi_source_threshold=2)
        assert result.verdict == "ready"
        assert result.metrics["multi_source_threshold"] == 2

    @pytest.mark.asyncio
    async def test_thin_files_not_counted(self, store):
        """Only promoted files count, not thin files."""
        from monitoring.step3b_readiness import check_step3b_readiness

        # 3 promoted multi-source + 5 thin multi-source
        for i in range(3):
            await _insert_company_file(store, f"promoted-{i}", ["github", "sec_edgar"], status="promoted")
        for i in range(5):
            await _insert_company_file(store, f"thin-{i}", ["github", "news_api"], status="thin")

        await _insert_canary_run(store, verdict="pass", pass_rate=1.0)

        result = await check_step3b_readiness(store)
        assert result.metrics["multi_source_promoted"] == 3
        assert result.verdict == "blocked"

    @pytest.mark.asyncio
    async def test_to_dict_structure(self, store):
        """to_dict() returns expected keys."""
        from monitoring.step3b_readiness import check_step3b_readiness

        result = await check_step3b_readiness(store)
        d = result.to_dict()
        assert "verdict" in d
        assert "blockers" in d
        assert "can_proceed" in d
        assert "metrics" in d
        assert "checked_at" in d
        assert isinstance(d["blockers"], list)
        assert isinstance(d["metrics"], dict)

    @pytest.mark.asyncio
    async def test_multiple_blockers_reported(self, store):
        """All failing predicates appear in blockers list."""
        from monitoring.step3b_readiness import check_step3b_readiness

        # No multi-source files, no canary, Phase G warn
        result = await check_step3b_readiness(store)
        assert result.verdict == "blocked"
        # At least 3 blockers: multi-source, canary, phase G
        assert len(result.blockers) >= 3
