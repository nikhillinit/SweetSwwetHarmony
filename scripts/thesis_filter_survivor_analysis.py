#!/usr/bin/env python3
"""Read-only survivor analysis for the active thesis-filter path."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.signal_consolidator import SignalConsolidator
from utils.thesis_filter import ThesisFilter, ThesisFilterConfig


CANONICAL_UNIT = "pending_consolidated_company"
CANONICAL_ENTRYPOINT = "ThesisFilter.classify(skip_llm=True)"
DOMAIN_NAME_MODE = "omitted_for_canonical_parity"


@dataclass
class PendingSignal:
    """Minimal StoredSignal-compatible row for read-only analysis."""

    id: int
    signal_type: str
    source_api: str
    canonical_key: str
    company_name: Optional[str]
    confidence: float
    raw_data: dict[str, Any]
    detected_at: datetime
    created_at: datetime
    company_id: Optional[str] = None
    processing_status: Optional[str] = None
    notion_page_id: Optional[str] = None
    processed_at: Optional[datetime] = None
    error_message: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_only_uri(db_path: Path) -> str:
    return "file:" + db_path.resolve().as_posix() + "?mode=ro"


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_read_only_uri(db_path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def _fetch_counts(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    rows = conn.execute(sql).fetchall()
    return {str(row[0] if row[0] is not None else "missing"): int(row[1]) for row in rows}


def _baseline(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "signal_rows": int(_fetch_scalar(conn, "SELECT COUNT(*) FROM signals")),
        "canonical_keys": int(
            _fetch_scalar(conn, "SELECT COUNT(DISTINCT canonical_key) FROM signals")
        ),
        "pending_rows": int(
            _fetch_scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM signals s
                INNER JOIN signal_processing p ON s.id = p.signal_id
                WHERE p.status = ?
                """,
                ("pending",),
            )
        ),
        "pending_canonical_keys": int(
            _fetch_scalar(
                conn,
                """
                SELECT COUNT(DISTINCT s.canonical_key)
                FROM signals s
                INNER JOIN signal_processing p ON s.id = p.signal_id
                WHERE p.status = ?
                """,
                ("pending",),
            )
        ),
        "user_version": int(_fetch_scalar(conn, "PRAGMA user_version")),
        "max_schema_migrations_version": _fetch_scalar(
            conn, "SELECT MAX(version) FROM schema_migrations"
        ),
    }


def _whole_baseline_census(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "status_counts": _fetch_counts(
            conn,
            """
            SELECT COALESCE(p.status, 'missing_processing_row'), COUNT(*)
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            GROUP BY COALESCE(p.status, 'missing_processing_row')
            ORDER BY 1
            """,
        ),
        "source_api_counts": _fetch_counts(
            conn,
            """
            SELECT source_api, COUNT(*)
            FROM signals
            GROUP BY source_api
            ORDER BY COUNT(*) DESC, source_api ASC
            """,
        ),
        "signal_type_counts": _fetch_counts(
            conn,
            """
            SELECT signal_type, COUNT(*)
            FROM signals
            GROUP BY signal_type
            ORDER BY COUNT(*) DESC, signal_type ASC
            """,
        ),
    }


def _fetch_pending_signals(conn: sqlite3.Connection) -> list[PendingSignal]:
    rows = conn.execute(
        """
        SELECT
            s.id, s.signal_type, s.source_api, s.canonical_key,
            s.company_name, s.confidence, s.raw_data,
            s.detected_at, s.created_at, s.company_id,
            p.status, p.notion_page_id, p.processed_at, p.error_message
        FROM signals s
        INNER JOIN signal_processing p ON s.id = p.signal_id
        WHERE p.status = 'pending'
        ORDER BY s.detected_at DESC
        """
    ).fetchall()

    signals: list[PendingSignal] = []
    for row in rows:
        signals.append(
            PendingSignal(
                id=row["id"],
                signal_type=row["signal_type"],
                source_api=row["source_api"],
                canonical_key=row["canonical_key"],
                company_name=row["company_name"],
                confidence=float(row["confidence"]),
                raw_data=json.loads(row["raw_data"]),
                detected_at=_parse_datetime(row["detected_at"]),
                created_at=_parse_datetime(row["created_at"]),
                company_id=row["company_id"],
                processing_status=row["status"],
                notion_page_id=row["notion_page_id"],
                processed_at=(
                    _parse_datetime(row["processed_at"]) if row["processed_at"] else None
                ),
                error_message=row["error_message"],
            )
        )
    return signals


