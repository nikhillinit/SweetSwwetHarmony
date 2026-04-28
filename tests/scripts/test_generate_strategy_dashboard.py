"""Day 3 — strategy dashboard generator contract tests.

The generator at ``scripts.generate_strategy_dashboard`` materializes five
status blocks into a Markdown target via marker-based idempotent injection.
This suite exercises the block builders, marker harness, and CLI invariants.

The plan of record is ``.omx/plans/phase2-day3-dashboards-plan.md``. Block
contracts and exit-code semantics in that plan are the source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures: build_health_report-shaped dicts without invoking the real
# heartbeat machinery, so block-builder tests stay fast and isolated.
# ---------------------------------------------------------------------------


def _empty_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": "2026-04-28T08:00:00+00:00",
        "db_path": "signals.db",
        "lookback_days": 90,
        "signal_counts": [],
        "collectors": [],
        "summary": {
            "total": 0,
            "by_effective_status": {},
            "silent_count": 0,
            "stale_count": 0,
            "failing_count": 0,
            "override_active_count": 0,
            "override_active_collectors": [],
            "unmapped_source_apis": [],
            "warnings": [],
        },
    }


def _collector_entry(
    name: str,
    *,
    configured_status: str = "enabled",
    effective_status: str = "healthy",
    last_run_status: str = "success",
    last_success_at: str | None = "2026-04-28T07:00:00+00:00",
    observed_signal_count: int = 5,
    override_active: bool = False,
    is_silent: bool = False,
    is_stale: bool = False,
    is_failing: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "configured_status": configured_status,
        "configured_status_reason": None,
        "expected_cadence_hours": 24,
        "last_run_status": last_run_status,
        "last_finished_at": last_success_at,
        "last_success_at": last_success_at,
        "consecutive_failures": 0,
        "effective_status": effective_status,
        "expected_source_apis": [name],
        "observed_signal_count": observed_signal_count,
        "is_stale": is_stale,
        "is_silent": is_silent,
        "is_failing": is_failing,
        "override_active": override_active,
    }


def _report_with(*collectors: dict[str, Any], **summary_overrides: Any) -> dict[str, Any]:
    report = _empty_report()
    report["collectors"] = list(collectors)
    summary = report["summary"]
    summary["total"] = len(collectors)
    by_status: dict[str, int] = {}
    silent = stale = failing = override = 0
    override_names: list[str] = []
    for c in collectors:
        es = c.get("effective_status", "unknown")
        by_status[es] = by_status.get(es, 0) + 1
        if c.get("is_silent"):
            silent += 1
        if c.get("is_stale"):
            stale += 1
        if c.get("is_failing"):
            failing += 1
        if c.get("override_active"):
            override += 1
            override_names.append(c["name"])
    summary["by_effective_status"] = dict(sorted(by_status.items()))
    summary["silent_count"] = silent
    summary["stale_count"] = stale
    summary["failing_count"] = failing
    summary["override_active_count"] = override
    summary["override_active_collectors"] = override_names
    summary.update(summary_overrides)
    return report


# ===========================================================================
# Block 1 — collector_matrix
# ===========================================================================


def test_collector_matrix_handles_empty_collectors_state_baseline():
    """Empty `state/collectors.json` baseline ({}) must render without raising.

    Day 3 plan: "state/collectors.json may be the tracked empty baseline {}.
    The generator must not require locally materialized collector runtime
    state to be committed; empty or partially populated state renders a
    warning/empty matrix rather than raising."
    """
    from scripts.generate_strategy_dashboard import build_collector_matrix

    rendered = build_collector_matrix(_empty_report())
    assert isinstance(rendered, str)
    assert "0" in rendered  # surfaces total=0 somewhere


def test_collector_matrix_includes_override_active_fields():
    """Each collector's `override_active` flag must be visible in the matrix."""
    from scripts.generate_strategy_dashboard import build_collector_matrix

    report = _report_with(
        _collector_entry("arxiv"),
        _collector_entry(
            "github",
            configured_status="blocked_access",
            effective_status="blocked_access",
            last_run_status="not_run",
            last_success_at=None,
            observed_signal_count=0,
            override_active=True,
        ),
    )
    rendered = build_collector_matrix(report)
    assert "github" in rendered
    # Override surfaces with a clearly readable indicator.
    assert "OVERRIDE" in rendered or "override" in rendered.lower()


def test_collector_matrix_summary_includes_override_active_count():
    """Summary line must include override_active_count from build_health_report."""
    from scripts.generate_strategy_dashboard import build_collector_matrix

    report = _report_with(
        _collector_entry("arxiv"),
        _collector_entry(
            "github",
            configured_status="disabled_intentional",
            effective_status="disabled_intentional",
            last_run_status="not_run",
            override_active=True,
        ),
        _collector_entry(
            "linkedin",
            configured_status="disabled_intentional",
            effective_status="disabled_intentional",
            last_run_status="not_run",
            override_active=True,
        ),
    )
    rendered = build_collector_matrix(report)
    assert "override_active_count=2" in rendered
    # Override-active list must name both overridden collectors.
    assert "github" in rendered
    assert "linkedin" in rendered


def test_collector_matrix_sorts_collectors_deterministically():
    """Collectors must render in sorted-by-name order regardless of input order."""
    from scripts.generate_strategy_dashboard import build_collector_matrix

    report = _report_with(
        _collector_entry("zeta"),
        _collector_entry("alpha"),
        _collector_entry("middle"),
    )
    rendered = build_collector_matrix(report)
    pos_alpha = rendered.find("alpha")
    pos_middle = rendered.find("middle")
    pos_zeta = rendered.find("zeta")
    assert pos_alpha != -1 and pos_middle != -1 and pos_zeta != -1
    assert pos_alpha < pos_middle < pos_zeta


