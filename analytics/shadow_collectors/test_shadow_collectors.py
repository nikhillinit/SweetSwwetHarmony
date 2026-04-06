"""Tests for the three Phase 0 shadow collectors.

Each collector is tested with a fake fetcher / resolver so the test runs
offline. The tests verify:
  - Persistence path actually writes to the shadow DB
  - Confidence scoring is in expected ranges
  - Rate-budget cap and max-items cap are honoured
  - The negative-space empty-watchlist guard refuses to issue API calls
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from analytics.shadow_collectors import ct_log, dns_fingerprint, gh_negative_space
from analytics.shadow_collectors.base import RateBudget
from analytics.shadow_sidecar import (
    ReadMode,
    ShadowSidecar,
    ShadowSidecarConfig,
)


# ---- Fixtures --------------------------------------------------------------


@pytest.fixture
def sidecar(tmp_path: Path) -> ShadowSidecar:
    """A sidecar with no production DB requirement (collectors don't read it)."""
    cfg = ShadowSidecarConfig(
        production_db=tmp_path / "fake_signals.db",  # not actually opened
        shadow_db=tmp_path / "shadow" / "discovery.db",
        snapshot_db=tmp_path / "shadow" / "snapshot.db",
        read_mode=ReadMode.IMMUTABLE_URI,
        register_dbtool_lock=False,
    )
    s = ShadowSidecar(cfg)
    # We need an initialized shadow DB but not the production read path,
    # so we manually init the shadow conn without snapshot.
    s.config.shadow_db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    s._shadow_conn = sqlite3.connect(str(s.config.shadow_db))
    s._shadow_conn.executescript(ShadowSidecar.SHADOW_SCHEMA)
    s._shadow_conn.commit()
    s._initialized = True
    yield s
    s.close()


# ---- CT-log collector ------------------------------------------------------


def _fake_crtsh(query: str) -> List[Dict[str, Any]]:
    return [
        {
            "common_name": "newco.ai",
            "issuer": "Let's Encrypt",
            "not_before": "2026-04-01T00:00:00Z",
            "san": ["newco.ai", "www.newco.ai"],
        },
        {
            "common_name": "*.cdn.example.com",
            "issuer": "DigiCert",
            "not_before": "2026-04-01T00:00:00Z",
            "san": ["*.cdn.example.com"] * 50,
        },
    ]


def test_ct_log_persists_certs(sidecar):
    cfg = ct_log.CtLogConfig(queries=("%.ai",), rate_per_hour=3600)
    result = ct_log.collect(sidecar, config=cfg, fetcher=_fake_crtsh)
    assert result.items_persisted == 2
    rows = sidecar.shadow_query(
        "SELECT canonical_key, confidence FROM shadow_signals "
        "WHERE shadow_collector = 'shadow_ct_log'"
    )
    keys = {r["canonical_key"] for r in rows}
    assert "domain:newco.ai" in keys
    # Wildcard cert should be normalized: *.cdn.example.com -> domain:cdn.example.com
    assert "domain:cdn.example.com" in keys


def test_ct_log_records_run(sidecar):
    cfg = ct_log.CtLogConfig(queries=("%.ai",), rate_per_hour=3600)
    result = ct_log.collect(sidecar, config=cfg, fetcher=_fake_crtsh)
    runs = sidecar.shadow_query(
        "SELECT collector, items_collected, completed_at FROM shadow_runs"
    )
    assert len(runs) == 1
    assert runs[0]["collector"] == "shadow_ct_log"
    assert runs[0]["items_collected"] == 2


def test_ct_log_respects_max_certs_per_run(sidecar):
    def big_fetcher(query):
        return _fake_crtsh(query) * 100  # 200 entries
    cfg = ct_log.CtLogConfig(
        queries=("%.ai",), rate_per_hour=36000, max_certs_per_run=5
    )
    result = ct_log.collect(sidecar, config=cfg, fetcher=big_fetcher)
    assert result.items_persisted == 5


def test_ct_log_confidence_in_range(sidecar):
    cfg = ct_log.CtLogConfig(queries=("%.ai",), rate_per_hour=3600)
    ct_log.collect(sidecar, config=cfg, fetcher=_fake_crtsh)
    rows = sidecar.shadow_query("SELECT confidence FROM shadow_signals")
    for r in rows:
        assert 0.0 <= r["confidence"] <= 0.7


def test_ct_log_handles_fetcher_exception(sidecar):
    def bad_fetcher(query):
        raise RuntimeError("crt.sh is down")
    cfg = ct_log.CtLogConfig(queries=("%.ai",), rate_per_hour=3600)
    result = ct_log.collect(sidecar, config=cfg, fetcher=bad_fetcher)
    assert result.items_persisted == 0
    assert any("fetcher error" in n for n in result.notes)


# ---- DNS fingerprint collector ---------------------------------------------


def _fake_resolver(domain: str, record_type: str) -> List[str]:
    fixtures = {
        ("newco.ai", "MX"): ["10 aspmx.l.google.com"],
        ("newco.ai", "TXT"): [
            "v=spf1 include:_spf.google.com ~all",
            "google-site-verification=xxx",
            "stripe-verification=yyy",
        ],
        ("newco.ai", "NS"): ["bob.ns.cloudflare.com"],
        ("emptyco.com", "MX"): [],
        ("emptyco.com", "TXT"): [],
        ("emptyco.com", "NS"): [],
    }
    return fixtures.get((domain, record_type), [])


def test_dns_fingerprint_persists_only_domains_with_records(sidecar):
    inputs = [
        dns_fingerprint.DnsFingerprintInput(domain="newco.ai", company_name="Newco"),
        dns_fingerprint.DnsFingerprintInput(domain="emptyco.com"),
    ]
    cfg = dns_fingerprint.DnsFingerprintConfig(rate_per_hour=36000)
    result = dns_fingerprint.collect(
        sidecar, inputs, config=cfg, resolver=_fake_resolver
    )
    assert result.items_persisted == 1
    rows = sidecar.shadow_query(
        "SELECT canonical_key, company_name, confidence FROM shadow_signals"
    )
    assert len(rows) == 1
    assert rows[0]["canonical_key"] == "domain:newco.ai"
    assert rows[0]["company_name"] == "Newco"


def test_dns_fingerprint_confidence_reflects_vendor_hints(sidecar):
    """Domain with multiple vendor hints should score higher than minimal."""
    inputs = [
        dns_fingerprint.DnsFingerprintInput(domain="newco.ai"),
    ]
    cfg = dns_fingerprint.DnsFingerprintConfig(rate_per_hour=36000)
    dns_fingerprint.collect(sidecar, inputs, config=cfg, resolver=_fake_resolver)
    rows = sidecar.shadow_query("SELECT confidence FROM shadow_signals")
    assert rows[0]["confidence"] >= 0.5  # base 0.35 + Google MX + Stripe TXT + ...


def test_dns_fingerprint_respects_max_domains_per_run(sidecar):
    inputs = [
        dns_fingerprint.DnsFingerprintInput(domain=f"co{i}.ai") for i in range(10)
    ]

    def yes_resolver(d, r):
        return ["foo"]

    cfg = dns_fingerprint.DnsFingerprintConfig(
        rate_per_hour=36000, max_domains_per_run=3
    )
    result = dns_fingerprint.collect(sidecar, inputs, config=cfg, resolver=yes_resolver)
    assert result.items_persisted == 3


# ---- GH negative-space collector -------------------------------------------


def _make_watchlist_csv(path: Path, founders: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "founder_id",
                "full_name",
                "github_username",
                "linkedin_url",
                "source",
                "associated_company_id",
                "added_at",
            ],
        )
        writer.writeheader()
        for i, gh in enumerate(founders):
            writer.writerow(
                {
                    "founder_id": f"f_{i}",
                    "full_name": f"Founder {i}",
                    "github_username": gh,
                    "linkedin_url": "",
                    "source": "manual_seed",
                    "associated_company_id": f"co_{i}",
                    "added_at": "2026-04-06T00:00:00Z",
                }
            )


