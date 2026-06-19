"""
Tests for Task 11: Pipeline wiring — identity resolution in save_signal(),
merge cascade propagation, thin file upsert, promotion sweep, identity gate,
and Phase G table validation.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from unittest import mock

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from storage.signal_store import SignalStore
from storage.entity_identity_store import EntityIdentityStore, StrongKeyBinding
from storage.identity_gate import check_identity_integrity, IdentityMigrationRequired
from storage.merge_cascade import cascade_merge
from workflows.thin_file_manager import (
    upsert_company_file,
    run_promotion_sweep,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest_asyncio.fixture
async def store():
    """Fresh SignalStore with all migrations applied."""
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


@pytest_asyncio.fixture
async def identity_store(store):
    """EntityIdentityStore wrapping the test SignalStore."""
    return EntityIdentityStore(store)


@pytest_asyncio.fixture
async def wired_store():
    """SignalStore with identity_store and use_thin_files wired up."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    s = SignalStore(db_path=path, use_thin_files=True)
    await s.initialize()

    # Create identity store and wire it in
    id_store = EntityIdentityStore(s)
    s._identity_store = id_store

    yield s

    await s.close()
    try:
        os.unlink(path)
    except OSError:
        pass


# =============================================================================
# SAVE_SIGNAL IDENTITY RESOLUTION
# =============================================================================

class TestSaveSignalIdentityResolution:
    """save_signal() resolves company_id via identity store."""

    @pytest.mark.asyncio
    async def test_save_signal_without_identity_store(self, store):
        """save_signal() without identity store sets company_id=NULL."""
        signal_id = await store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:test.com",
            confidence=0.8,
            raw_data={"test": True},
        )

        cursor = await store._db.execute(
            "SELECT company_id FROM signals WHERE id = ?", (signal_id,)
        )
        row = await cursor.fetchone()
        assert row[0] is None

    @pytest.mark.asyncio
    async def test_save_signal_with_identity_store_new_key(self, wired_store):
        """save_signal() with identity store generates new company_id."""
        signal_id = await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:newcompany.com",
            confidence=0.8,
            raw_data={"test": True},
        )

        cursor = await wired_store._db.execute(
            "SELECT company_id FROM signals WHERE id = ?", (signal_id,)
        )
        row = await cursor.fetchone()
        assert row[0] is not None
        assert len(row[0]) == 16  # SHA256[:16]

        # Verify consistent with entity_id_for_seed
        expected_id = EntityIdentityStore.entity_id_for_seed("domain:newcompany.com")
        assert row[0] == expected_id

    @pytest.mark.asyncio
    async def test_save_signal_registers_strong_key_binding(self, wired_store):
        """save_signal() registers a strong key binding in entity_aliases."""
        signal_id = await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:binding.com",
            confidence=0.8,
            raw_data={"test": True},
        )

        cursor = await wired_store._db.execute(
            "SELECT entity_id, source_signal_id, source_key FROM entity_aliases "
            "WHERE strong_key = ?",
            ("domain:binding.com",),
        )
        row = await cursor.fetchone()
        assert row is not None
        expected_id = EntityIdentityStore.entity_id_for_seed("domain:binding.com")
        assert row[0] == expected_id
        assert row[1] == signal_id
        assert row[2] == "github"

    @pytest.mark.asyncio
    async def test_save_signal_reuses_existing_company_id(self, wired_store):
        """Second signal with same canonical_key gets same company_id."""
        id1 = await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:same.com",
            confidence=0.8,
            raw_data={"test": True},
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        id2 = await wired_store.save_signal(
            signal_type="test",
            source_api="sec_edgar",
            canonical_key="domain:same.com",
            confidence=0.7,
            raw_data={"test": True},
            detected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        cursor = await wired_store._db.execute(
            "SELECT company_id FROM signals WHERE id IN (?, ?) ORDER BY id",
            (id1, id2),
        )
        rows = await cursor.fetchall()
        assert rows[0][0] == rows[1][0]
        assert rows[0][0] is not None

    @pytest.mark.asyncio
    async def test_save_signal_uses_transaction_immediate(self, wired_store):
        """save_signal() with identity store uses transaction_immediate."""
        original_tx_imm = wired_store.transaction_immediate
        call_count = 0

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def tracking_tx_imm():
            nonlocal call_count
            call_count += 1
            async with original_tx_imm() as conn:
                yield conn

        wired_store.transaction_immediate = tracking_tx_imm

        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:tx.com",
            confidence=0.8,
            raw_data={},
        )

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_save_signal_fail_fast_guard(self, store):
        """save_signal() raises when use_thin_files but no identity_store."""
        store._use_thin_files = True
        store._identity_store = None

        with pytest.raises(RuntimeError, match="use_thin_files requires"):
            await store.save_signal(
                signal_type="test",
                source_api="github",
                canonical_key="domain:test.com",
                confidence=0.8,
                raw_data={},
            )