def test_collector_matrix_escapes_markdown_table_cells():
    """Pipes inside collector fields must not break the markdown table.

    Defensive: collector names should not contain ``|``, but
    ``configured_status_reason`` or other operator-supplied text could.
    """
    from scripts.generate_strategy_dashboard import build_collector_matrix

    report = _report_with(
        _collector_entry(
            "weird|collector",
            last_run_status="error|details",
        ),
    )
    rendered = build_collector_matrix(report)
    # Find the data row by looking for the (escaped) collector name.
    row_lines = [
        line
        for line in rendered.splitlines()
        if "weird" in line and "collector" in line and line.lstrip().startswith("|")
    ]
    assert row_lines, f"expected a table row containing the collector, got:\n{rendered}"
    for line in row_lines:
        # A valid markdown row has separators between cells. Stripping the
        # outer pipes, the count of unescaped `|` separators must equal
        # cells - 1; if cells contain raw `|`, it explodes.
        inner = line.strip().strip("|")
        # No raw `|` left in any cell — every `|` should be escaped (e.g. `\|`)
        # or replaced. We accept either escape style as long as cells still
        # parse: every literal `|` separator must be flanked by spaces.
        # Easiest invariant: rendered row must NOT contain `||` (empty cell
        # caused by a stray `|` colliding with the next pipe).
        assert "||" not in inner, f"unescaped pipe collapsed cells: {line!r}"


def test_disabled_or_blocked_collectors_not_counted_as_unexpected_silent():
    """Plan: 'Missing/blocked collectors surface their configured_status
    (disabled_missing_key, blocked_access, etc.) without false-flagging as
    silent, preserving the heartbeat-v2 sticky-intent contract.'
    """
    from scripts.generate_strategy_dashboard import build_collector_matrix

    report = _report_with(
        _collector_entry(
            "linkedin",
            configured_status="blocked_access",
            effective_status="blocked_access",
            last_run_status="not_run",
            last_success_at=None,
            observed_signal_count=0,
            is_silent=False,
        ),
        _collector_entry(
            "github",
            configured_status="disabled_missing_key",
            effective_status="disabled_missing_key",
            last_run_status="not_run",
            last_success_at=None,
            observed_signal_count=0,
            is_silent=False,
        ),
    )
    rendered = build_collector_matrix(report)
    assert "blocked_access" in rendered
    assert "disabled_missing_key" in rendered
    # Summary must not claim these as silent.
    assert "silent_count=0" in rendered or "silent: 0" in rendered.lower()


# ===========================================================================
# Block 2 — evaluation_split_integrity
# ===========================================================================


def _write_summary(state_dir: Path, **overrides: Any) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-04-27T08:00:00+00:00",
        "seed": 42,
        "fractions": {"train": 0.6, "calibration": 0.2, "holdout": 0.2},
        "label_source": "signal_quality_metrics.human_label",
        "total_rows": 30,
        "sizes": {"train": 18, "calibration": 6, "holdout": 6},
        "overall_stratification": {
            "by_label": {"FP": 20, "TP": 10},
            "by_source_api": {"hacker_news": 15, "rss_feeds": 15},
            "by_year_month": {"2026-02": 30},
        },
    }
    payload.update(overrides)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "evaluation_splits_summary.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def _write_split(
    state_dir: Path,
    *,
    split: str,
    signal_ids: list[int],
    seed: int = 42,
    fractions: dict[str, float] | None = None,
    generated_at: str = "2026-04-27T08:00:00+00:00",
    stratification: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "split": split,
        "seed": seed,
        "fractions": dict(fractions or {"train": 0.6, "calibration": 0.2, "holdout": 0.2}),
        "label_source": "signal_quality_metrics.human_label",
        "size": len(signal_ids),
        "signal_ids": list(signal_ids),
        "stratification": stratification
        or {
            "by_label": {"FP": len(signal_ids)},
            "by_source_api": {"hacker_news": len(signal_ids)},
            "by_year_month": {"2026-02": len(signal_ids)},
        },
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / f"{split}_ids.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def test_missing_split_summary_emits_warning_block_not_exception(tmp_path):
    """Missing summary must WARN, not raise. Exit code 0 path."""
    from scripts.generate_strategy_dashboard import build_evaluation_split_integrity

    result = build_evaluation_split_integrity(
        summary_path=tmp_path / "evaluation_splits_summary.json",
        state_dir=tmp_path,
    )
    assert result.verdict == "WARN"
    assert "missing" in result.rendered.lower() or "warn" in result.rendered.lower()


def test_malformed_split_summary_emits_warning_block_not_exception(tmp_path):
    """Malformed summary JSON must WARN, not raise."""
    from scripts.generate_strategy_dashboard import build_evaluation_split_integrity

    target = tmp_path / "evaluation_splits_summary.json"
    target.write_text("{not valid json", encoding="utf-8")

    result = build_evaluation_split_integrity(
        summary_path=target, state_dir=tmp_path
    )
    assert result.verdict == "WARN"
    assert "malformed" in result.rendered.lower() or "warn" in result.rendered.lower()


def test_split_artifact_invariant_violation_surfaces_fail_evidence(tmp_path):
    """Present artifacts with overlapping IDs across splits must FAIL.

    Plan: 'Present artifacts with invariant violations render FAIL in
    readiness evidence; strict mode may return exit 2.'
    """
    from scripts.generate_strategy_dashboard import build_evaluation_split_integrity

    summary_path = _write_summary(
        tmp_path, sizes={"train": 4, "calibration": 2, "holdout": 2}, total_rows=8
    )
    # train and holdout overlap on id=3 — invariant violation.
    _write_split(tmp_path, split="train", signal_ids=[1, 2, 3, 4])
    _write_split(tmp_path, split="calibration", signal_ids=[5, 6])
    _write_split(tmp_path, split="holdout", signal_ids=[3, 7])

    result = build_evaluation_split_integrity(
        summary_path=summary_path, state_dir=tmp_path
    )
    assert result.verdict == "FAIL"
    assert "overlap" in result.rendered.lower() or "fail" in result.rendered.lower()


def test_split_summary_present_with_consistent_artifacts_passes(tmp_path):
    """Happy path: summary + 3 sibling files, all consistent → PASS evidence."""
    from scripts.generate_strategy_dashboard import build_evaluation_split_integrity

    summary_path = _write_summary(
        tmp_path,
        sizes={"train": 4, "calibration": 2, "holdout": 2},
        total_rows=8,
    )
    _write_split(tmp_path, split="train", signal_ids=[1, 2, 3, 4])
    _write_split(tmp_path, split="calibration", signal_ids=[5, 6])
    _write_split(tmp_path, split="holdout", signal_ids=[7, 8])

    result = build_evaluation_split_integrity(
        summary_path=summary_path, state_dir=tmp_path
    )
    assert result.verdict == "PASS"
    # Counts/percentages surface
    assert "8" in result.rendered  # total_rows somewhere
    assert "train" in result.rendered.lower()
    assert "holdout" in result.rendered.lower()