def test_gh_negative_space_refuses_empty_watchlist(sidecar, tmp_path):
    cfg = gh_negative_space.GhNegativeSpaceConfig(
        watchlist_path=tmp_path / "missing.csv",
        rate_per_hour=36000,
    )
    fetched: List[str] = []

    def tracked_fetcher(path):
        fetched.append(path)
        return {}

    result = gh_negative_space.collect(sidecar, config=cfg, fetcher=tracked_fetcher)
    assert result.items_persisted == 0
    assert fetched == [], "must NOT call fetcher when watchlist is empty"
    assert any("empty watchlist" in n for n in result.notes)


def test_gh_negative_space_persists_quiet_founder(sidecar, tmp_path):
    watchlist = tmp_path / "data" / "shadow" / "founder_watchlist.csv"
    _make_watchlist_csv(watchlist, ["quiet_founder", "active_founder"])

    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    def fake_fetcher(path):
        if "quiet_founder" in path:
            return {"events": [{"type": "PushEvent", "created_at": old}]}
        if "active_founder" in path:
            return {"events": [{"type": "PushEvent", "created_at": new}]}
        return {"events": []}

    cfg = gh_negative_space.GhNegativeSpaceConfig(
        watchlist_path=watchlist,
        rate_per_hour=36000,
        quiet_window_days=21,
    )
    result = gh_negative_space.collect(sidecar, config=cfg, fetcher=fake_fetcher)

    # Only the quiet founder should be persisted
    assert result.items_persisted == 1
    rows = sidecar.shadow_query(
        "SELECT canonical_key, raw_data FROM shadow_signals "
        "WHERE shadow_collector = 'shadow_gh_negative_space'"
    )
    assert len(rows) == 1
    assert "co_0" in rows[0]["canonical_key"]


def test_gh_negative_space_records_org_change_signal(sidecar, tmp_path):
    watchlist = tmp_path / "data" / "shadow" / "founder_watchlist.csv"
    _make_watchlist_csv(watchlist, ["recently_active_with_org_change"])

    new = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    def fake_fetcher(path):
        return {
            "events": [
                {"type": "PushEvent", "created_at": new},
                {"type": "MemberEvent", "created_at": new},
            ]
        }

    cfg = gh_negative_space.GhNegativeSpaceConfig(
        watchlist_path=watchlist,
        rate_per_hour=36000,
        quiet_window_days=21,
    )
    result = gh_negative_space.collect(sidecar, config=cfg, fetcher=fake_fetcher)
    assert result.items_persisted == 1


# ---- Rate budget primitive -------------------------------------------------


def test_rate_budget_call_count_tracks_acquires():
    budget = RateBudget(max_per_hour=3600 * 10)  # 10/sec, very fast
    for _ in range(5):
        budget.acquire()
    assert budget.call_count == 5


def test_rate_budget_invalid_rate_raises():
    with pytest.raises(ValueError):
        RateBudget(max_per_hour=0)
    with pytest.raises(ValueError):
        RateBudget(max_per_hour=-1)
