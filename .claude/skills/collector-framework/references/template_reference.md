# Collector Template Reference

Blueprint for creating collector #11, #12, etc.

## Template: Fill-In-The-Blanks

```yaml
---
collector_name: [YOUR_COLLECTOR_NAME]
signal_type: [funding_event|incorporation|github_spike|product_launch|news_mention|etc]
api_base: [https://api.example.com]
authentication: [None|Bearer|Basic|API Key]
rate_limit: [X seconds between requests OR requests/minute]
---
```

## 1. API Endpoints

List all endpoints your collector will use:

```
# Main endpoint
GET [api_base]/endpoint?param=value

# Detail endpoint (if needed)
GET [api_base]/details/{id}

# Pagination
# Method: [offset/limit | page/per_page | cursor-based]
```

## 2. Industry Classification

If your signal source includes industry/category data:

```
# SIC Codes (if applicable)
CONSUMER_CPG_CODES = ["2000", "2011", ...]
CONSUMER_HEALTH_CODES = ["8000", "8011", ...]

# Or custom classification
CATEGORIES_MAP = {
    "food": "consumer_cpg",
    "fitness": "consumer_health",
    ...
}
```

## 3. Confidence Scoring Formula

Define how confidence is calculated:

```python
def _calculate_confidence(self, data) -> float:
    base = [YOUR_BASE_CONFIDENCE]  # e.g., 0.7

    # Boosts
    if [CONDITION_1]: base += [BOOST_AMOUNT]
    if [CONDITION_2]: base += [BOOST_AMOUNT]

    # Penalties
    if [CONDITION_3]: base -= [PENALTY_AMOUNT]

    return min(1.0, max(0.0, base))
```

**Example values:**
- Base: 0.5-0.8 (higher if authoritative source)
- Boost: 0.05-0.2 per factor
- Penalty: 0.05-0.15 per factor

## 4. Canonical Key Strategy

Define deduplication priority:

```python
canonical_keys = build_canonical_key_candidates(
    domain_or_website=[WEBSITE_FIELD],           # Priority 1
    companies_house_number=[UK_NUMBER],          # Priority 2 (if UK)
    crunchbase_id=[CRUNCHBASE_ID],               # Priority 3
    github_org=[GITHUB_ORG],                     # Priority 4 (if applicable)
    fallback_company_name=[COMPANY_NAME],        # Priority 5
    fallback_region=[LOCATION]                   # Priority 6
)
```

## 5. Python Implementation

