"""GP workload report CLI.

Reads ``state/gp_workload.jsonl`` (the JSONL log written by the
``ops.cli quality learning-loop review-set`` and ``apply-labels`` hooks) and
emits a human-readable summary plus an optional JSON payload.

Two headline metrics, kept deliberately separate per the Phase 2 plan:

* ``raw_event_review_minutes_per_week`` — burden from scanning review-sets
* ``useful_label_minutes_per_week``     — actual labeling capacity

Read-only. The script never writes the JSONL log itself; it only summarizes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

# Bootstrap project root so this script can import sibling packages when run
# directly (matches the convention in scripts/preflight_check.py).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ops.gp_workload import (  # noqa: E402  (post-bootstrap import)
    DEFAULT_LOG_PATH,
    DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM,
    DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM,
    render_summary_table,
    summarize_workload,
)


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gp-workload-report",
        description=(
            "Summarize GP labeling capacity vs raw-event review burden over a "
            "rolling window. Reads state/gp_workload.jsonl."
        ),
    )
    parser.add_argument(
        "--log",
        default=str(DEFAULT_LOG_PATH),
        help="Path to gp_workload.jsonl (default: state/gp_workload.jsonl).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="Rolling window for the per-week metrics (default: 7).",
    )
    parser.add_argument(
        "--raw-review-seconds",
        type=float,
        default=DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM,
        help=f"Seconds per raw-event review (default: {DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM}).",
    )
    parser.add_argument(
        "--useful-label-seconds",
        type=float,
        default=DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM,
        help=f"Seconds per applied label (default: {DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM}).",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    summary = summarize_workload(
        args.log,
        window_days=args.window_days,
        raw_review_seconds_per_item=args.raw_review_seconds,
        useful_label_seconds_per_item=args.useful_label_seconds,
    )
    if args.format == "json":
        sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_summary_table(summary) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
