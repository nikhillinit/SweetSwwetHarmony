# PDFProfiler Verification Guide

This document provides verification commands to ensure the PDFProfiler implementation is working correctly.

## Quick Verification Commands

### 1. Test Suite Verification

Run all PDFProfiler tests:

```bash
# All profiler tests (should show 182 passed)
pytest tests/profilers/ -v

# PDFProfiler specific tests only (50 tests)
pytest tests/profilers/test_pdf_profiler.py -v
pytest tests/profilers/test_exit_predictor_integration.py -v
pytest tests/profilers/test_pdf_profiler_cli.py -v
pytest tests/profilers/test_env_example.py -v

# Configuration tests
pytest tests/profilers/test_config.py -v
```

### 2. Privacy Configuration Verification

Verify privacy flags are correctly configured:

```bash
# Check PrivacyConfig defaults (should show both False)
python -c "from profilers.config import PrivacyConfig; config = PrivacyConfig(); print(f'ALLOW_CLOUD_LLM: {config.allow_cloud_llm}'); print(f'ALLOW_CLOUD_VISION: {config.allow_cloud_vision}')"

# Expected output:
# ALLOW_CLOUD_LLM: False
# ALLOW_CLOUD_VISION: False
```

### 3. Database Schema Verification

Verify finance predicates are in database:

```bash
# Check schema version (should be 14)
python -c "from storage.signal_store import CURRENT_SCHEMA_VERSION; print(f'Schema version: {CURRENT_SCHEMA_VERSION}')"

# Expected output:
# Schema version: 14
```

### 4. PDFProfiler Functionality Test

Test PDF extraction with a sample document:

```python
# Create test_pdf_extraction.py
import asyncio
from pathlib import Path
from profilers.pdf_profiler import PDFProfiler
from profilers.config import PrivacyConfig

async def test_extraction():
    # Create a simple test PDF
    import fitz
    pdf_path = Path("test_sample.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Acme AI Inc.\nMonthly Burn Rate: $50,000\nRunway: 18 months")
    doc.save(str(pdf_path))
    doc.close()

    # Test local-only extraction (Tier 1)
    config = PrivacyConfig(allow_cloud_llm=False, allow_cloud_vision=False)
    profiler = PDFProfiler(config=config)

    # Extract text
    text = profiler.extract_text(pdf_path)
    print(f"✓ Text extracted: {len(text)} characters")

    # Extract finance metrics
    metrics = profiler.extract_finance_metrics(text)
    print(f"✓ Finance metrics found: {len(metrics)}")
    for metric, value in metrics.items():
        print(f"  - {metric}: {value}")

    # Test profile() method
    result = await profiler.profile(pdf_path, canonical_key="domain:test.ai")
    print(f"\n✓ Profile complete:")
    print(f"  - Text extracted: {result['text_extracted']}")
    print(f"  - Metrics extracted: {result['metrics_extracted']}")
    print(f"  - Tables extracted: {result['tables_extracted']}")

    # Cleanup
    pdf_path.unlink()
    print("\n✅ All PDFProfiler functions working correctly!")

asyncio.run(test_extraction())
```

Run it:

```bash
python test_pdf_extraction.py

# Expected output:
# ✓ Text extracted: 53 characters
# ✓ Finance metrics found: 2
#   - burn_rate_usd_monthly: 50000.0
#   - runway_months: 18
#
# ✓ Profile complete:
#   - Text extracted: True
#   - Metrics extracted: 2
#   - Tables extracted: 0
#
# ✅ All PDFProfiler functions working correctly!
```

### 5. ExitPredictor Integration Test

Verify ExitPredictor can read from ClaimStore:

```python
# Create test_exit_predictor.py
import asyncio
from storage.signal_store import SignalStore
from storage.claim_store import ClaimStore
from utils.exit_predictor import ExitPredictor

async def test_predictor():
    # Initialize stores
    store = SignalStore()
    await store.initialize()

    claim_store = ClaimStore(signal_store=store)

    # Create predictor with ClaimStore
    predictor = ExitPredictor(claim_store=claim_store)

    # Test compute_funding_score_from_claims
    score = await predictor.compute_funding_score_from_claims("domain:test.ai")
    print(f"✓ Funding score computed: {score}")
    print(f"  (Default 0.3 expected when no claims exist)")

    await store.close()
    print("\n✅ ExitPredictor integration working!")

asyncio.run(test_predictor())
```

Run it:

```bash
python test_exit_predictor.py

# Expected output:
# ✓ Funding score computed: 0.3
#   (Default 0.3 expected when no claims exist)
#
# ✅ ExitPredictor integration working!
```

### 6. CLI Command Verification

Test the repredict CLI command:

```bash
# Show help
python -m profilers.pdf_profiler_cli repredict --help

# Expected output:
# Usage: pdf_profiler_cli repredict [OPTIONS] CANONICAL_KEY
#
# Re-compute exit prediction using latest PDF finance claims
# ...

# Dry-run test
python -m profilers.pdf_profiler_cli repredict domain:test.ai --dry-run

# Expected output:
# Re-computing exit prediction for: domain:test.ai
#   Enhanced funding score: 0.300
#   ⚠ No claims found for domain:test.ai
#   Using default funding score: 0.300
#
# ✓ Dry-run mode: Would update exit prediction with funding_score=0.300
#   (No changes saved to database)
```

### 7. Privacy Gate Verification

Verify privacy gates prevent cloud access when disabled:

```python
# Create test_privacy_gates.py
import asyncio
from pathlib import Path
from profilers.pdf_profiler import PDFProfiler
from profilers.config import PrivacyConfig

async def test_privacy():
    # Create test PDF
    import fitz
    pdf_path = Path("test_privacy.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Confidential financial data")
    doc.save(str(pdf_path))
    doc.close()

    # Test Tier 2 protection (cloud LLM disabled)
    config = PrivacyConfig(allow_cloud_llm=False, allow_cloud_vision=False)
    profiler = PDFProfiler(config=config)

    try:
        await profiler.extract_structured_text(pdf_path)
        print("✗ FAILED: Should have raised PermissionError")
    except PermissionError as e:
        print(f"✓ Tier 2 gate working: {e}")

    # Test Tier 3 protection (cloud vision disabled)
    try:
        await profiler.extract_with_vision(pdf_path)
        print("✗ FAILED: Should have raised PermissionError")
    except PermissionError as e:
        print(f"✓ Tier 3 gate working: {e}")

    # Cleanup
    pdf_path.unlink()
    print("\n✅ Privacy gates preventing cloud access!")

asyncio.run(test_privacy())
```

Run it:

```bash
python test_privacy_gates.py

# Expected output:
# ✓ Tier 2 gate working: Cloud LLM extraction disabled (ALLOW_CLOUD_LLM=False). This prevents sending potentially sensitive PDF content to external APIs.
# ✓ Tier 3 gate working: Cloud vision extraction disabled (ALLOW_CLOUD_VISION=False). This prevents sending potentially sensitive PDF images to external APIs.
#
# ✅ Privacy gates preventing cloud access!
```

## Integration Verification

### Full Pipeline Test (Optional)

If you want to test the full pipeline with PDF profiling:

```bash
# 1. Create a sample PDF with financial data
# 2. Profile it using PDFProfiler
# 3. Verify claims are saved to ClaimStore
# 4. Run repredict to update exit prediction
# 5. Verify prediction was updated

# This requires setting up the full signal pipeline
# See docs/plans/YYYY-MM-DD-pragmatic-intelligence.md for details
```

## Expected Test Coverage

After running all tests:

```bash
pytest tests/profilers/ -v --tb=short

# Expected output (end):
# ============================= 182 passed in X.XX s =============================
```

Breakdown:
- `test_pdf_profiler.py`: 37 tests (text, tables, finance, profile, Tier 2/3)
- `test_exit_predictor_integration.py`: 7 tests (ClaimStore integration)
- `test_pdf_profiler_cli.py`: 6 tests (CLI commands)
- `test_env_example.py`: 9 tests (configuration)
- `test_config.py`: 11 tests (PrivacyConfig)
- `test_url_profiler.py`: 112 tests (existing URL profiler tests)

## Troubleshooting

### Import Errors

If you see `ModuleNotFoundError: No module named 'profilers.pdf_profiler'`:

```bash
# Verify you're in the project root
pwd  # Should be C:\dev\Harmonic

# Verify the module exists
ls profilers/pdf_profiler.py  # Should exist

# Try importing manually
python -c "from profilers.pdf_profiler import PDFProfiler; print('✓ Import successful')"
```

### Privacy Flag Not Working

If privacy flags aren't being respected:

```bash
# Check if .env file exists
cat .env | grep ALLOW_CLOUD

# If missing, copy from example
cp .env.example .env

# Verify environment variable loading
python -c "from profilers.config import load_privacy_config; config = load_privacy_config(); print(config)"
```

### Database Errors

If you see database errors:

```bash
# Remove old database and reinitialize
rm signals.db
python -c "import asyncio; from storage.signal_store import SignalStore; async def init(): s = SignalStore(); await s.initialize(); await s.close(); asyncio.run(init())"

# Verify schema version
python -c "from storage.signal_store import CURRENT_SCHEMA_VERSION; print(f'Schema: {CURRENT_SCHEMA_VERSION}')"
```

## Success Criteria

✅ All 182 tests pass
✅ Privacy gates prevent cloud access when disabled
✅ Finance metrics extracted from PDFs
✅ ExitPredictor reads from ClaimStore
✅ CLI repredict command works
✅ .env.example documents privacy flags
✅ No real secrets in .env.example

---

*Last updated: 2026-01-27*
