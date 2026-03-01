"""Phase-0 gate: evaluate FP-rate convergence before advancing activation tiers.

Reports current FP rate, trend direction, and whether the halt-week
convergence target has been met.

Usage:
    python scripts/evaluate_phase0_gate.py --db signals.db --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


HALT_WEEK = 4
TARGET_CONVERGENCE_PCT = 10.0


def _get_weekly_fp_rates(
    conn: sqlite3.Connection, weeks: int = HALT_WEEK + 1
) -> list:
    """Get weekly FP rates from quality_metrics_daily.

    Schema uses normalised rows: metric_name='overall_fp_rate', value=rate, n=sample_count.
    """
    now = datetime.now(timezone.utc)
    rates = []
    for w in range(weeks):
        week_end = now - timedelta(weeks=w)
        week_start = week_end - timedelta(weeks=1)
        row = conn.execute(
            "SELECT AVG(value) AS avg_rate, COALESCE(SUM(n), 0) AS total_n "
            "FROM quality_metrics_daily "
            "WHERE metric_name = 'overall_fp_rate' "
            "  AND metric_date >= ? AND metric_date < ?",
            (week_start.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")),
        ).fetchone()
        avg_rate = row[0]  # None if no rows
        total_n = row[1]
        rate = (avg_rate * 100.0) if avg_rate is not None else None
        rates.append({"week": weeks - 1 - w, "n": total_n, "rate": rate})
    rates.sort(key=lambda r: r["week"])
    return rates


def _compute_trend(rates: list) -> Optional[str]:
    """Compute trend from weekly FP rates (improving/stable/worsening)."""
    valid = [r for r in rates if r["rate"] is not None]
    if len(valid) < 2:
        return None
    recent = valid[-1]["rate"]
    previous = valid[-2]["rate"]
    delta = recent - previous
    if abs(delta) < 1.0:
        return "stable"
    return "improving" if delta < 0 else "worsening"


def _determine_tier(current_rate: Optional[float]) -> str:
    """Map current FP rate to a tier label."""
    if current_rate is None:
        return "insufficient_data"
    if current_rate <= TARGET_CONVERGENCE_PCT:
        return "converged"
    if current_rate <= 25.0:
        return "approaching"
    return "early"


def evaluate_phase0_gate(db_path: str) -> Dict[str, Any]:
    """Evaluate Phase-0 gate and return structured report."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        # Check table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='quality_metrics_daily'"
        )
        if cursor.fetchone() is None:
            return {
                "tier": "insufficient_data",
                "halt_week": HALT_WEEK,
                "target_convergence_pct": TARGET_CONVERGENCE_PCT,
                "current": None,
                "trend": None,
                "error": "quality_metrics_daily table missing",
            }

        rates = _get_weekly_fp_rates(conn)
        valid_rates = [r for r in rates if r["rate"] is not None]
        current = valid_rates[-1]["rate"] if valid_rates else None
        trend = _compute_trend(rates)
        tier = _determine_tier(current)

        return {
            "tier": tier,
            "halt_week": HALT_WEEK,
            "target_convergence_pct": TARGET_CONVERGENCE_PCT,
            "current": round(current, 2) if current is not None else None,
            "trend": trend,
            "weekly_rates": rates,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Phase-0 convergence gate")
    parser.add_argument("--db", required=True, help="Path to signals.db")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    sys.stdout.reconfigure(errors="replace")

    result = evaluate_phase0_gate(args.db)

    if args.json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Tier: {result['tier']}")
        print(f"Halt week: {result['halt_week']}")
        print(f"Target: {result['target_convergence_pct']}%")
        print(f"Current FP rate: {result['current']}%")
        print(f"Trend: {result['trend']}")

    sys.exit(0 if result["tier"] != "early" else 1)


if __name__ == "__main__":
    main()
