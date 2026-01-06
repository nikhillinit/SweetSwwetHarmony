# Notion Schema Validation

Automated validation to prevent silent failures from schema drift.

## Overview

The Discovery Engine pushes prospects to Notion CRM. If the Notion database schema changes (properties renamed, deleted, or select options modified), operations can fail silently or produce incorrect data.

**Schema validation** detects these issues early with clear error messages.

## Features

- **Preflight checks** - Validates schema before every upsert operation
- **Manual validation** - Call `validate_schema()` to check schema health
- **Init validation** - Optional schema check on connector initialization
- **MCP prompt** - `validate-notion-schema` for easy schema checks via MCP
- **Detailed reports** - Shows exactly what's missing or misconfigured

## Required Notion Properties

### Core Properties

| Property | Type | Required | Notes |
|----------|------|----------|-------|
| Company Name | title | Yes | Database title field |
| Status | select | Yes | Must include all 8 statuses (see below) |
| Investment Stage | select | Yes | Must include all 7 stages (see below) |
| Discovery ID | rich_text | Yes | Unique identifier from Discovery Engine |
| Canonical Key | rich_text | Yes | Deterministic deduplication key |
| Confidence Score | number | Yes | Thesis fit score (0.0-1.0) |

### Optional Properties (Recommended)

| Property | Type | Required | Notes |
|----------|------|----------|-------|
| Website | url | No | Company website |
| Signal Types | multi_select | No | What triggered discovery |
| Why Now | rich_text | No | 1-sentence summary |

### Required Status Options

The `Status` select property must include these EXACT values (including the typo):

- Source
- Initial Meeting / Call
- Dilligence _(note the double 'L')_
- Tracking
- Committed
- Funded
- Passed
- Lost

### Required Investment Stage Options

The `Investment Stage` select property must include:

- Pre-Seed
- Seed
- Seed +
- Series A
- Series B
- Series C
- Series D

## Usage

### 1. Manual Validation

```python
from connectors.notion_connector_v2 import NotionConnector

connector = NotionConnector(
    api_key="secret_xxx",
    database_id="database_id_xxx"
)

# Validate schema
result = await connector.validate_schema(force_refresh=True)

if not result.valid:
    print(result)  # Human-readable report
    raise ValueError("Schema validation failed")
```

### 2. Validation on Initialization

```python
# Fail fast if schema is broken
connector = NotionConnector(
    api_key="secret_xxx",
    database_id="database_id_xxx",
    validate_schema_on_init=True  # Raises ValueError on mismatch
)
```

### 3. Automatic Preflight (Already Integrated)

Schema validation runs automatically before every `upsert_prospect()` call:

```python
result = await connector.upsert_prospect(prospect)
# Schema is validated before the operation starts
```

### 4. Via MCP Prompt

```bash
# From Claude or MCP client
/mcp__discovery-engine__validate-notion-schema force_refresh=true
```

### 5. Standalone Test Script

```bash
python test_schema_validation.py
```

## ValidationResult

The `validate_schema()` method returns a `ValidationResult` object:

```python
@dataclass
class ValidationResult:
    valid: bool
    missing_properties: List[str]
    missing_optional_properties: List[str]
    missing_status_options: List[str]
    missing_stage_options: List[str]
    wrong_property_types: Dict[str, str]
    timestamp: datetime
```

**String representation** provides a human-readable report:

```
Schema validation FAILED:

Missing REQUIRED properties:
  - Discovery ID
  - Canonical Key

Missing optional properties (recommended):
  - Signal Types
  - Why Now

Missing Status select options:
  - Source
  - Tracking

Fix these issues in Notion database settings, then retry.
```

## How It Works

1. **Fetch schema** - `GET /databases/{database_id}` returns full schema
2. **Cache results** - Schema cached for 6 hours (configurable)
3. **Validate properties** - Check all required properties exist with correct types
4. **Validate select options** - Ensure Status and Investment Stage have all required values
5. **Return result** - ValidationResult with detailed findings

## Error Handling

### Missing Property

```
Schema validation FAILED:

Missing REQUIRED properties:
  - Discovery ID

Fix these issues in Notion database settings, then retry.
```

**Fix:** Add the missing property to your Notion database with the correct type.

### Missing Select Option