# =============================================================================
# THIN FILE UPSERT FROM SAVE_SIGNAL
# =============================================================================

class TestSaveSignalThinFileUpsert:
    """save_signal() upserts company_files when use_thin_files=True."""

    @pytest.mark.asyncio
    async def test_save_signal_creates_company_file(self, wired_store):
        """First signal creates a thin company file."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:thin.com",
            confidence=0.8,
            raw_data={},
            company_name="Thin Co",
        )

        cursor = await wired_store._db.execute(
            "SELECT company_id, company_name, status, source_apis "
            "FROM company_files WHERE canonical_key = ?",
            ("domain:thin.com",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[1] == "Thin Co"
        assert row[2] == "thin"
        assert json.loads(row[3]) == ["github"]

    @pytest.mark.asyncio
    async def test_save_signal_appends_source(self, wired_store):
        """Second signal from different source appends to source_apis."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:multi.com",
            confidence=0.8,
            raw_data={},
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await wired_store.save_signal(
            signal_type="test",
            source_api="sec_edgar",
            canonical_key="domain:multi.com",
            confidence=0.7,
            raw_data={},
            detected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        cursor = await wired_store._db.execute(
            "SELECT source_apis FROM company_files WHERE canonical_key = ?",
            ("domain:multi.com",),
        )
        row = await cursor.fetchone()
        sources = json.loads(row[0])
        assert "github" in sources
        assert "sec_edgar" in sources

    @pytest.mark.asyncio
    async def test_save_signal_no_thin_file_when_disabled(self, store):
        """save_signal() without use_thin_files does not create company_file."""
        # Wire identity store but NOT thin files
        id_store = EntityIdentityStore(store)
        store._identity_store = id_store

        await store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:nonthin.com",
            confidence=0.8,
            raw_data={},
        )

        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM company_files"
        )
        count = (await cursor.fetchone())[0]
        assert count == 0


# =============================================================================
# MERGE CASCADE FROM SAVE_SIGNAL
# =============================================================================

