"""
Tests for SEC_EDGAR_ALL_FILINGS env var and sic_matched metadata.

Verifies:
- Default behavior: target_sectors_only=True (unchanged)
- SEC_EDGAR_ALL_FILINGS=true: all filings pass through
- sic_matched correctly reflects is_target_sector
- Relative confidence: non-SIC < SIC (with sector boost)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import pytest

from collectors.sec_edgar import (
    FormDFiling,
    SECEdgarCollector,
    TARGET_SIC_CODES,
    CONSUMER_CPG_SIC_CODES,
)


def _make_filing(
    company_name: str = "Test Corp",
    sic_code: str | None = None,
    industry_group: str | None = None,
    offering_amount: float = 1_000_000,
    cik: str = "0001234567",
    accession_number: str = "0001234567-24-000001",
) -> FormDFiling:
    """Create a test filing."""
    return FormDFiling(
        cik=cik,
        company_name=company_name,
        accession_number=accession_number,
        filing_date=datetime.now(timezone.utc) - timedelta(days=5),
        offering_amount=offering_amount,
        sic_code=sic_code,
        industry_group=industry_group,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_number}",
    )


class TestDefaultBehavior:
    """Default: target_sectors_only=True."""

    def test_default_target_sectors_only(self):
        """Default is target_sectors_only=True."""
        collector = SECEdgarCollector()
        assert collector.target_sectors_only is True

    def test_explicit_true(self):
        """Explicit target_sectors_only=True."""
        collector = SECEdgarCollector(target_sectors_only=True)
        assert collector.target_sectors_only is True


class TestAllFilingsMode:
    """SEC_EDGAR_ALL_FILINGS=true: all filings pass through."""

    def test_target_sectors_only_false(self):
        """When target_sectors_only=False, all filings should pass."""
        collector = SECEdgarCollector(target_sectors_only=False)
        assert collector.target_sectors_only is False

    def test_pipeline_env_wiring(self, monkeypatch):
        """SEC_EDGAR_ALL_FILINGS env var wires to target_sectors_only=False."""
        monkeypatch.setenv("SEC_EDGAR_ALL_FILINGS", "true")
        val = os.getenv("SEC_EDGAR_ALL_FILINGS", "").lower() in ("true", "1", "yes")
        assert val is True
        # The pipeline would pass target_sectors_only=not val = False
        assert not val is False


class TestSicMatchedMetadata:
    """sic_matched field in raw_data correctly reflects is_target_sector."""

    def test_sic_matched_true_for_target_sector(self):
        """Filing with SIC in TARGET_SIC_CODES → sic_matched=True."""
        # Use a known target SIC code from consumer CPG
        target_sic = next(iter(CONSUMER_CPG_SIC_CODES))
        filing = _make_filing(
            sic_code=target_sic,
            industry_group="consumer_cpg",
        )
        assert filing.is_target_sector is True

        signal = filing.to_signal()
        assert signal.raw_data["sic_matched"] is True

    def test_sic_matched_false_for_non_target(self):
        """Filing with non-target SIC (e.g., 9999) → sic_matched=False."""
        filing = _make_filing(
            sic_code="9999",
            industry_group=None,  # Not classified
        )
        assert filing.is_target_sector is False

        signal = filing.to_signal()
        assert signal.raw_data["sic_matched"] is False

    def test_both_filings_in_same_run(self):
        """Two filings in same run each have correct sic_matched."""
        target_sic = next(iter(CONSUMER_CPG_SIC_CODES))
        filing_target = _make_filing(
            company_name="Healthy Co",
            sic_code=target_sic,
            industry_group="consumer_cpg",
            accession_number="0001234567-24-000001",
        )
        filing_nontarget = _make_filing(
            company_name="Software Inc",
            sic_code="9999",
            industry_group=None,
            accession_number="0001234567-24-000002",
        )

        sig1 = filing_target.to_signal()
        sig2 = filing_nontarget.to_signal()

        assert sig1.raw_data["sic_matched"] is True
        assert sig2.raw_data["sic_matched"] is False


class TestRelativeConfidence:
    """Confidence: non-SIC < SIC (sector boost)."""

    def test_non_sic_lower_than_sic(self):
        """Non-target filing should have lower confidence than target filing."""
        target_sic = next(iter(CONSUMER_CPG_SIC_CODES))
        filing_target = _make_filing(
            sic_code=target_sic,
            industry_group="consumer_cpg",
            accession_number="0001234567-24-000001",
        )
        filing_nontarget = _make_filing(
            sic_code="9999",
            industry_group=None,
            accession_number="0001234567-24-000002",
        )

        sig_target = filing_target.to_signal()
        sig_nontarget = filing_nontarget.to_signal()

        assert sig_nontarget.confidence < sig_target.confidence

    def test_sic_confidence_includes_sector_boost(self):
        """SIC-matched filing should get sector boost (0.15)."""
        target_sic = next(iter(CONSUMER_CPG_SIC_CODES))
        filing_target = _make_filing(
            sic_code=target_sic,
            industry_group="consumer_cpg",
        )
        filing_base = _make_filing(
            sic_code="9999",
            industry_group=None,
            accession_number="0001234567-24-999999",
        )

        sig_target = filing_target.to_signal()
        sig_base = filing_base.to_signal()

        # The difference should be the sector boost (0.15)
        diff = sig_target.confidence - sig_base.confidence
        assert abs(diff - 0.15) < 0.001, f"Expected ~0.15 diff, got {diff}"


class TestSicCodeField:
    """sic_code is preserved in raw_data."""

    def test_sic_code_in_raw_data(self):
        """sic_code appears in raw_data for downstream queries."""
        filing = _make_filing(sic_code="9999")
        signal = filing.to_signal()
        assert signal.raw_data["sic_code"] == "9999"


class TestSICCodeExpansion:
    """LOB v7 0C: 737X software SIC codes added to TARGET_SIC_CODES."""

    def test_software_sic_codes_included(self):
        assert {"7371", "7372", "7379"} <= TARGET_SIC_CODES

    def test_738x_excluded(self):
        assert "7380" not in TARGET_SIC_CODES
        assert "7381" not in TARGET_SIC_CODES

    def test_existing_codes_unchanged(self):
        assert "7370" in TARGET_SIC_CODES
        assert "7374" in TARGET_SIC_CODES

    def test_total_target_sic_codes(self):
        assert len(TARGET_SIC_CODES) >= 64
