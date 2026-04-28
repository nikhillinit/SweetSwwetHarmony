"""Day 3 — Strategy dashboard generator (read-only).

Materializes five status blocks into a target Markdown file via marker-based
idempotent injection. The plan of record is
``.omx/plans/phase2-day3-dashboards-plan.md``; block contracts and exit-code
semantics in that plan are the source of truth.

The generator never writes to ``signals.db``, never mutates ``state/*``, and
never depends on GitHub API access. Optional artifacts that are missing,
malformed, stale, or empty produce WARN evidence and do not prevent other
blocks from rendering. Required schema-contract violations render a FAIL
readiness gate and return exit ``2``.

Marker contract::

    <!-- harmonic:dashboard:<block>:start -->
    ...generated content...
    <!-- harmonic:dashboard:<block>:end -->

Block names: ``collector_matrix``, ``evaluation_split_integrity``,
``holdout_metrics``, ``gp_workload_capacity``, ``phase2_readiness_guardrails``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ops.collector_health import aggregate_signal_counts, build_health_report
from ops.collector_heartbeat import load_collector_state
from ops.gp_workload import (
    DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM,
    DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM,
    EVENT_LABELS_APPLIED,
    EVENT_REVIEW_SET_GENERATED,
)

EXIT_OK = 0
EXIT_CONTRACT_VIOLATION = 2
EXIT_IO_ERROR = 3
EXIT_MISSING_MARKER = 4

DEFAULT_DB_PATH = "signals.db"
DEFAULT_CONTRACT_PATH = Path(".omx") / "wave6" / "live_schema_contract.json"
DEFAULT_SUMMARY_PATH = Path("state") / "evaluation_splits_summary.json"
DEFAULT_HOLDOUT_PATH = Path("state") / "holdout_ids.json"
DEFAULT_GP_WORKLOAD_PATH = Path("state") / "gp_workload.jsonl"

BLOCK_NAMES: tuple[str, ...] = (
    "collector_matrix",
    "evaluation_split_integrity",
    "holdout_metrics",
    "gp_workload_capacity",
    "phase2_readiness_guardrails",
)
_BLOCK_NAMES_SET = frozenset(BLOCK_NAMES)


class DashboardMarkerError(Exception):
    """Raised when the target Markdown's dashboard markers violate contract.

    Contract violations include duplicate markers, reversed order, partial
    pairs, nested marker pairs, and unknown block names.
    """


_MARKER_RE = re.compile(
    r"^\s*<!--\s*harmonic:dashboard:([a-zA-Z0-9_]+):(start|end)\s*-->\s*$"
)


def _start_marker(block_name: str) -> str:
    return f"<!-- harmonic:dashboard:{block_name}:start -->"


def _end_marker(block_name: str) -> str:
    return f"<!-- harmonic:dashboard:{block_name}:end -->"


def find_dashboard_markers(text: str) -> dict[str, tuple[int, int]]:
    """Locate ``harmonic:dashboard:<block>:{start,end}`` marker pairs.

    Returns a mapping ``block_name -> (start_line_idx, end_line_idx)`` for
    each well-formed pair. Raises :class:`DashboardMarkerError` on:

    * duplicate markers (same block + kind appears more than once)
    * reversed order (an end marker before its corresponding start)
    * partial pairs (a start without an end, or vice versa)
    * nested marker pairs (another block opens before the prior closes)
    * unknown block names (not in :data:`BLOCK_NAMES`)
    """
    located: dict[str, tuple[int, int]] = {}
    seen_starts: dict[str, int] = {}
    seen_ends: dict[str, int] = {}
    current_open: str | None = None
    current_open_line: int = -1

    lines = text.splitlines()
    for idx, line in enumerate(lines):
        match = _MARKER_RE.match(line)
        if not match:
            continue
        block_name, kind = match.group(1), match.group(2)
        if block_name not in _BLOCK_NAMES_SET:
            raise DashboardMarkerError(
                f"unknown dashboard block at line {idx + 1}: "
                f"harmonic:dashboard:{block_name}:{kind}"
            )

        if kind == "start":
            if block_name in seen_starts:
                raise DashboardMarkerError(
                    f"duplicate start marker for {block_name!r} at line {idx + 1}"
                )
            seen_starts[block_name] = idx
            if current_open is not None:
                raise DashboardMarkerError(
                    f"nested start marker {block_name!r} at line {idx + 1}; "
                    f"previous block {current_open!r} not yet closed"
                )
            current_open = block_name
            current_open_line = idx
            continue

        # kind == "end"
        if block_name in seen_ends:
            raise DashboardMarkerError(
                f"duplicate end marker for {block_name!r} at line {idx + 1}"
            )
        seen_ends[block_name] = idx
        if current_open != block_name:
            raise DashboardMarkerError(
                f"end marker {block_name!r} at line {idx + 1} does not match "
                f"current open block {current_open!r}"
            )
        located[block_name] = (current_open_line, idx)
        current_open = None
        current_open_line = -1

    if current_open is not None:
        raise DashboardMarkerError(
            f"start marker {current_open!r} has no matching end marker"
        )

    # Partial pairs: any start without end or end without start (the
    # current_open check above handles dangling start; an end without
    # start would have already triggered the mismatch above).
    dangling_ends = set(seen_ends) - set(seen_starts)
    if dangling_ends:
        raise DashboardMarkerError(
            f"end marker(s) without matching start: {sorted(dangling_ends)}"
        )

    return located


def inject_block_content(text: str, block_name: str, content: str) -> str:
    """Replace the body of ``block_name`` with ``content`` between its markers.

    Idempotent: re-running with the same ``content`` produces a byte-identical
    result. Lines outside the named block are preserved exactly. ``content``
    is normalized to end with exactly one trailing newline.
    """
    located = find_dashboard_markers(text)
    if block_name not in located:
        raise DashboardMarkerError(
            f"cannot inject {block_name!r}: marker pair not found in target"
        )
    start_idx, end_idx = located[block_name]
    lines = text.splitlines(keepends=True)

    # Detect whether the original text ended with a newline; preserve that.
    trailing_newline = text.endswith("\n")

    # Normalize content to a list of newline-terminated lines.
    body = content
    if not body.endswith("\n"):
        body = body + "\n"
    body_lines = body.splitlines(keepends=True)

    # The start marker and end marker lines stay; replace everything between.
    new_lines = (
        lines[: start_idx + 1] + body_lines + lines[end_idx : len(lines)]
    )
    rebuilt = "".join(new_lines)
    if trailing_newline and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt


def append_init_pairs(text: str, missing_blocks: Sequence[str]) -> str:
    """Append empty marker pairs for each block in ``missing_blocks`` order.

    Caller is responsible for passing only blocks not yet present and in
    canonical order. The original ``text`` is preserved verbatim; the new
    marker pairs are appended at the end with a single blank line of
    separation.
    """
    if not missing_blocks:
        return text
    out = text
    if out and not out.endswith("\n"):
        out += "\n"
    appendix_parts: list[str] = []
    for block_name in missing_blocks:
        if block_name not in _BLOCK_NAMES_SET:
            raise DashboardMarkerError(
                f"refusing to append unknown block: {block_name!r}"
            )
        appendix_parts.append("\n")
        appendix_parts.append(_start_marker(block_name) + "\n")
        appendix_parts.append(_end_marker(block_name) + "\n")
    return out + "".join(appendix_parts)

VERDICT_PASS = "PASS"
VERDICT_WARN = "WARN"
VERDICT_FAIL = "FAIL"


@dataclass(frozen=True)
class BlockResult:
    """Carries rendered markdown for a block plus its readiness verdict.

    The CLI surfaces ``rendered`` between markers; the readiness gate
    consumes ``verdict`` and ``evidence`` to populate the traffic-light
    table. Verdicts are uppercase strings: ``PASS`` | ``WARN`` | ``FAIL``.
    """

    rendered: str
    verdict: str
    evidence: str


# ---------------------------------------------------------------------------
# Block 1 — collector_matrix
# ---------------------------------------------------------------------------

_COLLECTOR_MATRIX_HEADERS: tuple[str, ...] = (
    "collector",
    "configured_status",
    "effective_status",
    "last_run_status",
    "last_success_at",
    "signals_found_90d",
    "flags",
)


def _escape_md_cell(value: Any) -> str:
    """Escape a value for safe placement inside a markdown table cell.

    Replaces ``|`` with ``\\|`` so a stray pipe in operator-supplied text
    cannot collapse adjacent cells. Newlines are collapsed to a single
    space — multi-line cells are not part of the matrix contract.
    """
    if value is None:
        return "-"
    text = str(value)
    if not text:
        return "-"
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.replace("|", "\\|")


def _matrix_row(collector: Mapping[str, Any]) -> tuple[str, ...]:
    flags: list[str] = []
    if collector.get("is_silent"):
        flags.append("SILENT")
    if collector.get("is_stale"):
        flags.append("STALE")
    if collector.get("is_failing"):
        flags.append("FAILING")
    if collector.get("override_active"):
        flags.append("OVERRIDE")
    return (
        _escape_md_cell(collector.get("name") or "unknown"),
        _escape_md_cell(collector.get("configured_status") or "unknown"),
        _escape_md_cell(collector.get("effective_status") or "unknown"),
        _escape_md_cell(collector.get("last_run_status") or "unknown"),
        _escape_md_cell(collector.get("last_success_at")),
        _escape_md_cell(collector.get("observed_signal_count", 0)),
        _escape_md_cell(",".join(flags) or "-"),
    )


def _format_md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def build_collector_matrix(report: Mapping[str, Any]) -> str:
    """Render the collector_matrix block from a build_health_report() dict.

    Empty state (``collectors == []``) renders an empty matrix with summary
    totals; missing or partial state never raises. Collectors are sorted by
    name. Cell content is escaped so operator-supplied text containing ``|``
    cannot collapse adjacent cells.
    """
    summary = report.get("summary") or {}
    raw_collectors = report.get("collectors") or []
    collectors_sorted = sorted(
        (c for c in raw_collectors if isinstance(c, Mapping)),
        key=lambda c: str(c.get("name") or ""),
    )

    rows = [_matrix_row(c) for c in collectors_sorted]
    lines = _format_md_table(_COLLECTOR_MATRIX_HEADERS, rows)

    total = int(summary.get("total", 0))
    silent = int(summary.get("silent_count", 0))
    stale = int(summary.get("stale_count", 0))
    failing = int(summary.get("failing_count", 0))
    override = int(summary.get("override_active_count", 0))
    override_names = list(summary.get("override_active_collectors") or [])

    by_status = summary.get("by_effective_status") or {}
    by_status_str = (
        ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())) if by_status else "-"
    )

    lines.append("")
    lines.append(
        f"Totals: total={total} silent_count={silent} stale_count={stale} "
        f"failing_count={failing} override_active_count={override}"
    )
    lines.append(f"By effective_status: {by_status_str}")
    if override_names:
        lines.append(
            "Override-active collectors: " + ", ".join(sorted(override_names))
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Block 2 — evaluation_split_integrity
# ---------------------------------------------------------------------------

_SPLIT_NAMES: tuple[str, ...] = ("train", "calibration", "holdout")
_SPLIT_FILENAMES: dict[str, str] = {name: f"{name}_ids.json" for name in _SPLIT_NAMES}


def _load_optional_json(path: Path) -> tuple[Any | None, str | None]:
    """Load JSON at ``path`` if present.

    Returns ``(payload, error)`` where ``error`` is one of ``None`` (loaded),
    ``"missing"`` (file does not exist), or ``"malformed"`` (file exists but
    JSON parse fails). Read errors propagate as ``"malformed"`` so the caller
    can surface a single warning bucket per artifact.
    """
    if not path.exists():
        return None, "missing"
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, "malformed"


def _check_split_invariants(
    summary: Mapping[str, Any],
    splits: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Return a list of invariant-violation strings for a present split set.

    Each entry is a short, human-readable phrase suitable for the FAIL
    evidence block. An empty list means the split set is internally
    consistent.
    """
    violations: list[str] = []
    expected_seed = summary.get("seed")
    expected_fractions = summary.get("fractions") or {}
    expected_generated = summary.get("generated_at")

    seen_ids: dict[int | str, str] = {}
    for split_name, payload in splits.items():
        ids = payload.get("signal_ids")
        size = payload.get("size")
        seed = payload.get("seed")
        fractions = payload.get("fractions") or {}
        generated_at = payload.get("generated_at")

        if not isinstance(ids, list):
            violations.append(f"{split_name}: signal_ids missing or not a list")
            continue

        if size is not None and size != len(ids):
            violations.append(
                f"{split_name}: size {size} != len(signal_ids) {len(ids)}"
            )

        if expected_seed is not None and seed != expected_seed:
            violations.append(
                f"{split_name}: seed {seed} disagrees with summary seed {expected_seed}"
            )
        if expected_fractions and dict(fractions) != dict(expected_fractions):
            violations.append(
                f"{split_name}: fractions disagree with summary fractions"
            )
        if expected_generated is not None and generated_at != expected_generated:
            violations.append(
                f"{split_name}: generated_at disagrees with summary generated_at"
            )

        # Within-split duplicates.
        if len(set(ids)) != len(ids):
            dupe_count = len(ids) - len(set(ids))
            violations.append(
                f"{split_name}: contains {dupe_count} duplicate id(s) within split"
            )

        # Cross-split overlap detection.
        for sid in ids:
            prior = seen_ids.get(sid)
            if prior is not None and prior != split_name:
                violations.append(
                    f"id {sid!r} overlaps splits {prior} and {split_name}"
                )
            else:
                seen_ids[sid] = split_name

    expected_total = summary.get("total_rows")
    if expected_total is not None:
        if len(seen_ids) != int(expected_total):
            violations.append(
                f"union of unique ids ({len(seen_ids)}) != summary.total_rows ({expected_total})"
            )

    # Deduplicate while preserving order so the rendered evidence is stable.
    seen: set[str] = set()
    unique: list[str] = []
    for v in violations:
        if v in seen:
            continue
        seen.add(v)
        unique.append(v)
    return unique