class TestSaveSignalMergeCascade:
    """save_signal() merge cascade wiring when upsert_strong_key_bindings returns merges."""

    @pytest.mark.asyncio
    async def test_merge_cascade_wiring_invoked(self, wired_store):
        """Verify cascade_merge is called when upsert_strong_key_bindings returns pairs."""
        from unittest.mock import AsyncMock, patch

        # Create two company files to merge
        entity_a = "aaaa000000000000"
        entity_b = "bbbb000000000000"

        await wired_store._db.execute(
            """INSERT INTO company_files
               (company_id, company_name, canonical_key, status,
                source_apis, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, 'thin', ?, ?, ?)""",
            (entity_a, "Co A", "domain:a.com",
             json.dumps(["github"]),
             "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00"),
        )
        await wired_store._db.execute(
            """INSERT INTO company_files
               (company_id, company_name, canonical_key, status,
                source_apis, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, 'thin', ?, ?, ?)""",
            (entity_b, "Co B", "domain:b.com",
             json.dumps(["sec_edgar"]),
             "2026-01-02T00:00:00+00:00",
             "2026-01-02T00:00:00+00:00"),
        )
        await wired_store._db.commit()

        # Mock upsert_strong_key_bindings to return a merge pair
        original_upsert = wired_store._identity_store.upsert_strong_key_bindings

        async def fake_upsert(bindings, tx):
            # Call original to actually register the binding
            await original_upsert(bindings, tx)
            # Return a fake merge pair
            return [(entity_b, entity_a)]  # loser=b, winner=a

        wired_store._identity_store.upsert_strong_key_bindings = fake_upsert

        signal_id = await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:merge-test.com",
            confidence=0.8,
            raw_data={},
        )

        # Verify cascade_merge was executed — audit log should have entry
        cursor = await wired_store._db.execute(
            "SELECT action_type, entity_id FROM audit_log "
            "WHERE action_type = 'cascade_merge'"
        )
        audit_row = await cursor.fetchone()
        assert audit_row is not None
        assert audit_row[1] == entity_a  # winner

        # Loser's company_file should be deleted (merged into winner)
        cursor = await wired_store._db.execute(
            "SELECT COUNT(*) FROM company_files WHERE company_id = ?",
            (entity_b,),
        )
        assert (await cursor.fetchone())[0] == 0

    @pytest.mark.asyncio
    async def test_signal_company_id_updated_on_merge(self, wired_store):
        """If signal's company_id is the loser, it gets updated to winner."""
        from unittest.mock import AsyncMock

        entity_a = "aaaa000000000000"  # winner (lexmin)
        entity_b = "bbbb000000000000"

        # Mock upsert to return merge pair
        original_upsert = wired_store._identity_store.upsert_strong_key_bindings

        async def fake_upsert(bindings, tx):
            await original_upsert(bindings, tx)
            return [(entity_b, entity_a)]

        # Mock lookup to return entity_b (the loser)
        original_lookup = wired_store._identity_store.lookup_strong_keys

        async def fake_lookup(keys):
            return {}  # Force new entity creation

        wired_store._identity_store.upsert_strong_key_bindings = fake_upsert

        # Also mock entity_id_for_seed to return entity_b
        from storage.entity_identity_store import EntityIdentityStore
        original_seed = EntityIdentityStore.entity_id_for_seed

        with mock.patch.object(
            EntityIdentityStore, 'entity_id_for_seed',
            staticmethod(lambda key: entity_b),
        ):
            wired_store._identity_store.lookup_strong_keys = fake_lookup

            signal_id = await wired_store.save_signal(
                signal_type="test",
                source_api="github",
                canonical_key="domain:loser-signal.com",
                confidence=0.8,
                raw_data={},
            )

        # Signal should have winner's ID (updated from loser)
        cursor = await wired_store._db.execute(
            "SELECT company_id FROM signals WHERE id = ?", (signal_id,)
        )
        row = await cursor.fetchone()
        assert row[0] == entity_a  # Updated to winner

    @pytest.mark.asyncio
    async def test_no_merge_when_no_collision(self, wired_store):
        """Normal save_signal with no collision produces no audit merge entry."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:no-collision.com",
            confidence=0.8,
            raw_data={},
        )

        cursor = await wired_store._db.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action_type = 'cascade_merge'"
        )
        assert (await cursor.fetchone())[0] == 0


# =============================================================================
# IDENTITY GATE
# =============================================================================

class TestIdentityGate:
    """Identity gate blocks pipeline when signals have NULL company_id."""

    @pytest.mark.asyncio
    async def test_gate_passes_empty_db(self, store):
        """Identity gate passes on empty database (no signals)."""
        await check_identity_integrity(store)

    @pytest.mark.asyncio
    async def test_gate_passes_all_populated(self, wired_store):
        """Identity gate passes when all signals have company_id."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:test.com",
            confidence=0.8,
            raw_data={},
        )
        await check_identity_integrity(wired_store)

    @pytest.mark.asyncio
    async def test_gate_blocks_null_company_id(self, store):
        """Identity gate raises when signals have NULL company_id."""
        # Insert signal WITHOUT company_id
        await store._db.execute(
            """INSERT INTO signals
               (signal_type, source_api, canonical_key, company_name,
                confidence, raw_data, detected_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test", "github", "domain:null.com", "Null Co",
             0.8, "{}", "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00"),
        )
        await store._db.commit()

        with pytest.raises(IdentityMigrationRequired, match="NULL company_id"):
            await check_identity_integrity(store)


# =============================================================================
# PHASE G TABLE VALIDATION
# =============================================================================

class TestPhaseGTableValidation:
    """Pipeline validates Phase G tables exist when thin files enabled."""

    @pytest.mark.asyncio
    async def test_validation_passes_with_migrations(self):
        """Validation passes when all Phase G tables exist (via migrations)."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        try:
            config = PipelineConfig(
                db_path=path,
                use_thin_files=True,
                warmup_suppression_cache=False,
            )
            pipeline = DiscoveryPipeline(config)
            # initialize() should not raise — Phase G tables exist from migrations
            await pipeline.initialize()
            await pipeline.close()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_missing_tables_message_uses_configured_db_path(self):
        """Validation failure should not suggest repo-local signals.db."""
        from workflows.pipeline import DiscoveryPipeline, PipelineConfig

        class _Cursor:
            async def fetchall(self):
                return []

        class _Db:
            async def execute(self, query, params):
                return _Cursor()

        class _Store:
            _db = _Db()

        config = PipelineConfig(
            db_path=":memory:",
            use_thin_files=True,
            warmup_suppression_cache=False,
        )
        pipeline = DiscoveryPipeline(config)
        pipeline._store = _Store()

        with pytest.raises(RuntimeError) as exc_info:
            await pipeline._validate_phase_g_tables()

        msg = str(exc_info.value)
        assert '--db "$DISCOVERY_DB_PATH"' in msg
        assert "--db signals.db" not in msg


# =============================================================================
# PROMOTION SWEEP FROM PIPELINE
# =============================================================================

class TestPromotionSweepWiring:
    """Promotion sweep runs after collection phase."""

    @pytest.mark.asyncio
    async def test_promotion_sweep_promotes_multi_source(self, wired_store):
        """Signals from 2+ sources → thin file promoted → ReviewItem created."""
        company_id = EntityIdentityStore.entity_id_for_seed("domain:promo.com")

        # Signal 1: github
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:promo.com",
            confidence=0.8,
            raw_data={},
            company_name="Promo Co",
            detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        # Signal 2: sec_edgar (triggers multi-source)
        await wired_store.save_signal(
            signal_type="test",
            source_api="sec_edgar",
            canonical_key="domain:promo.com",
            confidence=0.7,
            raw_data={},
            company_name="Promo Co",
            detected_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        # Run promotion sweep
        promoted, _, _ = await run_promotion_sweep(wired_store)
        assert promoted == 1

        # Verify company file promoted
        cursor = await wired_store._db.execute(
            "SELECT status FROM company_files WHERE company_id = ?",
            (company_id,),
        )
        row = await cursor.fetchone()
        assert row[0] == "promoted"

        # Verify ReviewItem created
        cursor = await wired_store._db.execute(
            "SELECT status, evidence_bundle FROM review_items WHERE company_id = ?",
            (company_id,),
        )
        review = await cursor.fetchone()
        assert review is not None
        assert review[0] == "pending"
        bundle = json.loads(review[1])
        assert len(bundle["signal_ids"]) == 2

    @pytest.mark.asyncio
    async def test_promotion_sweep_single_source_no_promotion(self, wired_store):
        """Single non-trusted source → thin file stays thin."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:nopromo.com",
            confidence=0.8,
            raw_data={},
        )

        promoted, _, _ = await run_promotion_sweep(wired_store)
        assert promoted == 0

    @pytest.mark.asyncio
    async def test_promotion_sweep_trusted_source_promotes(self, wired_store):
        """Trusted source (sec_edgar) → single-source promotion."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="sec_edgar",
            canonical_key="domain:trusted.com",
            confidence=0.8,
            raw_data={},
        )

        promoted, _, _ = await run_promotion_sweep(wired_store)
        assert promoted == 1


# =============================================================================
# PIPELINE CONFIG
# =============================================================================

class TestPipelineConfigThinFiles:
    """PipelineConfig thin file flag."""

    def test_thin_files_default_disabled(self):
        from workflows.pipeline import PipelineConfig

        config = PipelineConfig()
        assert config.use_thin_files is False

    def test_thin_files_from_env(self):
        from workflows.pipeline import PipelineConfig

        with mock.patch.dict(os.environ, {"USE_THIN_FILES": "true"}, clear=False):
            config = PipelineConfig.from_env()
            assert config.use_thin_files is True

    def test_thin_files_env_false(self):
        from workflows.pipeline import PipelineConfig

        with mock.patch.dict(os.environ, {"USE_THIN_FILES": "false"}, clear=False):
            config = PipelineConfig.from_env()
            assert config.use_thin_files is False


# =============================================================================
# STORED SIGNAL COMPANY_ID
# =============================================================================

class TestStoredSignalCompanyId:
    """StoredSignal includes company_id from _row_to_signal."""

    @pytest.mark.asyncio
    async def test_get_signal_includes_company_id(self, wired_store):
        """get_signal() returns StoredSignal with company_id populated."""
        signal_id = await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:stored.com",
            confidence=0.8,
            raw_data={"key": "val"},
        )

        signal = await wired_store.get_signal(signal_id)
        assert signal is not None
        expected = EntityIdentityStore.entity_id_for_seed("domain:stored.com")
        assert signal.company_id == expected

    @pytest.mark.asyncio
    async def test_get_pending_signals_includes_company_id(self, wired_store):
        """get_pending_signals() returns signals with company_id."""
        await wired_store.save_signal(
            signal_type="test",
            source_api="github",
            canonical_key="domain:pending.com",
            confidence=0.8,
            raw_data={},
        )

        pending = await wired_store.get_pending_signals()
        assert len(pending) == 1
        expected = EntityIdentityStore.entity_id_for_seed("domain:pending.com")
        assert pending[0].company_id == expected
