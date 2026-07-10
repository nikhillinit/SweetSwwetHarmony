from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .failures import parse_retry_after

COOLDOWN_REPEAT_WINDOW = timedelta(minutes=10)
COOLDOWN_MAX = timedelta(hours=24)
DEFAULT_COOLDOWN_MINUTES = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderCooldownStore:
    """Advisory per-provider cooldown state persisted next to the ledger.

    Fail-open by design: unreadable or unwritable state never blocks a run.
    Concurrent writers are merged by keeping the longer cooldown per provider.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
        default_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    ):
        self.path = Path(path)
        self._clock = clock or _utcnow
        self.default_minutes = default_minutes

    def is_cooling(self, name: str) -> bool:
        until = self.cooling_until(name)
        return until is not None and until > self._clock()

    def cooling_until(self, name: str) -> datetime | None:
        return self._read().get(name)

    def set_cooldown(
        self,
        name: str,
        *,
        message: str = "",
        minutes: int | None = None,
    ) -> datetime:
        now = self._clock()
        state = self._read()
        prior = state.get(name)

        default = timedelta(minutes=minutes if minutes is not None else self.default_minutes)
        explicit = parse_retry_after(message, now=now)
        duration = (explicit - now) if explicit is not None else default
        if duration <= timedelta(0):
            duration = default

        repeat = prior is not None and now <= prior + COOLDOWN_REPEAT_WINDOW
        if repeat:
            duration = duration * 2
        duration = min(duration, COOLDOWN_MAX)

        state[name] = now + duration
        persisted = self._write(state, triggered_at=now)
        return persisted.get(name, state[name])

    def _read(self) -> dict[str, datetime]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._sideline_corrupt()
            return {}
        return _normalize(raw)

    def _sideline_corrupt(self) -> None:
        try:
            corrupt = self.path.with_suffix(self.path.suffix + ".corrupt")
            os.replace(self.path, corrupt)
        except OSError:
            # Advisory state must fail open even when the corrupt file is stuck.
            pass

    def _write(
        self,
        state: dict[str, datetime],
        *,
        triggered_at: datetime,
    ) -> dict[str, datetime]:
        # Re-read immediately before writing so a parallel run cannot shorten
        # a longer advisory cooldown.
        merged = dict(self._read())
        for name, until in state.items():
            prior = merged.get(name)
            if prior is None or until > prior:
                merged[name] = until

        payload = {
            "providers": {
                name: {
                    "coolingUntil": until.isoformat(),
                    "reason": "rate_limited",
                    "triggeredAt": triggered_at.isoformat(),
                }
                for name, until in sorted(merged.items())
            }
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(f".{os.getpid()}.tmp")
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            return state
        return merged


def _normalize(raw: object) -> dict[str, datetime]:
    providers = raw.get("providers") if isinstance(raw, dict) else None
    if not isinstance(providers, dict):
        return {}
    normalized: dict[str, datetime] = {}
    for name, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        try:
            until = datetime.fromisoformat(str(entry.get("coolingUntil")))
        except ValueError:
            continue
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        normalized[str(name)] = until
    return normalized
