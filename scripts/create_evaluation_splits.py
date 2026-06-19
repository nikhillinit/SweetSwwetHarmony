"""Day 2 — Deterministic, stratified evaluation-split creation (read-only DB).

Splits labeled signals into ``train`` / ``calibration`` / ``holdout`` JSON
files under ``state/`` (or ``--out-dir``). Stratification keys:

* label (TP / FP / UNSURE / ADJ from ``signal_quality_metrics.human_label``)
* ``signals.source_api``
* year-month bucket of ``signals.detected_at``

Determinism comes from a SHA-256 hash of ``f"{seed}:{signal_id}"`` mapped to
``[0, 1)``; each row is sorted within its stratum by that hash and assigned to
splits by cumulative-fraction cut-offs. Same seed + same labeled set produces
byte-identical output across machines and Python versions.

Pre-flight: the script invokes the Day 1.5 schema probe before issuing any
data query. If the contract is not satisfied, no state files are written and
the script exits non-zero.

Read-only: the only DB queries are ``PRAGMA table_info`` (via the probe),
``SELECT name FROM sqlite_master``, and a single SELECT join. The DB is
opened with ``file:...?mode=ro``.

Holdout protection contract: the holdout ``signal_ids`` produced here must
never be used as inputs to calibration or threshold-fitting. Day 4+ scripts
should pass ``--holdout-file state/holdout_ids.json`` to the relevant
``ops.cli quality`` commands.
"""

from __future__ import annotations

import argparse
import enum
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

# Bootstrap project root so this script can import sibling scripts when run
# directly (matches the convention in scripts/preflight_check.py).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.inspect_live_schema import (  # noqa: E402  (post-bootstrap import)
    DEFAULT_CONTRACT_PATH,
    inspect_database,
    load_contract,
)
from utils.db_path_helper import resolve_db_path_env  # noqa: E402

DEFAULT_OUT_DIR = Path("state")
DEFAULT_SEED = 42

DEFAULT_FRACTIONS: dict[str, float] = {
    "train": 0.6,
    "calibration": 0.2,
    "holdout": 0.2,
}

SPLIT_FILENAMES: dict[str, str] = {
    "train": "train_ids.json",
    "calibration": "calibration_ids.json",
    "holdout": "holdout_ids.json",
}

EXIT_OK = 0
EXIT_SCHEMA_FAILED = 2
EXIT_NO_LABELED_DATA = 4
EXIT_INVARIANT_FAILED = 5
EXIT_INVALID_ARGS = 64


class LabelSource(str, enum.Enum):
    SIGNAL_QUALITY_METRICS = "signal_quality_metrics.human_label"
    QUALITY_FEEDBACK = "quality_feedback.label"


@dataclass(frozen=True)
class LabeledRow:
    signal_id: int
    label: str
    source_api: str
    year_month: str

    @property
    def stratum(self) -> tuple[str, str, str]:
        return (self.label, self.source_api, self.year_month)


def _validate_fractions(fractions: Mapping[str, float]) -> None:
    expected = {"train", "calibration", "holdout"}
    if set(fractions) != expected:
        raise ValueError(
            f"fractions must have keys {sorted(expected)}, got {sorted(fractions)}"
        )
    if any(v < 0 for v in fractions.values()):
        raise ValueError("fractions must be non-negative")
    total = sum(fractions.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"fractions must sum to 1.0, got {total}")