```python
import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any
import httpx

from collectors.base import BaseCollector
from verification.verification_gate_v2 import Signal, VerificationStatus
from utils.canonical_keys import build_canonical_key_candidates
from collectors.provenance import create_provenance, hash_response

# Your data class
from dataclasses import dataclass

@dataclass
class YourDataClass:
    """Structured data for your signal source."""
    unique_id: str
    company_name: str
    event_date: datetime
    url: str
    # Add your fields here
    raw_data: Dict[str, Any]

    def to_signal(self, collector_name: str) -> Signal:
        """Convert to Signal object."""
        # Calculate confidence
        confidence = self._calculate_confidence()

        # Build canonical keys
        canonical_keys = build_canonical_key_candidates(
            domain_or_website=self.url,  # Customize this
            fallback_company_name=self.company_name
        )

        # Create provenance
        provenance = create_provenance(
            source_url=self.url,
            response_data=self.raw_data,
            endpoint="/your/endpoint",
            query_params={"param": "value"}
        )

        return Signal(
            id=f"{collector_name}_{self.unique_id}",
            signal_type="YOUR_SIGNAL_TYPE",  # Choose from list above
            confidence=confidence,
            source_api=collector_name,
            source_url=self.url,
            source_response_hash=hash_response(self.raw_data),
            detected_at=self.event_date,
            retrieved_at=datetime.now(timezone.utc),
            verification_status=VerificationStatus.SINGLE_SOURCE,
            verified_by_sources=[collector_name],
            raw_data={
                **self.__dict__,
                "canonical_key": canonical_keys[0],
                "canonical_key_candidates": canonical_keys,
                **provenance,
            }
        )

    def _calculate_confidence(self) -> float:
        """Calculate confidence score."""
        base = 0.7  # YOUR BASE CONFIDENCE

        # Add your boost/penalty logic here

        return min(1.0, max(0.0, base))


class YourCollector(BaseCollector):
    """Collector for [YOUR SOURCE]."""

    SOURCE_TYPE = "your_collector"

    def __init__(
        self,
        store=None,
        api_key: Optional[str] = None,
        lookback_days: int = 30,
        max_items: int = 100,
    ):
        super().__init__(store=store, collector_name=self.SOURCE_TYPE)
        self.api_key = api_key or os.environ.get("YOUR_API_KEY")
        self.lookback_days = lookback_days
        self.max_items = max_items
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Initialize async client."""
        self._client = httpx.AsyncClient(
            base_url="YOUR_API_BASE",
            headers={
                "Authorization": f"Bearer {self.api_key}",  # Or your auth method
                "User-Agent": "DiscoveryEngine/1.0"
            },
            timeout=30.0
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close async client."""
        if self._client:
            await self._client.aclose()

    async def _collect_signals(self) -> List[Signal]:
        """Main collection logic."""
        # Step 2: FETCH
        raw_items = await self._fetch_raw_data()

        # Step 3: ENRICH
        enriched_items = []
        for raw_item in raw_items:
            try:
                enriched = self._enrich_item(raw_item)
                enriched_items.append(enriched)
            except Exception as e:
                self._errors.append(str(e))
                continue

        # Step 4: CONVERT
        signals = [item.to_signal(self.SOURCE_TYPE) for item in enriched_items]

        # Step 5: PERSIST (handled by BaseCollector.run())
        return signals

    async def _fetch_raw_data(self) -> List[Dict]:
        """Fetch raw data from API."""
        items = []
        page = 1

        while len(items) < self.max_items:
            # Rate limiting
            await self.rate_limiter.acquire()

            # HTTP request
            try:
                response = await self._client.get(
                    "/your/endpoint",
                    params={
                        "page": page,
                        "per_page": 50,
                        # Add your params
                    }
                )
                response.raise_for_status()
                page_items = response.json()["items"]  # Adjust to your response structure

                items.extend(page_items)

                # Pagination check
                if len(page_items) < 50:  # Adjust to your page size
                    break

                page += 1

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    break  # No more pages
                elif e.response.status_code == 429:
                    # Rate limited, wait and retry
                    await asyncio.sleep(60)
                    continue
                else:
                    raise

        return items[:self.max_items]

    def _enrich_item(self, raw_item: Dict) -> YourDataClass:
        """Parse and enrich raw item."""
        return YourDataClass(
            unique_id=raw_item["id"],
            company_name=raw_item["company"]["name"],
            event_date=datetime.fromisoformat(raw_item["created_at"]),
            url=raw_item["url"],
            # Map your fields
            raw_data=raw_item
        )


# Example usage
async def main():
    from storage.signal_store import SignalStore

    store = await SignalStore.create()

    async with YourCollector(store=store, api_key="xxx") as collector:
        result = await collector.run(dry_run=True)
        print(f"Found {result.signals_found} signals")

if __name__ == "__main__":
    asyncio.run(main())
```

## 6. Integration

### Add to MCP Server

Edit `discovery_engine/mcp_server.py`:

```python
ALLOWED_COLLECTORS = [
    # ... existing collectors
    "your_collector",  # ADD THIS
]
```

### Add to CLI

The collector will automatically appear in:
```bash
python run_pipeline.py collect --collectors your_collector
```

## 7. Testing Checklist

- [ ] Dry-run mode works: `--dry-run` flag
- [ ] Pagination completes successfully
- [ ] At least one signal generated
- [ ] Confidence score calculated (0.0-1.0)
- [ ] Canonical keys present in raw_data
- [ ] Provenance block included
- [ ] No crashes on malformed API responses
- [ ] Rate limiting respected (no 429 errors)

## 8. Common Pitfalls

**Missing authentication:**
```python
# WRONG: Forgetting to add API key to headers
headers = {}

# RIGHT: Include authentication
headers = {"Authorization": f"Bearer {self.api_key}"}
```

**Not handling pagination:**
```python
# WRONG: Only fetching first page
items = await self._fetch_page(1)

# RIGHT: Loop until no more items
while len(items) < max_items:
    page_items = await self._fetch_page(page)
    if not page_items: break
```

**Skipping rate limiting:**
```python
# WRONG: Making requests without rate limit check
response = await self._client.get(url)

# RIGHT: Always acquire rate limit token
await self.rate_limiter.acquire()
response = await self._client.get(url)
```

**Hardcoding confidence:**
```python
# WRONG: Same confidence for all signals
confidence = 0.7

# RIGHT: Calculate based on signal quality
confidence = self._calculate_confidence(data)
```

## 9. Deployment

1. Write collector code in `collectors/your_collector.py`
2. Add to `ALLOWED_COLLECTORS` in `mcp_server.py`
3. Add API key to `.env` if needed
4. Test with `--dry-run`
5. Run full test: `python run_pipeline.py collect --collectors your_collector`
6. Verify signals in database: `python run_pipeline.py stats`

**Estimated time:** 2-4 hours using this template