def _group_by_canonical_key(signals: list[PendingSignal]) -> dict[str, list[PendingSignal]]:
    grouped: dict[str, list[PendingSignal]] = {}
    for signal in signals:
        grouped.setdefault(signal.canonical_key, []).append(signal)
    return grouped


def _effective_config_snapshot(config: ThesisFilterConfig) -> dict[str, Any]:
    return asdict(config)


def _build_filter_config(
    thesis_hold_threshold: Optional[float] = None,
) -> ThesisFilterConfig:
    config = ThesisFilterConfig.from_env()
    if thesis_hold_threshold is not None and thesis_hold_threshold != 0.3:
        config.hold_threshold = thesis_hold_threshold
    return config


def _matcher_runtime_snapshot(thesis_filter: ThesisFilter) -> dict[str, Any]:
    matcher = getattr(thesis_filter, "_keyword_matcher", None)
    controls = getattr(matcher, "_controls", None)
    if controls is None:
        return {"v2_enablement": None, "ml_enablement": None}
    return {
        "v2_enablement": getattr(controls, "v2_enablement", None),
        "ml_enablement": getattr(controls, "ml_enablement", None),
        "policy_loader_mode": getattr(controls, "policy_loader_mode", None),
        "v2_execution_enabled": getattr(controls, "v2_execution_enabled", None),
    }


def _canonical_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    routing = Counter(item["routing"] for item in results)
    path_codes = Counter(item["decision_path_code"] for item in results)
    negative_keywords: Counter[str] = Counter()
    llm_eligible = 0
    for item in results:
        negative_keywords.update(item.get("negative_keywords", []))
        if item.get("llm_eligible"):
            llm_eligible += 1

    return {
        "canonical_companies": len(results),
        "routing_counts": dict(sorted(routing.items())),
        "decision_path_code_counts": dict(sorted(path_codes.items())),
        "llm_eligible_count": llm_eligible,
        "top_negative_keywords": dict(negative_keywords.most_common(20)),
    }


def _rejection_motifs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    motifs: Counter[tuple[str, str, str]] = Counter()
    examples: dict[tuple[str, str, str], list[str]] = {}
    for item in results:
        if item["routing"] not in {"held", "rejected"}:
            continue
        negative = ",".join(item.get("negative_keywords", [])[:3]) or "no_negative_keywords"
        key = (item["routing"], item["decision_path_code"], negative)
        motifs[key] += 1
        examples.setdefault(key, []).append(item["canonical_key"])

    output: list[dict[str, Any]] = []
    for (routing, path_code, negative), count in motifs.most_common(20):
        output.append(
            {
                "routing": routing,
                "decision_path_code": path_code,
                "negative_keyword_cluster": negative,
                "count": count,
                "example_canonical_keys": examples[(routing, path_code, negative)][:5],
            }
        )
    return output


