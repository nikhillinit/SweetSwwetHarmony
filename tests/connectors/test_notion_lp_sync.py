"""
Tests for NotionLPSync - LP database synchronization for relationship intelligence.

TDD: These tests are written FIRST, before implementation.
Run with: pytest tests/connectors/test_notion_lp_sync.py -v
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from connectors.notion_lp_sync import (
    NotionLPSync,
    LPRecord,
    LPStatus,
    FirmRelationship,
    # Constants
    NOTION_SCORE_DOCS_SIGNED,
    NOTION_SCORE_VERBAL,
    NOTION_SCORE_ENGAGED,
    NOTION_SCORE_IN_DB,
    LP_PROVIDER_BLOCKLIST,
)


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def mock_notion_transport():
    """Create a mock Notion transport."""
    transport = MagicMock()
    transport.get = AsyncMock()
    transport.post = AsyncMock()
    return transport


@pytest.fixture
def sample_lp_pages() -> List[Dict[str, Any]]:
    """Sample Notion LP pages for testing."""
    return [
        {
            "id": "page-1",
            "properties": {
                "Name": {"title": [{"text": {"content": "John Smith"}}]},
                "Firm": {"rich_text": [{"text": {"content": "Sequoia Capital"}}]},
                "Email": {"email": "john@sequoia.com"},
                "Website": {"url": "https://www.sequoia.com"},
                "Status": {"select": {"name": "Docs Signed"}},
                "Last Updated": {"date": {"start": "2024-01-15"}},
            },
        },
        {
            "id": "page-2",
            "properties": {
                "Name": {"title": [{"text": {"content": "Jane Doe"}}]},
                "Firm": {"rich_text": [{"text": {"content": "a]16z"}}]},
                "Email": {"email": "jane@a16z.com"},
                "Website": {"url": "https://a16z.com"},
                "Status": {"select": {"name": "Verbal Confirm"}},
                "Last Updated": {"date": {"start": "2024-01-10"}},
            },
        },
        {
            "id": "page-3",
            "properties": {
                "Name": {"title": [{"text": {"content": "Bob Wilson"}}]},
                "Firm": {"rich_text": [{"text": {"content": "Accel"}}]},
                "Email": {"email": "bob@accel.com"},
                "Website": {"url": None},  # No website
                "Status": {"select": {"name": "Engagement Sent"}},
                "Last Updated": {"date": {"start": "2024-01-05"}},
            },
        },
    ]


@pytest.fixture
def sample_lp_pages_same_firm() -> List[Dict[str, Any]]:
    """Multiple LPs from the same firm for merge testing."""
    return [
        {
            "id": "page-seq-1",
            "properties": {
                "Name": {"title": [{"text": {"content": "Partner A"}}]},
                "Firm": {"rich_text": [{"text": {"content": "Sequoia Capital"}}]},
                "Email": {"email": "partnera@sequoia.com"},
                "Website": {"url": "https://www.sequoia.com"},
                "Status": {"select": {"name": "Docs Signed"}},
                "Last Updated": {"date": {"start": "2024-01-15"}},
            },
        },
        {
            "id": "page-seq-2",
            "properties": {
                "Name": {"title": [{"text": {"content": "Partner B"}}]},
                "Firm": {"rich_text": [{"text": {"content": "Sequoia Capital"}}]},
                "Email": {"email": "partnerb@sequoia.com"},
                "Website": {"url": "https://sequoia.com"},  # Slightly different URL
                "Status": {"select": {"name": "Verbal Confirm"}},
                "Last Updated": {"date": {"start": "2024-01-10"}},
            },
        },
        {
            "id": "page-seq-3",
            "properties": {
                "Name": {"title": [{"text": {"content": "Partner C"}}]},
                "Firm": {"rich_text": [{"text": {"content": "Sequoia"}}]},  # Slightly different name
                "Email": {"email": "partnerc@sequoia.com"},
                "Website": {"url": None},  # No website, uses email domain
                "Status": {"select": {"name": "In Database"}},
                "Last Updated": {"date": {"start": "2024-01-01"}},
            },
        },
    ]


# =============================================================================
# SCORING CONSTANTS TESTS
# =============================================================================

class TestScoringConstants:
    """Test that scoring constants match design spec."""

    def test_docs_signed_score(self):
        """Docs Signed score is 0.95."""
        assert NOTION_SCORE_DOCS_SIGNED == 0.95

    def test_verbal_confirm_score(self):
        """Verbal Confirm score is 0.70."""
        assert NOTION_SCORE_VERBAL == 0.70

    def test_engaged_score(self):
        """Engagement Sent score is 0.40."""
        assert NOTION_SCORE_ENGAGED == 0.40

    def test_in_db_score(self):
        """In Database score is 0.25."""
        assert NOTION_SCORE_IN_DB == 0.25


# =============================================================================
# LP STATUS ENUM TESTS
# =============================================================================

class TestLPStatus:
    """Test LP status enum and scoring."""

    def test_status_to_score_docs_signed(self):
        """Docs Signed maps to 0.95."""
        assert LPStatus.DOCS_SIGNED.score == 0.95

    def test_status_to_score_verbal(self):
        """Verbal Confirm maps to 0.70."""
        assert LPStatus.VERBAL_CONFIRM.score == 0.70

    def test_status_to_score_engaged(self):
        """Engagement Sent maps to 0.40."""
        assert LPStatus.ENGAGEMENT_SENT.score == 0.40

    def test_status_to_score_in_db(self):
        """In Database maps to 0.25."""
        assert LPStatus.IN_DATABASE.score == 0.25

    def test_status_to_score_declined(self):
        """Declined maps to 0.0."""
        assert LPStatus.DECLINED.score == 0.0

    def test_status_from_notion_string(self):
        """Can parse status from Notion string."""
        assert LPStatus.from_notion("Docs Signed") == LPStatus.DOCS_SIGNED
        assert LPStatus.from_notion("Verbal Confirm") == LPStatus.VERBAL_CONFIRM
        assert LPStatus.from_notion("Engagement Sent") == LPStatus.ENGAGEMENT_SENT
        assert LPStatus.from_notion("In Database") == LPStatus.IN_DATABASE
        assert LPStatus.from_notion("Declined") == LPStatus.DECLINED

    def test_status_from_unknown_defaults_to_in_db(self):
        """Unknown status defaults to In Database."""
        assert LPStatus.from_notion("Unknown Status") == LPStatus.IN_DATABASE
        assert LPStatus.from_notion("") == LPStatus.IN_DATABASE
        assert LPStatus.from_notion(None) == LPStatus.IN_DATABASE

    def test_status_tier_ordering(self):
        """Status tiers are properly ordered."""
        assert LPStatus.DOCS_SIGNED.score > LPStatus.VERBAL_CONFIRM.score
        assert LPStatus.VERBAL_CONFIRM.score > LPStatus.ENGAGEMENT_SENT.score
        assert LPStatus.ENGAGEMENT_SENT.score > LPStatus.IN_DATABASE.score
        assert LPStatus.IN_DATABASE.score > LPStatus.DECLINED.score


# =============================================================================
# LP RECORD TESTS
# =============================================================================

class TestLPRecord:
    """Test LP record data class."""

    def test_lp_record_creation(self):
        """Can create LP record with required fields."""
        record = LPRecord(
            notion_id="page-1",
            name="John Smith",
            firm="Sequoia Capital",
            email="john@sequoia.com",
            status=LPStatus.DOCS_SIGNED,
        )
        assert record.notion_id == "page-1"
        assert record.name == "John Smith"
        assert record.firm == "Sequoia Capital"
        assert record.status == LPStatus.DOCS_SIGNED
        assert record.score == 0.95

    def test_lp_record_domain_from_website(self):
        """Domain extracted from website."""
        record = LPRecord(
            notion_id="page-1",
            name="John Smith",
            firm="Sequoia",
            email="john@sequoia.com",
            status=LPStatus.DOCS_SIGNED,
            website="https://www.sequoia.com/team",
        )
        assert record.domain == "sequoia.com"

    def test_lp_record_domain_fallback_to_email(self):
        """Domain falls back to email when no website."""
        record = LPRecord(
            notion_id="page-1",
            name="John Smith",
            firm="Sequoia",
            email="john@sequoia.com",
            status=LPStatus.DOCS_SIGNED,
            website=None,
        )
        assert record.domain == "sequoia.com"

    def test_lp_record_no_domain_for_provider_email(self):
        """No domain extracted for provider emails (gmail, etc)."""
        record = LPRecord(
            notion_id="page-1",
            name="John Smith",
            firm="Angel Investor",
            email="john@gmail.com",
            status=LPStatus.DOCS_SIGNED,
            website=None,
        )
        assert record.domain is None

    def test_lp_record_score_property(self):
        """Score property returns status score."""
        record = LPRecord(
            notion_id="page-1",
            name="Test",
            firm="Test Firm",
            email="test@firm.com",
            status=LPStatus.VERBAL_CONFIRM,
        )
        assert record.score == 0.70


# =============================================================================
# PROVIDER BLOCKLIST TESTS
# =============================================================================

class TestProviderBlocklist:
    """Test provider blocklist for email domain extraction."""

    def test_blocklist_contains_common_providers(self):
        """Blocklist contains common email providers."""
        assert "gmail.com" in LP_PROVIDER_BLOCKLIST
        assert "googlemail.com" in LP_PROVIDER_BLOCKLIST
        assert "yahoo.com" in LP_PROVIDER_BLOCKLIST
        assert "outlook.com" in LP_PROVIDER_BLOCKLIST
        assert "hotmail.com" in LP_PROVIDER_BLOCKLIST
        assert "icloud.com" in LP_PROVIDER_BLOCKLIST
        assert "protonmail.com" in LP_PROVIDER_BLOCKLIST

    def test_blocklist_excludes_business_domains(self):
        """Blocklist does not contain business domains."""
        assert "sequoia.com" not in LP_PROVIDER_BLOCKLIST
        assert "a16z.com" not in LP_PROVIDER_BLOCKLIST
        assert "accel.com" not in LP_PROVIDER_BLOCKLIST


# =============================================================================
# DOMAIN EXTRACTION TESTS
# =============================================================================

class TestDomainExtraction:
    """Test domain extraction logic."""

    def test_extract_domain_from_website_url(self):
        """Extract domain from full URL."""
        sync = NotionLPSync.__new__(NotionLPSync)
        assert sync._extract_domain_from_website("https://www.sequoia.com/team") == "sequoia.com"
        assert sync._extract_domain_from_website("http://a16z.com") == "a16z.com"
        assert sync._extract_domain_from_website("https://WWW.ACCEL.COM/") == "accel.com"

    def test_extract_domain_strips_www(self):
        """Domain extraction strips www prefix."""
        sync = NotionLPSync.__new__(NotionLPSync)
        assert sync._extract_domain_from_website("https://www.example.com") == "example.com"
        assert sync._extract_domain_from_website("www.example.com") == "example.com"

    def test_extract_domain_from_email(self):
        """Extract domain from email address."""
        sync = NotionLPSync.__new__(NotionLPSync)
        assert sync._extract_domain_from_email("partner@sequoia.com") == "sequoia.com"
        assert sync._extract_domain_from_email("JOHN@A16Z.COM") == "a16z.com"

    def test_extract_domain_from_email_blocked(self):
        """Blocked email providers return None."""
        sync = NotionLPSync.__new__(NotionLPSync)
        assert sync._extract_domain_from_email("john@gmail.com") is None
        assert sync._extract_domain_from_email("jane@yahoo.com") is None
        assert sync._extract_domain_from_email("bob@outlook.com") is None

    def test_extract_domain_handles_none(self):
        """Handle None/empty inputs gracefully."""
        sync = NotionLPSync.__new__(NotionLPSync)
        assert sync._extract_domain_from_website(None) is None
        assert sync._extract_domain_from_website("") is None
        assert sync._extract_domain_from_email(None) is None
        assert sync._extract_domain_from_email("") is None


# =============================================================================
# FIRM RELATIONSHIP TESTS
# =============================================================================

class TestFirmRelationship:
    """Test firm relationship aggregation."""

    def test_firm_relationship_creation(self):
        """Can create firm relationship."""
        rel = FirmRelationship(
            domain="sequoia.com",
            score=0.95,
            status=LPStatus.DOCS_SIGNED,
            attribution="via John Smith",
            notion_lp_ids=["page-1"],
        )
        assert rel.domain == "sequoia.com"
        assert rel.score == 0.95
        assert rel.attribution == "via John Smith"

    def test_firm_relationship_badge_docs_signed(self):
        """Badge for Docs Signed status."""
        rel = FirmRelationship(
            domain="sequoia.com",
            score=0.95,
            status=LPStatus.DOCS_SIGNED,
            attribution="via John Smith",
            notion_lp_ids=["page-1"],
        )
        assert "LP - Docs Signed" in rel.badge

    def test_firm_relationship_badge_verbal(self):
        """Badge for Verbal Confirm status."""
        rel = FirmRelationship(
            domain="sequoia.com",
            score=0.70,
            status=LPStatus.VERBAL_CONFIRM,
            attribution="via John Smith",
            notion_lp_ids=["page-1"],
        )
        assert "LP - Verbal" in rel.badge

    def test_firm_relationship_badge_engaged(self):
        """Badge for Engagement Sent status."""
        rel = FirmRelationship(
            domain="accel.com",
            score=0.40,
            status=LPStatus.ENGAGEMENT_SENT,
            attribution="via Bob Wilson",
            notion_lp_ids=["page-1"],
        )
        assert "LP - Contacted" in rel.badge


# =============================================================================
# MULTI-LP MERGE TESTS
# =============================================================================

class TestMultiLPMerge:
    """Test merging multiple LPs from the same firm."""

    def test_merge_uses_highest_tier(self):
        """Merge uses highest tier status."""
        records = [
            LPRecord(
                notion_id="page-1",
                name="Partner A",
                firm="Sequoia",
                email="a@sequoia.com",
                status=LPStatus.DOCS_SIGNED,
                website="https://sequoia.com",
            ),
            LPRecord(
                notion_id="page-2",
                name="Partner B",
                firm="Sequoia",
                email="b@sequoia.com",
                status=LPStatus.VERBAL_CONFIRM,
                website="https://sequoia.com",
            ),
        ]

        sync = NotionLPSync.__new__(NotionLPSync)
        merged = sync._merge_lp_records(records)

        assert merged.score == 0.95  # Docs Signed (highest)
        assert merged.status == LPStatus.DOCS_SIGNED

    def test_merge_concatenates_attribution(self):
        """Merge concatenates unique names for attribution."""
        records = [
            LPRecord(
                notion_id="page-1",
                name="Willie Litvack",
                firm="Sequoia",
                email="willie@sequoia.com",
                status=LPStatus.DOCS_SIGNED,
                website="https://sequoia.com",
            ),
            LPRecord(
                notion_id="page-2",
                name="Sean Tolkin",
                firm="Sequoia",
                email="sean@sequoia.com",
                status=LPStatus.VERBAL_CONFIRM,
                website="https://sequoia.com",
            ),
        ]

        sync = NotionLPSync.__new__(NotionLPSync)
        merged = sync._merge_lp_records(records)

        assert "Willie Litvack" in merged.attribution
        assert "Sean Tolkin" in merged.attribution
        assert "via" in merged.attribution

    def test_merge_preserves_all_notion_ids(self):
        """Merge preserves all Notion page IDs for traceability."""
        records = [
            LPRecord(
                notion_id="page-1",
                name="Partner A",
                firm="Sequoia",
                email="a@sequoia.com",
                status=LPStatus.DOCS_SIGNED,
                website="https://sequoia.com",
            ),
            LPRecord(
                notion_id="page-2",
                name="Partner B",
                firm="Sequoia",
                email="b@sequoia.com",
                status=LPStatus.VERBAL_CONFIRM,
                website="https://sequoia.com",
            ),
        ]

        sync = NotionLPSync.__new__(NotionLPSync)
        merged = sync._merge_lp_records(records)

        assert "page-1" in merged.notion_lp_ids
        assert "page-2" in merged.notion_lp_ids

    def test_merge_uses_most_recent_update(self):
        """Merge uses most recent last_updated."""
        records = [
            LPRecord(
                notion_id="page-1",
                name="Partner A",
                firm="Sequoia",
                email="a@sequoia.com",
                status=LPStatus.ENGAGEMENT_SENT,
                website="https://sequoia.com",
                last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            LPRecord(
                notion_id="page-2",
                name="Partner B",
                firm="Sequoia",
                email="b@sequoia.com",
                status=LPStatus.DOCS_SIGNED,
                website="https://sequoia.com",
                last_updated=datetime(2024, 1, 15, tzinfo=timezone.utc),
            ),
        ]

        sync = NotionLPSync.__new__(NotionLPSync)
        merged = sync._merge_lp_records(records)

        assert merged.last_updated == datetime(2024, 1, 15, tzinfo=timezone.utc)


# =============================================================================
# NOTION LP SYNC TESTS
# =============================================================================

class TestNotionLPSync:
    """Test NotionLPSync main class."""

    @pytest.mark.asyncio
    async def test_sync_fetches_from_notion(self, mock_notion_transport, sample_lp_pages):
        """sync() fetches LP records from Notion."""
        mock_notion_transport.post.return_value = {
            "results": sample_lp_pages,
            "has_more": False,
        }

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        relationships = await sync.sync()

        # Should have called Notion API
        mock_notion_transport.post.assert_called()

        # Should return firm relationships
        assert len(relationships) > 0

    @pytest.mark.asyncio
    async def test_sync_groups_by_domain(self, mock_notion_transport, sample_lp_pages):
        """sync() groups LPs by domain."""
        mock_notion_transport.post.return_value = {
            "results": sample_lp_pages,
            "has_more": False,
        }

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        relationships = await sync.sync()
        domains = [r.domain for r in relationships]

        # Should have unique domains
        assert len(domains) == len(set(domains))

    @pytest.mark.asyncio
    async def test_sync_handles_pagination(self, mock_notion_transport, sample_lp_pages):
        """sync() handles Notion pagination."""
        # First page
        mock_notion_transport.post.side_effect = [
            {
                "results": sample_lp_pages[:2],
                "has_more": True,
                "next_cursor": "cursor-1",
            },
            {
                "results": sample_lp_pages[2:],
                "has_more": False,
            },
        ]

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        relationships = await sync.sync()

        # Should have called API twice
        assert mock_notion_transport.post.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_merges_same_firm(self, mock_notion_transport, sample_lp_pages_same_firm):
        """sync() merges multiple LPs from same firm."""
        mock_notion_transport.post.return_value = {
            "results": sample_lp_pages_same_firm,
            "has_more": False,
        }

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        relationships = await sync.sync()

        # Should merge to single firm relationship
        sequoia_rels = [r for r in relationships if r.domain == "sequoia.com"]
        assert len(sequoia_rels) == 1

        # Should use highest tier
        assert sequoia_rels[0].score == 0.95

        # Should have all three IDs
        assert len(sequoia_rels[0].notion_lp_ids) == 3

    @pytest.mark.asyncio
    async def test_sync_returns_individual_declined_candidates(self, mock_notion_transport):
        """sync() returns individual LP candidates for declined (not merged at firm level)."""
        pages = [
            {
                "id": "page-1",
                "properties": {
                    "Name": {"title": [{"text": {"content": "Good Partner"}}]},
                    "Firm": {"rich_text": [{"text": {"content": "Sequoia"}}]},
                    "Email": {"email": "good@sequoia.com"},
                    "Website": {"url": "https://sequoia.com"},
                    "Status": {"select": {"name": "Docs Signed"}},
                },
            },
            {
                "id": "page-2",
                "properties": {
                    "Name": {"title": [{"text": {"content": "Declined Partner"}}]},
                    "Firm": {"rich_text": [{"text": {"content": "Sequoia"}}]},
                    "Email": {"email": "declined@sequoia.com"},
                    "Website": {"url": "https://sequoia.com"},
                    "Status": {"select": {"name": "Declined"}},
                },
            },
        ]

        mock_notion_transport.post.return_value = {
            "results": pages,
            "has_more": False,
        }

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        relationships = await sync.sync()

        # Declined partner should not reduce firm score
        # (Design says: "Return individual LP candidates so a declined person
        # doesn't erase other partners at the same firm")
        sequoia_rels = [r for r in relationships if r.domain == "sequoia.com"]

        # Should still have high score from non-declined partner
        assert any(r.score >= 0.95 for r in sequoia_rels)


# =============================================================================
# RELATIONSHIP STORE INTEGRATION TESTS
# =============================================================================

class TestRelationshipStoreIntegration:
    """Test integration with RelationshipStore."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="upsert_lp_relationship will be added in Phase 4")
    async def test_save_to_relationship_store(self, mock_notion_transport, sample_lp_pages):
        """Can save LP relationships to RelationshipStore."""
        import tempfile
        from storage.relationship_store import RelationshipStore

        mock_notion_transport.post.return_value = {
            "results": sample_lp_pages,
            "has_more": False,
        }

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        relationships = await sync.sync()

        # Create temp DB
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            store = RelationshipStore(db_path)
            await store.initialize()

            # Save relationships
            for rel in relationships:
                await store.upsert_lp_relationship(
                    target_domain=rel.domain,
                    lp_status=rel.status.value,
                    lp_score=rel.score,
                    lp_names=[rel.attribution],
                    notion_lp_ids=rel.notion_lp_ids,
                )

            # Verify stored
            # (This assumes we add upsert_lp_relationship to RelationshipStore)

            await store.close()
        finally:
            os.unlink(db_path)


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_handles_missing_properties(self, mock_notion_transport):
        """Handles pages with missing properties gracefully."""
        pages = [
            {
                "id": "page-incomplete",
                "properties": {
                    "Name": {"title": []},  # Empty title
                    # Missing other properties
                },
            },
        ]

        mock_notion_transport.post.return_value = {
            "results": pages,
            "has_more": False,
        }

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        # Should not crash
        relationships = await sync.sync()

        # May return empty if all records are invalid
        assert isinstance(relationships, list)

    @pytest.mark.asyncio
    async def test_handles_api_error(self, mock_notion_transport):
        """Handles Notion API errors gracefully."""
        mock_notion_transport.post.side_effect = Exception("API Error")

        sync = NotionLPSync(
            api_key="test-key",
            database_id="test-db-id",
            transport=mock_notion_transport,
        )

        with pytest.raises(Exception):
            await sync.sync()
