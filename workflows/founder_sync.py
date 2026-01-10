"""
Founder Sync Job for Discovery Engine

Syncs founder identity data from Notion CRM into local FounderStore (SQLite),
so the web app can render founder cards without calling Notion live.

What it does:
1) Fetches Notion pages across pipeline statuses via a single OR query
2) Extracts Company canonical_key (or builds from Website / Company Name)
3) Extracts Founder name + Founder LinkedIn URL(s)
4) Normalizes LinkedIn URLs and upserts FounderProfile rows into FounderStore
5) Detects and reports conflicts (same founder_key linked to multiple companies)

Usage:
    # One-time sync
    python -m workflows.founder_sync --db-path signals.db

    # Dry run
    python -m workflows.founder_sync --dry-run

    # Run every 15 minutes
    python -m workflows.founder_sync --interval 900
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv

from connectors.notion_connector_v2 import NotionConnector
from storage.founder_store import FounderStore, FounderProfile
from utils.canonical_keys import normalize_domain, is_strong_key

logger = logging.getLogger(__name__)


@dataclass
class FounderSyncStats:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    notion_pages_fetched: int = 0
    notion_errors: int = 0

    companies_processed: int = 0
    companies_with_canonical_key: int = 0
    companies_without_canonical_key: int = 0
    companies_with_strong_key: int = 0
    companies_with_weak_key: int = 0

    founders_found: int = 0
    founders_synced: int = 0
    founders_skipped_no_linkedin: int = 0
    founders_skipped_invalid_linkedin: int = 0
    founders_conflicts: int = 0

    errors: List[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if not self.completed_at:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "notion_pages_fetched": self.notion_pages_fetched,
            "notion_errors": self.notion_errors,
            "companies_processed": self.companies_processed,
            "companies_with_canonical_key": self.companies_with_canonical_key,
            "companies_without_canonical_key": self.companies_without_canonical_key,
            "companies_with_strong_key": self.companies_with_strong_key,
            "companies_with_weak_key": self.companies_with_weak_key,
            "founders_found": self.founders_found,
            "founders_synced": self.founders_synced,
            "founders_skipped_no_linkedin": self.founders_skipped_no_linkedin,
            "founders_skipped_invalid_linkedin": self.founders_skipped_invalid_linkedin,
            "founders_conflicts": self.founders_conflicts,
            "errors_count": len(self.errors),
            "errors": self.errors[:10],
        }

    def log_summary(self) -> None:
        logger.info("=" * 80)
        logger.info("FOUNDER SYNC SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Started:   {self.started_at.isoformat()}")
        if self.completed_at:
            logger.info(f"Completed: {self.completed_at.isoformat()}")
            logger.info(f"Duration:  {self.duration_seconds:.2f}s")
        logger.info("")
        logger.info("Notion:")
        logger.info(f"  Pages fetched: {self.notion_pages_fetched}")
        if self.notion_errors:
            logger.warning(f"  Errors: {self.notion_errors}")
        logger.info("")
        logger.info("Companies:")
        logger.info(f"  Processed: {self.companies_processed}")
        logger.info(f"  With canonical key: {self.companies_with_canonical_key}")
        logger.info(f"  Without canonical key: {self.companies_without_canonical_key}")
        logger.info(f"  Strong keys: {self.companies_with_strong_key}")
        logger.info(f"  Weak keys: {self.companies_with_weak_key}")
        logger.info("")
        logger.info("Founders:")
        logger.info(f"  Found: {self.founders_found}")
        logger.info(f"  Synced: {self.founders_synced}")
        logger.info(f"  Skipped (no LinkedIn): {self.founders_skipped_no_linkedin}")
        logger.info(f"  Skipped (invalid LinkedIn): {self.founders_skipped_invalid_linkedin}")
        logger.info(f"  Conflicts: {self.founders_conflicts}")
        if self.errors:
            logger.warning("")
            logger.warning(f"Errors encountered: {len(self.errors)}")
            for i, err in enumerate(self.errors[:5], 1):
                logger.warning(f"  {i}. {err}")
            if len(self.errors) > 5:
                logger.warning(f"  ... and {len(self.errors) - 5} more")
        logger.info("=" * 80)


STATUS_VALUES = [
    "Source",
    "Initial Meeting / Call",
    "Dilligence",
    "Tracking",
    "Committed",
    "Funded",
    "Passed",
    "Lost",
]

_LINKEDIN_IN_RE = re.compile(
    r"(https?://)?(www\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)", re.IGNORECASE
)


def _extract_text(prop: Dict[str, Any]) -> Optional[str]:
    rt = prop.get("rich_text", [])
    if rt:
        return (rt[0].get("text", {}) or {}).get("content", "")
    return None


def _extract_title(prop: Dict[str, Any]) -> str:
    title = prop.get("title", [])
    if title:
        return (title[0].get("text", {}) or {}).get("content", "")
    return ""


def _extract_select(prop: Dict[str, Any]) -> Optional[str]:
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _extract_plain(prop: Dict[str, Any]) -> str:
    if not prop:
        return ""
    if prop.get("rich_text"):
        return _extract_text(prop) or ""
    if prop.get("title"):
        return _extract_title(prop) or ""
    return ""


def _extract_linkedin_urls(prop: Dict[str, Any]) -> List[str]:
    if not prop:
        return []

    urls: List[str] = []

    direct = prop.get("url")
    if isinstance(direct, str) and direct.strip():
        urls.append(direct.strip())

    rt = prop.get("rich_text", [])
    for chunk in rt:
        href = chunk.get("href")
        if isinstance(href, str) and href.strip():
            urls.append(href.strip())

        text = (chunk.get("plain_text") or "").strip()
        if text:
            for m in _LINKEDIN_IN_RE.finditer(text):
                urls.append(m.group(0))

    title = prop.get("title", [])
    for chunk in title:
        text = (chunk.get("plain_text") or "").strip()
        if text:
            for m in _LINKEDIN_IN_RE.finditer(text):
                urls.append(m.group(0))

    seen = set()
    out: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _normalize_linkedin_person_url(url: str) -> Optional[Tuple[str, str]]:
    if not url or not isinstance(url, str):
        return None

    url = url.strip()
    if not url:
        return None

    if url.startswith("www."):
        url = "https://" + url
    if not url.startswith("http://") and not url.startswith("https://"):
        if "linkedin.com/" in url:
            url = "https://" + url
        else:
            return None

    parsed = urlparse(url)

    host = (parsed.netloc or "").lower()
    host = host.replace("www.", "")
    if host != "linkedin.com":
        return None

    clean = parsed._replace(query="", fragment="")
    path = clean.path.rstrip("/")

    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    if parts[0].lower() != "in":
        return None

    slug = parts[1].strip()
    if not slug:
        return None

    founder_key = f"linkedin:{slug.lower()}"
    canonical_profile_url = f"https://www.linkedin.com/in/{slug}"
    return founder_key, canonical_profile_url


def _fallback_name_from_slug(founder_key: str) -> str:
    slug = founder_key.split(":", 1)[-1]
    name = slug.replace("-", " ").replace("_", " ").strip()
    return name.title() if name else "Unknown Founder"


def _slug(text: str) -> str:
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text.strip("-")


def _build_company_canonical_key(
    notion: NotionConnector,
    props: Dict[str, Any],
) -> Tuple[Optional[str], str, str]:
    company_name = _extract_title(props.get(notion.PROP_COMPANY_NAME, {}))
    website = (props.get(notion.PROP_WEBSITE, {}) or {}).get("url", "") or ""
    canonical_key = _extract_text(props.get(notion.PROP_CANONICAL_KEY, {}))

    if not canonical_key and website:
        domain = normalize_domain(website)
        if domain:
            canonical_key = f"domain:{domain}"

    if not canonical_key and company_name:
        s = _slug(company_name)
        if s:
            canonical_key = f"name_loc:{s}"

    return canonical_key, company_name, website


async def _schema_preflight(notion: NotionConnector, strict: bool = True) -> None:
    result = await notion.validate_schema(force_refresh=True)
    if not result.valid:
        msg = f"Notion schema validation failed:\n{result}"
        if strict:
            raise ValueError(msg)
        logger.warning(msg)


class FounderSync:
    def __init__(
        self,
        notion: NotionConnector,
        founder_store: FounderStore,
        dry_run: bool = False,
        skip_relinks: bool = True,
    ):
        self.notion = notion
        self.founder_store = founder_store
        self.dry_run = dry_run
        self.skip_relinks = skip_relinks
        self.stats = FounderSyncStats()

    async def run(self) -> FounderSyncStats:
        logger.info("Starting founder sync...")
        self.stats = FounderSyncStats()

        try:
            await _schema_preflight(self.notion, strict=False)

            pages = await self._fetch_all_pages()
            self.stats.notion_pages_fetched = len(pages)

            for page in pages:
                await self._process_page(page)

            self.stats.completed_at = datetime.now(timezone.utc)
            self.stats.log_summary()

        except Exception as e:
            self.stats.errors.append(f"Fatal error: {e}")
            logger.exception("Founder sync failed")
            raise

        return self.stats

    async def _fetch_all_pages(self) -> List[Dict[str, Any]]:
        logger.info("Fetching all pages from Notion...")
        all_pages: List[Dict[str, Any]] = []

        filter_obj = {
            "or": [
                {"property": "Status", "select": {"equals": s}}
                for s in STATUS_VALUES
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            has_more = True
            start_cursor = None

            while has_more:
                payload: Dict[str, Any] = {
                    "filter": filter_obj,
                    "page_size": 100,
                }
                if start_cursor:
                    payload["start_cursor"] = start_cursor

                resp = await client.post(
                    f"https://api.notion.com/v1/databases/{self.notion.database_id}/query",
                    headers=self.notion.headers,
                    json=payload,
                )

                if resp.status_code != 200:
                    self.stats.notion_errors += 1
                    logger.error(f"Notion API error: {resp.status_code} {resp.text}")
                    break

                data = resp.json()
                all_pages.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                start_cursor = data.get("next_cursor")

                await asyncio.sleep(0.35)

        logger.info(f"Fetched {len(all_pages)} pages from Notion")
        return all_pages

    async def _process_page(self, page: Dict[str, Any]) -> None:
        self.stats.companies_processed += 1
        props = page.get("properties", {})

        canonical_key, company_name, website = _build_company_canonical_key(
            self.notion, props
        )

        if not canonical_key:
            self.stats.companies_without_canonical_key += 1
            logger.debug(f"Skipping {company_name}: no canonical key")
            return

        self.stats.companies_with_canonical_key += 1
        if is_strong_key(canonical_key):
            self.stats.companies_with_strong_key += 1
        else:
            self.stats.companies_with_weak_key += 1

        founder_name = _extract_plain(props.get(self.notion.PROP_FOUNDER, {}))
        linkedin_urls = _extract_linkedin_urls(
            props.get(self.notion.PROP_FOUNDER_LINKEDIN, {})
        )

        if not linkedin_urls:
            self.stats.founders_skipped_no_linkedin += 1
            return

        self.stats.founders_found += 1

        for url in linkedin_urls:
            result = _normalize_linkedin_person_url(url)
            if not result:
                self.stats.founders_skipped_invalid_linkedin += 1
                logger.debug(f"Invalid LinkedIn URL: {url}")
                continue

            founder_key, linkedin_url = result
            name = founder_name or _fallback_name_from_slug(founder_key)

            if self.skip_relinks:
                existing = await self.founder_store.get_founder(founder_key)
                if existing and existing.canonical_key != canonical_key:
                    self.stats.founders_conflicts += 1
                    logger.warning(
                        f"Conflict: {founder_key} already linked to {existing.canonical_key}, "
                        f"skipping relink to {canonical_key}"
                    )
                    continue

            if self.dry_run:
                logger.info(f"[DRY RUN] Would sync founder: {name} ({founder_key}) -> {canonical_key}")
                self.stats.founders_synced += 1
                continue

            profile = FounderProfile(
                name=name,
                founder_key=founder_key,
                canonical_key=canonical_key,
                source_api="notion",
                linkedin_url=linkedin_url,
            )

            try:
                await self.founder_store.save_founder(profile)
                self.stats.founders_synced += 1
                logger.debug(f"Synced founder: {name} ({founder_key})")
            except Exception as e:
                self.stats.errors.append(f"Failed to save {founder_key}: {e}")
                logger.error(f"Failed to save founder {founder_key}: {e}")


async def run_founder_sync(
    db_path: str = "signals.db",
    dry_run: bool = False,
    skip_relinks: bool = True,
    verbose: bool = False,
) -> FounderSyncStats:
    load_dotenv()

    notion_api_key = os.environ.get("NOTION_API_KEY", "")
    notion_database_id = os.environ.get("NOTION_DATABASE_ID", "")

    if not notion_api_key or not notion_database_id:
        raise ValueError("NOTION_API_KEY and NOTION_DATABASE_ID must be set")

    notion = NotionConnector(
        api_key=notion_api_key,
        database_id=notion_database_id,
    )

    founder_store = FounderStore(db_path)
    await founder_store.initialize()

    try:
        sync = FounderSync(
            notion=notion,
            founder_store=founder_store,
            dry_run=dry_run,
            skip_relinks=skip_relinks,
        )
        return await sync.run()
    finally:
        await founder_store.close()


async def run_scheduled(
    interval_seconds: int,
    db_path: str = "signals.db",
    dry_run: bool = False,
) -> None:
    logger.info(f"Starting scheduled founder sync (interval: {interval_seconds}s)")
    while True:
        try:
            await run_founder_sync(db_path=db_path, dry_run=dry_run)
        except Exception as e:
            logger.exception(f"Scheduled sync failed: {e}")
        await asyncio.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="Sync founders from Notion to local store")
    parser.add_argument("--db-path", default="signals.db", help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without writing")
    parser.add_argument("--interval", type=int, help="Run on interval (seconds)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--allow-relinks", action="store_true", help="Allow relinking founders to different companies")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if args.interval:
        asyncio.run(run_scheduled(
            interval_seconds=args.interval,
            db_path=args.db_path,
            dry_run=args.dry_run,
        ))
    else:
        asyncio.run(run_founder_sync(
            db_path=args.db_path,
            dry_run=args.dry_run,
            skip_relinks=not args.allow_relinks,
            verbose=args.verbose,
        ))


if __name__ == "__main__":
    main()
