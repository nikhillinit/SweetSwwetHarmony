"""
Distribution Builders

HTML generation for email digests using Jinja2 templates.
"""

from distribution.builders.digest_builder import (
    DigestBuilder,
    DigestCompany,
    DigestResult,
    build_digest_for_recipient,
)

__all__ = [
    "DigestBuilder",
    "DigestCompany",
    "DigestResult",
    "build_digest_for_recipient",
]
