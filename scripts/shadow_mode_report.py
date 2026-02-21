"""
Shadow Mode Report for Warm Intro Indicators.

Queries shadow_log for warm_intro_indicators entries and computes:
- Total shadow entries
- Unique canonical keys enriched
- Score bucket distribution (high/medium/low)
- Source kind distribution (gmail/notion_lp)
- PII lint: verifies no free-text attribution leaked
- Promotion verdict: ready/not-ready with reasons

Usage:
    python scripts/shadow_mode_report.py [--db signals.db] [--days 30]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

FEATURE_NAME = "warm_intro_indicators"

# Fields allowed in WarmIntroIndicator
ALLOWED_FIELDS = {"investor_domain", "score_bucket", "badge", "source_kind"}


async def generate_report(db_path: str, days: int) -> Dict[str, Any]:
    """Generate shadow mode report from shadow_log entries."""
    store = SignalStore(db_path=db_path)
    await store.initialize()

    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        logs = await store.get_shadow_logs(
            feature_name=FEATURE_NAME, since=since, limit=10000
        )

        if not logs:
            return {
                "status": "no_data",
                "message": f"No {FEATURE_NAME} shadow entries in last {days} days",
                "verdict": "not_ready",
                "reasons": ["No shadow data collected yet"],
            }

        # Parse indicators from shadow_log entries
        all_indicators: List[Dict[str, Any]] = []
        canonical_keys = set()
        pii_violations = []

        for log_entry in logs:
            canonical_keys.add(log_entry.get("canonical_key", ""))
            try:
                computed = json.loads(log_entry.get("computed_value", "[]"))
                if isinstance(computed, list):
                    for ind in computed:
                        all_indicators.append(ind)
                        # PII lint: check for unexpected fields
                        extra_fields = set(ind.keys()) - ALLOWED_FIELDS
                        if extra_fields:
                            pii_violations.append({
                                "canonical_key": log_entry.get("canonical_key"),
                                "extra_fields": sorted(extra_fields),
                            })
            except (json.JSONDecodeError, TypeError):
                pass

        # Score bucket distribution
        score_buckets = {"high": 0, "medium": 0, "low": 0}
        for ind in all_indicators:
            bucket = ind.get("score_bucket", "unknown")
            if bucket in score_buckets:
                score_buckets[bucket] += 1

        # Source kind distribution
        source_kinds = {"gmail": 0, "notion_lp": 0}
        for ind in all_indicators:
            kind = ind.get("source_kind", "unknown")
            if kind in source_kinds:
                source_kinds[kind] += 1

        # Promotion verdict
        reasons = []
        if len(logs) < 10:
            reasons.append(f"Only {len(logs)} shadow entries (need >=10)")
        if pii_violations:
            reasons.append(f"{len(pii_violations)} PII violations detected")
        if score_buckets.get("high", 0) == 0 and score_buckets.get("medium", 0) == 0:
            reasons.append("No high/medium-confidence indicators found")

        verdict = "ready" if not reasons else "not_ready"

        return {
            "status": "ok",
            "period_days": days,
            "total_shadow_entries": len(logs),
            "unique_canonical_keys": len(canonical_keys),
            "total_indicators": len(all_indicators),
            "score_buckets": score_buckets,
            "source_kinds": source_kinds,
            "pii_violations": len(pii_violations),
            "pii_details": pii_violations[:5],  # Limit output
            "verdict": verdict,
            "reasons": reasons if reasons else ["All checks passed"],
        }
    finally:
        await store.close()


def main():
    parser = argparse.ArgumentParser(description="Shadow Mode Report for Warm Intro Indicators")
    parser.add_argument("--db", default="signals.db", help="Database path")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    args = parser.parse_args()

    report = asyncio.run(generate_report(args.db, args.days))

    print(json.dumps(report, indent=2))

    if report.get("verdict") == "ready":
        print("\nVERDICT: READY for promotion to live mode")
    else:
        print(f"\nVERDICT: NOT READY")
        for reason in report.get("reasons", []):
            print(f"  - {reason}")


if __name__ == "__main__":
    main()
