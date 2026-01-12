# Mini-Scout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Mini-Scout page to the existing Streamlit dashboard enabling non-technical team members to browse signals, filter by thesis criteria, and perform fuzzy searches.

**Architecture:** Extend the existing Streamlit dashboard with a new page, add SQLite FTS5 for fuzzy search, and implement saved filter presets. No new services or deployment changes required.

**Tech Stack:** Streamlit (existing), SQLite FTS5, pytest-asyncio

---

## Overview

### What We're Building

A "Mini-Scout" page in the existing dashboard with:
- Fuzzy search (partial names, keywords)
- Full thesis filtering (vertical, confidence, source, date, signal type)
- Saved filter presets ("Travel Series A prospects")
- Company drill-down view
- CSV export

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Extend existing Streamlit dashboard | Reuses styling, caching, single deployment |
| SQLite FTS5 for search | Built-in, fast, supports fuzzy/ranked results |
| Button-triggered search (not real-time) | Streamlit lacks debouncing; simpler UX |
| Vertical card list (not grid) | Matches existing dashboard pattern |
| Lazy-load raw_data | Performance with large result sets |

### Files to Create/Modify

| File | Action |
|------|--------|
| `storage/signal_store.py` | Add FTS5 table, presets table, search methods |
| `dashboard/app.py` | Add Mini-Scout page, filter components |
| `dashboard/mini_scout.py` | New file for Mini-Scout page logic |
| `tests/storage/test_signal_search.py` | FTS5 and search tests |
| `tests/storage/test_filter_presets.py` | Preset CRUD tests |
| `tests/dashboard/test_mini_scout_filters.py` | Filter logic tests |

---

## Task 1: Add FTS5 Virtual Table Schema

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_signal_search.py`

**Step 1: Write failing test for FTS table creation**

```python
# tests/storage/test_signal_search.py
import pytest
from storage.signal_store import SignalStore


class TestFTSSetup:
    """Test FTS5 virtual table creation."""

    async def test_fts_table_exists_after_init(self, temp_db):
        """FTS table should be created during initialization."""
        store = SignalStore(db_path=temp_db)
        await store.initialize()

        async with store.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signals_fts'"
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "signals_fts"
        await store.close()
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/storage/test_signal_search.py::TestFTSSetup::test_fts_table_exists_after_init -v
```

Expected: FAIL with "AssertionError: assert None is not None"

**Step 3: Implement FTS5 table creation**

```python
# Add to SignalStore class in storage/signal_store.py

