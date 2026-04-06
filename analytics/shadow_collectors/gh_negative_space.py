"""Shadow GitHub negative-space collector.

Phase 0 task `p0.9`. First-wave shadow collector for the curated founder
watchlist. Writes only to `data/shadow/discovery.db`. No production touchpoints.

Why negative-space:
  - The strategy document's most differentiated bet: instead of watching for
    NEW positive activity (commits, releases, stars), watch for the *absence*
    of activity from a known founder's existing repos plus the *appearance*
    of new private repo references in their personal account.
  - Negative-space patterns:
      * Sustained drop in public commit cadence (founder is heads-down on
        a private project)
      * Disappearance from previous-employer repos (left the job)
      * New private-repo references in their public activity feed (org
        memberships, starred repos in adjacent niches)
  - All from the public GitHub API (`/users/<username>/events/public`,
    `/users/<username>/repos`). Authenticated. Rate-budgeted.

P2 prerequisite: this collector requires a CURATED founder watchlist
(`data/shadow/founder_watchlist.csv`). Without it, gh-negative-space would
issue unbounded API calls and bust the GITHUB_TOKEN budget. Run
`python -m scripts.build_founder_watchlist` first.

Rate-budget contract:
  - GITHUB_TOKEN allows 5000 req/hr authenticated.
  - This collector caps at 2000 req/hr (40% of token limit) to leave
    headroom for production github.py and github_activity.py collectors.
  - Per founder, the collector spends ~3 calls (events, repos, profile).
    At 2000 req/hr, that's ~666 founders/hour comfortably.

Fetcher protocol::

    def fetcher(path: str) -> Dict[str, Any]:
        \"\"\"Issue a GET to https://api.github.com/<path> and return JSON.\"\"\"

The default fetcher is a no-op that returns {}. Plug in httpx + auth when
ready to go live.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from analytics.shadow_collectors.base import (
    RateBudget,
    ShadowCollectorResult,
    make_run_id,
    persist_shadow_signal,
    utcnow_iso,
)
from analytics.shadow_sidecar import ShadowSidecar

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "shadow_gh_negative_space"

DEFAULT_WATCHLIST_PATH = Path("data/shadow/founder_watchlist.csv")
GITHUB_API_BASE = "https://api.github.com"

Fetcher = Callable[[str], Dict[str, Any]]


def null_fetcher(path: str) -> Dict[str, Any]:
    """Default fetcher — returns empty dict. Used until live HTTP is enabled."""
    return {}


@dataclass
class GhNegativeSpaceConfig:
    """Configuration for the GH negative-space shadow collector."""

    watchlist_path: Path = field(default_factory=lambda: DEFAULT_WATCHLIST_PATH)
    rate_per_hour: int = 2000        # 40% of GITHUB_TOKEN 5000/hr cap
    max_founders_per_run: int = 250
    quiet_window_days: int = 21      # consider founder "quiet" if no events in N days


@dataclass
class FounderWatchEntry:
    """One row from the founder watchlist."""

    founder_id: str
    full_name: str
    github_username: str
    linkedin_url: Optional[str] = None
    associated_company_id: Optional[str] = None
    source: Optional[str] = None


def load_watchlist(path: Path) -> List[FounderWatchEntry]:
    """Load the founder watchlist CSV. Returns entries with non-empty github_username."""
    if not path.exists():
        return []
    out: List[FounderWatchEntry] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gh = (row.get("github_username") or "").strip()
            if not gh:
                continue
            out.append(
                FounderWatchEntry(
                    founder_id=row.get("founder_id", ""),
                    full_name=row.get("full_name", ""),
                    github_username=gh,
                    linkedin_url=row.get("linkedin_url") or None,
                    associated_company_id=row.get("associated_company_id") or None,
                    source=row.get("source"),
                )
            )
    return out


def _parse_event_time(event: Dict[str, Any]) -> Optional[datetime]:
    raw = event.get("created_at")
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _is_negative_space_signal(
    events: List[Dict[str, Any]],
    quiet_window_days: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Inspect a founder's recent public events for negative-space patterns.

    Returns a dict describing the signal:
      - is_signal: bool
      - last_event_at: Optional[ISO 8601 string]
      - days_since_last_event: Optional[int]
      - signal_kind: 'quiet' | 'private_repo_ref' | 'org_change' | None
    """
    now = now or datetime.now(timezone.utc)
    last_event_at: Optional[datetime] = None

    for ev in events:
        ts = _parse_event_time(ev)
        if ts is not None and (last_event_at is None or ts > last_event_at):
            last_event_at = ts

    days_since = None
    is_signal = False
    signal_kind: Optional[str] = None

    if last_event_at is not None:
        days_since = (now - last_event_at).days
        if days_since >= quiet_window_days:
            is_signal = True
            signal_kind = "quiet"

    if not is_signal:
        # Look for private repo references (event types like ForkEvent on
        # private orgs aren't shown to unauth, but org membership events are)
        for ev in events:
            ev_type = ev.get("type")
            if ev_type == "MemberEvent":
                is_signal = True
                signal_kind = "org_change"
                break

    return {
        "is_signal": is_signal,
        "last_event_at": last_event_at.isoformat() if last_event_at else None,
        "days_since_last_event": days_since,
        "signal_kind": signal_kind,
    }


