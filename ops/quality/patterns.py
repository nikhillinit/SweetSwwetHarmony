"""
FP pattern detection over labeled signals.

This module is designed to be "good enough" without heavy ML deps.
It focuses on actionable patterns that lead to concrete tuning actions:
- Collector concentration (source_api heavy FP)
- Thesis-category concentration (category heavy FP)
- Repeated descriptions (exact/near-exact)
- Temporal hotspots (hour-of-day)
- Weak canonical keys (name_loc) overrepresented in FP

Outputs JSON-serializable dicts so they can be:
- stored as artifacts
- rendered in a dashboard
- passed to a tuning proposal generator
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    # Collapse whitespace + drop punctuation-ish characters
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return s.strip()


def _extract_description(raw_data: Any) -> str:
    if not raw_data:
        return ""
    data = raw_data
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except Exception:
            return ""
    if not isinstance(data, dict):
        return ""
    for k in ("description", "company_description", "summary", "short_description", "long_description"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _hour_bucket(iso_ts: str) -> Optional[int]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return int(dt.hour)
    except Exception:
        return None


@dataclass(frozen=True)
class PatternConfig:
    days: int = 30
    min_count: int = 10
    fp_rate_threshold: float = 0.70  # for concentration patterns


def _iter_labeled(conn: sqlite3.Connection, *, days: int) -> Iterable[sqlite3.Row]:
    since = _iso_days_ago(days)
    return conn.execute(
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
            s.canonical_key,
            s.source_api,
            s.signal_type,
            s.company_name,
            s.detected_at,
            s.raw_data,
            sqm.human_label,
            tc.category AS thesis_category
        FROM signals s
        JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
        LEFT JOIN latest_tc tc ON tc.signal_id = s.id
        WHERE s.detected_at >= ?
        """,
        (since,),
    ).fetchall()


