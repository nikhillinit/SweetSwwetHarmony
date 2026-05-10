from __future__ import annotations

import pytest

from monitoring.events import EventType
from monitoring.models import Watch
from monitoring.website_monitor import WebsiteMonitor


class FakeSignalStore:
    def __init__(self):
        self.enqueue_calls = []

    async def enqueue_notion_write(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return 1


@pytest.mark.asyncio
async def test_profile_update_enqueue_sets_canonical_outbox_event_type():
    store = FakeSignalStore()
    monitor = WebsiteMonitor(signal_store=store)
    watch = Watch(
        id=7,
        canonical_key="domain:acme.com",
        url="https://acme.com",
    )

    await monitor._enqueue_profile_update(
        watch=watch,
        snapshot_id=11,
        diff_id=12,
        trigger="high_severity",
    )

    assert store.enqueue_calls == [{
        "idempotency_key": "profile_update:7:11",
        "payload": {
            "event_type": EventType.PROFILE_UPDATE_REQUESTED.value,
            "version": 1,
            "watch_id": 7,
            "snapshot_id": 11,
            "diff_id": 12,
            "trigger": "high_severity",
            "canonical_key": "domain:acme.com",
            "url": "https://acme.com",
        },
        "event_type": EventType.PROFILE_UPDATE_REQUESTED.value,
    }]