def collect(
    sidecar: ShadowSidecar,
    *,
    config: Optional[GhNegativeSpaceConfig] = None,
    fetcher: Fetcher = null_fetcher,
) -> ShadowCollectorResult:
    """Run the GH negative-space shadow collector once.

    Hard requires a non-empty founder watchlist at `config.watchlist_path`.
    """
    cfg = config or GhNegativeSpaceConfig()
    run_id = make_run_id(COLLECTOR_NAME)
    started_at = utcnow_iso()

    watchlist = load_watchlist(cfg.watchlist_path)
    notes: List[str] = []

    if not watchlist:
        notes.append(
            f"empty watchlist at {cfg.watchlist_path}; refusing to issue "
            "unbounded GitHub API calls (run scripts.build_founder_watchlist first)"
        )
        sidecar.begin_run(collector=COLLECTOR_NAME, run_id=run_id, notes=notes[0])
        sidecar.end_run(run_id=run_id, items_collected=0)
        return ShadowCollectorResult(
            collector=COLLECTOR_NAME,
            run_id=run_id,
            items_collected=0,
            items_persisted=0,
            started_at=started_at,
            completed_at=utcnow_iso(),
            notes=notes,
        )

    sidecar.begin_run(collector=COLLECTOR_NAME, run_id=run_id)

    budget = RateBudget(max_per_hour=cfg.rate_per_hour)
    persisted = 0
    examined = 0

    for entry in watchlist[: cfg.max_founders_per_run]:
        examined += 1
        try:
            budget.acquire()
            events_resp = fetcher(f"users/{entry.github_username}/events/public")
        except Exception as exc:
            logger.warning("gh fetch failed for %s: %s", entry.github_username, exc)
            notes.append(f"fetch error for {entry.github_username}: {exc}")
            continue

        events = events_resp.get("items") or events_resp.get("events") or []
        if not isinstance(events, list):
            events = []

        analysis = _is_negative_space_signal(events, cfg.quiet_window_days)
        if not analysis.get("is_signal"):
            continue

        canonical_key = (
            f"company:{entry.associated_company_id}"
            if entry.associated_company_id
            else f"founder:{entry.github_username}"
        )
        persist_shadow_signal(
            sidecar,
            collector=COLLECTOR_NAME,
            canonical_key=canonical_key,
            company_name=None,
            confidence=0.5 if analysis["signal_kind"] == "quiet" else 0.4,
            raw_data={
                "founder_id": entry.founder_id,
                "github_username": entry.github_username,
                "full_name": entry.full_name,
                "associated_company_id": entry.associated_company_id,
                "analysis": analysis,
            },
        )
        persisted += 1

    completed_at = utcnow_iso()
    sidecar.end_run(run_id=run_id, items_collected=persisted)

    return ShadowCollectorResult(
        collector=COLLECTOR_NAME,
        run_id=run_id,
        items_collected=examined,
        items_persisted=persisted,
        started_at=started_at,
        completed_at=completed_at,
        notes=notes,
    )
