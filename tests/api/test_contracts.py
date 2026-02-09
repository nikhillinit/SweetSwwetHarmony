"""Tests for API contracts — pagination, error envelope, idempotency."""

import pytest

from api.contracts import (
    BaseResponse,
    ErrorDetail,
    ListMeta,
    ListResponse,
    check_idempotency,
    check_version,
    clear_idempotency_cache,
    error_response,
    get_idempotency_key,
    inputs_hash,
    store_idempotency,
)
from api.pagination import (
    CursorParams,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    build_page_meta,
    decode_cursor,
    encode_cursor,
    paginate_query,
)
from fastapi import HTTPException


# ============================================================================
# Error Envelope
# ============================================================================


class TestErrorEnvelope:
    def test_error_detail_model(self):
        detail = ErrorDetail(
            error="not_found",
            code="SIGNAL_NOT_FOUND",
            message="Signal 42 not found",
        )
        assert detail.error == "not_found"
        assert detail.code == "SIGNAL_NOT_FOUND"
        assert detail.detail is None

    def test_error_detail_with_detail(self):
        detail = ErrorDetail(
            error="validation",
            code="INVALID_INPUT",
            message="Bad request",
            detail={"field": "email", "reason": "invalid format"},
        )
        assert detail.detail["field"] == "email"

    def test_error_response_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            raise error_response(404, "not_found", "SIG_404", "Signal not found")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["error"] == "not_found"
        assert exc_info.value.detail["code"] == "SIG_404"

    def test_error_response_with_request_id(self):
        with pytest.raises(HTTPException) as exc_info:
            raise error_response(
                500, "internal", "ERR", "Oops", request_id="abc-123"
            )
        assert exc_info.value.detail["request_id"] == "abc-123"


# ============================================================================
# Idempotency
# ============================================================================


class TestIdempotency:
    def setup_method(self):
        clear_idempotency_cache()

    def test_no_key_returns_none(self):
        assert check_idempotency(None) is None
        assert check_idempotency("") is None

    def test_unknown_key_returns_none(self):
        assert check_idempotency("never-seen") is None

    def test_store_and_retrieve(self):
        store_idempotency("key-1", 200, {"id": 42})
        result = check_idempotency("key-1")
        assert result is not None
        assert result.cached is True
        assert result.status_code == 200
        assert result.body == {"id": 42}

    def test_store_none_key_is_noop(self):
        store_idempotency(None, 200, {})
        assert check_idempotency(None) is None

    def test_duplicate_key_returns_same(self):
        store_idempotency("dup", 201, {"created": True})
        r1 = check_idempotency("dup")
        r2 = check_idempotency("dup")
        assert r1 is not None
        assert r2 is not None
        assert r1.body == r2.body


# ============================================================================
# Optimistic Concurrency (version check)
# ============================================================================


class TestVersionCheck:
    def test_none_expected_passes(self):
        # No version check if client doesn't send expected version
        check_version(None, "2026-02-09T00:00:00", "signal")

    def test_matching_versions_pass(self):
        ts = "2026-02-09T12:00:00+00:00"
        check_version(ts, ts, "signal")

    def test_mismatching_versions_raise_409(self):
        with pytest.raises(HTTPException) as exc_info:
            check_version(
                "2026-02-09T12:00:00", "2026-02-09T13:00:00", "signal"
            )
        assert exc_info.value.status_code == 409
        assert "VERSION_MISMATCH" in str(exc_info.value.detail)


# ============================================================================
# Response Models
# ============================================================================


class TestResponseModels:
    def test_base_response(self):
        resp = BaseResponse(data={"id": 1}, meta={"version": "1.0"})
        assert resp.data == {"id": 1}
        assert resp.meta["version"] == "1.0"

    def test_list_response(self):
        resp = ListResponse(
            data=[{"id": 1}, {"id": 2}],
            meta=ListMeta(total=100, next_cursor="abc", has_more=True),
        )
        assert len(resp.data) == 2
        assert resp.meta.has_more is True
        assert resp.meta.next_cursor == "abc"

    def test_list_meta_defaults(self):
        meta = ListMeta()
        assert meta.total is None
        assert meta.next_cursor is None
        assert meta.has_more is False


# ============================================================================
# Cursor Pagination
# ============================================================================


class TestCursorPagination:
    def test_encode_decode_roundtrip(self):
        values = {"created_at": "2026-02-09T12:00:00", "id": 42}
        cursor = encode_cursor(values)
        decoded = decode_cursor(cursor)
        assert decoded == values

    def test_decode_none(self):
        assert decode_cursor(None) is None
        assert decode_cursor("") is None

    def test_decode_invalid_returns_none(self):
        assert decode_cursor("not-valid-base64!!!") is None

    def test_cursor_params_defaults(self):
        params = CursorParams()
        assert params.cursor is None
        assert params.limit == DEFAULT_PAGE_SIZE

    def test_cursor_params_clamps(self):
        with pytest.raises(Exception):
            CursorParams(limit=MAX_PAGE_SIZE + 1)
        with pytest.raises(Exception):
            CursorParams(limit=0)


class TestPaginateQuery:
    def test_first_page_no_cursor(self):
        sql, params = paginate_query(
            "SELECT * FROM signals",
            ["created_at", "id"],
            cursor_values=None,
            limit=10,
        )
        assert "ORDER BY" in sql
        assert "LIMIT ?" in sql
        assert params == [11]  # limit + 1

    def test_with_cursor_desc(self):
        cursor = {"created_at": "2026-02-09", "id": 100}
        sql, params = paginate_query(
            "SELECT * FROM signals WHERE status = ?",
            ["created_at", "id"],
            cursor_values=cursor,
            limit=20,
        )
        assert "AND" in sql
        assert "<" in sql
        # Params: status value not in params (base query already has it)
        # Cursor params + limit
        assert 21 in params  # limit + 1

    def test_ascending_uses_gt(self):
        cursor = {"id": 5}
        sql, params = paginate_query(
            "SELECT * FROM signals",
            ["id"],
            cursor_values=cursor,
            limit=10,
            descending=False,
        )
        assert ">" in sql
        assert "ASC" in sql


class TestBuildPageMeta:
    def test_no_more_pages(self):
        rows = [{"id": 1}, {"id": 2}]
        page, cursor, has_more = build_page_meta(rows, limit=5, cursor_columns=["id"])
        assert len(page) == 2
        assert cursor is None
        assert has_more is False

    def test_has_more_pages(self):
        rows = [{"id": i} for i in range(6)]  # 6 rows, limit 5
        page, cursor, has_more = build_page_meta(rows, limit=5, cursor_columns=["id"])
        assert len(page) == 5
        assert cursor is not None
        assert has_more is True
        # Decode cursor to verify it points to last item
        decoded = decode_cursor(cursor)
        assert decoded["id"] == 4

    def test_empty_rows(self):
        page, cursor, has_more = build_page_meta([], limit=10, cursor_columns=["id"])
        assert len(page) == 0
        assert cursor is None
        assert has_more is False


# ============================================================================
# Utility
# ============================================================================


class TestInputsHash:
    def test_deterministic(self):
        h1 = inputs_hash("a", 1, [2, 3])
        h2 = inputs_hash("a", 1, [2, 3])
        assert h1 == h2

    def test_different_inputs(self):
        h1 = inputs_hash("a")
        h2 = inputs_hash("b")
        assert h1 != h2

    def test_length(self):
        h = inputs_hash("test")
        assert len(h) == 16
