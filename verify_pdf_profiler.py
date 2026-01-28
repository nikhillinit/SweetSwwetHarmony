#!/usr/bin/env python3
"""
Quick verification script for PDFProfiler implementation

Tests all major components and reports results.

Usage:
    python verify_pdf_profiler.py
"""

import asyncio
import sys
from pathlib import Path


def print_header(text):
    """Print a section header"""
    print(f"\n{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}\n")


def print_result(passed, message):
    """Print a test result"""
    status = "[PASS]" if passed else "[FAIL]"
    print(f"{status} | {message}")


async def verify_imports():
    """Verify all imports work"""
    print_header("1. Import Verification")

    tests_passed = 0
    tests_total = 0

    # Test profilers module
    tests_total += 1
    try:
        from profilers.pdf_profiler import PDFProfiler
        print_result(True, "PDFProfiler import")
        tests_passed += 1
    except ImportError as e:
        print_result(False, f"PDFProfiler import: {e}")

    # Test config
    tests_total += 1
    try:
        from profilers.config import PrivacyConfig, load_privacy_config
        print_result(True, "PrivacyConfig import")
        tests_passed += 1
    except ImportError as e:
        print_result(False, f"PrivacyConfig import: {e}")

    # Test exit predictor
    tests_total += 1
    try:
        from utils.exit_predictor import ExitPredictor
        print_result(True, "ExitPredictor import")
        tests_passed += 1
    except ImportError as e:
        print_result(False, f"ExitPredictor import: {e}")

    # Test CLI
    tests_total += 1
    try:
        from profilers import pdf_profiler_cli
        print_result(True, "CLI module import")
        tests_passed += 1
    except ImportError as e:
        print_result(False, f"CLI module import: {e}")

    return tests_passed, tests_total


async def verify_privacy_config():
    """Verify privacy configuration defaults"""
    print_header("2. Privacy Configuration Verification")

    tests_passed = 0
    tests_total = 0

    try:
        from profilers.config import PrivacyConfig

        # Test defaults
        tests_total += 1
        config = PrivacyConfig()
        if config.allow_cloud_llm is False and config.allow_cloud_vision is False:
            print_result(True, "Privacy defaults (both False)")
            tests_passed += 1
        else:
            print_result(False, f"Privacy defaults incorrect: LLM={config.allow_cloud_llm}, Vision={config.allow_cloud_vision}")

        # Test explicit settings
        tests_total += 1
        config_enabled = PrivacyConfig(allow_cloud_llm=True, allow_cloud_vision=True)
        if config_enabled.allow_cloud_llm is True and config_enabled.allow_cloud_vision is True:
            print_result(True, "Privacy config accepts True values")
            tests_passed += 1
        else:
            print_result(False, "Privacy config doesn't accept True values")

    except Exception as e:
        print_result(False, f"Privacy config test failed: {e}")

    return tests_passed, tests_total


async def verify_pdf_extraction():
    """Verify PDF text and metric extraction"""
    print_header("3. PDF Extraction Verification")

    tests_passed = 0
    tests_total = 0

    try:
        import fitz
        from profilers.pdf_profiler import PDFProfiler
        from profilers.config import PrivacyConfig

        # Create test PDF
        pdf_path = Path("test_verify.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Acme AI Inc.\nMonthly Burn Rate: $50,000\nRunway: 18 months\nCash on Hand: $900,000")
        doc.save(str(pdf_path))
        doc.close()

        config = PrivacyConfig()
        profiler = PDFProfiler(config=config)

        # Test text extraction
        tests_total += 1
        text = profiler.extract_text(pdf_path)
        if len(text) > 0 and "Acme AI" in text:
            print_result(True, f"Text extraction ({len(text)} chars)")
            tests_passed += 1
        else:
            print_result(False, "Text extraction failed or empty")

        # Test finance metrics
        tests_total += 1
        metrics = profiler.extract_finance_metrics(text)
        if len(metrics) >= 2:
            print_result(True, f"Finance metrics extraction ({len(metrics)} metrics)")
            tests_passed += 1
        else:
            print_result(False, f"Finance metrics extraction (expected ≥2, got {len(metrics)})")

        # Test profile method
        tests_total += 1
        result = await profiler.profile(pdf_path, canonical_key="domain:test.ai")
        if result.get("text_extracted") and result.get("metrics_extracted", 0) >= 2:
            print_result(True, "Profile method integration")
            tests_passed += 1
        else:
            print_result(False, "Profile method integration")

        # Cleanup
        pdf_path.unlink()

    except Exception as e:
        print_result(False, f"PDF extraction test failed: {e}")

    return tests_passed, tests_total


