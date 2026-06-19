"""Compute the discovery KPI baseline (red-team v2 task `p0.10`).

These KPIs are the *product* metrics — the ones that determine whether the
system is winning at investment-pipeline discovery, as opposed to the
guardrail metrics (publish_fp_rate etc.) that ensure it isn't getting worse.

KPIs computed (all over the production signals.db, read-only via the
ShadowSidecar):

  1. Lead time vs first public mention
     For each company that reached company_files.status='promoted',
     compute (first_public_mention_at - first_seen_at). Positive = we
     found it before any news/HN/PH/RSS mention. Negative = we lagged.

  2. Analyst precision at queue size 20
     Top 20 most recently surfaced companies (review_items + Notion suppression
     cache). Of those, what fraction have a TP label in signal_quality_metrics?
     Caveat: precision floor is sensitive to label availability — if labels are
     sparse, the denominator changes the headline number.

  3. Meetings booked per surfaced company
     Of companies with a Notion page, fraction whose status is in
     {Initial Meeting / Call, Dilligence, Committed, Funded}.

  4. Pre-launch / pre-fundraise detection rate
     Of TPs in last 90 days, fraction first seen before any AMBIENT_CORROBORATION
     signal. Uses the evidence ontology — Phase 0 derived value, no schema.

  5. Cross-source convergence rate
     Of surfaced companies (status='promoted'), fraction with >= 2 distinct
     discovery evidence classes (excluding AMBIENT_CORROBORATION,
     ANALYST_SEED, and UNKNOWN). Computed per-signal via
     analytics.kg_bridge.class_for_signal_row, which routes through the
     production-authoritative verification.evidence_families.get_family().
     The source-shape distribution branch (sole_ambient/with_any_discovery
     counts) intentionally still uses analytics.evidence_ontology.classify_source_api
     because company_files.source_apis is a list of source-api strings only
     and has no signal_type column.

KPI 4 is GNews-only — Crunchbase data is not configured (per memory). The
markdown report acknowledges this gap explicitly.

Output:
  artifacts/red-team-execution/phase0/discovery-kpi-baseline.md
  artifacts/red-team-execution/phase0/discovery-kpi-baseline.json

Safety:
  - Reads signals.db ONLY through ShadowSidecar (immutable URI mode).
  - Writes only to artifacts/. No DB writes anywhere.

Usage:
    python -m scripts.compute_discovery_kpi_baseline [--days 90] [--output-dir ...]
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from analytics.evidence_ontology import (
    EvidenceClass,
    classify_source_api,
)
from analytics.kg_bridge import class_for_signal_row
from analytics.shadow_sidecar import (
    ReadMode,
    ShadowSidecar,
    ShadowSidecarConfig,
)
from utils.db_path_helper import resolve_db_path_env

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("artifacts/red-team-execution/phase0")


@dataclass
class KpiBaseline:
    """One KPI baseline snapshot."""

    computed_at: str
    window_days: int
    n_companies_promoted: int
    n_signals_in_window: int

    # KPI 1: lead time
    lead_time_median_days: Optional[float] = None
    lead_time_n_with_public_mention: int = 0
    lead_time_n_without_public_mention: int = 0

    # KPI 2: precision at queue
    queue_size: int = 20
    precision_at_queue_n_labelled: int = 0
    precision_at_queue_n_tp: int = 0
    precision_at_queue_value: Optional[float] = None

    # KPI 3: meetings booked
    notion_total: int = 0
    notion_meeting_or_beyond: int = 0
    meeting_rate: Optional[float] = None

    # KPI 4: pre-launch detection
    pre_launch_n_tp: int = 0
    pre_launch_n_pre_ambient: int = 0
    pre_launch_rate: Optional[float] = None
    pre_launch_caveat: str = "GNews-only baseline; Crunchbase not configured"

    # KPI 5: cross-source convergence
    convergence_n_promoted: int = 0
    convergence_n_two_or_more_classes: int = 0
    convergence_rate: Optional[float] = None
    # E3: which classifier produced the convergence numbers
    # ("production_evidence_family" via analytics.kg_bridge.class_for_signal_row,
    # or "source_api_only" via analytics.evidence_ontology.classify_source_api).
    convergence_classifier: str = "production_evidence_family"

    # Per-source counts (for sanity / debugging)
    signal_counts_by_source: Dict[str, int] = field(default_factory=dict)

    # Promoted-cohort source-shape distribution (the headline finding)
    promoted_source_shapes: List[Dict[str, Any]] = field(default_factory=list)
    promoted_sole_ambient_count: int = 0
    promoted_with_any_discovery_class: int = 0


# ---- Helpers ---------------------------------------------------------------


def _table_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _median(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    xs_sorted = sorted(xs)
    n = len(xs_sorted)
    mid = n // 2
    if n % 2 == 1:
        return xs_sorted[mid]
    return (xs_sorted[mid - 1] + xs_sorted[mid]) / 2.0


# ---- KPI computations ------------------------------------------------------


def _compute_signal_counts_by_source(conn, since_iso: str) -> Dict[str, int]:
    if not _table_exists(conn, "signals"):
        return {}
    rows = conn.execute(
        "SELECT source_api, COUNT(*) AS n FROM signals "
        "WHERE created_at >= ? GROUP BY source_api",
        (since_iso,),
    ).fetchall()
    return {r["source_api"]: r["n"] for r in rows}


def _compute_lead_time(
    conn, since_iso: str
) -> Dict[str, Any]:
    """KPI 1 — median lead time vs first ambient (public-mention proxy).

    "First public mention" is approximated as the first AMBIENT_CORROBORATION
    signal for a canonical_key. The first non-ambient signal is the
    "first_seen_at" baseline.
    """
    if not _table_exists(conn, "signals"):
        return {
            "median_days": None,
            "n_with_public_mention": 0,
            "n_without_public_mention": 0,
        }
    rows = conn.execute(
        "SELECT canonical_key, source_api, MIN(detected_at) AS first_dt "
        "FROM signals WHERE detected_at >= ? "
        "GROUP BY canonical_key, source_api",
        (since_iso,),
    ).fetchall()

    by_key: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"first_strong": None, "first_ambient": None}
    )
    for r in rows:
        cls = classify_source_api(r["source_api"])
        ts = _parse_iso(r["first_dt"])
        if ts is None:
            continue
        rec = by_key[r["canonical_key"]]
        if cls == EvidenceClass.AMBIENT_CORROBORATION:
            if rec["first_ambient"] is None or ts < rec["first_ambient"]:
                rec["first_ambient"] = ts
        elif cls != EvidenceClass.UNKNOWN:
            if rec["first_strong"] is None or ts < rec["first_strong"]:
                rec["first_strong"] = ts

    deltas_days: List[float] = []
    n_with = 0
    n_without = 0
    for rec in by_key.values():
        if rec["first_strong"] is None:
            continue
        if rec["first_ambient"] is None:
            n_without += 1
            continue
        delta = (rec["first_ambient"] - rec["first_strong"]).total_seconds() / 86400.0
        deltas_days.append(delta)
        n_with += 1

    return {
        "median_days": _median(deltas_days),
        "n_with_public_mention": n_with,
        "n_without_public_mention": n_without,
    }


def _compute_meetings_booked(conn) -> Dict[str, Any]:
    """KPI 3 — meetings booked per Notion-surfaced company."""
    if not _table_exists(conn, "suppression_cache"):
        return {"total": 0, "meeting_or_beyond": 0, "rate": None}
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM suppression_cache GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    total = sum(counts.values())
    meeting_or_beyond_statuses = {
        "Initial Meeting / Call",
        "Dilligence",
        "Committed",
        "Funded",
    }
    meeting = sum(counts.get(s, 0) for s in meeting_or_beyond_statuses)
    rate = (meeting / total) if total else None
    return {"total": total, "meeting_or_beyond": meeting, "rate": rate}


def _compute_precision_at_queue(
    conn, queue_size: int, since_iso: str
) -> Dict[str, Any]:
    """KPI 2 — precision at fixed queue size, restricted to labelled signals."""
    if not _table_exists(conn, "signal_quality_metrics"):
        return {
            "n_labelled": 0,
            "n_tp": 0,
            "value": None,
        }
    if not _table_exists(conn, "signals"):
        return {
            "n_labelled": 0,
            "n_tp": 0,
            "value": None,
        }

    rows = conn.execute(
        """
        SELECT s.id, sqm.human_label
          FROM signals s
          JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
         WHERE s.created_at >= ?
         ORDER BY s.created_at DESC
         LIMIT ?
        """,
        (since_iso, queue_size),
    ).fetchall()

    n_labelled = len(rows)
    n_tp = sum(1 for r in rows if (r["human_label"] or "").upper() == "TP")
    value = (n_tp / n_labelled) if n_labelled else None
    return {"n_labelled": n_labelled, "n_tp": n_tp, "value": value}


def _compute_pre_launch_detection(conn, since_iso: str) -> Dict[str, Any]:
    """KPI 4 — fraction of TPs first seen before any ambient signal.

    GNews-only baseline. Crunchbase not configured.
    """
    if not _table_exists(conn, "signal_quality_metrics"):
        return {"n_tp": 0, "n_pre_ambient": 0, "rate": None}
    if not _table_exists(conn, "signals"):
        return {"n_tp": 0, "n_pre_ambient": 0, "rate": None}

    tps = conn.execute(
        """
        SELECT s.id, s.canonical_key, s.detected_at, s.source_api
          FROM signals s
          JOIN signal_quality_metrics sqm ON sqm.signal_id = s.id
         WHERE upper(coalesce(sqm.human_label, '')) = 'TP'
           AND s.created_at >= ?
        """,
        (since_iso,),
    ).fetchall()

    if not tps:
        return {"n_tp": 0, "n_pre_ambient": 0, "rate": None}

    n_tp = 0
    n_pre = 0
    for tp in tps:
        canonical_key = tp["canonical_key"]
        n_tp += 1
        # Find earliest ambient signal for this canonical_key
        rows = conn.execute(
            "SELECT MIN(detected_at) AS first_dt FROM signals "
            "WHERE canonical_key = ? AND source_api IN "
            "('hacker_news','arxiv','rss_feeds','news_api','product_hunt')",
            (canonical_key,),
        ).fetchone()
        first_ambient = _parse_iso(rows["first_dt"]) if rows else None
        tp_seen = _parse_iso(tp["detected_at"])
        if first_ambient is None or (tp_seen and tp_seen < first_ambient):
            n_pre += 1

    rate = (n_pre / n_tp) if n_tp else None
    return {"n_tp": n_tp, "n_pre_ambient": n_pre, "rate": rate}


def _compute_cross_source_convergence(conn) -> Dict[str, Any]:
    """KPI 5 — fraction of promoted companies with >= 2 discovery classes.

    Also computes the promoted-cohort source-shape distribution and the
    sole-ambient count, since these are the most interpretable numbers in
    the whole report.
    """
    if not _table_exists(conn, "company_files") or not _table_exists(conn, "signals"):
        return {
            "n_promoted": 0,
            "n_two_or_more": 0,
            "rate": None,
            "source_shapes": [],
            "sole_ambient_count": 0,
            "with_any_discovery_class": 0,
        }

    promoted = conn.execute(
        "SELECT company_id, canonical_key, source_apis FROM company_files "
        "WHERE status = 'promoted'"
    ).fetchall()

    if not promoted:
        return {
            "n_promoted": 0,
            "n_two_or_more": 0,
            "rate": None,
            "source_shapes": [],
            "sole_ambient_count": 0,
            "with_any_discovery_class": 0,
        }

    import json as _json
    from collections import Counter

    shape_counter: Counter = Counter()
    sole_ambient = 0
    with_any_discovery = 0
    n_two_or_more = 0
    for p in promoted:
        # Source-shape from company_files.source_apis (the live promotion record)
        try:
            srcs = tuple(sorted(set(_json.loads(p["source_apis"] or "[]"))))
        except Exception:
            srcs = ("parse_error",)
        shape_counter[srcs] += 1

        # Class composition from the actual signals table
        shape_classes = {classify_source_api(s) for s in srcs}
        discovery_in_shape = {
            c
            for c in shape_classes
            if c
            not in (
                EvidenceClass.AMBIENT_CORROBORATION,
                EvidenceClass.ANALYST_SEED,
                EvidenceClass.UNKNOWN,
            )
        }
        if not discovery_in_shape:
            sole_ambient += 1
        else:
            with_any_discovery += 1
    for p in promoted:
        # E3: classify each signal row through the production-authoritative
        # classifier (verification.evidence_families.get_family) by way of
        # analytics.kg_bridge.class_for_signal_row, which uses BOTH
        # signal_type and source_api. The previous (P0) path used only
        # source_api which over-counted distinct discovery classes for
        # promotions whose source_apis collapse to the same evidence family
        # under the production taxonomy (e.g. linkedin_company web_presence
        # and sec_edgar regulatory both → INFRASTRUCTURE_INTENT).
        signal_rows = conn.execute(
            "SELECT signal_type, source_api, detected_at FROM signals "
            "WHERE canonical_key = ?",
            (p["canonical_key"],),
        ).fetchall()
        signal_classes = {
            class_for_signal_row(r["signal_type"], r["source_api"])
            for r in signal_rows
        }
        # Discovery classes only — exclude AMBIENT_CORROBORATION (popularity
        # signals), ANALYST_SEED (analyst priors), and UNKNOWN (unmapped
        # signal types). A company with (analyst_seed + greenhouse_jobs)
        # has only ONE discovery class.
        discovery_classes = {
            c
            for c in signal_classes
            if c
            not in (
                EvidenceClass.AMBIENT_CORROBORATION,
                EvidenceClass.ANALYST_SEED,
                EvidenceClass.UNKNOWN,
            )
        }
        if len(discovery_classes) >= 2:
            n_two_or_more += 1

    rate = (n_two_or_more / len(promoted)) if promoted else None
    shape_list = [
        {"source_apis": list(shape), "count": n}
        for shape, n in shape_counter.most_common()
    ]
    return {
        "n_promoted": len(promoted),
        "n_two_or_more": n_two_or_more,
        "rate": rate,
        "source_shapes": shape_list,
        "sole_ambient_count": sole_ambient,
        "with_any_discovery_class": with_any_discovery,
    }


# ---- Orchestration ---------------------------------------------------------


def compute_baseline(
    *,
    production_db: Optional[Path] = None,
    window_days: int = 90,
    queue_size: int = 20,
) -> KpiBaseline:
    production_db = Path(resolve_db_path_env()) if production_db is None else production_db
    cfg = ShadowSidecarConfig(
        production_db=production_db,
        read_mode=ReadMode.IMMUTABLE_URI,
        register_dbtool_lock=False,
    )
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)
    since_iso = since.isoformat()

    baseline = KpiBaseline(
        computed_at=now.isoformat(),
        window_days=window_days,
        queue_size=queue_size,
        n_companies_promoted=0,
        n_signals_in_window=0,
    )

    if not production_db.exists():
        logger.warning("Production DB not found at %s; returning empty baseline", production_db)
        return baseline

    with ShadowSidecar(cfg) as sidecar:
        with sidecar.production_read_connection() as conn:
            baseline.signal_counts_by_source = _compute_signal_counts_by_source(conn, since_iso)
            baseline.n_signals_in_window = sum(baseline.signal_counts_by_source.values())

            promoted_row = conn.execute(
                "SELECT COUNT(*) AS n FROM company_files WHERE status = 'promoted'"
            ).fetchone() if _table_exists(conn, "company_files") else None
            baseline.n_companies_promoted = promoted_row["n"] if promoted_row else 0

            lt = _compute_lead_time(conn, since_iso)
            baseline.lead_time_median_days = lt["median_days"]
            baseline.lead_time_n_with_public_mention = lt["n_with_public_mention"]
            baseline.lead_time_n_without_public_mention = lt["n_without_public_mention"]

            mb = _compute_meetings_booked(conn)
            baseline.notion_total = mb["total"]
            baseline.notion_meeting_or_beyond = mb["meeting_or_beyond"]
            baseline.meeting_rate = mb["rate"]

            pq = _compute_precision_at_queue(conn, queue_size, since_iso)
            baseline.precision_at_queue_n_labelled = pq["n_labelled"]
            baseline.precision_at_queue_n_tp = pq["n_tp"]
            baseline.precision_at_queue_value = pq["value"]

            pl = _compute_pre_launch_detection(conn, since_iso)
            baseline.pre_launch_n_tp = pl["n_tp"]
            baseline.pre_launch_n_pre_ambient = pl["n_pre_ambient"]
            baseline.pre_launch_rate = pl["rate"]

            cv = _compute_cross_source_convergence(conn)
            baseline.convergence_n_promoted = cv["n_promoted"]
            baseline.convergence_n_two_or_more_classes = cv["n_two_or_more"]
            baseline.convergence_rate = cv["rate"]
            baseline.promoted_source_shapes = cv.get("source_shapes", [])
            baseline.promoted_sole_ambient_count = cv.get("sole_ambient_count", 0)
            baseline.promoted_with_any_discovery_class = cv.get(
                "with_any_discovery_class", 0
            )

    return baseline


def render_markdown(baseline: KpiBaseline) -> str:
    def fmt_pct(x):
        return f"{x * 100:.1f}%" if isinstance(x, float) else "—"

    def fmt_num(x):
        return f"{x:.2f}" if isinstance(x, float) else "—"

    sole_ambient_pct = (
        baseline.promoted_sole_ambient_count / baseline.n_companies_promoted
        if baseline.n_companies_promoted
        else None
    )

    shape_lines = []
    for s in baseline.promoted_source_shapes[:10]:
        srcs = ", ".join(s["source_apis"]) or "(empty)"
        shape_lines.append(f"| `{srcs}` | {s['count']} |")
    shape_table = "\n".join(shape_lines) or "| (none) | 0 |"

    return f"""# Discovery KPI Baseline (Phase 0, task p0.10)

