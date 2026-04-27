from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.create_evaluation_splits import (
    DEFAULT_FRACTIONS,
    LabelSource,
    LabeledRow,
    deterministic_split,
    load_labeled_rows,
    main,
    write_split_artifacts,
)


def _create_schema(db_path: Path) -> None:
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
                label TEXT
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
            CREATE TABLE signal_processing (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                status TEXT
            );
            CREATE TABLE thesis_ml_predictions (
                id INTEGER PRIMARY KEY,
                signal_id INTEGER,
                ml_enablement TEXT
            );
            CREATE TABLE notion_status_events (
                id INTEGER PRIMARY KEY,
                canonical_key TEXT,
                old_status TEXT,
                new_status TEXT,
                observed_at TEXT
            );
            """
        )
        con.commit()
    finally:
        con.close()


def _insert_labeled_signal(
    db_path: Path,
    *,
    signal_id: int,
    label: str,
    source_api: str,
    detected_at: str,
) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO signals(id, canonical_key, source_api, signal_type, detected_at, confidence) "
            "VALUES(?,?,?,?,?,?)",
            (signal_id, f"key:{signal_id}", source_api, "x", detected_at, 0.5),
        )
        con.execute(
            "INSERT INTO signal_quality_metrics(signal_id, canonical_key, human_label, label_source, labeled_at) "
            "VALUES(?,?,?,?,?)",
            (signal_id, f"key:{signal_id}", label, "test", detected_at),
        )
        con.commit()
    finally:
        con.close()


def _populated_db(tmp_path, *, total: int = 100) -> Path:
    db_path = tmp_path / "signals.db"
    _create_schema(db_path)
    # Distribute: 70 FP, 20 TP, 10 UNSURE across 3 source_apis and 2 months.
    sources = ["hacker_news", "rss_feeds", "news_api"]
    months = ["2026-02", "2026-03"]
    rows: list[tuple[int, str, str, str]] = []
    sid = 1
    for label, count in (("FP", 70), ("TP", 20), ("UNSURE", 10)):
        for i in range(count):
            src = sources[i % len(sources)]
            ym = months[i % len(months)]
            rows.append((sid, label, src, f"{ym}-15T12:00:00+00:00"))
            sid += 1
    for r in rows[:total]:
        _insert_labeled_signal(
            db_path,
            signal_id=r[0],
            label=r[1],
            source_api=r[2],
            detected_at=r[3],
        )
    return db_path


def test_load_labeled_rows_pulls_human_label_from_signal_quality_metrics(tmp_path):
    db_path = _populated_db(tmp_path)
    rows = load_labeled_rows(db_path, label_source=LabelSource.SIGNAL_QUALITY_METRICS)
    assert len(rows) == 100
    assert all(isinstance(r, LabeledRow) for r in rows)
    labels = {r.label for r in rows}
    assert labels == {"FP", "TP", "UNSURE"}


def test_load_labeled_rows_supports_quality_feedback_label_source(tmp_path):
    db_path = tmp_path / "signals.db"
    _create_schema(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO signals(id, canonical_key, source_api, signal_type, detected_at, confidence) "
            "VALUES(1, 'k', 'hn', 'x', '2026-02-15T12:00:00+00:00', 0.5)"
        )
        con.execute("INSERT INTO quality_feedback(signal_id, label) VALUES(1, 'TP')")
        con.commit()
    finally:
        con.close()

    rows = load_labeled_rows(db_path, label_source=LabelSource.QUALITY_FEEDBACK)
    assert len(rows) == 1
    assert rows[0].label == "TP"


def test_deterministic_split_is_reproducible():
    rows = [
        LabeledRow(signal_id=i, label="FP", source_api="hn", year_month="2026-02")
        for i in range(50)
    ]
    a = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=42)
    b = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=42)
    assert a["train"] == b["train"]
    assert a["calibration"] == b["calibration"]
    assert a["holdout"] == b["holdout"]


def test_different_seeds_produce_different_splits():
    rows = [
        LabeledRow(signal_id=i, label="FP", source_api="hn", year_month="2026-02")
        for i in range(50)
    ]
    a = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=1)
    b = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=2)
    # At least one split should differ in membership.
    assert (a["train"], a["calibration"], a["holdout"]) != (
        b["train"],
        b["calibration"],
        b["holdout"],
    )


def test_split_is_exhaustive_and_disjoint():
    rows = [
        LabeledRow(signal_id=i, label=("FP" if i % 2 == 0 else "TP"), source_api="hn", year_month="2026-02")
        for i in range(50)
    ]
    splits = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=42)
    all_ids = (
        list(splits["train"])
        + list(splits["calibration"])
        + list(splits["holdout"])
    )
    assert sorted(all_ids) == list(range(50))
    assert len(set(all_ids)) == 50


def test_stratification_preserves_class_proportions():
    # 80 FP, 20 TP — stratified split should keep similar ratios per split.
    rows = [
        LabeledRow(signal_id=i, label="FP", source_api="hn", year_month="2026-02")
        for i in range(80)
    ] + [
        LabeledRow(signal_id=80 + i, label="TP", source_api="hn", year_month="2026-02")
        for i in range(20)
    ]
    splits = deterministic_split(
        rows,
        fractions={"train": 0.6, "calibration": 0.2, "holdout": 0.2},
        seed=42,
    )
    rows_by_id = {r.signal_id: r for r in rows}

    def label_counts(ids):
        labels = [rows_by_id[i].label for i in ids]
        return {"FP": labels.count("FP"), "TP": labels.count("TP")}

    train_counts = label_counts(splits["train"])
    cal_counts = label_counts(splits["calibration"])
    hold_counts = label_counts(splits["holdout"])

    # FP proportion = 0.8, so each split should have FP/(FP+TP) within tolerance.
    for counts in (train_counts, cal_counts, hold_counts):
        total = counts["FP"] + counts["TP"]
        if total == 0:
            continue
        assert 0.65 <= counts["FP"] / total <= 0.95, counts


def test_split_handles_singleton_strata_without_dropping_rows():
    # 1 row per stratum — must still appear in some split.
    rows = [
        LabeledRow(signal_id=1, label="UNSURE", source_api="lever_jobs", year_month="2025-12"),
        LabeledRow(signal_id=2, label="UNSURE", source_api="ashby_jobs", year_month="2025-12"),
    ]
    splits = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=42)
    placed = (
        list(splits["train"]) + list(splits["calibration"]) + list(splits["holdout"])
    )
    assert sorted(placed) == [1, 2]


def test_deterministic_split_rejects_invalid_fractions():
    rows = [LabeledRow(signal_id=1, label="FP", source_api="hn", year_month="2026-02")]
    with pytest.raises(ValueError):
        deterministic_split(rows, fractions={"train": 0.5, "calibration": 0.3, "holdout": 0.3}, seed=1)


def test_write_split_artifacts_produces_per_split_and_summary(tmp_path):
    rows = [
        LabeledRow(signal_id=i, label="FP", source_api="hn", year_month="2026-02")
        for i in range(50)
    ]
    splits = deterministic_split(rows, fractions=DEFAULT_FRACTIONS, seed=42)
    out_dir = tmp_path / "state"
    write_split_artifacts(
        out_dir=out_dir,
        rows=rows,
        splits=splits,
        seed=42,
        fractions=DEFAULT_FRACTIONS,
        label_source=LabelSource.SIGNAL_QUALITY_METRICS,
    )

    assert (out_dir / "train_ids.json").exists()
    assert (out_dir / "calibration_ids.json").exists()
    assert (out_dir / "holdout_ids.json").exists()
    assert (out_dir / "evaluation_splits_summary.json").exists()

    train = json.loads((out_dir / "train_ids.json").read_text(encoding="utf-8"))
    assert train["split"] == "train"
    assert train["seed"] == 42
    assert train["label_source"] == "signal_quality_metrics.human_label"
    assert isinstance(train["signal_ids"], list)
    assert "stratification" in train

    summary = json.loads((out_dir / "evaluation_splits_summary.json").read_text(encoding="utf-8"))
    assert summary["total_rows"] == 50
    assert sum(summary["sizes"].values()) == 50


def test_main_aborts_when_schema_probe_fails(tmp_path, capsys):
    # Empty DB — schema probe will fail.
    db_path = tmp_path / "signals.db"
    sqlite3.connect(db_path).close()
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "version": 1,
                "required_tables": {
                    "signals": {"required_columns": ["id"]},
                    "signal_quality_metrics": {"required_columns": ["signal_id", "human_label"]},
                },
                "forbidden_references": [],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--db", str(db_path),
            "--out-dir", str(tmp_path / "state"),
            "--contract", str(contract_path),
            "--seed", "42",
        ]
    )
    assert exit_code != 0
    # No state files written when probe fails.
    assert not (tmp_path / "state").exists() or not list((tmp_path / "state").glob("*"))


def test_main_writes_splits_when_schema_probe_passes(tmp_path):
    db_path = _populated_db(tmp_path, total=60)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "version": 1,
                "required_tables": {
                    "signals": {"required_columns": ["id", "source_api", "detected_at"]},
                    "signal_quality_metrics": {
                        "required_columns": ["signal_id", "human_label"]
                    },
                },
                "forbidden_references": [],
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "state"
    exit_code = main(
        [
            "--db", str(db_path),
            "--out-dir", str(out_dir),
            "--contract", str(contract_path),
            "--seed", "42",
        ]
    )
    assert exit_code == 0
    summary = json.loads((out_dir / "evaluation_splits_summary.json").read_text(encoding="utf-8"))
    assert summary["total_rows"] == 60


def test_main_does_not_modify_database(tmp_path):
    db_path = _populated_db(tmp_path, total=30)
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(
            {
                "version": 1,
                "required_tables": {
                    "signals": {"required_columns": ["id"]},
                    "signal_quality_metrics": {
                        "required_columns": ["signal_id", "human_label"]
                    },
                },
                "forbidden_references": [],
            }
        ),
        encoding="utf-8",
    )
    before_size = db_path.stat().st_size
    before_mtime = db_path.stat().st_mtime_ns
    main(
        [
            "--db", str(db_path),
            "--out-dir", str(tmp_path / "state"),
            "--contract", str(contract_path),
            "--seed", "42",
        ]
    )
    assert db_path.stat().st_size == before_size
    assert db_path.stat().st_mtime_ns == before_mtime
