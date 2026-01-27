"""
Email Sender Abstraction

Provides pluggable email transport for the distribution layer.
Supports Console (dev), Resend (production), and SMTP (fallback).
"""

import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from distribution.config import DistributionConfig

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """Email message to be sent."""
    to: List[str]
    subject: str
    html_body: str
    text_body: Optional[str] = None
    reply_to: Optional[str] = None


@dataclass
class SendResult:
    """Result of sending an email."""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class EmailSender(ABC):
    """Abstract base class for email senders."""

    def __init__(self, config: DistributionConfig):
        self.config = config
        self.from_email = config.digest_from_email

    @abstractmethod
    async def send(self, message: EmailMessage) -> SendResult:
        """
        Send an email message.

        Args:
            message: The email message to send

        Returns:
            SendResult with success status and optional message_id/error
        """
        pass

    @abstractmethod
    def get_transport_name(self) -> str:
        """Return the name of this transport for logging."""
        pass


class ConsoleSender(EmailSender):
    """
    Development sender that prints emails to stdout.

    Used when EMAIL_TRANSPORT=console or as fallback when credentials missing.
    """

    async def send(self, message: EmailMessage) -> SendResult:
        """Print email to console instead of sending."""
        separator = "=" * 60
        print(f"\n{separator}")
        print("EMAIL (Console Mode - Not Actually Sent)")
        print(separator)
        print(f"From: {self.from_email}")
        print(f"To: {', '.join(message.to)}")
        print(f"Subject: {message.subject}")
        if message.reply_to:
            print(f"Reply-To: {message.reply_to}")
        print(f"{separator}")
        print("HTML Body Preview (first 500 chars):")
        print(message.html_body[:500])
        if len(message.html_body) > 500:
            print(f"... ({len(message.html_body)} total chars)")
        print(separator)

        if message.text_body:
            print("Text Body:")
            print(message.text_body[:300])
            if len(message.text_body) > 300:
                print(f"... ({len(message.text_body)} total chars)")
            print(separator)

        logger.info(f"Console email printed: {message.subject} -> {message.to}")

        return SendResult(
            success=True,
            message_id="console-" + str(hash(message.subject))[:8],
        )

    def get_transport_name(self) -> str:
        return "console"


class ResendSender(EmailSender):
    """
    Production sender using Resend API.

    Requires RESEND_API_KEY to be set.
    """

    def __init__(self, config: DistributionConfig):
        super().__init__(config)
        if not config.resend_api_key:
            raise ValueError("ResendSender requires RESEND_API_KEY")
        self.api_key = config.resend_api_key

    async def send(self, message: EmailMessage) -> SendResult:
        """Send email via Resend API."""
        try:
            import resend

            resend.api_key = self.api_key

            params = {
                "from": self.from_email,
                "to": message.to,
                "subject": message.subject,
                "html": message.html_body,
            }

            if message.text_body:
                params["text"] = message.text_body

            if message.reply_to:
                params["reply_to"] = message.reply_to

            # Resend SDK is sync, but we wrap for consistency
            response = resend.Emails.send(params)

            logger.info(f"Resend email sent: {message.subject} -> {message.to}, id={response.get('id')}")

            return SendResult(
                success=True,
                message_id=response.get("id"),
            )

        except ImportError:
            logger.error("resend package not installed. Run: pip install resend")
            return SendResult(
                success=False,
                error="resend package not installed",
            )

        except Exception as e:
            logger.error(f"Resend send failed: {e}")
            return SendResult(
                success=False,
                error=str(e),
            )

    def get_transport_name(self) -> str:
        return "resend"


class SMTPSender(EmailSender):
    """
    SMTP sender for environments where Resend isn't available.

    Requires SMTP_HOST, optionally SMTP_USER/SMTP_PASSWORD for auth.
    """

    def __init__(self, config: DistributionConfig):
        super().__init__(config)
        if not config.smtp_host:
            raise ValueError("SMTPSender requires SMTP_HOST")
        self.host = config.smtp_host
        self.port = config.smtp_port
        self.user = config.smtp_user
        self.password = config.smtp_password

    async def send(self, message: EmailMessage) -> SendResult:
        """Send email via SMTP."""
        try:
            # Build MIME message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.from_email
            msg["To"] = ", ".join(message.to)
            msg["Subject"] = message.subject

            if message.reply_to:
                msg["Reply-To"] = message.reply_to

            # Attach text and HTML parts
            if message.text_body:
                text_part = MIMEText(message.text_body, "plain")
                msg.attach(text_part)

            html_part = MIMEText(message.html_body, "html")
            msg.attach(html_part)

            # Send via SMTP
            with smtplib.SMTP(self.host, self.port) as server:
                server.ehlo()
                if self.port == 587:
                    server.starttls()
                    server.ehlo()

                if self.user and self.password:
                    server.login(self.user, self.password)

                server.sendmail(
                    self.from_email,
                    message.to,
                    msg.as_string(),
                )

            logger.info(f"SMTP email sent: {message.subject} -> {message.to}")

            return SendResult(
                success=True,
                message_id=f"smtp-{hash(message.subject)}",
            )

        except smtplib.SMTPException as e:
            logger.error(f"SMTP send failed: {e}")
            return SendResult(
                success=False,
                error=str(e),
            )

        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            return SendResult(
                success=False,
                error=str(e),
            )

    def get_transport_name(self) -> str:
        return "smtp"


def get_email_sender(config: DistributionConfig) -> EmailSender:
    """
    Factory function to get the appropriate email sender.

    Selection logic:
    1. If EMAIL_TRANSPORT=console -> ConsoleSender
    2. If EMAIL_TRANSPORT=resend and RESEND_API_KEY set -> ResendSender
    3. If EMAIL_TRANSPORT=smtp and SMTP_HOST set -> SMTPSender
    4. Fallback to ConsoleSender with warning

    Args:
        config: Distribution configuration

    Returns:
        Appropriate EmailSender instance
    """
    transport = config.email_transport.lower()

    if transport == "console":
        logger.info("Using ConsoleSender (development mode)")
        return ConsoleSender(config)

    if transport == "resend":
        if config.resend_api_key:
            logger.info("Using ResendSender (production)")
            return ResendSender(config)
        else:
            logger.warning("RESEND_API_KEY not set, falling back to ConsoleSender")
            return ConsoleSender(config)

    if transport == "smtp":
        if config.smtp_host:
            logger.info(f"Using SMTPSender ({config.smtp_host}:{config.smtp_port})")
            return SMTPSender(config)
        else:
            logger.warning("SMTP_HOST not set, falling back to ConsoleSender")
            return ConsoleSender(config)

    # Unknown transport - fallback with warning
    logger.warning(f"Unknown EMAIL_TRANSPORT '{transport}', falling back to ConsoleSender")
    return ConsoleSender(config)


# Quick test when run directly
if __name__ == "__main__":
    import asyncio
    from distribution.config import load_config

    async def test_sender():
        config = load_config()
        sender = get_email_sender(config)

        print(f"Using transport: {sender.get_transport_name()}")

        result = await sender.send(EmailMessage(
            to=["test@example.com"],
            subject="Test Email from Distribution Layer",
            html_body="<h1>Hello!</h1><p>This is a test email.</p>",
            text_body="Hello! This is a test email.",
        ))

        print(f"\nResult: success={result.success}, id={result.message_id}")
        if result.error:
            print(f"Error: {result.error}")

    asyncio.run(test_sender())
