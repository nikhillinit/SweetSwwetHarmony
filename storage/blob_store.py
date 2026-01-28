"""
Content-Addressable Blob Store for Snapshot Storage

Provides efficient, deduplicated storage for raw snapshot content:
- SHA256 hashing for content-addressable storage
- Zstandard compression (fast, good compression ratio)
- Automatic deduplication (same content = same hash = stored once)
- Directory sharding to avoid filesystem limits

Directory structure:
    data/blobs/
    ├── ab/cd/abcd1234...5678.zst
    ├── ef/gh/efgh5678...1234.zst
    └── ...

Usage:
    store = BlobStore("data/blobs")

    # Store content
    content = b"raw HTML or JSON content"
    content_hash = await store.store(content)
    # Returns: "abcd1234567890..."

    # Retrieve content
    retrieved = await store.retrieve(content_hash)
    # Returns: b"raw HTML or JSON content"

    # Check existence
    exists = await store.exists(content_hash)
    # Returns: True/False
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, BinaryIO

try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False
    import gzip

logger = logging.getLogger(__name__)

# Default compression level (1-22, 3 is fast with good ratio)
DEFAULT_COMPRESSION_LEVEL = 3

# Thread pool for blocking I/O operations
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="blob_store")


@dataclass
class BlobMetadata:
    """Metadata about a stored blob."""
    content_hash: str
    compressed_size: int
    original_size: int
    compression_ratio: float
    stored_at: datetime


class BlobStore:
    """
    Content-addressable blob store with compression and deduplication.

    Thread-safe for concurrent reads and writes.
    """

    def __init__(
        self,
        base_path: str = "data/blobs",
        compression_level: int = DEFAULT_COMPRESSION_LEVEL,
    ):
        """
        Initialize blob store.

        Args:
            base_path: Directory for blob storage
            compression_level: Zstd compression level (1-22)
        """
        self.base = Path(base_path)
        self.compression_level = compression_level

        # Initialize compressor/decompressor
        if ZSTD_AVAILABLE:
            self._compressor = zstd.ZstdCompressor(level=compression_level)
            self._decompressor = zstd.ZstdDecompressor()
            self._extension = ".zst"
        else:
            self._compressor = None
            self._decompressor = None
            self._extension = ".gz"
            logger.warning("zstandard not available, falling back to gzip")

    async def initialize(self) -> None:
        """Create base directory if needed."""
        await asyncio.get_event_loop().run_in_executor(
            _executor,
            self._ensure_directory,
            self.base
        )

    def _ensure_directory(self, path: Path) -> None:
        """Create directory (sync, runs in executor)."""
        path.mkdir(parents=True, exist_ok=True)

    def _hash_content(self, content: bytes) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content).hexdigest()

    def _hash_to_path(self, content_hash: str) -> Path:
        """
        Convert hash to storage path with directory sharding.

        Example: "abcdef123456..." -> base/ab/cd/abcdef123456.zst
        """
        return (
            self.base
            / content_hash[:2]
            / content_hash[2:4]
            / f"{content_hash}{self._extension}"
        )

    def _compress(self, content: bytes) -> bytes:
        """Compress content using zstd or gzip."""
        if ZSTD_AVAILABLE:
            return self._compressor.compress(content)
        else:
            return gzip.compress(content, compresslevel=self.compression_level)

    def _decompress(self, compressed: bytes) -> bytes:
        """Decompress content using zstd or gzip."""
        if ZSTD_AVAILABLE:
            return self._decompressor.decompress(compressed)
        else:
            return gzip.decompress(compressed)

    def _store_sync(self, content: bytes) -> BlobMetadata:
        """
        Store content synchronously (runs in thread pool).

        Returns metadata about the stored blob.
        """
        content_hash = self._hash_content(content)
        blob_path = self._hash_to_path(content_hash)

        # Check if already exists (deduplication)
        if blob_path.exists():
            stat = blob_path.stat()
            return BlobMetadata(
                content_hash=content_hash,
                compressed_size=stat.st_size,
                original_size=len(content),
                compression_ratio=len(content) / stat.st_size if stat.st_size > 0 else 1.0,
                stored_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )

        # Compress
        compressed = self._compress(content)

        # Create directory and write atomically
        blob_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file first, then rename (atomic on POSIX)
        # On Windows, use unique temp name to avoid race conditions
        import time
        import threading
        temp_path = blob_path.with_suffix(f".tmp.{threading.get_ident()}.{int(time.time()*1000)}")
        try:
            temp_path.write_bytes(compressed)
            try:
                temp_path.rename(blob_path)
            except FileExistsError:
                # Another thread/process wrote the file first - that's fine (dedup)
                temp_path.unlink()
            except PermissionError:
                # On Windows, may get this if another process has the file open
                # Just delete our temp file - the other one wins
                temp_path.unlink()
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        return BlobMetadata(
            content_hash=content_hash,
            compressed_size=len(compressed),
            original_size=len(content),
            compression_ratio=len(content) / len(compressed) if len(compressed) > 0 else 1.0,
            stored_at=datetime.now(timezone.utc),
        )

    def _retrieve_sync(self, content_hash: str) -> Optional[bytes]:
        """
        Retrieve content synchronously (runs in thread pool).

        Returns None if blob doesn't exist.
        """
        blob_path = self._hash_to_path(content_hash)

        if not blob_path.exists():
            return None

        compressed = blob_path.read_bytes()
        return self._decompress(compressed)

    def _exists_sync(self, content_hash: str) -> bool:
        """Check if blob exists (sync, runs in executor)."""
        blob_path = self._hash_to_path(content_hash)
        return blob_path.exists()

    def _delete_sync(self, content_hash: str) -> bool:
        """
        Delete blob (sync, runs in executor).

        Returns True if deleted, False if not found.
        """
        blob_path = self._hash_to_path(content_hash)

        if not blob_path.exists():
            return False

        blob_path.unlink()

        # Clean up empty parent directories
        try:
            blob_path.parent.rmdir()
            blob_path.parent.parent.rmdir()
        except OSError:
            pass  # Directory not empty

        return True

    async def store(self, content: bytes) -> str:
        """
        Store content and return its hash.

        Automatically deduplicates: if content already exists,
        returns the existing hash without re-storing.

        Args:
            content: Raw bytes to store

        Returns:
            SHA256 hash of the content (64 hex characters)
        """
        loop = asyncio.get_event_loop()
        metadata = await loop.run_in_executor(
            _executor,
            self._store_sync,
            content
        )
        return metadata.content_hash

    async def store_with_metadata(self, content: bytes) -> BlobMetadata:
        """
        Store content and return full metadata.

        Args:
            content: Raw bytes to store

        Returns:
            BlobMetadata with hash, sizes, and compression info
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._store_sync,
            content
        )

    async def retrieve(self, content_hash: str) -> Optional[bytes]:
        """
        Retrieve content by hash.

        Args:
            content_hash: SHA256 hash returned from store()

        Returns:
            Original uncompressed content, or None if not found
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._retrieve_sync,
            content_hash
        )

    async def exists(self, content_hash: str) -> bool:
        """
        Check if content exists.

        Args:
            content_hash: SHA256 hash to check

        Returns:
            True if blob exists, False otherwise
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._exists_sync,
            content_hash
        )

    async def delete(self, content_hash: str) -> bool:
        """
        Delete content by hash.

        Args:
            content_hash: SHA256 hash to delete

        Returns:
            True if deleted, False if not found
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._delete_sync,
            content_hash
        )

    async def get_stats(self) -> dict:
        """
        Get storage statistics.

        Returns:
            Dictionary with blob count, total size, etc.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._get_stats_sync
        )

    def _get_stats_sync(self) -> dict:
        """Get storage stats (sync, runs in executor)."""
        if not self.base.exists():
            return {
                "blob_count": 0,
                "total_bytes": 0,
                "total_bytes_human": "0 B",
            }

        blob_count = 0
        total_bytes = 0

        for root, dirs, files in os.walk(self.base):
            for f in files:
                if f.endswith(self._extension):
                    blob_count += 1
                    total_bytes += (Path(root) / f).stat().st_size

        return {
            "blob_count": blob_count,
            "total_bytes": total_bytes,
            "total_bytes_human": self._format_bytes(total_bytes),
        }

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Format bytes as human-readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"


# Convenience function for quick hashing
def compute_hash(content: bytes) -> str:
    """Compute SHA256 hash of content without storing."""
    return hashlib.sha256(content).hexdigest()
