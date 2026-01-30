"""
Timeout Configuration for Discovery Engine Collectors.

Provides:
- TimeoutConfig: Configuration for operation-specific timeouts
- OperationType: Enum for categorizing operations
- TimeoutEvent: Telemetry event for timeout tracking

Usage:
    from collectors.timeout_config import TimeoutConfig, OperationType

    config = TimeoutConfig.from_pipeline_config(pipeline_config)
    timeout = config.to_httpx_timeout(OperationType.SEARCH)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Union

import httpx

if TYPE_CHECKING:
    from workflows.pipeline import PipelineConfig


class OperationType(str, Enum):
    """
    Operation type enum for timeout configuration.

    Categorizes operations by their typical timeout requirements:
    - SEARCH: List/search endpoints (often the slowest)
    - ENRICH: Detail fetching for specific items
    - DOWNLOAD: Large file downloads
    """

    SEARCH = "search"
    ENRICH = "enrich"
    DOWNLOAD = "download"


@dataclass
class TimeoutConfig:
    """
    Explicit timeout configuration for collector operations.

    Provides operation-specific read timeouts while sharing
    common connect, write, and pool timeouts.
    """

    connect_timeout: float = 10.0   # TCP connection establishment
    search_timeout: float = 60.0    # List/search endpoints (often slowest)
    enrich_timeout: float = 45.0    # Detail fetching
    download_timeout: float = 90.0  # Large file downloads

    def to_httpx_timeout(
        self, operation: Union[OperationType, str] = "default"
    ) -> httpx.Timeout:
        """
        Convert to httpx.Timeout with appropriate read timeout for operation.

        Args:
            operation: Operation type (OperationType enum or string)

        Returns:
            httpx.Timeout configured for the operation
        """
        # Convert string to operation type or use default
        if isinstance(operation, OperationType):
            op_str = operation.value
        else:
            op_str = operation

        read_timeout = {
            "search": self.search_timeout,
            "enrich": self.enrich_timeout,
            "download": self.download_timeout,
        }.get(op_str, self.search_timeout)  # Default to search_timeout

        return httpx.Timeout(
            connect=self.connect_timeout,
            read=read_timeout,
            write=30.0,
            pool=10.0,
        )

    @classmethod
    def from_pipeline_config(cls, config: "PipelineConfig") -> "TimeoutConfig":
        """
        Create TimeoutConfig from PipelineConfig.

        Args:
            config: Pipeline configuration with timeout fields

        Returns:
            TimeoutConfig with values from pipeline config
        """
        return cls(
            connect_timeout=config.collector_connect_timeout,
            search_timeout=config.collector_search_timeout,
            enrich_timeout=config.collector_enrich_timeout,
            download_timeout=config.collector_download_timeout,
        )


@dataclass
class TimeoutEvent:
    """
    Telemetry event for timeout tracking.

    Captures actionable information about where timeouts occur
    to help tune the right timeout knob.
    """

    collector: str           # "github", "sec_edgar"
    operation: OperationType # SEARCH, ENRICH, DOWNLOAD
    endpoint: str            # URL path (e.g., "/search/repositories")
    timeout_seconds: float   # Timeout value that was exceeded
    occurred_at: datetime    # When the timeout happened
