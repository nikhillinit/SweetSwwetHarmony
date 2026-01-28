"""
Tests for BlobStore - content-addressable storage with compression.
"""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from storage.blob_store import BlobStore, BlobMetadata, compute_hash


@pytest.fixture
def temp_blob_dir():
    """Create temporary directory for blob storage."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def blob_store(temp_blob_dir):
    """Create blob store with temporary directory."""
    return BlobStore(temp_blob_dir)


class TestBlobStoreBasics:
    """Test basic store/retrieve operations."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, blob_store):
        """Store content and retrieve it back."""
        content = b"Hello, World!"

        # Store
        content_hash = await blob_store.store(content)

        # Verify hash format (SHA256 = 64 hex chars)
        assert len(content_hash) == 64
        assert all(c in "0123456789abcdef" for c in content_hash)

        # Retrieve
        retrieved = await blob_store.retrieve(content_hash)
        assert retrieved == content

    @pytest.mark.asyncio
    async def test_store_with_metadata(self, blob_store):
        """Store returns metadata about compression."""
        content = b"x" * 10000  # Compressible content

        metadata = await blob_store.store_with_metadata(content)

        assert isinstance(metadata, BlobMetadata)
        assert len(metadata.content_hash) == 64
        assert metadata.original_size == 10000
        assert metadata.compressed_size < 10000  # Should compress well
        assert metadata.compression_ratio > 1.0  # Ratio > 1 means compression worked
        assert metadata.stored_at is not None

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent(self, blob_store):
        """Retrieve returns None for missing content."""
        fake_hash = "a" * 64

        result = await blob_store.retrieve(fake_hash)

        assert result is None

    @pytest.mark.asyncio
    async def test_exists_true(self, blob_store):
        """Exists returns True for stored content."""
        content = b"test content"
        content_hash = await blob_store.store(content)

        exists = await blob_store.exists(content_hash)

        assert exists is True

    @pytest.mark.asyncio
    async def test_exists_false(self, blob_store):
        """Exists returns False for missing content."""
        fake_hash = "b" * 64

        exists = await blob_store.exists(fake_hash)

        assert exists is False

    @pytest.mark.asyncio
    async def test_delete(self, blob_store):
        """Delete removes content."""
        content = b"to be deleted"
        content_hash = await blob_store.store(content)

        # Verify it exists
        assert await blob_store.exists(content_hash)

        # Delete
        deleted = await blob_store.delete(content_hash)
        assert deleted is True

        # Verify it's gone
        assert not await blob_store.exists(content_hash)
        assert await blob_store.retrieve(content_hash) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, blob_store):
        """Delete returns False for missing content."""
        fake_hash = "c" * 64

        deleted = await blob_store.delete(fake_hash)

        assert deleted is False


class TestDeduplication:
    """Test content deduplication."""

    @pytest.mark.asyncio
    async def test_same_content_same_hash(self, blob_store):
        """Same content produces same hash."""
        content = b"duplicate me"

        hash1 = await blob_store.store(content)
        hash2 = await blob_store.store(content)

        assert hash1 == hash2

    @pytest.mark.asyncio
    async def test_different_content_different_hash(self, blob_store):
        """Different content produces different hashes."""
        content1 = b"content one"
        content2 = b"content two"

        hash1 = await blob_store.store(content1)
        hash2 = await blob_store.store(content2)

        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_dedup_doesnt_overwrite(self, blob_store):
        """Storing same content twice doesn't cause issues."""
        content = b"store me twice"

        hash1 = await blob_store.store(content)
        hash2 = await blob_store.store(content)

        # Both should succeed and return same hash
        assert hash1 == hash2

        # Content should be retrievable
        retrieved = await blob_store.retrieve(hash1)
        assert retrieved == content

    @pytest.mark.asyncio
    async def test_dedup_preserves_file(self, temp_blob_dir):
        """Second store of same content doesn't modify file."""
        store = BlobStore(temp_blob_dir)
        content = b"original content"

        hash1 = await store.store(content)

        # Get file modification time
        blob_path = store._hash_to_path(hash1)
        mtime1 = blob_path.stat().st_mtime

        # Wait a bit and store again
        await asyncio.sleep(0.01)
        hash2 = await store.store(content)

        # File should not be modified
        mtime2 = blob_path.stat().st_mtime
        assert mtime1 == mtime2


class TestDirectoryStructure:
    """Test sharded directory structure."""

    @pytest.mark.asyncio
    async def test_directory_sharding(self, temp_blob_dir):
        """Files are stored in sharded directories."""
        store = BlobStore(temp_blob_dir)
        content = b"test sharding"

        content_hash = await store.store(content)

        # Check directory structure: base/XX/YY/hash.zst
        expected_dir = Path(temp_blob_dir) / content_hash[:2] / content_hash[2:4]
        assert expected_dir.exists()

        # Check file exists
        files = list(expected_dir.glob(f"{content_hash}.*"))
        assert len(files) == 1

    @pytest.mark.asyncio
    async def test_multiple_shards(self, temp_blob_dir):
        """Multiple contents go to different shards."""
        store = BlobStore(temp_blob_dir)

        # Store multiple items
        hashes = []
        for i in range(10):
            h = await store.store(f"content {i}".encode())
            hashes.append(h)

        # Should have files in potentially different shards
        base = Path(temp_blob_dir)
        shard_dirs = list(base.glob("*/*"))
        assert len(shard_dirs) > 0

        # All content should be retrievable
        for i, h in enumerate(hashes):
            content = await store.retrieve(h)
            assert content == f"content {i}".encode()


