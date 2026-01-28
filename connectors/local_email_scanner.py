"""
LocalEmailScanner - Streaming MBOX/EML parser for relationship intelligence.

Extracts email headers from Gmail Takeout exports without loading entire
file into memory. Designed for privacy-first local processing.

Key features:
- Streaming parser (handles multi-GB MBOX files)
- Timezone normalization (all dates converted to UTC)
- Threading detection (message_id, in_reply_to, references)
- Provider filtering (ignores gmail.com, yahoo.com, etc.)

Usage:
    scanner = LocalEmailScanner(my_email='founder@startup.com')
    tracker = scanner.scan_mbox('takeout.mbox')

    for domain, contact in tracker.get_all_contacts().items():
        print(f"{domain}: {contact['total_messages']} messages")
"""

from __future__ import annotations

import logging
import mailbox
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime, getaddresses
from pathlib import Path
from typing import Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

PROVIDER_BLOCKLIST = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
}

# Patterns for detecting intro emails
INTRO_PATTERNS = [
    re.compile(r'\bintro\b', re.IGNORECASE),
    re.compile(r'\bintroduction\b', re.IGNORECASE),
    re.compile(r'\bintroducing\b', re.IGNORECASE),
]


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class EmailHeader:
    """
    Parsed email header with normalized fields.

    All dates are normalized to UTC timezone.
    """
    date: Optional[datetime]
    from_addr: str
    to_addr: Optional[str]
    cc_addr: Optional[str]
    subject: Optional[str]
    message_id: Optional[str]
    in_reply_to: Optional[str]
    references: List[str] = field(default_factory=list)


@dataclass
class ContactStats:
    """Statistics for a domain contact."""
    first_contact: datetime
    last_contact: datetime
    total_messages: int = 0
    intro_count: int = 0
    reply_count: int = 0


# =============================================================================
# MBOX STREAMER
# =============================================================================

