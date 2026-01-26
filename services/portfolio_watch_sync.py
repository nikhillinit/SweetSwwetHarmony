"""
Portfolio Watch Sync Service

Syncs portfolio companies from config/portfolio.json to the monitoring watches table.
Uses UPSERT pattern to safely update existing watches without breaking foreign key references.

Usage:
    from services.portfolio_watch_sync import PortfolioWatchSync

    sync = PortfolioWatchSync(signal_store)
    stats = await sync.sync_portfolio()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)


@dataclass
class PortfolioCompany:
    """A company from portfolio.json."""
    name: str
    website: str
    canonical_key: Optional[str] = None

    def __post_init__(self):
        """Generate canonical key from website if not provided."""
        if not self.canonical_key:
            parsed = urlparse(self.website)
            domain = parsed.netloc or parsed.path
            # Remove www. prefix and lowercase
            domain = domain.lower().replace("www.", "")
            self.canonical_key = f"domain:{domain}"


@dataclass
class SyncStats:
    """Statistics from a portfolio sync run."""
    companies_in_config: int = 0
    watches_created: int = 0
    watches_updated: int = 0
    watches_deactivated: int = 0
    watches_adopted: int = 0  # Manual watches upgraded to portfolio type
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "companies_in_config": self.companies_in_config,
            "watches_created": self.watches_created,
            "watches_updated": self.watches_updated,
            "watches_deactivated": self.watches_deactivated,
            "watches_adopted": self.watches_adopted,
            "errors": self.errors,
            "total_portfolio_watches": self.watches_created + self.watches_updated + self.watches_adopted,
        }


class PortfolioWatchSync:
    """
    Sync portfolio companies to monitoring watches.

    Features:
    - Loads companies from portfolio.json
    - Creates/updates watches with watch_type='portfolio'
    - Uses UPSERT (ON CONFLICT DO UPDATE) to preserve FK references
    - Deactivates portfolio watches not in current config
    - Can adopt existing 'website' watches as 'portfolio' type
    """

    DEFAULT_PORTFOLIO_PATH = Path("config/portfolio.json")
    WATCH_TYPE = "portfolio"
    DEFAULT_INTERVAL = 86400  # 24 hours

    def __init__(
        self,
        signal_store: "SignalStore",
        portfolio_path: Optional[Path] = None,
    ):
        """
        Initialize PortfolioWatchSync.

        Args:
            signal_store: SignalStore instance for database access
            portfolio_path: Path to portfolio.json (default: config/portfolio.json)
        """
        self._store = signal_store
        self._portfolio_path = portfolio_path or self.DEFAULT_PORTFOLIO_PATH

    @property
    def _db(self):
        """Get database connection from SignalStore."""
        return self._store._db

    def load_portfolio(self) -> List[PortfolioCompany]:
        """
        Load portfolio companies from JSON file.

        Returns:
            List of PortfolioCompany objects

        Note:
            Returns empty list if file missing or empty (logs warning, doesn't fail)
        """
        if not self._portfolio_path.exists():
            logger.warning(
                f"Portfolio file not found: {self._portfolio_path}. "
                "Create it with company entries to enable portfolio monitoring."
            )
            return []

        try:
            with open(self._portfolio_path, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {self._portfolio_path}: {e}")
            return []

        companies = data.get("companies", [])
        if not companies:
            logger.warning(
                f"Portfolio file is empty: {self._portfolio_path}. "
                "Add companies to enable portfolio monitoring."
            )
            return []

        result = []
        for entry in companies:
            if not entry.get("name") or not entry.get("website"):
                logger.warning(f"Skipping invalid portfolio entry: {entry}")
                continue

            result.append(PortfolioCompany(
                name=entry["name"],
                website=entry["website"],
                canonical_key=entry.get("canonical_key"),
            ))

        return result

    async def sync_portfolio(
        self,
        dry_run: bool = False,
        deactivate_missing: bool = True,
    ) -> SyncStats:
        """
        Sync portfolio companies to monitoring watches.

        Args:
            dry_run: If True, don't make changes
            deactivate_missing: If True, deactivate portfolio watches not in config

        Returns:
            SyncStats with operation counts
        """
        stats = SyncStats()

        companies = self.load_portfolio()
        stats.companies_in_config = len(companies)

        if not companies:
            logger.info("No portfolio companies to sync")
            return stats

        # Track canonical keys in current config
        config_keys = set()

        for company in companies:
            config_keys.add(company.canonical_key)

            try:
                created, updated = await self._upsert_watch(
                    company=company,
                    dry_run=dry_run,
                )
                if created:
                    stats.watches_created += 1
                elif updated:
                    stats.watches_updated += 1

            except Exception as e:
                error_msg = f"Error syncing {company.name}: {e}"
                logger.error(error_msg)
                stats.errors.append(error_msg)

        # Deactivate portfolio watches not in config
        if deactivate_missing and not dry_run:
            deactivated = await self._deactivate_missing(config_keys)
            stats.watches_deactivated = deactivated

        # Log summary
        logger.info(
            f"Portfolio sync complete: "
            f"{stats.watches_created} created, "
            f"{stats.watches_updated} updated, "
            f"{stats.watches_deactivated} deactivated, "
            f"{len(stats.errors)} errors"
        )

        return stats

    async def _upsert_watch(
        self,
        company: PortfolioCompany,
        dry_run: bool = False,
    ) -> tuple[bool, bool]:
        """
        Upsert a portfolio watch.

        Uses ON CONFLICT DO UPDATE to preserve foreign key references
        (diffs and snapshots reference watch IDs).

        Args:
            company: Portfolio company to sync
            dry_run: If True, don't make changes

        Returns:
            (created, updated) - True if watch was created/updated
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Check if watch already exists
        cursor = await self._db.execute(
            """
            SELECT id, active FROM watches
            WHERE canonical_key = ? AND url = ? AND watch_type = ?
            """,
            (company.canonical_key, company.website, self.WATCH_TYPE)
        )
        existing = await cursor.fetchone()

        if dry_run:
            if existing:
                logger.debug(f"Would update watch for {company.name}")
                return (False, True)
            else:
                logger.debug(f"Would create watch for {company.name}")
                return (True, False)

        now = datetime.now(timezone.utc).isoformat()

        # UPSERT: Insert or update (preserves ID for FK references)
        await self._db.execute(
            """
            INSERT INTO watches (canonical_key, url, watch_type, interval_seconds, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key, watch_type, url) DO UPDATE SET
                interval_seconds = excluded.interval_seconds,
                active = 1,
                updated_at = excluded.updated_at
            """,
            (
                company.canonical_key,
                company.website,
                self.WATCH_TYPE,
                self.DEFAULT_INTERVAL,
                now,
                now,
            )
        )
        await self._db.commit()

        if existing:
            logger.info(f"Updated portfolio watch for {company.name}: {company.website}")
            return (False, True)
        else:
            logger.info(f"Created portfolio watch for {company.name}: {company.website}")
            return (True, False)

    async def _deactivate_missing(self, config_keys: set) -> int:
        """
        Deactivate portfolio watches not in current config.

        Args:
            config_keys: Set of canonical_keys in current portfolio config

        Returns:
            Count of deactivated watches
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        # Get all active portfolio watches
        cursor = await self._db.execute(
            """
            SELECT id, canonical_key, url FROM watches
            WHERE watch_type = ? AND active = 1
            """,
            (self.WATCH_TYPE,)
        )
        rows = await cursor.fetchall()

        # Find watches not in config
        to_deactivate = []
        for row in rows:
            watch_id, canonical_key, url = row
            if canonical_key not in config_keys:
                to_deactivate.append((watch_id, canonical_key))

        if not to_deactivate:
            return 0

        # Deactivate them
        now = datetime.now(timezone.utc).isoformat()
        for watch_id, canonical_key in to_deactivate:
            await self._db.execute(
                """
                UPDATE watches
                SET active = 0,
                    deactivated_reason = 'removed_from_portfolio',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, watch_id)
            )
            logger.info(f"Deactivated portfolio watch {watch_id} ({canonical_key}): removed from config")

        await self._db.commit()
        return len(to_deactivate)

    async def get_portfolio_watches(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """
        Get all portfolio watches.

        Args:
            active_only: Only return active watches

        Returns:
            List of watch dicts
        """
        if not self._db:
            raise RuntimeError("Database not initialized")

        query = """
            SELECT id, canonical_key, url, watch_type, interval_seconds,
                   last_checked_at, active, created_at
            FROM watches
            WHERE watch_type = ?
        """
        params = [self.WATCH_TYPE]

        if active_only:
            query += " AND active = 1"

        query += " ORDER BY canonical_key"

        cursor = await self._db.execute(query, params)
        rows = await cursor.fetchall()

        return [
            {
                "id": row[0],
                "canonical_key": row[1],
                "url": row[2],
                "watch_type": row[3],
                "interval_seconds": row[4],
                "last_checked_at": row[5],
                "active": bool(row[6]),
                "created_at": row[7],
            }
            for row in rows
        ]