def _hash_bucket(seed: int, signal_id: int) -> float:
    """Map (seed, signal_id) to a stable float in [0, 1)."""
    digest = hashlib.sha256(f"{seed}:{signal_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def deterministic_split(
    rows: Sequence[LabeledRow],
    *,
    fractions: Mapping[str, float],
    seed: int,
) -> dict[str, list[int]]:
    """Assign each labeled row to exactly one split, stratified by label/source/month.

    Within each stratum, rows are sorted by their hash bucket and sliced by
    cumulative cut-offs. With ``train=0.6, calibration=0.2, holdout=0.2`` and
    a stratum of size 5, the split yields ``[3, 1, 1]`` (rounded by floor on
    the cumulative cut-offs). Singleton strata land in whichever split owns
    bucket 0 of the hash-sorted ordering — i.e., they always land in train,
    matching the largest fraction. This bias is acceptable for tiny strata
    that cannot meaningfully be split.
    """
    _validate_fractions(fractions)

    cutoffs = {
        "train": fractions["train"],
        "calibration": fractions["train"] + fractions["calibration"],
        "holdout": 1.0,
    }

    by_stratum: dict[tuple[str, str, str], list[LabeledRow]] = {}
    for row in rows:
        by_stratum.setdefault(row.stratum, []).append(row)

    splits: dict[str, list[int]] = {"train": [], "calibration": [], "holdout": []}

    for stratum_rows in by_stratum.values():
        ranked = sorted(
            stratum_rows,
            key=lambda r: (_hash_bucket(seed, r.signal_id), r.signal_id),
        )
        n = len(ranked)
        if n == 0:
            continue
        train_end = int(cutoffs["train"] * n)
        cal_end = int(cutoffs["calibration"] * n)
        # Ensure singleton strata land in train deterministically.
        if n == 1:
            train_end, cal_end = 1, 1
        splits["train"].extend(r.signal_id for r in ranked[:train_end])
        splits["calibration"].extend(r.signal_id for r in ranked[train_end:cal_end])
        splits["holdout"].extend(r.signal_id for r in ranked[cal_end:])

    for key in splits:
        splits[key].sort()
    return splits


def _open_ro(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)


def load_labeled_rows(
    db_path: str | os.PathLike[str],
    *,
    label_source: LabelSource = LabelSource.SIGNAL_QUALITY_METRICS,
    time_window_months: Optional[int] = None,
) -> list[LabeledRow]:
    """Read labeled rows joined with signals metadata.

    Deduplicates by ``signal_id``: when multiple labels exist for the same
    signal, the most-recently-recorded one wins. Tiebreakers:

    * ``signal_quality_metrics`` — ``ORDER BY labeled_at DESC, rowid DESC``
    * ``quality_feedback``       — ``ORDER BY created_at DESC, rowid DESC``

    Without this, a signal that was relabeled (e.g., FP → ADJ after a manual
    revisit) would appear twice in the source population and could land in
    multiple splits — silently breaking the holdout-protection contract.
    """
    if label_source is LabelSource.SIGNAL_QUALITY_METRICS:
        # ROW_NUMBER CTE: take latest label per signal_id by labeled_at, then rowid.
        sql = """
            WITH latest AS (
                SELECT m.signal_id,
                       m.human_label,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.signal_id
                           ORDER BY COALESCE(m.labeled_at, '') DESC, m.rowid DESC
                       ) AS rn
                FROM signal_quality_metrics m
                WHERE m.human_label IS NOT NULL
            )
            SELECT latest.signal_id AS signal_id,
                   latest.human_label AS label,
                   s.source_api AS source_api,
                   substr(s.detected_at, 1, 7) AS year_month
            FROM latest
            JOIN signals s ON latest.signal_id = s.id
            WHERE latest.rn = 1
        """
    elif label_source is LabelSource.QUALITY_FEEDBACK:
        sql = """
            WITH latest AS (
                SELECT q.signal_id,
                       q.label,
                       ROW_NUMBER() OVER (
                           PARTITION BY q.signal_id
                           ORDER BY COALESCE(q.created_at, '') DESC, q.rowid DESC
                       ) AS rn
                FROM quality_feedback q
                WHERE q.label IS NOT NULL
            )
            SELECT latest.signal_id AS signal_id,
                   latest.label AS label,
                   s.source_api AS source_api,
                   substr(s.detected_at, 1, 7) AS year_month
            FROM latest
            JOIN signals s ON latest.signal_id = s.id
            WHERE latest.rn = 1
        """
    else:
        raise ValueError(f"unsupported label_source: {label_source!r}")

    params: tuple[Any, ...] = ()
    if time_window_months is not None and time_window_months > 0:
        sql += " AND s.detected_at > datetime('now', ?)"
        params = (f"-{int(time_window_months) * 30} days",)

    con = _open_ro(db_path)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    out: list[LabeledRow] = []
    for r in rows:
        if r["signal_id"] is None or r["label"] is None:
            continue
        out.append(
            LabeledRow(
                signal_id=int(r["signal_id"]),
                label=str(r["label"]),
                source_api=str(r["source_api"] or "unknown"),
                year_month=str(r["year_month"] or "unknown"),
            )
        )
    return out