class MboxStreamer:
    """
    Streaming MBOX parser.

    Iterates through messages without loading entire file into memory.
    Yields EmailHeader objects with normalized fields.
    """

    def stream_headers(self, mbox_path: str) -> Generator[EmailHeader, None, None]:
        """
        Stream email headers from an MBOX file.

        Args:
            mbox_path: Path to the MBOX file

        Yields:
            EmailHeader objects with parsed fields
        """
        if not Path(mbox_path).exists():
            raise FileNotFoundError(f"MBOX file not found: {mbox_path}")

        box = mailbox.mbox(mbox_path)
        try:
            for message in box:
                try:
                    header = self._parse_message(message)
                    if header:
                        yield header
                except Exception as e:
                    logger.warning(f"Skipped malformed message: {e}")
        finally:
            box.close()

    def _parse_message(self, message) -> Optional[EmailHeader]:
        """Parse a single message into an EmailHeader."""
        # Parse date with timezone normalization
        date = self._parse_date(message.get('date'))

        # Parse From address (single)
        from_addr = self._parse_address(message.get('from', ''))

        # Keep raw To/CC for multiple address handling
        to_addr = message.get('to')
        cc_addr = message.get('cc')

        # Parse threading fields
        message_id = message.get('message-id')
        in_reply_to = message.get('in-reply-to')
        references = self._parse_references(message.get('references'))

        # Parse subject
        subject = self._decode_header(message.get('subject'))

        return EmailHeader(
            date=date,
            from_addr=from_addr,
            to_addr=to_addr,
            cc_addr=cc_addr,
            subject=subject,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
        )

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Parse date string and normalize to UTC.

        Handles various timezone formats including named timezones.
        """
        if not date_str:
            return None

        try:
            dt = parsedate_to_datetime(date_str)

            # Normalize to UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)

            return dt
        except Exception as e:
            logger.debug(f"Failed to parse date '{date_str}': {e}")
            return None

    def _parse_address(self, addr_str: Optional[str]) -> Optional[str]:
        """Extract email address from header value."""
        if not addr_str:
            return None

        # Decode MIME-encoded headers
        decoded = self._decode_header(addr_str)

        # Parse the address
        _, email = parseaddr(decoded)
        return email.lower() if email else None

    def _decode_header(self, header_value: Optional[str]) -> Optional[str]:
        """Decode MIME-encoded header value."""
        if not header_value:
            return None

        try:
            decoded_parts = decode_header(header_value)
            result = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result.append(part.decode(charset or 'utf-8', errors='replace'))
                else:
                    result.append(part)
            return ''.join(result)
        except Exception:
            return header_value

    def _parse_references(self, refs_str: Optional[str]) -> List[str]:
        """Parse References header into list of message IDs."""
        if not refs_str:
            return []

        # References are space-separated message IDs
        # Format: <id1@domain> <id2@domain> ...
        refs = []
        for match in re.finditer(r'<[^>]+>', refs_str):
            refs.append(match.group(0))
        return refs


# =============================================================================
# CONTACT TRACKER
# =============================================================================

class ContactTracker:
    """
    Tracks contact statistics per domain.

    Maintains first_contact, last_contact, message counts, etc.
    """

    def __init__(self):
        self._contacts: Dict[str, ContactStats] = {}

    def add_contact(
        self,
        domain: str,
        timestamp: datetime,
        is_intro: bool = False,
        is_reply_received: bool = False,
    ) -> None:
        """
        Add a contact event for a domain.

        Args:
            domain: The domain (e.g., 'sequoia.com')
            timestamp: When the contact occurred
            is_intro: Whether this was an intro email
            is_reply_received: Whether this was a reply we received
        """
        if domain not in self._contacts:
            self._contacts[domain] = ContactStats(
                first_contact=timestamp,
                last_contact=timestamp,
            )

        contact = self._contacts[domain]

        # Update timestamps
        if timestamp < contact.first_contact:
            contact.first_contact = timestamp
        if timestamp > contact.last_contact:
            contact.last_contact = timestamp

        # Update counts
        contact.total_messages += 1
        if is_intro:
            contact.intro_count += 1
        if is_reply_received:
            contact.reply_count += 1

    def get_contact(self, domain: str) -> Optional[Dict]:
        """
        Get contact statistics for a domain.

        Returns:
            Dict with contact stats, or None if domain not tracked
        """
        if domain not in self._contacts:
            return None

        contact = self._contacts[domain]
        return {
            'first_contact': contact.first_contact,
            'last_contact': contact.last_contact,
            'total_messages': contact.total_messages,
            'intro_count': contact.intro_count,
            'reply_count': contact.reply_count,
        }

    def get_all_contacts(self) -> Dict[str, Dict]:
        """Get all tracked contacts."""
        return {
            domain: self.get_contact(domain)
            for domain in self._contacts
        }


# =============================================================================
# LOCAL EMAIL SCANNER
# =============================================================================

class LocalEmailScanner:
    """
    Main scanner class for extracting relationship data from MBOX files.

    Combines MboxStreamer and ContactTracker to build relationship graph.
    """

    def __init__(
        self,
        my_email: str,
        extra_blocked_domains: Optional[set] = None,
    ):
        """
        Initialize scanner.

        Args:
            my_email: Your email address (to determine sent vs received)
            extra_blocked_domains: Additional domains to filter out
        """
        self.my_email = my_email.lower()
        self.my_domain = self._extract_domain(my_email)

        self.blocked_domains = PROVIDER_BLOCKLIST.copy()
        if extra_blocked_domains:
            self.blocked_domains.update(extra_blocked_domains)

        self._streamer = MboxStreamer()

    def scan_mbox(self, mbox_path: str) -> ContactTracker:
        """
        Scan an MBOX file and return contact statistics.

        Args:
            mbox_path: Path to the MBOX file

        Returns:
            ContactTracker with populated contact data
        """
        if not Path(mbox_path).exists():
            raise FileNotFoundError(f"MBOX file not found: {mbox_path}")

        tracker = ContactTracker()

        for header in self._streamer.stream_headers(mbox_path):
            self._process_header(header, tracker)

        return tracker

    def _process_header(self, header: EmailHeader, tracker: ContactTracker) -> None:
        """Process a single email header and update tracker."""
        if header.date is None:
            # Skip messages without dates
            return

        # Determine if sent or received
        is_sent = self._is_from_me(header.from_addr)
        is_intro = self._is_intro_email(header.subject)

        if is_sent:
            # Extract domains from recipients
            self._process_recipients(header, tracker, is_intro)
        else:
            # Extract domain from sender
            self._process_sender(header, tracker)

    def _process_recipients(
        self,
        header: EmailHeader,
        tracker: ContactTracker,
        is_intro: bool,
    ) -> None:
        """Process recipients of a sent email."""
        recipients = []

        if header.to_addr:
            recipients.extend(self._extract_all_addresses(header.to_addr))
        if header.cc_addr:
            recipients.extend(self._extract_all_addresses(header.cc_addr))

        for addr in recipients:
            domain = self._extract_domain(addr)
            if domain and self._should_track_domain(domain):
                tracker.add_contact(
                    domain=domain,
                    timestamp=header.date,
                    is_intro=is_intro,
                    is_reply_received=False,
                )

    def _process_sender(
        self,
        header: EmailHeader,
        tracker: ContactTracker,
    ) -> None:
        """Process sender of a received email."""
        domain = self._extract_domain(header.from_addr)

        if domain and self._should_track_domain(domain):
            # Check if this is a reply to something we sent
            is_reply = header.in_reply_to is not None

            tracker.add_contact(
                domain=domain,
                timestamp=header.date,
                is_intro=False,
                is_reply_received=is_reply,
            )

    def _extract_all_addresses(self, addr_str: str) -> List[str]:
        """Extract all email addresses from a header value."""
        # Handle comma-separated addresses
        addresses = []
        for _, email in getaddresses([addr_str]):
            if email:
                addresses.append(email.lower())
        return addresses

    def _extract_domain(self, email: Optional[str]) -> Optional[str]:
        """Extract domain from email address."""
        if not email or '@' not in email:
            return None
        return email.split('@')[1].lower()

    def _is_from_me(self, from_addr: Optional[str]) -> bool:
        """Check if email is from my address."""
        if not from_addr:
            return False
        return from_addr.lower() == self.my_email

    def _is_intro_email(self, subject: Optional[str]) -> bool:
        """Check if subject indicates an intro email."""
        if not subject:
            return False
        return any(pattern.search(subject) for pattern in INTRO_PATTERNS)

    def _should_track_domain(self, domain: str) -> bool:
        """Check if domain should be tracked."""
        if domain in self.blocked_domains:
            return False
        if domain == self.my_domain:
            return False
        return True
