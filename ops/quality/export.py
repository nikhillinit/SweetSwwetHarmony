"""
Dataset export for evaluation and offline analysis.

Exports a row per signal with:
- label (TP/FP/UNSURE) + label source
- core signal fields (source_api, signal_type, company_name, detected_at, confidence)
- extracted text fields (best-effort) from raw_data
- optional join fields from thesis_classifications (latest row per signal)

Output formats:
- CSV (default)
- JSONL
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _extract_text_fields(raw_data: Any) -> Dict[str, str]:
    """
    Best-effort extraction of descriptive fields from heterogeneous raw_data.
    """
    out = {"title": "", "description": "", "url": "", "domain": ""}
    if not raw_data:
        return out

    # raw_data may be JSON string or dict
    data = raw_data
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except Exception:
            return out

    if not isinstance(data, dict):
        return out

    # Common keys across collectors
    for k in ("title", "name", "company", "company_name"):
        if not out["title"] and isinstance(data.get(k), str):
            out["title"] = data.get(k, "").strip()

    for k in ("description", "company_description", "summary", "short_description", "long_description"):
        if not out["description"] and isinstance(data.get(k), str):
            out["description"] = data.get(k, "").strip()

    for k in ("url", "website", "company_url", "source_url", "link"):
        if not out["url"] and isinstance(data.get(k), str):
            out["url"] = data.get(k, "").strip()

    for k in ("domain", "hostname"):
        if not out["domain"] and isinstance(data.get(k), str):
            out["domain"] = data.get(k, "").strip()

    return out


def iter_labeled_signals(
    conn: sqlite3.Connection,
    *,
    days: int = 90,
    label_sources: Optional[Tuple[str, ...]] = None,
) -> Iterable[Dict[str, Any]]:
    """
    Yield dict rows for labeled signals.
    """
    since = _iso_days_ago(days)

    params: List[Any] = [since]
    source_filter = ""
    if label_sources:
        source_filter = " AND sqm.label_source IN ({})".format(",".join(["?"] * len(label_sources)))
        params.extend(list(label_sources))

    # Join latest thesis_classifications row per signal (if any)
    rows = conn.execute(
        f"""
        WITH latest_tc AS (
            SELECT tc.*
            FROM thesis_classifications tc
            JOIN (
                SELECT signal_id, MAX(id) AS max_id
                FROM thesis_classifications
                GROUP BY signal_id
            ) tmax ON tmax.signal_id = tc.signal_id AND tmax.max_id = tc.id
        )
        SELECT
            s.id AS signal_id,
            s.canonical_key,
            s.signal_type,
            s.source_api,
            s.company_name,
            s.confidence,
            s.detected_at,
            s.raw_data,
            sqm.human_label,
            sqm.label_source,
            sqm.labeled_at,
            sqm.notion_status,

            tc.keyword_score,
            tc.keyword_category,
            tc.negative_keywords,
            tc.thesis_match,
            tc.thesis_fit_score,
            tc.category AS thesis_category,
            tc.stage_estimate,
            tc.confidence AS llm_confidence,
            tc.rationale,
            tc.key_signals,
            tc.model,
            tc.latency_ms,
            tc.classified_at
        FROM signals s
        JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
        LEFT JOIN latest_tc tc ON tc.signal_id = s.id
        WHERE s.detected_at >= ?
        {source_filter}
        ORDER BY s.detected_at DESC
        """,
        params,
    ).fetchall()

    for r in rows:
        raw_data = r["raw_data"]
        text_fields = _extract_text_fields(raw_data)

        yield {
            "signal_id": int(r["signal_id"]),
            "canonical_key": str(r["canonical_key"]),
            "signal_type": str(r["signal_type"]),
            "source_api": str(r["source_api"]),
            "company_name": str(r["company_name"] or ""),
            "confidence": float(r["confidence"] or 0.0),
            "detected_at": str(r["detected_at"]),
            "human_label": str(r["human_label"]),
            "label_source": str(r["label_source"]),
            "labeled_at": str(r["labeled_at"]),
            "notion_status": str(r["notion_status"] or ""),

            "title": text_fields["title"],
            "description": text_fields["description"],
            "url": text_fields["url"],
            "domain": text_fields["domain"],

            # thesis_classifications (latest)
            "keyword_score": float(r["keyword_score"] or 0.0) if r["keyword_score"] is not None else None,
            "keyword_category": str(r["keyword_category"] or ""),
            "negative_keywords": str(r["negative_keywords"] or ""),
            "thesis_match": int(r["thesis_match"] or 0) if r["thesis_match"] is not None else None,
            "thesis_fit_score": float(r["thesis_fit_score"] or 0.0) if r["thesis_fit_score"] is not None else None,
            "thesis_category": str(r["thesis_category"] or ""),
            "stage_estimate": str(r["stage_estimate"] or ""),
            "llm_confidence": str(r["llm_confidence"] or ""),
            "rationale": str(r["rationale"] or ""),
            "key_signals": str(r["key_signals"] or ""),
            "model": str(r["model"] or ""),
            "latency_ms": int(r["latency_ms"] or 0) if r["latency_ms"] is not None else None,
            "classified_at": str(r["classified_at"] or ""),
        }


def export_dataset_csv(
    conn: sqlite3.Connection,
    *,
    out_path: str | Path,
    days: int = 90,
    label_sources: Optional[Tuple[str, ...]] = None,
) -> int:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = list(iter_labeled_signals(conn, days=days, label_sources=label_sources))
    if not rows:
        out_path.write_text("")
        return 0

    fieldnames = list(rows[0].keys())
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)

    return len(rows)


def export_dataset_jsonl(
    conn: sqlite3.Connection,
    *,
    out_path: str | Path,
    days: int = 90,
    label_sources: Optional[Tuple[str, ...]] = None,
) -> int:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for row in iter_labeled_signals(conn, days=days, label_sources=label_sources):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n
