"""Tests for failure classification (v2.4)."""

import pytest
from datetime import timedelta

from monitoring.failure_classifier import (
    FailureCategory,
    FailureClassifier,
    ClassifiedFailure,
    FailureCategoryConfig,
    classify_failure,
    DEFAULT_CATEGORY_CONFIG,
)


class TestFailureCategory:
    """Test failure category classification."""

    def test_transient_500(self):
        """500 status should classify as transient."""
        result = classify_failure(status_code=500, error=None)
        assert result.category == FailureCategory.TRANSIENT

    def test_transient_502(self):
        """502 Bad Gateway should classify as transient."""
        result = classify_failure(status_code=502, error=None)
        assert result.category == FailureCategory.TRANSIENT

    def test_transient_503(self):
        """503 Service Unavailable should classify as transient."""
        result = classify_failure(status_code=503, error=None)
        assert result.category == FailureCategory.TRANSIENT

    def test_transient_504(self):
        """504 Gateway Timeout should classify as transient."""
        result = classify_failure(status_code=504, error=None)
        assert result.category == FailureCategory.TRANSIENT

    def test_transient_timeout_error(self):
        """Timeout in error message should classify as transient."""
        result = classify_failure(status_code=None, error="Connection timeout")
        assert result.category == FailureCategory.TRANSIENT

    def test_transient_connection_reset(self):
        """Connection reset should classify as transient."""
        result = classify_failure(status_code=None, error="Connection reset by peer")
        assert result.category == FailureCategory.TRANSIENT

    def test_client_error_404(self):
        """404 Not Found should classify as client_error."""
        result = classify_failure(status_code=404, error=None)
        assert result.category == FailureCategory.CLIENT_ERROR

    def test_client_error_403(self):
        """403 Forbidden should classify as client_error."""
        result = classify_failure(status_code=403, error=None)
        assert result.category == FailureCategory.CLIENT_ERROR

    def test_client_error_410(self):
        """410 Gone should classify as client_error."""
        result = classify_failure(status_code=410, error=None)
        assert result.category == FailureCategory.CLIENT_ERROR

    def test_client_error_400(self):
        """400 Bad Request should classify as client_error."""
        result = classify_failure(status_code=400, error=None)
        assert result.category == FailureCategory.CLIENT_ERROR

    def test_rate_limited_429(self):
        """429 Too Many Requests should classify as rate_limited."""
        result = classify_failure(status_code=429, error=None)
        assert result.category == FailureCategory.RATE_LIMITED

    def test_rate_limited_stores_retry_after(self):
        """Rate limited should store Retry-After header value."""
        result = classify_failure(status_code=429, error=None, retry_after=120)
        assert result.category == FailureCategory.RATE_LIMITED
        assert result.retry_after_seconds == 120

    def test_ssl_error_certificate(self):
        """SSL certificate error in message."""
        result = classify_failure(status_code=None, error="SSL: CERTIFICATE_VERIFY_FAILED")
        assert result.category == FailureCategory.SSL_ERROR

    def test_ssl_error_handshake(self):
        """SSL handshake failure."""
        result = classify_failure(status_code=None, error="ssl handshake failure")
        assert result.category == FailureCategory.SSL_ERROR

    def test_ssl_error_tls(self):
        """TLS error should classify as SSL error."""
        result = classify_failure(status_code=None, error="TLS negotiation failed")
        assert result.category == FailureCategory.SSL_ERROR

    def test_content_error_decode(self):
        """Decoding error should classify as content_error."""
        result = classify_failure(status_code=200, error="UnicodeDecodeError")
        assert result.category == FailureCategory.CONTENT_ERROR

    def test_content_error_encoding(self):
        """Encoding error should classify as content_error."""
        result = classify_failure(status_code=200, error="Unknown encoding charset")
        assert result.category == FailureCategory.CONTENT_ERROR

    def test_content_error_too_large(self):
        """Too large response should classify as content_error."""
        result = classify_failure(status_code=200, error="Response too large")
        assert result.category == FailureCategory.CONTENT_ERROR

    def test_unknown_error(self):
        """Unknown error type should classify as unknown."""
        result = classify_failure(status_code=200, error="Something unexpected happened")
        assert result.category == FailureCategory.UNKNOWN