class SplitInvariantError(AssertionError):
    """Raised when post-split sanity checks fail.

    A failure here indicates a bug in ``deterministic_split`` (or upstream
    duplication that slipped past the dedup CTE), not an operator error.
    """


def assert_split_invariants(
    rows: Sequence[LabeledRow],
    splits: Mapping[str, Sequence[int]],
) -> None:
    """Defense-in-depth: each split is internally unique, splits are pairwise
    disjoint, and the union equals the labeled population.
    """
    expected = {r.signal_id for r in rows}

    seen: dict[int, str] = {}
    for split_name, ids in splits.items():
        ids_list = list(ids)
        if len(ids_list) != len(set(ids_list)):
            dupes = sorted({i for i in ids_list if ids_list.count(i) > 1})
            raise SplitInvariantError(
                f"split '{split_name}' contains duplicate signal_ids: {dupes}"
            )
        for sid in ids_list:
            if sid in seen:
                raise SplitInvariantError(
                    f"signal_id={sid} appears in both '{seen[sid]}' and '{split_name}'"
                )
            seen[sid] = split_name

    union = set(seen.keys())
    if union != expected:
        missing = expected - union
        extra = union - expected
        raise SplitInvariantError(
            f"split union mismatch: missing={sorted(missing)} extra={sorted(extra)}"
        )


