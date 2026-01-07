"""
Tests for content hash deduplication.

Tests hash computation for signal deduplication.
"""

import pytest


class TestComputeContentHash:
    """Test compute_content_hash function"""

    def test_function_exists(self):
        """compute_content_hash should be importable"""
        from consumer.storage.deduplication import compute_content_hash

        assert callable(compute_content_hash)

    def test_returns_32_char_hex(self):
        """compute_content_hash should return 32-char hex string"""
        from consumer.storage.deduplication import compute_content_hash

        result = compute_content_hash("hn", "12345678")

        assert isinstance(result, str)
        assert len(result) == 32
        # Should be valid hex
        int(result, 16)

    def test_consistent_hashing(self):
        """Same inputs should produce same hash"""
        from consumer.storage.deduplication import compute_content_hash

        hash1 = compute_content_hash("reddit", "abc123")
        hash2 = compute_content_hash("reddit", "abc123")

        assert hash1 == hash2

    def test_different_inputs_different_hash(self):
        """Different inputs should produce different hashes"""
        from consumer.storage.deduplication import compute_content_hash

        hash1 = compute_content_hash("reddit", "abc123")
        hash2 = compute_content_hash("reddit", "xyz789")
        hash3 = compute_content_hash("hn", "abc123")

        assert hash1 != hash2
        assert hash1 != hash3

    def test_source_api_matters(self):
        """Same source_id with different source_api should differ"""
        from consumer.storage.deduplication import compute_content_hash

        hash1 = compute_content_hash("hn", "12345")
        hash2 = compute_content_hash("reddit", "12345")

        assert hash1 != hash2


class TestComputeContentHashFromSignal:
    """Test compute_content_hash_from_signal function"""

    def test_function_exists(self):
        """compute_content_hash_from_signal should be importable"""
        from consumer.storage.deduplication import compute_content_hash_from_signal

        assert callable(compute_content_hash_from_signal)

    def test_extracts_from_dict(self):
        """Should extract source_api and source_id from dict"""
        from consumer.storage.deduplication import (
            compute_content_hash,
            compute_content_hash_from_signal,
        )

        signal_data = {
            "source_api": "hn",
            "source_id": "12345678",
            "title": "Some title",
        }

        result = compute_content_hash_from_signal(signal_data)
        expected = compute_content_hash("hn", "12345678")

        assert result == expected

    def test_handles_missing_keys(self):
        """Should handle missing keys gracefully"""
        from consumer.storage.deduplication import compute_content_hash_from_signal

        # Missing keys should use empty strings
        result = compute_content_hash_from_signal({})

        assert isinstance(result, str)
        assert len(result) == 32


class TestNormalizeSourceId:
    """Test normalize_source_id function"""

    def test_function_exists(self):
        """normalize_source_id should be importable"""
        from consumer.storage.deduplication import normalize_source_id

        assert callable(normalize_source_id)

    def test_converts_to_string(self):
        """Should convert non-string IDs to strings"""
        from consumer.storage.deduplication import normalize_source_id

        result = normalize_source_id("hn", 12345678)

        assert isinstance(result, str)
        assert result == "12345678"

    def test_strips_whitespace(self):
        """Should strip whitespace from IDs"""
        from consumer.storage.deduplication import normalize_source_id

        result = normalize_source_id("hn", "  12345  ")

        assert result == "12345"

    def test_reddit_prefix_removal(self):
        """Should remove t3_ prefix from Reddit IDs"""
        from consumer.storage.deduplication import normalize_source_id

        result = normalize_source_id("reddit", "t3_abc123")

        assert result == "abc123"

    def test_reddit_keeps_non_prefixed(self):
        """Should keep Reddit IDs without prefix"""
        from consumer.storage.deduplication import normalize_source_id

        result = normalize_source_id("reddit", "abc123")

        assert result == "abc123"

    def test_hn_passthrough(self):
        """HN IDs should pass through unchanged"""
        from consumer.storage.deduplication import normalize_source_id

        result = normalize_source_id("hn", "42069")

        assert result == "42069"

    def test_uspto_removes_formatting(self):
        """USPTO IDs should have dashes/spaces removed"""
        from consumer.storage.deduplication import normalize_source_id

        result = normalize_source_id("uspto_tm", "12-345 678")

        assert result == "12345678"