class TestBackoffCalculation:
    """Test backoff calculation per category."""

    def test_transient_exponential_backoff_first_failure(self):
        """First transient failure should have short backoff."""
        classifier = FailureClassifier()
        backoff = classifier.calculate_backoff(FailureCategory.TRANSIENT, consecutive_failures=1)
        # First backoff is 1 minute
        assert backoff == timedelta(minutes=1)

    def test_transient_exponential_backoff_increases(self):
        """Transient failures should have increasing backoff."""
        classifier = FailureClassifier()

        backoff1 = classifier.calculate_backoff(FailureCategory.TRANSIENT, consecutive_failures=1)
        backoff2 = classifier.calculate_backoff(FailureCategory.TRANSIENT, consecutive_failures=2)
        backoff3 = classifier.calculate_backoff(FailureCategory.TRANSIENT, consecutive_failures=3)

        assert backoff2 > backoff1
        assert backoff3 > backoff2

    def test_client_error_fixed_backoff(self):
        """Client errors use fixed 24h backoff."""
        classifier = FailureClassifier()
        backoff = classifier.calculate_backoff(FailureCategory.CLIENT_ERROR, consecutive_failures=1)
        assert backoff == timedelta(minutes=1440)  # 24 hours

    def test_rate_limited_respects_retry_after(self):
        """Rate limited should respect Retry-After header."""
        classifier = FailureClassifier()
        backoff = classifier.calculate_backoff(
            FailureCategory.RATE_LIMITED,
            consecutive_failures=1,
            retry_after_seconds=300
        )
        assert backoff == timedelta(seconds=300)

    def test_rate_limited_default_backoff(self):
        """Rate limited without Retry-After uses default."""
        classifier = FailureClassifier()
        backoff = classifier.calculate_backoff(FailureCategory.RATE_LIMITED, consecutive_failures=1)
        assert backoff == timedelta(minutes=60)  # 1 hour default

    def test_backoff_capped_at_max(self):
        """Transient backoff should be capped at last value."""
        classifier = FailureClassifier()
        # After many failures, should cap at 240 minutes (4 hours)
        backoff = classifier.calculate_backoff(FailureCategory.TRANSIENT, consecutive_failures=100)
        assert backoff == timedelta(minutes=240)

    def test_ssl_error_backoff(self):
        """SSL errors should have fixed 24h backoff."""
        classifier = FailureClassifier()
        backoff = classifier.calculate_backoff(FailureCategory.SSL_ERROR, consecutive_failures=1)
        assert backoff == timedelta(minutes=1440)


class TestDeactivationLogic:
    """Test watch deactivation logic."""

    def test_deactivate_after_max_transient_failures(self):
        """Watch should deactivate after max consecutive transient failures."""
        classifier = FailureClassifier()
        # Default max is 10 for transient
        assert not classifier.should_deactivate(FailureCategory.TRANSIENT, 9)
        assert classifier.should_deactivate(FailureCategory.TRANSIENT, 10)

    def test_deactivate_after_max_client_error_failures(self):
        """Watch should deactivate after max client errors."""
        classifier = FailureClassifier()
        # Default max is 3 for client_error
        assert not classifier.should_deactivate(FailureCategory.CLIENT_ERROR, 2)
        assert classifier.should_deactivate(FailureCategory.CLIENT_ERROR, 3)

    def test_deactivate_after_ssl_errors(self):
        """Watch should deactivate quickly for SSL errors."""
        classifier = FailureClassifier()
        # Default max is 2 for SSL errors
        assert not classifier.should_deactivate(FailureCategory.SSL_ERROR, 1)
        assert classifier.should_deactivate(FailureCategory.SSL_ERROR, 2)


class TestClassifiedFailureResult:
    """Test the ClassifiedFailure result object."""

    def test_result_has_all_fields(self):
        """ClassifiedFailure should have all required fields."""
        result = classify_failure(500, "Internal Server Error")

        assert hasattr(result, 'category')
        assert hasattr(result, 'error_message')
        assert hasattr(result, 'retry_after_seconds')

    def test_result_preserves_error_message(self):
        """Result should preserve error message."""
        result = classify_failure(500, "Database connection failed")
        assert "Database connection failed" in result.error_message


class TestDefaultConfiguration:
    """Test default configuration values."""

    def test_default_config_has_all_categories(self):
        """Default config should have all failure categories."""
        assert FailureCategory.TRANSIENT in DEFAULT_CATEGORY_CONFIG
        assert FailureCategory.CLIENT_ERROR in DEFAULT_CATEGORY_CONFIG
        assert FailureCategory.RATE_LIMITED in DEFAULT_CATEGORY_CONFIG
        assert FailureCategory.SSL_ERROR in DEFAULT_CATEGORY_CONFIG
        assert FailureCategory.CONTENT_ERROR in DEFAULT_CATEGORY_CONFIG
        assert FailureCategory.UNKNOWN in DEFAULT_CATEGORY_CONFIG

    def test_transient_config(self):
        """Transient config should have exponential backoff."""
        config = DEFAULT_CATEGORY_CONFIG[FailureCategory.TRANSIENT]
        assert config.backoff_type == "exponential"
        assert config.max_consecutive_failures == 10

    def test_client_error_config(self):
        """Client error config should have fixed backoff."""
        config = DEFAULT_CATEGORY_CONFIG[FailureCategory.CLIENT_ERROR]
        assert config.backoff_type == "fixed"
        assert config.max_consecutive_failures == 3


class TestDeactivationReason:
    """Test deactivation reason generation."""

    def test_deactivation_reason_format(self):
        """Deactivation reason should include category."""
        classifier = FailureClassifier()
        reason = classifier.get_deactivation_reason(FailureCategory.TRANSIENT)
        assert "transient" in reason
        assert "max_failures" in reason
