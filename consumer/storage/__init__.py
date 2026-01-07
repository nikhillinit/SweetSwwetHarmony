"""Storage layer for Consumer Discovery Engine."""

from .consumer_store import ConsumerStore, consumer_store
from .deduplication import compute_content_hash

__all__ = ["ConsumerStore", "consumer_store", "compute_content_hash"]
