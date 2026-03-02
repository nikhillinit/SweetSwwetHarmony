#!/usr/bin/env python3
"""
Thesis Filter Calibration: Pipeline CSV vs Keyword Matcher.

Runs all companies from the Notion-exported venture pipeline CSV through
ThesisMatcher.score() (keyword-only, no LLM) to identify where the filter
is too aggressive and would reject companies the team actually sourced.

Usage:
    python scripts/analyze_pipeline_thesis.py path/to/venture_pipeline.csv
    python scripts/analyze_pipeline_thesis.py pipeline.csv --out artifacts/thesis_calibration_report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import tldextract

from utils.thesis_matcher import ThesisMatcher

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOLD_THRESHOLD = 0.3  # Matches pipeline.py thesis_hold_threshold

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


def _classify_routing(score: float, has_negative_kw: bool) -> str:
    """Classify a score into QUALIFIED / HELD / REJECTED."""
    if has_negative_kw and score < HOLD_THRESHOLD:
        return "REJECTED"
    if score < HOLD_THRESHOLD:
        return "HELD"
    return "QUALIFIED"


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


def analyze(csv_path: Path) -> Dict[str, Any]:
    """Run all CSV companies through ThesisMatcher and build the report."""
    rows = _read_csv(csv_path)
    logger.info("Loaded %d rows from %s", len(rows), csv_path)

    matcher = ThesisMatcher()
    results: List[Dict[str, Any]] = []

    for row in rows:
        company_name = (row.get("Company Name") or "").strip()
        description = (row.get("Short Description") or "").strip()
        website = (row.get("Website") or "").strip()
        status = (row.get("Status") or "").strip()

        domain = _extract_domain(website)
        text = description if description else company_name

        fit = matcher.score(
            text=text,
            company_name=company_name if description else None,
            domain_name=domain,
        )

        routing = _classify_routing(fit.score, bool(fit.negative_keywords))

        results.append(
            {
                "company_name": company_name,
                "status": status,
                "description": description,
                "website": website,
                "domain": domain,
                "has_description": bool(description),
                "keyword_score": round(fit.score, 4),
                "routing": routing,
                "negative_keywords": list(fit.negative_keywords),
                "matched_keywords": list(fit.matched_keywords),
                "category": fit.thesis.value if fit.thesis else "unknown",
                "confidence": fit.confidence,
                "is_active": status in ACTIVE_STATUSES,
            }
        )

    return _build_report(results)


def _build_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    # Import NEGATIVE_KEYWORDS for weights
    from utils.thesis_matcher import NEGATIVE_KEYWORDS

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
        }
        for r in active_rejected
    ]

    report = {
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
        },
        "negative_keyword_hits": neg_kw_hits_sorted,
        "rejected_active_pipeline": rejected_active_list,
        "status_breakdown": dict(status_counter),
        "category_distribution": dict(category_dist),
    }

    return report


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------


def print_summary(report: Dict[str, Any]) -> None:
    """Print a human-readable summary to stdout."""
    s = report["summary"]

    print("\n" + "=" * 70)
    print("  THESIS FILTER CALIBRATION REPORT")
    print("=" * 70)

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
            print(f"    Score: {r['keyword_score']:.3f}  Neg: {neg}")
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
        description="Thesis filter calibration: pipeline CSV vs keyword matcher"
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
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Error: CSV not found at {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    report = analyze(args.csv_path)

    # Write JSON
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Report written to %s", args.out)

    # Print human-readable summary
    print_summary(report)


if __name__ == "__main__":
    main()
