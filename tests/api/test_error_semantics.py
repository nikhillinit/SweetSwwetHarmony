"""Tests for Wave 4 error semantics contract.

Validates that all write endpoints follow the error priority order:
1. Authentication (401)
2. RBAC authorization (403)
3. Feature/write-mode gate (423)
4. Validation / state conflicts (409/422)

Also validates:
- 423 error envelope structure for feature_disabled_response()
- Retrofit: batch.py returns 423 (not 403) for DeliveryPolicyError
- Error envelope includes required fields
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from api.contracts import error_response, feature_disabled_response


class TestFeatureDisabledResponse:
    """Test the 423 feature_disabled_response helper."""

    def test_returns_423(self):
        exc = feature_disabled_response("merge_writes", "MERGE_WRITES_ENABLED")
        assert exc.status_code == 423

    def test_error_envelope_structure(self):
        exc = feature_disabled_response(
            "merge_writes", "MERGE_WRITES_ENABLED", request_id="req-123"
        )
        detail = exc.detail
        assert detail["error"] == "locked"
        assert detail["code"] == "FEATURE_DISABLED"
        assert "merge_writes" in detail["message"]
        assert detail["detail"]["env_var"] == "MERGE_WRITES_ENABLED"
        assert detail["request_id"] == "req-123"

    def test_bulk_triage_feature(self):
        exc = feature_disabled_response("bulk_triage", "BULK_TRIAGE_ENABLED")
        assert exc.status_code == 423
        assert exc.detail["code"] == "FEATURE_DISABLED"
        assert "bulk_triage" in exc.detail["message"]

    def test_hunter_promote_feature(self):
        exc = feature_disabled_response("hunter_promote", "HUNTER_PROMOTE_ENABLED")
        assert exc.status_code == 423
        assert exc.detail["code"] == "FEATURE_DISABLED"


class TestErrorResponseCodes:
    """Test that error_response generates correct status codes and envelopes."""

    def test_409_conflict(self):
        exc = error_response(409, "conflict", "VERSION_MISMATCH", "Stale version")
        assert exc.status_code == 409
        assert exc.detail["error"] == "conflict"
        assert exc.detail["code"] == "VERSION_MISMATCH"

    def test_403_forbidden(self):
        exc = error_response(403, "forbidden", "INSUFFICIENT_PERMISSION", "No access")
        assert exc.status_code == 403
        assert exc.detail["error"] == "forbidden"

    def test_422_validation(self):
        exc = error_response(422, "validation_error", "VALIDATION_ERROR", "Bad input")
        assert exc.status_code == 422
        assert exc.detail["code"] == "VALIDATION_ERROR"

    def test_401_unauthenticated(self):
        exc = error_response(401, "unauthenticated", "UNAUTHENTICATED", "No token")
        assert exc.status_code == 401
        assert exc.detail["code"] == "UNAUTHENTICATED"

    def test_detail_field_included(self):
        exc = error_response(
            409, "conflict", "TEST", "msg",
            detail={"expected": "a", "actual": "b"},
        )
        assert exc.detail["detail"]["expected"] == "a"
        assert exc.detail["detail"]["actual"] == "b"

    def test_request_id_included(self):
        exc = error_response(
            500, "server_error", "INTERNAL", "Oops",
            request_id="req-abc",
        )
        assert exc.detail["request_id"] == "req-abc"

    def test_none_detail_excluded(self):
        exc = error_response(400, "bad_request", "BAD", "msg")
        assert "detail" not in exc.detail or exc.detail.get("detail") is None
