"""
Cursor-based pagination for the Discovery Engine API.

Provides:
- CursorPage: Generic paginated result container
- encode_cursor / decode_cursor: Opaque cursor encoding
- paginate_query: SQL helper for cursor-based pagination

Cursors are opaque base64-encoded JSON payloads containing sort-key values.
Clients treat them as strings; only the server encodes/decodes.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Max page size to prevent accidental full-table pulls
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


class CursorParams(BaseModel):
    """Query parameters for cursor-based pagination."""

    cursor: Optional[str] = Field(
        default=None, description="Opaque cursor from previous response"
    )
    limit: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Page size (1-{MAX_PAGE_SIZE})",
    )


# =============================================================================
# CURSOR ENCODING
# =============================================================================

def encode_cursor(values: dict[str, Any]) -> str:
    """Encode sort-key values into an opaque cursor string.

    Args:
        values: Dict of column-name → value pairs defining the position.
                e.g. {"created_at": "2026-02-09T...", "id": 42}

    Returns:
        Base64-encoded cursor string.
    """
    payload = json.dumps(values, sort_keys=True, default=str)
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: Optional[str]) -> Optional[dict[str, Any]]:
    """Decode an opaque cursor string back to sort-key values.

    Returns None for empty/invalid cursors instead of raising.
    """
    if not cursor:
        return None
    try:
        payload = base64.urlsafe_b64decode(cursor.encode()).decode()
        return json.loads(payload)
    except Exception:
        logger.warning("Invalid cursor: %s", cursor[:40] if cursor else "")
        return None


# =============================================================================
# SQL PAGINATION HELPER
# =============================================================================

def paginate_query(
    base_sql: str,
    cursor_columns: list[str],
    cursor_values: Optional[dict[str, Any]],
    limit: int,
    descending: bool = True,
) -> tuple[str, list[Any]]:
    """Build a paginated SQL query from a base query and cursor state.

    Uses composite cursor pagination (keyset pagination) for stable,
    performant paging even on large tables.

    Args:
        base_sql: Base SELECT query (without ORDER BY / LIMIT).
                  May contain a WHERE clause.
        cursor_columns: Ordered list of columns forming the cursor key.
                        e.g. ["created_at", "id"]
        cursor_values: Decoded cursor dict (None for first page).
        limit: Number of rows to fetch.
        descending: Sort direction (True = newest first).

    Returns:
        Tuple of (sql_string, params_list).

    Example:
        sql, params = paginate_query(
            "SELECT * FROM signals WHERE status = ?",
            ["created_at", "id"],
            {"created_at": "2026-02-09", "id": 100},
            limit=50,
            descending=True,
        )
        # sql includes WHERE + ORDER BY + LIMIT
        # params includes status value + cursor values + limit
    """
    params: list[Any] = []
    sql = base_sql

    # Add cursor condition
    if cursor_values:
        op = "<" if descending else ">"
        # Composite cursor: (a, b) < (va, vb) ⟹ a < va OR (a = va AND b < vb)
        conditions = []
        for i in range(len(cursor_columns)):
            parts = []
            for j in range(i):
                col = cursor_columns[j]
                val = cursor_values.get(col)
                parts.append(f"{col} = ?")
                params.append(val)
            col = cursor_columns[i]
            val = cursor_values.get(col)
            parts.append(f"{col} {op} ?")
            params.append(val)
            conditions.append("(" + " AND ".join(parts) + ")")

        cursor_clause = " OR ".join(conditions)

        if "WHERE" in sql.upper():
            sql += f" AND ({cursor_clause})"
        else:
            sql += f" WHERE ({cursor_clause})"

    # Add ORDER BY
    direction = "DESC" if descending else "ASC"
    order_cols = ", ".join(f"{col} {direction}" for col in cursor_columns)
    sql += f" ORDER BY {order_cols}"

    # Add LIMIT (fetch one extra to detect has_more)
    sql += " LIMIT ?"
    params.append(limit + 1)

    return sql, params


def build_page_meta(
    rows: list[Any],
    limit: int,
    cursor_columns: list[str],
    row_to_dict: Any = None,
) -> tuple[list[Any], Optional[str], bool]:
    """Process raw rows into a page with cursor metadata.

    Args:
        rows: Raw rows from DB (fetched with limit+1).
        limit: Requested page size.
        cursor_columns: Columns used for cursor.
        row_to_dict: Optional callable to convert row to dict
                     (for extracting cursor values). If None,
                     rows must be dicts or support [] access.

    Returns:
        Tuple of (page_rows, next_cursor, has_more).
    """
    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor = None
    if has_more and page:
        last = page[-1]
        if row_to_dict:
            last = row_to_dict(last)

        cursor_vals = {}
        for col in cursor_columns:
            if isinstance(last, dict):
                cursor_vals[col] = last.get(col)
            else:
                cursor_vals[col] = getattr(last, col, None)

        next_cursor = encode_cursor(cursor_vals)

    return page, next_cursor, has_more
