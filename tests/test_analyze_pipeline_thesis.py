"""Tests for scripts/analyze_pipeline_thesis.py — thesis filter calibration."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.analyze_pipeline_thesis import (
    _extract_domain,
    _snippet,
    analyze,
    _holdout_split,
)


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------


class TestExtractDomain:
    def test_full_url(self):
        assert _extract_domain("https://www.noon.world/") == "noon.world"

    def test_bare_domain(self):
        assert _extract_domain("farmysnacks.com") == "farmysnacks.com"

    def test_subdomain(self):
        assert _extract_domain("https://app.getfit.io") == "getfit.io"

    def test_junk_na(self):
        assert _extract_domain("NA") is None

    def test_junk_n_a(self):
        assert _extract_domain("N A") is None

    def test_junk_no_active(self):
        assert _extract_domain("No active website") is None

    def test_empty(self):
        assert _extract_domain("") is None

    def test_none(self):
        assert _extract_domain(None) is None


# ---------------------------------------------------------------------------
# Snippet
# ---------------------------------------------------------------------------


class TestSnippet:
    def test_short(self):
        assert _snippet("hello") == "hello"

    def test_long(self):
        result = _snippet("x" * 100, length=10)
        assert result == "x" * 10 + "..."

    def test_none(self):
        assert _snippet(None) == ""

    def test_newlines(self):
        assert _snippet("hello\nworld") == "hello world"


# ---------------------------------------------------------------------------
# End-to-end analysis with a fixture CSV
# ---------------------------------------------------------------------------


def _make_csv(rows: list[dict[str, str]], path: Path) -> None:
    fieldnames = ["Company Name", "Short Description", "Website", "Status"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "pipeline.csv"
    _make_csv(
        [
            {
                "Company Name": "FarmySnacks",
                "Short Description": "Organic snack brand delivering healthy vegan snacks direct to consumer",
                "Website": "https://farmysnacks.com",
                "Status": "Source",
            },
            {
                "Company Name": "EnterpriseSaaS Co",
                "Short Description": "Enterprise SaaS platform for B2B API management and devops",
                "Website": "https://enterprisesaas.io",
                "Status": "Passed",
            },
            {
                "Company Name": "MeditateNow",
                "Short Description": "Meditation app for guided relaxation and sleep tracking",
                "Website": "https://meditatenow.app",
                "Status": "Dilligence",
            },
            {
                "Company Name": "CryptoTrader",
                "Short Description": "Blockchain crypto defi trading platform",
                "Website": "https://cryptotrader.xyz",
                "Status": "Tracking",
            },
            {
                "Company Name": "StealthCo",
                "Short Description": "",
                "Website": "NA",
                "Status": "",
            },
        ],
        csv_path,
    )
    return csv_path


def test_analyze_basic(sample_csv: Path):
    report = analyze(sample_csv)
    s = report["summary"]

    assert s["total"] == 5
    assert s["has_description"] == 4
    assert s["no_description"] == 1

    # FarmySnacks + MeditateNow should be qualified (consumer CPG / health)
    # CryptoTrader should be rejected (blockchain, crypto, defi = hard_reject)
    # EnterpriseSaaS: HELD not REJECTED (enterprise = hard_hold per ADR-1)
    assert s["rejected"] >= 1, f"Expected at least 1 rejected, got {s['rejected']}"
    assert s["held"] >= 1, f"Expected at least 1 held, got {s['held']}"

    # Check negative keyword hits exist
    neg_hits = report["negative_keyword_hits"]
    # "enterprise", "b2b", "blockchain", "crypto" etc should all appear
    all_neg_kws = set(neg_hits.keys())
    assert "enterprise" in all_neg_kws or "b2b" in all_neg_kws
    assert "blockchain" in all_neg_kws or "crypto" in all_neg_kws

    # Status breakdown should have entries
    assert len(report["status_breakdown"]) > 0

    # Category distribution should exist
    assert len(report["category_distribution"]) > 0


def test_analyze_rejected_active(sample_csv: Path):
    """CryptoTrader is active (Tracking) and rejected → should appear."""
    report = analyze(sample_csv)
    rejected_names = [r["company_name"] for r in report["rejected_active_pipeline"]]
    assert "CryptoTrader" in rejected_names


def test_analyze_no_description(sample_csv: Path):
    """StealthCo has no description — should still be scored (by name)."""
    report = analyze(sample_csv)
    # StealthCo should exist in the status breakdown under "(empty)"
    assert "(empty)" in report["status_breakdown"]


# ---------------------------------------------------------------------------
# Phase 3: Production routing, hold-out split, experiment mode
# ---------------------------------------------------------------------------


class TestCalibrationUsesProductionRouting:
    """Verify calibration script uses ThesisFilter routing, not _classify_routing."""

    def test_no_classify_routing_function(self):
        """_classify_routing should not exist in the module."""
        import scripts.analyze_pipeline_thesis as mod
        assert not hasattr(mod, "_classify_routing"), (
            "_classify_routing still exists — should be replaced by ThesisFilter routing"
        )

    def test_results_have_decision_path_code(self, sample_csv: Path):
        """Every per-company result includes a decision_path_code from DecisionPathCode."""
        report = analyze(sample_csv)
        from utils.thesis_filter import DecisionPathCode
        valid_codes = {c.value for c in DecisionPathCode}
        for item in report.get("results", []):
            assert "decision_path_code" in item, f"Missing decision_path_code for {item.get('company_name')}"
            assert item["decision_path_code"] in valid_codes, (
                f"Invalid path code {item['decision_path_code']}"
            )

    def test_summary_has_cascade_metrics(self, sample_csv: Path):
        """Summary includes Phase 3 cascade metrics."""
        report = analyze(sample_csv)
        s = report["summary"]
        for key in (
            "hard_veto_count",
            "hard_hold_count",
            "consumer_rescue_count",
            "llm_call_eligible_rate",
        ):
            assert key in s, f"Missing summary metric: {key}"

    def test_decision_path_code_distribution(self, sample_csv: Path):
        """Report includes decision path code distribution."""
        report = analyze(sample_csv)
        assert "decision_path_code_distribution" in report
        dist = report["decision_path_code_distribution"]
        assert isinstance(dist, dict)
        # At least one path code should have non-zero count
        assert sum(dist.values()) == report["summary"]["total"]


class TestHoldoutSplit:
    """Hold-out split: deterministic hash, 70/30 train/eval."""

    def test_holdout_deterministic(self, sample_csv: Path):
        """Same CSV + same seed → identical split assignment."""
        r1 = analyze(sample_csv, split_seed=42)
        r2 = analyze(sample_csv, split_seed=42)
        splits_1 = [item["split"] for item in r1["results"]]
        splits_2 = [item["split"] for item in r2["results"]]
        assert splits_1 == splits_2

    def test_holdout_no_leakage(self, sample_csv: Path):
        """Train ∩ eval = ∅."""
        report = analyze(sample_csv, split_seed=42)
        train_names = {
            item["company_name"]
            for item in report["results"]
            if item["split"] == "train"
        }
        eval_names = {
            item["company_name"]
            for item in report["results"]
            if item["split"] == "eval"
        }
        assert train_names & eval_names == set(), (
            f"Leakage: {train_names & eval_names}"
        )
        # All companies assigned
        assert len(train_names) + len(eval_names) == report["summary"]["total"]

    def test_holdout_ratio_approximate(self):
        """With many companies, ~70% train and ~30% eval."""
        # Generate 100 fake names
        names = [f"Company_{i}" for i in range(100)]
        train_count = sum(
            1 for n in names if _holdout_split(n, seed=42) == "train"
        )
        # 70/30 ± 15 tolerance (small sample)
        assert 55 <= train_count <= 85, f"Train count {train_count} outside 55-85 range"

    def test_holdout_split_function(self):
        """_holdout_split returns 'train' or 'eval' only."""
        for name in ["Acme", "Foo", "Bar"]:
            result = _holdout_split(name, seed=42)
            assert result in ("train", "eval"), f"Invalid split: {result}"

    def test_different_seed_different_split(self):
        """Different seeds produce different assignments for at least some names."""
        names = [f"Company_{i}" for i in range(50)]
        splits_42 = [_holdout_split(n, seed=42) for n in names]
        splits_99 = [_holdout_split(n, seed=99) for n in names]
        # Not all identical
        assert splits_42 != splits_99, "Different seeds should produce different splits"

    def test_split_filter_train(self, sample_csv: Path):
        """When split='train', summary only counts train companies."""
        report_all = analyze(sample_csv, split_seed=42)
        report_train = analyze(sample_csv, split_seed=42, split="train")
        train_count = sum(
            1 for item in report_all["results"] if item["split"] == "train"
        )
        assert report_train["summary"]["total"] == train_count

    def test_split_filter_eval(self, sample_csv: Path):
        """When split='eval', summary only counts eval companies."""
        report_all = analyze(sample_csv, split_seed=42)
        report_eval = analyze(sample_csv, split_seed=42, split="eval")
        eval_count = sum(
            1 for item in report_all["results"] if item["split"] == "eval"
        )
        assert report_eval["summary"]["total"] == eval_count


class TestExperimentMode:
    """THESIS_EXPERIMENT_MODE env var isolation."""

    def test_experiment_mode_off_by_default(self, sample_csv: Path):
        """Default experiment mode is off — report metadata shows it."""
        report = analyze(sample_csv)
        assert report.get("metadata", {}).get("experiment_mode") == "off"

    def test_experiment_mode_active(self, sample_csv: Path, monkeypatch):
        """When active, report metadata reflects it."""
        monkeypatch.setenv("THESIS_EXPERIMENT_MODE", "active")
        report = analyze(sample_csv)
        assert report["metadata"]["experiment_mode"] == "active"

    def test_experiment_threshold_isolated(self, sample_csv: Path, monkeypatch):
        """Experiment threshold only used when THESIS_EXPERIMENT_MODE=active."""
        monkeypatch.setenv("THESIS_EXPERIMENT_MODE", "active")
        monkeypatch.setenv("THESIS_SKIP_LLM_EXPERIMENT_THRESHOLD", "0.05")
        report = analyze(sample_csv)
        assert report["metadata"]["skip_llm_threshold_used"] == 0.05

    def test_experiment_threshold_ignored_when_off(self, sample_csv: Path, monkeypatch):
        """Experiment threshold ignored when mode is off."""
        monkeypatch.setenv("THESIS_EXPERIMENT_MODE", "off")
        monkeypatch.setenv("THESIS_SKIP_LLM_EXPERIMENT_THRESHOLD", "0.05")
        report = analyze(sample_csv)
        # Should use default, not experiment value
        assert report["metadata"]["skip_llm_threshold_used"] == 0.2
