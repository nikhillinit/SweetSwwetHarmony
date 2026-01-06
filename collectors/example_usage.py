"""
Example usage of the GitHub Signal Collector

This script demonstrates how to:
1. Run the GitHub collector
2. Analyze the signals it finds
3. Inspect canonical keys and confidence scores
4. Route signals through the verification gate
"""

import asyncio
import logging
import os
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def example_basic():
    """Basic example: Run collector and print results"""
    from github import GitHubCollector

    logger.info("=== BASIC EXAMPLE ===")

    # Create collector
    collector = GitHubCollector(
        lookback_days=30,
        max_repos=50  # Limit for demo
    )

    # Run in dry-run mode
    result = await collector.run(dry_run=True)

    # Print summary
    print("\n" + "=" * 70)
    print("GITHUB COLLECTOR RESULTS")
    print("=" * 70)
    print(f"Status:             {result.status.value}")
    print(f"Signals Found:      {result.signals_found}")
    print(f"Signals New:        {result.signals_new}")
    print(f"Signals Suppressed: {result.signals_suppressed}")
    print(f"Dry Run:            {result.dry_run}")
    if result.error_message:
        print(f"Errors:             {result.error_message}")
    print("=" * 70)


async def example_with_verification():
    """Advanced example: Run collector and route through verification gate"""
    from github import GitHubCollector
    from verification.verification_gate_v2 import VerificationGate

    logger.info("=== VERIFICATION EXAMPLE ===")

    # Run collector
    collector = GitHubCollector(lookback_days=14, max_repos=20)

    # Note: To actually get signals, we'd need to modify the collector
    # to return them. For now, this shows the integration pattern.

    # In production, you would:
    # 1. Run collector to get signals
    # 2. Group signals by canonical key
    # 3. Run through verification gate
    # 4. Push qualified prospects to Notion

    print("\nExample verification flow:")
    print("1. Collector finds GitHub spike signals")
    print("2. Group signals by company (using canonical keys)")
    print("3. VerificationGate evaluates confidence")
    print("4. Route to Notion: 'Source' (high conf) or 'Tracking' (medium conf)")


async def example_canonical_keys():
    """Example: Build canonical keys from GitHub data"""
    from utils.canonical_keys import (
        build_canonical_key_candidates,
        is_strong_key,
        get_key_strength_score
    )

    logger.info("=== CANONICAL KEY EXAMPLE ===")

    # Example 1: Company with website and GitHub org
    print("\nExample 1: Company with website")
    candidates = build_canonical_key_candidates(
        domain_or_website="https://openai.com",
        github_org="openai",
        github_repo="openai/gpt-4",
        fallback_company_name="OpenAI",
    )
    print(f"Canonical keys: {candidates}")
    print(f"Primary key: {candidates[0]}")
    print(f"Is strong key: {is_strong_key(candidates[0])}")
    print(f"Strength score: {get_key_strength_score(candidates[0])}/100")

    # Example 2: Stealth company (no website yet)
    print("\nExample 2: Stealth company (GitHub only)")
    candidates = build_canonical_key_candidates(
        github_org="stealth-startup",
        github_repo="stealth-startup/cool-ai-thing",
        fallback_company_name="Stealth Startup",
        fallback_region="San Francisco",
    )
    print(f"Canonical keys: {candidates}")
    print(f"Primary key: {candidates[0]}")
    print(f"Is strong key: {is_strong_key(candidates[0])}")
    print(f"Strength score: {get_key_strength_score(candidates[0])}/100")


async def example_signal_inspection():
    """Example: Inspect signal structure and metadata"""
    logger.info("=== SIGNAL INSPECTION EXAMPLE ===")

    print("\nTypical GitHub spike signal structure:")
    print("""
    Signal(
        id="github_spike_abc123",
        signal_type="github_spike",
        confidence=0.75,
        source_api="github",
        source_url="https://github.com/anthropic/claude-sdk",
        verified_by_sources=["github"],
        verification_status=VerificationStatus.SINGLE_SOURCE,
        raw_data={
            # Identifiers
            "repo_full_name": "anthropic/claude-sdk",
            "canonical_key": "domain:anthropic.com",
            "canonical_key_candidates": [
                "domain:anthropic.com",
                "github_org:anthropic",
                "github_repo:anthropic/claude-sdk"
            ],

            # Metrics (why this is a signal)
            "stars": 1500,
            "recent_stars": 250,
            "growth_rate": 0.20,
            "velocity_stars_per_day": 8.3,

            # Context
            "language": "Python",
            "topics": ["ai", "llm", "sdk", "anthropic"],
            "owner_type": "Organization",
            "owner_company": "Anthropic",
            "owner_website": "https://anthropic.com",

            # Narrative
            "why_now": "Rapid adoption: +250 stars in 30 days; 20% growth rate",
            "thesis_fit": "AI Infrastructure",
        }
    )
    """)

    print("\nConfidence scoring factors:")
    print("- Base: 0.5")
    print("- +0.2 if recent_stars > 100")
    print("- +0.15 if growth_rate > 50%")
    print("- +0.1 if organization-owned")
    print("- +0.05 if has website/company info")
    print("- Max: 0.95 (never 100% from single source)")


async def main():
    """Run all examples"""
    print("\n" + "=" * 70)
    print("GITHUB COLLECTOR EXAMPLES")
    print("=" * 70)

    # Check for GitHub token
    if not os.getenv("GITHUB_TOKEN"):
        print("\nWARNING: GITHUB_TOKEN not set. Collector will fail.")
        print("Set it with: export GITHUB_TOKEN=ghp_your_token_here")
        print("\nRunning other examples...\n")

        # Run examples that don't need API
        await example_canonical_keys()
        await example_signal_inspection()
        await example_with_verification()
        return

    # Run all examples
    await example_basic()
    await example_canonical_keys()
    await example_signal_inspection()
    await example_with_verification()

    print("\n" + "=" * 70)
    print("Examples complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