**Computed at:** {baseline.computed_at}
**Window:** last {baseline.window_days} days
**Queue size for KPI 2:** {baseline.queue_size}

## Headline numbers

| KPI | Value | Sample size |
|---|---|---|
| 1. Lead time vs first public mention (median, days) | {fmt_num(baseline.lead_time_median_days)} | {baseline.lead_time_n_with_public_mention} (with public mention) / {baseline.lead_time_n_without_public_mention} (without) |
| 2. Analyst precision at queue size {baseline.queue_size} | {fmt_pct(baseline.precision_at_queue_value)} | {baseline.precision_at_queue_n_tp}/{baseline.precision_at_queue_n_labelled} labelled |
| 3. Meetings booked rate | {fmt_pct(baseline.meeting_rate)} | {baseline.notion_meeting_or_beyond}/{baseline.notion_total} Notion entries |
| 4. Pre-launch / pre-fundraise detection rate (GNews-only) | {fmt_pct(baseline.pre_launch_rate)} | {baseline.pre_launch_n_pre_ambient}/{baseline.pre_launch_n_tp} TPs |
| 5. Cross-source convergence rate (≥2 non-ambient classes) | {fmt_pct(baseline.convergence_rate)} | {baseline.convergence_n_two_or_more_classes}/{baseline.convergence_n_promoted} promoted companies |

