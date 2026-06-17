from __future__ import annotations

from typing import Any

from ops.collector_health import CollectorHealthReport, REPORT_SCHEMA_VERSION
from storage.collector_suspension import SuspensionStore


class TrustStatusError(RuntimeError):
    pass


class TrustStatusCLI:
    def __init__(self, suspension_store: SuspensionStore | None = None) -> None:
        self.suspension_store = suspension_store

    def load_reports(self, schema_version: int) -> None:
        if schema_version != REPORT_SCHEMA_VERSION:
            raise TrustStatusError(
                f"trust status CLI requires schema_version={REPORT_SCHEMA_VERSION} "
                f"(collector_health v2), got schema_version={schema_version}. "
                "Ensure M3 (collector_health v2) is deployed before running M7."
            )

    def summarize(self, reports: list[CollectorHealthReport]) -> dict[str, Any]:
        self.load_reports(schema_version=REPORT_SCHEMA_VERSION)
        collectors = []
        any_suspended = False
        for r in reports:
            suspended = bool(
                self.suspension_store and self.suspension_store.is_suspended(r.collector)
            )
            if suspended:
                any_suspended = True
            collectors.append({
                "collector": r.collector,
                "status": r.status,
                "detail": r.detail,
                "suspended": suspended,
            })
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "overall": "degraded" if any_suspended else "ok",
            "collectors": collectors,
        }
