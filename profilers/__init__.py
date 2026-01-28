"""
Profilers Module for Discovery Engine.

Provides profiling capabilities for companies:
- URL profiling: Fetch and extract data from websites
- PDF profiling: Extract financial data from pitch decks (Phase 1)
- Privacy controls: Prevent NDA leakage via cloud services

Usage:
    from profilers import URLProfiler, PrivacyConfig, load_privacy_config

    # URL profiling
    profiler = URLProfiler(signal_store=store)
    profile = await profiler.profile("https://acme.ai")

    # Privacy configuration
    config = load_privacy_config()  # Reads from env vars
"""

from profilers.config import (
    PrivacyConfig,
    load_privacy_config,
)

from profilers.url_profiler import (
    URLProfiler,
    CompanyProfile,
    ExtractedField,
    ProfileExtractionResult,
    PageFetchResult,
)

# CLI module (imported separately)
from profilers import pdf_profiler_cli

__all__ = [
    # Configuration
    "PrivacyConfig",
    "load_privacy_config",
    # URL Profiler
    "URLProfiler",
    "CompanyProfile",
    "ExtractedField",
    "ProfileExtractionResult",
    "PageFetchResult",
    # CLI
    "pdf_profiler_cli",
]