def _stratification_breakdown(rows: Iterable[LabeledRow]) -> dict[str, dict[str, int]]:
    by_label: dict[str, int] = {}
    by_source_api: dict[str, int] = {}
    by_year_month: dict[str, int] = {}
    for r in rows:
        by_label[r.label] = by_label.get(r.label, 0) + 1
        by_source_api[r.source_api] = by_source_api.get(r.source_api, 0) + 1
        by_year_month[r.year_month] = by_year_month.get(r.year_month, 0) + 1
    return {
        "by_label": dict(sorted(by_label.items())),
        "by_source_api": dict(sorted(by_source_api.items())),
        "by_year_month": dict(sorted(by_year_month.items())),
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_split_artifacts(
    *,
    out_dir: str | os.PathLike[str],
    rows: Sequence[LabeledRow],
    splits: Mapping[str, Sequence[int]],
    seed: int,
    fractions: Mapping[str, float],
    label_source: LabelSource,
) -> None:
    """Write per-split JSON files plus an aggregate summary."""
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    rows_by_id = {r.signal_id: r for r in rows}
    generated_at = _utc_now_iso()

    sizes: dict[str, int] = {}
    for split_name, signal_ids in splits.items():
        size = len(signal_ids)
        sizes[split_name] = size
        member_rows = [rows_by_id[i] for i in signal_ids if i in rows_by_id]
        payload = {
            "schema_version": 1,
            "generated_at": generated_at,
            "split": split_name,
            "seed": seed,
            "fractions": dict(fractions),
            "label_source": label_source.value,
            "size": size,
            "signal_ids": list(signal_ids),
            "stratification": _stratification_breakdown(member_rows),
        }
        target = out_dir_path / SPLIT_FILENAMES[split_name]
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    summary = {
        "schema_version": 1,
        "generated_at": generated_at,
        "seed": seed,
        "fractions": dict(fractions),
        "label_source": label_source.value,
        "total_rows": len(rows),
        "sizes": sizes,
        "overall_stratification": _stratification_breakdown(rows),
    }
    (out_dir_path / "evaluation_splits_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parse_label_source(value: str) -> LabelSource:
    canonical = value.strip()
    aliases = {
        "signal_quality_metrics": LabelSource.SIGNAL_QUALITY_METRICS,
        "signal_quality_metrics.human_label": LabelSource.SIGNAL_QUALITY_METRICS,
        "quality_feedback": LabelSource.QUALITY_FEEDBACK,
        "quality_feedback.label": LabelSource.QUALITY_FEEDBACK,
    }
    if canonical not in aliases:
        raise argparse.ArgumentTypeError(
            f"label-source must be one of {sorted(aliases)}, got {value!r}"
        )
    return aliases[canonical]


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="create-evaluation-splits",
        description=(
            "Day 2 deterministic stratified split for the Phase 2 learning loop. "
            "Pre-flights the schema probe before reading any labeled data."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite DB (default: DISCOVERY_DB_PATH).",
    )
    parser.add_argument(
        "--contract",
        default=str(DEFAULT_CONTRACT_PATH),
        help="Path to the live schema contract JSON.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Directory to write split files (default: state).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Deterministic seed (default: {DEFAULT_SEED}).",
    )
    parser.add_argument("--train-fraction", type=float, default=DEFAULT_FRACTIONS["train"])
    parser.add_argument(
        "--calibration-fraction", type=float, default=DEFAULT_FRACTIONS["calibration"]
    )
    parser.add_argument(
        "--holdout-fraction", type=float, default=DEFAULT_FRACTIONS["holdout"]
    )
    parser.add_argument(
        "--label-source",
        type=_parse_label_source,
        default=LabelSource.SIGNAL_QUALITY_METRICS,
        help=(
            "Label source: signal_quality_metrics (default) or quality_feedback. "
            "Use the dotted forms (signal_quality_metrics.human_label / "
            "quality_feedback.label) for clarity in dashboards."
        ),
    )
    parser.add_argument(
        "--time-window-months",
        type=int,
        default=None,
        help="If set, restrict to signals detected within the last N months.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute splits but skip writing files.",
    )
    return parser.parse_args(argv)


def _preflight(db_path: str, contract_path: str) -> tuple[bool, dict[str, Any]]:
    contract = load_contract(contract_path)
    report = inspect_database(db_path, contract)
    return bool(report.get("ok")), report


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    args.db = resolve_db_path_env(args.db)
    fractions = {
        "train": float(args.train_fraction),
        "calibration": float(args.calibration_fraction),
        "holdout": float(args.holdout_fraction),
    }
    try:
        _validate_fractions(fractions)
    except ValueError as exc:
        sys.stderr.write(f"invalid fractions: {exc}\n")
        return EXIT_INVALID_ARGS

    try:
        ok, report = _preflight(args.db, args.contract)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"schema preflight failed: {exc}\n")
        return EXIT_SCHEMA_FAILED

    if not ok:
        if report.get("error"):
            sys.stderr.write(
                f"schema preflight failed: {report['error']} (db={args.db})\n"
            )
        else:
            sys.stderr.write(
                f"schema preflight failed: missing_tables={report.get('missing_tables')} "
                f"missing_columns={report.get('missing_columns')}\n"
            )
        return EXIT_SCHEMA_FAILED

    rows = load_labeled_rows(
        args.db,
        label_source=args.label_source,
        time_window_months=args.time_window_months,
    )
    if not rows:
        sys.stderr.write("no labeled rows found; refusing to write empty splits\n")
        return EXIT_NO_LABELED_DATA

    splits = deterministic_split(rows, fractions=fractions, seed=args.seed)

    try:
        assert_split_invariants(rows, splits)
    except SplitInvariantError as exc:
        sys.stderr.write(f"split invariant failed: {exc}\n")
        return EXIT_INVARIANT_FAILED

    if not args.dry_run:
        write_split_artifacts(
            out_dir=args.out_dir,
            rows=rows,
            splits=splits,
            seed=args.seed,
            fractions=fractions,
            label_source=args.label_source,
        )

    summary_line = (
        f"split: train={len(splits['train'])} "
        f"calibration={len(splits['calibration'])} "
        f"holdout={len(splits['holdout'])} "
        f"(seed={args.seed}, label_source={args.label_source.value})"
    )
    sys.stdout.write(summary_line + "\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