async def verify_privacy_gates():
    """Verify privacy gates prevent cloud access"""
    print_header("4. Privacy Gate Verification")

    tests_passed = 0
    tests_total = 0

    try:
        import fitz
        from profilers.pdf_profiler import PDFProfiler
        from profilers.config import PrivacyConfig

        # Create test PDF
        pdf_path = Path("test_privacy.pdf")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Confidential data")
        doc.save(str(pdf_path))
        doc.close()

        config = PrivacyConfig(allow_cloud_llm=False, allow_cloud_vision=False)
        profiler = PDFProfiler(config=config)

        # Test Tier 2 gate
        tests_total += 1
        try:
            await profiler.extract_structured_text(pdf_path)
            print_result(False, "Tier 2 gate (should have blocked)")
        except PermissionError:
            print_result(True, "Tier 2 gate (cloud LLM blocked)")
            tests_passed += 1

        # Test Tier 3 gate
        tests_total += 1
        try:
            await profiler.extract_with_vision(pdf_path)
            print_result(False, "Tier 3 gate (should have blocked)")
        except PermissionError:
            print_result(True, "Tier 3 gate (cloud vision blocked)")
            tests_passed += 1

        # Cleanup
        pdf_path.unlink()

    except Exception as e:
        print_result(False, f"Privacy gate test failed: {e}")

    return tests_passed, tests_total


async def verify_exit_predictor_integration():
    """Verify ExitPredictor ClaimStore integration"""
    print_header("5. ExitPredictor Integration Verification")

    tests_passed = 0
    tests_total = 0

    try:
        from storage.signal_store import SignalStore
        from storage.claim_store import ClaimStore
        from utils.exit_predictor import ExitPredictor

        # Initialize stores
        store = SignalStore()
        await store.initialize()
        claim_store = ClaimStore(signal_store=store)

        # Test predictor with ClaimStore
        tests_total += 1
        predictor = ExitPredictor(claim_store=claim_store)
        score = await predictor.compute_funding_score_from_claims("domain:test.ai")

        if score == 0.3:  # Default when no claims
            print_result(True, f"ClaimStore integration (default score: {score})")
            tests_passed += 1
        else:
            print_result(False, f"ClaimStore integration (unexpected score: {score})")

        await store.close()

    except Exception as e:
        print_result(False, f"ExitPredictor integration test failed: {e}")

    return tests_passed, tests_total


async def verify_env_example():
    """Verify .env.example has privacy flags"""
    print_header("6. Environment Configuration Verification")

    tests_passed = 0
    tests_total = 0

    try:
        env_example = Path(".env.example")

        tests_total += 1
        if env_example.exists():
            print_result(True, ".env.example exists")
            tests_passed += 1
        else:
            print_result(False, ".env.example not found")
            return tests_passed, tests_total

        content = env_example.read_text()

        tests_total += 1
        if "ALLOW_CLOUD_LLM" in content:
            print_result(True, "ALLOW_CLOUD_LLM documented")
            tests_passed += 1
        else:
            print_result(False, "ALLOW_CLOUD_LLM not in .env.example")

        tests_total += 1
        if "ALLOW_CLOUD_VISION" in content:
            print_result(True, "ALLOW_CLOUD_VISION documented")
            tests_passed += 1
        else:
            print_result(False, "ALLOW_CLOUD_VISION not in .env.example")

        tests_total += 1
        if any(keyword in content.upper() for keyword in ["NDA", "WARNING", "SENSITIVE"]):
            print_result(True, "Privacy warnings present")
            tests_passed += 1
        else:
            print_result(False, "Missing privacy warnings")

    except Exception as e:
        print_result(False, f"Env verification failed: {e}")

    return tests_passed, tests_total


async def main():
    """Run all verification checks"""
    print("\n" + "=" * 70)
    print("  PDFProfiler Implementation Verification")
    print("=" * 70)

    all_passed = 0
    all_total = 0

    # Run all checks
    checks = [
        verify_imports(),
        verify_privacy_config(),
        verify_pdf_extraction(),
        verify_privacy_gates(),
        verify_exit_predictor_integration(),
        verify_env_example(),
    ]

    for check in checks:
        passed, total = await check
        all_passed += passed
        all_total += total

    # Final summary
    print_header("Summary")

    percentage = (all_passed / all_total * 100) if all_total > 0 else 0

    print(f"Tests Passed: {all_passed}/{all_total} ({percentage:.1f}%)")

    if all_passed == all_total:
        print("\n*** All verification checks passed! ***")
        print("PDFProfiler implementation is working correctly")
        return 0
    else:
        print(f"\n*** {all_total - all_passed} verification check(s) failed ***")
        print("Please review the failures above")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
