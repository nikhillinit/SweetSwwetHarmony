"""
URL Profiler Module for Discovery Engine.

Provides real-time profiling of company websites:
- Fetch and cache web pages
- Extract structured business information via LLM
- Store as claims in the knowledge graph

Usage:
    from profilers import URLProfiler

    profiler = URLProfiler(signal_store=store)
    profile = await profiler.profile("https://acme.ai")
"""

from profilers.url_profiler import (
    URLProfiler,
    CompanyProfile,
    ExtractedField,
    ProfileExtractionResult,
    PageFetchResult,
)

__all__ = [
    "URLProfiler",
    "CompanyProfile",
    "ExtractedField",
    "ProfileExtractionResult",
    "PageFetchResult",
]