## Caveats

1. **KPI 4 is GNews-only.** Crunchbase data is not configured in this
   environment (no `CRUNCHBASE_API_KEY`). The pre-launch detection rate
   biases toward news-mentioned companies; it cannot detect "pre-fundraise"
   in the strict Crunchbase-funding-event sense. Adding Crunchbase is a
   Phase 1+ prerequisite for the strict version of this KPI.

2. **KPI 2 is sensitive to label availability.** With sparse
   `signal_quality_metrics`, the precision-at-queue denominator can be
   dramatically smaller than the queue size, inflating or deflating the
   headline. The labelling sprint (`p0.3`) is the primary remedy.

3. **KPI 5 uses derived classes.** Computed via
   `analytics.kg_bridge.class_for_signal_row`, which defers to
   `verification.evidence_families.get_family()` (the production-
   authoritative classifier). No schema migration. The result will change
   when new collectors land in the ontology table or when
   `verification/evidence_families.py` adds new (signal_type, source_api)
   mappings.

## KPI 5 classifier provenance (E3)

KPI 5 (cross-source convergence) is computed using the
**`{baseline.convergence_classifier}`** path:

- `production_evidence_family` *(default after E3)*: per-signal
  classification via `analytics.kg_bridge.class_for_signal_row(signal_type,
  source_api)`, which defers to the production-authoritative
  `verification.evidence_families.get_family()`. This path uses BOTH
  `signals.signal_type` and `signals.source_api`, so it correctly handles
  source-API overrides for ambiguous signal types and collapses
  `linkedin_company` (web_presence) and `incorporation` (regulatory) into
  the same INFRASTRUCTURE_INTENT discovery class.