```
Schema validation FAILED:

Missing Status select options:
  - Source

Fix these issues in Notion database settings, then retry.
```

**Fix:** Add the missing option to the Status select property in Notion.

### Wrong Property Type

```
Schema validation FAILED:

Wrong property types:
  - Confidence Score: expected number

Fix these issues in Notion database settings, then retry.
```

**Fix:** Change the property type in Notion to match the expected type.

## Performance

- **Schema fetch:** ~200-300ms (first call)
- **Validation check:** ~1-5ms (cached)
- **Cache TTL:** 6 hours (adjustable)
- **Rate limiting:** Respects Notion 3 req/sec limit

## Configuration

```python
connector = NotionConnector(
    api_key=api_key,
    database_id=database_id,
    validate_schema_on_init=False,  # Validate on init?
)

# Adjust cache TTL (default 6 hours)
connector._schema_ttl = timedelta(hours=1)
```

## Integration Points

### 1. NotionConnector.upsert_prospect()

Runs `_ensure_schema(strict=True)` before every upsert.

### 2. NotionConnector.get_suppression_list()

Runs `_ensure_schema(strict=False)` - logs warnings but doesn't block.

### 3. Test Suite

`test_connection()` function runs validation as part of connector tests:

```bash
python connectors/notion_connector_v2.py
```

### 4. MCP Server

New prompt: `validate-notion-schema` - returns JSON result via MCP.

## Best Practices

1. **Run validation after schema changes** - Immediately after updating Notion
2. **Monitor validation in CI/CD** - Catch drift before deployment
3. **Enable init validation in production** - Fail fast on startup
4. **Check ValidationResult** - Don't just check `.valid`, examine details
5. **Fix missing optional properties** - They're optional but recommended

## Troubleshooting

### "Schema validation failed on init"

**Cause:** Schema mismatch detected during connector initialization.

**Fix:** Run manual validation to see detailed report:

```python
result = await connector.validate_schema(force_refresh=True)
print(result)
```

### "Optional Notion properties missing"

**Cause:** Optional properties not in database (warning only).

**Fix:** Add recommended properties to improve tracking:
- Signal Types (multi_select)
- Why Now (rich_text)
- Website (url)

### Schema cached but outdated

**Cause:** Schema was updated in Notion but cache not refreshed.

**Fix:** Force refresh:

```python
result = await connector.validate_schema(force_refresh=True)
```

## Testing

```bash
# Test connector with validation
python connectors/notion_connector_v2.py

# Test validation specifically
python test_schema_validation.py

# Test via MCP
python -m discovery_engine.mcp_server
# Then use: /mcp__discovery-engine__validate-notion-schema
```

## Related Files

| File | Purpose |
|------|---------|
| `connectors/notion_connector_v2.py` | Main implementation |
| `discovery_engine/mcp_server.py` | MCP prompt integration |
| `test_schema_validation.py` | Standalone test script |
| `docs/SCHEMA_VALIDATION.md` | This document |

## Migration Guide

If upgrading from v1 connector:

```python
# OLD (v1 - no validation)
connector = NotionConnector(api_key, database_id)

# NEW (v2 - with validation)
connector = NotionConnector(
    api_key,
    database_id,
    validate_schema_on_init=True  # Optional: fail fast
)

# Check schema health
result = await connector.validate_schema()
if not result.valid:
    print(result)
```

## FAQ

**Q: How often does validation run?**

A: Automatically before every upsert. Manual calls can be made anytime.

**Q: Does validation slow down operations?**

A: First call: ~200-300ms. Subsequent calls (cached): ~1-5ms. Negligible overhead.

**Q: What happens if validation fails during upsert?**

A: Operation is aborted with a clear error message before any Notion API calls.

**Q: Can I disable validation?**

A: No. Preflight checks prevent silent data corruption. Use `strict=False` for warnings only.

**Q: How do I add a new required property?**

A:
1. Add property to Notion database
2. Update `required_props_with_types` in `validate_schema()`
3. Update property constants (e.g., `PROP_NEW_FIELD`)
4. Update this documentation

## See Also

- [Notion API Reference](https://developers.notion.com/reference/intro)
- [MCP Architecture](./MCP_ARCHITECTURE.md)
- [Tool Reference Card](./TOOL_REFERENCE_CARD.md)