class TestCompression:
    """Test compression behavior."""

    @pytest.mark.asyncio
    async def test_compressible_content(self, blob_store):
        """Highly compressible content gets smaller."""
        # Highly compressible: repeated pattern
        content = b"abc" * 10000

        metadata = await blob_store.store_with_metadata(content)

        # Should compress very well (ratio > 10)
        assert metadata.compression_ratio > 5.0
        assert metadata.compressed_size < metadata.original_size / 5

    @pytest.mark.asyncio
    async def test_incompressible_content(self, blob_store):
        """Random content may not compress much."""
        import random
        random.seed(42)

        # Random bytes are hard to compress
        content = bytes(random.getrandbits(8) for _ in range(1000))

        metadata = await blob_store.store_with_metadata(content)

        # Still retrievable
        retrieved = await blob_store.retrieve(metadata.content_hash)
        assert retrieved == content

    @pytest.mark.asyncio
    async def test_empty_content(self, blob_store):
        """Empty content can be stored."""
        content = b""

        content_hash = await blob_store.store(content)
        retrieved = await blob_store.retrieve(content_hash)

        assert retrieved == content


class TestConcurrency:
    """Test concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_stores(self, blob_store):
        """Multiple concurrent stores don't conflict."""
        contents = [f"content_{i}".encode() for i in range(20)]

        # Store all concurrently
        tasks = [blob_store.store(c) for c in contents]
        hashes = await asyncio.gather(*tasks)

        # All should succeed with unique hashes
        assert len(set(hashes)) == 20

        # All retrievable
        for h, c in zip(hashes, contents):
            retrieved = await blob_store.retrieve(h)
            assert retrieved == c

    @pytest.mark.asyncio
    async def test_concurrent_same_content(self, blob_store):
        """Concurrent stores of same content are safe."""
        content = b"concurrent content"

        # Store same content 10 times concurrently
        tasks = [blob_store.store(content) for _ in range(10)]
        hashes = await asyncio.gather(*tasks)

        # All should return the same hash
        assert all(h == hashes[0] for h in hashes)

        # Content retrievable
        retrieved = await blob_store.retrieve(hashes[0])
        assert retrieved == content

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self, blob_store):
        """Concurrent reads and writes don't conflict."""
        content = b"read write test"
        content_hash = await blob_store.store(content)

        # Concurrent reads
        read_tasks = [blob_store.retrieve(content_hash) for _ in range(10)]

        # Concurrent writes (same content - should be fast due to dedup)
        write_tasks = [blob_store.store(content) for _ in range(5)]

        # Run all concurrently
        results = await asyncio.gather(*read_tasks, *write_tasks)

        # All reads should return content
        for r in results[:10]:
            assert r == content

        # All writes should return same hash
        for h in results[10:]:
            assert h == content_hash


class TestStats:
    """Test storage statistics."""

    @pytest.mark.asyncio
    async def test_empty_stats(self, blob_store):
        """Empty store returns zero stats."""
        stats = await blob_store.get_stats()

        assert stats["blob_count"] == 0
        assert stats["total_bytes"] == 0

    @pytest.mark.asyncio
    async def test_stats_after_store(self, blob_store):
        """Stats reflect stored content."""
        # Store some content
        for i in range(5):
            await blob_store.store(f"content {i}".encode())

        stats = await blob_store.get_stats()

        assert stats["blob_count"] == 5
        assert stats["total_bytes"] > 0
        assert "B" in stats["total_bytes_human"] or "KB" in stats["total_bytes_human"]


class TestHashFunction:
    """Test standalone hash function."""

    def test_compute_hash(self):
        """compute_hash returns correct SHA256."""
        content = b"test content"

        result = compute_hash(content)
        expected = hashlib.sha256(content).hexdigest()

        assert result == expected

    def test_compute_hash_empty(self):
        """compute_hash works with empty content."""
        content = b""

        result = compute_hash(content)
        expected = hashlib.sha256(content).hexdigest()

        assert result == expected


class TestInitialization:
    """Test store initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_directory(self, temp_blob_dir):
        """Initialize creates base directory."""
        # Use a subdirectory that doesn't exist
        store = BlobStore(f"{temp_blob_dir}/new/subdir")

        await store.initialize()

        assert Path(f"{temp_blob_dir}/new/subdir").exists()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, temp_blob_dir):
        """Initialize can be called multiple times."""
        store = BlobStore(temp_blob_dir)

        await store.initialize()
        await store.initialize()

        # Should not raise
        assert Path(temp_blob_dir).exists()