def _format_split_summary_lines(
    summary: Mapping[str, Any],
    splits: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    total = int(summary.get("total_rows") or 0)
    sizes = summary.get("sizes") or {}
    lines: list[str] = []
    lines.append(
        f"Split summary: seed={summary.get('seed')} "
        f"generated_at={summary.get('generated_at')} total_rows={total}"
    )
    headers = ("split", "size", "pct_of_total", "by_label")
    rows: list[tuple[str, ...]] = []
    for split_name in _SPLIT_NAMES:
        size = int((sizes.get(split_name) or 0))
        pct = (size / total * 100.0) if total else 0.0
        labels = (splits.get(split_name) or {}).get("stratification", {}).get("by_label", {})
        labels_str = (
            ", ".join(f"{k}={v}" for k, v in sorted(labels.items())) if labels else "-"
        )
        rows.append(
            (
                _escape_md_cell(split_name),
                _escape_md_cell(size),
                _escape_md_cell(f"{pct:.1f}%"),
                _escape_md_cell(labels_str),
            )
        )
    lines.extend(_format_md_table(headers, rows))
    return lines


def build_evaluation_split_integrity(
    *,
    summary_path: Path,
    state_dir: Path,
) -> BlockResult:
    """Render the evaluation_split_integrity block.

    Reads the Day 2 split summary and sibling per-split artifacts. Missing
    or malformed optional artifacts produce WARN evidence (no exception).
    Present artifacts that violate consistency invariants render FAIL
    evidence with the offending invariant(s) listed.
    """
    summary_payload, summary_error = _load_optional_json(Path(summary_path))
    if summary_error == "missing":
        return BlockResult(
            rendered=(
                "**WARN — split summary missing.** Expected at "
                f"`{summary_path}`. "
                "Run `python scripts/create_evaluation_splits.py` to materialize.\n"
            ),
            verdict=VERDICT_WARN,
            evidence=f"split summary missing at {summary_path}",
        )
    if summary_error == "malformed" or not isinstance(summary_payload, Mapping):
        return BlockResult(
            rendered=(
                "**WARN — split summary malformed.** "
                f"`{summary_path}` could not be parsed as JSON.\n"
            ),
            verdict=VERDICT_WARN,
            evidence=f"split summary malformed at {summary_path}",
        )

    state_dir = Path(state_dir)
    splits: dict[str, Mapping[str, Any]] = {}
    sibling_warnings: list[str] = []
    for split_name in _SPLIT_NAMES:
        sibling = state_dir / _SPLIT_FILENAMES[split_name]
        payload, error = _load_optional_json(sibling)
        if error == "missing":
            sibling_warnings.append(
                f"sibling `{_SPLIT_FILENAMES[split_name]}` missing"
            )
            continue
        if error == "malformed" or not isinstance(payload, Mapping):
            sibling_warnings.append(
                f"sibling `{_SPLIT_FILENAMES[split_name]}` malformed"
            )
            continue
        splits[split_name] = payload

    if sibling_warnings and not splits:
        return BlockResult(
            rendered=(
                "**WARN — split sibling artifacts unavailable.**\n"
                + "\n".join(f"- {w}" for w in sibling_warnings)
                + "\n"
            ),
            verdict=VERDICT_WARN,
            evidence="; ".join(sibling_warnings),
        )

    violations = _check_split_invariants(summary_payload, splits)
    summary_lines = _format_split_summary_lines(summary_payload, splits)

    if violations:
        body = (
            "**FAIL — split invariant violations:**\n"
            + "\n".join(f"- {v}" for v in violations)
            + "\n\n"
            + "\n".join(summary_lines)
            + "\n"
        )
        return BlockResult(
            rendered=body,
            verdict=VERDICT_FAIL,
            evidence="; ".join(violations[:3]),
        )

    pass_lines = list(summary_lines)
    if sibling_warnings:
        pass_lines.append("")
        pass_lines.append("**Notes:**")
        pass_lines.extend(f"- {w}" for w in sibling_warnings)
    body = "\n".join(pass_lines) + "\n"
    return BlockResult(
        rendered=body,
        verdict=VERDICT_PASS if not sibling_warnings else VERDICT_WARN,
        evidence=(
            "split summary + siblings consistent"
            if not sibling_warnings
            else "; ".join(sibling_warnings)
        ),
    )


# ---------------------------------------------------------------------------
# Block 3 — holdout_metrics
# ---------------------------------------------------------------------------

HOLDOUT_PROTECTION_STATEMENT = (
    "Holdout IDs are protected from threshold fitting and calibration. "
    "Day 4+ threshold/calibration commands must pass "
    "--holdout-file state/holdout_ids.json."
)


def _canonical_holdout_sha(unique_sorted_string_ids: Sequence[str]) -> str:
    """Compute the canonical SHA256 over sorted unique stringified IDs.

    Plan: 'SHA is computed over the canonical UTF-8 JSON serialization of
    sorted unique IDs coerced to strings: json.dumps(sorted_ids,
    separators=(",", ":"), ensure_ascii=False).'
    """
    canonical = json.dumps(
        list(unique_sorted_string_ids),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_holdout_metrics(*, holdout_path: Path) -> BlockResult:
    """Render the holdout_metrics block.

    Reads ``--holdout-ids`` and renders count + canonical SHA + the
    explicit protection statement. **Never** renders the IDs themselves;
    duplicates surface only as a count. Missing or malformed file → WARN
    with no exception.
    """
    payload, error = _load_optional_json(Path(holdout_path))
    if error == "missing":
        return BlockResult(
            rendered=(
                "**WARN — holdout file missing.** Expected at "
                f"`{holdout_path}`. " + HOLDOUT_PROTECTION_STATEMENT + "\n"
            ),
            verdict=VERDICT_WARN,
            evidence=f"holdout file missing at {holdout_path}",
        )
    if error == "malformed" or not isinstance(payload, Mapping):
        return BlockResult(
            rendered=(
                "**WARN — holdout file malformed.** "
                f"`{holdout_path}` could not be parsed as JSON. "
                + HOLDOUT_PROTECTION_STATEMENT
                + "\n"
            ),
            verdict=VERDICT_WARN,
            evidence=f"holdout file malformed at {holdout_path}",
        )

    raw_ids = payload.get("signal_ids")
    if not isinstance(raw_ids, list):
        return BlockResult(
            rendered=(
                "**WARN — holdout file missing `signal_ids` list.** "
                + HOLDOUT_PROTECTION_STATEMENT
                + "\n"
            ),
            verdict=VERDICT_WARN,
            evidence="holdout file missing signal_ids list",
        )

    string_ids = [str(sid) for sid in raw_ids]
    unique_sorted = sorted(set(string_ids))
    total_count = len(string_ids)
    unique_count = len(unique_sorted)
    duplicate_count = total_count - unique_count
    sha = _canonical_holdout_sha(unique_sorted)

    lines: list[str] = []
    lines.append(f"Holdout total entries: {total_count}")
    lines.append(f"Holdout unique entries: {unique_count}")
    if duplicate_count:
        lines.append(f"Holdout duplicate entries: {duplicate_count}")
    lines.append(f"Holdout protected-set SHA256: `{sha}`")
    lines.append("")
    lines.append(HOLDOUT_PROTECTION_STATEMENT)
    rendered = "\n".join(lines) + "\n"

    if duplicate_count:
        return BlockResult(
            rendered=rendered,
            verdict=VERDICT_WARN,
            evidence=(
                f"holdout has {duplicate_count} duplicate entries; "
                "deduplicated for SHA"
            ),
        )
    return BlockResult(
        rendered=rendered,
        verdict=VERDICT_PASS,
        evidence=f"holdout protected; unique_count={unique_count}",
    )


# ---------------------------------------------------------------------------
# Block 4 — gp_workload_capacity
# ---------------------------------------------------------------------------

GP_WORKLOAD_WINDOW_DAYS = 28
_GP_WORKLOAD_WINDOW_WEEKS = GP_WORKLOAD_WINDOW_DAYS / 7  # = 4.0


def _parse_iso_timestamp(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp into a UTC-aware datetime.

    Returns ``None`` for falsy or unparseable values; callers treat
    parse failures as malformed-row events.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class _GpAggregate:
    raw_items: int = 0
    useful_succeeded: int = 0
    rows_in_window: int = 0
    rows_outside_window: int = 0
    malformed_rows: int = 0


def _aggregate_gp_workload_stream(
    jsonl_path: Path, *, as_of: datetime
) -> _GpAggregate:
    """Stream a JSONL workload log and aggregate within the rolling window.

    Iterates line-by-line via the file's iterator protocol so a long log
    will not be loaded into memory. Malformed JSON lines and unparseable
    timestamps increment ``malformed_rows`` and are skipped without
    aborting the aggregation.
    """
    cutoff = as_of - timedelta(days=GP_WORKLOAD_WINDOW_DAYS)

    raw_items = 0
    useful_succeeded = 0
    rows_in_window = 0
    rows_outside_window = 0
    malformed_rows = 0

    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except (json.JSONDecodeError, UnicodeDecodeError):
                malformed_rows += 1
                continue
            if not isinstance(payload, Mapping):
                malformed_rows += 1
                continue

            ts = _parse_iso_timestamp(payload.get("timestamp"))
            if ts is None:
                malformed_rows += 1
                continue
            if ts < cutoff or ts > as_of:
                rows_outside_window += 1
                continue

            rows_in_window += 1
            event = payload.get("event")
            if event == EVENT_REVIEW_SET_GENERATED:
                try:
                    raw_items += int(payload.get("items_count") or 0)
                except (TypeError, ValueError):
                    malformed_rows += 1
                    rows_in_window -= 1
            elif event == EVENT_LABELS_APPLIED:
                try:
                    useful_succeeded += int(payload.get("succeeded") or 0)
                except (TypeError, ValueError):
                    malformed_rows += 1
                    rows_in_window -= 1
            # Unknown event types are tolerated as in-window rows but do
            # not contribute to either headline metric.

    return _GpAggregate(
        raw_items=raw_items,
        useful_succeeded=useful_succeeded,
        rows_in_window=rows_in_window,
        rows_outside_window=rows_outside_window,
        malformed_rows=malformed_rows,
    )


def build_gp_workload_capacity(
    *,
    jsonl_path: Path,
    as_of: datetime,
    raw_review_seconds_per_item: float = DEFAULT_RAW_REVIEW_SECONDS_PER_ITEM,
    useful_label_seconds_per_item: float = DEFAULT_USEFUL_LABEL_SECONDS_PER_ITEM,
) -> BlockResult:
    """Render the gp_workload_capacity block via streaming aggregation.

    Two metrics are kept **separate** per the Day 2 contract: raw event
    review burden and useful labeling capacity. Conflating them has caused
    incorrect headcount and pacing decisions in the past.
    """
    target = Path(jsonl_path)
    if not target.exists():
        return BlockResult(
            rendered=(
                "**WARN — gp_workload log missing.** Expected at "
                f"`{target}`. raw_event_review_minutes_per_week and "
                "useful_label_minutes_per_week cannot be computed offline.\n"
            ),
            verdict=VERDICT_WARN,
            evidence=f"gp_workload log missing at {target}",
        )

    agg = _aggregate_gp_workload_stream(target, as_of=as_of)

    raw_minutes_total = (agg.raw_items * raw_review_seconds_per_item) / 60.0
    useful_minutes_total = (agg.useful_succeeded * useful_label_seconds_per_item) / 60.0
    raw_per_week = raw_minutes_total / _GP_WORKLOAD_WINDOW_WEEKS
    useful_per_week = useful_minutes_total / _GP_WORKLOAD_WINDOW_WEEKS

    headers = ("metric", "minutes_per_week", "items_in_window")
    rows = [
        (
            "raw_event_review_minutes_per_week",
            f"{raw_per_week:.1f}",
            str(agg.raw_items),
        ),
        (
            "useful_label_minutes_per_week",
            f"{useful_per_week:.1f}",
            str(agg.useful_succeeded),
        ),
    ]

    lines: list[str] = []
    lines.append(
        f"GP workload (rolling {GP_WORKLOAD_WINDOW_DAYS}d window, "
        f"as_of={as_of.isoformat()})"
    )
    lines.extend(_format_md_table(headers, rows))
    lines.append("")
    lines.append(
        "Note: raw event review burden does not equal useful labeling "
        "capacity. Both are surfaced separately to avoid headcount/pacing "
        "decisions based on a conflated metric."
    )

    if agg.malformed_rows:
        lines.append("")
        lines.append(f"**Warning:** {agg.malformed_rows} malformed row(s) skipped.")

    rendered = "\n".join(lines) + "\n"

    if agg.rows_in_window == 0:
        return BlockResult(
            rendered=rendered,
            verdict=VERDICT_WARN,
            evidence=(
                f"no rows within rolling {GP_WORKLOAD_WINDOW_DAYS}d window "
                f"(malformed={agg.malformed_rows})"
            ),
        )
    return BlockResult(
        rendered=rendered,
        verdict=VERDICT_PASS,
        evidence=(
            f"rows_in_window={agg.rows_in_window} malformed={agg.malformed_rows}"
        ),
    )


# ---------------------------------------------------------------------------
# Block 5 — phase2_readiness_guardrails
# ---------------------------------------------------------------------------

_INTENTIONAL_EFFECTIVE_STATUSES = {
    "disabled_intentional",
    "disabled_missing_key",
    "blocked_access",
    "deprecated",
}


def _schema_probe_gate(
    db_path: Path, contract_path: Path
) -> tuple[str, str]:
    """Run the pure schema probe and map results to a readiness verdict.

    Uses ``scripts.inspect_live_schema.load_contract`` and ``inspect_database``
    directly — never ``main()``, which would write report files. Mapping:

    * ``ok=True``                         → PASS
    * ``ok=False`` with missing tables/cols → FAIL (eq probe exit 2)
    * contract load error or DB not found → WARN (eq probe exit 3)
    """
    from scripts import inspect_live_schema

    try:
        contract = inspect_live_schema.load_contract(contract_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return VERDICT_WARN, f"contract load error: {exc.__class__.__name__}"

    try:
        report = inspect_live_schema.inspect_database(db_path, contract)
    except (OSError, ValueError) as exc:
        return VERDICT_WARN, f"db inspect error: {exc.__class__.__name__}"

    if report.get("error") == "database_not_found":
        return VERDICT_WARN, f"database not found at {db_path}"
    if report.get("ok"):
        return VERDICT_PASS, "schema contract satisfied"
    missing = report.get("missing_tables") or []
    missing_cols = report.get("missing_columns") or {}
    bits: list[str] = []
    if missing:
        bits.append(f"missing tables: {', '.join(missing)}")
    if missing_cols:
        bits.append(
            "missing columns: "
            + "; ".join(f"{t}={','.join(c)}" for t, c in sorted(missing_cols.items()))
        )
    return VERDICT_FAIL, "; ".join(bits) or "schema contract violation"


def _collector_health_gate(report: Mapping[str, Any]) -> tuple[str, str]:
    summary = report.get("summary") or {}
    silent_count = int(summary.get("silent_count", 0))
    failing_count = int(summary.get("failing_count", 0))
    if silent_count > 0 or failing_count > 0:
        return (
            VERDICT_FAIL,
            f"silent_count={silent_count} failing_count={failing_count}",
        )

    by_status = summary.get("by_effective_status") or {}
    if not by_status:
        return VERDICT_WARN, "no collectors materialized in state"
    if all(status in _INTENTIONAL_EFFECTIVE_STATUSES for status in by_status):
        return (
            VERDICT_WARN,
            "all collectors are intentionally disabled/missing-key/blocked",
        )
    return VERDICT_PASS, "no enabled collector unexpectedly silent"


def _holdout_gate(holdout_result: BlockResult) -> tuple[str, str]:
    """Defense-in-depth: require the protection statement in the rendered block.

    The plan demands ``FAIL if IDs are rendered or the protection statement
    is absent``. ID-rendering is enforced by contract tests on
    ``build_holdout_metrics`` itself; here we re-verify the protection
    statement appears, then trust the upstream verdict.
    """
    if HOLDOUT_PROTECTION_STATEMENT not in holdout_result.rendered:
        return VERDICT_FAIL, "holdout protection statement not rendered"
    return holdout_result.verdict, holdout_result.evidence


_GATE_ROW_HEADERS: tuple[str, ...] = ("gate", "verdict", "evidence")


def build_phase2_readiness_guardrails(
    *,
    db_path: Path,
    contract_path: Path,
    collector_report: Mapping[str, Any],
    split_result: BlockResult,
    holdout_result: BlockResult,
    gp_workload_result: BlockResult,
) -> BlockResult:
    """Render the composite readiness traffic-light table.

    Five gates:

    * ``schema_probe_passes`` — pure schema probe (no GitHub, no writes)
    * ``collector_health_no_unexpected_silence``
    * ``evaluation_splits_present_and_invariants_hold``
    * ``holdout_protection_documented``
    * ``gp_workload_logging_active``

    Aggregate verdict: ``FAIL`` if any gate FAILS, else ``WARN`` if any
    gate WARNs, else ``PASS``. Generation must succeed offline.
    """
    schema_verdict, schema_evidence = _schema_probe_gate(Path(db_path), Path(contract_path))
    collector_verdict, collector_evidence = _collector_health_gate(collector_report)
    holdout_verdict, holdout_evidence = _holdout_gate(holdout_result)

    gates: list[tuple[str, str, str]] = [
        ("schema_probe_passes", schema_verdict, schema_evidence),
        (
            "collector_health_no_unexpected_silence",
            collector_verdict,
            collector_evidence,
        ),
        (
            "evaluation_splits_present_and_invariants_hold",
            split_result.verdict,
            split_result.evidence,
        ),
        ("holdout_protection_documented", holdout_verdict, holdout_evidence),
        ("gp_workload_logging_active", gp_workload_result.verdict, gp_workload_result.evidence),
    ]

    rows = [
        (_escape_md_cell(name), _escape_md_cell(verdict), _escape_md_cell(evidence))
        for (name, verdict, evidence) in gates
    ]
    lines = _format_md_table(_GATE_ROW_HEADERS, rows)

    if any(v == VERDICT_FAIL for (_, v, _) in gates):
        aggregate = VERDICT_FAIL
    elif any(v == VERDICT_WARN for (_, v, _) in gates):
        aggregate = VERDICT_WARN
    else:
        aggregate = VERDICT_PASS

    lines.append("")
    lines.append(f"Aggregate readiness: **{aggregate}**")

    rendered = "\n".join(lines) + "\n"
    return BlockResult(
        rendered=rendered,
        verdict=aggregate,
        evidence=f"aggregate={aggregate}",
    )


# ---------------------------------------------------------------------------
# Atomic write + CLI
# ---------------------------------------------------------------------------


def _atomic_write_text(target: Path, content: str) -> None:
    """Atomically write ``content`` to ``target`` via a temp sibling rename.

    On any failure during write, fsync, or rename, the original ``target``
    is left unchanged and the temp file is cleaned up.
    """
    target = Path(target)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except (AttributeError, OSError):
                # fsync may be unavailable on the platform / FS. Best-effort.
                pass
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _parse_as_of(value: Optional[str]) -> datetime:
    """Parse an ISO date or datetime string into a UTC-aware datetime.

    Accepts ``YYYY-MM-DD`` (interpreted as midnight UTC) and full
    ISO 8601 datetimes. Trailing ``Z`` is normalized to ``+00:00``.
    """
    if value is None:
        return datetime.now(timezone.utc)
    raw = value.strip()
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--as-of must be an ISO 8601 date or datetime: {value!r} ({exc})"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_collector_health_report(db_path: Path) -> Mapping[str, Any]:
    """Build a build_health_report() dict using state + signals.db only.

    Uses ``include_configured=False`` so an empty baseline state file
    renders as zero collectors rather than as the full configured set;
    this honors the plan's empty-baseline contract while still preserving
    override_active when both state and config disagree.
    """
    state = load_collector_state(state_path=None, include_configured=False)
    counts = aggregate_signal_counts(db_path)
    return build_health_report(
        state,
        counts,
        db_path=str(db_path),
    )


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="generate-strategy-dashboard",
        description=(
            "Day 3 read-only strategy dashboard generator. Materializes five "
            "status blocks into a target Markdown file via marker-based "
            "idempotent injection."
        ),
    )
    parser.add_argument("--target", required=True, help="markdown file with the 5 markers")
    parser.add_argument(
        "--init",
        action="store_true",
        help="create missing markers (else missing -> exit 4)",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DISCOVERY_DB_PATH", DEFAULT_DB_PATH),
        help="path to signals.db (default: $DISCOVERY_DB_PATH or signals.db)",
    )
    parser.add_argument(
        "--schema-contract",
        default=str(DEFAULT_CONTRACT_PATH),
        help="path to schema contract JSON",
    )
    parser.add_argument(
        "--split-summary",
        default=str(DEFAULT_SUMMARY_PATH),
        help="path to Day 2 evaluation splits summary",
    )
    parser.add_argument(
        "--holdout-ids",
        default=str(DEFAULT_HOLDOUT_PATH),
        help="path to Day 2 holdout ids artifact",
    )
    parser.add_argument(
        "--gp-workload-jsonl",
        default=str(DEFAULT_GP_WORKLOAD_PATH),
        help="path to GP workload JSONL log",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="ISO date or datetime; anchors rolling windows (default: now UTC)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="optional output path; default rewrites --target in place",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    target_path = Path(args.target)
    output_path = Path(args.output) if args.output else target_path
    as_of = _parse_as_of(args.as_of)

    try:
        original_text = target_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        sys.stderr.write(f"target not found: {target_path}\n")
        return EXIT_IO_ERROR
    except OSError as exc:
        sys.stderr.write(f"failed to read target {target_path}: {exc}\n")
        return EXIT_IO_ERROR

    # 1. Marker discovery / --init.
    text = original_text
    try:
        located = find_dashboard_markers(text)
    except DashboardMarkerError as exc:
        sys.stderr.write(f"marker contract violation: {exc}\n")
        return EXIT_CONTRACT_VIOLATION

    missing = [b for b in BLOCK_NAMES if b not in located]
    if missing and not args.init:
        sys.stderr.write(
            "missing markers; rerun with --init to append: "
            + ", ".join(missing)
            + "\n"
        )
        return EXIT_MISSING_MARKER
    if missing and args.init:
        text = append_init_pairs(text, missing)
        try:
            located = find_dashboard_markers(text)
        except DashboardMarkerError as exc:
            sys.stderr.write(f"marker contract violation after --init: {exc}\n")
            return EXIT_CONTRACT_VIOLATION
        if any(b not in located for b in BLOCK_NAMES):
            sys.stderr.write("--init failed to materialize all marker pairs\n")
            return EXIT_CONTRACT_VIOLATION

    # 2. Build all five blocks. None of these raise.
    db_path = Path(args.db)
    contract_path = Path(args.schema_contract)
    summary_path = Path(args.split_summary)
    holdout_path = Path(args.holdout_ids)
    gp_path = Path(args.gp_workload_jsonl)

    collector_report = _build_collector_health_report(db_path)
    collector_block = build_collector_matrix(collector_report)
    split_result = build_evaluation_split_integrity(
        summary_path=summary_path,
        state_dir=summary_path.parent,
    )
    holdout_result = build_holdout_metrics(holdout_path=holdout_path)
    gp_result = build_gp_workload_capacity(jsonl_path=gp_path, as_of=as_of)
    readiness_result = build_phase2_readiness_guardrails(
        db_path=db_path,
        contract_path=contract_path,
        collector_report=collector_report,
        split_result=split_result,
        holdout_result=holdout_result,
        gp_workload_result=gp_result,
    )

    # 3. Inject content.
    text = inject_block_content(text, "collector_matrix", collector_block)
    text = inject_block_content(text, "evaluation_split_integrity", split_result.rendered)
    text = inject_block_content(text, "holdout_metrics", holdout_result.rendered)
    text = inject_block_content(text, "gp_workload_capacity", gp_result.rendered)
    text = inject_block_content(
        text, "phase2_readiness_guardrails", readiness_result.rendered
    )

    # 4. Atomic write. --output always writes there; --target stays untouched
    #    when --output is set.
    try:
        _atomic_write_text(output_path, text)
    except OSError as exc:
        sys.stderr.write(f"atomic write failed for {output_path}: {exc}\n")
        return EXIT_IO_ERROR

    # 5. Determine exit code from readiness verdicts.
    schema_verdict, _ = _schema_probe_gate(db_path, contract_path)
    if schema_verdict == VERDICT_FAIL:
        return EXIT_CONTRACT_VIOLATION
    if split_result.verdict == VERDICT_FAIL:
        return EXIT_CONTRACT_VIOLATION
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
