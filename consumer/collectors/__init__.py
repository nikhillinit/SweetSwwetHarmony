"""Signal collectors for consumer discovery."""

from .base import ConsumerCollector, CollectorResult, Signal, run_collectors
from .hn_collector import HNCollector
from .bevnet_collector import BevNetCollector
from .uspto_collector import USPTOCollector
from .reddit_collector import RedditCollector

__all__ = [
    "ConsumerCollector",
    "CollectorResult",
    "Signal",
    "run_collectors",
    "HNCollector",
    "BevNetCollector",
    "USPTOCollector",
    "RedditCollector",
]
