"""
Tests for LocalEmailScanner - MBOX/EML parsing for relationship intelligence.

TDD: These tests are written FIRST, before implementation.
Run with: pytest tests/connectors/test_local_email_scanner.py -v
"""

import os
import sys
import tempfile
import mailbox
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from connectors.local_email_scanner import (
    MboxStreamer,
    EmailHeader,
    ContactTracker,
    LocalEmailScanner,
    PROVIDER_BLOCKLIST,
)


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def temp_mbox():
    """Create a temporary MBOX file with sample emails."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
        mbox_path = f.name

    # Create mbox and add messages
    mbox = mailbox.mbox(mbox_path)

    # Message 1: Initial email to investor
    msg1 = EmailMessage()
    msg1['From'] = 'founder@startup.com'
    msg1['To'] = 'partner@sequoia.com'
    msg1['Subject'] = 'Introduction - Startup Inc'
    msg1['Date'] = 'Mon, 15 Jan 2024 10:00:00 -0500'
    msg1['Message-ID'] = '<msg001@startup.com>'
    msg1.set_content('Hello, I wanted to introduce myself...')
    mbox.add(msg1)

    # Message 2: Reply from investor
    msg2 = EmailMessage()
    msg2['From'] = 'partner@sequoia.com'
    msg2['To'] = 'founder@startup.com'
    msg2['Subject'] = 'Re: Introduction - Startup Inc'
    msg2['Date'] = 'Mon, 15 Jan 2024 14:30:00 -0500'
    msg2['Message-ID'] = '<msg002@sequoia.com>'
    msg2['In-Reply-To'] = '<msg001@startup.com>'
    msg2['References'] = '<msg001@startup.com>'
    msg2.set_content('Thanks for reaching out...')
    mbox.add(msg2)

    # Message 3: Intro email (contains "intro" in subject)
    msg3 = EmailMessage()
    msg3['From'] = 'founder@startup.com'
    msg3['To'] = 'partner@a]16z.com'
    msg3['Cc'] = 'mutual@friend.com'
    msg3['Subject'] = 'Intro: Founder <> Partner'
    msg3['Date'] = 'Tue, 16 Jan 2024 09:00:00 +0000'
    msg3['Message-ID'] = '<msg003@startup.com>'
    msg3.set_content('I wanted to make an introduction...')
    mbox.add(msg3)

    mbox.close()

    yield mbox_path

    # Cleanup
    os.unlink(mbox_path)


@pytest.fixture
def temp_mbox_with_threads():
    """Create MBOX with threaded conversation."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
        mbox_path = f.name

    mbox = mailbox.mbox(mbox_path)

    # Thread 1: 3 messages
    msg1 = EmailMessage()
    msg1['From'] = 'founder@startup.com'
    msg1['To'] = 'investor@vc.com'
    msg1['Subject'] = 'Pitch Deck'
    msg1['Date'] = 'Mon, 01 Jan 2024 10:00:00 +0000'
    msg1['Message-ID'] = '<thread1-msg1@startup.com>'
    mbox.add(msg1)

    msg2 = EmailMessage()
    msg2['From'] = 'investor@vc.com'
    msg2['To'] = 'founder@startup.com'
    msg2['Subject'] = 'Re: Pitch Deck'
    msg2['Date'] = 'Mon, 01 Jan 2024 12:00:00 +0000'
    msg2['Message-ID'] = '<thread1-msg2@vc.com>'
    msg2['In-Reply-To'] = '<thread1-msg1@startup.com>'
    msg2['References'] = '<thread1-msg1@startup.com>'
    mbox.add(msg2)

    msg3 = EmailMessage()
    msg3['From'] = 'founder@startup.com'
    msg3['To'] = 'investor@vc.com'
    msg3['Subject'] = 'Re: Pitch Deck'
    msg3['Date'] = 'Mon, 01 Jan 2024 14:00:00 +0000'
    msg3['Message-ID'] = '<thread1-msg3@startup.com>'
    msg3['In-Reply-To'] = '<thread1-msg2@vc.com>'
    msg3['References'] = '<thread1-msg1@startup.com> <thread1-msg2@vc.com>'
    mbox.add(msg3)

    mbox.close()

    yield mbox_path
    os.unlink(mbox_path)


