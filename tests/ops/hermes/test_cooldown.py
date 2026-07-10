from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from integrations.hermes.cooldown import (
    COOLDOWN_MAX,
    COOLDOWN_REPEAT_WINDOW,
    ProviderCooldownStore,
)

START = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, now: datetime = START):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def _store(tmp_path: Path, clock: _Clock | None = None) -> ProviderCooldownStore:
    return ProviderCooldownStore(
        tmp_path / "provider-state.json",
        clock=clock or _Clock(),
    )


def test_missing_state_file_means_not_cooling(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.is_cooling("kimi") is False
    assert store.cooling_until("kimi") is None


def test_set_cooldown_uses_default_minutes(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    until = store.set_cooldown("kimi")
    assert until == START + timedelta(minutes=60)
    assert store.is_cooling("kimi") is True


def test_set_cooldown_honors_retry_after_hint_in_message(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    until = store.set_cooldown("kimi", message="429: retry after 2 minutes")
    assert until == START + timedelta(minutes=2)


def test_repeat_trigger_within_window_doubles_duration(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    store.set_cooldown("kimi", message="retry after 10 seconds")
    clock.advance(timedelta(seconds=10) + COOLDOWN_REPEAT_WINDOW / 2)
    until = store.set_cooldown("kimi", message="retry after 10 seconds")
    assert until == clock.now + timedelta(seconds=20)


def test_cooldown_capped_at_max(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    until = store.set_cooldown("kimi", message="retry after 90000 seconds")
    assert until == START + COOLDOWN_MAX


def test_cooling_expires_with_clock(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path, clock)
    store.set_cooldown("kimi", message="retry after 30 seconds")
    clock.advance(timedelta(seconds=31))
    assert store.is_cooling("kimi") is False


def test_corrupt_state_file_is_sidelined_and_store_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "provider-state.json"
    path.write_text("{not json", encoding="utf-8")
    store = ProviderCooldownStore(path, clock=_Clock())
    assert store.is_cooling("kimi") is False
    assert path.with_suffix(".json.corrupt").exists()
    store.set_cooldown("kimi")
    assert store.is_cooling("kimi") is True


def test_concurrent_longer_cooldown_is_not_shortened(tmp_path: Path) -> None:
    clock = _Clock()
    path = tmp_path / "provider-state.json"
    longer = (START + timedelta(hours=3)).isoformat()
    path.write_text(
        json.dumps({"providers": {"kimi": {"coolingUntil": longer}}}),
        encoding="utf-8",
    )
    store = ProviderCooldownStore(path, clock=clock)
    store.set_cooldown("kimi", message="retry after 60 seconds")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["providers"]["kimi"]["coolingUntil"] == longer


def test_state_survives_reload_from_disk(tmp_path: Path) -> None:
    clock = _Clock()
    _store(tmp_path, clock).set_cooldown("kimi")
    reloaded = _store(tmp_path, clock)
    assert reloaded.is_cooling("kimi") is True
