"""
Distribution Layer

Weekly digest emails with magic links for GP "No-Login" workflow.

Components:
- config: Environment configuration with validation
- sender: Email transport abstraction (Console, Resend, SMTP)
- scheduler: Idempotent run_once job for digest processing
- builders: HTML generation with Jinja2 templates
"""

from distribution.config import DistributionConfig, load_config
from distribution.sender import (
    EmailMessage,
    EmailSender,
    SendResult,
    ConsoleSender,
    ResendSender,
    SMTPSender,
    get_email_sender,
)
from distribution.builders import (
    DigestBuilder,
    DigestCompany,
    DigestResult,
    build_digest_for_recipient,
)
from distribution.scheduler import DigestScheduler

__all__ = [
    # Config
    "DistributionConfig",
    "load_config",
    # Sender
    "EmailMessage",
    "EmailSender",
    "SendResult",
    "ConsoleSender",
    "ResendSender",
    "SMTPSender",
    "get_email_sender",
    # Builders
    "DigestBuilder",
    "DigestCompany",
    "DigestResult",
    "build_digest_for_recipient",
    # Scheduler
    "DigestScheduler",
]