async def _classify_canonical_groups(
    pending_signals: list[PendingSignal],
    config: ThesisFilterConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    consolidator = SignalConsolidator()
    thesis_filter = ThesisFilter(config)
    matcher_runtime = _matcher_runtime_snapshot(thesis_filter)

    results: list[dict[str, Any]] = []
    for canonical_key, signals in _group_by_canonical_key(pending_signals).items():
        consolidated = consolidator.consolidate(signals)
        description = (
            " ".join(consolidated.descriptions) if consolidated.descriptions else ""
        )
        result = await thesis_filter.classify(
            description,
            company_name=consolidated.company_name,
            skip_llm=True,
        )
        if getattr(thesis_filter, "_llm_classifier", None) is not None:
            raise RuntimeError("LLM classifier was initialized during survivor analysis")

        result_dict = result.to_dict()
        llm_eligible = result.keyword_score >= config.skip_llm_if_keyword_below
        results.append(
            {
                "canonical_key": canonical_key,
                "company_name": consolidated.company_name,
                "signal_ids": list(consolidated.contributing_signal_ids),
                "signal_count": consolidated.signal_count,
                "source_apis": sorted(consolidated.source_apis),
                "signal_types": sorted(consolidated.signal_types),
                "description_present": bool(description),
                "description_length": len(description),
                "routing": result_dict["routing"],
                "decision_path_code": result_dict["decision_path_code"],
                "keyword_score": result_dict["keyword_score"],
                "keyword_category": result_dict.get("keyword_category"),
                "keyword_matches": result_dict.get("keyword_matches", []),
                "negative_keywords": result_dict.get("negative_keywords", []),
                "consumer_signal_score": result_dict.get("consumer_signal_score", 0.0),
                "consumer_anchor_count": result_dict.get("consumer_anchor_count", 0),
                "b2b_soft_score": result_dict.get("b2b_soft_score", 0.0),
                "llm_eligible": llm_eligible,
                "thesis_result": result_dict,
            }
        )

    return results, matcher_runtime


async def analyze_database(
    db_path: str | Path,
    *,
    out_path: str | Path | None = None,
    thesis_hold_threshold: Optional[float] = None,
) -> dict[str, Any]:
    """Analyze pending survivor behavior without opening write-capable DB paths."""

    db = Path(db_path)
    config = _build_filter_config(thesis_hold_threshold=thesis_hold_threshold)
    with _connect_read_only(db) as conn:
        baseline = _baseline(conn)
        census = _whole_baseline_census(conn)
        pending_signals = _fetch_pending_signals(conn)

    canonical_results, matcher_runtime = await _classify_canonical_groups(
        pending_signals,
        config,
    )

    report = {
        "metadata": {
            "generated_at_utc": _utc_now(),
            "analysis_version": 1,
            "db_path": str(db),
            "db_open_mode": "sqlite_uri_mode_ro",
            "canonical_unit": CANONICAL_UNIT,
            "canonical_entrypoint": CANONICAL_ENTRYPOINT,
            "domain_name_mode": DOMAIN_NAME_MODE,
            "llm_calls_made": 0,
            "pipeline_hold_threshold_override": thesis_hold_threshold,
            "effective_config_snapshot": _effective_config_snapshot(config),
            "matcher_runtime": matcher_runtime,
        },
        "baseline": baseline,
        "auxiliary_whole_baseline_census": census,
        "canonical_results": canonical_results,
        "canonical_summary": _canonical_summary(canonical_results),
        "repeated_rejection_motifs": _rejection_motifs(canonical_results),
    }

    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run read-only thesis-filter survivor analysis.",
    )
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument("--out", required=True, help="Path to JSON artifact")
    parser.add_argument(
        "--thesis-hold-threshold",
        type=float,
        default=None,
        help="Optional PipelineConfig hold-threshold override.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = asyncio.run(
        analyze_database(
            args.db,
            out_path=args.out,
            thesis_hold_threshold=args.thesis_hold_threshold,
        )
    )
    print(
        json.dumps(
            {
                "baseline": report["baseline"],
                "canonical_summary": report["canonical_summary"],
                "metadata": {
                    "canonical_unit": report["metadata"]["canonical_unit"],
                    "canonical_entrypoint": report["metadata"]["canonical_entrypoint"],
                    "domain_name_mode": report["metadata"]["domain_name_mode"],
                    "llm_calls_made": report["metadata"]["llm_calls_made"],
                    "matcher_runtime": report["metadata"]["matcher_runtime"],
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
