#!/usr/bin/env python3
"""
Thesis Filter Calibration: Pipeline CSV vs Production Routing.

Runs all companies from the Notion-exported venture pipeline CSV through
ThesisFilter's production routing (keyword-only, no LLM) to identify where
the filter is too aggressive and would reject companies the team actually sourced.

Phase 3: Uses ThesisFilter._resolve_cascade_routing() for parity with
production. Includes hold-out split, expanded cascade metrics, and
experiment mode toggle.

Usage:
    python scripts/analyze_pipeline_thesis.py path/to/venture_pipeline.csv
    python scripts/analyze_pipeline_thesis.py pipeline.csv --out artifacts/thesis_calibration_report.json
    python scripts/analyze_pipeline_thesis.py pipeline.csv --split-seed 42 --split train
    CASCADE_ROUTING_ENABLEMENT=shadow python scripts/analyze_pipeline_thesis.py pipeline.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import tldextract

from utils.thesis_filter import (
    DecisionPathCode,
    ThesisFilter,
    ThesisFilterConfig,
)
from utils.thesis_matcher import NEGATIVE_KEYWORDS, ThesisMatcher

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = {
    "Source",
    "Initial Meeting / Call",
    "Dilligence",  # Intentional typo — matches Notion schema
    "Tracking",
    "Funded",
    "Committed",
    "",  # Empty status = unprocessed, still active
}

INACTIVE_STATUSES = {"Passed", "Lost"}

# Junk values for the Website column that should be treated as missing
_JUNK_DOMAINS = {
    "na",
    "n a",
    "n/a",
    "none",
    "no active website",
    "no website",
    "tbd",
    "-",
    "",
}

# Offline tldextract — no network calls
_tld_extract = tldextract.TLDExtract(suffix_list_urls=())

# Hold-out split ratio (train fraction)
_TRAIN_FRACTION = 0.70


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_domain(raw_website: Optional[str]) -> Optional[str]:
    """Normalise a website field into a bare registered domain.

    Examples:
        https://www.noon.world/  → noon.world
        farmysnacks.com          → farmysnacks.com
        NA / empty               → None
    """
    if not raw_website:
        return None
    cleaned = raw_website.strip().lower()
    if cleaned in _JUNK_DOMAINS:
        return None

    parsed = _tld_extract(cleaned)
    # Use top_domain_under_public_suffix (replaces deprecated registered_domain)
    domain = getattr(parsed, "top_domain_under_public_suffix", None) or getattr(
        parsed, "registered_domain", None
    )
    return domain if domain else None


def _snippet(text: Optional[str], length: int = 80) -> str:
    """Return a truncated snippet of text for display."""
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) <= length:
        return text
    return text[:length] + "..."


def _holdout_split(key: str, seed: int = 42) -> str:
    """Deterministic hold-out split: hash key+seed → 'train' or 'eval'.

    Uses SHA-256 of (seed, key) to produce a uniform [0,1) float.
    Returns 'train' if < _TRAIN_FRACTION (0.70), else 'eval'.
    """
    h = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()
    # Use first 8 hex chars → 32-bit integer → normalise to [0,1)
    frac = int(h[:8], 16) / 0x100000000
    return "train" if frac < _TRAIN_FRACTION else "eval"


def _resolve_experiment_mode() -> Dict[str, Any]:
    """Resolve experiment mode and skip-LLM threshold.

    Returns dict with experiment_mode and skip_llm_threshold_used.
    """
    experiment_mode = os.environ.get(
        "THESIS_EXPERIMENT_MODE", "off"
    ).strip().lower()
    if experiment_mode not in ("off", "active"):
        logger.warning(
            "Invalid THESIS_EXPERIMENT_MODE='%s', defaulting to 'off'",
            experiment_mode,
        )
        experiment_mode = "off"

    # Default skip-LLM threshold
    default_threshold = 0.2

    if experiment_mode == "active":
        raw = os.environ.get("THESIS_SKIP_LLM_EXPERIMENT_THRESHOLD")
        if raw is not None:
            try:
                threshold = float(raw.strip())
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid THESIS_SKIP_LLM_EXPERIMENT_THRESHOLD='%s', "
                    "using default=%s",
                    raw,
                    default_threshold,
                )
                threshold = default_threshold
        else:
            threshold = default_threshold
    else:
        threshold = default_threshold

    return {
        "experiment_mode": experiment_mode,
        "skip_llm_threshold_used": threshold,
    }


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------


def _read_csv(csv_path: Path) -> List[Dict[str, str]]:
    """Read the Notion export CSV, handling BOM and encoding."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(csv_path, newline="", encoding=encoding) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if rows:
                return rows
        except (UnicodeDecodeError, KeyError):
            continue
    raise RuntimeError(f"Could not read CSV at {csv_path}")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze(
    csv_path: Path,
    *,
    split_seed: int = 42,
    split: str = "all",
) -> Dict[str, Any]:
    """Run all CSV companies through production ThesisFilter routing.

    Args:
        csv_path: Path to the Notion-exported pipeline CSV.
        split_seed: Seed for deterministic hold-out split.
        split: 'all', 'train', or 'eval' — filter results to this split.

    Returns:
        Calibration report dict with summary, results, metrics, metadata.
    """
    rows = _read_csv(csv_path)
    logger.info("Loaded %d rows from %s", len(rows), csv_path)

    # Production routing: ThesisFilter with env-based config
    config = ThesisFilterConfig.from_env()
    thesis_filter = ThesisFilter(config=config)
    matcher = thesis_filter._keyword_matcher

    # Experiment mode
    experiment = _resolve_experiment_mode()

    results: List[Dict[str, Any]] = []

    for row in rows:
        company_name = (row.get("Company Name") or "").strip()
        description = (row.get("Short Description") or "").strip()
        website = (row.get("Website") or "").strip()
        status = (row.get("Status") or "").strip()

        domain = _extract_domain(website)
        text = description if description else company_name

        # Hold-out split assignment (uses company name as key)
        split_label = _holdout_split(company_name, seed=split_seed)

        # Keyword scoring (production matcher)
        fit = matcher.score(
            text=text,
            company_name=company_name if description else None,
            domain_name=domain,
        )

        # Production routing via ThesisFilter's cascade-aware router
        routing, path_code = thesis_filter._resolve_cascade_routing(fit)

        # LLM eligibility: score >= skip_llm threshold
        llm_eligible = fit.score >= experiment["skip_llm_threshold_used"]

        results.append(
            {
                "company_name": company_name,
                "status": status,
                "description": description,
                "website": website,
                "domain": domain,
                "has_description": bool(description),
                "keyword_score": round(fit.score, 4),
                "routing": routing.value.upper(),
                "decision_path_code": path_code.value,
                "negative_keywords": list(fit.negative_keywords),
                "matched_keywords": list(fit.matched_keywords),
                "category": fit.thesis.value if fit.thesis else "unknown",
                "confidence": fit.confidence,
                "is_active": status in ACTIVE_STATUSES,
                "consumer_signal_score": round(fit.consumer_signal_score, 4),
                "consumer_anchor_count": fit.consumer_anchor_count,
                "b2b_soft_score": round(fit.b2b_soft_score, 4),
                "split": split_label,
                "llm_eligible": llm_eligible,
            }
        )

    # Filter by split if requested
    if split in ("train", "eval"):
        results = [r for r in results if r["split"] == split]

    return _build_report(results, experiment, split_seed=split_seed, split=split)