def test_split_summary_seed_mismatch_against_per_split_fails(tmp_path):
    """Plan: 'summary and per-split artifacts agree on generated_at, seed, fractions'."""
    from scripts.generate_strategy_dashboard import build_evaluation_split_integrity

    summary_path = _write_summary(
        tmp_path,
        seed=42,
        sizes={"train": 4, "calibration": 2, "holdout": 2},
        total_rows=8,
    )
    _write_split(tmp_path, split="train", signal_ids=[1, 2, 3, 4], seed=42)
    _write_split(tmp_path, split="calibration", signal_ids=[5, 6], seed=42)
    # Seed disagreement on holdout — stale split artifact scenario.
    _write_split(tmp_path, split="holdout", signal_ids=[7, 8], seed=99)

    result = build_evaluation_split_integrity(
        summary_path=summary_path, state_dir=tmp_path
    )
    assert result.verdict == "FAIL"


# ===========================================================================
# Block 3 — holdout_metrics
# ===========================================================================


PROTECTION_STATEMENT = (
    "Holdout IDs are protected from threshold fitting and calibration. "
    "Day 4+ threshold/calibration commands must pass "
    "--holdout-file state/holdout_ids.json."
)


def _write_holdout(
    tmp_path: Path, *, signal_ids: list[Any], filename: str = "holdout_ids.json"
) -> Path:
    payload = {
        "schema_version": 1,
        "generated_at": "2026-04-27T08:00:00+00:00",
        "split": "holdout",
        "seed": 42,
        "fractions": {"train": 0.6, "calibration": 0.2, "holdout": 0.2},
        "label_source": "signal_quality_metrics.human_label",
        "size": len(signal_ids),
        "signal_ids": list(signal_ids),
        "stratification": {"by_label": {"FP": len(signal_ids)}},
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / filename
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def test_missing_holdout_file_emits_warning_block(tmp_path):
    """Missing holdout file → WARN, not raise."""
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    result = build_holdout_metrics(holdout_path=tmp_path / "holdout_ids.json")
    assert result.verdict == "WARN"
    assert "missing" in result.rendered.lower() or "warn" in result.rendered.lower()


def test_malformed_holdout_file_emits_warning_block_not_exception(tmp_path):
    """Malformed JSON → WARN, not raise."""
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    target = tmp_path / "holdout_ids.json"
    target.write_text("{ this is not valid", encoding="utf-8")
    result = build_holdout_metrics(holdout_path=target)
    assert result.verdict == "WARN"


def test_holdout_metrics_includes_protection_statement(tmp_path):
    """Plan: must render the explicit protection statement."""
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    target = _write_holdout(tmp_path, signal_ids=[1, 2, 3, 4, 5])
    result = build_holdout_metrics(holdout_path=target)
    assert "Holdout IDs are protected" in result.rendered
    assert "--holdout-file state/holdout_ids.json" in result.rendered


def test_holdout_metrics_does_not_render_ids(tmp_path):
    """Critical security: IDs must never appear in the rendered block.

    Using string IDs with non-hex characters (``id-A-N``) so a coincidental
    SHA-hex substring cannot produce a false-positive leak match.
    """
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    distinctive_ids = ["id-A-1", "id-A-2", "id-A-3", "id-A-4", "id-A-5"]
    target = _write_holdout(tmp_path, signal_ids=distinctive_ids)
    result = build_holdout_metrics(holdout_path=target)
    for sid in distinctive_ids:
        assert sid not in result.rendered, (
            f"holdout id {sid!r} leaked into rendered block"
        )
    # Also check the canonical-JSON serialization marker can't slip in.
    assert "id-A-" not in result.rendered


def test_holdout_sha_is_order_independent(tmp_path):
    """SHA over sorted unique IDs → same SHA regardless of input order."""
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    a = _write_holdout(tmp_path / "a", signal_ids=[3, 1, 4, 1, 5, 9, 2, 6])
    b = _write_holdout(tmp_path / "b", signal_ids=[9, 6, 5, 4, 3, 2, 1, 1])
    result_a = build_holdout_metrics(holdout_path=a)
    result_b = build_holdout_metrics(holdout_path=b)

    # Pull the SHA hex string out of each rendered block.
    import re

    sha_a = re.search(r"\b([0-9a-f]{64})\b", result_a.rendered)
    sha_b = re.search(r"\b([0-9a-f]{64})\b", result_b.rendered)
    assert sha_a and sha_b, "expected SHA256 hex in rendered block"
    assert sha_a.group(1) == sha_b.group(1)


def test_holdout_sha_canonicalizes_string_ids(tmp_path):
    """SHA must coerce IDs to strings via canonical JSON serialization.

    Plan: 'SHA is computed over the canonical UTF-8 JSON serialization of
    sorted unique IDs coerced to strings: json.dumps(sorted_ids,
    separators=(",", ":"), ensure_ascii=False).'
    """
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    int_path = _write_holdout(tmp_path / "int", signal_ids=[1, 2, 3])
    str_path = _write_holdout(tmp_path / "str", signal_ids=["1", "2", "3"])
    int_result = build_holdout_metrics(holdout_path=int_path)
    str_result = build_holdout_metrics(holdout_path=str_path)

    import re

    sha_int = re.search(r"\b([0-9a-f]{64})\b", int_result.rendered)
    sha_str = re.search(r"\b([0-9a-f]{64})\b", str_result.rendered)
    assert sha_int and sha_str
    # Coerce-to-string canonicalization makes int ids and string-ids equivalent.
    assert sha_int.group(1) == sha_str.group(1)


def test_holdout_duplicate_ids_warn_without_rendering_ids(tmp_path):
    """Duplicates → surfaced as a count, never as IDs."""
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    distinctive = ["dup-X-1", "dup-X-1", "dup-X-2", "dup-X-2", "dup-X-3"]
    target = _write_holdout(tmp_path, signal_ids=distinctive)
    result = build_holdout_metrics(holdout_path=target)
    for sid in distinctive:
        assert sid not in result.rendered
    assert "dup-X-" not in result.rendered
    # Must count the duplicates somewhere.
    assert "duplicate" in result.rendered.lower()


def test_holdout_metrics_unique_count_is_reported(tmp_path):
    """The unique count must be present in the rendered block.

    The TOTAL count of IDs in the file equals 5; with 2 duplicates the
    unique count is 3. Both numbers should be safe to render (they are
    counts, not IDs).
    """
    from scripts.generate_strategy_dashboard import build_holdout_metrics

    target = _write_holdout(tmp_path, signal_ids=[1, 1, 2, 2, 3])
    result = build_holdout_metrics(holdout_path=target)
    assert "3" in result.rendered  # unique count surfaces
    assert result.verdict in ("PASS", "WARN")  # duplicates → WARN policy


# ===========================================================================
# Block 4 — gp_workload_capacity
# ===========================================================================


def _write_jsonl(target: Path, events: list[dict[str, Any]]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        for evt in events:
            fh.write(json.dumps(evt, sort_keys=True) + "\n")
    return target


def _gp_event(
    *, ts: datetime, event: str = "review_set_generated", **fields: Any
) -> dict[str, Any]:
    base = {
        "timestamp": ts.astimezone(timezone.utc).isoformat(),
        "event": event,
        "runner": fields.pop("runner", "test"),
    }
    base.update(fields)
    return base


def test_missing_gp_workload_emits_warning_block(tmp_path):
    """Missing JSONL → WARN, must NOT raise."""
    from scripts.generate_strategy_dashboard import build_gp_workload_capacity

    as_of = datetime(2026, 4, 28, 8, 0, 0, tzinfo=timezone.utc)
    result = build_gp_workload_capacity(
        jsonl_path=tmp_path / "gp_workload.jsonl", as_of=as_of
    )
    assert result.verdict == "WARN"
    assert "missing" in result.rendered.lower() or "warn" in result.rendered.lower()


def test_gp_workload_keeps_raw_and_useful_separate(tmp_path):
    """Day 2 contract: raw event review minutes ≠ useful label minutes.

    100 raw events reviewed, 10 labels successfully applied. The two
    metrics MUST remain separate; conflating them is the failure mode the
    Day 2 prerequisite #9 was written to prevent.
    """
    from scripts.generate_strategy_dashboard import build_gp_workload_capacity

    as_of = datetime(2026, 4, 28, 8, 0, 0, tzinfo=timezone.utc)
    target = tmp_path / "gp_workload.jsonl"
    events = [
        _gp_event(ts=as_of - timedelta(days=1), items_count=100),
        _gp_event(
            ts=as_of - timedelta(days=2),
            event="labels_applied",
            attempted=15,
            succeeded=10,
            failed=5,
        ),
    ]
    _write_jsonl(target, events)

    result = build_gp_workload_capacity(jsonl_path=target, as_of=as_of)
    # Both metrics surface, distinct.
    assert "raw_event_review_minutes_per_week" in result.rendered
    assert "useful_label_minutes_per_week" in result.rendered
    # Sanity on numeric magnitude: per Day 2 constants, 100 raw items at
    # 30s/item → 50 minutes total / 4 weeks = 12.5 min/wk. 10 useful labels
    # at 45s/item → 7.5 minutes total / 4 weeks ≈ 1.9 min/wk. The two
    # values must NOT be identical (which would prove conflation).
    import re

    nums = re.findall(r"\d+\.\d", result.rendered)
    assert len(set(nums)) >= 2, (
        f"raw and useful metrics appear conflated; rendered:\n{result.rendered}"
    )


def test_gp_workload_rolling_window_uses_as_of(tmp_path):
    """Same data + different --as-of must produce different windows."""
    from scripts.generate_strategy_dashboard import build_gp_workload_capacity

    target = tmp_path / "gp_workload.jsonl"
    early = datetime(2026, 1, 15, 8, 0, 0, tzinfo=timezone.utc)
    events = [_gp_event(ts=early, items_count=100)]
    _write_jsonl(target, events)

    # as_of within 28d of `early` → row counted.
    in_window = early + timedelta(days=10)
    in_result = build_gp_workload_capacity(jsonl_path=target, as_of=in_window)
    # as_of long after `early` → row outside the 28d rolling window.
    out_window = early + timedelta(days=60)
    out_result = build_gp_workload_capacity(jsonl_path=target, as_of=out_window)

    assert in_result.rendered != out_result.rendered


def test_gp_workload_ignores_rows_outside_4_week_window(tmp_path):
    """Rows older than 28 days from --as-of must not affect the metrics."""
    from scripts.generate_strategy_dashboard import build_gp_workload_capacity

    as_of = datetime(2026, 4, 28, 8, 0, 0, tzinfo=timezone.utc)
    target = tmp_path / "gp_workload.jsonl"
    events = [
        # Inside the 28-day window.
        _gp_event(ts=as_of - timedelta(days=5), items_count=40),
        # Way outside.
        _gp_event(ts=as_of - timedelta(days=200), items_count=999_999),
    ]
    _write_jsonl(target, events)

    result = build_gp_workload_capacity(jsonl_path=target, as_of=as_of)
    # The huge out-of-window count must NOT inflate the metrics.
    assert "999999" not in result.rendered.replace(",", "")


def test_gp_workload_streams_large_file_without_loading_all_rows(tmp_path, monkeypatch):
    """Plan: 'Stream-read JSONL so a long file will not OOM the report.'

    Contract test: patch ``Path.read_text`` to raise so any
    ``read_text``-style full-file load fails fast. Streaming-iterator
    implementations using ``with path.open(...)`` succeed.
    """
    from scripts.generate_strategy_dashboard import build_gp_workload_capacity

    as_of = datetime(2026, 4, 28, 8, 0, 0, tzinfo=timezone.utc)
    target = tmp_path / "gp_workload.jsonl"
    events = [
        _gp_event(ts=as_of - timedelta(days=i % 28), items_count=10) for i in range(200)
    ]
    _write_jsonl(target, events)

    original_read_text = Path.read_text

    def _no_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        raise AssertionError(
            f"build_gp_workload_capacity must stream, not call read_text on {self}"
        )

    monkeypatch.setattr(Path, "read_text", _no_read_text)
    try:
        result = build_gp_workload_capacity(jsonl_path=target, as_of=as_of)
    finally:
        monkeypatch.setattr(Path, "read_text", original_read_text)

    assert result.verdict in ("PASS", "WARN", "FAIL")


def test_gp_workload_malformed_rows_warn_and_do_not_abort(tmp_path):
    """Malformed JSONL rows must be skipped and counted in a warning line."""
    from scripts.generate_strategy_dashboard import build_gp_workload_capacity

    as_of = datetime(2026, 4, 28, 8, 0, 0, tzinfo=timezone.utc)
    target = tmp_path / "gp_workload.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        # Valid event.
        fh.write(
            json.dumps(_gp_event(ts=as_of - timedelta(days=1), items_count=20))
            + "\n"
        )
        # Garbage line.
        fh.write("{not valid json\n")
        # Empty line.
        fh.write("\n")
        # Another valid event.
        fh.write(
            json.dumps(
                _gp_event(
                    ts=as_of - timedelta(days=2),
                    event="labels_applied",
                    attempted=5,
                    succeeded=3,
                    failed=2,
                )
            )
            + "\n"
        )

    result = build_gp_workload_capacity(jsonl_path=target, as_of=as_of)
    # Malformed-row count surfaces.
    assert "malformed" in result.rendered.lower()
    # Function did not raise.
    assert result.verdict in ("PASS", "WARN", "FAIL")


# ===========================================================================
# Block 5 — phase2_readiness_guardrails
# ===========================================================================


def _write_contract(tmp_path: Path, *, required_tables: dict[str, dict[str, Any]] | None = None) -> Path:
    contract = {
        "version": 1,
        "description": "test contract",
        "verified_against": "test",
        "required_tables": required_tables
        or {"signals": {"required_columns": ["id", "canonical_key"]}},
        "forbidden_references": [],
    }
    target = tmp_path / "contract.json"
    target.write_text(json.dumps(contract), encoding="utf-8")
    return target


def _create_signals_db(tmp_path: Path, *, columns: list[str] | None = None) -> Path:
    import sqlite3

    db_path = tmp_path / "signals.db"
    cols = columns or ["id INTEGER PRIMARY KEY", "canonical_key TEXT"]
    con = sqlite3.connect(db_path)
    try:
        con.execute(f"CREATE TABLE signals ({', '.join(cols)})")
        con.commit()
    finally:
        con.close()
    return db_path


def _ok_block(rendered: str = "ok") -> Any:
    from scripts.generate_strategy_dashboard import BlockResult, VERDICT_PASS

    return BlockResult(rendered=rendered, verdict=VERDICT_PASS, evidence="ok")


def _warn_block(rendered: str = "warn") -> Any:
    from scripts.generate_strategy_dashboard import BlockResult, VERDICT_WARN

    return BlockResult(rendered=rendered, verdict=VERDICT_WARN, evidence="warn")


def _fail_block(rendered: str = "fail") -> Any:
    from scripts.generate_strategy_dashboard import BlockResult, VERDICT_FAIL

    return BlockResult(rendered=rendered, verdict=VERDICT_FAIL, evidence="fail")


def test_phase2_readiness_does_not_call_github_api(tmp_path, monkeypatch):
    """The readiness gate must succeed offline. No HTTP calls."""
    from scripts.generate_strategy_dashboard import build_phase2_readiness_guardrails

    db = _create_signals_db(tmp_path)
    contract = _write_contract(tmp_path)

    # Defensive: any HTTP attempt explodes.
    import urllib.request
    import urllib.error

    def _no_http(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("readiness gate must not make HTTP calls")

    monkeypatch.setattr(urllib.request, "urlopen", _no_http)
    try:
        import requests  # noqa: F401

        monkeypatch.setattr("requests.get", _no_http, raising=False)
        monkeypatch.setattr("requests.post", _no_http, raising=False)
    except ImportError:
        pass

    result = build_phase2_readiness_guardrails(
        db_path=db,
        contract_path=contract,
        collector_report=_empty_report(),
        split_result=_warn_block("split warn"),
        holdout_result=_warn_block("holdout warn"),
        gp_workload_result=_warn_block("gp workload warn"),
    )
    assert result.verdict in ("PASS", "WARN", "FAIL")
    # All five gate names appear in the rendered traffic-light table.
    for gate in (
        "schema_probe_passes",
        "collector_health_no_unexpected_silence",
        "evaluation_splits_present_and_invariants_hold",
        "holdout_protection_documented",
        "gp_workload_logging_active",
    ):
        assert gate in result.rendered


def test_phase2_readiness_traffic_lights_match_underlying_state(tmp_path):
    """Each row's verdict is derived from the underlying upstream state."""
    from scripts.generate_strategy_dashboard import build_phase2_readiness_guardrails

    db = _create_signals_db(tmp_path)
    contract = _write_contract(tmp_path)

    # Force collector_health: enabled-but-silent → FAIL on that row.
    silent_report = _report_with(
        _collector_entry(
            "arxiv",
            effective_status="healthy",
            observed_signal_count=0,
            is_silent=True,
        )
    )

    result = build_phase2_readiness_guardrails(
        db_path=db,
        contract_path=contract,
        collector_report=silent_report,
        split_result=_ok_block("split ok"),
        holdout_result=_ok_block(
            "holdout ok\n" + PROTECTION_STATEMENT
        ),
        gp_workload_result=_ok_block("gp ok"),
    )
    # collector_health row should show FAIL.
    assert "FAIL" in result.rendered
    # Aggregate verdict is FAIL when any gate FAILs.
    assert result.verdict == "FAIL"


def test_phase2_readiness_schema_probe_exit2_is_fail(tmp_path):
    """Live schema missing required column → contract violation → FAIL."""
    from scripts.generate_strategy_dashboard import build_phase2_readiness_guardrails

    # Contract requires `id` AND `canonical_key`; DB has only `id`.
    contract = _write_contract(
        tmp_path,
        required_tables={
            "signals": {"required_columns": ["id", "canonical_key", "missing_col"]}
        },
    )
    db = _create_signals_db(tmp_path, columns=["id INTEGER PRIMARY KEY", "canonical_key TEXT"])

    result = build_phase2_readiness_guardrails(
        db_path=db,
        contract_path=contract,
        collector_report=_empty_report(),
        split_result=_ok_block(),
        holdout_result=_ok_block(PROTECTION_STATEMENT),
        gp_workload_result=_ok_block(),
    )
    # Find the schema_probe_passes row and confirm FAIL.
    schema_row = next(
        (line for line in result.rendered.splitlines() if "schema_probe_passes" in line),
        "",
    )
    assert "FAIL" in schema_row, f"schema row was: {schema_row!r}"


def test_phase2_readiness_schema_probe_exit3_is_warn(tmp_path):
    """Probe load error (missing DB file) → WARN, not FAIL."""
    from scripts.generate_strategy_dashboard import build_phase2_readiness_guardrails

    contract = _write_contract(tmp_path)
    missing_db = tmp_path / "does_not_exist.db"

    result = build_phase2_readiness_guardrails(
        db_path=missing_db,
        contract_path=contract,
        collector_report=_empty_report(),
        split_result=_ok_block(),
        holdout_result=_ok_block(PROTECTION_STATEMENT),
        gp_workload_result=_ok_block(),
    )
    schema_row = next(
        (line for line in result.rendered.splitlines() if "schema_probe_passes" in line),
        "",
    )
    assert "WARN" in schema_row, f"schema row was: {schema_row!r}"


def test_phase2_readiness_schema_probe_contract_load_error_is_warn(tmp_path):
    """Bad contract path / unparseable contract → WARN (eq exit 3)."""
    from scripts.generate_strategy_dashboard import build_phase2_readiness_guardrails

    db = _create_signals_db(tmp_path)
    bad_contract = tmp_path / "bad_contract.json"
    bad_contract.write_text("{not json", encoding="utf-8")

    result = build_phase2_readiness_guardrails(
        db_path=db,
        contract_path=bad_contract,
        collector_report=_empty_report(),
        split_result=_ok_block(),
        holdout_result=_ok_block(PROTECTION_STATEMENT),
        gp_workload_result=_ok_block(),
    )
    schema_row = next(
        (line for line in result.rendered.splitlines() if "schema_probe_passes" in line),
        "",
    )
    assert "WARN" in schema_row


def test_phase2_readiness_holdout_protection_statement_required(tmp_path):
    """If the holdout block does not contain the protection statement,
    the holdout_protection_documented gate must FAIL.
    """
    from scripts.generate_strategy_dashboard import build_phase2_readiness_guardrails

    db = _create_signals_db(tmp_path)
    contract = _write_contract(tmp_path)

    bad_holdout = _ok_block(rendered="some other text without the magic phrase")
    result = build_phase2_readiness_guardrails(
        db_path=db,
        contract_path=contract,
        collector_report=_empty_report(),
        split_result=_ok_block(),
        holdout_result=bad_holdout,
        gp_workload_result=_ok_block(),
    )
    holdout_row = next(
        (line for line in result.rendered.splitlines() if "holdout_protection" in line),
        "",
    )
    assert "FAIL" in holdout_row, f"holdout row was: {holdout_row!r}"


# ===========================================================================
# Marker discovery + idempotent injection harness
# ===========================================================================


CANONICAL_BLOCKS = (
    "collector_matrix",
    "evaluation_split_integrity",
    "holdout_metrics",
    "gp_workload_capacity",
    "phase2_readiness_guardrails",
)


def _markers_for(block: str) -> tuple[str, str]:
    return (
        f"<!-- harmonic:dashboard:{block}:start -->",
        f"<!-- harmonic:dashboard:{block}:end -->",
    )


def _well_formed_target(blocks: list[str] = list(CANONICAL_BLOCKS)) -> str:
    out: list[str] = ["# Strategy Dashboard\n", "Some preamble.\n", "\n"]
    for b in blocks:
        s, e = _markers_for(b)
        out.append(s + "\n")
        out.append(f"placeholder content for {b}\n")
        out.append(e + "\n")
        out.append("\n")
    out.append("\nTrailing prose, do not touch.\n")
    return "".join(out)


def test_find_dashboard_markers_returns_block_locations():
    from scripts.generate_strategy_dashboard import find_dashboard_markers

    text = _well_formed_target()
    located = find_dashboard_markers(text)
    assert set(located.keys()) == set(CANONICAL_BLOCKS)


def test_find_dashboard_markers_raises_on_duplicate_marker():
    from scripts.generate_strategy_dashboard import (
        DashboardMarkerError,
        find_dashboard_markers,
    )

    s, e = _markers_for("collector_matrix")
    text = "\n".join([s, "x", e, s, "y", e]) + "\n"
    with pytest.raises(DashboardMarkerError):
        find_dashboard_markers(text)


def test_find_dashboard_markers_raises_on_partial_pair():
    from scripts.generate_strategy_dashboard import (
        DashboardMarkerError,
        find_dashboard_markers,
    )

    s, _e = _markers_for("collector_matrix")
    text = s + "\nlonely start, no end\n"
    with pytest.raises(DashboardMarkerError):
        find_dashboard_markers(text)


def test_find_dashboard_markers_raises_on_reversed_order():
    from scripts.generate_strategy_dashboard import (
        DashboardMarkerError,
        find_dashboard_markers,
    )

    s, e = _markers_for("collector_matrix")
    text = "\n".join([e, "content", s]) + "\n"
    with pytest.raises(DashboardMarkerError):
        find_dashboard_markers(text)


def test_find_dashboard_markers_raises_on_unknown_block_name():
    from scripts.generate_strategy_dashboard import (
        DashboardMarkerError,
        find_dashboard_markers,
    )

    text = (
        "<!-- harmonic:dashboard:nope_unknown_block:start -->\n"
        "x\n"
        "<!-- harmonic:dashboard:nope_unknown_block:end -->\n"
    )
    with pytest.raises(DashboardMarkerError):
        find_dashboard_markers(text)


def test_inject_block_content_replaces_only_between_markers():
    from scripts.generate_strategy_dashboard import inject_block_content

    text = _well_formed_target()
    new_text = inject_block_content(text, "collector_matrix", "FRESH MATRIX\n")
    # Original content removed.
    assert "placeholder content for collector_matrix" not in new_text
    # New content present.
    assert "FRESH MATRIX" in new_text
    # Other blocks left alone.
    assert "placeholder content for holdout_metrics" in new_text
    # Surrounding prose preserved.
    assert "Some preamble." in new_text
    assert "Trailing prose, do not touch." in new_text


def test_inject_block_content_is_idempotent():
    from scripts.generate_strategy_dashboard import inject_block_content

    text = _well_formed_target()
    once = inject_block_content(text, "collector_matrix", "stable content\n")
    twice = inject_block_content(once, "collector_matrix", "stable content\n")
    assert once == twice


def test_append_init_pairs_appends_in_canonical_order():
    from scripts.generate_strategy_dashboard import append_init_pairs

    bare = "# Dashboard\n\nNo markers here.\n"
    new_text = append_init_pairs(bare, list(CANONICAL_BLOCKS))
    # Each canonical block appears exactly once.
    for b in CANONICAL_BLOCKS:
        s, e = _markers_for(b)
        assert new_text.count(s) == 1
        assert new_text.count(e) == 1
    # Order: collector_matrix < ... < phase2_readiness_guardrails
    positions = [
        new_text.find(_markers_for(b)[0]) for b in CANONICAL_BLOCKS
    ]
    assert positions == sorted(positions)
    # Original prose preserved.
    assert new_text.startswith("# Dashboard\n\nNo markers here.\n")


def test_append_init_pairs_preserves_existing_valid_pairs():
    from scripts.generate_strategy_dashboard import (
        append_init_pairs,
        find_dashboard_markers,
    )

    s, e = _markers_for("collector_matrix")
    text = f"# Doc\n\n{s}\nMATRIX\n{e}\n\nPostlude.\n"
    # Caller decides which blocks need appending — collector_matrix is
    # already present so it must not be appended again.
    missing = [b for b in CANONICAL_BLOCKS if b != "collector_matrix"]
    new_text = append_init_pairs(text, missing)
    located = find_dashboard_markers(new_text)
    assert set(located.keys()) == set(CANONICAL_BLOCKS)
    # No duplicate collector_matrix markers.
    assert new_text.count(s) == 1
    assert new_text.count(e) == 1


# ===========================================================================
# CLI integration: main() + atomic write + read-only invariants
# ===========================================================================


import sqlite3


def _setup_complete_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Build a full set of input fixtures + a markered target.

    Points COLLECTOR_STATE_PATH at an empty state-v2 doc and
    COLLECTOR_CONFIG_PATH at a nonexistent file so collector health
    materializes with zero rows. Returns the canonical CLI paths.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # signals.db with the columns needed by the contract.
    db_path = tmp_path / "signals.db"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                source_api TEXT,
                signal_type TEXT,
                detected_at TEXT,
                confidence REAL
            );
            CREATE TABLE quality_feedback (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                label TEXT,
                created_at TEXT
            );
            CREATE TABLE signal_quality_metrics (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                canonical_key TEXT,
                human_label TEXT,
                label_source TEXT,
                labeled_at TEXT,
                status_event_id INTEGER
            );
            """
        )
        con.commit()
    finally:
        con.close()

    contract_path = _write_contract(
        tmp_path,
        required_tables={
            "signals": {"required_columns": ["id", "canonical_key"]},
            "quality_feedback": {"required_columns": ["signal_id", "label"]},
            "signal_quality_metrics": {
                "required_columns": ["signal_id", "human_label"]
            },
        },
    )

    # Empty schema-v2 collector state.
    state_path = state_dir / "collectors.json"
    state_path.write_text(
        json.dumps(
            {"schema_version": 2, "updated_at": None, "collectors": {}},
            indent=2,
        ),
        encoding="utf-8",
    )

    # Day 2 split summary + siblings, all consistent.
    summary_path = _write_summary(
        state_dir,
        sizes={"train": 4, "calibration": 2, "holdout": 2},
        total_rows=8,
    )
    _write_split(state_dir, split="train", signal_ids=[1, 2, 3, 4])
    _write_split(state_dir, split="calibration", signal_ids=[5, 6])
    _write_split(state_dir, split="holdout", signal_ids=[7, 8])

    # Holdout file (siblings already created above; mirror it as the canonical
    # holdout-ids artifact name expected by the CLI flag).
    holdout_path = state_dir / "holdout_ids.json"

    # Gp_workload jsonl with one in-window event.
    as_of = datetime(2026, 4, 28, 8, 0, 0, tzinfo=timezone.utc)
    gp_path = state_dir / "gp_workload.jsonl"
    _write_jsonl(
        gp_path,
        [_gp_event(ts=as_of - timedelta(days=2), items_count=20)],
    )

    # Initialized markered target.
    target_path = tmp_path / "STRATEGY.md"
    target_path.write_text(
        _well_formed_target(),
        encoding="utf-8",
    )

    # Env vars: state path and a guaranteed-missing config path.
    monkeypatch.setenv("COLLECTOR_STATE_PATH", str(state_path))
    monkeypatch.setenv("COLLECTOR_CONFIG_PATH", str(tmp_path / "no_config.yaml"))

    return {
        "db": db_path,
        "contract": contract_path,
        "summary": summary_path,
        "holdout": holdout_path,
        "gp_workload": gp_path,
        "target": target_path,
        "state": state_path,
        "as_of": as_of,
    }


def _run_main(argv: list[str]) -> int:
    from scripts.generate_strategy_dashboard import main

    return main(argv)


def _base_args(env: dict[str, Path]) -> list[str]:
    return [
        "--target", str(env["target"]),
        "--db", str(env["db"]),
        "--schema-contract", str(env["contract"]),
        "--split-summary", str(env["summary"]),
        "--holdout-ids", str(env["holdout"]),
        "--gp-workload-jsonl", str(env["gp_workload"]),
        "--as-of", env["as_of"].isoformat(),
    ]


def test_main_exits_0_on_happy_path(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    exit_code = _run_main(_base_args(env))
    assert exit_code == 0
    rendered = env["target"].read_text(encoding="utf-8")
    assert "Strategy Dashboard" in rendered
    assert "schema_probe_passes" in rendered  # readiness block injected


def test_main_signals_db_mtime_unchanged(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    before_mtime = env["db"].stat().st_mtime_ns
    before_size = env["db"].stat().st_size
    _run_main(_base_args(env))
    assert env["db"].stat().st_mtime_ns == before_mtime
    assert env["db"].stat().st_size == before_size


def test_main_state_files_mtime_unchanged(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    state_files = [
        env["state"],
        env["holdout"],
        env["gp_workload"],
        env["summary"],
    ]
    before = {p: p.stat().st_mtime_ns for p in state_files}
    _run_main(_base_args(env))
    for p, mtime in before.items():
        assert p.stat().st_mtime_ns == mtime, f"{p} mtime changed"


def test_main_schema_probe_reports_are_not_written(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    out_dir = tmp_path / ".omx" / "wave6"
    _run_main(_base_args(env))
    # Probe normally writes live_schema_report.{json,md}; the dashboard
    # generator must call the pure functions and never touch the report files.
    assert not (out_dir / "live_schema_report.json").exists()
    assert not (out_dir / "live_schema_report.md").exists()


def test_main_double_generation_produces_zero_diff(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    _run_main(_base_args(env))
    once = env["target"].read_text(encoding="utf-8")
    _run_main(_base_args(env))
    twice = env["target"].read_text(encoding="utf-8")
    assert once == twice


def test_main_blocks_deterministic_for_fixed_inputs_and_as_of(tmp_path, monkeypatch):
    root_a = tmp_path / "a"
    root_a.mkdir()
    env_a = _setup_complete_env(root_a, monkeypatch)
    _run_main(_base_args(env_a))
    out_a = env_a["target"].read_text(encoding="utf-8")

    monkeypatch.undo()
    root_b = tmp_path / "b"
    root_b.mkdir()
    env_b = _setup_complete_env(root_b, monkeypatch)
    _run_main(_base_args(env_b))
    out_b = env_b["target"].read_text(encoding="utf-8")

    # Strip absolute paths that legitimately differ between runs.
    def _normalize(text: str) -> str:
        return text.replace(str(tmp_path / "a"), "ROOT").replace(
            str(tmp_path / "b"), "ROOT"
        )

    assert _normalize(out_a) == _normalize(out_b)


def test_main_content_outside_markers_unchanged(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    custom = (
        "# Custom header\n\n"
        "Operator notes that must persist.\n\n"
        + _well_formed_target().split("# Strategy Dashboard\n", 1)[1]
        + "\n## Custom footer\n\nMore prose.\n"
    )
    env["target"].write_text(custom, encoding="utf-8")
    _run_main(_base_args(env))
    rendered = env["target"].read_text(encoding="utf-8")
    assert "# Custom header" in rendered
    assert "Operator notes that must persist." in rendered
    assert "## Custom footer" in rendered
    assert "More prose." in rendered


def test_main_missing_markers_exit_4_without_init(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    env["target"].write_text("# No markers here\n", encoding="utf-8")
    exit_code = _run_main(_base_args(env))
    assert exit_code == 4


def test_main_init_creates_missing_markers_idempotently(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    env["target"].write_text("# Bare\n", encoding="utf-8")
    exit_code = _run_main(_base_args(env) + ["--init"])
    assert exit_code == 0
    once = env["target"].read_text(encoding="utf-8")
    # Re-run should produce zero diff.
    exit_code = _run_main(_base_args(env) + ["--init"])
    assert exit_code == 0
    twice = env["target"].read_text(encoding="utf-8")
    assert once == twice


def test_main_no_block_duplication_on_rerun_with_init(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    _run_main(_base_args(env) + ["--init"])
    rendered = env["target"].read_text(encoding="utf-8")
    for block in CANONICAL_BLOCKS:
        s, e = _markers_for(block)
        assert rendered.count(s) == 1, f"{block} start marker duplicated"
        assert rendered.count(e) == 1, f"{block} end marker duplicated"


def test_main_duplicate_marker_exits_2(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    s, e = _markers_for("collector_matrix")
    duped = _well_formed_target() + f"\n{s}\nstray\n{e}\n"
    env["target"].write_text(duped, encoding="utf-8")
    exit_code = _run_main(_base_args(env))
    assert exit_code == 2


def test_main_partial_marker_pair_exits_2_even_with_init(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    s, _e = _markers_for("collector_matrix")
    partial = "# Doc\n\n" + s + "\nlonely\n"
    env["target"].write_text(partial, encoding="utf-8")
    exit_code = _run_main(_base_args(env) + ["--init"])
    assert exit_code == 2


def test_main_unknown_dashboard_marker_exits_2(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    bad = (
        _well_formed_target()
        + "\n<!-- harmonic:dashboard:not_a_block:start -->\nx\n"
        + "<!-- harmonic:dashboard:not_a_block:end -->\n"
    )
    env["target"].write_text(bad, encoding="utf-8")
    exit_code = _run_main(_base_args(env))
    assert exit_code == 2


def test_main_failed_write_leaves_original_target_unchanged(tmp_path, monkeypatch):
    """If the atomic write fails (e.g., temp open errors), the target is intact."""
    env = _setup_complete_env(tmp_path, monkeypatch)
    original = env["target"].read_text(encoding="utf-8")

    # Patch os.replace to simulate a final-rename failure.
    import os as _os

    real_replace = _os.replace

    def _fail_replace(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr("os.replace", _fail_replace)
    try:
        exit_code = _run_main(_base_args(env))
    finally:
        monkeypatch.setattr("os.replace", real_replace)
    assert exit_code != 0
    assert env["target"].read_text(encoding="utf-8") == original


def test_main_output_mode_does_not_modify_target(tmp_path, monkeypatch):
    env = _setup_complete_env(tmp_path, monkeypatch)
    target_before = env["target"].read_text(encoding="utf-8")
    out_path = tmp_path / "elsewhere.md"
    exit_code = _run_main(_base_args(env) + ["--output", str(out_path)])
    assert exit_code == 0
    assert env["target"].read_text(encoding="utf-8") == target_before
    assert out_path.exists()
    assert "schema_probe_passes" in out_path.read_text(encoding="utf-8")


def test_main_warns_on_missing_optional_artifacts_but_renders(tmp_path, monkeypatch):
    """Missing optional artifacts must not fail rendering; readiness still WARNs."""
    env = _setup_complete_env(tmp_path, monkeypatch)
    # Delete optional artifacts after env setup.
    env["holdout"].unlink()
    env["gp_workload"].unlink()
    env["summary"].unlink()
    exit_code = _run_main(_base_args(env))
    # Plan: optional artifact missing → exit 0 with WARN evidence.
    assert exit_code == 0
    rendered = env["target"].read_text(encoding="utf-8")
    assert "WARN" in rendered
