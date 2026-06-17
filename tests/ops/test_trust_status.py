import pytest

from ops.collector_health import CollectorHealthReport, REPORT_SCHEMA_VERSION
from ops.trust_status import TrustStatusCLI, TrustStatusError


def test_requires_schema_version_2():
    cli = TrustStatusCLI()
    with pytest.raises(TrustStatusError, match="schema_version"):
        cli.load_reports(schema_version=1)


def test_accepts_schema_version_2():
    cli = TrustStatusCLI()
    reports = [
        CollectorHealthReport(collector="github", status="success"),
        CollectorHealthReport(collector="news_api", status="api_shape_changed",
                              detail="field missing"),
    ]
    summary = cli.summarize(reports)
    assert summary["schema_version"] == 2
    assert any(r["status"] == "api_shape_changed" for r in summary["collectors"])


def test_summary_flags_suspended_collectors(tmp_path):
    from storage.collector_suspension import SuspensionStore
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("news_api", reason="api_shape_changed: test")
    cli = TrustStatusCLI(suspension_store=store)
    reports = [CollectorHealthReport(collector="news_api", status="api_shape_changed")]
    summary = cli.summarize(reports)
    news_api_entry = next(r for r in summary["collectors"] if r["collector"] == "news_api")
    assert news_api_entry["suspended"] is True


def test_overall_status_is_degraded_when_any_suspended(tmp_path):
    from storage.collector_suspension import SuspensionStore
    store = SuspensionStore(tmp_path / "suspensions.json")
    store.suspend("github", reason="test")
    cli = TrustStatusCLI(suspension_store=store)
    reports = [CollectorHealthReport(collector="github", status="api_shape_changed")]
    summary = cli.summarize(reports)
    assert summary["overall"] == "degraded"