async def _create_fts_table(self, conn):
    """Create FTS5 virtual table for fuzzy search."""
    await conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS signals_fts USING fts5(
            signal_id UNINDEXED,
            company_name,
            searchable_text,
            vertical,
            source_api,
            tokenize='porter unicode61'
        )
    """)
```

Add call in `initialize()` method:

```python
# In initialize() method, after other table creation
await self._create_fts_table(conn)
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/storage/test_signal_search.py::TestFTSSetup::test_fts_table_exists_after_init -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_signal_search.py
git commit -m "feat(storage): add FTS5 virtual table for signal search"
```

---

## Task 2: Add FTS Indexing Methods

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_signal_search.py`

**Step 1: Write failing test for indexing a signal**

```python
# Add to tests/storage/test_signal_search.py

class TestFTSIndexing:
    """Test FTS5 indexing operations."""

    async def test_index_signal_adds_to_fts(self, temp_db):
        """Indexing a signal should add it to FTS table."""
        store = SignalStore(db_path=temp_db)
        await store.initialize()

        # Create a test signal first
        signal_id = "test-signal-001"
        await store.store_signal(
            signal_id=signal_id,
            company_name="Telehealth Plus",
            signal_type="funding",
            source_api="producthunt",
            raw_data={"description": "Virtual care platform for remote patients"},
            confidence=0.85,
            vertical="health"
        )

        # Index it
        await store.index_signal_for_search(signal_id)

        # Verify in FTS
        async with store.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT company_name FROM signals_fts WHERE signal_id = ?",
                (signal_id,)
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] == "Telehealth Plus"
        await store.close()
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/storage/test_signal_search.py::TestFTSIndexing::test_index_signal_adds_to_fts -v
```

Expected: FAIL with "AttributeError: 'SignalStore' object has no attribute 'index_signal_for_search'"

**Step 3: Implement indexing method**

```python
# Add to SignalStore class in storage/signal_store.py

def _build_searchable_text(self, raw_data: dict) -> str:
    """Extract searchable text from raw_data JSON."""
    searchable_parts = []

    # Common fields to extract
    for field in ['description', 'summary', 'tagline', 'tags', 'keywords', 'category']:
        if field in raw_data:
            value = raw_data[field]
            if isinstance(value, list):
                searchable_parts.extend(str(v) for v in value)
            else:
                searchable_parts.append(str(value))

    return " ".join(searchable_parts)


async def index_signal_for_search(self, signal_id: str) -> None:
    """Add or update a signal in the FTS index."""
    async with self.get_connection() as conn:
        # Fetch signal data
        cursor = await conn.execute(
            "SELECT company_name, raw_data, source_api FROM signals WHERE signal_id = ?",
            (signal_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return

        company_name, raw_data_json, source_api = row
        raw_data = json.loads(raw_data_json) if raw_data_json else {}
        searchable_text = self._build_searchable_text(raw_data)

        # Get vertical from processing table or default
        cursor = await conn.execute(
            "SELECT vertical FROM signal_processing WHERE signal_id = ?",
            (signal_id,)
        )
        vertical_row = await cursor.fetchone()
        vertical = vertical_row[0] if vertical_row else "unknown"

        # Upsert into FTS (delete then insert)
        await conn.execute("DELETE FROM signals_fts WHERE signal_id = ?", (signal_id,))
        await conn.execute(
            "INSERT INTO signals_fts (signal_id, company_name, searchable_text, vertical, source_api) VALUES (?, ?, ?, ?, ?)",
            (signal_id, company_name, searchable_text, vertical, source_api)
        )
        await conn.commit()
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/storage/test_signal_search.py::TestFTSIndexing::test_index_signal_adds_to_fts -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_signal_search.py
git commit -m "feat(storage): add FTS indexing method with searchable text extraction"
```

---

## Task 3: Add Fuzzy Search Method

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_signal_search.py`

**Step 1: Write failing tests for fuzzy search**

```python
# Add to tests/storage/test_signal_search.py

class TestFuzzySearch:
    """Test FTS5 fuzzy search functionality."""

    @pytest.fixture
    async def populated_store(self, temp_db):
        """Store with indexed test signals."""
        store = SignalStore(db_path=temp_db)
        await store.initialize()

        test_signals = [
            ("sig-1", "Telehealth Plus", "health", "producthunt", {"description": "Virtual care platform"}),
            ("sig-2", "Teladoc Health", "health", "sec_edgar", {"description": "Telemedicine services"}),
            ("sig-3", "TravelTech Pro", "travel", "g2crowd", {"description": "Hotel booking software"}),
            ("sig-4", "SaaSify", "saas", "capterra", {"description": "B2B subscription platform"}),
        ]

        for sig_id, name, vertical, source, raw_data in test_signals:
            await store.store_signal(
                signal_id=sig_id,
                company_name=name,
                signal_type="funding",
                source_api=source,
                raw_data=raw_data,
                confidence=0.8,
                vertical=vertical
            )
            await store.index_signal_for_search(sig_id)

        yield store
        await store.close()

    async def test_partial_name_match(self, populated_store):
        """'tele' should match 'Telehealth Plus' and 'Teladoc Health'."""
        results = await populated_store.search_signals_fts("tele")

        assert len(results) == 2
        names = [r["company_name"] for r in results]
        assert "Telehealth Plus" in names
        assert "Teladoc Health" in names

    async def test_description_match(self, populated_store):
        """'virtual care' should match signal with that description."""
        results = await populated_store.search_signals_fts("virtual care")

        assert len(results) >= 1
        assert results[0]["company_name"] == "Telehealth Plus"

    async def test_no_match_returns_empty(self, populated_store):
        """Non-matching query returns empty list."""
        results = await populated_store.search_signals_fts("xyznonexistent")

        assert results == []

    async def test_results_include_rank(self, populated_store):
        """Results should include relevance rank."""
        results = await populated_store.search_signals_fts("health")

        assert len(results) > 0
        assert "rank" in results[0]
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/storage/test_signal_search.py::TestFuzzySearch -v
```

Expected: FAIL with "AttributeError: 'SignalStore' object has no attribute 'search_signals_fts'"

**Step 3: Implement fuzzy search method**

```python
# Add to SignalStore class in storage/signal_store.py

async def search_signals_fts(
    self,
    query: str,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Fuzzy search signals using FTS5.

    Args:
        query: Search query (supports partial matches, phrases in quotes)
        limit: Maximum results to return

    Returns:
        List of matching signals with relevance rank
    """
    if not query or not query.strip():
        return []

    # Escape special FTS5 characters and prepare query
    # Add * for prefix matching to enable fuzzy search
    safe_query = query.strip().replace('"', '""')
    fts_query = f'"{safe_query}"*'

    async with self.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = await conn.execute("""
            SELECT
                f.signal_id,
                f.company_name,
                f.vertical,
                f.source_api,
                s.confidence,
                s.signal_type,
                s.created_at,
                bm25(signals_fts) as rank
            FROM signals_fts f
            JOIN signals s ON f.signal_id = s.signal_id
            WHERE signals_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit))

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/storage/test_signal_search.py::TestFuzzySearch -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_signal_search.py
git commit -m "feat(storage): add FTS5 fuzzy search with relevance ranking"
```

---

## Task 4: Add FTS Edge Case Handling

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_signal_search.py`

**Step 1: Write failing tests for edge cases**

```python
# Add to tests/storage/test_signal_search.py

class TestFTSEdgeCases:
    """Test FTS5 edge cases and security."""

    async def test_unicode_search(self, populated_store):
        """Search with Unicode characters works."""
        # Add signal with Unicode
        await populated_store.store_signal(
            signal_id="unicode-1",
            company_name="Café Health",
            signal_type="funding",
            source_api="producthunt",
            raw_data={"description": "Héalthy café services"},
            confidence=0.8
        )
        await populated_store.index_signal_for_search("unicode-1")

        results = await populated_store.search_signals_fts("café")
        assert len(results) >= 1

    async def test_special_characters_safe(self, populated_store):
        """Special characters don't break search."""
        # These shouldn't crash
        results = await populated_store.search_signals_fts('test"query')
        assert isinstance(results, list)

        results = await populated_store.search_signals_fts("test'query")
        assert isinstance(results, list)

        results = await populated_store.search_signals_fts("test*query")
        assert isinstance(results, list)

    async def test_very_long_query_handled(self, populated_store):
        """Very long queries don't crash."""
        long_query = "a" * 1000
        results = await populated_store.search_signals_fts(long_query)
        assert isinstance(results, list)

    async def test_empty_query_returns_empty(self, populated_store):
        """Empty query returns empty list, not all results."""
        results = await populated_store.search_signals_fts("")
        assert results == []

        results = await populated_store.search_signals_fts("   ")
        assert results == []
```

**Step 2: Run tests, identify failures, fix search method**

Update `search_signals_fts` to handle edge cases:

```python
# Update in storage/signal_store.py

async def search_signals_fts(
    self,
    query: str,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """Fuzzy search signals using FTS5."""
    if not query or not query.strip():
        return []

    query = query.strip()

    # Limit query length
    if len(query) > 500:
        query = query[:500]

    # Escape special FTS5 characters
    # FTS5 special chars: " * - + ( ) : ^
    safe_query = query.replace('"', ' ').replace('*', ' ').replace('-', ' ')
    safe_query = safe_query.replace('+', ' ').replace('(', ' ').replace(')', ' ')
    safe_query = safe_query.replace(':', ' ').replace('^', ' ')
    safe_query = ' '.join(safe_query.split())  # Normalize whitespace

    if not safe_query:
        return []

    # Add * for prefix matching
    fts_query = f'{safe_query}*'

    try:
        async with self.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = await conn.execute("""
                SELECT
                    f.signal_id,
                    f.company_name,
                    f.vertical,
                    f.source_api,
                    s.confidence,
                    s.signal_type,
                    s.created_at,
                    bm25(signals_fts) as rank
                FROM signals_fts f
                JOIN signals s ON f.signal_id = s.signal_id
                WHERE signals_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (fts_query, limit))

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"FTS search failed for query '{query[:50]}': {e}")
        return []
```

**Step 3: Run tests to verify they pass**

```bash
pytest tests/storage/test_signal_search.py::TestFTSEdgeCases -v
```

**Step 4: Commit**

```bash
git add storage/signal_store.py tests/storage/test_signal_search.py
git commit -m "feat(storage): add FTS edge case handling for special chars and long queries"
```

---

## Task 5: Add Filter Presets Table

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_filter_presets.py`

**Step 1: Write failing test for presets table**

```python
# tests/storage/test_filter_presets.py
import pytest
import json
from storage.signal_store import SignalStore


class TestPresetsTable:
    """Test filter presets table creation."""

    async def test_presets_table_exists(self, temp_db):
        """Presets table should be created during initialization."""
        store = SignalStore(db_path=temp_db)
        await store.initialize()

        async with store.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='filter_presets'"
            )
            row = await cursor.fetchone()

        assert row is not None
        await store.close()
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/storage/test_filter_presets.py::TestPresetsTable -v
```

**Step 3: Implement presets table**

```python
# Add to SignalStore class in storage/signal_store.py

async def _create_filter_presets_table(self, conn):
    """Create filter presets table for saved searches."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS filter_presets (
            preset_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            filters TEXT NOT NULL,
            schema_version INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            last_used TIMESTAMP
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_presets_name ON filter_presets(name)"
    )
```

Add call in `initialize()`:

```python
await self._create_filter_presets_table(conn)
```

**Step 4: Run test to verify it passes**

```bash
pytest tests/storage/test_filter_presets.py::TestPresetsTable -v
```

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_filter_presets.py
git commit -m "feat(storage): add filter_presets table with schema versioning"
```

---

## Task 6: Add Preset CRUD Methods

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_filter_presets.py`

**Step 1: Write failing tests for CRUD operations**

```python
# Add to tests/storage/test_filter_presets.py

class TestPresetCRUD:
    """Test preset create, read, update, delete operations."""

    @pytest.fixture
    async def store(self, temp_db):
        store = SignalStore(db_path=temp_db)
        await store.initialize()
        yield store
        await store.close()

    async def test_save_preset(self, store):
        """Should save a new preset."""
        filters = {"vertical": "health", "min_confidence": 0.7}
        preset_id = await store.save_filter_preset("Health High Confidence", filters)

        assert preset_id is not None

    async def test_load_preset(self, store):
        """Should load a saved preset."""
        filters = {"vertical": "travel", "sources": ["g2crowd"]}
        await store.save_filter_preset("Travel G2", filters)

        loaded = await store.load_filter_preset("Travel G2")

        assert loaded is not None
        assert loaded["filters"]["vertical"] == "travel"

    async def test_load_updates_last_used(self, store):
        """Loading a preset should update last_used timestamp."""
        await store.save_filter_preset("Test Preset", {"vertical": "saas"})

        await store.load_filter_preset("Test Preset")
        preset = await store.load_filter_preset("Test Preset")

        assert preset["last_used"] is not None

    async def test_list_presets(self, store):
        """Should list all presets."""
        await store.save_filter_preset("Preset A", {"a": 1})
        await store.save_filter_preset("Preset B", {"b": 2})

        presets = await store.list_filter_presets()

        assert len(presets) == 2
        names = [p["name"] for p in presets]
        assert "Preset A" in names
        assert "Preset B" in names

    async def test_delete_preset(self, store):
        """Should delete a preset."""
        await store.save_filter_preset("To Delete", {"x": 1})

        await store.delete_filter_preset("To Delete")

        presets = await store.list_filter_presets()
        names = [p["name"] for p in presets]
        assert "To Delete" not in names

    async def test_duplicate_name_raises(self, store):
        """Should raise error on duplicate preset name."""
        await store.save_filter_preset("Duplicate", {"a": 1})

        with pytest.raises(ValueError, match="already exists"):
            await store.save_filter_preset("Duplicate", {"b": 2})

    async def test_update_preset(self, store):
        """Should update existing preset filters."""
        await store.save_filter_preset("Updatable", {"old": True})

        await store.update_filter_preset("Updatable", {"new": True})

        loaded = await store.load_filter_preset("Updatable")
        assert loaded["filters"]["new"] is True
        assert "old" not in loaded["filters"]
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/storage/test_filter_presets.py::TestPresetCRUD -v
```

**Step 3: Implement CRUD methods**

```python
# Add to SignalStore class in storage/signal_store.py

async def save_filter_preset(self, name: str, filters: Dict[str, Any]) -> str:
    """Save a new filter preset."""
    import uuid

    async with self.get_connection() as conn:
        # Check for duplicate
        cursor = await conn.execute(
            "SELECT preset_id FROM filter_presets WHERE name = ?", (name,)
        )
        if await cursor.fetchone():
            raise ValueError(f"Preset '{name}' already exists")

        preset_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        await conn.execute("""
            INSERT INTO filter_presets (preset_id, name, filters, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (preset_id, name, json.dumps(filters), now, now))
        await conn.commit()

        return preset_id


async def load_filter_preset(self, name: str) -> Optional[Dict[str, Any]]:
    """Load a filter preset by name, updating last_used."""
    async with self.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = await conn.execute(
            "SELECT * FROM filter_presets WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        # Update last_used
        now = datetime.now(timezone.utc)
        await conn.execute(
            "UPDATE filter_presets SET last_used = ? WHERE name = ?",
            (now, name)
        )
        await conn.commit()

        result = dict(row)
        result["filters"] = json.loads(result["filters"])
        return result


async def list_filter_presets(self) -> List[Dict[str, Any]]:
    """List all filter presets."""
    async with self.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = await conn.execute(
            "SELECT preset_id, name, created_at, last_used FROM filter_presets ORDER BY name"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def delete_filter_preset(self, name: str) -> None:
    """Delete a filter preset by name."""
    async with self.get_connection() as conn:
        await conn.execute("DELETE FROM filter_presets WHERE name = ?", (name,))
        await conn.commit()


async def update_filter_preset(self, name: str, filters: Dict[str, Any]) -> None:
    """Update filters for an existing preset."""
    async with self.get_connection() as conn:
        now = datetime.now(timezone.utc)
        await conn.execute("""
            UPDATE filter_presets SET filters = ?, updated_at = ? WHERE name = ?
        """, (json.dumps(filters), now, name))
        await conn.commit()
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/storage/test_filter_presets.py::TestPresetCRUD -v
```

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_filter_presets.py
git commit -m "feat(storage): add filter preset CRUD methods"
```

---

## Task 7: Add Filtered Signal Query Method

**Files:**
- Modify: `storage/signal_store.py`
- Test: `tests/storage/test_signal_search.py`

**Step 1: Write failing tests for filtered queries**

```python
# Add to tests/storage/test_signal_search.py

class TestFilteredSignalQuery:
    """Test signal filtering by various criteria."""

    @pytest.fixture
    async def store_with_signals(self, temp_db):
        """Store with diverse test signals."""
        store = SignalStore(db_path=temp_db)
        await store.initialize()

        signals = [
            ("s1", "HealthCo", "health", "producthunt", 0.9, "funding"),
            ("s2", "TravelApp", "travel", "g2crowd", 0.7, "launch"),
            ("s3", "SaaSPro", "saas", "capterra", 0.5, "funding"),
            ("s4", "HealthTech", "health", "sec_edgar", 0.6, "regulatory"),
            ("s5", "ConsumerBrand", "consumer", "producthunt", 0.8, "launch"),
        ]

        for sig_id, name, vertical, source, confidence, sig_type in signals:
            await store.store_signal(
                signal_id=sig_id,
                company_name=name,
                signal_type=sig_type,
                source_api=source,
                raw_data={},
                confidence=confidence,
                vertical=vertical
            )

        yield store
        await store.close()

    async def test_filter_by_vertical(self, store_with_signals):
        """Filter signals by vertical."""
        results = await store_with_signals.get_filtered_signals(verticals=["health"])

        assert len(results) == 2
        for r in results:
            assert r["vertical"] == "health"

    async def test_filter_by_confidence_range(self, store_with_signals):
        """Filter signals by confidence threshold."""
        results = await store_with_signals.get_filtered_signals(min_confidence=0.8)

        assert len(results) == 2
        for r in results:
            assert r["confidence"] >= 0.8

    async def test_filter_by_sources(self, store_with_signals):
        """Filter signals by source."""
        results = await store_with_signals.get_filtered_signals(sources=["producthunt"])

        assert len(results) == 2

    async def test_filter_by_signal_type(self, store_with_signals):
        """Filter signals by signal type."""
        results = await store_with_signals.get_filtered_signals(signal_types=["funding"])

        assert len(results) == 2

    async def test_combined_filters(self, store_with_signals):
        """Multiple filters combined with AND."""
        results = await store_with_signals.get_filtered_signals(
            verticals=["health"],
            min_confidence=0.8
        )

        assert len(results) == 1
        assert results[0]["company_name"] == "HealthCo"

    async def test_empty_filters_returns_all(self, store_with_signals):
        """No filters returns all signals."""
        results = await store_with_signals.get_filtered_signals()

        assert len(results) == 5
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/storage/test_signal_search.py::TestFilteredSignalQuery -v
```

**Step 3: Implement filtered query method**

```python
# Add to SignalStore class in storage/signal_store.py

async def get_filtered_signals(
    self,
    verticals: Optional[List[str]] = None,
    sources: Optional[List[str]] = None,
    signal_types: Optional[List[str]] = None,
    min_confidence: Optional[float] = None,
    max_confidence: Optional[float] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """
    Get signals matching filter criteria.

    All filters are ANDed together. Empty/None filters are ignored.
    """
    conditions = []
    params = []

    if verticals:
        placeholders = ",".join("?" * len(verticals))
        conditions.append(f"s.vertical IN ({placeholders})")
        params.extend(verticals)

    if sources:
        placeholders = ",".join("?" * len(sources))
        conditions.append(f"s.source_api IN ({placeholders})")
        params.extend(sources)

    if signal_types:
        placeholders = ",".join("?" * len(signal_types))
        conditions.append(f"s.signal_type IN ({placeholders})")
        params.extend(signal_types)

    if min_confidence is not None:
        conditions.append("s.confidence >= ?")
        params.append(min_confidence)

    if max_confidence is not None:
        conditions.append("s.confidence <= ?")
        params.append(max_confidence)

    if start_date:
        conditions.append("s.created_at >= ?")
        params.append(start_date)

    if end_date:
        conditions.append("s.created_at <= ?")
        params.append(end_date)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.extend([limit, offset])

    async with self.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = await conn.execute(f"""
            SELECT
                s.signal_id,
                s.company_name,
                s.signal_type,
                s.source_api,
                s.confidence,
                s.created_at,
                s.vertical
            FROM signals s
            WHERE {where_clause}
            ORDER BY s.created_at DESC
            LIMIT ? OFFSET ?
        """, params)

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/storage/test_signal_search.py::TestFilteredSignalQuery -v
```

**Step 5: Commit**

```bash
git add storage/signal_store.py tests/storage/test_signal_search.py
git commit -m "feat(storage): add filtered signal query with multiple criteria"
```

---

## Task 8: Create Mini-Scout Page Module

**Files:**
- Create: `dashboard/mini_scout.py`
- Modify: `dashboard/app.py`
- Test: Manual verification

**Step 1: Create the Mini-Scout page module**

```python
# dashboard/mini_scout.py
"""
Mini-Scout: Signal search and exploration interface.

Provides fuzzy search, thesis filtering, and saved presets for
non-technical team members to explore signals.
"""
import asyncio
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from storage.signal_store import SignalStore


def run_async(coro):
    """Helper to run async code in Streamlit."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def init_session_state():
    """Initialize session state for filters."""
    if "mini_scout_filters" not in st.session_state:
        st.session_state.mini_scout_filters = {
            "search_query": "",
            "verticals": [],
            "sources": [],
            "signal_types": [],
            "min_confidence": 0.0,
            "date_range": "all",
        }
    if "mini_scout_results" not in st.session_state:
        st.session_state.mini_scout_results = []
    if "mini_scout_result_count" not in st.session_state:
        st.session_state.mini_scout_result_count = 0


def render_search_bar():
    """Render the search input and button."""
    col1, col2 = st.columns([5, 1])

    with col1:
        query = st.text_input(
            "Search",
            value=st.session_state.mini_scout_filters["search_query"],
            placeholder="Search companies, keywords...",
            label_visibility="collapsed"
        )
        st.session_state.mini_scout_filters["search_query"] = query

    with col2:
        search_clicked = st.button("Search", type="primary", use_container_width=True)

    # Help tooltip
    st.caption('Tip: Use quotes for exact phrases, e.g. "series a funding"')

    return search_clicked


def render_filter_sidebar(store: SignalStore):
    """Render the filter controls in sidebar."""
    st.sidebar.markdown("### Filters")

    # Preset controls
    st.sidebar.markdown("**Saved Presets**")
    presets = run_async(store.list_filter_presets())
    preset_names = ["(None)"] + [p["name"] for p in presets]

    col1, col2 = st.sidebar.columns(2)
    with col1:
        selected_preset = st.selectbox("Load preset", preset_names, label_visibility="collapsed")
    with col2:
        if st.button("Save current"):
            _show_save_preset_dialog(store)

    if selected_preset != "(None)":
        preset = run_async(store.load_filter_preset(selected_preset))
        if preset:
            st.session_state.mini_scout_filters.update(preset["filters"])
            st.rerun()

    st.sidebar.markdown("---")

    # Vertical filter
    st.sidebar.markdown("**Vertical**")
    verticals = st.sidebar.multiselect(
        "Vertical",
        options=["health", "travel", "saas", "consumer", "unknown"],
        default=st.session_state.mini_scout_filters.get("verticals", []),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["verticals"] = verticals

    # Confidence filter
    st.sidebar.markdown("**Minimum Confidence**")
    min_conf = st.sidebar.slider(
        "Confidence",
        min_value=0.0,
        max_value=1.0,
        value=st.session_state.mini_scout_filters.get("min_confidence", 0.0),
        step=0.1,
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["min_confidence"] = min_conf

    # Source filter
    st.sidebar.markdown("**Source**")
    all_sources = ["producthunt", "g2crowd", "capterra", "sec_edgar", "companies_house", "github"]
    sources = st.sidebar.multiselect(
        "Source",
        options=all_sources,
        default=st.session_state.mini_scout_filters.get("sources", []),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["sources"] = sources

    # Date range filter
    st.sidebar.markdown("**Date Range**")
    date_options = {
        "all": "All time",
        "7d": "Last 7 days",
        "30d": "Last 30 days",
        "90d": "Last 90 days",
    }
    date_range = st.sidebar.radio(
        "Date",
        options=list(date_options.keys()),
        format_func=lambda x: date_options[x],
        index=list(date_options.keys()).index(
            st.session_state.mini_scout_filters.get("date_range", "all")
        ),
        label_visibility="collapsed"
    )
    st.session_state.mini_scout_filters["date_range"] = date_range

    # Clear filters button
    st.sidebar.markdown("---")
    if st.sidebar.button("Clear all filters"):
        st.session_state.mini_scout_filters = {
            "search_query": "",
            "verticals": [],
            "sources": [],
            "signal_types": [],
            "min_confidence": 0.0,
            "date_range": "all",
        }
        st.rerun()


def _show_save_preset_dialog(store: SignalStore):
    """Show dialog to save current filters as preset."""
    with st.sidebar.expander("Save Preset", expanded=True):
        preset_name = st.text_input("Preset name")
        if st.button("Save") and preset_name:
            try:
                run_async(store.save_filter_preset(
                    preset_name,
                    st.session_state.mini_scout_filters
                ))
                st.success(f"Saved '{preset_name}'")
            except ValueError as e:
                st.error(str(e))


def execute_search(store: SignalStore) -> List[Dict[str, Any]]:
    """Execute search with current filters."""
    filters = st.session_state.mini_scout_filters

    # Calculate date range
    start_date = None
    if filters["date_range"] != "all":
        days = int(filters["date_range"].replace("d", ""))
        start_date = datetime.now() - timedelta(days=days)

    # If search query, use FTS
    if filters["search_query"]:
        results = run_async(store.search_signals_fts(
            filters["search_query"],
            limit=500
        ))
        # Apply additional filters to FTS results
        if filters["verticals"]:
            results = [r for r in results if r.get("vertical") in filters["verticals"]]
        if filters["min_confidence"] > 0:
            results = [r for r in results if r.get("confidence", 0) >= filters["min_confidence"]]
        if filters["sources"]:
            results = [r for r in results if r.get("source_api") in filters["sources"]]
    else:
        # No search query, use filtered query
        results = run_async(store.get_filtered_signals(
            verticals=filters["verticals"] or None,
            sources=filters["sources"] or None,
            min_confidence=filters["min_confidence"] if filters["min_confidence"] > 0 else None,
            start_date=start_date,
            limit=500
        ))

    return results


def render_signal_card(signal: Dict[str, Any], store: SignalStore):
    """Render a single signal card."""
    # Confidence color
    conf = signal.get("confidence", 0)
    if conf >= 0.8:
        conf_color = "#10B981"  # Green
    elif conf >= 0.5:
        conf_color = "#F59E0B"  # Yellow
    else:
        conf_color = "#EF4444"  # Red

    # Vertical badge color
    vertical_colors = {
        "health": "#3B82F6",
        "travel": "#8B5CF6",
        "saas": "#EC4899",
        "consumer": "#F97316",
        "unknown": "#6B7280",
    }
    vert = signal.get("vertical", "unknown")
    vert_color = vertical_colors.get(vert, "#6B7280")

    with st.container():
        st.markdown(f"""
        <div style="border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 12px; background: #1a1a1a;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="font-family: 'DM Serif Display', serif; font-size: 1.2em; color: #fff;">{signal.get('company_name', 'Unknown')}</span>
                <div>
                    <span style="background: {vert_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; margin-right: 8px;">{vert.upper()}</span>
                    <span style="background: {conf_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em;">{conf:.0%}</span>
                </div>
            </div>
            <div style="color: #888; font-size: 0.9em;">
                {signal.get('source_api', '')} · {signal.get('signal_type', '')} · {str(signal.get('created_at', ''))[:10]}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Expandable raw data (lazy loaded)
        with st.expander("View details"):
            raw_data = run_async(store.get_signal_raw_data(signal["signal_id"]))
            if raw_data:
                st.json(raw_data)
            else:
                st.caption("No additional data available")


def render_results(results: List[Dict[str, Any]], store: SignalStore):
    """Render search results."""
    if not results:
        st.info("No signals match your search. Try broader terms or adjust filters.")
        return

    # Result count and export
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**{len(results)}** signals found")
    with col2:
        if st.button("Export CSV"):
            csv = _convert_to_csv(results)
            st.download_button(
                "Download",
                csv,
                "signals.csv",
                "text/csv",
                key="download_csv"
            )

    # Warning if truncated
    if len(results) >= 500:
        st.warning("Showing first 500 results. Narrow your search for more specific results.")

    st.markdown("---")

    # Render cards
    for signal in results:
        render_signal_card(signal, store)


def _convert_to_csv(results: List[Dict[str, Any]]) -> str:
    """Convert results to CSV string."""
    import csv
    import io

    if not results:
        return ""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
    return output.getvalue()


def render_mini_scout_page(store: SignalStore):
    """Main entry point for Mini-Scout page."""
    st.title("Mini-Scout")
    st.caption("Search and explore signals")

    init_session_state()
    render_filter_sidebar(store)

    search_clicked = render_search_bar()

    if search_clicked:
        with st.spinner("Searching..."):
            results = execute_search(store)
            st.session_state.mini_scout_results = results

    render_results(st.session_state.mini_scout_results, store)
```

**Step 2: Add Mini-Scout page to main app**

```python
# Add to dashboard/app.py

# Import at top
from dashboard.mini_scout import render_mini_scout_page

# Add to page navigation (find existing page selection code)
pages = ["Pipeline", "Signals", "Mini-Scout"]  # Add Mini-Scout

# Add to page routing
if selected_page == "Mini-Scout":
    render_mini_scout_page(store)
```

**Step 3: Verify manually**

```bash
cd C:\dev\Harmonic
streamlit run dashboard/app.py
```

Checklist:
- [ ] Mini-Scout page appears in navigation
- [ ] Search bar accepts input
- [ ] Filters render in sidebar
- [ ] Search returns results
- [ ] Signal cards display correctly
- [ ] Presets save and load

**Step 4: Commit**

```bash
git add dashboard/mini_scout.py dashboard/app.py
git commit -m "feat(dashboard): add Mini-Scout page with search and filtering"
```

---

## Task 9: Add Company Drill-Down View

**Files:**
- Modify: `dashboard/mini_scout.py`
- Modify: `storage/signal_store.py`

**Step 1: Add method to get all signals for a company**

```python
# Add to SignalStore in storage/signal_store.py

async def get_signals_for_company(self, company_name: str) -> List[Dict[str, Any]]:
    """Get all signals for a specific company."""
    async with self.get_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = await conn.execute("""
            SELECT
                signal_id, company_name, signal_type, source_api,
                confidence, created_at, vertical, raw_data
            FROM signals
            WHERE company_name = ?
            ORDER BY created_at DESC
        """, (company_name,))

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
```

**Step 2: Add drill-down dialog to Mini-Scout**

```python
# Add to dashboard/mini_scout.py

def render_company_detail(company_name: str, store: SignalStore):
    """Render detailed view of all signals for a company."""
    st.subheader(f"All signals for: {company_name}")

    signals = run_async(store.get_signals_for_company(company_name))

    if not signals:
        st.info("No signals found for this company.")
        return

    st.markdown(f"**{len(signals)} signals** from {len(set(s['source_api'] for s in signals))} sources")

    # Timeline view
    for signal in signals:
        with st.container():
            col1, col2 = st.columns([1, 4])
            with col1:
                st.caption(str(signal.get("created_at", ""))[:10])
            with col2:
                st.markdown(f"**{signal['signal_type']}** from {signal['source_api']}")
                st.caption(f"Confidence: {signal.get('confidence', 0):.0%}")

                with st.expander("Raw data"):
                    try:
                        import json
                        raw = json.loads(signal.get("raw_data", "{}"))
                        st.json(raw)
                    except:
                        st.text(signal.get("raw_data", ""))

        st.markdown("---")
```

Update `render_signal_card` to make company name clickable:

```python
# Update in render_signal_card function
# Replace the company name span with a button

if st.button(signal.get('company_name', 'Unknown'), key=f"company_{signal['signal_id']}"):
    st.session_state.selected_company = signal.get('company_name')
    st.rerun()
```

Update `render_mini_scout_page` to handle drill-down:

```python
def render_mini_scout_page(store: SignalStore):
    """Main entry point for Mini-Scout page."""
    init_session_state()

    # Check if viewing company detail
    if "selected_company" in st.session_state and st.session_state.selected_company:
        if st.button("← Back to search"):
            st.session_state.selected_company = None
            st.rerun()
        render_company_detail(st.session_state.selected_company, store)
        return

    # Normal search view
    st.title("Mini-Scout")
    # ... rest of the function
```

**Step 3: Test manually and commit**

```bash
git add dashboard/mini_scout.py storage/signal_store.py
git commit -m "feat(dashboard): add company drill-down view in Mini-Scout"
```

---

## Task 10: Add FTS Index Maintenance

**Files:**
- Modify: `storage/signal_store.py`
- Modify: `dashboard/mini_scout.py`

**Step 1: Add rebuild index method**

```python
# Add to SignalStore in storage/signal_store.py

async def rebuild_fts_index(self) -> int:
    """Rebuild entire FTS index from signals table. Returns count indexed."""
    async with self.get_connection() as conn:
        # Clear existing index
        await conn.execute("DELETE FROM signals_fts")

        # Get all signals
        cursor = await conn.execute("SELECT signal_id FROM signals")
        signal_ids = [row[0] for row in await cursor.fetchall()]

        await conn.commit()

    # Index each signal
    count = 0
    for signal_id in signal_ids:
        await self.index_signal_for_search(signal_id)
        count += 1

    return count


async def get_fts_index_stats(self) -> Dict[str, int]:
    """Get FTS index statistics."""
    async with self.get_connection() as conn:
        # Count signals
        cursor = await conn.execute("SELECT COUNT(*) FROM signals")
        total_signals = (await cursor.fetchone())[0]

        # Count indexed
        cursor = await conn.execute("SELECT COUNT(*) FROM signals_fts")
        indexed_signals = (await cursor.fetchone())[0]

        return {
            "total_signals": total_signals,
            "indexed_signals": indexed_signals,
            "unindexed": total_signals - indexed_signals
        }
```

**Step 2: Add index management to Mini-Scout sidebar**

```python
# Add to render_filter_sidebar in dashboard/mini_scout.py

# At bottom of sidebar
st.sidebar.markdown("---")
st.sidebar.markdown("**Search Index**")
stats = run_async(store.get_fts_index_stats())
st.sidebar.caption(f"Indexed: {stats['indexed_signals']} / {stats['total_signals']}")

if stats['unindexed'] > 0:
    st.sidebar.warning(f"{stats['unindexed']} signals not indexed")
    if st.sidebar.button("Rebuild index"):
        with st.spinner("Rebuilding search index..."):
            count = run_async(store.rebuild_fts_index())
            st.sidebar.success(f"Indexed {count} signals")
            st.rerun()
```

**Step 3: Commit**

```bash
git add storage/signal_store.py dashboard/mini_scout.py
git commit -m "feat(storage): add FTS index rebuild and stats methods"
```

---

## Verification Checklist

After completing all tasks, verify:

- [ ] FTS5 search finds partial matches ("tele" → "Telehealth")
- [ ] Filters narrow results correctly
- [ ] Presets save and load successfully
- [ ] Company drill-down shows all signals
- [ ] CSV export downloads correctly
- [ ] Search index rebuild works
- [ ] All tests pass: `pytest tests/storage/test_signal_search.py tests/storage/test_filter_presets.py -v`
- [ ] UI matches editorial theme (dark background, gold accents)

---

## Estimated Effort

| Task | Hours |
|------|-------|
| Task 1: FTS5 schema | 1h |
| Task 2: FTS indexing | 2h |
| Task 3: Fuzzy search | 2h |
| Task 4: Edge cases | 1.5h |
| Task 5: Presets table | 1h |
| Task 6: Preset CRUD | 2h |
| Task 7: Filtered queries | 2h |
| Task 8: Mini-Scout page | 6h |
| Task 9: Company drill-down | 2h |
| Task 10: Index maintenance | 1.5h |
| Integration + polish | 3h |
| **Total** | **24h** |
