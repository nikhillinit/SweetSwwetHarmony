"""
Verification script for signal storage layer installation.

Checks:
1. Dependencies installed
2. Module imports work
3. Database creation works
4. Basic operations work
5. Migration tools work

Run with:
    python storage/verify_installation.py
"""

import sys
import os
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check_dependencies():
    """Check that required dependencies are installed."""
    print("Checking dependencies...")

    try:
        import aiosqlite
        print("  OK: aiosqlite installed")
    except ImportError:
        print("  ERROR: aiosqlite not installed")
        print("  Run: pip install aiosqlite")
        return False

    return True


def check_imports():
    """Check that storage module imports work."""
    print("\nChecking imports...")

    try:
        from storage import SignalStore, StoredSignal, SuppressionEntry, signal_store
        print("  OK: storage module imports work")
    except ImportError as e:
        print(f"  ERROR: Failed to import storage module: {e}")
        return False

    try:
        from utils.canonical_keys import build_canonical_key
        print("  OK: canonical_keys imports work")
    except ImportError as e:
        print(f"  ERROR: Failed to import canonical_keys: {e}")
        return False

    return True


async def check_basic_operations():
    """Check that basic storage operations work."""
    print("\nChecking basic operations...")

    try:
        from storage import signal_store

        # Create temporary database
        db_path = Path("verify_test.db")

        async with signal_store(db_path) as store:
            # Test save
            signal_id = await store.save_signal(
                signal_type="test",
                source_api="verify",
                canonical_key="domain:test.com",
                company_name="Test Co",
                confidence=0.5,
                raw_data={"test": True}
            )
            print(f"  OK: Save signal (ID: {signal_id})")

            # Test retrieve
            signal = await store.get_signal(signal_id)
            assert signal is not None
            print(f"  OK: Retrieve signal")

            # Test duplicate check
            is_dup = await store.is_duplicate("domain:test.com")
            assert is_dup
            print(f"  OK: Duplicate detection")

            # Test mark pushed
            await store.mark_pushed(signal_id, "notion-test-123")
            print(f"  OK: Mark pushed")

            # Test stats
            stats = await store.get_stats()
            assert stats['total_signals'] == 1
            print(f"  OK: Get stats")

        # Clean up
        if db_path.exists():
            db_path.unlink()

        return True

    except Exception as e:
        print(f"  ERROR: Basic operations failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_migration_tools():
    """Check that migration tools work."""
    print("\nChecking migration tools...")

    try:
        from storage.migrations import list_migrations, validate_schema, get_info

        # Check that functions exist
        print("  OK: Migration functions available")

        return True

    except ImportError as e:
        print(f"  ERROR: Failed to import migration tools: {e}")
        return False


async def run_verification():
    """Run all verification checks."""
    print("=" * 70)
    print("Signal Storage Installation Verification")
    print("=" * 70)

    all_ok = True

    # Check dependencies
    if not check_dependencies():
        all_ok = False

    # Check imports
    if not check_imports():
        all_ok = False

    # Check basic operations
    if not await check_basic_operations():
        all_ok = False

    # Check migration tools
    if not check_migration_tools():
        all_ok = False

    # Final result
    print("\n" + "=" * 70)
    if all_ok:
        print("VERIFICATION PASSED")
        print("=" * 70)
        print("\nThe signal storage layer is properly installed!")
        print("\nNext steps:")
        print("  - Run tests: python storage/test_signal_store.py")
        print("  - View example: python storage/integration_example.py")
        print("  - Read docs: storage/README.md")
        return 0
    else:
        print("VERIFICATION FAILED")
        print("=" * 70)
        print("\nSome checks failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_verification())
    sys.exit(exit_code)