def _build_report(
    results: List[Dict[str, Any]],
    experiment: Dict[str, Any],
    *,
    split_seed: int = 42,
    split: str = "all",
) -> Dict[str, Any]:
    """Aggregate individual results into the calibration report."""
    total = len(results)
    has_desc = sum(1 for r in results if r["has_description"])
    no_desc = total - has_desc

    qualified = [r for r in results if r["routing"] == "QUALIFIED"]
    held = [r for r in results if r["routing"] == "HELD"]
    rejected = [r for r in results if r["routing"] == "REJECTED"]

    rejected_by_neg_kw = [r for r in rejected if r["negative_keywords"]]
    rejected_by_low_score = [r for r in rejected if not r["negative_keywords"]]

    active = [r for r in results if r["is_active"]]
    active_rejected = [r for r in active if r["routing"] == "REJECTED"]
    active_held = [r for r in active if r["routing"] == "HELD"]
    active_qualified = [r for r in active if r["routing"] == "QUALIFIED"]

    # Phase 3: Decision path code distribution
    path_code_dist = Counter(r["decision_path_code"] for r in results)

    # Phase 3: Cascade metrics
    hard_veto_count = sum(
        1 for r in results
        if r["decision_path_code"] in (
            DecisionPathCode.VETO_HARD_REJECT.value,
            DecisionPathCode.VETO_WEB3.value,
            DecisionPathCode.VETO_DOMAIN_BLACKLIST.value,
        )
    )
    hard_hold_count = sum(
        1 for r in results
        if r["decision_path_code"] == DecisionPathCode.HOLD_HARD_HOLD.value
    )
    consumer_rescue_count = sum(
        1 for r in results
        if r["decision_path_code"] == DecisionPathCode.QUALIFY_CONSUMER_RESCUE.value
    )
    b2b_guard_block_count = sum(
        1 for r in results
        if r["decision_path_code"] == DecisionPathCode.HOLD_B2B_GUARD_BLOCK.value
    )
    llm_eligible_count = sum(1 for r in results if r["llm_eligible"])

    # Consumer rescue dominance stats (for companies that attempted rescue)
    rescue_attempted = [
        r for r in results
        if r["consumer_signal_score"] >= 0.25 and r["consumer_anchor_count"] >= 1
    ]
    dominance_stats = {}
    if rescue_attempted:
        margins = [
            r["consumer_signal_score"] - r["b2b_soft_score"]
            for r in rescue_attempted
        ]
        dominance_stats = {
            "attempted": len(rescue_attempted),
            "rescued": consumer_rescue_count,
            "blocked": b2b_guard_block_count,
            "margin_mean": round(sum(margins) / len(margins), 4),
            "margin_min": round(min(margins), 4),
            "margin_max": round(max(margins), 4),
        }

    # Negative keyword hit aggregation
    neg_kw_hits: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "companies": []}
    )
    for r in results:
        for kw in r["negative_keywords"]:
            neg_kw_hits[kw]["count"] += 1
            neg_kw_hits[kw]["companies"].append(
                {
                    "name": r["company_name"],
                    "status": r["status"],
                    "description_snippet": _snippet(r["description"]),
                }
            )

    for kw in neg_kw_hits:
        neg_kw_hits[kw]["weight"] = NEGATIVE_KEYWORDS.get(kw, 0.0)

    # Sort by count descending
    neg_kw_hits_sorted = dict(
        sorted(neg_kw_hits.items(), key=lambda x: x[1]["count"], reverse=True)
    )

    # Status breakdown
    status_counter: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"total": 0, "rejected": 0, "held": 0, "qualified": 0}
    )
    for r in results:
        s = r["status"] or "(empty)"
        status_counter[s]["total"] += 1
        status_counter[s][r["routing"].lower()] += 1

    # Category distribution
    category_dist = Counter(r["category"] for r in results)

    # Build rejected active pipeline list
    rejected_active_list = [
        {
            "company_name": r["company_name"],
            "status": r["status"],
            "description": r["description"],
            "keyword_score": r["keyword_score"],
            "negative_keywords": r["negative_keywords"],
            "matched_keywords": r["matched_keywords"],
            "category": r["category"],
            "website": r["website"],
            "decision_path_code": r["decision_path_code"],
        }
        for r in active_rejected
    ]

    report = {
        "metadata": {
            "split_seed": split_seed,
            "split": split,
            "cascade_routing_enablement": os.environ.get(
                "CASCADE_ROUTING_ENABLEMENT", "disabled"
            ),
            **experiment,
        },
        "summary": {
            "total": total,
            "has_description": has_desc,
            "no_description": no_desc,
            "qualified": len(qualified),
            "held": len(held),
            "rejected": len(rejected),
            "rejected_by_negative_kw": len(rejected_by_neg_kw),
            "rejected_by_low_score": len(rejected_by_low_score),
            "rejection_rate_overall": round(len(rejected) / total * 100, 1)
            if total
            else 0,
            "rejection_rate_active_pipeline": round(
                len(active_rejected) / len(active) * 100, 1
            )
            if active
            else 0,
            "qualified_rate_active": round(
                len(active_qualified) / len(active) * 100, 1
            )
            if active
            else 0,
            "rejected_rate_active": round(
                len(active_rejected) / len(active) * 100, 1
            )
            if active
            else 0,
            # Phase 3: Cascade metrics
            "hard_veto_count": hard_veto_count,
            "hard_hold_count": hard_hold_count,
            "consumer_rescue_count": consumer_rescue_count,
            "b2b_guard_block_count": b2b_guard_block_count,
            "consumer_rescue_dominance_stats": dominance_stats,
            "llm_call_eligible_rate": round(
                llm_eligible_count / total * 100, 1
            )
            if total
            else 0,
        },
        "decision_path_code_distribution": dict(path_code_dist),
        "negative_keyword_hits": neg_kw_hits_sorted,
        "rejected_active_pipeline": rejected_active_list,
        "status_breakdown": dict(status_counter),
        "category_distribution": dict(category_dist),
        "results": results,
    }

    return report


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def print_summary(report: Dict[str, Any]) -> None:
    """Print a human-readable summary to stdout."""
    s = report["summary"]
    meta = report.get("metadata", {})

    print("\n" + "=" * 70)
    print("  THESIS FILTER CALIBRATION REPORT")
    print("=" * 70)

    if meta:
        print(f"\n  Cascade mode: {meta.get('cascade_routing_enablement', 'disabled')}")
        print(f"  Experiment mode: {meta.get('experiment_mode', 'off')}")
        print(f"  Split: {meta.get('split', 'all')} (seed={meta.get('split_seed', 42)})")

    print(f"\nTotal companies:           {s['total']}")
    print(f"  With description:        {s['has_description']}")
    print(f"  Without description:     {s['no_description']}")

    print(f"\nRouting breakdown:")
    print(f"  QUALIFIED (score >= 0.3): {s['qualified']}")
    print(f"  HELD (score < 0.3):       {s['held']}")
    print(f"  REJECTED (neg kw):        {s['rejected_by_negative_kw']}")
    print(f"  REJECTED (low score):     {s['rejected_by_low_score']}")
    print(f"  Total rejected:           {s['rejected']}")

    print(f"\nRejection rate (overall):          {s['rejection_rate_overall']}%")
    print(
        f"Rejection rate (active pipeline):  {s['rejection_rate_active_pipeline']}%"
    )

    # Phase 3: Cascade metrics
    print(f"\nCascade metrics:")
    print(f"  Hard vetoes:              {s['hard_veto_count']}")
    print(f"  Hard holds:               {s['hard_hold_count']}")
    print(f"  Consumer rescues:         {s['consumer_rescue_count']}")
    print(f"  B2B guard blocks:         {s['b2b_guard_block_count']}")
    print(f"  LLM-eligible rate:        {s['llm_call_eligible_rate']}%")

    dom_stats = s.get("consumer_rescue_dominance_stats", {})
    if dom_stats:
        print(f"\n  Rescue dominance stats:")
        print(f"    Attempted: {dom_stats['attempted']}, Rescued: {dom_stats['rescued']}, Blocked: {dom_stats['blocked']}")
        print(f"    Margin: mean={dom_stats['margin_mean']}, min={dom_stats['margin_min']}, max={dom_stats['margin_max']}")

    # Decision path code distribution
    dist = report.get("decision_path_code_distribution", {})
    if dist:
        print(f"\nDecision path code distribution:")
        for code, count in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"  {code:<30} {count:>5}")

    # Status breakdown
    print(f"\n{'Status':<25} {'Total':>6} {'Qual':>6} {'Held':>6} {'Rej':>6}")
    print("-" * 55)
    for status, counts in sorted(report["status_breakdown"].items()):
        print(
            f"{status:<25} {counts['total']:>6} {counts['qualified']:>6} "
            f"{counts['held']:>6} {counts['rejected']:>6}"
        )

    # Top negative keywords
    neg_hits = report["negative_keyword_hits"]
    if neg_hits:
        print(f"\nTop negative keywords causing rejections:")
        print(f"  {'Keyword':<30} {'Hits':>5}  {'Weight':>6}")
        print("  " + "-" * 45)
        for kw, info in list(neg_hits.items())[:15]:
            print(f"  {kw:<30} {info['count']:>5}  {info['weight']:>6.2f}")

    # Rejected active pipeline companies
    rejected_active = report["rejected_active_pipeline"]
    if rejected_active:
        print(f"\nActive pipeline companies REJECTED by keyword filter ({len(rejected_active)}):")
        print("-" * 70)
        for r in rejected_active[:30]:
            neg = ", ".join(r["negative_keywords"]) if r["negative_keywords"] else "low score"
            print(f"  {r['company_name']:<30} [{r['status'] or '(empty)'}]")
            print(f"    Score: {r['keyword_score']:.3f}  Neg: {neg}  Path: {r['decision_path_code']}")
            if r["description"]:
                print(f"    Desc:  {_snippet(r['description'], 60)}")
            print()

    # Category distribution
    print(f"\nCategory distribution:")
    for cat, count in sorted(
        report["category_distribution"].items(), key=lambda x: -x[1]
    ):
        print(f"  {cat:<30} {count:>5}")

    print("\n" + "=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Thesis filter calibration: pipeline CSV vs production routing"
    )
    parser.add_argument(
        "csv_path",
        type=Path,
        help="Path to the Notion-exported venture pipeline CSV",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artifacts/thesis_calibration_report.json"),
        help="Output JSON report path (default: artifacts/thesis_calibration_report.json)",
    )
    parser.add_argument(
        "--split-seed",
        type=int,
        default=42,
        help="Seed for deterministic hold-out split (default: 42)",
    )
    parser.add_argument(
        "--split",
        choices=["all", "train", "eval"],
        default="all",
        help="Filter results to train/eval split (default: all)",
    )
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Error: CSV not found at {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    report = analyze(
        args.csv_path,
        split_seed=args.split_seed,
        split=args.split,
    )

    # Write JSON
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Report written to %s", args.out)

    # Print human-readable summary
    print_summary(report)


if __name__ == "__main__":
    main()