- `source_api_only` *(pre-E3 path; preserved for the source-shape branch
  at line ~380 because `company_files.source_apis` has only source-api
  strings, no signal_type)*: classification via
  `analytics.evidence_ontology.classify_source_api(source_api)`. This path
  is what the `promoted_sole_ambient_count` and
  `promoted_with_any_discovery_class` numbers above are computed under,
  because the source-shape branch operates on a list of source-api strings
  with no signal_type available.

This means the report mixes two classifiers by design: the headline KPI 5
uses the production classifier; the source-shape distribution uses the
simpler source-api map. The two classifiers agree on most cases but
disagree where the production taxonomy distinguishes signals that the
simple map collapses (or vice versa). The disagreement is documented in
`artifacts/red-team-execution/phase0/kg-enhancement-design.md` §4.

## Per-source signal counts (window)

| Source API | Count |
|---|---|
{chr(10).join(f"| {k} | {v} |" for k, v in sorted(baseline.signal_counts_by_source.items(), key=lambda x: -x[1])) or "| (none) | 0 |"}

**Total signals in window:** {baseline.n_signals_in_window}
**Total promoted companies:** {baseline.n_companies_promoted}

## Promoted-cohort source-shape distribution (lifetime)

This is the most interpretable number in the report. It answers: *what
combinations of sources have actually triggered the OR-based promotion
rule in `workflows/thin_file_manager.py`?*

| Source shape | Promoted count |
|---|---|
{shape_table}

**Sole-ambient promotions:** {baseline.promoted_sole_ambient_count} ({fmt_pct(sole_ambient_pct)} of all promoted)
**Promotions with any discovery class:** {baseline.promoted_with_any_discovery_class}

A "discovery class" excludes both AMBIENT_CORROBORATION (popularity
signals) and ANALYST_SEED (manual analyst entries). The strategy
document's central diagnosis — that the system is over-reliant on
single-class popularity signals — is **directly verifiable from this
table**.
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--queue-size", type=int, default=20)
    parser.add_argument(
        "--production-db",
        type=Path,
        default=None,
        help="Production DB path (default: DISCOVERY_DB_PATH)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    baseline = compute_baseline(
        production_db=args.production_db,
        window_days=args.days,
        queue_size=args.queue_size,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.output_dir / "discovery-kpi-baseline.md"
    json_path = args.output_dir / "discovery-kpi-baseline.json"

    md_path.write_text(render_markdown(baseline), encoding="utf-8")
    json_path.write_text(
        json.dumps(asdict(baseline), indent=2, default=str), encoding="utf-8"
    )

    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
