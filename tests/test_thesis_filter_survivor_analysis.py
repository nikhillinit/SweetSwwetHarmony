"""Tests for the read-only thesis-filter survivor analysis script."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from utils.thesis_filter import DecisionPathCode, RoutingDecision, ThesisFilterResult

from scripts import thesis_filter_survivor_analysis as survivor


def _make_live_shape_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_type TEXT NOT NULL,
            source_api TEXT NOT NULL,
            canonical_key TEXT NOT NULL,
            company_name TEXT,
            confidence REAL NOT NULL,
            raw_data TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            company_id TEXT,
            evidence_family TEXT,
            canonical_key_v2 TEXT,
            evidence_key TEXT
        );
        CREATE TABLE signal_processing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            notion_page_id TEXT,
            processed_at TEXT,
            error_message TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations (version, applied_at)
        VALUES (53, '2026-05-11T00:00:00+00:00');
        """
    )
    rows = [
        (
            "github_spike",
            "github",
            "domain:snack.test",
            "Snack Test",
            0.7,
            {
                "description": (
                    "Direct to consumer organic snack brand with vegan pantry staples"
                )
            },
            "2026-05-11T01:00:00+00:00",
            "2026-05-11T01:00:00+00:00",
            "company-snack",
        ),
        (
            "news",
            "news_api",
            "domain:held.test",
            "Held Test",
            0.5,
            {"description": "Enterprise workflow automation for B2B teams"},
            "2026-05-10T01:00:00+00:00",
            "2026-05-10T01:00:00+00:00",
            "company-held",
        ),
        (
            "rss",
            "rss_feeds",
            "domain:unprocessed.test",
            "Unprocessed Test",
            0.4,
            {"description": "Consumer wellness newsletter"},
            "2026-05-09T01:00:00+00:00",
            "2026-05-09T01:00:00+00:00",
            "company-unprocessed",
        ),
    ]
    for row in rows:
        conn.execute(
            """
            INSERT INTO signals (
                signal_type, source_api, canonical_key, company_name, confidence,
                raw_data, detected_at, created_at, company_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*row[:5], json.dumps(row[5]), *row[6:]),
        )
    conn.execute(
        """
        INSERT INTO signal_processing (
            signal_id, status, created_at, updated_at
        )
        VALUES (1, 'pending', '2026-05-11T01:00:00+00:00', '2026-05-11T01:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO signal_processing (
            signal_id, status, created_at, updated_at
        )
        VALUES (2, 'held', '2026-05-10T01:00:00+00:00', '2026-05-10T01:00:00+00:00')
        """
    )
    conn.commit()
    conn.close()


def test_analyze_uses_processing_join_and_read_only_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    _make_live_shape_db(db_path)

    report = asyncio.run(survivor.analyze_database(db_path))

    assert report["baseline"]["signal_rows"] == 3
    assert report["baseline"]["canonical_keys"] == 3
    assert report["baseline"]["pending_rows"] == 1
    assert report["baseline"]["pending_canonical_keys"] == 1
    assert report["metadata"]["db_open_mode"] == "sqlite_uri_mode_ro"
    assert len(report["canonical_results"]) == 1
    assert report["canonical_results"][0]["canonical_key"] == "domain:snack.test"


def test_canonical_classify_omits_domain_and_skips_llm(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "signals.db"
    _make_live_shape_db(db_path)
    calls: list[dict[str, object]] = []

    async def fake_classify(
        self,
        text: str,
        company_name: str | None = None,
        domain_name: str | None = None,
        skip_llm: bool = False,
    ) -> ThesisFilterResult:
        calls.append(
            {
                "text": text,
                "company_name": company_name,
                "domain_name": domain_name,
                "skip_llm": skip_llm,
            }
        )
        return ThesisFilterResult(
            routing=RoutingDecision.HELD,
            keyword_score=0.1,
            keyword_category="low_fit",
            negative_keywords=["enterprise"],
            llm_skipped=True,
            decision_path_code=DecisionPathCode.HOLD_DEFAULT,
        )

    monkeypatch.setattr(survivor.ThesisFilter, "classify", fake_classify)

    report = asyncio.run(survivor.analyze_database(db_path))

    assert len(calls) == 1
    assert calls[0]["company_name"] == "Snack Test"
    assert calls[0]["domain_name"] is None
    assert calls[0]["skip_llm"] is True
    assert "organic snack brand" in str(calls[0]["text"])
    assert report["metadata"]["domain_name_mode"] == "omitted_for_canonical_parity"
    assert report["metadata"]["llm_calls_made"] == 0
    assert report["canonical_results"][0]["llm_eligible"] is False


def test_report_emits_config_and_matcher_runtime_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    _make_live_shape_db(db_path)

    report = asyncio.run(survivor.analyze_database(db_path))

    assert report["metadata"]["canonical_unit"] == "pending_consolidated_company"
    assert (
        report["metadata"]["canonical_entrypoint"]
        == "ThesisFilter.classify(skip_llm=True)"
    )
    assert "effective_config_snapshot" in report["metadata"]
    assert "hold_threshold" in report["metadata"]["effective_config_snapshot"]
    assert "matcher_runtime" in report["metadata"]
    assert "v2_enablement" in report["metadata"]["matcher_runtime"]
    assert "ml_enablement" in report["metadata"]["matcher_runtime"]


def test_writes_json_artifact_outside_database(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    out_path = tmp_path / "survivor.json"
    _make_live_shape_db(db_path)

    report = asyncio.run(survivor.analyze_database(db_path, out_path=out_path))

    assert out_path.exists()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["baseline"] == report["baseline"]
    assert saved["metadata"]["llm_calls_made"] == 0


def test_script_does_not_import_write_capable_store_or_pipeline() -> None:
    source = Path(survivor.__file__).read_text(encoding="utf-8")

    assert "storage.signal_store" not in source
    assert "workflows.pipeline" not in source
    assert "DiscoveryPipeline" not in source
