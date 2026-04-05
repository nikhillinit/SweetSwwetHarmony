"""Export all signals with existing + proposed labels to CSV for review.

Label semantics:
  TP   = right sector + right stage + right geo (actionable deal candidate)
  FP   = wrong sector (B2B, crypto, dev tools, etc.)
  ADJ  = right sector, wrong stage/geo/maturity (filter correct, not investable)
  UNSURE = can't determine from available info
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Thesis-positive keywords (consumer sector fit)
CONSUMER_TP_SIGNALS = {
    "meal kit", "food delivery", "beverage", "skincare", "beauty", "cosmetic",
    "wellness", "fitness", "mental health", "meditation", "nutrition", "diet",
    "travel", "hospitality", "hotel", "booking", "restaurant", "cpg",
    "consumer marketplace", "e-commerce", "ecommerce", "dtc", "d2c",
    "direct-to-consumer", "pet food", "snack", "grocery", "health tech",
    "telehealth", "wearable", "sleep", "supplement", "organic food",
    "plant-based", "vegan", "gluten-free", "marketplace", "retail",
    "fashion", "apparel", "clothing", "personal care", "food tech",
    "cheese", "sparkling water", "salad", "skin science", "beverage company",
    "skincare line", "grilled cheese", "kefir", "probiotic",
    "food brand", "food safety", "food market",
}

# Thesis-negative keywords (wrong sector)
FP_SIGNALS = {
    "b2b", "enterprise", "saas", "developer tool", "devtool", "dev tool",
    "infrastructure", "api platform", "sdk", "framework", "database", "cloud platform",
    "kubernetes", "docker", "ci/cd", "crypto", "blockchain", "web3",
    "nft", "defi", "token", "forex", "mining", "bitcoin", "ethereum",
    "iot", "embedded", "hardware", "chip", "semiconductor",
    "cybersecurity", "security tool", "waf", "firewall",
    "arxiv", "research paper", "academic",
    "open-source framework", "rag system", "ai agent",
    "code editor", "ide", "paas", "oauth", "payroll software",
    "clojure", "rust lang", "ebpf", "mcp server", "dsl",
}

# Late-stage / public-company markers (trigger ADJ, not FP)
LATE_STAGE_MARKERS = [
    "nyse:", "nasdaq:", "(nyse", "(nasdaq", "publicly traded",
    "series c", "series d", "series e", "ipo ",
    "billion", "quarterly earnings", "annual revenue",
    "fleetwide", "78 restaurant", "78 location",
]

KNOWN_PUBLIC_BRANDS = [
    "etsy", "borden", "seabourn", "carnival", "american greetings",
    "ardagh", "kbp brands", "pepsico", "bubly", "pepsi",
    "darden", "red robin", "j & j snack", "j &amp; j snack",
    "karat packaging", "lifeway foods", "ball corporation",
    "cbdmd", "restaurant brands international",
]

EXISTING_FP_TO_ADJ = {46, 98, 99, 171, 596}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="Path to SQLite database")
    parser.add_argument("--out", default="labeling_review_v2.csv", help="CSV output path")
    return parser


def _is_late_stage(text_lower):
    for marker in LATE_STAGE_MARKERS:
        if marker in text_lower:
            return True, marker
    for brand in KNOWN_PUBLIC_BRANDS:
        if brand in text_lower:
            return True, brand
    return False, None


def propose_label(name, desc, ckey, stype, src):
    text_lower = " ".join([name or "", desc or "", ckey or "", stype or "", src or ""]).lower()

    fp_matches = [kw for kw in FP_SIGNALS if kw in text_lower]
    tp_matches = [kw for kw in CONSUMER_TP_SIGNALS if kw in text_lower]

    if src == "arxiv":
        return "FP", "arxiv research paper, not a startup"

    if tp_matches:
        is_late, marker = _is_late_stage(text_lower)
        if is_late:
            return "ADJ", "Consumer fit but wrong stage/maturity: " + marker
        if fp_matches:
            return "UNSURE", "Mixed: TP(" + ", ".join(tp_matches[:2]) + ") FP(" + ", ".join(fp_matches[:2]) + ")"
        return "TP", "Consumer thesis fit: " + ", ".join(tp_matches[:3])

    if fp_matches:
        return "FP", "Non-thesis: " + ", ".join(fp_matches[:3])

    return "UNSURE", "No strong thesis signal detected"


def propose_correction(sid, existing_label, text_lower):
    if existing_label != "FP":
        return "", ""
    if sid in EXISTING_FP_TO_ADJ:
        is_late, marker = _is_late_stage(text_lower)
        return "ADJ", "Reclassify: consumer sector fit, wrong stage (" + (marker or "public/late") + ")"
    return "", ""


def main(argv: list[str] | None = None) -> int:
    from utils.db_path_helper import resolve_db_path_env

    args = build_parser().parse_args(argv)
    db_path = resolve_db_path_env(args.db_path)
    conn = sqlite3.connect(db_path)

    rows = conn.execute(
        """
        SELECT s.id, s.signal_type, s.source_api, s.canonical_key, s.company_name,
               s.confidence, s.created_at, s.raw_data,
               sqm.human_label, sqm.labeled_at, sqm.notes
        FROM signals s
        LEFT JOIN signal_quality_metrics sqm ON s.id = sqm.signal_id
        ORDER BY s.id
        """
    ).fetchall()

    out_path = args.out
    stats = {
        "existing": 0,
        "existing_correction": 0,
        "proposed_tp": 0,
        "proposed_fp": 0,
        "proposed_adj": 0,
        "proposed_unsure": 0,
    }

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "signal_id", "source_api", "company_name", "canonical_key",
                "confidence", "created_at", "description", "url",
                "existing_label", "labeled_at", "label_notes",
                "proposed_label", "proposed_reason",
            ]
        )

        for row in rows:
            sid, stype, src, ckey, name, conf, created, raw_str, existing, labeled_at, notes = row
            try:
                raw_data = json.loads(raw_str) if raw_str else {}
            except Exception:
                raw_data = {}
            desc = raw_data.get("description", raw_data.get("title", raw_data.get("readme_excerpt", raw_data.get("abstract", ""))))
            if not isinstance(desc, str):
                desc = str(desc) if desc else ""
            desc = desc[:300]
            url = raw_data.get("url", raw_data.get("html_url", raw_data.get("source_url", "")))

            proposed_label = ""
            proposed_reason = ""

            if existing:
                text_lower = " ".join([name or "", desc, ckey or ""]).lower()
                proposed_label, proposed_reason = propose_correction(sid, existing, text_lower)
                if proposed_label:
                    stats["existing_correction"] += 1
                stats["existing"] += 1
            else:
                proposed_label, proposed_reason = propose_label(name, desc, ckey, stype, src)
                key = {
                    "TP": "proposed_tp",
                    "FP": "proposed_fp",
                    "ADJ": "proposed_adj",
                    "UNSURE": "proposed_unsure",
                }[proposed_label]
                stats[key] += 1

            writer.writerow(
                [
                    sid, src, name or "", ckey,
                    conf, created[:10], desc, url or "",
                    existing or "", labeled_at or "", notes or "",
                    proposed_label, proposed_reason,
                ]
            )

    print(f"Exported {len(rows)} signals from {db_path} to {out_path}")
    print()
    print("Existing labels:")
    print(f"  Total:       {stats['existing']}")
    print(f"  Corrections: {stats['existing_correction']} (FP -> ADJ)")
    print()
    print("Proposed labels for unlabeled signals:")
    print(f"  TP:    {stats['proposed_tp']}")
    print(f"  FP:    {stats['proposed_fp']}")
    print(f"  ADJ:   {stats['proposed_adj']}")
    print(f"  UNSURE: {stats['proposed_unsure']}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
