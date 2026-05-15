"""Integration proof that `process --dry-run` leaves persistent tables untouched."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.entity_resolution import AssetToLead, EntityResolutionStore, ResolutionMethod
from storage.founder_store import FounderProfile, FounderStore
from storage.signal_store import SignalStore
from tests.support.db_snapshot import compare_dry_run


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def seeded_template_db_path() -> str:
    """Create a minimal scratch DB with one pending signal."""
    fd, path = tempfile.mkstemp(suffix="_dry_run_readonly_template.db")
    os.close(fd)

    loop = asyncio.new_event_loop()
    try:

        async def _create() -> None:
            store = SignalStore(db_path=path)
            await store.initialize()
            signal_id = await store.save_signal(
                signal_type="github_trending",
                source_api="github",
                canonical_key="domain:dry-run-readonly.test",
                company_name="Dry Run Readonly Co",
                confidence=0.82,
                raw_data={
                    "description": "Consumer wellness product for families",
                    "url": "https://dry-run-readonly.test",
                },
                detected_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
            )
            async with store.transaction() as conn:
                await conn.execute(
                    "UPDATE signals SET company_id = ? WHERE id = ?",
                    ("company-dry-run-readonly", signal_id),
                )
            await store.save_functional_schema(
                {
                    "company_id": "company-dry-run-readonly",
                    "customer_archetype": "parents",
                    "problem_archetypes": ["wellness"],
                    "schema_confidence": 0.9,
                }
            )
            await store.close()

            entity_store = EntityResolutionStore(db_path=path)
            await entity_store.initialize()
            await entity_store.create_link(
                AssetToLead(
                    asset_id=signal_id,
                    asset_source_type="github_repo",
                    asset_external_id="dry-run-readonly/repo",
                    lead_canonical_key="domain:dry-run-readonly.test",
                    confidence=0.95,
                    resolved_by=ResolutionMethod.DOMAIN_MATCH,
                )
            )
            await entity_store.close()

            founder_store = FounderStore(db_path=path)
            await founder_store.initialize()
            await founder_store.save_founder(
                FounderProfile(
                    name="Readonly Founder",
                    founder_key="linkedin:readonly-founder",
                    canonical_key="domain:dry-run-readonly.test",
                    source_api="linkedin",
                )
            )
            await founder_store.close()

        loop.run_until_complete(_create())
    finally:
        loop.close()

    yield path

    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def scratch_db_path(seeded_template_db_path: str, tmp_path: Path) -> Path:
    target = tmp_path / "signals-dry-run-readonly.sqlite"
    shutil.copy2(seeded_template_db_path, target)
    return target


LANE_PARAMS = [
    pytest.param("baseline", id="baseline"),
    pytest.param("claim_facts", id="claim_facts"),
    pytest.param("entities", id="entities"),
    pytest.param("phase_g_identity_resolution", id="phase_g_identity_resolution"),
    pytest.param("shadow_entity_resolution", id="shadow_entity_resolution"),
    pytest.param("exit_predictor", id="exit_predictor"),
    pytest.param("investor_matching", id="investor_matching"),
    pytest.param("founder_scoring", id="founder_scoring"),
    pytest.param("functional_schema", id="functional_schema"),
    pytest.param("combined_high_risk", id="combined_high_risk"),
]


@pytest.mark.parametrize("lane", LANE_PARAMS)
def test_process_dry_run_preserves_all_persistent_tables(
    lane: str,
    scratch_db_path: Path,
) -> None:
    command = subprocess.list2cmdline(
        [
            sys.executable,
            "run_pipeline.py",
            "process",
            "--dry-run",
            "--disable-gating",
            "--db-path",
            str(scratch_db_path),
        ]
    )

    result = compare_dry_run(
        db_path=scratch_db_path,
        command=command,
        lane=lane,
    )

    assert result.command_returncode == 0, (
        f"lane={lane} exited {result.command_returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.changed_tables == [], (
        f"lane={lane} mutated tables: {result.changed_tables}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
