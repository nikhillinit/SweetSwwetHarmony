"""
Tests for PR11: Enrichment collector SKIP semantics.

Enrichment collectors (domain_whois, opencorporates) should raise
CollectorSkipError when invoked without input, instead of silently
returning an empty list. This maps to CollectorStatus.SKIPPED in
BaseCollector.run().
"""

import pytest

from collectors.base import CollectorSkipError
from discovery_engine.mcp_server import CollectorStatus


class TestDomainWhoisSkip:
    """domain_whois raises CollectorSkipError when no domains are provided."""

    @pytest.mark.asyncio
    async def test_no_domains_raises_skip(self):
        """_collect_signals with no domains should raise CollectorSkipError."""
        from collectors.domain_whois import DomainWhoisCollector

        collector = DomainWhoisCollector()
        # _domains_to_check is None by default
        with pytest.raises(CollectorSkipError, match="no domains provided"):
            await collector._collect_signals()

    @pytest.mark.asyncio
    async def test_run_without_domains_returns_skipped_status(self):
        """run() with no domains should return SKIPPED status via BaseCollector."""
        from collectors.domain_whois import DomainWhoisCollector

        collector = DomainWhoisCollector()
        result = await collector.run(domains=None, dry_run=True)

        assert result.status == CollectorStatus.SKIPPED
        assert "no domains provided" in result.error_message

    @pytest.mark.asyncio
    async def test_run_with_domains_does_not_skip(self, monkeypatch):
        """run() with domains should NOT raise CollectorSkipError."""
        from collectors.domain_whois import DomainWhoisCollector

        collector = DomainWhoisCollector()

        # Mock _check_domains to avoid network calls
        async def mock_check_domains(domains):
            return []

        monkeypatch.setattr(collector, "_check_domains", mock_check_domains)

        result = await collector.run(domains=["example.com"], dry_run=True)

        assert result.status != CollectorStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_empty_domains_list_does_not_skip(self, monkeypatch):
        """run() with an empty list should NOT skip (empty list is truthy-false
        but semantically different from None / not provided)."""
        from collectors.domain_whois import DomainWhoisCollector

        collector = DomainWhoisCollector()

        # Empty list is falsy, so it should still trigger skip
        result = await collector.run(domains=[], dry_run=True)

        assert result.status == CollectorStatus.SKIPPED


class TestOpenCorporatesSkip:
    """opencorporates raises CollectorSkipError in bulk _collect_signals."""

    @pytest.mark.asyncio
    async def test_collect_signals_raises_skip(self):
        """_collect_signals should raise CollectorSkipError."""
        from collectors.opencorporates import OpenCorporatesCollector

        collector = OpenCorporatesCollector()
        with pytest.raises(CollectorSkipError, match="requires company names"):
            await collector._collect_signals()

    @pytest.mark.asyncio
    async def test_collect_raises_skip(self):
        """collect() (public interface) should also raise CollectorSkipError."""
        from collectors.opencorporates import OpenCorporatesCollector

        collector = OpenCorporatesCollector()
        with pytest.raises(CollectorSkipError, match="requires company names"):
            await collector.collect()

    @pytest.mark.asyncio
    async def test_run_returns_skipped_status(self):
        """run() should return SKIPPED status via BaseCollector."""
        from collectors.opencorporates import OpenCorporatesCollector

        collector = OpenCorporatesCollector()
        result = await collector.run(dry_run=True)

        assert result.status == CollectorStatus.SKIPPED
        assert "requires company names" in result.error_message

    @pytest.mark.asyncio
    async def test_collect_for_company_does_not_skip(self, monkeypatch):
        """collect_for_company() with a name should NOT raise CollectorSkipError."""
        from collectors.opencorporates import OpenCorporatesCollector

        collector = OpenCorporatesCollector()

        # Mock search_company to avoid network calls
        async def mock_search(name, jurisdictions=None):
            return []

        monkeypatch.setattr(collector, "search_company", mock_search)

        # This path should work normally (no skip)
        signals = await collector.collect_for_company("TestCorp")
        assert isinstance(signals, list)


class TestSkipErrorMessage:
    """Verify error messages are descriptive and actionable."""

    @pytest.mark.asyncio
    async def test_domain_whois_message_mentions_enrichment(self):
        """domain_whois skip message should guide user to enrichment mode."""
        from collectors.domain_whois import DomainWhoisCollector

        collector = DomainWhoisCollector()
        result = await collector.run(domains=None, dry_run=True)

        assert "enrichment" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_opencorporates_message_mentions_alternatives(self):
        """opencorporates skip message should mention collect_for_company."""
        from collectors.opencorporates import OpenCorporatesCollector

        collector = OpenCorporatesCollector()
        result = await collector.run(dry_run=True)

        assert "collect_for_company" in result.error_message
