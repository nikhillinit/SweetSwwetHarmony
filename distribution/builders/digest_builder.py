"""
Digest Builder

Builds weekly digest emails with anti-spam logic.
Uses SignalStore directly (no HTTP calls) for data access.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader

from api.auth.magic_tokens import create_action_token, create_magic_link_url
from distribution.config import DistributionConfig
from storage.signal_store import SignalStore

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


@dataclass
class DigestCompany:
    """Company data prepared for digest template."""
    canonical_key: str
    name: str
    website: Optional[str]
    one_liner: Optional[str]
    confidence_pct: int
    confidence_color: str
    sources: str
    track_url: str
    pass_url: str
    details_url: str


@dataclass
class DigestResult:
    """Result of building a digest."""
    html: str
    text: str
    company_count: int
    company_keys: List[str]


class DigestBuilder:
    """
    Builds weekly digest emails.

    Features:
    - Direct SignalStore access (no HTTP)
    - Anti-spam: excludes companies digested in last 7 days
    - Per-recipient magic link tokens
    - Confidence-based company sorting
    """

    def __init__(self, config: DistributionConfig):
        self.config = config
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )

    async def build_weekly_digest(
        self,
        store: SignalStore,
        recipient: str,
        recipient_name: Optional[str] = None,
    ) -> DigestResult:
        """
        Build a weekly digest for a recipient.

        Args:
            store: SignalStore instance (must be initialized)
            recipient: Recipient email address
            recipient_name: Optional display name for greeting

        Returns:
            DigestResult with HTML, text, and company list
        """
        # Get eligible companies (anti-spam filtered)
        companies = await self._get_eligible_companies(store, recipient)

        # Generate magic link tokens and prepare template data
        digest_companies = await self._prepare_companies(store, companies, recipient)

        # Get stats for the digest
        stats = await self._get_digest_stats(store)

        # Render templates
        template_data = {
            "recipient_name": recipient_name,
            "digest_date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
            "companies": [self._company_to_dict(c) for c in digest_companies],
            "stats": stats,
            "unsubscribe_url": None,  # TODO: Implement unsubscribe
            "preferences_url": None,  # TODO: Implement preferences
        }

        html = self._render_html(template_data)
        text = self._render_text(template_data)

        return DigestResult(
            html=html,
            text=text,
            company_count=len(digest_companies),
            company_keys=[c.canonical_key for c in digest_companies],
        )

    async def _get_eligible_companies(
        self,
        store: SignalStore,
        recipient: str,
    ) -> List[dict]:
        """
        Get companies eligible for digest (anti-spam filtered).

        Eligibility rules:
        - Status is 'inbox' (or no explicit status)
        - First seen within lookback period
        - NOT already sent to this recipient in last 7 days
        """
        # Get inbox companies
        inbox_companies = await store.get_inbox_companies(
            status="inbox",
            min_confidence=0.0,
            limit=100,  # Get more, then filter
        )

        if not inbox_companies:
            return []

        # Get companies already digested by this recipient
        already_digested = await self._get_recently_digested(store, recipient)

        # Filter out recently digested companies
        eligible = []
        cutoff_date = datetime.now(timezone.utc) - timedelta(
            days=self.config.digest_lookback_days
        )

        for company in inbox_companies:
            # Skip if already digested
            if company.canonical_key in already_digested:
                logger.debug(f"Skipping {company.canonical_key}: already digested")
                continue

            # Skip if too old (no recent signals)
            if company.first_seen < cutoff_date and company.last_seen < cutoff_date:
                logger.debug(f"Skipping {company.canonical_key}: no recent signals")
                continue

            eligible.append({
                "canonical_key": company.canonical_key,
                "company_name": company.company_name,
                "max_confidence": company.max_confidence,
                "sources": company.sources,
                "first_seen": company.first_seen,
                "last_seen": company.last_seen,
            })

        # Sort by confidence (highest first) and limit
        eligible.sort(key=lambda x: x["max_confidence"], reverse=True)
        eligible = eligible[:self.config.max_companies_per_digest]

        logger.info(
            f"Found {len(eligible)} eligible companies for {recipient} "
            f"(filtered {len(inbox_companies) - len(eligible)})"
        )

        return eligible

    async def _get_recently_digested(
        self,
        store: SignalStore,
        recipient: str,
    ) -> set:
        """
        Get canonical keys of companies already digested by this recipient.

        Queries company_actions for 'digest_sent' actions by this recipient
        in the last 7 days.
        """
        digested_keys = set()

        # Query all recent digest_sent actions
        # We need to query the database directly since there's no specific method
        if not store._db:
            return digested_keys

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(
                days=self.config.digest_lookback_days
            )
            cutoff_str = cutoff.isoformat()

            cursor = await store._db.execute(
                """
                SELECT DISTINCT canonical_key
                FROM company_actions
                WHERE action = 'digest_sent'
                  AND actor = ?
                  AND occurred_at > ?
                """,
                (recipient, cutoff_str),
            )

            rows = await cursor.fetchall()
            digested_keys = {row[0] for row in rows}

            logger.debug(
                f"Found {len(digested_keys)} companies already digested by {recipient}"
            )

        except Exception as e:
            logger.warning(f"Error querying digested companies: {e}")

        return digested_keys

    async def _prepare_companies(
        self,
        store: SignalStore,
        companies: List[dict],
        recipient: str,
    ) -> List[DigestCompany]:
        """
        Prepare companies for template with magic link tokens.
        """
        prepared = []

        for company in companies:
            canonical_key = company["canonical_key"]

            # Get additional company details
            details = await store.get_company_by_key(canonical_key)
            one_liner = None
            website = None

            if details:
                one_liner = details.get("one_liner")
                website = details.get("website")

            # Generate magic link tokens
            track_token = await create_action_token(
                store=store,
                canonical_key=canonical_key,
                action="track",
                expires_in_days=self.config.token_expiry_days,
            )

            pass_token = await create_action_token(
                store=store,
                canonical_key=canonical_key,
                action="pass",
                expires_in_days=self.config.token_expiry_days,
            )

            # Build URLs
            track_url = create_magic_link_url(
                self.config.public_api_base_url,
                track_token,
            )

            pass_url = create_magic_link_url(
                self.config.public_api_base_url,
                pass_token,
            )

            details_url = (
                f"{self.config.public_profile_base_url}"
                f"/api/v1/companies/{canonical_key}/public"
            )

            # Calculate confidence display
            confidence = company["max_confidence"]
            confidence_pct = int(confidence * 100)
            confidence_color = self._get_confidence_color(confidence)

            prepared.append(DigestCompany(
                canonical_key=canonical_key,
                name=company["company_name"] or canonical_key,
                website=website,
                one_liner=one_liner,
                confidence_pct=confidence_pct,
                confidence_color=confidence_color,
                sources=company["sources"] or "",
                track_url=track_url,
                pass_url=pass_url,
                details_url=details_url,
            ))

        return prepared

    async def _get_digest_stats(self, store: SignalStore) -> Optional[dict]:
        """Get stats for the digest footer."""
        try:
            stats = await store.get_stats()
            # Get unique source count
            source_count = len(set(
                s.strip()
                for s in stats.get("sources_breakdown", "").split(",")
                if s.strip()
            )) or 0

            return {
                "new_signals": stats.get("signals_this_week", stats.get("total_signals", 0)),
                "source_count": source_count or 8,  # Fallback
            }
        except Exception as e:
            logger.warning(f"Error getting digest stats: {e}")
            return None

    def _get_confidence_color(self, confidence: float) -> str:
        """Get color for confidence badge."""
        if confidence >= 0.7:
            return "#10B981"  # Green
        elif confidence >= 0.4:
            return "#F59E0B"  # Amber
        else:
            return "#6B7280"  # Gray

    def _company_to_dict(self, company: DigestCompany) -> dict:
        """Convert DigestCompany to template dict."""
        return {
            "name": company.name,
            "website": company.website,
            "one_liner": company.one_liner,
            "confidence_pct": company.confidence_pct,
            "confidence_color": company.confidence_color,
            "sources": company.sources,
            "track_url": company.track_url,
            "pass_url": company.pass_url,
            "details_url": company.details_url,
        }

    def _render_html(self, data: dict) -> str:
        """Render HTML template."""
        try:
            template = self._jinja_env.get_template("weekly_digest.html")
            return template.render(**data)
        except Exception as e:
            logger.error(f"Error rendering HTML template: {e}")
            raise

    def _render_text(self, data: dict) -> str:
        """Render plain text template."""
        try:
            template = self._jinja_env.get_template("weekly_digest.txt")
            return template.render(**data)
        except Exception as e:
            logger.error(f"Error rendering text template: {e}")
            raise


async def build_digest_for_recipient(
    store: SignalStore,
    config: DistributionConfig,
    recipient: str,
    recipient_name: Optional[str] = None,
) -> DigestResult:
    """
    Convenience function to build a digest for a recipient.

    Args:
        store: Initialized SignalStore
        config: Distribution configuration
        recipient: Recipient email
        recipient_name: Optional display name

    Returns:
        DigestResult with HTML and text content
    """
    builder = DigestBuilder(config)
    return await builder.build_weekly_digest(
        store=store,
        recipient=recipient,
        recipient_name=recipient_name,
    )


# Quick test when run directly
if __name__ == "__main__":
    import asyncio
    from distribution.config import load_config

    async def test_builder():
        config = load_config()
        store = SignalStore()
        await store.initialize()

        try:
            builder = DigestBuilder(config)
            result = await builder.build_weekly_digest(
                store=store,
                recipient="test@example.com",
                recipient_name="Test User",
            )

            print(f"Digest built successfully:")
            print(f"  Companies: {result.company_count}")
            print(f"  HTML length: {len(result.html)} chars")
            print(f"  Text length: {len(result.text)} chars")
            print(f"  Company keys: {result.company_keys[:5]}...")

        finally:
            await store.close()

    asyncio.run(test_builder())
