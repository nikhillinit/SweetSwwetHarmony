"""Tests for scripts/thesis_activation_sandbox.py."""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

import scripts.thesis_activation_sandbox as sandbox_script
from storage.signal_store import SignalStore


async def _seed_signal(
    store: SignalStore,
    *,
    source_api: str,
    canonical_key: str,
    detected_at: datetime,
) -> int:
    return await store.save_signal(
        signal_type="company_mention",
        source_api=source_api,
        canonical_key=canonical_key,
        company_name=canonical_key,
        confidence=0.7,
        raw_data={"title": canonical_key},
        detected_at=detected_at,
    )


@pytest_asyncio.fixture
async def sandbox_db(tmp_path: Path) -> dict[str, Any]:
    db_path = tmp_path / "signals.db"
    store = SignalStore(str(db_path))
    await store.initialize()
    try:
        base = datetime.now(timezone.utc).replace(microsecond=0)

        hn_signal = await _seed_signal(
            store,
            source_api="hacker_news",
            canonical_key="domain:hn-example.ai",
            detected_at=base,
        )
        arxiv_signal = await _seed_signal(
            store,
            source_api="arxiv",
            canonical_key="domain:arxiv-example.ai",
            detected_at=base - timedelta(minutes=1),
        )
        news_signal = await _seed_signal(
            store,
            source_api="news_api",
            canonical_key="domain:news-example.ai",
            detected_at=base - timedelta(minutes=2),
        )

        await store.save_thesis_classification(
            signal_id=hn_signal,
            canonical_key="domain:hn-example.ai",
            keyword_score=0.0,
            keyword_category="unknown",
        )
        await store.save_thesis_classification(
            signal_id=news_signal,
            canonical_key="domain:news-example.ai",
            keyword_score=0.6,
            keyword_category="consumer",
            thesis_fit_score=0.8,
            category="consumer",
            rationale="LLM fit",
            model="gemini-2.0-flash",
        )
    finally:
        await store.close()

    return {
        "db_path": db_path,
        "signals": {
            "hn": hn_signal,
            "arxiv": arxiv_signal,
            "news": news_signal,
        },
    }


class TestEvaluateActivationSandbox:
    def test_reports_source_specific_pending_state(self, sandbox_db, monkeypatch):
        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
        monkeypatch.delenv("THESIS_SKIP_LLM_BELOW", raising=False)

        report = sandbox_script.evaluate_activation_sandbox(
            sandbox_db["db_path"],
            source_api="hacker_news",
        )

        pending_by_source = {
            row["source_api"]: row for row in report["backlog"]["pending_by_source"]
        }
        assert pending_by_source["hacker_news"]["keyword_only_latest"] == 1
        assert pending_by_source["arxiv"]["missing_thesis"] == 1
        assert pending_by_source["news_api"]["llm_latest"] == 1

        assert report["target_source"]["current_pending_state"]["keyword_only_latest"] == 1
        assert report["current_env"]["THESIS_SKIP_LLM_BELOW_effective"] == pytest.approx(0.2)
        assert any(
            "thesis-classify-batch will not revisit them" in item
            for item in report["observations"]
        )

    def test_scratch_process_reports_fresh_llm_rows(self, sandbox_db, monkeypatch):
        cleanup_targets: list[Path] = []

        async def _fake_run_sandbox_process(scratch_db: Path, source_api: str, batch_size: int):
            conn = sqlite3.connect(str(scratch_db), timeout=5)
            try:
                conn.execute("PRAGMA busy_timeout=5000")
                row = conn.execute(
                    """
                    SELECT s.id, s.canonical_key
                    FROM signals s
                    INNER JOIN signal_processing p ON p.signal_id = s.id
                    WHERE p.status = 'pending' AND s.source_api = ?
                    ORDER BY s.detected_at DESC, s.id DESC
                    LIMIT 1
                    """,
                    (source_api,),
                ).fetchone()
                assert row is not None
                signal_id, canonical_key = int(row[0]), str(row[1])
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO thesis_classifications (
                        signal_id, canonical_key,
                        keyword_score, keyword_category,
                        thesis_fit_score, category, rationale,
                        model, classified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id,
                        canonical_key,
                        0.0,
                        "unknown",
                        0.83,
                        "consumer",
                        "scratch proof",
                        "gemini-2.0-flash",
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO confidence_ledger (
                        execution_id, canonical_key, company_id,
                        evaluation_origin, is_dry_run, breakdown_kind,
                        gate_score, reported_score, base_score,
                        multi_source_boost, convergence_boost, founder_boost,
                        velocity_boost, enrichment_boost, community_sentiment_boost,
                        recalibration_factor, policy_version, breakdown_schema_version,
                        signals_contributing, sources_checked,
                        decision, verification_status, reason,
                        breakdown_json, details_json, signal_ids_json,
                        routing_config_json, evaluated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "scratch-proof",
                        canonical_key,
                        None,
                        "pipeline",
                        1,
                        "normal",
                        0.8,
                        0.8,
                        0.8,
                        1.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        "test-policy",
                        "1.0",
                        1,
                        1,
                        "needs_review",
                        "single_source",
                        "scratch proof",
                        json.dumps({"overall": 0.8, "base_score": 0.8, "signals_contributing": 1, "sources_checked": 1}),
                        json.dumps([]),
                        json.dumps([signal_id]),
                        json.dumps({"high_threshold": 0.8}),
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            return {
                "ok": True,
                "result": {
                    "processed": 1,
                    "auto_push": 0,
                    "needs_review": 1,
                    "held": 0,
                    "rejected": 0,
                    "prospects_created": 0,
                    "prospects_updated": 0,
                    "prospects_skipped": 1,
                },
            }

        monkeypatch.setenv("LLM_THESIS_MODE", "shadow")
        monkeypatch.setattr(sandbox_script, "_run_sandbox_process", _fake_run_sandbox_process)
        real_rmtree = shutil.rmtree

        def _fake_rmtree(path, *args, **kwargs):
            cleanup_targets.append(Path(path))
            raise PermissionError("simulated open handle")

        monkeypatch.setattr(sandbox_script.shutil, "rmtree", _fake_rmtree)

        report = sandbox_script.evaluate_activation_sandbox(
            sandbox_db["db_path"],
            source_api="hacker_news",
            batch_size=1,
            execute_process=True,
        )

        sandbox_run = report["sandbox_run"]
        assert sandbox_run["target_signal_ids"] == [sandbox_db["signals"]["hn"]]
        assert sandbox_run["before"]["latest_thesis_state"]["llm_latest"] == 0
        assert sandbox_run["after"]["latest_thesis_state"]["llm_latest"] == 1
        assert sandbox_run["proof"]["new_llm_rows"] == 1
        assert sandbox_run["proof"]["new_confidence_ledger_rows"] == 1
        assert sandbox_run["proof"]["llm_fired"] is True
        assert sandbox_run["cleanup_status"] == "retained_due_to_open_handle"
        assert cleanup_targets

        for path in cleanup_targets:
            real_rmtree(path, ignore_errors=True)
