"""
Test script for Notion schema validation.

Tests the new validate_schema() method and ValidationResult class.

Usage:
    pytest test_schema_validation.py -v
"""

import pytest
import pytest_asyncio
import asyncio
import os
from connectors.notion_connector_v2 import NotionConnector, ValidationResult


@pytest.mark.asyncio
async def test_validation():
    """Test schema validation"""
    print("=" * 80)
    print("Notion Schema Validation Test")
    print("=" * 80)

    # Get credentials from environment
    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("NOTION_DATABASE_ID")

    if not api_key or not database_id:
        print("\nERROR: Missing environment variables")
        print("Required: NOTION_API_KEY, NOTION_DATABASE_ID")
        return

    print(f"\nDatabase ID: {database_id[:8]}...")

    # Create connector WITHOUT validation on init
    print("\n1. Creating connector (validation_on_init=False)...")
    connector = NotionConnector(
        api_key=api_key,
        database_id=database_id,
        validate_schema_on_init=False
    )
    print("   ✅ Connector created")

    # Test manual validation
    print("\n2. Running manual schema validation...")
    result = await connector.validate_schema(force_refresh=True)

    print(f"\n   Validation Result:")
    print(f"   {'=' * 76}")
    if result.valid:
        print(f"   ✅ VALID")
        if result.missing_optional_properties:
            print(f"   ⚠️  Missing optional properties: {result.missing_optional_properties}")
    else:
        print(f"   ❌ INVALID\n")
        print(str(result))

    # Show detailed results
    print(f"\n   Details:")
    print(f"   - Timestamp: {result.timestamp}")
    print(f"   - Missing required: {result.missing_properties or 'None'}")
    print(f"   - Missing optional: {result.missing_optional_properties or 'None'}")
    print(f"   - Wrong types: {result.wrong_property_types or 'None'}")
    print(f"   - Missing status options: {result.missing_status_options or 'None'}")
    print(f"   - Missing stage options: {result.missing_stage_options or 'None'}")

    # Test validation on init (if schema is valid)
    if result.valid:
        print("\n3. Testing validation on init...")
        try:
            connector_with_validation = NotionConnector(
                api_key=api_key,
                database_id=database_id,
                validate_schema_on_init=True
            )
            print("   ✅ Init validation passed")
        except ValueError as e:
            print(f"   ❌ Init validation failed: {e}")

    # Test preflight in upsert flow
    print("\n4. Testing preflight check in operations...")
    from connectors.notion_connector_v2 import ProspectPayload, InvestmentStage
    import httpx

    async with httpx.AsyncClient() as client:
        try:
            await connector._ensure_schema(client, strict=True)
            print("   ✅ Preflight check passed")
        except ValueError as e:
            print(f"   ❌ Preflight check failed: {e}")

    print("\n" + "=" * 80)
    print("Test completed!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_validation())
