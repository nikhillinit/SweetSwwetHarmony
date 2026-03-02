"""Tests for scripts/analyze_pipeline_thesis.py — thesis filter calibration."""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.analyze_pipeline_thesis import (
    _extract_domain,
    _classify_routing,
    _snippet,
    analyze,
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
# Routing classification
# ---------------------------------------------------------------------------


class TestClassifyRouting:
    def test_qualified(self):
        assert _classify_routing(0.5, has_negative_kw=False) == "QUALIFIED"

    def test_qualified_at_threshold(self):
        assert _classify_routing(0.3, has_negative_kw=False) == "QUALIFIED"

    def test_held_below_threshold(self):
        assert _classify_routing(0.29, has_negative_kw=False) == "HELD"

    def test_rejected_with_neg_kw(self):
        assert _classify_routing(0.1, has_negative_kw=True) == "REJECTED"

    def test_qualified_with_neg_kw_high_score(self):
        # Even with negative keywords, high enough score passes
        assert _classify_routing(0.4, has_negative_kw=True) == "QUALIFIED"


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
    # EnterpriseSaaS should be rejected (enterprise, b2b, saas, devops, api management)
    # CryptoTrader should be rejected (blockchain, crypto, defi)
    assert s["rejected"] >= 2, f"Expected at least 2 rejected, got {s['rejected']}"

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