def detect_patterns(conn: sqlite3.Connection, *, config: PatternConfig) -> List[Dict[str, Any]]:
    rows = list(_iter_labeled(conn, days=config.days))
    if not rows:
        return []

    # Partition by label
    fp_rows = [r for r in rows if str(r["human_label"]) == "FP"]
    tp_rows = [r for r in rows if str(r["human_label"]) == "TP"]
    # We ignore UNSURE for rate calc.

    patterns: List[Dict[str, Any]] = []

    # -------------------------------------------------------------------------
    # Pattern 1: source_api concentration (high FP rate)
    # -------------------------------------------------------------------------
    counts_by_source = defaultdict(lambda: {"fp": 0, "tp": 0, "unsure": 0})
    for r in rows:
        src = str(r["source_api"] or "")
        lbl = str(r["human_label"])
        if lbl == "FP":
            counts_by_source[src]["fp"] += 1
        elif lbl == "TP":
            counts_by_source[src]["tp"] += 1
        else:
            counts_by_source[src]["unsure"] += 1

    for src, c in counts_by_source.items():
        labeled = c["fp"] + c["tp"] + c["unsure"]
        denom = c["fp"] + c["tp"]
        fp_rate = (c["fp"] / denom) if denom else 0.0
        if (c["fp"] >= config.min_count) and (fp_rate >= config.fp_rate_threshold):
            patterns.append(
                {
                    "type": "source_api_fp_rate",
                    "source_api": src,
                    "fp": c["fp"],
                    "tp": c["tp"],
                    "unsure": c["unsure"],
                    "fp_rate": fp_rate,
                    "window_days": config.days,
                    "recommendation": "Investigate source parsing + add targeted exclusions or raise thresholds.",
                }
            )

    # -------------------------------------------------------------------------
    # Pattern 2: source_api + thesis_category concentration
    # -------------------------------------------------------------------------
    counts_by_src_cat = defaultdict(lambda: {"fp": 0, "tp": 0})
    for r in rows:
        src = str(r["source_api"] or "")
        cat = str(r["thesis_category"] or "UNKNOWN")
        lbl = str(r["human_label"])
        if lbl == "FP":
            counts_by_src_cat[(src, cat)]["fp"] += 1
        elif lbl == "TP":
            counts_by_src_cat[(src, cat)]["tp"] += 1

    for (src, cat), c in counts_by_src_cat.items():
        denom = c["fp"] + c["tp"]
        fp_rate = (c["fp"] / denom) if denom else 0.0
        if c["fp"] >= config.min_count and denom >= config.min_count and fp_rate >= config.fp_rate_threshold:
            patterns.append(
                {
                    "type": "source_api_category_fp_rate",
                    "source_api": src,
                    "thesis_category": cat,
                    "fp": c["fp"],
                    "tp": c["tp"],
                    "fp_rate": fp_rate,
                    "window_days": config.days,
                    "recommendation": "Tighten category routing for this source (keywords/negatives) or disable noisy slice.",
                }
            )

    # -------------------------------------------------------------------------
    # Pattern 3: repeated descriptions (exact-normalized)
    # -------------------------------------------------------------------------
    desc_counter = Counter()
    desc_examples: Dict[str, List[int]] = defaultdict(list)

    for r in fp_rows:
        desc = _extract_description(r["raw_data"])
        norm = _norm_text(desc)
        if norm:
            desc_counter[norm] += 1
            if len(desc_examples[norm]) < 10:
                desc_examples[norm].append(int(r["signal_id"]))

    for norm_desc, cnt in desc_counter.most_common():
        if cnt >= config.min_count:
            patterns.append(
                {
                    "type": "duplicate_fp_description",
                    "count": cnt,
                    "normalized_description": norm_desc[:240],
                    "example_signal_ids": desc_examples[norm_desc],
                    "recommendation": "Add a negative keyword/phrase, or improve description parsing + spam filtering.",
                }
            )

    # -------------------------------------------------------------------------
    # Pattern 4: temporal hotspots (hour-of-day)
    # -------------------------------------------------------------------------
    # We use FP distribution by hour per source_api.
    fp_by_src_hour = defaultdict(lambda: Counter())
    for r in fp_rows:
        src = str(r["source_api"] or "")
        hr = _hour_bucket(str(r["detected_at"]))
        if hr is not None:
            fp_by_src_hour[src][hr] += 1

    for src, ctr in fp_by_src_hour.items():
        total = sum(ctr.values())
        if total < config.min_count:
            continue
        mean = total / 24.0
        # simple: if any hour has >= max(min_count, mean*3) flag it
        for hr, cnt in ctr.items():
            if cnt >= max(config.min_count, int(math.ceil(mean * 3))):
                patterns.append(
                    {
                        "type": "fp_temporal_hotspot",
                        "source_api": src,
                        "hour_utc": int(hr),
                        "fp_count": int(cnt),
                        "fp_total": int(total),
                        "window_days": config.days,
                        "recommendation": "Check rate limits / scraper anomalies / cron overlap. Consider delaying or batching.",
                    }
                )

    # -------------------------------------------------------------------------
    # Pattern 5: weak canonical keys overrepresented in FP
    # -------------------------------------------------------------------------
    weak_fp = 0
    weak_total_fp = len(fp_rows)
    weak_key_examples: List[int] = []
    for r in fp_rows:
        key = str(r["canonical_key"] or "")
        if key.startswith("name_loc:"):
            weak_fp += 1
            if len(weak_key_examples) < 20:
                weak_key_examples.append(int(r["signal_id"]))

    if weak_total_fp >= config.min_count:
        share = weak_fp / weak_total_fp if weak_total_fp else 0.0
        if share >= 0.50 and weak_fp >= config.min_count:
            patterns.append(
                {
                    "type": "weak_canonical_keys_in_fp",
                    "fp_count": weak_fp,
                    "fp_total": weak_total_fp,
                    "share": share,
                    "example_signal_ids": weak_key_examples,
                    "recommendation": "Strengthen canonical keys (domain/org) to reduce duplicates and mis-merges.",
                }
            )

    return patterns


def detect_patterns_wrapper(db_path: str, *, days: int = 30) -> List[Dict[str, Any]]:
    """
    Scheduler-friendly wrapper for detect_patterns.

    Args:
        db_path: Path to signals database
        days: Number of days to analyze

    Returns:
        List of detected patterns
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Ensure quality tables exist
        from ops.quality.db import ensure_quality_tables
        ensure_quality_tables(conn)

        config = PatternConfig(days=days)
        return detect_patterns(conn, config=config)
    finally:
        conn.close()