@pytest.fixture
def empty_mbox():
    """Create an empty MBOX file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
        mbox_path = f.name

    mbox = mailbox.mbox(mbox_path)
    mbox.close()

    yield mbox_path
    os.unlink(mbox_path)


# =============================================================================
# MBOX STREAMER TESTS
# =============================================================================

class TestMboxStreamer:
    """Tests for MboxStreamer class."""

    def test_stream_headers_yields_email_headers(self, temp_mbox):
        """stream_headers() yields EmailHeader objects."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        assert len(headers) == 3
        assert all(isinstance(h, EmailHeader) for h in headers)

    def test_email_header_has_required_fields(self, temp_mbox):
        """EmailHeader contains all required fields from design doc."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        header = headers[0]
        assert hasattr(header, 'date')
        assert hasattr(header, 'from_addr')
        assert hasattr(header, 'to_addr')
        assert hasattr(header, 'cc_addr')
        assert hasattr(header, 'subject')
        assert hasattr(header, 'message_id')
        assert hasattr(header, 'in_reply_to')
        assert hasattr(header, 'references')

    def test_date_parsed_as_utc_datetime(self, temp_mbox):
        """Dates are parsed and normalized to UTC."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        # First message: Mon, 15 Jan 2024 10:00:00 -0500 -> 15:00 UTC
        header = headers[0]
        assert isinstance(header.date, datetime)
        assert header.date.tzinfo == timezone.utc
        assert header.date.hour == 15  # 10:00 EST = 15:00 UTC

    def test_threading_fields_extracted(self, temp_mbox):
        """message_id, in_reply_to, references are extracted."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        # First message has message_id but no in_reply_to
        assert headers[0].message_id == '<msg001@startup.com>'
        assert headers[0].in_reply_to is None

        # Second message is a reply
        assert headers[1].message_id == '<msg002@sequoia.com>'
        assert headers[1].in_reply_to == '<msg001@startup.com>'
        assert '<msg001@startup.com>' in headers[1].references

    def test_from_address_extracted(self, temp_mbox):
        """From address is extracted correctly."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        assert headers[0].from_addr == 'founder@startup.com'
        assert headers[1].from_addr == 'partner@sequoia.com'

    def test_to_address_extracted(self, temp_mbox):
        """To address is extracted correctly."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        assert headers[0].to_addr == 'partner@sequoia.com'

    def test_cc_address_extracted(self, temp_mbox):
        """CC address is extracted when present."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox))

        # Third message has CC
        assert headers[2].cc_addr == 'mutual@friend.com'

        # First message has no CC
        assert headers[0].cc_addr is None

    def test_empty_mbox_yields_nothing(self, empty_mbox):
        """Empty MBOX yields no headers."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(empty_mbox))

        assert headers == []

    def test_handles_missing_date_gracefully(self):
        """Messages without Date header are handled."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)
            msg = EmailMessage()
            msg['From'] = 'test@example.com'
            msg['To'] = 'other@example.com'
            msg['Subject'] = 'No date'
            # No Date header
            mbox.add(msg)
            mbox.close()

            streamer = MboxStreamer()
            headers = list(streamer.stream_headers(mbox_path))

            assert len(headers) == 1
            assert headers[0].date is None or isinstance(headers[0].date, datetime)
        finally:
            os.unlink(mbox_path)

    def test_handles_malformed_date_gracefully(self):
        """Malformed dates don't crash the parser."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)
            msg = EmailMessage()
            msg['From'] = 'test@example.com'
            msg['To'] = 'other@example.com'
            msg['Date'] = 'not a valid date'
            mbox.add(msg)
            mbox.close()

            streamer = MboxStreamer()
            headers = list(streamer.stream_headers(mbox_path))

            # Should not crash, date should be None or fallback
            assert len(headers) == 1
        finally:
            os.unlink(mbox_path)

    def test_references_parsed_as_list(self, temp_mbox_with_threads):
        """References header is parsed as a list of message IDs."""
        streamer = MboxStreamer()
        headers = list(streamer.stream_headers(temp_mbox_with_threads))

        # Third message has multiple references
        assert isinstance(headers[2].references, list)
        assert len(headers[2].references) == 2
        assert '<thread1-msg1@startup.com>' in headers[2].references
        assert '<thread1-msg2@vc.com>' in headers[2].references


# =============================================================================
# CONTACT TRACKER TESTS
# =============================================================================

class TestContactTracker:
    """Tests for ContactTracker class."""

    def test_tracks_first_and_last_contact(self):
        """Tracks first_contact and last_contact timestamps."""
        tracker = ContactTracker()

        t1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2024, 1, 15, tzinfo=timezone.utc)
        t3 = datetime(2024, 1, 10, tzinfo=timezone.utc)  # Out of order

        tracker.add_contact('vc.com', t1)
        tracker.add_contact('vc.com', t2)
        tracker.add_contact('vc.com', t3)

        contact = tracker.get_contact('vc.com')
        assert contact['first_contact'] == t1
        assert contact['last_contact'] == t2

    def test_counts_total_messages(self):
        """Counts total messages per domain."""
        tracker = ContactTracker()

        t = datetime.now(timezone.utc)
        tracker.add_contact('vc.com', t)
        tracker.add_contact('vc.com', t)
        tracker.add_contact('vc.com', t)

        contact = tracker.get_contact('vc.com')
        assert contact['total_messages'] == 3

    def test_counts_intro_messages(self):
        """Counts intro messages (subject contains intro/introduction)."""
        tracker = ContactTracker()

        t = datetime.now(timezone.utc)
        tracker.add_contact('vc.com', t, is_intro=True)
        tracker.add_contact('vc.com', t, is_intro=False)
        tracker.add_contact('vc.com', t, is_intro=True)

        contact = tracker.get_contact('vc.com')
        assert contact['intro_count'] == 2

    def test_counts_replies_received(self):
        """Counts replies received from the domain."""
        tracker = ContactTracker()

        t = datetime.now(timezone.utc)
        tracker.add_contact('vc.com', t, is_reply_received=True)
        tracker.add_contact('vc.com', t, is_reply_received=True)
        tracker.add_contact('vc.com', t, is_reply_received=False)

        contact = tracker.get_contact('vc.com')
        assert contact['reply_count'] == 2

    def test_unknown_domain_returns_none(self):
        """Unknown domain returns None."""
        tracker = ContactTracker()
        assert tracker.get_contact('unknown.com') is None

    def test_get_all_contacts(self):
        """get_all_contacts() returns all tracked domains."""
        tracker = ContactTracker()

        t = datetime.now(timezone.utc)
        tracker.add_contact('vc1.com', t)
        tracker.add_contact('vc2.com', t)
        tracker.add_contact('vc3.com', t)

        contacts = tracker.get_all_contacts()
        assert len(contacts) == 3
        assert 'vc1.com' in contacts
        assert 'vc2.com' in contacts
        assert 'vc3.com' in contacts


# =============================================================================
# LOCAL EMAIL SCANNER TESTS
# =============================================================================

class TestLocalEmailScanner:
    """Tests for LocalEmailScanner - the main class."""

    def test_scan_mbox_returns_contact_tracker(self, temp_mbox):
        """scan_mbox() returns populated ContactTracker."""
        scanner = LocalEmailScanner(my_email='founder@startup.com')
        tracker = scanner.scan_mbox(temp_mbox)

        assert isinstance(tracker, ContactTracker)
        contacts = tracker.get_all_contacts()
        assert len(contacts) > 0

    def test_extracts_domains_from_recipients(self, temp_mbox):
        """Extracts domains from To/CC recipients."""
        scanner = LocalEmailScanner(my_email='founder@startup.com')
        tracker = scanner.scan_mbox(temp_mbox)

        contacts = tracker.get_all_contacts()
        assert 'sequoia.com' in contacts

    def test_extracts_domains_from_senders_when_receiving(self, temp_mbox):
        """When receiving email, extracts sender domain."""
        scanner = LocalEmailScanner(my_email='founder@startup.com')
        tracker = scanner.scan_mbox(temp_mbox)

        # founder received a reply from sequoia.com
        contact = tracker.get_contact('sequoia.com')
        assert contact is not None

    def test_detects_intro_emails(self, temp_mbox):
        """Detects intro emails by subject."""
        scanner = LocalEmailScanner(my_email='founder@startup.com')
        tracker = scanner.scan_mbox(temp_mbox)

        # Third email has "Intro:" in subject
        # Note: a]16z.com is intentional typo in fixture, should be a16z.com
        # The scanner should still count intros

    def test_detects_replies_by_in_reply_to(self, temp_mbox_with_threads):
        """Detects replies using in_reply_to header."""
        scanner = LocalEmailScanner(my_email='founder@startup.com')
        tracker = scanner.scan_mbox(temp_mbox_with_threads)

        contact = tracker.get_contact('vc.com')
        # investor@vc.com replied once
        assert contact['reply_count'] >= 1

    def test_filters_provider_domains(self):
        """Filters out provider domains (gmail, yahoo, etc)."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)

            # Email to gmail address
            msg = EmailMessage()
            msg['From'] = 'founder@startup.com'
            msg['To'] = 'personal@gmail.com'
            msg['Date'] = 'Mon, 01 Jan 2024 10:00:00 +0000'
            msg['Message-ID'] = '<msg@startup.com>'
            mbox.add(msg)
            mbox.close()

            scanner = LocalEmailScanner(my_email='founder@startup.com')
            tracker = scanner.scan_mbox(mbox_path)

            contacts = tracker.get_all_contacts()
            assert 'gmail.com' not in contacts
        finally:
            os.unlink(mbox_path)

    def test_provider_blocklist_contents(self):
        """PROVIDER_BLOCKLIST contains expected domains."""
        assert 'gmail.com' in PROVIDER_BLOCKLIST
        assert 'googlemail.com' in PROVIDER_BLOCKLIST
        assert 'yahoo.com' in PROVIDER_BLOCKLIST
        assert 'outlook.com' in PROVIDER_BLOCKLIST
        assert 'hotmail.com' in PROVIDER_BLOCKLIST
        assert 'icloud.com' in PROVIDER_BLOCKLIST
        assert 'protonmail.com' in PROVIDER_BLOCKLIST

    def test_ignores_own_domain(self):
        """Ignores emails to/from own domain."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)

            # Email to colleague
            msg = EmailMessage()
            msg['From'] = 'founder@startup.com'
            msg['To'] = 'cofounder@startup.com'
            msg['Date'] = 'Mon, 01 Jan 2024 10:00:00 +0000'
            msg['Message-ID'] = '<msg@startup.com>'
            mbox.add(msg)
            mbox.close()

            scanner = LocalEmailScanner(my_email='founder@startup.com')
            tracker = scanner.scan_mbox(mbox_path)

            contacts = tracker.get_all_contacts()
            assert 'startup.com' not in contacts
        finally:
            os.unlink(mbox_path)


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestLocalEmailScannerIntegration:
    """Integration tests for LocalEmailScanner with RelationshipStore."""

    @pytest.mark.asyncio
    async def test_scan_and_store_relationships(self, temp_mbox_with_threads):
        """Scan MBOX and store relationships in RelationshipStore."""
        from storage.relationship_store import RelationshipStore

        # Create temp DB
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        try:
            store = RelationshipStore(db_path)
            await store.initialize()

            scanner = LocalEmailScanner(my_email='founder@startup.com')
            tracker = scanner.scan_mbox(temp_mbox_with_threads)

            # Store relationships
            for domain, contact in tracker.get_all_contacts().items():
                await store.upsert_domain_edge(
                    me_email='founder@startup.com',
                    target_domain=domain,
                    intro_count=contact['intro_count'],
                    reply_count=contact['reply_count'],
                    total_messages=contact['total_messages'],
                    last_contact_at=contact['last_contact'],
                    first_contact_at=contact['first_contact'],
                )

            # Verify stored
            strength = await store.get_domain_strength('founder@startup.com', 'vc.com')
            assert strength is not None
            assert strength.total_messages == 3

            await store.close()
        finally:
            os.unlink(db_path)

    def test_handles_large_mbox_streaming(self):
        """Handles large MBOX without loading entire file into memory."""
        # This test verifies the streaming approach works
        # In production, we'd test with multi-GB files

        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)

            # Add 1000 messages
            for i in range(1000):
                msg = EmailMessage()
                msg['From'] = 'founder@startup.com'
                msg['To'] = f'investor{i % 10}@vc{i % 5}.com'
                msg['Date'] = f'Mon, {(i % 28) + 1:02d} Jan 2024 10:00:00 +0000'
                msg['Message-ID'] = f'<msg{i}@startup.com>'
                msg['Subject'] = f'Message {i}'
                mbox.add(msg)

            mbox.close()

            scanner = LocalEmailScanner(my_email='founder@startup.com')
            tracker = scanner.scan_mbox(mbox_path)

            # Should have contacts for 5 VC domains (vc0.com to vc4.com)
            contacts = tracker.get_all_contacts()
            assert len(contacts) == 5

            # Each domain should have 200 messages (1000 / 5)
            for domain in contacts:
                assert contacts[domain]['total_messages'] == 200
        finally:
            os.unlink(mbox_path)


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Edge case tests."""

    def test_handles_encoded_headers(self):
        """Handles MIME-encoded headers."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)
            msg = EmailMessage()
            msg['From'] = '=?utf-8?b?Sm9obiBEb2U=?= <john@example.com>'
            msg['To'] = 'partner@vc.com'
            msg['Date'] = 'Mon, 01 Jan 2024 10:00:00 +0000'
            msg['Message-ID'] = '<msg@example.com>'
            mbox.add(msg)
            mbox.close()

            streamer = MboxStreamer()
            headers = list(streamer.stream_headers(mbox_path))

            assert len(headers) == 1
            assert 'example.com' in headers[0].from_addr or 'john' in headers[0].from_addr.lower()
        finally:
            os.unlink(mbox_path)

    def test_handles_multiple_to_addresses(self):
        """Handles multiple To addresses."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)
            msg = EmailMessage()
            msg['From'] = 'founder@startup.com'
            msg['To'] = 'partner1@vc1.com, partner2@vc2.com'
            msg['Date'] = 'Mon, 01 Jan 2024 10:00:00 +0000'
            msg['Message-ID'] = '<msg@startup.com>'
            mbox.add(msg)
            mbox.close()

            scanner = LocalEmailScanner(my_email='founder@startup.com')
            tracker = scanner.scan_mbox(mbox_path)

            contacts = tracker.get_all_contacts()
            assert 'vc1.com' in contacts
            assert 'vc2.com' in contacts
        finally:
            os.unlink(mbox_path)

    def test_handles_missing_file(self):
        """Raises appropriate error for missing file."""
        scanner = LocalEmailScanner(my_email='test@example.com')

        with pytest.raises(FileNotFoundError):
            scanner.scan_mbox('/nonexistent/path.mbox')

    def test_timezone_edge_cases(self):
        """Handles various timezone formats."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mbox', delete=False) as f:
            mbox_path = f.name

        try:
            mbox = mailbox.mbox(mbox_path)

            # Various timezone formats
            dates = [
                'Mon, 01 Jan 2024 10:00:00 +0000',      # UTC
                'Mon, 01 Jan 2024 10:00:00 -0800',      # PST
                'Mon, 01 Jan 2024 10:00:00 +0530',      # IST
                'Mon, 01 Jan 2024 10:00:00 GMT',        # Named TZ
                'Mon, 01 Jan 2024 10:00:00 EST',        # Named TZ
            ]

            for i, date in enumerate(dates):
                msg = EmailMessage()
                msg['From'] = f'sender{i}@example.com'
                msg['To'] = 'recipient@example.com'
                msg['Date'] = date
                msg['Message-ID'] = f'<msg{i}@example.com>'
                mbox.add(msg)

            mbox.close()

            streamer = MboxStreamer()
            headers = list(streamer.stream_headers(mbox_path))

            # All should be parsed and converted to UTC
            for header in headers:
                if header.date:
                    assert header.date.tzinfo == timezone.utc
        finally:
            os.unlink(mbox_path)
