"""
Thesis classification helpers for Quality Ops.

Key workflows:
- Run keyword matcher + LLM classifier for a signal, store results to thesis_classifications
- Batch classify recent unlabeled signals (or signals missing thesis_classifications)
- Generate "disagreement" reports between keyword gating and LLM output

Notes:
- LLMClassifier relies on google-genai (google.genai). If it's not installed,
  these functions raise ImportError with a helpful message.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.thesis_matcher import ThesisMatcher
from ops.quality.db import utc_now_iso


@dataclass(frozen=True)
class ThesisRunResult:
    signal_id: int
    canonical_key: str
    keyword_score: float
    keyword_category: str
    negative_keywords: List[str]
    thesis_match: bool
    thesis_fit_score: float
    category: str
    stage_estimate: str
    confidence: str
    latency_ms: int
    classified_at: str
    model: str
    prompt_version: str


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _load_signal(conn: sqlite3.Connection, signal_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, canonical_key, source_api, signal_type, company_name, raw_data, detected_at, company_id FROM signals WHERE id = ?",
        (signal_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Signal id={signal_id} not found")
    return row


def _extract_text(raw_data: Any, company_name: str) -> str:
    if not raw_data:
        return company_name or ""
    data = raw_data
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except Exception:
            return company_name or ""
    if not isinstance(data, dict):
        return company_name or ""

    parts: List[str] = []
    title = data.get("title") or data.get("name") or company_name
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())

    desc = data.get("description") or data.get("company_description") or data.get("summary")
    if isinstance(desc, str) and desc.strip():
        parts.append(desc.strip())

    url = data.get("url") or data.get("website") or data.get("link")
    if isinstance(url, str) and url.strip():
        parts.append(f"URL: {url.strip()}")

    return "\n".join(parts).strip()


def _ensure_thesis_table_exists(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_classifications'"
    ).fetchone()
    if not row:
        raise RuntimeError(
            "thesis_classifications table not found. Run the pipeline once to initialize the DB schema."
        )


def store_thesis_classification(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    canonical_key: str,
    keyword_score: float,
    keyword_category: str,
    negative_keywords: List[str],
    thesis_match: bool,
    thesis_fit_score: float,
    category: str,
    stage_estimate: str,
    confidence: str,
    rationale: str,
    key_signals: List[str],
    prompt_version: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    latency_ms: int,
    competitor_flag: bool = False,
    competitor_match: Optional[Dict[str, Any]] = None,
    classified_at: Optional[str] = None,
) -> int:
    _ensure_thesis_table_exists(conn)

    classified_at = classified_at or utc_now_iso()

    # Compute disagreement flag (Phase 9 Quality Ops)
    disagreement_detected = 0
    if keyword_score is not None and thesis_fit_score is not None:
        if (keyword_score >= 0.7 and thesis_fit_score < 0.4) or \
           (keyword_score < 0.4 and thesis_fit_score >= 0.7):
            disagreement_detected = 1

    cur = conn.execute(
        """
        INSERT INTO thesis_classifications (
            signal_id, canonical_key,
            keyword_score, keyword_category, negative_keywords,
            thesis_match, thesis_fit_score, category, stage_estimate, confidence,
            rationale, key_signals,
            prompt_version, model, input_tokens, output_tokens, latency_ms,
            competitor_flag, competitor_match,
            disagreement_detected,
            classified_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            canonical_key,
            float(keyword_score) if keyword_score is not None else None,
            keyword_category,
            json.dumps(negative_keywords, ensure_ascii=False),
            1 if thesis_match else 0,
            float(thesis_fit_score) if thesis_fit_score is not None else None,
            category,
            stage_estimate,
            confidence,
            rationale,
            json.dumps(key_signals, ensure_ascii=False),
            prompt_version,
            model,
            input_tokens,
            output_tokens,
            latency_ms,
            1 if competitor_flag else 0,
            json.dumps(competitor_match, ensure_ascii=False) if competitor_match else None,
            disagreement_detected,
            classified_at,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def classify_signal_llm(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    model: str = "gemini-2.0-flash",
    prompt_version: str = "quality-ops-v1",
) -> ThesisRunResult:
    """
    Runs keyword matcher + LLM classifier and stores result to thesis_classifications.

    Requires environment variable GEMINI_API_KEY (via google-genai).
    """
    row = _load_signal(conn, signal_id)
    canonical_key = str(row["canonical_key"])
    company_name = str(row["company_name"] or "")
    raw_data = row["raw_data"]

    # Keyword match (fast, deterministic)
    matcher = ThesisMatcher()
    text_blob = _extract_text(raw_data, company_name=company_name)
    fit = matcher.score(text_blob)

    keyword_score = float(fit.score)
    keyword_category = str(getattr(fit.thesis, "value", str(fit.thesis)))
    negative_keywords = list(fit.negative_keywords or [])

    # LLM match
    try:
        from consumer.thesis_filter.llm_classifier import LLMClassifier
    except Exception as e:
        raise ImportError(
            "LLM classification requires google-genai. Install with: pip install google-genai"
        ) from e

    classifier = LLMClassifier(model=model)

    start = time.time()
    classification = classifier.classify_sync(
        {
            "canonical_key": canonical_key,
            "company_name": company_name,
            "text": text_blob,
            "source_api": str(row["source_api"] or ""),
            "signal_type": str(row["signal_type"] or ""),
        }
    )
    latency_ms = int((time.time() - start) * 1000)

    classification_id = store_thesis_classification(
        conn,
        signal_id=signal_id,
        canonical_key=canonical_key,
        keyword_score=keyword_score,
        keyword_category=keyword_category,
        negative_keywords=negative_keywords,
        thesis_match=bool(classification.thesis_match),
        thesis_fit_score=float(classification.thesis_fit_score),
        category=str(classification.category or ""),
        stage_estimate=str(classification.stage_estimate or ""),
        confidence=str(classification.confidence or ""),
        rationale=str(classification.rationale or ""),
        key_signals=list(classification.key_signals or []),
        prompt_version=prompt_version,
        model=model,
        input_tokens=None,
        output_tokens=None,
        latency_ms=latency_ms,
    )

    return ThesisRunResult(
        signal_id=signal_id,
        canonical_key=canonical_key,
        keyword_score=keyword_score,
        keyword_category=keyword_category,
        negative_keywords=negative_keywords,
        thesis_match=bool(classification.thesis_match),
        thesis_fit_score=float(classification.thesis_fit_score),
        category=str(classification.category or ""),
        stage_estimate=str(classification.stage_estimate or ""),
        confidence=str(classification.confidence or ""),
        latency_ms=latency_ms,
        classified_at=utc_now_iso(),
        model=model,
        prompt_version=prompt_version,
    )


def iter_signals_missing_thesis(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    limit: int = 200,
) -> List[int]:
    since = _iso_days_ago(days)

    rows = conn.execute(
        """
        SELECT s.id AS signal_id
        FROM signals s
        LEFT JOIN (
            SELECT signal_id, MAX(id) AS max_id
            FROM thesis_classifications
            GROUP BY signal_id
        ) tc ON tc.signal_id = s.id
        WHERE s.detected_at >= ?
          AND tc.max_id IS NULL
        ORDER BY s.detected_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()

    return [int(r["signal_id"]) for r in rows]


def batch_classify_missing_thesis(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    limit: int = 200,
    model: str = "gemini-2.0-flash",
    prompt_version: str = "quality-ops-v1",
    stop_on_error: bool = False,
) -> Dict[str, Any]:
    """
    Classify recent signals that do not have a thesis classification row.

    Returns summary dict with successes/errors.
    """
    ids = iter_signals_missing_thesis(conn, days=days, limit=limit)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for sid in ids:
        try:
            r = classify_signal_llm(conn, signal_id=sid, model=model, prompt_version=prompt_version)
            results.append({"signal_id": sid, "thesis_match": r.thesis_match, "thesis_fit_score": r.thesis_fit_score})
        except Exception as e:
            errors.append({"signal_id": sid, "error": str(e)})
            if stop_on_error:
                break

    return {"attempted": len(ids), "succeeded": len(results), "failed": len(errors), "results": results, "errors": errors}


def batch_classify_recent(
    db_path: str,
    *,
    limit: int = 50,
    chunk_size: int = 10,
    upsert: bool = True
) -> int:
    """
    Scheduler-friendly wrapper for batch_classify_missing_thesis.

    Args:
        db_path: Path to signals database
        limit: Maximum signals to classify
        chunk_size: Batch size (not used currently, but kept for API compat)
        upsert: If True, uses UPSERT to avoid duplicates (idempotent)

    Returns:
        Number of signals successfully classified
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Ensure quality tables exist
        from ops.quality.db import ensure_quality_tables
        ensure_quality_tables(conn)

        result = batch_classify_missing_thesis(
            conn,
            days=30,  # Last 30 days
            limit=limit,
            stop_on_error=False
        )

        return result["succeeded"]
    finally:
        conn.close()


def generate_disagreement_report(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    keyword_threshold: float = 0.40,
    out_path: Optional[str] = None,
) -> str:
    """
    Compare keyword_score vs LLM thesis_match and output a markdown report.

    Uses the disagreement_detected column (migration 26) for efficient filtering.

    Disagreements:
    - Keyword says match (>= threshold) but LLM says no -> keyword false positives
    - Keyword says no (< threshold) but LLM says yes -> keyword false negatives
    """
    since = _iso_days_ago(days)

    # Query all classified signals
    all_rows = conn.execute(
        """
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
            s.source_api,
            s.company_name,
            s.detected_at,
            tc.keyword_score,
            tc.keyword_category,
            tc.thesis_match,
            tc.thesis_fit_score,
            tc.category AS thesis_category,
            tc.confidence AS llm_confidence,
            COALESCE(tc.disagreement_detected, 0) AS disagreement_detected
        FROM signals s
        JOIN latest_tc tc ON tc.signal_id = s.id
        WHERE s.detected_at >= ?
        ORDER BY s.detected_at DESC
        """,
        (since,),
    ).fetchall()

    # Filter for disagreements using the column
    disagreement_rows = [r for r in all_rows if r["disagreement_detected"] == 1]

    # Categorize disagreements
    kw_fp: List[sqlite3.Row] = []  # keyword high, LLM low
    kw_fn: List[sqlite3.Row] = []  # keyword low, LLM high

    for r in disagreement_rows:
        kw = float(r["keyword_score"] or 0.0)
        llm_fit = float(r["thesis_fit_score"] or 0.0)

        if kw >= 0.7 and llm_fit < 0.4:
            kw_fp.append(r)
        elif kw < 0.4 and llm_fit >= 0.7:
            kw_fn.append(r)

    # Compute statistics by category
    from collections import Counter
    kw_fp_by_category = Counter(r["keyword_category"] for r in kw_fp if r["keyword_category"])
    kw_fn_by_category = Counter(r["thesis_category"] for r in kw_fn if r["thesis_category"])

    def _fmt_row(r: sqlite3.Row) -> str:
        return (
            f"- signal_id={int(r['signal_id'])} "
            f"source_api={r['source_api']} "
            f"kw={float(r['keyword_score'] or 0.0):.2f} "
            f"llm_match={bool(r['thesis_match'])} "
            f"llm_fit={float(r['thesis_fit_score'] or 0.0):.2f} "
            f"company='{(r['company_name'] or '')[:80]}'"
        )

    # Build report
    total_classified = len(all_rows)
    total_disagreements = len(disagreement_rows)
    disagreement_rate = (total_disagreements / total_classified * 100) if total_classified > 0 else 0.0

    md = []
    md.append(f"# Thesis Disagreement Report (last {days} days)")
    md.append("")
    md.append("## Summary")
    md.append("")
    md.append(f"- **Total classified**: {total_classified}")
    md.append(f"- **Total disagreements**: {total_disagreements} ({disagreement_rate:.1f}%)")
    md.append(f"- **Keyword false positives**: {len(kw_fp)} (keyword says yes, LLM says no)")
    md.append(f"- **Keyword false negatives**: {len(kw_fn)} (keyword says no, LLM says yes)")
    md.append("")

    # False positives by category
    if kw_fp_by_category:
        md.append("### False Positives by Keyword Category")
        md.append("")
        for cat, count in kw_fp_by_category.most_common():
            pct = (count / len(kw_fp) * 100) if kw_fp else 0
            md.append(f"- {cat}: {count} ({pct:.1f}%)")
        md.append("")

    # False negatives by LLM category
    if kw_fn_by_category:
        md.append("### False Negatives by LLM Category")
        md.append("")
        for cat, count in kw_fn_by_category.most_common():
            pct = (count / len(kw_fn) * 100) if kw_fn else 0
            md.append(f"- {cat}: {count} ({pct:.1f}%)")
        md.append("")

    md.append("## Details")
    md.append("")
    md.append("### Keyword False Positives (keyword >= 0.7, LLM < 0.4)")
    md.append("")
    if kw_fp:
        for r in kw_fp[:200]:
            md.append(_fmt_row(r))
    else:
        md.append("*(none)*")

    md.append("")
    md.append("### Keyword False Negatives (keyword < 0.4, LLM >= 0.7)")
    md.append("")
    if kw_fn:
        for r in kw_fn[:200]:
            md.append(_fmt_row(r))
    else:
        md.append("*(none)*")

    report = "\n".join(md) + "\n"
    if out_path:
        Path(out_path).write_text(report, encoding="utf-8")
    return report
