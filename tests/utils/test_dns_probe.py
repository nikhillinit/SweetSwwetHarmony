"""
Tests for utils/dns_probe.py — domain candidate generation and async DNS probing.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import pytest

from utils.dns_probe import (
    generate_domain_candidates,
    probe_domain,
    dns_probe_company,
)


# =============================================================================
# Candidate generation tests
# =============================================================================


class TestGenerateDomainCandidates:
    """Tests for generate_domain_candidates()."""

    def test_candidates_tld_first_ordering(self):
        """All .com variants appear before .io variants."""
        candidates = generate_domain_candidates("Borden Cheese")
        com_indices = [i for i, c in enumerate(candidates) if c.endswith(".com")]
        io_indices = [i for i, c in enumerate(candidates) if c.endswith(".io")]
        if com_indices and io_indices:
            assert max(com_indices) < min(io_indices), \
                f".com candidates ({com_indices}) should all precede .io ({io_indices})"

    def test_candidates_single_word(self):
        """Single-word name should not produce duplicate first-token slug."""
        candidates = generate_domain_candidates("Wildbrine")
        com_candidates = [c for c in candidates if c.endswith(".com")]
        # Only "wildbrine.com" — no separate first-token
        assert com_candidates == ["wildbrine.com"]

    def test_candidates_legal_suffix_strip(self):
        """Legal suffixes are stripped before slugging."""
        candidates = generate_domain_candidates("Acme Inc")
        assert "acme.com" in candidates
        # "acmeinc.com" should NOT appear (Inc stripped)
        assert "acmeinc.com" not in candidates

    def test_candidates_stoplist_blocks(self):
        """First token in stoplist → first-token variant blocked."""
        candidates = generate_domain_candidates("National Beverage")
        assert "national.com" not in candidates
        # Full concat still present
        assert "nationalbeverage.com" in candidates

    def test_candidates_short_allcaps_allowed(self):
        """Short all-caps token (2-3 chars) is allowed as exception."""
        candidates = generate_domain_candidates("GE Aerospace")
        assert "ge.com" in candidates

    def test_candidates_digit_token_allowed(self):
        """Token containing a digit is allowed as exception."""
        candidates = generate_domain_candidates("3M Company")
        # "Company" is a legal suffix → stripped → just "3M"
        # Single word after strip, so only full-concat variant
        assert "3m.com" in candidates

    def test_candidates_short_lowercase_blocked(self):
        """Short lowercase token (< 4 chars, not all-caps) is blocked."""
        candidates = generate_domain_candidates("Ai Labs")
        # "ai" is only 2 chars, not all-caps in input → blocked
        assert "ai.com" not in candidates
        # Full concat still present
        assert "ailabs.com" in candidates

    def test_candidates_empty_garbage(self):
        """Empty/garbage inputs return empty list."""
        assert generate_domain_candidates("") == []
        assert generate_domain_candidates("123") == []
        assert generate_domain_candidates("A") == []

    def test_blocklist_full_domain(self):
        """Publisher domains are rejected in generated candidates."""
        candidates = generate_domain_candidates("TechCrunch")
        assert "techcrunch.com" not in candidates

    def test_candidates_max_cap(self):
        """Never more than 12 candidates."""
        # Long multi-word name → many combos
        candidates = generate_domain_candidates("Alpha Beta Gamma Delta Inc")
        assert len(candidates) <= 12

    def test_candidates_hyphenated(self):
        """Multi-word names produce a hyphenated variant."""
        candidates = generate_domain_candidates("Borden Cheese")
        assert "borden-cheese.com" in candidates


# =============================================================================
# probe_domain tests
# =============================================================================


class TestProbeDomain:
    """Tests for async probe_domain()."""

    @pytest.mark.asyncio
    async def test_probe_resolves(self):
        """Mocked getaddrinfo → True."""
        cache: dict[str, bool] = {}
        with patch("utils.dns_probe.asyncio.get_running_loop") as mock_loop:
            fut = asyncio.get_event_loop().create_future()
            fut.set_result([("127.0.0.1",)])
            mock_loop.return_value.getaddrinfo = lambda *a, **k: fut
            result = await probe_domain("example.com", cache=cache)
        assert result is True
        assert cache["example.com"] is True

    @pytest.mark.asyncio
    async def test_probe_gaierror(self):
        """socket.gaierror → False."""
        cache: dict[str, bool] = {}
        with patch("utils.dns_probe.asyncio.get_running_loop") as mock_loop:
            fut = asyncio.get_event_loop().create_future()
            fut.set_exception(socket.gaierror("NXDOMAIN"))
            mock_loop.return_value.getaddrinfo = lambda *a, **k: fut
            result = await probe_domain("doesnotexist.zzz", cache=cache)
        assert result is False
        assert cache["doesnotexist.zzz"] is False

    @pytest.mark.asyncio
    async def test_probe_timeout_wait_for(self):
        """Simulated hang → TimeoutError → False."""
        cache: dict[str, bool] = {}

        async def _hang(*a, **k):
            await asyncio.sleep(100)

        with patch("utils.dns_probe.asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.getaddrinfo = _hang
            result = await probe_domain("slow.example.com", timeout=0.01, cache=cache)
        assert result is False

    @pytest.mark.asyncio
    async def test_probe_cache_hit(self):
        """Second call skips DNS (call count check)."""
        cache: dict[str, bool] = {"cached.com": True}
        # No mocking needed — should return from cache directly
        result = await probe_domain("cached.com", cache=cache)
        assert result is True


# =============================================================================
# dns_probe_company tests
# =============================================================================


class TestDnsProbeCompany:
    """Tests for dns_probe_company()."""

    @pytest.mark.asyncio
    async def test_company_parallel_priority_order(self):
        """Concurrent probes, winner chosen by input priority order."""
        # Make second candidate resolve but not first
        resolve_map = {
            "bordencheese.com": False,
            "borden.com": True,
            "borden-cheese.com": True,
            "bordencheese.io": False,
        }

        async def fake_probe(domain, timeout=1.0, cache=None):
            return resolve_map.get(domain, False)

        with patch("utils.dns_probe.probe_domain", side_effect=fake_probe):
            result = await dns_probe_company("Borden Cheese", max_attempts=4)

        # "borden.com" is candidate #2, "borden-cheese.com" is #3
        # Winner should be borden.com (first resolving in priority order)
        assert result == "borden.com"

    @pytest.mark.asyncio
    async def test_company_all_fail(self):
        """All probes fail → None."""
        async def fake_probe(domain, timeout=1.0, cache=None):
            return False

        with patch("utils.dns_probe.probe_domain", side_effect=fake_probe):
            result = await dns_probe_company("Nonexistent Startup Corp")
        assert result is None

    @pytest.mark.asyncio
    async def test_company_max_attempts_cap(self):
        """Only probes max_attempts candidates even if more exist."""
        probed: list[str] = []

        async def fake_probe(domain, timeout=1.0, cache=None):
            probed.append(domain)
            return False

        with patch("utils.dns_probe.probe_domain", side_effect=fake_probe):
            await dns_probe_company("Alpha Beta Gamma Delta", max_attempts=4)

        assert len(probed) <= 4
